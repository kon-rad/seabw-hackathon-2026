"""
MiroShark Storage Layer

Local graph storage replacing Zep Cloud:
- Neo4j CE for graph persistence
- Ollama for embeddings (nomic-embed-text)
- LLM-based NER/RE extraction
- Hybrid search (vector + keyword)
"""

from .graph_storage import GraphStorage
from .neo4j_storage import Neo4jStorage
from .community_builder import CommunityBuilder
from .contradiction_detector import ContradictionDetector
from .embedding_service import EmbeddingService, EmbeddingError
from .entity_resolver import EntityResolver
from .ner_extractor import NERExtractor
from .reasoning_trace import ReasoningTraceRecorder
from .reranker_service import RerankerService
from .search_service import SearchService

__all__ = [
    "GraphStorage",
    "Neo4jStorage",
    "CommunityBuilder",
    "ContradictionDetector",
    "EmbeddingService",
    "EmbeddingError",
    "EntityResolver",
    "NERExtractor",
    "ReasoningTraceRecorder",
    "RerankerService",
    "SearchService",
]
