"""
Belief system integration helpers for simulation scripts.

Provides functions that the simulation scripts (run_reddit, run_parallel)
call to initialize, update, and inject belief state. Keeps the belief
logic in one place instead of duplicating across scripts.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from wonderwall.social_agent.belief_state import (
    BeliefState,
    extract_topics_from_requirement,
    inject_belief_context,
)
from wonderwall.social_agent.round_analyzer import (
    RoundAnalyzer,
    RoundSnapshot,
    SimulationTrajectory,
    update_trust_from_actions,
)


def _merge_per_platform_trajectories(sim_dir: str) -> None:
    """Merge every ``trajectory_<platform>.json`` in *sim_dir* into a single
    authoritative ``trajectory.json`` that consumers (drift chart, article
    generator, quality metrics, report agent) read.

    Per-round merge semantics:
      - ``belief_positions`` / ``belief_deltas``: union of agent keys
        (platforms track disjoint agent pools in practice; last-write-wins
        for any collision).
      - ``total_posts_created`` / ``total_engagements`` / ``active_agent_count``:
        summed across platforms.
      - ``viral_posts``: concatenated.
      - ``sentiment_summary``: last-write-wins (cross-platform sentiment
        doesn't meaningfully average).
      - Derived fields (``belief_trajectories``, ``opinion_convergence``,
        ``turning_points``) are re-derived from the merged snapshots via
        :class:`SimulationTrajectory`.

    No-op when no per-platform files exist.
    """
    per_platform = sorted(glob.glob(os.path.join(sim_dir, "trajectory_*.json")))
    if not per_platform:
        return

    merged: Dict[int, Dict[str, Any]] = {}
    topics: set[str] = set()

    for path in per_platform:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for t in data.get("topics") or []:
            topics.add(t)
        for snap in data.get("snapshots") or []:
            rn = snap.get("round_num")
            if rn is None:
                continue
            slot = merged.setdefault(rn, {
                "round_num": rn,
                "timestamp": snap.get("timestamp") or "",
                "total_posts_created": 0,
                "total_engagements": 0,
                "active_agent_count": 0,
                "belief_positions": {},
                "belief_deltas": {},
                "viral_posts": [],
                "sentiment_summary": {},
            })
            slot["total_posts_created"] += snap.get("total_posts_created") or 0
            slot["total_engagements"] += snap.get("total_engagements") or 0
            slot["active_agent_count"] += snap.get("active_agent_count") or 0
            slot["belief_positions"].update(snap.get("belief_positions") or {})
            slot["belief_deltas"].update(snap.get("belief_deltas") or {})
            slot["viral_posts"].extend(snap.get("viral_posts") or [])
            for k, v in (snap.get("sentiment_summary") or {}).items():
                slot["sentiment_summary"][k] = v

    traj = SimulationTrajectory()
    traj.topics = sorted(topics)
    for rn in sorted(merged.keys()):
        s = merged[rn]
        # belief_positions keys arrive as strings from JSON; RoundSnapshot
        # stores them as ints. Coerce defensively.
        def _coerce(d: Dict[Any, Any]) -> Dict[int, Any]:
            out: Dict[int, Any] = {}
            for k, v in d.items():
                try:
                    out[int(k)] = v
                except (ValueError, TypeError):
                    continue
            return out

        traj.add_snapshot(RoundSnapshot(
            round_num=s["round_num"],
            timestamp=s["timestamp"],
            total_posts_created=s["total_posts_created"],
            total_engagements=s["total_engagements"],
            active_agent_count=s["active_agent_count"],
            belief_positions=_coerce(s["belief_positions"]),
            belief_deltas=_coerce(s["belief_deltas"]),
            viral_posts=s["viral_posts"],
            sentiment_summary=s["sentiment_summary"],
        ))

    traj.save(os.path.join(sim_dir, "trajectory.json"))


class BeliefTracker:
    """Manages belief tracking for a single platform's simulation."""

    def __init__(self, config: Dict[str, Any], simulation_dir: str, platform: str):
        simulation_req = config.get("simulation_requirement", "")
        self.topics = extract_topics_from_requirement(simulation_req)
        self.platform = platform
        self.simulation_dir = simulation_dir

        self.belief_states: Dict[int, BeliefState] = {}
        self.round_analyzer = RoundAnalyzer(self.topics)
        self.trajectory = SimulationTrajectory()
        self.trajectory.topics = self.topics

        # Initialize per-agent beliefs from config
        agent_configs = config.get("agent_configs", [])
        for cfg in agent_configs:
            agent_id = cfg.get("agent_id", 0)
            self.belief_states[agent_id] = BeliefState.from_profile(cfg, self.topics)

        # If a prior run of this platform saved its belief states (pause / crash
        # / branch), restore them so resumed agents keep their accumulated
        # stance instead of snapping back to the profile defaults.
        self._load_belief_states_if_exists()

    # ── Belief state persistence (for pause/resume) ─────────────

    def _belief_states_path(self) -> str:
        return os.path.join(
            self.simulation_dir, f"belief_states_{self.platform}.json"
        )

    def save_belief_states(self) -> None:
        """Persist per-agent belief states so resume starts from this round's stance.

        Best-effort; failures don't block the simulation.
        """
        path = self._belief_states_path()
        try:
            payload = {
                "platform": self.platform,
                "topics": self.topics,
                "agents": {
                    str(aid): bs.to_dict() for aid, bs in self.belief_states.items()
                },
            }
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            pass

    def _load_belief_states_if_exists(self) -> None:
        path = self._belief_states_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        agents = data.get("agents") or {}
        for aid_str, bs_dict in agents.items():
            try:
                aid = int(aid_str)
            except (TypeError, ValueError):
                continue
            if aid in self.belief_states:
                self.belief_states[aid] = BeliefState.from_dict(bs_dict)

    def after_round(
        self,
        db_path: str,
        env,
        active_agents: List[Tuple[int, Any]],
        round_num: int,
        actual_actions: Optional[List[Dict[str, Any]]] = None,
    ):
        """Call after env.step() each round to update beliefs and inject context.

        Args:
            db_path: Path to the platform's SQLite database.
            env: The WonderwallEnv instance.
            active_agents: List of (agent_id, agent) tuples.
            round_num: Current round number.
            actual_actions: If available, the list of action dicts from this round
                (used for trust updates).
        """
        active_ids = [aid for aid, _ in active_agents]

        # Update trust from explicit actions (like/dislike/follow)
        if actual_actions:
            update_trust_from_actions(self.belief_states, actual_actions)

        # Analyze round and update beliefs
        snapshot = self.round_analyzer.analyze_round(
            db_path=db_path,
            belief_states=self.belief_states,
            active_agent_ids=active_ids,
            round_num=round_num,
            actual_actions=actual_actions,
        )
        self.trajectory.add_snapshot(snapshot)

        # Inject updated beliefs into each active agent's system message
        for agent_id, agent in active_agents:
            bs = self.belief_states.get(agent_id)
            if not bs:
                continue
            belief_text = bs.to_prompt_text()
            feedback = self.round_analyzer.generate_agent_feedback(
                snapshot, agent_id, bs
            )
            combined = belief_text
            if feedback:
                combined += "\n\n" + feedback
            if combined.strip():
                inject_belief_context(agent, combined)

        # Persist trajectory after every round so in-progress simulations
        # expose belief data to the frontend (drift chart, article generator,
        # quality metrics) without waiting for sim completion.
        try:
            self.save_trajectory()
        except Exception:
            # Per-round saves are best-effort; the final save at sim end is
            # the authoritative one.
            pass
        # Save belief states so a paused/crashed sim can resume with the same
        # per-agent stance rather than re-initializing from the profile.
        self.save_belief_states()

    def save_trajectory(self):
        """Persist this platform's trajectory and refresh the merged view.

        Writes ``trajectory_<platform>.json`` (authoritative per-platform
        record) and then rebuilds ``trajectory.json`` by merging every
        per-platform file in the sim directory. The merged file is what
        downstream consumers (drift chart, article generator, report agent)
        read, so multi-platform simulations now see the union of belief
        data instead of whichever tracker saved last.
        """
        per_platform_path = os.path.join(
            self.simulation_dir, f"trajectory_{self.platform}.json"
        )
        self.trajectory.save(per_platform_path)
        _merge_per_platform_trajectories(self.simulation_dir)
        return per_platform_path

    def get_summary(self) -> str:
        """Return a short summary of belief dynamics."""
        convergence = self.trajectory._compute_convergence()
        turning = self.trajectory._find_turning_points()
        lines = [f"Belief tracking: {len(self.topics)} topics, {len(self.belief_states)} agents"]
        for topic, conv in convergence.items():
            if conv > 0.1:
                lines.append(f"  {topic}: opinions CONVERGED by {conv:.2f}")
            elif conv < -0.1:
                lines.append(f"  {topic}: opinions POLARIZED by {abs(conv):.2f}")
            else:
                lines.append(f"  {topic}: opinions stayed roughly stable")
        if turning:
            lines.append(f"  {len(turning)} significant belief shifts detected")
        return "\n".join(lines)
