from __future__ import annotations

import pytest

from repetit import config
from repetit.integration.feed import (
    FeedAuthError,
    FeedCapture,
    FeedError,
    _is_orders_batch,
    _is_search_orders,
)


class _Request:
    def __init__(self, method: str):
        self.method = method


class _Response:
    def __init__(self, method: str, url: str, payload=None, status: int = 200):
        self.request = _Request(method)
        self.url = url
        self.status = status
        self._payload = payload

    def json(self):
        return self._payload


class _Page:
    def __init__(self, responses, url="https://repetit.ru/lk/teacher/neworders#repetit-worker"):
        self.responses = list(responses)
        self.url = url
        self._listeners = []

    def on(self, event, callback):
        assert event == "response"
        self._listeners.append(callback)

    def reload(self, **_kwargs):
        for response in self.responses:
            for callback in list(self._listeners):
                callback(response)

    def wait_for_timeout(self, _milliseconds):
        return None

    def remove_listener(self, event, callback):
        assert event == "response"
        if callback in self._listeners:
            self._listeners.remove(callback)


def _search(payload, *, status=200, host="repetit.ru"):
    return _Response(
        "POST",
        f"https://{host}{config.API_SEARCH_ORDERS_PATH}",
        payload=payload,
        status=status,
    )


def _batch(payload, *, status=200, host="repetit.ru"):
    return _Response(
        "GET",
        f"https://{host}{config.API_ORDERS_BATCH_PATH}?ids=1",
        payload=payload,
        status=status,
    )


def _detail(order_id: int):
    return {
        "id": order_id,
        "subject": {"id": 10, "name": "Информатика"},
        "purpose": "ОГЭ",
        "information": f"Заявка {order_id}",
    }


def _fast_capture(monkeypatch, responses):
    monkeypatch.setattr(config, "CAPTURE_WINDOW_S", 0.0)
    monkeypatch.setattr(config, "CAPTURE_EXTRA_S", 0.0)
    return FeedCapture(_Page(responses))


def test_feed_matchers_require_method_path_and_repetit_host():
    assert _is_search_orders(_search([1]))
    assert _is_orders_batch(_batch([_detail(1)]))
    assert not _is_search_orders(_search([1], host="evil.example"))
    assert not _is_orders_batch(_batch([_detail(1)], host="evil.example"))
    assert not _is_search_orders(_Response("GET", f"https://repetit.ru{config.API_SEARCH_ORDERS_PATH}"))


def test_empty_feed_is_valid_without_details_batch(monkeypatch):
    capture = _fast_capture(monkeypatch, [_search([])])
    orders, ids = capture.reload_and_capture()
    assert orders == []
    assert ids == []
    assert capture.last_diag["ids_total"] == 0


def test_feed_merges_multiple_detail_batches_in_search_order(monkeypatch):
    capture = _fast_capture(
        monkeypatch,
        [_search([2, 1]), _batch([_detail(1)]), _batch([_detail(2)])],
    )
    orders, ids = capture.reload_and_capture()
    assert ids == [2, 1]
    assert [order.id for order in orders] == [2, 1]


def test_feed_rejects_ambiguous_search_responses(monkeypatch):
    capture = _fast_capture(
        monkeypatch,
        [_search([1]), _search([2]), _batch([_detail(1), _detail(2)])],
    )
    with pytest.raises(FeedError, match="FEED_AMBIGUOUS"):
        capture.reload_and_capture()
    assert capture.last_diag["error"] == "FEED_AMBIGUOUS"


def test_nonempty_feed_without_details_is_incomplete(monkeypatch):
    capture = _fast_capture(monkeypatch, [_search([1])])
    with pytest.raises(FeedError, match="батч"):
        capture.reload_and_capture()


def test_feed_auth_status_stops_cycle(monkeypatch):
    capture = _fast_capture(monkeypatch, [_search(None, status=401)])
    with pytest.raises(FeedAuthError):
        capture.reload_and_capture()


def test_feed_rejects_non_numeric_search_ids(monkeypatch):
    capture = _fast_capture(monkeypatch, [_search(["not-an-id"]), _batch([])])
    with pytest.raises(FeedError, match="нечисловой id"):
        capture.reload_and_capture()
