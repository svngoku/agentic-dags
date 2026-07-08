"""Offline packaging/deployment checks: compose file, Dockerfile, entrypoints.

No Docker daemon required — the compose file is validated by YAML parsing
and structural assertions, the Dockerfile by content checks, and the
entrypoints by importing them (which must never touch the network).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SERVICES = {"prefect-server", "mcp-stub", "worker"}


def _load_compose() -> dict[str, Any]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert isinstance(compose, dict)
    return compose


def _dockerignore_entries() -> set[str]:
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


# ---- docker-compose.yml -------------------------------------------------


def test_compose_parses_with_expected_services() -> None:
    compose = _load_compose()
    services = compose.get("services", {})
    assert set(services) >= EXPECTED_SERVICES, f"missing services in {set(services)}"


def test_compose_worker_wiring() -> None:
    worker = _load_compose()["services"]["worker"]
    env = worker["environment"]
    assert env["PREFECT_API_URL"] == "http://prefect-server:4200/api"
    assert env["MCP_URL"] == "http://mcp-stub:8000/mcp"
    assert "OPENAI_API_KEY" in env
    assert set(worker["depends_on"]) == {"prefect-server", "mcp-stub"}


def test_compose_mcp_stub_binds_all_interfaces() -> None:
    stub = _load_compose()["services"]["mcp-stub"]
    assert stub["environment"]["MCP_HOST"] == "0.0.0.0"
    assert str(stub["environment"]["MCP_PORT"]) == "8000"
    assert "src.mcp_stub_server" in stub["command"]


def test_compose_prefect_server_persists_data() -> None:
    compose = _load_compose()
    server = compose["services"]["prefect-server"]
    assert "prefect server start" in server["command"]
    assert "4200:4200" in server["ports"]
    assert any("/root/.prefect" in volume for volume in server["volumes"])
    assert "prefect-data" in compose["volumes"]


# ---- Dockerfile / .dockerignore -----------------------------------------


def test_dockerfile_layers_lockfile_before_source() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "pyproject.toml" in dockerfile
    assert "uv.lock" in dockerfile
    assert re.search(r"uv sync --frozen(\s|$)", dockerfile)
    assert "--no-dev" in dockerfile
    # Dependency layer must come before the application source copy.
    assert dockerfile.index("uv.lock") < dockerfile.index("COPY src")


def test_dockerignore_excludes_secrets_and_venv() -> None:
    entries = _dockerignore_entries()
    assert ".env" in entries
    assert ".venv" in entries


# ---- Entrypoints import offline ------------------------------------------


def test_serve_module_imports_and_exposes_main() -> None:
    from src import serve

    assert callable(serve.main)
    assert serve.DEFAULT_DEPLOYMENT_NAME == "work-order-orchestrator"


def test_incident_triage_imports_and_builds_request() -> None:
    from examples import incident_triage

    request = incident_triage.build_request()
    assert isinstance(request, str) and request
    assert "incident" in request.lower()
    assert "P1" in request
    assert callable(incident_triage.main)
