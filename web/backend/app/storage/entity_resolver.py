"""
EntityResolver — dedup entities at ingestion time.

Before we MERGE a newly extracted entity into the graph, we check if it's
actually a variant of something already there ("NeuralCoin" vs "Neural Coin"
vs "NC"). Pipeline:

  1. Fuzzy name match (difflib) over existing entity names.
  2. Vector similarity over the entity_embedding index.
  3. High-confidence candidates → auto-merge.
  4. Ambiguous candidates → LLM adjudicates (Graphiti-style reflection).
  5. No candidates → new entity.

The resolver returns a mapping idx → canonical_name_lower that the caller
splices into the entity batch before MERGE; the existing MERGE-by-name_lower
path then naturally hits the canonical node.
"""

import difflib
import logging
from typing import Dict, Any, List, Optional

from neo4j import Session as Neo4jSession

from ..config import Config
from ..utils.llm_client import LLMClient, create_ner_llm_client

logger = logging.getLogger('miroshark.entity_resolver')


_ADJUDICATION_PROMPT = """You are resolving entity duplicates in a knowledge graph.

For each NEW entity, decide whether it refers to the same real-world thing as
one of the CANDIDATE entities already in the graph. Be conservative — only
pick a candidate if you're confident it's the same entity.

NEW ENTITIES:
{new_block}

CANDIDATES:
{candidate_block}

For each new entity, return the letter of the matching candidate, or the
string "none" if no candidate is the same entity.

Return JSON only, with this exact shape:
{{"resolutions": {{"1": "a", "2": "none", ...}}}}"""


class EntityResolver:
    """Resolve newly-extracted entities against existing graph nodes."""

    # Default thresholds (0.0–1.0, higher = more similar)
    AUTO_MERGE_THRESHOLD = 0.95       # above this: merge without asking LLM
    LLM_ADJUDICATE_THRESHOLD = 0.70   # between this and AUTO: LLM decides
    CANDIDATES_PER_ENTITY = 3
    MAX_FUZZY_CORPUS = 2000           # upper bound on names to fuzzy-match against

    def __init__(self, llm_client: Optional[LLMClient] = None):
        # Lazy — only instantiate when first LLM adjudication happens.
        self._llm_override = llm_client
        self._llm: Optional[LLMClient] = None
        # Per-add_text() cached corpus of (uuid, name_lower) tuples per graph.
        # Reset on each resolve_batch call so new merges in this ingestion are visible.

    @property
    def enabled(self) -> bool:
        return Config.ENTITY_RESOLUTION_ENABLED

    def _llm_client(self) -> LLMClient:
        if self._llm is None:
            self._llm = self._llm_override or create_ner_llm_client()
        return self._llm

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def resolve_batch(
        self,
        session: Neo4jSession,
        graph_id: str,
        entities: List[Dict[str, Any]],
        embeddings: List[List[float]],
    ) -> Dict[int, str]:
        """
        Find canonical matches for each new entity against the existing graph.

        Args:
            entities: [{name, type, attributes, ...}] as produced by NER.
            embeddings: aligned list of embedding vectors.

        Returns:
            {entity_idx: canonical_name_lower} — include only entities that
            should be merged with an existing node. Entities without a match
            are omitted (they become new nodes).
        """
        if not self.enabled or not entities:
            return {}

        # Pull the full name corpus once per ingestion batch.
        name_corpus = self._load_name_corpus(session, graph_id)
        if not name_corpus:
            return {}  # empty graph — nothing to resolve against

        candidates_per_entity: List[List[Dict[str, Any]]] = []
        for entity, embedding in zip(entities, embeddings):
            cands = self._find_candidates(
                session, graph_id, entity, embedding, name_corpus
            )
            candidates_per_entity.append(cands)

        resolutions: Dict[int, str] = {}
        ambiguous: List[tuple] = []  # (idx, entity, candidates)

        for idx, (entity, cands) in enumerate(zip(entities, candidates_per_entity)):
            if not cands:
                continue
            top = cands[0]
            # Don't resolve to yourself — caller may be re-ingesting same text.
            if top["name_lower"] == entity["name"].lower():
                continue
            if top["score"] >= self.AUTO_MERGE_THRESHOLD:
                resolutions[idx] = top["name_lower"]
            elif top["score"] >= self.LLM_ADJUDICATE_THRESHOLD:
                ambiguous.append((idx, entity, cands))

        if ambiguous and Config.ENTITY_RESOLUTION_USE_LLM:
            llm_resolutions = self._llm_adjudicate(ambiguous)
            for idx, canonical_nl in llm_resolutions.items():
                resolutions[idx] = canonical_nl

        if resolutions:
            logger.info(
                f"[resolver] Merged {len(resolutions)}/{len(entities)} new entities "
                f"into existing nodes (auto: {len(resolutions) - len(ambiguous)}, "
                f"llm: {len([r for r in ambiguous if r[0] in resolutions])})"
            )
        return resolutions

    # ----------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------

    def _load_name_corpus(
        self, session: Neo4jSession, graph_id: str
    ) -> Dict[str, Dict[str, Any]]:
        """Return {name_lower: {uuid, name, name_lower, summary}} for the graph."""
        try:
            result = session.run(
                """
                MATCH (n:Entity {graph_id: $gid})
                RETURN n.uuid AS uuid, n.name AS name,
                       toLower(n.name) AS name_lower, n.summary AS summary
                LIMIT $max
                """,
                gid=graph_id,
                max=self.MAX_FUZZY_CORPUS,
            )
            return {
                rec["name_lower"]: {
                    "uuid": rec["uuid"],
                    "name": rec["name"],
                    "name_lower": rec["name_lower"],
                    "summary": rec["summary"] or "",
                }
                for rec in result
            }
        except Exception as e:
            logger.warning(f"Failed to load name corpus: {e}")
            return {}

    def _find_candidates(
        self,
        session: Neo4jSession,
        graph_id: str,
        entity: Dict[str, Any],
        embedding: List[float],
        name_corpus: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Combined fuzzy name + vector similarity candidate list.
        Returns up to CANDIDATES_PER_ENTITY items sorted by score desc.
        """
        candidates: Dict[str, Dict[str, Any]] = {}

        # Fuzzy name match (difflib). Close-match cutoff is 0.70 — we still
        # evaluate against thresholds below.
        name_lower = entity["name"].lower()
        close = difflib.get_close_matches(
            name_lower,
            list(name_corpus.keys()),
            n=self.CANDIDATES_PER_ENTITY,
            cutoff=0.70,
        )
        for nl in close:
            corpus_entry = name_corpus[nl]
            ratio = difflib.SequenceMatcher(None, name_lower, nl).ratio()
            candidates[corpus_entry["uuid"]] = {
                **corpus_entry,
                "score": ratio,
                "source": "fuzzy",
            }

        # Vector similarity
        if embedding:
            try:
                result = session.run(
                    """
                    CALL db.index.vector.queryNodes('entity_embedding', $k, $emb)
                    YIELD node, score
                    WHERE node.graph_id = $gid
                      AND toLower(node.name) <> $name_lower
                    RETURN node.uuid AS uuid, node.name AS name,
                           toLower(node.name) AS name_lower,
                           node.summary AS summary, score
                    LIMIT $k
                    """,
                    k=self.CANDIDATES_PER_ENTITY,
                    emb=embedding,
                    gid=graph_id,
                    name_lower=name_lower,
                )
                for rec in result:
                    uuid_ = rec["uuid"]
                    score = float(rec["score"])
                    if uuid_ in candidates:
                        # Keep the higher score
                        if score > candidates[uuid_]["score"]:
                            candidates[uuid_]["score"] = score
                            candidates[uuid_]["source"] = "both"
                    else:
                        candidates[uuid_] = {
                            "uuid": uuid_,
                            "name": rec["name"],
                            "name_lower": rec["name_lower"],
                            "summary": rec["summary"] or "",
                            "score": score,
                            "source": "vector",
                        }
            except Exception as e:
                logger.debug(f"Vector candidate search failed: {e}")

        ranked = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
        return ranked[: self.CANDIDATES_PER_ENTITY]

    def _llm_adjudicate(
        self,
        ambiguous: List[tuple],
    ) -> Dict[int, str]:
        """
        Batched LLM call. For each ambiguous (idx, entity, candidates),
        ask the LLM to pick a candidate letter or "none".

        Returns {idx: canonical_name_lower} for entities the LLM matched.
        """
        # Build the prompt
        new_lines = []
        cand_blocks = []
        # Stable entity numbering starts at 1 for readability in prompt.
        for i, (_, entity, cands) in enumerate(ambiguous, start=1):
            etype = entity.get("type", "Entity")
            summary = self._entity_summary_for_prompt(entity)
            new_lines.append(f'{i}. name="{entity["name"]}", type="{etype}", summary="{summary}"')

            cand_lines = [f"For entity {i}:"]
            for letter, c in zip("abcdefghij", cands):
                csum = (c.get("summary") or "")[:120]
                cand_lines.append(f'   {letter}. name="{c["name"]}", summary="{csum}"')
            cand_blocks.append("\n".join(cand_lines))

        prompt = _ADJUDICATION_PROMPT.format(
            new_block="\n".join(new_lines),
            candidate_block="\n\n".join(cand_blocks),
        )

        try:
            llm = self._llm_client()
            response = llm.chat_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=512,
            )
            resolutions_raw = response.get("resolutions", {}) if isinstance(response, dict) else {}
        except Exception as e:
            logger.warning(f"LLM adjudication failed, treating all ambiguous as new: {e}")
            return {}

        out: Dict[int, str] = {}
        for i, (idx, _, cands) in enumerate(ambiguous, start=1):
            choice = str(resolutions_raw.get(str(i), "none")).strip().lower()
            if choice == "none" or not choice:
                continue
            letter_idx = ord(choice[0]) - ord("a")
            if 0 <= letter_idx < len(cands):
                out[idx] = cands[letter_idx]["name_lower"]

        return out

    @staticmethod
    def _entity_summary_for_prompt(entity: Dict[str, Any]) -> str:
        """Extract a short summary for the LLM prompt."""
        attrs = entity.get("attributes", {}) or {}
        summary = attrs.get("summary") or attrs.get("description") or ""
        if summary:
            return str(summary)[:120].replace('"', "'")
        return f"{entity.get('type', 'Entity')}"
