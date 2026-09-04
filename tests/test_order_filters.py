from __future__ import annotations

from repetit import config
from repetit.filters import hard_filter
from repetit.models.order import Order


def _detail(**overrides):
    data = {
        "id": 101,
        "subject": {"id": 10, "name": "Информатика"},
        "purpose": "Подготовка к ОГЭ",
        "information": "9 класс, нужно закрыть пробелы по Python",
        "minPrice": 2500,
        "maxPrice": 3500,
        "contactName": "Светлана",
        "area": {"cityName": "Москва"},
        "homeMetroName": "Сокол",
        "lessonPlace": 4,
        "pupilCategory": {"name": "школьники 9 класса"},
        "orderDate": "2026-09-04T08:00:00",
        "subjectAdditions": [{"name": "ОГЭ"}, {"name": "Python"}],
        "subjectDivisions": [{"name": "Программирование"}],
    }
    data.update(overrides)
    return data


def _order(**overrides) -> Order:
    return Order.from_api(_detail(**overrides))


def test_order_from_api_maps_triage_fields():
    order = _order()
    assert order.id == 101
    assert order.subject == "Информатика"
    assert order.is_remote
    assert order.additions == ["ОГЭ", "Python"]
    assert order.divisions == ["Программирование"]
    assert "Python" in order.searchable
    assert order.title.startswith("Информатика:")

    payload = order.triage_dict()
    assert payload["id"] == 101
    assert payload["online"] is True
    assert payload["price_rub_per_60min"] == [2500, 3500]
    assert payload["client_name"] == "Светлана"


def test_order_from_api_tolerates_missing_optional_nested_fields():
    order = Order.from_api({"id": 7, "subject": {"name": "Информатика"}})
    assert order.id == 7
    assert order.subject == "Информатика"
    assert order.additions == []
    assert order.divisions == []
    assert order.city is None
    assert not order.is_remote


def test_hard_filter_rejects_wrong_subject(monkeypatch):
    monkeypatch.setattr(config, "SUBJECT_KEYWORDS", ["информатик", "программирован"])
    verdict = hard_filter(
        _order(
            subject={"id": 20, "name": "Математика"},
            purpose="Подтянуть алгебру",
            information="Нужно разобрать квадратные уравнения",
            subjectAdditions=[],
            subjectDivisions=[],
        )
    )
    assert not verdict.passed
    assert "не наш предмет" in verdict.reason


def test_hard_filter_rejects_special_needs_and_barter(monkeypatch):
    monkeypatch.setattr(config, "SUBJECT_KEYWORDS", ["информатик"])
    monkeypatch.setattr(config, "MIN_CLIENT_RATE", 0)

    special = hard_filter(_order(information="Информатика, ребёнок с СДВГ"))
    assert not special.passed
    assert "особые потребности" in special.reason

    barter = hard_filter(_order(information="Информатика, предлагаю бартер"))
    assert not barter.passed
    assert "бартер" in barter.reason


def test_hard_filter_budget_uses_highest_known_client_price(monkeypatch):
    monkeypatch.setattr(config, "SUBJECT_KEYWORDS", ["информатик"])
    monkeypatch.setattr(config, "MIN_CLIENT_RATE", 3000)

    assert hard_filter(_order(minPrice=2000, maxPrice=3500)).passed

    low = hard_filter(_order(minPrice=1500, maxPrice=2500))
    assert not low.passed
    assert "2500 < 3000" in low.reason


def test_hard_filter_unknown_budget_is_not_false_negative(monkeypatch):
    monkeypatch.setattr(config, "SUBJECT_KEYWORDS", ["информатик"])
    monkeypatch.setattr(config, "MIN_CLIENT_RATE", 3000)
    assert hard_filter(_order(minPrice=None, maxPrice=None)).passed
