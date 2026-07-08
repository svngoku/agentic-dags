"""Long-running deployment entrypoint: serve the work-order flow.

Registers :func:`src.flows.work_order_flow.work_order_flow` as a Prefect
deployment and blocks, polling the Prefect API (``PREFECT_API_URL``) for
scheduled or manually triggered runs and executing them in this process.

Run with ``python -m src.serve`` (the Docker image's default command).
Trigger a run once it is up::

    prefect deployment run 'work-order-flow/work-order-orchestrator' \
        -p request_text="Audit last week's incidents"

Environment variables:
    SERVE_DEPLOYMENT_NAME: deployment name (default ``work-order-orchestrator``).
    SERVE_CRON: optional cron string (e.g. ``0 8 * * 1``); when unset or
        empty, the deployment has no schedule and only runs when triggered.

Importing this module never touches the Prefect API; only ``main()`` does.
"""

from __future__ import annotations

import os
from typing import Any

from src.flows.work_order_flow import DEFAULT_REQUEST, work_order_flow

DEFAULT_DEPLOYMENT_NAME = "work-order-orchestrator"


def main() -> None:
    """Serve the flow as a Prefect deployment; blocks until interrupted.

    ``Flow.serve`` (synchronous in Prefect 3) creates or updates the
    deployment on the API, then runs a runner loop in this process. Cron
    scheduling is only requested when ``SERVE_CRON`` is set, so the default
    deployment is trigger-only.
    """
    deployment_name = (
        os.getenv("SERVE_DEPLOYMENT_NAME", "").strip() or DEFAULT_DEPLOYMENT_NAME
    )
    cron = os.getenv("SERVE_CRON", "").strip()

    serve_kwargs: dict[str, Any] = {
        "name": deployment_name,
        "parameters": {"request_text": DEFAULT_REQUEST},
        "tags": ["agentic-dags"],
        "description": "Plan -> execute -> synthesize agent DAG (see README).",
    }
    if cron:
        serve_kwargs["cron"] = cron

    work_order_flow.serve(**serve_kwargs)


if __name__ == "__main__":
    main()
