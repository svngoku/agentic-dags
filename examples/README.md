# Examples

Builder-facing examples showing how to adapt the agentic-dags template to a
new domain. Each example is a runnable module (`uv run python -m examples.<name>`)
that imports the template's building blocks (`src.config`, `src.wrapper.*`,
`src.flows.work_order_flow`) instead of forking them.

| Example | Scenario | What it demonstrates | Run |
| --- | --- | --- | --- |
| `incident_triage.py` | Triage a P1 incident: gather metrics, search runbooks, open follow-up tasks, produce an incident report | In-code `AppConfig` (model override), `build_agents`, direct agent runs via `src.wrapper.runner.run_agent`, and the same request through the full Prefect DAG (`--flow`) | `uv run python -m examples.incident_triage [--flow]` |

All examples need the MCP stub running (`uv run python -m src.mcp_stub_server`)
and `OPENAI_API_KEY` set — at runtime only; every module imports offline.

## How to adapt this template

Work through this checklist when turning the template into your own app:

1. **Write your request.** Start like `incident_triage.build_request()`: a
   plain-language request that names the tools you expect the planner to use.
   Often this is the only change needed for a first end-to-end run.
2. **Replace the stub tools with real MCP servers.** The stub
   (`src/mcp_stub_server.py`) returns canned data. Point `MCP_URL` at your
   real MCP server (or add more servers in `src/mcp_servers.py`), and update
   `STUB_TOOL_NAMES` in `src/wrapper/mcp.py` so the tool filter allows your
   tools. Tool docstrings become the LLM-facing descriptions — write them well.
3. **Adjust the schemas.** `src/agents/schemas.py` defines the contract:
   `WorkPlan`/`PlanStep` (planning), `StepResult` (execution), `FinalOutput`
   (synthesis). Add domain fields (severity, owner, due date, ...) and mirror
   them in the agent instructions.
4. **Tune approvals and parallel groups.** The supervisor assigns each step a
   `parallel_group` (same group = concurrent) and `needs_approval` (pauses the
   Prefect flow run until approved unless `AUTO_APPROVE` is truthy). Steer
   this in `src/agents/supervisor.py` — e.g. "any step that mutates external
   systems needs approval".
5. **Wire guardrails.** `src/wrapper/guardrails.py` builds the input/output
   guardrails attached to every run via `build_run_config`. Swap in your own
   checks (PII, jailbreak, output shape) — tripwires fail the run early.
6. **Pick models per config, not per fork.** Override with `LLM_MODEL` (env)
   or construct `AppConfig(model=...)` in code as `incident_triage.build_config()`
   does.
7. **Ship it.** `python -m src.serve` turns the flow into a long-running
   Prefect deployment; `docker compose up` brings up server, stub, and worker.
   See `docs/DEPLOYMENT.md`.
