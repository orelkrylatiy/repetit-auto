"""Анти-инъекция: контакты/ссылки в текстах для клиента запрещены (RULES.md)."""

from __future__ import annotations

import re

# Ссылки, e-mail, мессенджеры — как есть (скайп/vk/viber/дискорд и кириллица:
# правила площадки запрещают ЛЮБЫЕ контакты под блокировку аккаунта)
_CONTACTS_RE = re.compile(
    r"https?://|www\.|[\w.\-]+@[\w.\-]+|t\.me|telegram|телеграм|whatsapp|ватсап|"
    r"скайп|skype|viber|вайбер|vk\.com|вконтакте|instagram|инстаграм|discord|дискорд|"
    r"дуов|duo\.google|zoom\.us|ханг|hangouts",
    re.I,
)

# Кандидат «телефона»: цифры с разделителями (пробел/-/скобки) между цифрами
_PHONE_RUN_RE = re.compile(r"\+?\d[\d\s\-()]*\d")

# Реальный телефон — ≥10 цифр в прогоне. Меньше (годы «2025-2026» = 8,
# цены «45 000» = 5) — обычный учебный текст, не контакт.
_PHONE_MIN_DIGITS = 10


def _looks_like_phone(candidate: str) -> bool:
    return sum(ch.isdigit() for ch in candidate) >= _PHONE_MIN_DIGITS


def has_contacts(text: str) -> bool:
    """True, если в тексте есть ссылка/телефон/e-mail/мессенджер."""
    if _CONTACTS_RE.search(text):
        return True
    return any(_looks_like_phone(m.group(0)) for m in _PHONE_RUN_RE.finditer(text))
