from __future__ import annotations

import os
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP


server = FastMCP(
    name="capabilities",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
)

_MEMORY_STORE: Dict[str, str] = {}


@server.tool()
def search_docs(query: str) -> Dict[str, Any]:
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
def read_metrics(week: str) -> Dict[str, Any]:
    return {
        "week": week,
        "metrics": {
            "uptime": "99.95%",
            "incidents": 2,
            "latency_p95_ms": 180,
        },
    }


@server.tool()
def create_task(title: str, description: str) -> Dict[str, Any]:
    return {
        "id": "TASK-1001",
        "title": title,
        "description": description,
        "status": "created",
    }


@server.tool()
def update_ticket(ticket_id: str, status: str, comment: str | None = None) -> Dict[str, Any]:
    return {
        "ticket_id": ticket_id,
        "status": status,
        "comment": comment,
    }


@server.tool()
def save_memory(key: str, value: str) -> Dict[str, Any]:
    _MEMORY_STORE[key] = value
    return {"status": "saved", "key": key}


@server.tool()
def get_memory(key: str) -> Dict[str, Any]:
    return {"key": key, "value": _MEMORY_STORE.get(key)}


if __name__ == "__main__":
    server.run(transport="streamable-http")
