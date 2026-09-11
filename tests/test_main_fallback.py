from __future__ import annotations

from repetit import config
from repetit import main as main_module
from repetit.models.order import Order
from repetit.models.verdict import FilterVerdict


def _order() -> Order:
    return Order.from_api(
        {
            "id": 55,
            "subject": {"id": 10, "name": "Информатика"},
            "purpose": "Подготовка к ОГЭ",
            "information": "Нужна помощь с Python",
            "lessonPlace": 4,
            "contactName": "Светлана",
        }
    )


def test_hard_filter_still_runs_before_fallback(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "hard_filter",
        lambda _o: FilterVerdict(False, "не наш предмет"),
    )
    monkeypatch.setattr(main_module, "_cooldown_active", lambda _p: True)
    monkeypatch.setattr(
        main_module,
        "fallback_reply",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )
    result = main_module._new_order_decision(_order())
    assert result["decision"] == "filtered"


def test_llm_cooldown_uses_fallback_without_calling_llm(monkeypatch):
    monkeypatch.setattr(main_module, "hard_filter", lambda _o: FilterVerdict(True, "ok"))
    monkeypatch.setattr(main_module, "_cooldown_active", lambda _p: True)
    monkeypatch.setattr(
        main_module,
        "triage",
        lambda _o: (_ for _ in ()).throw(AssertionError("LLM triage must not run")),
    )
    monkeypatch.setattr(config, "FALLBACK_ENABLED", True)
    monkeypatch.setattr(config, "FALLBACK_TEMPLATES", ())
    result = main_module._new_order_decision(_order())
    assert result["decision"] == "respond"
    assert result["source"] == "fallback"


def test_normal_llm_skip_never_becomes_fallback(monkeypatch):
    monkeypatch.setattr(main_module, "hard_filter", lambda _o: FilterVerdict(True, "ok"))
    monkeypatch.setattr(main_module, "_cooldown_active", lambda _p: False)
    monkeypatch.setattr(
        main_module,
        "triage",
        lambda _o: {"decision": "skip", "reason": "не подходит", "text": ""},
    )
    monkeypatch.setattr(
        main_module,
        "fallback_reply",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )
    result = main_module._new_order_decision(_order())
    assert result["decision"] == "skip"
    assert result["source"] == "llm"


def test_llm_network_error_sets_cooldown_and_uses_fallback(monkeypatch):
    monkeypatch.setattr(main_module, "hard_filter", lambda _o: FilterVerdict(True, "ok"))
    monkeypatch.setattr(main_module, "_cooldown_active", lambda _p: False)
    monkeypatch.setattr(
        main_module,
        "triage",
        lambda _o: {"decision": "llm_error", "reason": "HTTP 429", "text": ""},
    )
    calls = []
    monkeypatch.setattr(main_module, "_set_cooldown", lambda path, seconds: calls.append(seconds))
    monkeypatch.setattr(config, "FALLBACK_ENABLED", True)
    monkeypatch.setattr(config, "FALLBACK_TEMPLATES", ())
    result = main_module._new_order_decision(_order())
    assert calls == [30 * 60]
    assert result["decision"] == "respond"
    assert result["source"] == "fallback"


def test_invalid_llm_output_uses_fallback_but_not_global_cooldown(monkeypatch):
    monkeypatch.setattr(main_module, "hard_filter", lambda _o: FilterVerdict(True, "ok"))
    monkeypatch.setattr(main_module, "_cooldown_active", lambda _p: False)
    monkeypatch.setattr(
        main_module,
        "triage",
        lambda _o: {"decision": "error", "reason": "bad json", "text": ""},
    )
    monkeypatch.setattr(
        main_module,
        "_set_cooldown",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no global cooldown")),
    )
    monkeypatch.setattr(config, "FALLBACK_ENABLED", True)
    monkeypatch.setattr(config, "FALLBACK_TEMPLATES", ())
    result = main_module._new_order_decision(_order())
    assert result["decision"] == "respond"
    assert result["source"] == "fallback"
