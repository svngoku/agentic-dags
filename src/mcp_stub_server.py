"""Local MCP stub server (Streamable HTTP) exposing demo tools.

Run with ``uv run python -m src.mcp_stub_server``. All tools return canned
data so the DAG can be exercised end-to-end without real backends. Tool
docstrings matter: FastMCP surfaces them as tool descriptions to the LLM.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

server = FastMCP(
    name="capabilities",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
)

#: In-memory key/value store backing ``save_memory`` / ``get_memory``.
#: Contents are lost when the server restarts.
_MEMORY_STORE: dict[str, str] = {}


@server.tool()
def search_docs(query: str) -> dict[str, Any]:
    """Search internal documentation and return matching links."""
    return {
        "query": query,
        "results": [
            {
                "title": "Weekly status guidelines",
                "url": "https://example.internal/docs/status",
            }
        ],
    }


@server.tool()
def read_metrics(week: str) -> dict[str, Any]:
    """Read operational metrics (uptime, incidents, latency) for a given week."""
    return {
        "week": week,
        "metrics": {
            "uptime": "99.95%",
            "incidents": 2,
            "latency_p95_ms": 180,
        },
    }


@server.tool()
def create_task(title: str, description: str) -> dict[str, Any]:
    """Create a follow-up task and return its identifier."""
    return {
        "id": "TASK-1001",
        "title": title,
        "description": description,
        "status": "created",
    }


@server.tool()
def update_ticket(ticket_id: str, status: str, comment: str | None = None) -> dict[str, Any]:
    """Update a ticket's status, optionally attaching a comment."""
    return {
        "ticket_id": ticket_id,
        "status": status,
        "comment": comment,
    }


@server.tool()
def save_memory(key: str, value: str) -> dict[str, Any]:
    """Persist a value in the in-memory store under ``key``."""
    _MEMORY_STORE[key] = value
    return {"status": "saved", "key": key}


@server.tool()
def get_memory(key: str) -> dict[str, Any]:
    """Fetch a previously saved value by ``key`` (``null`` when absent)."""
    return {"key": key, "value": _MEMORY_STORE.get(key)}


if __name__ == "__main__":
    server.run(transport="streamable-http")
