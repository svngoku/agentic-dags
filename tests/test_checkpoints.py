"""Unit tests for the JSON-file checkpoint store (offline, tmp_path based)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.agents.schemas import Artifact, StepResult
from src.wrapper.checkpoints import CheckpointStore

REQUEST = "Create a weekly status report and open follow-ups for risks."


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from ambient checkpoint configuration."""
    for name in (
        "CHECKPOINT_ENABLED",
        "CHECKPOINT_DIR",
        "WORK_ORDER_ID",
        "CHECKPOINT_CLEAR_ON_SUCCESS",
    ):
        monkeypatch.delenv(name, raising=False)


def _enable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHECKPOINT_ENABLED", "true")
    monkeypatch.setenv("CHECKPOINT_DIR", str(tmp_path))


def _store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: str = REQUEST
) -> CheckpointStore:
    _enable(monkeypatch, tmp_path)
    store = CheckpointStore.for_request(request)
    assert store is not None
    return store


def _ok(step_id: str = "step-1", note: str = "done") -> StepResult:
    return StepResult(
        step_id=step_id,
        status="ok",
        artifacts=[Artifact(key="ticket", value="T-123")],
        notes=[note],
    )


# ---- enablement and keying ---------------------------------------------------


def test_disabled_by_default_returns_none() -> None:
    assert CheckpointStore.for_request(REQUEST) is None


def test_explicitly_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHECKPOINT_ENABLED", "0")
    assert CheckpointStore.for_request(REQUEST) is None


def test_run_key_defaults_to_request_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(monkeypatch, tmp_path)
    expected = hashlib.sha256(REQUEST.strip().encode("utf-8")).hexdigest()[:12]
    assert store.run_key == expected
    assert store.path == tmp_path / f"{expected}.json"


def test_work_order_id_overrides_run_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORK_ORDER_ID", "wo-42")
    store = _store(monkeypatch, tmp_path)
    assert store.run_key == "wo-42"
    assert store.path == tmp_path / "wo-42.json"
    # The request hash is still recorded for provenance.
    assert store.request_hash != "wo-42"


def test_clear_on_success_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _store(monkeypatch, tmp_path).clear_on_success is True
    monkeypatch.setenv("CHECKPOINT_CLEAR_ON_SUCCESS", "false")
    assert _store(monkeypatch, tmp_path).clear_on_success is False


# ---- save / load -------------------------------------------------------------


def test_save_load_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _store(monkeypatch, tmp_path).save(_ok())

    fresh = _store(monkeypatch, tmp_path)  # new instance, same request
    loaded = fresh.load()
    assert set(loaded) == {"step-1"}
    assert loaded["step-1"] == _ok()


def test_only_ok_results_are_persisted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(monkeypatch, tmp_path)
    store.save(StepResult(step_id="bad", status="failed", notes=["boom"]))
    store.save(StepResult(step_id="meh", status="skipped"))

    assert store.load() == {}
    assert not store.path.exists()  # nothing worth writing


def test_upsert_last_write_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(monkeypatch, tmp_path)
    store.save(_ok(note="first"))
    store.save(_ok(note="second"))

    loaded = store.load()
    assert loaded["step-1"].notes == ["second"]


def test_failed_rerun_drops_stale_ok_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(monkeypatch, tmp_path)
    store.save(_ok())
    store.save(StepResult(step_id="step-1", status="failed"))
    assert store.load() == {}


def test_save_many_persists_group_in_one_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(monkeypatch, tmp_path)
    store.save_many([_ok("a"), StepResult(step_id="b", status="failed"), _ok("c")])
    assert set(store.load()) == {"a", "c"}


def test_two_requests_use_separate_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = _store(monkeypatch, tmp_path, request="request one")
    second = _store(monkeypatch, tmp_path, request="request two")
    assert first.path != second.path

    first.save(_ok("only-in-first"))
    second.save(_ok("only-in-second"))

    assert set(first.load()) == {"only-in-first"}
    assert set(second.load()) == {"only-in-second"}
    assert len(list(tmp_path.glob("*.json"))) == 2


# ---- robustness ---------------------------------------------------------------


def test_missing_file_loads_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _store(monkeypatch, tmp_path).load() == {}


def test_corrupt_file_loads_empty_without_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(monkeypatch, tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json at all", encoding="utf-8")
    assert store.load() == {}


def test_unknown_schema_version_loads_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(monkeypatch, tmp_path)
    store.save(_ok())
    payload = store.path.read_text(encoding="utf-8").replace(
        '"schema_version": 1', '"schema_version": 99'
    )
    store.path.write_text(payload, encoding="utf-8")
    assert store.load() == {}


def test_write_is_atomic_and_leaves_no_temp_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(monkeypatch, tmp_path)
    store.save(_ok())
    assert store.path.exists()
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


def test_clear_removes_file_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(monkeypatch, tmp_path)
    store.save(_ok())
    assert store.path.exists()

    store.clear()
    assert not store.path.exists()
    store.clear()  # second call must not raise
