"""
Agent Belief State — tracks evolving opinions, confidence, and trust across rounds.

Each agent maintains:
- positions: topic → stance (-1.0 to +1.0)
- confidence: topic → certainty (0.0 to 1.0)
- trust: agent_id → trust level (0.0 to 1.0, default 0.5)
- exposure_history: set of argument hashes (prevents re-processing)

Beliefs update heuristically after each round based on:
- Posts the agent read (weighted by trust in author)
- Engagement received on own posts (social reinforcement)
- Novel arguments encountered (larger impact than repeated ones)
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class BeliefState:
    """Mutable belief state for a simulation agent."""

    positions: Dict[str, float] = field(default_factory=dict)
    confidence: Dict[str, float] = field(default_factory=dict)
    trust: Dict[int, float] = field(default_factory=dict)
    exposure_history: Set[str] = field(default_factory=set)

    # Cap exposure history to prevent unbounded memory growth
    MAX_EXPOSURE_HISTORY: int = 2000

    # ── Initialization ──────────────────────────────────────────

    @classmethod
    def from_profile(
        cls,
        agent_config: Dict[str, Any],
        topics: List[str],
    ) -> "BeliefState":
        """Create initial beliefs from an agent's simulation config.

        Args:
            agent_config: Dict with optional keys ``stance``, ``sentiment_bias``.
            topics: List of topic strings extracted from the simulation requirement.
        """
        stance_str = agent_config.get("stance", "neutral")
        sentiment_bias = agent_config.get("sentiment_bias", 0.0)

        stance_map = {
            "supportive": 0.6,
            "strongly_supportive": 0.9,
            "opposing": -0.6,
            "strongly_opposing": -0.9,
            "neutral": 0.0,
            "observer": 0.0,
        }
        base_position = stance_map.get(stance_str, 0.0)

        # Confidence starts moderate; sentiment_bias pushes it higher/lower
        base_confidence = min(1.0, max(0.1, 0.4 + abs(sentiment_bias) * 0.4))

        positions = {}
        confidences = {}
        for topic in topics:
            # Add small noise so agents aren't identical
            position_noise = random.gauss(0, 0.15)
            confidence_noise = random.gauss(0, 0.05)
            positions[topic] = max(-1.0, min(1.0, base_position + sentiment_bias * 0.2 + position_noise))
            confidences[topic] = min(1.0, max(0.1, base_confidence + confidence_noise))

        return cls(positions=positions, confidence=confidences)

    # ── Update rules (heuristic, no LLM calls) ─────────────────

    def update_from_round(
        self,
        posts_seen: List[Dict[str, Any]],
        own_engagement: Dict[str, Any],
        round_num: int,
    ) -> Dict[str, float]:
        """Update beliefs based on what happened in a round.

        Args:
            posts_seen: List of posts the agent was shown, each with keys:
                ``content``, ``author_id``, ``num_likes``, ``num_dislikes``.
            own_engagement: Dict with keys ``likes_received``, ``dislikes_received``
                for posts the agent created this round.
            round_num: Current round number (used for decay).

        Returns:
            Dict mapping topic → position delta for this round.
        """
        deltas: Dict[str, float] = {}

        # --- Process posts the agent read ---
        for post in posts_seen:
            content = post.get("content", "")
            author_id = post.get("author_id")
            if not content:
                continue

            content_hash = hashlib.md5(content.encode()).hexdigest()[:12]
            is_novel = content_hash not in self.exposure_history
            self.exposure_history.add(content_hash)
            # Evict oldest entries if over cap (set is unordered, but hash
            # collisions make this a rough FIFO — good enough for dedup)
            if len(self.exposure_history) > self.MAX_EXPOSURE_HISTORY:
                to_remove = list(self.exposure_history)[:500]
                self.exposure_history -= set(to_remove)

            # Estimate post stance from simple sentiment heuristic
            post_stance = _estimate_stance(content)
            if post_stance is None:
                continue

            # Trust weight for author (default 0.5 for unknown)
            author_trust = self.trust.get(author_id, 0.5) if author_id else 0.5

            # Social proof: posts with more likes carry more weight
            likes = post.get("num_likes", 0)
            social_weight = min(1.0, 0.3 + likes * 0.07)

            # Novelty amplifier: first time seeing an argument has 2x impact
            novelty_mult = 1.5 if is_novel else 0.5

            for topic, current_pos in self.positions.items():
                # Only update if the post seems related to the topic
                if not _content_relates_to_topic(content, topic):
                    continue

                current_conf = self.confidence.get(topic, 0.5)

                # Nudge = direction * trust * social_proof * novelty / confidence
                # High-confidence agents resist change; low-confidence agents are swayed
                resistance = 0.3 + current_conf * 0.7  # 0.3 to 1.0
                nudge = (
                    (post_stance - current_pos)
                    * author_trust
                    * social_weight
                    * novelty_mult
                    * 0.08  # base learning rate
                    / resistance
                )

                self.positions[topic] = max(-1.0, min(1.0, current_pos + nudge))
                deltas[topic] = deltas.get(topic, 0.0) + nudge

        # --- Process engagement on own posts ---
        likes_received = own_engagement.get("likes_received", 0)
        dislikes_received = own_engagement.get("dislikes_received", 0)

        if likes_received > 0 or dislikes_received > 0:
            for topic in self.positions:
                current_conf = self.confidence.get(topic, 0.5)
                if likes_received > dislikes_received:
                    # Social reinforcement: increase confidence
                    boost = min(0.15, (likes_received - dislikes_received) * 0.03)
                    self.confidence[topic] = min(1.0, current_conf + boost)
                elif dislikes_received > likes_received:
                    # Social pushback: decrease confidence (not position)
                    drop = min(0.15, (dislikes_received - likes_received) * 0.03)
                    self.confidence[topic] = max(0.1, current_conf - drop)

        return deltas

    def update_trust(self, other_agent_id: int, action: str):
        """Update trust toward another agent based on an interaction.

        Args:
            other_agent_id: The agent being interacted with.
            action: One of ``like``, ``dislike``, ``follow``, ``unfollow``, ``mute``.
        """
        current = self.trust.get(other_agent_id, 0.5)
        adjustments = {
            "like": 0.05,
            "dislike": -0.05,
            "follow": 0.10,
            "unfollow": -0.10,
            "mute": -0.20,
        }
        delta = adjustments.get(action, 0.0)
        self.trust[other_agent_id] = max(0.0, min(1.0, current + delta))

    # ── Prompt generation ───────────────────────────────────────

    def to_prompt_text(self) -> str:
        """Convert belief state to natural language for LLM prompt injection."""
        if not self.positions:
            return ""

        lines = ["# YOUR CURRENT BELIEFS AND STANCE"]
        lines.append(
            "These reflect your evolving understanding based on what you've "
            "observed and experienced. Let them guide (but not rigidly dictate) "
            "your actions."
        )
        lines.append("")

        for topic, position in self.positions.items():
            conf = self.confidence.get(topic, 0.5)
            stance_label = _stance_label(position)
            conf_label = _confidence_label(conf)
            lines.append(
                f"- On **{topic}**: You are {stance_label} "
                f"(confidence: {conf_label})"
            )

        # Add trusted/distrusted agents if any non-default
        trusted = [
            (aid, t) for aid, t in self.trust.items()
            if t > 0.7
        ]
        distrusted = [
            (aid, t) for aid, t in self.trust.items()
            if t < 0.3
        ]

        if trusted:
            lines.append("")
            lines.append(
                "You tend to trust these users' perspectives: "
                + ", ".join(f"Agent_{aid}" for aid, _ in trusted[:5])
            )
        if distrusted:
            lines.append(
                "You tend to be skeptical of these users: "
                + ", ".join(f"Agent_{aid}" for aid, _ in distrusted[:5])
            )

        return "\n".join(lines)

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "positions": self.positions,
            "confidence": self.confidence,
            "trust": {str(k): v for k, v in self.trust.items()},
            "exposure_count": len(self.exposure_history),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BeliefState":
        return cls(
            positions=data.get("positions", {}),
            confidence=data.get("confidence", {}),
            trust={int(k): v for k, v in data.get("trust", {}).items()},
            exposure_history=set(),  # not persisted for size
        )


# ── Marker for system message injection (same pattern as cross_platform_digest) ──

_BELIEF_STATE_MARKER = "\n\n# YOUR CURRENT BELIEFS AND STANCE"


def inject_belief_context(agent, belief_text: str):
    """Inject belief state into an agent's system message.

    Follows the same pattern as ``inject_cross_platform_context``: appends
    (or replaces) a marked section at the end of ``agent.system_message.content``.
    """
    content = agent.system_message.content

    # Remove previous belief section if present
    marker_pos = content.find(_BELIEF_STATE_MARKER)
    if marker_pos != -1:
        # Find the next section marker (cross-platform or end)
        next_marker = content.find("\n\n# ", marker_pos + len(_BELIEF_STATE_MARKER))
        if next_marker != -1:
            content = content[:marker_pos] + content[next_marker:]
        else:
            content = content[:marker_pos]

    agent.system_message.content = content + "\n\n" + belief_text


def clear_belief_context(agent):
    """Remove the belief state section from an agent's system message."""
    content = agent.system_message.content
    marker_pos = content.find(_BELIEF_STATE_MARKER)
    if marker_pos != -1:
        next_marker = content.find("\n\n# ", marker_pos + len(_BELIEF_STATE_MARKER))
        if next_marker != -1:
            agent.system_message.content = content[:marker_pos] + content[next_marker:]
        else:
            agent.system_message.content = content[:marker_pos]


# ── Private helpers ─────────────────────────────────────────────

def _estimate_stance(content: str) -> Optional[float]:
    """Quick heuristic stance estimation from post content.

    Returns a float from -1.0 (strongly negative) to 1.0 (strongly positive),
    or None only for very short / empty content.  This is intentionally simple
    — keyword matching with a broad fallback — to avoid extra LLM calls.
    """
    content_lower = content.lower()

    if len(content_lower.strip()) < 3:
        return None

    positive_signals = [
        "support", "agree", "great", "excellent", "beneficial", "important",
        "necessary", "progress", "opportunity", "innovative", "promising",
        "approve", "endorse", "welcome", "positive", "good news",
        "well done", "proud", "celebrate", "achievement",
    ]
    negative_signals = [
        "oppose", "disagree", "terrible", "harmful", "dangerous", "threat",
        "unacceptable", "disastrous", "catastrophe", "fail", "wrong",
        "corrupt", "scandal", "outrage", "incompetent", "reckless",
        "protest", "condemn", "reject", "concerned", "worried",
    ]

    pos_count = sum(1 for w in positive_signals if w in content_lower)
    neg_count = sum(1 for w in negative_signals if w in content_lower)

    total = pos_count + neg_count
    if total > 0:
        return (pos_count - neg_count) / total

    # Broad fallback: expanded sentiment words so most real content
    # gets *some* signal rather than being silently dropped.
    broad_positive = [
        "love", "like", "happy", "hope", "excited", "better", "best",
        "awesome", "amazing", "cool", "nice", "interesting", "helpful",
        "thank", "thanks", "appreciate", "win", "success", "improve",
        "trust", "confident", "optimis", "encourage", "empower",
        "brilliant", "fantastic", "incredible", "wonderful",
        "recommend", "favor", "advantage", "benefit", "gain",
    ]
    broad_negative = [
        "hate", "bad", "sad", "fear", "angry", "worse", "worst",
        "awful", "horrible", "stupid", "ugly", "annoying", "disappoint",
        "frustrat", "problem", "issue", "risk", "lose", "loss", "damage",
        "distrust", "pessimis", "discourage", "alarm",
        "ridiculous", "absurd", "pathetic", "disaster",
        "blame", "against", "unfair", "disadvantage", "cost",
    ]

    bp = sum(1 for w in broad_positive if w in content_lower)
    bn = sum(1 for w in broad_negative if w in content_lower)
    broad_total = bp + bn

    if broad_total > 0:
        # Attenuate broad signal (less confident than primary keywords)
        return 0.6 * (bp - bn) / broad_total

    # Final fallback: return a mild neutral signal so the post still
    # participates in belief updates (weighted near zero).  Returning
    # None would skip the post entirely, which is the root cause of
    # beliefs never changing when content doesn't match keywords.
    return 0.0


def _content_relates_to_topic(content: str, topic: str) -> bool:
    """Check if content is related to a topic via keyword overlap.

    Uses a low bar: any single keyword match counts. For short topics
    (1-2 words), we also check the whole topic as a substring.
    """
    content_lower = content.lower()
    topic_lower = topic.lower()

    # Direct substring match
    if topic_lower in content_lower:
        return True

    # Word-level overlap (include short words like "AI" for topics)
    topic_words = [w.strip().lower() for w in topic.split() if len(w.strip()) > 1]
    if not topic_words:
        return True  # If topic is empty, assume everything relates
    matches = sum(1 for w in topic_words if w in content_lower)
    return matches >= 1


def _stance_label(position: float) -> str:
    if position > 0.6:
        return "strongly supportive"
    elif position > 0.2:
        return "leaning supportive"
    elif position > -0.2:
        return "neutral / undecided"
    elif position > -0.6:
        return "leaning opposed"
    else:
        return "strongly opposed"


def _confidence_label(confidence: float) -> str:
    if confidence > 0.8:
        return "very high — firmly held view"
    elif confidence > 0.6:
        return "moderate — open to strong arguments"
    elif confidence > 0.4:
        return "low — genuinely uncertain"
    else:
        return "very low — actively seeking perspective"


def extract_topics_from_requirement(simulation_requirement: str) -> List[str]:
    """Extract key debate topics from a simulation requirement.

    Tries LLM extraction first (precise, deduplicated), falls back to
    simple keyword extraction if the LLM is unavailable.

    Returns 2-4 topic strings that agents can hold positions on.
    """
    # Try LLM extraction
    try:
        from app.utils.llm_client import create_llm_client
        llm = create_llm_client()
        result = llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract 2-4 key DEBATE TOPICS from a simulation requirement. "
                        "Each topic should be a short phrase (2-5 words) that people "
                        "can have a stance on (supportive, neutral, opposed). "
                        "Return ONLY a JSON array of strings, nothing else. "
                        "Example: [\"AI regulation\", \"tech industry self-governance\", \"data privacy\"]"
                    ),
                },
                {"role": "user", "content": simulation_requirement},
            ],
            temperature=0.1,
            max_tokens=200,
        )
        # Parse JSON array from response
        import json
        import re
        # Extract JSON array from response (might have markdown wrapping)
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if match:
            topics = json.loads(match.group())
            if isinstance(topics, list) and len(topics) >= 2:
                # Deduplicate and clean
                seen = set()
                clean = []
                for t in topics:
                    t = str(t).strip()
                    if t.lower() not in seen and len(t) > 3:
                        seen.add(t.lower())
                        clean.append(t)
                if clean:
                    return clean[:4]
    except Exception:
        pass

    # Fallback: simple extraction
    stopwords = {
        "the", "a", "an", "is", "are", "of", "to", "in", "for", "and", "or",
        "on", "at", "by", "how", "what", "will", "this", "that", "with",
        "simulate", "simulation", "predict", "reaction", "focus", "public",
    }
    words = simulation_requirement.split()
    content_words = [
        w.strip(".,;:!?\"'()[]{}") for w in words
        if w.strip(".,;:!?\"'()[]{}").lower() not in stopwords
        and len(w.strip(".,;:!?\"'()[]{}")) > 3
    ]
    # Take first 3 unique content words as topic proxies
    seen = set()
    topics = []
    for w in content_words:
        if w.lower() not in seen:
            seen.add(w.lower())
            topics.append(w)
    return topics[:4] if topics else [simulation_requirement[:50]]
