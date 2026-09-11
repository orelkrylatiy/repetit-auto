"""Safe deterministic fallback for first outreach messages.

Fallback is used only after hard filters have passed and only when the LLM
cannot produce a usable reply. It never replaces a normal LLM ``skip`` verdict.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from repetit import config
from repetit.utils import textguard

DEFAULT_TEMPLATES: tuple[str, ...] = (
    (
        "Здравствуйте! Могу помочь с информатикой и программированием. "
        "На первом занятии посмотрим, что уже получается, и определим, "
        "на каких темах лучше сосредоточиться в первую очередь. "
        "Занимаюсь онлайн. Когда вам удобно попробовать?"
    ),
    (
        "Добрый день! Работаю онлайн с информатикой и программированием. "
        "Предлагаю начать с пробного занятия: разберём текущий уровень, "
        "посмотрим основные сложности и после этого спокойно соберём план. "
        "Когда вам было бы удобно?"
    ),
    (
        "Здравствуйте! Могу подключиться по информатике и программированию. "
        "На вводном занятии разберём текущие задачи и станет понятно, "
        "что лучше взять в работу первым. Формат занятий онлайн. "
        "Подскажите, когда удобно провести первое занятие?"
    ),
)


def choose_fallback(order_id: int | str, templates: Sequence[str]) -> str | None:
    """Stable per-order template choice, mirroring profi-agent's fallback."""
    clean = [str(t).strip() for t in templates if str(t).strip()]
    if not clean:
        return None
    digest = hashlib.sha256(str(order_id).encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(clean)
    return clean[index]


def validate_fallback(text: str) -> tuple[str | None, str | None]:
    """Apply the same hard output contract as first-message LLM text."""
    text = str(text or "").strip().replace("—", "-")
    if not (config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN):
        return (
            None,
            f"длина fallback {len(text)} вне {config.MIN_TEXT_LEN}..{config.MAX_TEXT_LEN}",
        )
    if textguard.has_contacts(text):
        return None, "textguard: контакты в fallback"
    return text, None


def fallback_reply(
    order_id: int | str,
    *,
    reason: str,
    templates: Sequence[str] | None = None,
) -> dict:
    """Return a triage-compatible fallback decision.

    Passing an explicit empty ``templates`` sequence is intentionally different
    from ``None``: it means the caller has no fallback copy and must fail closed.
    """
    if not config.FALLBACK_ENABLED:
        return {
            "decision": "error",
            "reason": f"{reason}; fallback выключен"[:500],
            "text": "",
            "source": "fallback",
        }

    available: Sequence[str]
    if templates is None:
        available = config.FALLBACK_TEMPLATES or DEFAULT_TEMPLATES
    else:
        available = templates
    selected = choose_fallback(order_id, available)
    if not selected:
        return {
            "decision": "error",
            "reason": f"{reason}; fallback-шаблоны пусты"[:500],
            "text": "",
            "source": "fallback",
        }
    text, invalid = validate_fallback(selected)
    if invalid:
        return {
            "decision": "error",
            "reason": f"{reason}; fallback отклонён: {invalid}"[:500],
            "text": "",
            "source": "fallback",
        }
    return {
        "decision": "respond",
        "reason": str(reason)[:500],
        "text": text,
        "source": "fallback",
    }
