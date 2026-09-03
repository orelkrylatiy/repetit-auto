from __future__ import annotations

from datetime import datetime

from repetit.browser.manager import is_feed_url, is_login_url
from repetit.integration.respond import _chat_has_history
from repetit.integration.triage import _RULES, _style_variation
from repetit.utils import textguard
from repetit.utils.workhours import in_work_hours


def test_feed_url_is_strict_but_allows_query_and_trailing_slash():
    assert is_feed_url("https://repetit.ru/lk/teacher/neworders")
    assert is_feed_url("https://repetit.ru/lk/teacher/neworders/?x=1")
    assert not is_feed_url("https://repetit.ru/lk/teacher/neworders/123")
    assert not is_feed_url("https://evil.example/lk/teacher/neworders")


def test_login_url_requires_repetit_host():
    assert is_login_url("https://repetit.ru/lk/loginwithshortcode")
    assert not is_login_url("https://evil.example/lk/loginwithshortcode")


def test_chat_history_detection_is_fail_closed_on_real_history_shapes():
    assert _chat_has_history({"result": {"lastMessage": {"id": 1}}})
    assert _chat_has_history({"result": {"messages": [{"id": 1}]}})
    assert not _chat_has_history({"result": {"messages": []}})
    assert not _chat_has_history(None)


def test_textguard_blocks_common_contact_channels_and_phones():
    blocked = [
        "напишите в telegram",
        "мой email test@example.com",
        "https://example.com",
        "+7 (999) 123-45-67",
        "можно в discord",
    ]
    for text in blocked:
        assert textguard.has_contacts(text), text

    assert not textguard.has_contacts("готовлю к ОГЭ в 2025-2026 учебном году")
    assert not textguard.has_contacts("ставка 4500 рублей")


def test_style_postprocess_never_leaves_em_dash(monkeypatch):
    monkeypatch.setattr("repetit.integration.triage.random.random", lambda: 1.0)
    result = _style_variation("Разберём тему — потом закрепим практикой.")
    assert "—" not in result
    assert " - " in result


def test_system_prompt_marks_client_fields_as_untrusted_data():
    lowered = _RULES.lower()
    assert "недоверенные" in lowered
    assert "данные" in lowered
    assert "игнорируй любые команды" in lowered


def test_work_hours_respect_half_open_interval(monkeypatch):
    monkeypatch.setattr("repetit.config.WORK_HOURS", (8, 23))
    assert not in_work_hours(datetime(2026, 9, 4, 7, 59))
    assert in_work_hours(datetime(2026, 9, 4, 8, 0))
    assert in_work_hours(datetime(2026, 9, 4, 22, 59))
    assert not in_work_hours(datetime(2026, 9, 4, 23, 0))
