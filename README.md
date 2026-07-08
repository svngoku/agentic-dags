# Agentic DAG Template

[![CI](https://github.com/svngoku/agentic-dags/actions/workflows/ci.yml/badge.svg)](https://github.com/svngoku/agentic-dags/actions/workflows/ci.yml)

This template implements a production-ready agentic DAG with the OpenAI Agents SDK and Prefect.
It includes a minimal wrapper layer that exposes core SDK features (guardrails, sessions, tracing,
streaming, MCP approvals, handoffs) using Pydantic configuration.

## How it works

The work-order flow runs a three-stage DAG:

1. **Plan** — the `Supervisor` agent turns the request into a structured `WorkPlan`.
2. **Execute** — steps are grouped by `parallel_group` and each group runs concurrently.
   Groups flagged `needs_approval` pause the Prefect flow run until approved
   (or run straight through when `AUTO_APPROVE` is set). A failing step is
   recorded as a `failed` `StepResult` instead of aborting the whole run.
3. **Synthesize** — the `Writer` agent produces a markdown report plus an actions
   manifest, and the result is persisted to MCP memory (best effort).

## Quickstart (uv)

1. Install dependencies

```bash
uv sync
```

2. Configure environment

```bash
cp .env.example .env
```

3. Start the local MCP stub (Streamable HTTP)

```bash
uv run python -m src.mcp_stub_server
```

4. Start Prefect UI

```bash
prefect server start
```

5. Run the flow

```bash
uv run python -m src.flows.work_order_flow
# or with a custom request:
uv run python -m src.flows.work_order_flow "Audit last week's incidents"
```

Open the Prefect UI at `http://127.0.0.1:4200` to visualize the DAG.

## Project demos

- Streaming + sessions demo:

```bash
uv run python -m src.projects.interactive_assistant
# or with a custom prompt:
uv run python -m src.projects.interactive_assistant "Summarize this: ..."
```

## Project layout

```
src/
├── agents/          # Agent builders + pydantic I/O schemas
├── flows/           # Prefect flow orchestrating the DAG
├── projects/        # Runnable demos
├── wrapper/         # SDK wrapper: guardrails, sessions, MCP, approvals, ...
├── config.py        # AppConfig (env parsing) + agent wiring
├── mcp_servers.py   # MCP server entry point used by flows
└── mcp_stub_server.py  # Local FastMCP stub with demo tools
tests/               # Offline unit tests (no API key required)
```

## Wrapper features

- **Guardrails**: input/output guardrails and tool guardrails in `src/wrapper/guardrails.py`.
- **Sessions**: SQLite-backed sessions via `src/wrapper/sessions.py`.
- **Tracing**: controlled by `TRACING_*` env vars and `RunConfig` in `src/config.py`.
- **Streaming**: helper in `src/wrapper/runner.py` for `Runner.run_streamed()`.
- **Approvals**: hosted MCP approval hook in `src/wrapper/approvals.py`.
- **Handoffs**: helper in `src/wrapper/handoffs.py`.
- **Pydantic config**: `src/config.py` uses `BaseModel` and `AppConfig.from_env()`.

## Development

Install dev dependencies (pytest, ruff) and run the checks:

```bash
uv sync
uv run ruff check .
uv run pytest
```

The test suite is fully offline — no `OPENAI_API_KEY` needed — and CI runs the
same checks on every push and pull request.

## Notes

- MCP server endpoint is configured with `MCP_URL` (defaults to `http://localhost:8000/mcp`).
- The stub server bind address is configured with `MCP_HOST` / `MCP_PORT`.
- The MCP stub exposes `search_docs`, `read_metrics`, `create_task`, `update_ticket`,
  `save_memory`, `get_memory` tools.
- To enable hosted MCP approvals, set `USE_HOSTED_MCP=true` and `MCP_HOSTED_URL`.

## Docs references

- OpenAI Agents SDK (agents, handoffs, MCP): https://openai.github.io/openai-agents-python/
- OpenAI Agents SDK repository: https://github.com/openai/openai-agents-python
- Prefect flow run pause/resume: https://reference.prefect.io/prefect/flow_runs/
