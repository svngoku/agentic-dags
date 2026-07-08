"""Step-level checkpointing so re-runs skip already-completed steps.

Complements Prefect task retries (which live inside one flow run) with a tiny
JSON file per work order that survives process crashes: when the same request
is re-run, steps whose ``StepResult`` was previously persisted with
``status == "ok"`` are loaded from disk instead of re-executed. Failed and
skipped steps are intentionally *not* persisted so they re-run next time.

Configuration (all read from the environment at :meth:`CheckpointStore.for_request`
time, parsed like the other booleans in the codebase -- see ``src.config``):

* ``CHECKPOINT_ENABLED`` -- master switch, default ``false``.
* ``CHECKPOINT_DIR`` -- directory for checkpoint files, default ``.checkpoints``
  (the orchestrator must add it to ``.gitignore``).
* ``WORK_ORDER_ID`` -- optional explicit run key; when unset the key is
  ``sha256(request_text.strip())[:12]`` so identical requests share a file.
* ``CHECKPOINT_CLEAR_ON_SUCCESS`` -- delete the file after a fully successful
  flow run, default ``true``.

File format: a :class:`CheckpointEnvelope` serialized with pydantic
(``schema_version`` guards future format changes). Writes are atomic
(temp file + ``os.replace``) and all I/O is best effort: corruption or I/O
errors degrade to "no checkpoint" with a warning, never an exception.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from src.agents.schemas import StepResult

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_CHECKPOINT_DIR = ".checkpoints"

_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean env var (same convention as ``src.config._env_bool``)."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUTHY_VALUES


def _request_hash(request_text: str) -> str:
    """Stable 12-hex-char key derived from the normalized request text."""
    return hashlib.sha256(request_text.strip().encode("utf-8")).hexdigest()[:12]


class CheckpointEnvelope(BaseModel):
    """On-disk schema for one work order's checkpoint file."""

    schema_version: int = SCHEMA_VERSION
    run_key: str
    request_hash: str
    results: dict[str, StepResult] = Field(default_factory=dict)


class CheckpointStore(BaseModel):
    """JSON-file store of completed (``status == "ok"``) step results.

    Instances are cheap value objects; every ``load``/``save`` re-reads the
    file, so concurrent flow runs of *different* work orders never interact
    (they use different files) and a crash between groups loses at most the
    in-flight group.
    """

    path: Path
    run_key: str
    request_hash: str
    clear_on_success: bool = True

    @classmethod
    def for_request(cls, request_text: str) -> CheckpointStore | None:
        """Build a store for ``request_text`` from the environment.

        Returns ``None`` when ``CHECKPOINT_ENABLED`` is falsy (the feature's
        default), so callers can gate all checkpoint logic on a single
        ``if store:`` check.
        """
        if not _env_bool("CHECKPOINT_ENABLED", default=False):
            return None
        directory = os.getenv("CHECKPOINT_DIR", "").strip() or DEFAULT_CHECKPOINT_DIR
        request_hash = _request_hash(request_text)
        run_key = (os.getenv("WORK_ORDER_ID") or "").strip() or request_hash
        return cls(
            path=Path(directory) / f"{run_key}.json",
            run_key=run_key,
            request_hash=request_hash,
            clear_on_success=_env_bool("CHECKPOINT_CLEAR_ON_SUCCESS", default=True),
        )

    def load(self) -> dict[str, StepResult]:
        """Return previously completed results keyed by ``step_id``.

        Never raises: a missing file yields ``{}`` silently, while a corrupt
        or unreadable file (bad JSON, schema mismatch, I/O error) yields
        ``{}`` with a warning. When ``WORK_ORDER_ID`` pins the run key but the
        stored ``request_hash`` differs (the request text was edited between
        runs), the results are still honored -- the operator chose the key --
        but a warning is logged. Defensively, only ``status == "ok"`` entries
        are returned even if the file was hand-edited.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            logger.warning("Checkpoint file %s unreadable; ignoring", self.path)
            return {}
        try:
            envelope = CheckpointEnvelope.model_validate_json(raw)
        except (ValidationError, ValueError):
            logger.warning("Checkpoint file %s corrupt; ignoring", self.path)
            return {}
        if envelope.schema_version != SCHEMA_VERSION:
            logger.warning(
                "Checkpoint file %s has schema_version=%s (expected %s); ignoring",
                self.path,
                envelope.schema_version,
                SCHEMA_VERSION,
            )
            return {}
        if envelope.request_hash != self.request_hash:
            logger.warning(
                "Checkpoint %s was written for a different request text "
                "(hash %s != %s); reusing it because WORK_ORDER_ID pins the key",
                self.path,
                envelope.request_hash,
                self.request_hash,
            )
        return {
            step_id: result
            for step_id, result in envelope.results.items()
            if result.status == "ok"
        }

    def save(self, result: StepResult) -> None:
        """Upsert one result and atomically rewrite the checkpoint file.

        Only ``status == "ok"`` results are persisted; failed and skipped
        steps must re-run on the next attempt, so they are dropped here (and
        an existing stale entry for that step is removed). I/O errors are
        logged and swallowed -- checkpointing must never fail the flow.
        """
        self.save_many([result])

    def save_many(self, results: Iterable[StepResult]) -> None:
        """Upsert several results with a single atomic read-modify-write.

        Prefer this after each parallel group: one disk write per group
        instead of one per step. Semantics match :meth:`save`.
        """
        current = self.load()
        changed = False
        for result in results:
            if result.status == "ok":
                current[result.step_id] = result
                changed = True
            elif current.pop(result.step_id, None) is not None:
                changed = True  # drop stale "ok" entry for a now-failed step
        if not changed:
            return
        envelope = CheckpointEnvelope(
            run_key=self.run_key,
            request_hash=self.request_hash,
            results=current,
        )
        try:
            self._atomic_write(envelope.model_dump_json(indent=2))
        except OSError:
            logger.warning("Failed to write checkpoint %s; continuing", self.path)

    def clear(self) -> None:
        """Delete this run's checkpoint file (no-op when it does not exist)."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove checkpoint %s; continuing", self.path)

    def _atomic_write(self, payload: str) -> None:
        """Write ``payload`` to :attr:`path` via a temp file + ``os.replace``."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, self.path)
        except OSError:
            Path(tmp_name).unlink(missing_ok=True)
            raise
