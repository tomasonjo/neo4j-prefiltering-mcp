"""
Neo4j Vector Search MCP Server (FastMCP)

Env vars:
  NEO4J_URI          – bolt://localhost:7687
  NEO4J_USER         – neo4j
  NEO4J_PASSWORD     – password
  NEO4J_DATABASE     – neo4j
  EMBEDDING_MODEL    – e.g. "openai:text-embedding-3-small", "cohere:embed-english-v3.0"
                       Any spec supported by langchain.embeddings.init_embeddings()
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from typing import Any, Optional

from neo4j import GraphDatabase
from neo4j.time import Date, DateTime
from langchain.embeddings import init_embeddings
from mcp.server.fastmcp import FastMCP

# ── Config ────────────────────────────────────────────────────────────────────

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "openai:text-embedding-3-small")

# ── Helpers ───────────────────────────────────────────────────────────────────


def _detect_type(val: Any) -> str:
    if isinstance(val, list):
        return "vector"
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, int):
        return "int"
    if isinstance(val, float):
        return "float"
    if isinstance(val, (Date, DateTime, date, datetime)):
        return "date"
    return "string"


def _sample_property_types(
    driver, label: str, properties: list[str]
) -> tuple[str | None, dict[str, str]]:
    embedding_prop: str | None = None
    meta_types: dict[str, str] = {}

    for p in properties:
        cypher = (
            f"MATCH (n:`{label}`) WHERE n.`{p}` IS NOT NULL "
            f"WITH n LIMIT 1 RETURN n.`{p}` AS val"
        )
        result, _, _ = driver.execute_query(cypher, database_=NEO4J_DATABASE)
        if not result:
            continue
        t = _detect_type(result[0]["val"])
        if t == "vector":
            embedding_prop = p
        else:
            meta_types[p] = t

    return embedding_prop, meta_types


def _build_where(
    prop_types: dict[str, str], filters: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    for prop, t in prop_types.items():
        safe = prop.replace(" ", "_").replace("-", "_")
        val = filters.get(safe)
        if val is None:
            continue

        if t in ("float", "int"):
            if isinstance(val, dict):
                if val.get("min") is not None:
                    k = f"filt_{safe}_min"
                    clauses.append(f"n.`{prop}` >= ${k}")
                    params[k] = val["min"]
                if val.get("max") is not None:
                    k = f"filt_{safe}_max"
                    clauses.append(f"n.`{prop}` <= ${k}")
                    params[k] = val["max"]
        elif t == "date":
            if isinstance(val, dict):
                if val.get("min") is not None:
                    k = f"filt_{safe}_min"
                    clauses.append(f"n.`{prop}` >= date(${k})")
                    params[k] = val["min"]
                if val.get("max") is not None:
                    k = f"filt_{safe}_max"
                    clauses.append(f"n.`{prop}` <= date(${k})")
                    params[k] = val["max"]
        elif t == "bool":
            k = f"filt_{safe}"
            clauses.append(f"n.`{prop}` = ${k}")
            params[k] = val
        else:  # string exact match
            k = f"filt_{safe}"
            clauses.append(f"n.`{prop}` = ${k}")
            params[k] = val

    where = " AND ".join(clauses)
    return where, params


def _build_server() -> FastMCP:
    """Connect to Neo4j, discover indexes, and build the MCP server."""
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
    except Exception as exc:
        print(f"FATAL: Cannot connect to Neo4j at {NEO4J_URI}: {exc}")
        sys.exit(1)

    records, _, _ = driver.execute_query(
        """
        SHOW INDEXES
        YIELD name, type, labelsOrTypes, properties, options
        WHERE type = 'VECTOR'
        RETURN name, labelsOrTypes, properties, options
        """,
        database_=NEO4J_DATABASE,
    )

    if not records:
        print(
            "FATAL: No vector indexes found in the database. "
            "Create at least one vector index before starting this server.",
            file=sys.stderr,
        )
        driver.close()
        sys.exit(1)

    index_meta: list[dict[str, Any]] = []
    for r in records:
        label = r["labelsOrTypes"][0]
        embed_prop, prop_types = _sample_property_types(
            driver, label, r["properties"]
        )
        meta = {
            "name": r["name"],
            "label": label,
            "embed_prop": embed_prop,
            "prop_types": prop_types,
            "options": r["options"],
        }
        index_meta.append(meta)
        print(
            f"Discovered index '{meta['name']}' on :{label} "
            f"| embedding: {embed_prop} | filters: {list(prop_types.keys())}",
            file=sys.stderr,
        )

    embedder = init_embeddings(EMBEDDING_MODEL)

    # ── MCP Server ────────────────────────────────────────────────────────────

    mcp = FastMCP(
        "Neo4j Vector Search",
        instructions="Semantic vector search over Neo4j knowledge-graph indexes.",
    )

    def _make_search_fn(idx: dict[str, Any]):
        """Factory that closes over a single index's metadata."""

        index_name = idx["name"]
        label = idx["label"]
        embed_prop = idx["embed_prop"]
        prop_types = idx["prop_types"]

        # Build the filter-parameter docstring dynamically
        filter_lines = []
        for prop, t in prop_types.items():
            safe = prop.replace(" ", "_").replace("-", "_")
            if t in ("float", "int"):
                filter_lines.append(
                    f"  {safe}: Optional dict with 'min' and/or 'max' (numeric range)"
                )
            elif t == "date":
                filter_lines.append(
                    f"  {safe}: Optional dict with 'min' and/or 'max' (ISO date strings)"
                )
            elif t == "bool":
                filter_lines.append(f"  {safe}: Optional bool")
            else:
                filter_lines.append(f"  {safe}: Optional str (exact match)")

        filters_doc = "\n".join(filter_lines) if filter_lines else "  (none)"

        async def search(
            query: str,
            top_k: int = 10,
            filters: Optional[dict[str, Any]] = None,
        ) -> str:
            query_vec = embedder.embed_query(query)
            where_clause, cypher_params = _build_where(prop_types, filters or {})
            where_part = f"\n    WHERE {where_clause}" if where_clause else ""

            cypher = (
                f"CYPHER 25\n"
                f"MATCH (n:`{label}`)\n"
                f"  SEARCH n IN (\n"
                f"    VECTOR INDEX {index_name}\n"
                f"    FOR $query_vec{where_part}\n"
                f"    LIMIT $top_k\n"
                f"  ) SCORE AS score\n"
                f"RETURN n {{ .*, `{embed_prop}`: null }} AS doc, score\n"
                f"ORDER BY score DESC"
            )

            cypher_params["query_vec"] = query_vec
            cypher_params["top_k"] = top_k

            rows, _, _ = driver.execute_query(
                cypher, cypher_params, database_=NEO4J_DATABASE
            )
            results = [{"doc": dict(r["doc"]), "score": r["score"]} for r in rows]
            return json.dumps(results, indent=2, default=str)

        search.__name__ = f"search_{index_name}"
        search.__qualname__ = f"search_{index_name}"
        search.__doc__ = (
            f"Semantic vector search over :{label} nodes using the "
            f"'{index_name}' index.\n\n"
            f"Args:\n"
            f"  query: Natural-language search text (will be embedded).\n"
            f"  top_k: Number of results to return (default 10).\n"
            f"  filters: Optional dict of metadata filters:\n"
            f"{filters_doc}\n"
        )
        return search

    # Register one tool per discovered vector index
    for idx in index_meta:
        fn = _make_search_fn(idx)
        mcp.tool()(fn)
        print(f"Registered MCP tool: {fn.__name__}")

    return mcp


def main():
    mcp = _build_server()
    mcp.run()


if __name__ == "__main__":
    main()
