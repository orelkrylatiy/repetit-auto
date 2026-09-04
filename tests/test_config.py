from __future__ import annotations

from repetit import config


def test_parse_work_hours_accepts_normal_and_explicit_24x7():
    assert config._parse_work_hours("8,23") == (8, 23)
    assert config._parse_work_hours(" 9 , 18 ") == (9, 18)
    assert config._parse_work_hours("0,24") == (0, 24)


def test_parse_work_hours_invalid_values_fail_to_safe_default():
    bad = [None, "", "8", "abc,23", "8,abc", "23,8", "8,8", "-1,23", "8,25"]
    for value in bad:
        assert config._parse_work_hours(value) == config.DEFAULT_WORK_HOURS, value


def test_chat_url_encodes_title_but_keeps_order_id():
    url = config.chat_url(123, "№ 123, Светлана")
    assert url.startswith("https://repetit.ru/lk/teacher/chatforteacher?orderId=123&chatTitle=")
    assert "Светлана" not in url
    assert "%E2%84%96" in url
