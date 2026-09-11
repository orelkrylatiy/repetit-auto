from __future__ import annotations

import json

from repetit import config
from repetit import fallback as fallback_module
from repetit.integration import chat as chat_module
from repetit.integration import chat_triage


def test_fallback_choice_is_stable_for_same_order():
    templates = ("a" * 120, "b" * 120, "c" * 120)
    first = fallback_module.choose_fallback(12345, templates)
    assert first == fallback_module.choose_fallback(12345, templates)
    assert first in templates


def test_explicit_empty_fallback_templates_fail_closed(monkeypatch):
    monkeypatch.setattr(config, "FALLBACK_ENABLED", True)
    result = fallback_module.fallback_reply(1, reason="llm down", templates=())
    assert result["decision"] == "error"
    assert "пусты" in result["reason"]


def test_fallback_text_runs_contact_guard(monkeypatch):
    monkeypatch.setattr(config, "FALLBACK_ENABLED", True)
    unsafe = (
        "Здравствуйте! Могу помочь с информатикой. На первом занятии посмотрим текущий уровень. "
        "Для связи напишите в telegram, там обсудим детали и время пробного занятия."
    )
    result = fallback_module.fallback_reply(1, reason="llm down", templates=(unsafe,))
    assert result["decision"] == "error"
    assert "textguard" in result["reason"]


def test_default_fallback_is_valid(monkeypatch):
    monkeypatch.setattr(config, "FALLBACK_ENABLED", True)
    monkeypatch.setattr(config, "FALLBACK_TEMPLATES", ())
    result = fallback_module.fallback_reply(77, reason="cooldown")
    assert result["decision"] == "respond"
    assert result["source"] == "fallback"
    assert config.MIN_TEXT_LEN <= len(result["text"]) <= config.MAX_TEXT_LEN


def test_chat_state_url_requires_exact_https_host_path_and_order():
    good = "https://ws.repetit.ru/api/chats/personal?orderId=42&teacherId=7"
    assert chat_module.is_chat_state_url(good, "GET", 42)
    assert not chat_module.is_chat_state_url(good, "POST", 42)
    assert not chat_module.is_chat_state_url(good, "GET", 43)
    assert not chat_module.is_chat_state_url(
        "http://ws.repetit.ru/api/chats/personal?orderId=42", "GET", 42
    )
    assert not chat_module.is_chat_state_url(
        "https://evil.example/?x=ws.repetit.ru/api/chats/personal&orderId=42",
        "GET",
        42,
    )


def _history(last, messages=None):
    return {"result": {"lastMessage": last, "messages": messages or [last]}}


def test_chat_parser_accepts_only_explicit_client_sender():
    payload = _history(
        {"id": "m2", "text": "Когда можно начать?", "senderType": "client", "createdAt": 2},
        [
            {"id": "m1", "text": "Здравствуйте", "senderType": "teacher", "createdAt": 1},
            {"id": "m2", "text": "Когда можно начать?", "senderType": "client", "createdAt": 2},
        ],
    )
    snap = chat_module.parse_chat_snapshot(payload)
    assert snap.state == "client_last"
    assert snap.incoming_key == "m2"
    assert snap.incoming_text == "Когда можно начать?"
    assert "tutor: Здравствуйте" in snap.dialog_text


def test_outgoing_false_alone_is_not_enough_to_call_message_client():
    payload = _history({"id": "m2", "text": "Привет", "isOutgoing": False})
    snap = chat_module.parse_chat_snapshot(payload)
    assert snap.state == "unsupported"
    assert "sender" in snap.detail


def test_system_or_tutor_last_never_becomes_auto_reply_target():
    system = chat_module.parse_chat_snapshot(
        _history({"id": "s1", "text": "Системное сообщение", "senderType": "system"})
    )
    tutor = chat_module.parse_chat_snapshot(
        _history({"id": "t1", "text": "Наш ответ", "senderType": "teacher"})
    )
    assert system.state == "system_last"
    assert tutor.state == "tutor_last"


def test_without_last_message_timestamps_are_required_for_safe_ordering():
    good = {
        "result": {
            "messages": [
                {"id": "1", "text": "наш", "senderType": "teacher", "createdAt": 1},
                {"id": "2", "text": "ответ", "senderType": "client", "createdAt": 2},
            ]
        }
    }
    assert chat_module.parse_chat_snapshot(good).state == "client_last"

    bad = {
        "result": {
            "messages": [
                {"id": "1", "text": "наш", "senderType": "teacher"},
                {"id": "2", "text": "ответ", "senderType": "client"},
            ]
        }
    }
    snap = chat_module.parse_chat_snapshot(bad)
    assert snap.state == "unsupported"
    assert "timestamp" in snap.detail


def test_chat_human_gates_cover_price_contacts_onsite_and_unsupported_subjects():
    blocked = [
        "А сколько стоит занятие?",
        "Можно дешевле за 1500 руб?",
        "Давайте очные занятия",
        "Нужен C++ для олимпиады",
        "Можно ваш телефон?",
    ]
    for text in blocked:
        assert chat_triage.requires_human(text), text
    assert chat_triage.requires_human("Когда можно начать занятия?") is None


def test_chat_human_gate_does_not_call_llm(monkeypatch):
    monkeypatch.setattr(
        chat_triage.llm,
        "chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("LLM must not be called")),
    )
    result = chat_triage.generate_chat_reply(
        order_id=1,
        last_client_text="Сколько стоит?",
        dialog_text="client: Сколько стоит?",
    )
    assert result["decision"] == "needs_human"


def test_chat_reply_network_failure_is_retryable(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("HTTP 429")

    monkeypatch.setattr(chat_triage.llm, "chat", fail)
    result = chat_triage.generate_chat_reply(
        order_id=1,
        last_client_text="Когда можно начать?",
        dialog_text="client: Когда можно начать?",
    )
    assert result["decision"] == "llm_error"


def test_chat_reply_valid_json_is_normalized(monkeypatch):
    raw = json.dumps(
        {
            "reply": "Да, можем начать с пробного занятия — подскажите, какие дни вам удобнее?",
            "needs_human": False,
            "note": "обычный вопрос",
        },
        ensure_ascii=False,
    )
    monkeypatch.setattr(chat_triage.llm, "chat", lambda *_a, **_k: raw)
    result = chat_triage.generate_chat_reply(
        order_id=1,
        last_client_text="Когда можно начать?",
        dialog_text="client: Когда можно начать?",
    )
    assert result["decision"] == "reply"
    assert "—" not in result["text"]
    assert " - " in result["text"]
