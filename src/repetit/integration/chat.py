"""Fail-closed inspection and UI replies for existing Repetit chats.

The module deliberately does not scrape a chat sidebar. Auto-reply candidates
come from SQLite rows whose first message was sent by this worker. For each
candidate we open its chat URL, passively capture Repetit's chat-state response,
and reply only when the last message can be proven to be from the client.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext, Page

from repetit import config
from repetit.utils.pacing import human_pause, type_human

log = logging.getLogger("repetit.chat")

_COMPOSER = '[data-testid="message-composer-input"]'
_SEND_BTN = '[data-testid="message-composer-send-button"]'
_WS_CHAT_PATH = "/api/chats/personal"
_LEGACY_CHAT_PATHS = {"/api/teacher/chats/order", "/lk/api/teacher/chats/order"}

_CLIENT_ROLES = {"client", "customer", "student", "pupil", "parent"}
_TUTOR_ROLES = {"teacher", "tutor", "expert", "specialist", "executor", "self", "me"}
_SYSTEM_ROLES = {"system", "robot", "bot", "service"}


@dataclass(frozen=True)
class ChatMessage:
    key: str
    text: str
    sender: str  # client | tutor | system | unknown
    timestamp: float | None


@dataclass(frozen=True)
class ChatSnapshot:
    state: str  # empty | client_last | tutor_last | system_last | unsupported
    incoming_key: str | None = None
    incoming_text: str | None = None
    dialog_text: str = ""
    detail: str = ""


def is_chat_state_url(url: str, method: str, order_id: int | str) -> bool:
    """Accept only the confirmed Repetit chat-state origins and exact paths."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme != "https" or method.upper() != "GET":
        return False
    host = (parsed.hostname or "").lower()
    if host == "ws.repetit.ru" and parsed.path == _WS_CHAT_PATH:
        values = parse_qs(parsed.query).get("orderId", [])
        return str(order_id) in {str(v) for v in values}
    if host == "repetit.ru" and parsed.path in _LEGACY_CHAT_PATHS:
        values = parse_qs(parsed.query).get("orderId", [])
        return not values or str(order_id) in {str(v) for v in values}
    return False


def _role_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower().replace("_", "-")
    return cleaned or None


def _classify_sender(message: dict) -> str:
    """Classify sender only from explicit role/flags.

    `isOutgoing=false` by itself is intentionally insufficient: service/system
    messages may also be non-outgoing. Unknown shapes therefore fail closed.
    """
    containers = [message]
    for key in ("sender", "author", "from"):
        nested = message.get(key)
        if isinstance(nested, dict):
            containers.append(nested)
        elif isinstance(nested, str):
            role = _role_value(nested)
            if role in _SYSTEM_ROLES:
                return "system"
            if role in _TUTOR_ROLES:
                return "tutor"
            if role in _CLIENT_ROLES:
                return "client"

    for obj in containers:
        if obj.get("isSystem") is True or obj.get("system") is True:
            return "system"
    for obj in containers:
        if any(obj.get(k) is True for k in ("isTeacher", "isTutor", "isMine", "isOwn")):
            return "tutor"
    if message.get("isOutgoing") is True or message.get("outgoing") is True:
        return "tutor"
    for obj in containers:
        if any(obj.get(k) is True for k in ("isClient", "isCustomer", "isStudent")):
            return "client"

    role_keys = ("senderType", "authorType", "senderRole", "authorRole", "role", "type")
    seen: set[str] = set()
    for obj in containers:
        for key in role_keys:
            role = _role_value(obj.get(key))
            if role:
                seen.add(role)
    if seen & _SYSTEM_ROLES:
        return "system"
    if seen & _TUTOR_ROLES:
        return "tutor"
    if seen & _CLIENT_ROLES:
        return "client"
    return "unknown"


def _message_timestamp(message: dict) -> float | None:
    for key in ("createdAt", "created_at", "created", "sentAt", "date", "timestamp"):
        value = message.get(key)
        if isinstance(value, (int, float)):
            # Milliseconds are common in browser APIs.
            return float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
        if isinstance(value, str) and value.strip():
            raw = value.strip().replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(raw).timestamp()
            except ValueError:
                continue
    return None


def _parse_message(message: object) -> tuple[ChatMessage | None, str | None]:
    if not isinstance(message, dict):
        return None, "message не object"
    key = None
    for field in ("id", "messageId", "messageID", "guid", "uuid"):
        value = message.get(field)
        if value not in (None, ""):
            key = str(value)
            break
    if not key:
        return None, "нет устойчивого message id"

    raw_text = None
    for field in ("text", "message", "body", "content"):
        value = message.get(field)
        if isinstance(value, str):
            raw_text = value
            break
    text = " ".join((raw_text or "").split())
    if not text:
        return None, "нет текстового содержимого сообщения"

    sender = _classify_sender(message)
    return ChatMessage(key=key, text=text, sender=sender, timestamp=_message_timestamp(message)), None


def _root(payload: dict) -> dict | None:
    result = payload.get("result")
    if result is None:
        return payload
    return result if isinstance(result, dict) else None


def _dialog_text(root: dict, last: ChatMessage) -> str:
    raw_messages = root.get("messages")
    parsed: list[ChatMessage] = []
    if isinstance(raw_messages, list):
        for item in raw_messages:
            msg, _ = _parse_message(item)
            if msg is not None:
                parsed.append(msg)
    if not parsed:
        parsed = [last]
    elif all(msg.timestamp is not None for msg in parsed):
        parsed.sort(key=lambda msg: msg.timestamp or 0)
    lines = [f"{msg.sender}: {msg.text}" for msg in parsed[-20:]]
    if last.key not in {msg.key for msg in parsed}:
        lines.append(f"{last.sender}: {last.text}")
    return "\n".join(lines)[-4500:]


def parse_chat_snapshot(payload: object) -> ChatSnapshot:
    """Parse chat state without guessing an unverified sender/order shape."""
    if payload == {}:
        return ChatSnapshot(state="empty", detail="HTTP 204 / пустой чат")
    if not isinstance(payload, dict):
        return ChatSnapshot(state="unsupported", detail="payload не object")
    root = _root(payload)
    if root is None:
        return ChatSnapshot(state="unsupported", detail="result не object")

    raw_last = root.get("lastMessage")
    last: ChatMessage | None = None
    if raw_last is not None:
        last, error = _parse_message(raw_last)
        if error:
            return ChatSnapshot(state="unsupported", detail=f"lastMessage: {error}")
    else:
        raw_messages = root.get("messages")
        if raw_messages in (None, []):
            return ChatSnapshot(state="empty", detail="история пуста")
        if not isinstance(raw_messages, list):
            return ChatSnapshot(state="unsupported", detail="messages не list")
        parsed: list[ChatMessage] = []
        for item in raw_messages:
            msg, error = _parse_message(item)
            if error:
                return ChatSnapshot(state="unsupported", detail=f"messages: {error}")
            parsed.append(msg)
        if not parsed:
            return ChatSnapshot(state="empty", detail="messages пуст")
        if any(msg.timestamp is None for msg in parsed):
            return ChatSnapshot(
                state="unsupported",
                detail="нет lastMessage и timestamp для безопасного определения последнего",
            )
        last = max(parsed, key=lambda msg: msg.timestamp or 0)

    if last is None or last.sender == "unknown":
        return ChatSnapshot(state="unsupported", detail="sender последнего сообщения неизвестен")
    dialog = _dialog_text(root, last)
    if last.sender == "client":
        return ChatSnapshot(
            state="client_last",
            incoming_key=last.key,
            incoming_text=last.text,
            dialog_text=dialog,
            detail="последнее сообщение явно от клиента",
        )
    return ChatSnapshot(
        state=f"{last.sender}_last",
        dialog_text=dialog,
        detail=f"последнее сообщение: {last.sender}",
    )


def payload_shape(payload: object) -> str:
    """PII-free shape hint for canary logs: keys only, never message values."""
    if not isinstance(payload, dict):
        return type(payload).__name__
    keys = sorted(str(k) for k in payload)[:30]
    root = _root(payload)
    root_keys = sorted(str(k) for k in root)[:30] if isinstance(root, dict) else []
    return f"payload_keys={keys}; result_keys={root_keys}"


class ChatResponder:
    def __init__(self, ctx: BrowserContext):
        self.ctx = ctx

    def _capture(self, page: Page, order_id: int | str, chat_title: str) -> tuple[object, str | None]:
        events: list[tuple[str, object]] = []

        def on_response(resp) -> None:
            try:
                if not is_chat_state_url(resp.url or "", resp.request.method, order_id):
                    return
                if resp.status == 204:
                    events.append(("payload", {}))
                    return
                if resp.status != 200:
                    events.append(("error", f"HTTP {resp.status}"))
                    return
                data = resp.json()
                if not isinstance(data, dict):
                    events.append(("error", f"payload {type(data).__name__}"))
                    return
                events.append(("payload", data))
            except Exception as exc:
                events.append(("error", f"{type(exc).__name__}: {exc}"))

        page.on("response", on_response)
        page.goto(config.chat_url(order_id, chat_title), wait_until="domcontentloaded", timeout=45_000)
        human_pause(0.8, 1.6)
        if config.LOGIN_PATH in (page.url or ""):
            return {}, "auth_required"
        try:
            page.wait_for_selector(_COMPOSER, timeout=15_000)
        except Exception:
            if config.LOGIN_PATH in (page.url or ""):
                return {}, "auth_required"
            return {}, "composer не появился"

        deadline = time.monotonic() + config.CHAT_STATE_WAIT_S
        while not events and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        if not events:
            return {}, "chat-state response не пойман"
        errors = [str(value) for kind, value in events if kind == "error"]
        if errors:
            return {}, "; ".join(errors)[:500]
        payloads = [value for kind, value in events if kind == "payload"]
        nonempty = [value for value in payloads if value != {}]
        if len(nonempty) > 1 and any(value != nonempty[0] for value in nonempty[1:]):
            return {}, "несколько разных chat-state payload за одно открытие"
        return (nonempty[0] if nonempty else {}), None

    def inspect(self, order_id: int | str, chat_title: str) -> ChatSnapshot:
        page = self.ctx.new_page()
        try:
            payload, error = self._capture(page, order_id, chat_title)
            if error == "auth_required":
                return ChatSnapshot(state="unsupported", detail="auth_required")
            if error:
                return ChatSnapshot(state="unsupported", detail=error)
            snapshot = parse_chat_snapshot(payload)
            if snapshot.state == "unsupported":
                log.warning(
                    "chat %s: unsupported state: %s | %s",
                    order_id,
                    snapshot.detail,
                    payload_shape(payload),
                )
            return snapshot
        finally:
            try:
                page.close(run_before_unload=False)
            except Exception:
                pass

    def send_reply(
        self,
        order_id: int | str,
        chat_title: str,
        incoming_key: str,
        text: str,
    ) -> dict:
        """Re-open, re-check exact incoming message, then UI-send the reply."""
        page = self.ctx.new_page()
        shot = None
        clicked = False
        try:
            payload, error = self._capture(page, order_id, chat_title)
            if error == "auth_required":
                return {"status": "auth_required", "detail": error, "screenshot": None}
            if error:
                return {"status": "retry", "detail": error, "screenshot": None}
            snapshot = parse_chat_snapshot(payload)
            if snapshot.state != "client_last" or snapshot.incoming_key != str(incoming_key):
                return {
                    "status": "stale",
                    "detail": "последнее сообщение изменилось до Send",
                    "screenshot": None,
                }

            composer = page.locator(_COMPOSER).first
            send_btn = page.locator(_SEND_BTN).first
            try:
                existing = (composer.input_value() or "").strip()
            except Exception as exc:
                return {
                    "status": "retry",
                    "detail": f"не смог прочитать composer: {exc}",
                    "screenshot": None,
                }
            if existing:
                return {
                    "status": "retry",
                    "detail": "composer уже содержит ручной черновик — не трогаем",
                    "screenshot": self._screenshot(page, order_id, "manual-draft"),
                }

            body_before = page.locator("body").inner_text()
            if text[:80] in body_before:
                return {
                    "status": "already_sent",
                    "detail": "такой ответ уже виден в чате",
                    "screenshot": None,
                }

            human_pause(0.6, 1.2)
            type_human(page, composer, text)
            human_pause(0.3, 0.8)
            value = (composer.input_value() or "").strip()
            if value != text.strip():
                return {
                    "status": "retry",
                    "detail": f"в composer не наш текст: {value[:60]!r}",
                    "screenshot": self._screenshot(page, order_id, "input-mismatch"),
                }
            self._screenshot(page, order_id, "filled")
            send_btn.click()
            clicked = True

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                body = page.locator("body").inner_text()
                try:
                    empty = (composer.input_value() or "").strip() == ""
                except Exception:
                    empty = False
                if text[:80] in body and empty:
                    shot = self._screenshot(page, order_id, "after")
                    return {"status": "sent", "detail": "ок", "screenshot": shot}
                page.wait_for_timeout(500)

            shot = self._screenshot(page, order_id, "after-unknown")
            return {
                "status": "unknown" if clicked else "retry",
                "detail": "после Send не подтверждены одновременно bubble и пустой composer",
                "screenshot": shot,
            }
        except Exception as exc:
            shot = shot or self._try_screenshot(page, order_id)
            return {
                "status": "unknown" if clicked else "retry",
                "detail": f"{type(exc).__name__}: {exc}",
                "screenshot": shot,
            }
        finally:
            try:
                page.close(run_before_unload=False)
            except Exception:
                pass

    @staticmethod
    def _screenshot(page: Page, order_id: int | str, tag: str) -> str | None:
        try:
            config.CHAT_SHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = config.CHAT_SHOT_DIR / f"{order_id}_{tag}_{int(time.time())}.png"
            page.screenshot(path=str(path))
            return str(path)
        except Exception:
            return None

    def _try_screenshot(self, page: Page, order_id: int | str) -> str | None:
        return self._screenshot(page, order_id, "error")
