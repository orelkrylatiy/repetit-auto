from __future__ import annotations

import json

from repetit.integration import triage as triage_module
from repetit.models.order import Order


def _order() -> Order:
    return Order.from_api(
        {
            "id": 55,
            "subject": {"id": 10, "name": "Информатика"},
            "purpose": "Подготовка к ОГЭ",
            "information": "9 класс, нужно закрыть пробелы и разобрать Python",
            "lessonPlace": 4,
            "contactName": "Светлана",
        }
    )


def _good_text() -> str:
    return (
        "Здравствуйте, Светлана! Помогу разобрать пробелы по информатике и спокойно пройти темы "
        "по Python, которые сейчас вызывают сложности. На занятиях можно параллельно закреплять "
        "формат заданий ОГЭ, чтобы теория сразу переходила в практику. Занимаюсь онлайн. "
        "Какие темы сейчас проседают сильнее всего?"
    )


def _reply(decision="respond", reason="подходит", text=None):
    return json.dumps(
        {"decision": decision, "reason": reason, "text": _good_text() if text is None else text},
        ensure_ascii=False,
    )


def test_triage_network_failure_is_retryable_llm_error(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("HTTP 429")

    monkeypatch.setattr(triage_module.llm, "chat", fail)
    result = triage_module.triage(_order())
    assert result["decision"] == "llm_error"
    assert "HTTP 429" in result["reason"]


def test_triage_malformed_json_is_local_error_not_global_llm_failure(monkeypatch):
    monkeypatch.setattr(triage_module.llm, "chat", lambda *_args, **_kwargs: "not-json")
    result = triage_module.triage(_order())
    assert result["decision"] == "error"
    assert "невалидный JSON" in result["reason"]


def test_triage_valid_json_must_be_object(monkeypatch):
    monkeypatch.setattr(triage_module.llm, "chat", lambda *_args, **_kwargs: "[]")
    result = triage_module.triage(_order())
    assert result["decision"] == "error"
    assert "должен быть объектом" in result["reason"]


def test_triage_rejects_unknown_decision(monkeypatch):
    monkeypatch.setattr(
        triage_module.llm,
        "chat",
        lambda *_args, **_kwargs: _reply(decision="maybe"),
    )
    result = triage_module.triage(_order())
    assert result["decision"] == "error"
    assert "невалидный decision" in result["reason"]


def test_triage_skip_never_returns_message_text(monkeypatch):
    monkeypatch.setattr(
        triage_module.llm,
        "chat",
        lambda *_args, **_kwargs: _reply(decision="skip", reason="не наш кейс", text="лишний текст"),
    )
    result = triage_module.triage(_order())
    assert result == {"decision": "skip", "reason": "не наш кейс", "text": ""}


def test_triage_rejects_too_short_response(monkeypatch):
    monkeypatch.setattr(
        triage_module.llm,
        "chat",
        lambda *_args, **_kwargs: _reply(text="Здравствуйте!"),
    )
    result = triage_module.triage(_order())
    assert result["decision"] == "error"
    assert "длина текста" in result["reason"]


def test_triage_rejects_contacts_after_generation(monkeypatch):
    text = _good_text() + " Напишите мне в telegram, если удобно."
    monkeypatch.setattr(triage_module.llm, "chat", lambda *_args, **_kwargs: _reply(text=text))
    monkeypatch.setattr(triage_module.random, "random", lambda: 1.0)
    result = triage_module.triage(_order())
    assert result["decision"] == "error"
    assert "textguard" in result["reason"]


def test_triage_valid_response_is_returned_and_hard_style_is_applied(monkeypatch):
    text = _good_text().replace("спокойно пройти", "спокойно пройти — без гонки")
    monkeypatch.setattr(triage_module.llm, "chat", lambda *_args, **_kwargs: _reply(text=text))
    monkeypatch.setattr(triage_module.random, "random", lambda: 1.0)
    result = triage_module.triage(_order())
    assert result["decision"] == "respond"
    assert result["reason"] == "подходит"
    assert "—" not in result["text"]
    assert result["text"].endswith("?")
