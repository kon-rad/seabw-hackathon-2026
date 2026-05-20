"""
MiroShark MCP server — expose knowledge-graph queries to Claude Desktop.

Runs over stdio. Configure in Claude Desktop's settings (mcpServers section):

  {
    "mcpServers": {
      "miroshark": {
        "command": "uv",
        "args": [
          "run",
          "--directory",
          "/absolute/path/to/MiroShark/backend",
          "python",
          "mcp_server.py"
        ]
      }
    }
  }

The server connects to the same Neo4j and OpenRouter credentials as the
MiroShark backend via the shared .env file. No additional config needed.

Tools exposed:
  list_graphs         — survey available graphs
  search_graph        — hybrid + rerank retrieval (vector + BM25 + traversal)
  browse_clusters     — community zoom-out with auto-build on first use
  search_communities  — direct semantic search over cluster summaries
  get_community       — expand one cluster with member entities
  list_reports        — reports previously generated for a graph
  get_reasoning_trace — inspect an agent's ReACT decision chain per section
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Make sure `app.*` is importable whether launched via uv or python directly.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# MCP stdio protocol uses stdout for JSON-RPC — absolutely nothing else may
# be printed there. MiroShark's app/utils/logger.py binds a StreamHandler to
# sys.stdout per sub-logger at import time, so we temporarily alias
# sys.stdout -> sys.stderr during imports. The handlers capture the stderr
# reference at bind time; we restore the real stdout afterwards for the MCP
# transport itself to use.
_real_stdout = sys.stdout
sys.stdout = sys.stderr
try:
    from mcp.server import Server  # noqa: E402
    from mcp.server.stdio import stdio_server  # noqa: E402
    from mcp.types import TextContent, Tool  # noqa: E402
    from app.storage import Neo4jStorage  # noqa: E402
finally:
    sys.stdout = _real_stdout

# Belt-and-braces: purge any handlers that still reference the real stdout.
for _name in list(logging.Logger.manager.loggerDict.keys()) + [""]:
    _lg = logging.getLogger(_name)
    for _h in list(_lg.handlers):
        if isinstance(_h, logging.StreamHandler) and _h.stream is _real_stdout:
            _lg.removeHandler(_h)

_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(logging.Formatter("[miroshark-mcp] %(levelname)s %(name)s: %(message)s"))
logging.getLogger().addHandler(_stderr_handler)
logging.getLogger().setLevel(os.environ.get("MIROSHARK_MCP_LOG_LEVEL", "INFO"))
logger = logging.getLogger("miroshark.mcp")

server = Server("miroshark")
_storage: Neo4jStorage | None = None


def _get_storage() -> Neo4jStorage:
    """Lazy-init the storage so the server starts fast even when Neo4j is warming up."""
    global _storage
    if _storage is None:
        logger.info("Connecting to Neo4j...")
        _storage = Neo4jStorage()
    return _storage


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_TOOLS: List[Tool] = [
    Tool(
        name="list_graphs",
        description=(
            "List all knowledge graphs in this MiroShark instance, with entity "
            "and edge counts. Use this first to discover what graph_ids exist."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="search_graph",
        description=(
            "Hybrid retrieval over a graph: vector + BM25 + graph-traversal "
            "fused and reranked with a cross-encoder. Returns edges (facts), "
            "nodes (entities), or both. Supports time-travel (as_of) and "
            "epistemic filtering (kinds=['fact'|'belief'|'observation'])."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "graph_id": {"type": "string", "description": "Target graph UUID"},
                "query": {"type": "string", "description": "Natural-language query"},
                "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                "scope": {
                    "type": "string",
                    "enum": ["edges", "nodes", "both"],
                    "default": "edges",
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["fact", "belief", "observation"]},
                    "description": "Optional epistemic filter",
                },
                "as_of": {
                    "type": "string",
                    "description": "ISO-8601 point-in-time filter (default = current view)",
                },
                "include_invalidated": {
                    "type": "boolean",
                    "description": "Include superseded edges (default false)",
                    "default": False,
                },
            },
            "required": ["graph_id", "query"],
        },
    ),
    Tool(
        name="browse_clusters",
        description=(
            "Zoom-out over a graph: return LLM-summarized community clusters. "
            "Auto-builds clusters on first call if none exist. Use this to "
            "discover the major themes before drilling into facts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "graph_id": {"type": "string"},
                "query": {
                    "type": "string",
                    "description": "Optional semantic query; empty = largest clusters",
                },
                "limit": {"type": "integer", "default": 8},
            },
            "required": ["graph_id"],
        },
    ),
    Tool(
        name="search_communities",
        description="Semantic search over cluster summaries only (faster than search_graph).",
        inputSchema={
            "type": "object",
            "properties": {
                "graph_id": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["graph_id", "query"],
        },
    ),
    Tool(
        name="get_community",
        description="Expand one cluster — returns title, summary, and member entities.",
        inputSchema={
            "type": "object",
            "properties": {"community_uuid": {"type": "string"}},
            "required": ["community_uuid"],
        },
    ),
    Tool(
        name="list_reports",
        description="List prior reports generated for a graph (most recent first).",
        inputSchema={
            "type": "object",
            "properties": {"graph_id": {"type": "string"}},
            "required": ["graph_id"],
        },
    ),
    Tool(
        name="get_reasoning_trace",
        description=(
            "Return the report-agent's full decision chain for one report section: "
            "thoughts, tool calls, observations, and final conclusion. "
            "Use list_reports → list_report_sections → this to navigate."
        ),
        inputSchema={
            "type": "object",
            "properties": {"section_uuid": {"type": "string"}},
            "required": ["section_uuid"],
        },
    ),
    Tool(
        name="list_report_sections",
        description="List the sections of a report in order.",
        inputSchema={
            "type": "object",
            "properties": {"report_uuid": {"type": "string"}},
            "required": ["report_uuid"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> List[Tool]:
    return _TOOLS


# ---------------------------------------------------------------------------
# Tool handlers (sync Neo4j calls run in a thread to avoid blocking the event loop)
# ---------------------------------------------------------------------------

async def _to_thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


_NOISY_FIELDS = ("embedding", "fact_embedding", "summary_embedding")


def _strip_embeddings(obj: Any) -> Any:
    """Drop raw embedding vectors from a result payload — they're noise for LLMs."""
    if isinstance(obj, dict):
        return {k: _strip_embeddings(v) for k, v in obj.items() if k not in _NOISY_FIELDS}
    if isinstance(obj, list):
        return [_strip_embeddings(v) for v in obj]
    return obj


def _list_graphs() -> List[Dict[str, Any]]:
    s = _get_storage()
    with s._driver.session() as sess:
        result = sess.run(
            """
            MATCH (n:Entity)
            WITH n.graph_id AS gid, count(n) AS entities
            OPTIONAL MATCH ()-[r:RELATION {graph_id: gid}]->()
            RETURN gid, entities, count(DISTINCT r) AS edges
            ORDER BY entities DESC
            """
        )
        return [dict(rec) for rec in result]


def _run_tool_sync(name: str, args: Dict[str, Any]) -> str:
    """Dispatch table — each branch returns a string to hand to Claude Desktop."""
    s = _get_storage()

    if name == "list_graphs":
        graphs = _list_graphs()
        if not graphs:
            return "No graphs found."
        lines = [f"{len(graphs)} graph(s):"]
        for g in graphs:
            lines.append(f"  {g['gid']}  ({g['entities']} entities, {g['edges']} edges)")
        return "\n".join(lines)

    if name == "search_graph":
        res = s.search(
            graph_id=args["graph_id"],
            query=args["query"],
            limit=int(args.get("limit", 10)),
            scope=args.get("scope", "edges"),
            as_of=args.get("as_of"),
            include_invalidated=bool(args.get("include_invalidated", False)),
            kinds=args.get("kinds"),
        )
        return json.dumps(_strip_embeddings(res), indent=2, default=str)

    if name == "browse_clusters":
        graph_id = args["graph_id"]
        query = args.get("query") or ""
        limit = int(args.get("limit", 8))
        existing = s.list_communities(graph_id)
        if not existing:
            stats = s.build_communities(graph_id)
            logger.info(f"Auto-built communities: {stats}")
            existing = s.list_communities(graph_id)
        if not existing:
            return "No clusters could be formed (graph too small / sparse)."
        if query.strip():
            hits = s.search_communities(graph_id, query.strip(), limit=limit)
            payload = {"mode": "search", "query": query, "results": hits}
        else:
            payload = {"mode": "browse", "clusters": existing[:limit]}
        return json.dumps(_strip_embeddings(payload), indent=2, default=str)

    if name == "search_communities":
        hits = s.search_communities(
            graph_id=args["graph_id"],
            query=args["query"],
            limit=int(args.get("limit", 5)),
        )
        return json.dumps(_strip_embeddings(hits), indent=2, default=str)

    if name == "get_community":
        detail = s.get_community(args["community_uuid"])
        if not detail:
            return f"No community found with uuid={args['community_uuid']}"
        return json.dumps(_strip_embeddings(detail), indent=2, default=str)

    if name == "list_reports":
        reports = s.list_reports(args["graph_id"])
        return json.dumps(_strip_embeddings(reports), indent=2, default=str)

    if name == "list_report_sections":
        sections = s.list_report_sections(args["report_uuid"])
        return json.dumps(_strip_embeddings(sections), indent=2, default=str)

    if name == "get_reasoning_trace":
        trace = s.get_reasoning_trace(args["section_uuid"])
        if not trace:
            return f"No section found with uuid={args['section_uuid']}"
        return json.dumps(_strip_embeddings(trace), indent=2, default=str)

    return f"Unknown tool: {name}"


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    try:
        logger.info(f"call_tool: {name} args={list(arguments.keys())}")
        out = await _to_thread(_run_tool_sync, name, arguments)
    except Exception as e:
        logger.exception(f"tool {name} failed")
        out = f"Error in {name}: {e}"
    return [TextContent(type="text", text=out)]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    logger.info("MiroShark MCP server starting (stdio)")
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
