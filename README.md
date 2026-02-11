# Agentic DAG Template

This template implements a production-ready agentic DAG with the OpenAI Agents SDK and Prefect.
It includes a minimal wrapper layer that exposes core SDK features (guardrails, sessions, tracing,
streaming, MCP approvals, handoffs) using Pydantic configuration.

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
```

Open the Prefect UI at `http://127.0.0.1:4200` to visualize the DAG.

## Project demos

- Streaming + sessions demo:

```bash
uv run python -m src.projects.interactive_assistant
```

## Wrapper features

- **Guardrails**: input/output guardrails and tool guardrails in `src/wrapper/guardrails.py`.
- **Sessions**: SQLite-backed sessions via `src/wrapper/sessions.py`.
- **Tracing**: controlled by `TRACING_*` env vars and `RunConfig` in `src/config.py`.
- **Streaming**: helper in `src/wrapper/runner.py` for `Runner.run_streamed()`.
- **Approvals**: hosted MCP approval hook in `src/wrapper/approvals.py`.
- **Handoffs**: helper in `src/wrapper/handoffs.py`.
- **Pydantic config**: `src/config.py` uses `BaseModel` and `AppConfig.from_env()`.

## Notes

- MCP server endpoint is configured with `MCP_URL` (defaults to `http://localhost:8000/mcp`).
- The MCP stub exposes `search_docs`, `read_metrics`, `create_task`, `update_ticket` tools.
- To enable hosted MCP approvals, set `USE_HOSTED_MCP=true` and `MCP_HOSTED_URL`.

## Docs references

- OpenAI Agents SDK (agents, handoffs, MCP): https://openai.github.io/openai-agents-python/
- OpenAI Agents SDK repository: https://github.com/openai/openai-agents-python
- Prefect flow run pause/resume: https://reference.prefect.io/prefect/flow_runs/
