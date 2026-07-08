# Deployment

How to run the agentic-dags template beyond `python -m src.flows.work_order_flow`:
as a long-running Prefect deployment, locally or with Docker Compose.

## Local (no Docker) recap

```bash
uv sync                                   # install deps
cp .env.example .env                      # set OPENAI_API_KEY
uv run python -m src.mcp_stub_server      # terminal 1: MCP stub
prefect server start                      # terminal 2: Prefect API + UI (:4200)
uv run python -m src.serve                # terminal 3: serve the deployment
```

`src/serve.py` registers the flow as deployment `work-order-flow/work-order-orchestrator`
and blocks, executing runs in-process as they are triggered or scheduled.

## Docker Compose

Prerequisites: Docker with Compose v2, and `OPENAI_API_KEY` exported in your
shell or set in a `.env` file next to `docker-compose.yml` (Compose reads it
automatically; the file is dockerignored so it never enters the image).

```bash
echo "OPENAI_API_KEY=sk-..." > .env   # or reuse your existing .env
docker compose up --build
```

### The three services

| Service | Image | Role |
| --- | --- | --- |
| `prefect-server` | `prefecthq/prefect:3-latest` | Prefect 3 API + UI on <http://localhost:4200>; state persisted in the `prefect-data` volume (`/root/.prefect`). |
| `mcp-stub` | built from `Dockerfile` | FastMCP stub with demo tools, bound to `0.0.0.0:8000` (internal only, reachable as `http://mcp-stub:8000/mcp`). |
| `worker` | built from `Dockerfile` | Runs `python -m src.serve`: registers the deployment against `prefect-server` and executes flow runs, calling tools on `mcp-stub`. |

All three share the default Compose network; the worker waits for both
dependencies' healthchecks (`/api/health` for Prefect, TCP for the stub).

### Trigger a run

From the UI: open <http://localhost:4200>, go to **Deployments →
work-order-orchestrator**, and use **Run** (custom run lets you set
`request_text`). Or from a shell inside the network:

```bash
docker compose exec worker \
  prefect deployment run 'work-order-flow/work-order-orchestrator' \
  -p request_text="Audit last week's incidents"
```

Runs that hit an approval gate (`needs_approval` steps with `AUTO_APPROVE=0`)
pause and wait in the UI under the flow run's **Resume** button.

### Scheduling

Set `SERVE_CRON` to attach a cron schedule to the deployment (UTC):

```bash
SERVE_CRON="0 8 * * 1" docker compose up --build   # every Monday 08:00
```

Unset (default) means the deployment only runs when triggered manually.

## Environment variables (deployment-relevant)

| Variable | Default | Used by | Purpose |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | — (required for runs) | worker | LLM access; runs fail fast without it. |
| `LLM_MODEL` | `gpt-4.1` | worker | Model for all three agents. |
| `SERVE_DEPLOYMENT_NAME` | `work-order-orchestrator` | worker (`src/serve.py`) | Deployment name shown in Prefect. |
| `SERVE_CRON` | unset | worker (`src/serve.py`) | Optional cron schedule for the deployment. |
| `PREFECT_API_URL` | `http://prefect-server:4200/api` (compose) | worker | Prefect API the runner registers with. |
| `MCP_URL` | `http://mcp-stub:8000/mcp` (compose) | worker | Streamable HTTP MCP endpoint used by agents. |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` (compose) | mcp-stub | Stub server bind address. |
| `AUTO_APPROVE` | `1` (compose default) | worker | `0` pauses approval-flagged groups until resumed in the UI. |
| `TRACING_DISABLED`, `TRACING_API_KEY`, `TRACE_INCLUDE_SENSITIVE_DATA` | see `.env.example` | worker | OpenAI Agents SDK tracing controls. |
| `SESSION_ID`, `SESSION_DB_PATH` | unset / `sessions.db` | worker | Optional SQLite conversation memory (written to `/app`). |

## Production checklist

- **Secrets**: never bake `OPENAI_API_KEY` into the image (it is dockerignored);
  inject via your orchestrator's secret store (Compose `secrets:`, K8s Secrets)
  instead of plain env files on shared hosts. Set `MCP_TOKEN` when your real
  MCP server requires auth.
- **Persistent state**: keep the `prefect-data` volume (flow-run history,
  pending approvals). Mount a volume for `SESSION_DB_PATH` and `.checkpoints/`
  if you rely on sessions/checkpoints across restarts.
- **Resource limits**: add `deploy.resources.limits` (or `mem_limit`/`cpus`)
  for `worker` — agent runs are bursty; Prefect server needs ~1 GiB to be
  comfortable. Consider Postgres instead of the default SQLite for a real
  Prefect deployment.
- **Tracing**: in production either set `TRACING_API_KEY` deliberately and keep
  `TRACE_INCLUDE_SENSITIVE_DATA=false`, or set `TRACING_DISABLED=true` to keep
  prompts/outputs out of external trace storage.
- **Approvals**: set `AUTO_APPROVE=0` in production so mutating steps wait
  for a human in the Prefect UI; `1` is a demo convenience.
- **Real MCP servers**: swap `MCP_URL` from the stub to your production MCP
  endpoint(s) and review the tool allowlist in `src/wrapper/mcp.py`.
