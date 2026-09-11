from __future__ import annotations

from repetit import config
from repetit import main as main_module
from repetit.integration.chat import ChatSnapshot
from repetit.storage.store import Store


class _Mgr:
    def ensure_ready(self):
        return main_module.bm.READY

    def context(self):
        return object()


class _FakeResponder:
    send_result = {"status": "sent", "detail": "ok", "screenshot": None}
    sent = []

    def __init__(self, _ctx):
        pass

    def inspect(self, order_id, _title):
        return ChatSnapshot(
            state="client_last",
            incoming_key="m-1",
            incoming_text="Когда можно начать?",
            dialog_text="client: Когда можно начать?",
            detail="ok",
        )

    def send_reply(self, order_id, title, incoming_key, text):
        self.sent.append((str(order_id), title, incoming_key, text))
        return dict(self.send_result)


def _store(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.upsert_response(
        10,
        decision="respond",
        status="sent",
        sent=True,
        chat_title="№ 10, Клиент",
    )
    return store


def _patch_common(monkeypatch):
    monkeypatch.setattr(main_module, "in_work_hours", lambda: True)
    monkeypatch.setattr(main_module, "_cooldown_active", lambda _p: False)
    monkeypatch.setattr(main_module, "ChatResponder", _FakeResponder)
    monkeypatch.setattr(config, "CHAT_CANDIDATE_SCAN_LIMIT", 6)
    monkeypatch.setattr(config, "CHAT_MAX_ORDER_AGE_DAYS", 14)
    monkeypatch.setattr(config, "CHAT_MAX_PER_CYCLE", 2)


def test_chat_dry_run_persists_draft_without_send(monkeypatch, tmp_path):
    _patch_common(monkeypatch)
    _FakeResponder.sent = []
    monkeypatch.setattr(
        main_module,
        "generate_chat_reply",
        lambda **_k: {"decision": "reply", "reason": "ok", "text": "Какие дни вам удобны?"},
    )
    store = _store(tmp_path)
    try:
        summary = main_module.run_chat_cycle(_Mgr(), store, dry_run=True, force=True)
        row = store.get_chat_reply(10, "m-1")
        assert summary["targets"] == 1
        assert row["status"] == "dry_run"
        assert row["text"] == "Какие дни вам удобны?"
        assert _FakeResponder.sent == []
    finally:
        store.close()


def test_stale_recheck_is_terminal_for_old_incoming_message(monkeypatch, tmp_path):
    _patch_common(monkeypatch)
    _FakeResponder.sent = []
    _FakeResponder.send_result = {
        "status": "stale",
        "detail": "последнее сообщение изменилось до Send",
        "screenshot": None,
    }
    monkeypatch.setattr(
        main_module,
        "generate_chat_reply",
        lambda **_k: {"decision": "reply", "reason": "ok", "text": "Какие дни вам удобны?"},
    )
    store = _store(tmp_path)
    try:
        main_module.run_chat_cycle(_Mgr(), store, force=True)
        row = store.get_chat_reply(10, "m-1")
        assert row["status"] == "stale"
        assert len(_FakeResponder.sent) == 1
    finally:
        store.close()


def test_terminal_incoming_key_is_never_generated_or_sent_twice(monkeypatch, tmp_path):
    _patch_common(monkeypatch)
    _FakeResponder.sent = []
    store = _store(tmp_path)
    store.upsert_chat_reply(
        10,
        "m-1",
        incoming_text="Когда можно начать?",
        decision="reply",
        text="Какие дни вам удобны?",
        status="sent",
        sent=True,
    )
    monkeypatch.setattr(
        main_module,
        "generate_chat_reply",
        lambda **_k: (_ for _ in ()).throw(AssertionError("must not regenerate")),
    )
    try:
        main_module.run_chat_cycle(_Mgr(), store, force=True)
        assert _FakeResponder.sent == []
    finally:
        store.close()


def test_llm_error_in_chat_sets_cooldown_but_keeps_retryable(monkeypatch, tmp_path):
    _patch_common(monkeypatch)
    calls = []
    monkeypatch.setattr(
        main_module,
        "generate_chat_reply",
        lambda **_k: {"decision": "llm_error", "reason": "HTTP 429", "text": ""},
    )
    monkeypatch.setattr(main_module, "_set_cooldown", lambda _p, seconds: calls.append(seconds))
    store = _store(tmp_path)
    try:
        main_module.run_chat_cycle(_Mgr(), store, force=True)
        row = store.get_chat_reply(10, "m-1")
        assert row["status"] == "retry"
        assert row["text"] is None
        assert calls == [30 * 60]
    finally:
        store.close()
