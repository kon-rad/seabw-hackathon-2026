"""
SearchService — hybrid search over Neo4j graph data.

Three retrieval strategies run in parallel:
  1. Vector similarity (cosine over embeddings)
  2. BM25 keyword (Neo4j fulltext index)
  3. Graph traversal (1–N hop BFS from top seed entities)

Candidates from all three are fused, then passed through a cross-encoder
reranker (if enabled) for final ordering.
"""

import logging
from typing import List, Dict, Any, Optional

from neo4j import Session as Neo4jSession

from ..config import Config
from .embedding_service import EmbeddingService
from .reranker_service import RerankerService

logger = logging.getLogger('miroshark.search')

# Bi-temporal filter fragment, parameterised so we can inject it into every edge query.
# $as_of is an ISO-8601 string or null; $include_invalidated is a boolean.
#   - When as_of is null AND include_invalidated is false: current view
#     (drops any edge with invalid_at set).
#   - When as_of is null AND include_invalidated is true: everything.
#   - When as_of is set: point-in-time view (edge was valid at that moment).
_EDGE_TEMPORAL_FILTER = (
    "(($as_of IS NULL AND ($include_invalidated OR r.invalid_at IS NULL))"
    " OR "
    "($as_of IS NOT NULL"
    "  AND (r.valid_at IS NULL OR r.valid_at <= $as_of)"
    "  AND (r.invalid_at IS NULL OR r.invalid_at > $as_of)))"
)

# Epistemic filter fragment. $kinds is a list of "fact"/"belief"/"observation",
# or null for "all kinds".
_EDGE_KIND_FILTER = "($kinds IS NULL OR r.kind IN $kinds)"

# Cypher for vector search on edges (facts)
_VECTOR_SEARCH_EDGES = f"""
CALL db.index.vector.queryRelationships('fact_embedding', $limit, $query_vector)
YIELD relationship, score
WITH relationship AS r, score
WHERE r.graph_id = $graph_id AND {_EDGE_TEMPORAL_FILTER} AND {_EDGE_KIND_FILTER}
RETURN r, score
ORDER BY score DESC
LIMIT $limit
"""

# Cypher for vector search on nodes (entities)
_VECTOR_SEARCH_NODES = """
CALL db.index.vector.queryNodes('entity_embedding', $limit, $query_vector)
YIELD node, score
WHERE node.graph_id = $graph_id
RETURN node AS n, score
ORDER BY score DESC
LIMIT $limit
"""

# Cypher for fulltext (BM25) search on edges
_FULLTEXT_SEARCH_EDGES = f"""
CALL db.index.fulltext.queryRelationships('fact_fulltext', $query_text)
YIELD relationship, score
WITH relationship AS r, score
WHERE r.graph_id = $graph_id AND {_EDGE_TEMPORAL_FILTER} AND {_EDGE_KIND_FILTER}
RETURN r, score
ORDER BY score DESC
LIMIT $limit
"""

# Cypher for fulltext search on nodes
_FULLTEXT_SEARCH_NODES = """
CALL db.index.fulltext.queryNodes('entity_fulltext', $query_text)
YIELD node, score
WHERE node.graph_id = $graph_id
RETURN node AS n, score
ORDER BY score DESC
LIMIT $limit
"""

# Graph traversal: edges within N hops of seed entities (1-hop default).
# Depth is hard-coded per query because Neo4j doesn't parameterise path length.
_GRAPH_TRAVERSAL_EDGES_1HOP = f"""
MATCH (seed:Entity)-[r:RELATION]-(other:Entity)
WHERE seed.uuid IN $seed_uuids
  AND r.graph_id = $graph_id
  AND {_EDGE_TEMPORAL_FILTER}
  AND {_EDGE_KIND_FILTER}
RETURN DISTINCT r, 1.0 AS score
LIMIT $limit
"""

_GRAPH_TRAVERSAL_EDGES_2HOP = """
MATCH (seed:Entity)-[rels:RELATION*1..2]-(other:Entity)
WHERE seed.uuid IN $seed_uuids
  AND all(x IN rels WHERE x.graph_id = $graph_id)
  AND all(x IN rels WHERE
    ($as_of IS NULL AND ($include_invalidated OR x.invalid_at IS NULL))
    OR ($as_of IS NOT NULL
        AND (x.valid_at IS NULL OR x.valid_at <= $as_of)
        AND (x.invalid_at IS NULL OR x.invalid_at > $as_of)))
  AND all(x IN rels WHERE $kinds IS NULL OR x.kind IN $kinds)
UNWIND rels AS r
WITH DISTINCT r, size(rels) AS hops
RETURN r, 1.0 / hops AS score
ORDER BY score DESC
LIMIT $limit
"""

# Graph traversal: entities within 1 hop of seed entities (neighbors).
_GRAPH_TRAVERSAL_NODES_1HOP = f"""
MATCH (seed:Entity)-[r:RELATION]-(n:Entity)
WHERE seed.uuid IN $seed_uuids
  AND r.graph_id = $graph_id
  AND {_EDGE_TEMPORAL_FILTER}
  AND {_EDGE_KIND_FILTER}
  AND NOT n.uuid IN $seed_uuids
RETURN DISTINCT n, 1.0 AS score
LIMIT $limit
"""


class SearchService:
    """Hybrid search combining vector similarity and keyword matching."""

    # Fusion weights across 3 retrieval strategies.
    # The reranker does the final ordering; these only shape the candidate pool.
    VECTOR_WEIGHT = 0.5
    KEYWORD_WEIGHT = 0.25
    GRAPH_WEIGHT = 0.25

    def __init__(
        self,
        embedding_service: EmbeddingService,
        reranker_service: Optional[RerankerService] = None,
    ):
        self.embedding = embedding_service
        self.reranker = reranker_service or RerankerService()

    def _candidate_pool_size(self, limit: int) -> int:
        """How many candidates to fetch from each retrieval strategy before reranking."""
        if self.reranker.enabled:
            return max(Config.RERANKER_CANDIDATES, limit * 2)
        return limit * 2

    def _pick_seed_uuids(
        self,
        session: Neo4jSession,
        graph_id: str,
        query_vector: List[float],
        query: str,
    ) -> List[str]:
        """Top-K entity UUIDs to use as traversal seeds (from vector + BM25 hits)."""
        n_seeds = Config.GRAPH_SEARCH_SEEDS
        seeds: Dict[str, float] = {}

        # Prefer vector hits for semantic seeds
        for r in self._run_node_vector_search(session, graph_id, query_vector, n_seeds):
            seeds[r["uuid"]] = max(seeds.get(r["uuid"], 0.0), r["_score"])

        # Top up with BM25 if we have room
        if len(seeds) < n_seeds:
            for r in self._run_node_keyword_search(session, graph_id, query, n_seeds):
                seeds.setdefault(r["uuid"], r["_score"])
                if len(seeds) >= n_seeds:
                    break

        return list(seeds.keys())[:n_seeds]

    def search_edges(
        self,
        session: Neo4jSession,
        graph_id: str,
        query: str,
        limit: int = 10,
        as_of: Optional[str] = None,
        include_invalidated: bool = False,
        kinds: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search edges via vector + BM25 + graph traversal, then rerank.

        Args:
            as_of: ISO-8601 point-in-time filter.
            include_invalidated: if True, keep superseded edges.
            kinds: list like ["fact"] / ["belief"] / ["fact","observation"].
        """
        query_vector = self.embedding.embed(query)
        pool = self._candidate_pool_size(limit)

        vector_results = self._run_edge_vector_search(
            session, graph_id, query_vector, pool, as_of, include_invalidated, kinds,
        )
        keyword_results = self._run_edge_keyword_search(
            session, graph_id, query, pool, as_of, include_invalidated, kinds,
        )

        graph_results: List[Dict[str, Any]] = []
        if Config.GRAPH_SEARCH_ENABLED:
            seeds = self._pick_seed_uuids(session, graph_id, query_vector, query)
            if seeds:
                graph_results = self._run_edge_graph_traversal(
                    session, graph_id, seeds, pool, as_of, include_invalidated, kinds,
                )

        return self._merge_results(
            vector_results,
            keyword_results,
            graph_results,
            key="uuid",
            limit=limit,
            query=query,
            rerank_text_key="fact",
        )

    def search_nodes(
        self,
        session: Neo4jSession,
        graph_id: str,
        query: str,
        limit: int = 10,
        as_of: Optional[str] = None,
        include_invalidated: bool = False,
        kinds: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search nodes via vector + BM25 + graph traversal, then rerank.

        Temporal + kind filters apply to the RELATION edges traversed for
        neighbour expansion. Node vector / BM25 hits ignore those filters since
        entities themselves aren't tagged with kind.
        """
        query_vector = self.embedding.embed(query)
        pool = self._candidate_pool_size(limit)

        vector_results = self._run_node_vector_search(session, graph_id, query_vector, pool)
        keyword_results = self._run_node_keyword_search(session, graph_id, query, pool)

        graph_results: List[Dict[str, Any]] = []
        if Config.GRAPH_SEARCH_ENABLED:
            seed_uuids = [r["uuid"] for r in vector_results[: Config.GRAPH_SEARCH_SEEDS]]
            if not seed_uuids:
                seed_uuids = [r["uuid"] for r in keyword_results[: Config.GRAPH_SEARCH_SEEDS]]
            if seed_uuids:
                graph_results = self._run_node_graph_traversal(
                    session, graph_id, seed_uuids, pool, as_of, include_invalidated, kinds,
                )

        return self._merge_results(
            vector_results,
            keyword_results,
            graph_results,
            key="uuid",
            limit=limit,
            query=query,
            rerank_text_key="_node_rerank_text",
        )

    def _run_edge_vector_search(
        self,
        session: Neo4jSession,
        graph_id: str,
        query_vector: List[float],
        limit: int,
        as_of: Optional[str] = None,
        include_invalidated: bool = False,
        kinds: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Run vector similarity search on edge fact_embedding."""
        try:
            result = session.run(
                _VECTOR_SEARCH_EDGES,
                graph_id=graph_id,
                query_vector=query_vector,
                limit=limit,
                as_of=as_of,
                include_invalidated=include_invalidated,
                kinds=kinds,
            )
            return [
                {**dict(record["r"]), "uuid": record["r"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Vector edge search failed (index may not exist yet): {e}")
            return []

    def _run_edge_keyword_search(
        self,
        session: Neo4jSession,
        graph_id: str,
        query: str,
        limit: int,
        as_of: Optional[str] = None,
        include_invalidated: bool = False,
        kinds: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Run fulltext (BM25) search on edge fact + name."""
        try:
            safe_query = self._escape_lucene(query)
            result = session.run(
                _FULLTEXT_SEARCH_EDGES,
                graph_id=graph_id,
                query_text=safe_query,
                limit=limit,
                as_of=as_of,
                include_invalidated=include_invalidated,
                kinds=kinds,
            )
            return [
                {**dict(record["r"]), "uuid": record["r"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Keyword edge search failed: {e}")
            return []

    def _run_node_vector_search(
        self, session: Neo4jSession, graph_id: str, query_vector: List[float], limit: int
    ) -> List[Dict[str, Any]]:
        """Run vector similarity search on entity embedding."""
        try:
            result = session.run(
                _VECTOR_SEARCH_NODES,
                graph_id=graph_id,
                query_vector=query_vector,
                limit=limit,
            )
            return [
                {**dict(record["n"]), "uuid": record["n"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Vector node search failed: {e}")
            return []

    def _run_node_keyword_search(
        self, session: Neo4jSession, graph_id: str, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Run fulltext search on entity name + summary."""
        try:
            safe_query = self._escape_lucene(query)
            result = session.run(
                _FULLTEXT_SEARCH_NODES,
                graph_id=graph_id,
                query_text=safe_query,
                limit=limit,
            )
            return [
                {**dict(record["n"]), "uuid": record["n"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Keyword node search failed: {e}")
            return []

    def _run_edge_graph_traversal(
        self,
        session: Neo4jSession,
        graph_id: str,
        seed_uuids: List[str],
        limit: int,
        as_of: Optional[str] = None,
        include_invalidated: bool = False,
        kinds: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """BFS from seed entity UUIDs, returning edges within GRAPH_SEARCH_HOPS."""
        cypher = (
            _GRAPH_TRAVERSAL_EDGES_2HOP
            if Config.GRAPH_SEARCH_HOPS >= 2
            else _GRAPH_TRAVERSAL_EDGES_1HOP
        )
        try:
            result = session.run(
                cypher,
                graph_id=graph_id,
                seed_uuids=seed_uuids,
                limit=limit,
                as_of=as_of,
                include_invalidated=include_invalidated,
                kinds=kinds,
            )
            return [
                {**dict(record["r"]), "uuid": record["r"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Graph edge traversal failed: {e}")
            return []

    def _run_node_graph_traversal(
        self,
        session: Neo4jSession,
        graph_id: str,
        seed_uuids: List[str],
        limit: int,
        as_of: Optional[str] = None,
        include_invalidated: bool = False,
        kinds: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """1-hop neighbors of seed entities — expands the entity candidate pool."""
        try:
            result = session.run(
                _GRAPH_TRAVERSAL_NODES_1HOP,
                graph_id=graph_id,
                seed_uuids=seed_uuids,
                limit=limit,
                as_of=as_of,
                include_invalidated=include_invalidated,
                kinds=kinds,
            )
            return [
                {**dict(record["n"]), "uuid": record["n"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Graph node traversal failed: {e}")
            return []

    def _merge_results(
        self,
        vector_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        graph_results: List[Dict[str, Any]],
        key: str,
        limit: int,
        query: Optional[str] = None,
        rerank_text_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Merge vector, BM25, and graph-traversal results with weighted scoring, then rerank.

        Flow:
          1. Normalize each strategy's scores to [0, 1].
          2. Weighted fuse into `fused_score`.
          3. Take top N (RERANKER_CANDIDATES) by fused score.
          4. Cross-encoder rerank (query, doc) pairs → final `score`.
          5. Return top `limit`.

        If reranker is disabled or fails, falls back to fused score ordering.
        """
        def _normalize(results: List[Dict[str, Any]]) -> Dict[str, float]:
            m = max((r["_score"] for r in results), default=1.0) or 1.0
            return {r[key]: r["_score"] / m for r in results}

        v_scores = _normalize(vector_results)
        k_scores = _normalize(keyword_results)
        g_scores = _normalize(graph_results)

        # Build combined result map (first occurrence wins for properties)
        all_items: Dict[str, Dict[str, Any]] = {}
        for source in (vector_results, keyword_results, graph_results):
            for r in source:
                if r[key] not in all_items:
                    all_items[r[key]] = {k: v for k, v in r.items() if k != "_score"}

        # Calculate fused hybrid scores
        scored = []
        for uid, item in all_items.items():
            v = v_scores.get(uid, 0.0)
            kw = k_scores.get(uid, 0.0)
            g = g_scores.get(uid, 0.0)
            fused = self.VECTOR_WEIGHT * v + self.KEYWORD_WEIGHT * kw + self.GRAPH_WEIGHT * g
            item["fused_score"] = fused
            item["score"] = fused  # default; reranker may overwrite
            item["_sources"] = "".join([
                "v" if uid in v_scores else "",
                "k" if uid in k_scores else "",
                "g" if uid in g_scores else "",
            ])
            scored.append(item)

        scored.sort(key=lambda x: x["fused_score"], reverse=True)

        # Attempt rerank on the top candidates
        if query and rerank_text_key and self.reranker.enabled and scored:
            candidate_pool = scored[: Config.RERANKER_CANDIDATES]
            docs = [self._rerank_text(item, rerank_text_key) for item in candidate_pool]
            if all(docs):  # skip rerank if any candidate has no text
                ranked = self.reranker.rerank_with_indices(query, docs)
                if ranked is not None:
                    reordered = []
                    for idx, rscore in ranked:
                        item = candidate_pool[idx]
                        item["score"] = float(rscore)
                        reordered.append(item)
                    return reordered[:limit]

        return scored[:limit]

    @staticmethod
    def _rerank_text(item: Dict[str, Any], key: str) -> str:
        """Extract the text used for cross-encoder rerank."""
        if key == "_node_rerank_text":
            name = item.get("name", "") or ""
            summary = item.get("summary", "") or ""
            return f"{name}: {summary}".strip(": ").strip()
        return str(item.get(key, "") or "")

    @staticmethod
    def _escape_lucene(query: str) -> str:
        """Escape special Lucene query characters."""
        special = r'+-&|!(){}[]^"~*?:\/'
        result = []
        for ch in query:
            if ch in special:
                result.append('\\')
            result.append(ch)
        return ''.join(result)
