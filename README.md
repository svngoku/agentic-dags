# Agentic DAG Template

[![CI](https://github.com/svngoku/agentic-dags/actions/workflows/ci.yml/badge.svg)](https://github.com/svngoku/agentic-dags/actions/workflows/ci.yml)

This template implements a production-ready agentic DAG with the OpenAI Agents SDK and Prefect.
It includes a minimal wrapper layer that exposes core SDK features (guardrails, sessions, tracing,
streaming, MCP approvals, handoffs) using Pydantic configuration — plus production hardening:
LLM retry/fallback, token & cost budgets, typed human-in-the-loop approvals, step checkpointing,
and a Docker/compose deployment story.

## How it works

The work-order flow runs a three-stage DAG:

1. **Plan** — the `Supervisor` agent turns the request into a structured `WorkPlan`.
2. **Execute** — steps are grouped by `parallel_group` and each group runs concurrently
   (fan-out capped by `MAX_CONCURRENT_STEPS`). Before each group the token/cost budget is
   checked, and groups flagged `needs_approval` pause the run for a typed approve/reject
   decision (or run straight through when `AUTO_APPROVE` is set). A failing step is
   recorded as a `failed` `StepResult` instead of aborting the whole run.
3. **Synthesize** — the `Writer` agent produces a markdown report plus an actions
   manifest; the result is persisted to MCP memory and run metrics are published as a
   Prefect artifact (both best effort).

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

## Docker & deployment

Run the whole stack (Prefect server + MCP stub + a worker serving the flow as a
deployment) with Docker Compose:

```bash
echo "OPENAI_API_KEY=sk-..." > .env
docker compose up --build
```

Then open http://localhost:4200 and trigger `work-order-flow/work-order-orchestrator`,
or run it long-lived without Docker via `uv run python -m src.serve` (deployment name
via `SERVE_DEPLOYMENT_NAME`, optional schedule via `SERVE_CRON`). Scheduling, env vars,
and the production checklist live in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Examples

Builder-facing adaptations of the template live in [examples/](examples/README.md):

```bash
uv run python -m src.mcp_stub_server              # terminal 1
uv run python -m examples.incident_triage         # direct SDK path
uv run python -m examples.incident_triage --flow  # full Prefect DAG
```

## Project layout

```text
src/
├── agents/          # Agent builders + pydantic I/O schemas
├── flows/           # Prefect flow orchestrating the DAG
├── projects/        # Runnable demos
├── wrapper/         # SDK wrapper: guardrails, sessions, MCP, approvals,
│                    # resilience, metrics, HITL, checkpoints, ...
├── config.py        # AppConfig (env parsing) + agent wiring
├── mcp_servers.py   # MCP server entry point used by flows
├── mcp_stub_server.py  # Local FastMCP stub with demo tools
└── serve.py         # Long-running Prefect deployment entrypoint
examples/            # Builder-facing template adaptations
docs/                # Deployment / ops guides
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

## Production features

### LLM-call resilience (`src/wrapper/resilience.py`)

Every `Runner.run` call in the flow goes through `run_with_resilience`:

- **Retries with backoff** — transient failures (OpenAI 429/5xx, timeouts, connection
  errors, and `ModelBehaviorError`, i.e. malformed model output) are retried up to
  `LLM_MAX_ATTEMPTS` times with capped exponential backoff and jitter. Guardrail
  tripwires, `MaxTurnsExceeded`, and auth/4xx errors are never retried.
- **Model fallback** — set `LLM_FALLBACK_MODEL` (e.g. `gpt-4.1-mini`) to clone the agent
  onto that model with a fresh attempt budget after the primary model exhausts its tries.
- **Bounded fan-out** — `MAX_CONCURRENT_STEPS` caps concurrent steps per parallel group
  (`0` = unlimited).

These layer beneath the Prefect task retries (`retries=2`), which remain the coarse
outer safety net.

### Usage metrics & cost budgets (`src/wrapper/metrics.py`)

Every model call is recorded by a `UsageTracker` under a label (`plan`, `step:<id>`,
`synthesize`, `memory`); token counts come from the SDK's aggregated usage. Costs are
estimated from `DEFAULT_MODEL_PRICES` (approximate defaults — override with
`LLM_PRICE_INPUT_PER_1M` / `LLM_PRICE_OUTPUT_PER_1M`, USD per 1M tokens; unknown models
stay unpriced).

Set `MAX_TOKENS_BUDGET` and/or `MAX_COST_USD` to cap a run: the flow checks the budget
before each parallel group and marks remaining steps as `skipped` (reason in their
notes) instead of calling the model. At the end of the run, the per-label breakdown is
published as a Prefect markdown artifact (key `run-metrics`).

### Human-in-the-loop approvals (`src/wrapper/hitl.py`)

Plan groups containing `needs_approval` steps pause the flow run with a typed form
(`ApprovalDecision`: `approved`, `comment`). In the Prefect UI open the paused run,
press **Resume**, review the pending-step list, and submit. Approving runs the group;
rejecting records the pending steps as `skipped` (your comment lands in the step notes
and final report) and the flow continues. Set `AUTO_APPROVE=1` to bypass all gates
(dev only).

### Step checkpoints (`src/wrapper/checkpoints.py`)

With `CHECKPOINT_ENABLED=1`, each successful step result is persisted as JSON under
`CHECKPOINT_DIR` (default `.checkpoints/`, git-ignored). Re-running the same request
after a crash skips steps that already succeeded; failed and skipped steps always
re-run. Files are keyed by `WORK_ORDER_ID` when set, else by a hash of the request
text. After a fully successful run (every step `ok`) the checkpoint file is deleted
unless `CHECKPOINT_CLEAR_ON_SUCCESS=0`.

### Production env vars

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_MAX_ATTEMPTS` | `3` | LLM attempts per model (primary and fallback) |
| `LLM_RETRY_BASE_DELAY` / `LLM_RETRY_MAX_DELAY` | `1.0` / `30.0` | Backoff curve (seconds) |
| `LLM_FALLBACK_MODEL` | unset | Fallback model after primary exhaustion |
| `MAX_CONCURRENT_STEPS` | `0` | Per-group step concurrency (0 = unlimited) |
| `MAX_TOKENS_BUDGET` / `MAX_COST_USD` | unset | Per-run budget caps (unset = disabled) |
| `LLM_PRICE_INPUT_PER_1M` / `LLM_PRICE_OUTPUT_PER_1M` | unset | Price overrides for cost estimation |
| `CHECKPOINT_ENABLED` | `false` | Turn step checkpointing on |
| `CHECKPOINT_DIR` | `.checkpoints` | Where checkpoint files live |
| `WORK_ORDER_ID` | request hash | Explicit resume key across runs |
| `CHECKPOINT_CLEAR_ON_SUCCESS` | `true` | Delete checkpoint after a fully-ok run |
| `SERVE_DEPLOYMENT_NAME` | `work-order-orchestrator` | Prefect deployment name |
| `SERVE_CRON` | unset | Optional cron schedule for `src/serve.py` |

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
