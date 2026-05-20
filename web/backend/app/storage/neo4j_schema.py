"""
Neo4j Schema — Cypher queries for index creation and schema management.

Called by Neo4jStorage.create_graph() to set up vector + fulltext indexes.
Vector dimensions are read from Config.EMBEDDING_DIMENSIONS at startup.
"""

from ..config import Config

# Constraints
CREATE_GRAPH_UUID_CONSTRAINT = """
CREATE CONSTRAINT graph_uuid IF NOT EXISTS
FOR (g:Graph) REQUIRE g.graph_id IS UNIQUE
"""

CREATE_ENTITY_UUID_CONSTRAINT = """
CREATE CONSTRAINT entity_uuid IF NOT EXISTS
FOR (n:Entity) REQUIRE n.uuid IS UNIQUE
"""

CREATE_EPISODE_UUID_CONSTRAINT = """
CREATE CONSTRAINT episode_uuid IF NOT EXISTS
FOR (ep:Episode) REQUIRE ep.uuid IS UNIQUE
"""

CREATE_COMMUNITY_UUID_CONSTRAINT = """
CREATE CONSTRAINT community_uuid IF NOT EXISTS
FOR (c:Community) REQUIRE c.uuid IS UNIQUE
"""

# Reasoning-trace subgraph: persists the report agent's ReACT loop as a
# traversable decision tree.
CREATE_REPORT_UUID_CONSTRAINT = """
CREATE CONSTRAINT report_uuid IF NOT EXISTS
FOR (r:Report) REQUIRE r.uuid IS UNIQUE
"""

CREATE_REPORT_SECTION_UUID_CONSTRAINT = """
CREATE CONSTRAINT report_section_uuid IF NOT EXISTS
FOR (s:ReportSection) REQUIRE s.uuid IS UNIQUE
"""

CREATE_REASONING_STEP_UUID_CONSTRAINT = """
CREATE CONSTRAINT reasoning_step_uuid IF NOT EXISTS
FOR (st:ReasoningStep) REQUIRE st.uuid IS UNIQUE
"""

# Fulltext indexes (for BM25 keyword search)
CREATE_ENTITY_FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS
FOR (n:Entity) ON EACH [n.name, n.summary]
"""

CREATE_FACT_FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX fact_fulltext IF NOT EXISTS
FOR ()-[r:RELATION]-() ON EACH [r.fact, r.name]
"""

# Bi-temporal indexes — accelerate point-in-time ("as-of") queries and
# current-only filters over the RELATION edge set.
CREATE_VALID_AT_INDEX = """
CREATE INDEX relation_valid_at IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.valid_at)
"""

CREATE_INVALID_AT_INDEX = """
CREATE INDEX relation_invalid_at IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.invalid_at)
"""

# Idempotent backfill: older edges were created before bi-temporal was wired
# up and have valid_at = NULL. Copy created_at into valid_at for those so
# time-range queries include them.
BACKFILL_VALID_AT = """
MATCH ()-[r:RELATION]-()
WHERE r.valid_at IS NULL AND r.created_at IS NOT NULL
SET r.valid_at = r.created_at
"""

# Epistemic kind — ground-truth facts vs. agent beliefs vs. observations.
# Default "fact" is backfilled onto legacy edges so existing queries behave
# identically to pre-label behavior.
CREATE_KIND_INDEX = """
CREATE INDEX relation_kind IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.kind)
"""

BACKFILL_KIND = """
MATCH ()-[r:RELATION]-()
WHERE r.kind IS NULL
SET r.kind = 'fact'
"""


def get_vector_index_queries() -> list[str]:
    """Return vector index CREATE queries using the configured dimensions."""
    dims = Config.EMBEDDING_DIMENSIONS
    return [
        f"""
CREATE VECTOR INDEX entity_embedding IF NOT EXISTS
FOR (n:Entity) ON (n.embedding)
OPTIONS {{indexConfig: {{
    `vector.dimensions`: {dims},
    `vector.similarity_function`: 'cosine'
}}}}
""",
        f"""
CREATE VECTOR INDEX fact_embedding IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.fact_embedding)
OPTIONS {{indexConfig: {{
    `vector.dimensions`: {dims},
    `vector.similarity_function`: 'cosine'
}}}}
""",
        f"""
CREATE VECTOR INDEX community_embedding IF NOT EXISTS
FOR (c:Community) ON (c.summary_embedding)
OPTIONS {{indexConfig: {{
    `vector.dimensions`: {dims},
    `vector.similarity_function`: 'cosine'
}}}}
""",
    ]


def get_all_schema_queries() -> list[str]:
    """All schema queries to run on startup."""
    return [
        CREATE_GRAPH_UUID_CONSTRAINT,
        CREATE_ENTITY_UUID_CONSTRAINT,
        CREATE_EPISODE_UUID_CONSTRAINT,
        CREATE_COMMUNITY_UUID_CONSTRAINT,
        CREATE_REPORT_UUID_CONSTRAINT,
        CREATE_REPORT_SECTION_UUID_CONSTRAINT,
        CREATE_REASONING_STEP_UUID_CONSTRAINT,
        *get_vector_index_queries(),
        CREATE_ENTITY_FULLTEXT_INDEX,
        CREATE_FACT_FULLTEXT_INDEX,
        CREATE_VALID_AT_INDEX,
        CREATE_INVALID_AT_INDEX,
        BACKFILL_VALID_AT,
        CREATE_KIND_INDEX,
        BACKFILL_KIND,
    ]
