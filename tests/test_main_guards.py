from __future__ import annotations

from repetit import config
from repetit import main as main_module


class _Store:
    def __init__(self, sends: int):
        self._sends = sends

    def sends_today(self) -> int:
        return self._sends


def test_worker_lock_is_shared_between_run_and_once_modes(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    first = main_module._acquire_worker_lock()
    assert first is not None
    try:
        second = main_module._acquire_worker_lock()
        assert second is None
    finally:
        first.close()

    third = main_module._acquire_worker_lock()
    assert third is not None
    third.close()


def test_gates_stop_outside_work_hours(monkeypatch):
    monkeypatch.setattr(main_module, "in_work_hours", lambda: False)
    ok, reason = main_module._gates_ok(_Store(0))
    assert not ok
    assert "вне рабочих часов" in reason


def test_gates_enforce_daily_limit(monkeypatch):
    monkeypatch.setattr(main_module, "in_work_hours", lambda: True)
    monkeypatch.setattr(config, "DAILY_SEND_LIMIT", 3)

    assert main_module._gates_ok(_Store(2)) == (True, "ok")
    ok, reason = main_module._gates_ok(_Store(3))
    assert not ok
    assert "дневной лимит 3" in reason


def test_zero_daily_limit_means_unlimited(monkeypatch):
    monkeypatch.setattr(main_module, "in_work_hours", lambda: True)
    monkeypatch.setattr(config, "DAILY_SEND_LIMIT", 0)
    assert main_module._gates_ok(_Store(999)) == (True, "ok")
