"""LLM-триаж и генерация текста отклика (glm-5.3-flash).

Вердикт JSON: {"decision": "respond"|"skip", "reason": str, "text": str}.
Гейты честности: текст проходит textguard (никаких контактов — блокировка
аккаунта по правилам площадки, RECON §5), длина ≤ config.MAX_TEXT_LEN.
"""

from __future__ import annotations

import json
import logging

from repetit import config
from repetit.llm import client as llm
from repetit.models.order import Order
from repetit.utils import textguard

log = logging.getLogger("repetit.triage")

_RULES = """\
Ты — ассистент репетитора. Твоя задача: по заявке ученика решить, откликаться ли,
и написать первый ответ клиенту в чат.

ЖЁСТКИЕ ПРАВИЛА ТЕКСТА:
1. Запрещено указывать любые контакты (телефон, email, мессенджеры, ссылки,
   никнеймы) — это блокировка аккаунта по правилам площадки.
2. Текст только честный: не выдумывай опыт, достижения, отзывы, стаж.
   Нет требуемого опыта — пиши как есть, упирай на смежное.
3. Текст кастомный под конкретную заявку: упоминай детали (предмет, цель, класс,
   формат, расписание). Никаких шаблонных заготовок.
4. Обращение по имени клиента, живой тон, без канцелярита, 3–6 предложений.
5. Не называй цену, если клиент не спросил в тексте заявки.

Ответ строго JSON:
{"decision": "respond" | "skip", "reason": "кратко почему", "text": "..."}
"text" обязателен при decision=respond, пустой при skip.
"""


def _persona() -> str:
    path = config.PERSONA_DIR / f"{config.PERSONA}.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    log.warning("персона %s не найдена (%s) — работаю без неё", config.PERSONA, path)
    return ""


def triage(order: Order) -> dict:
    """LLM-вердикт по заявке. Возвращает {decision, reason, text}.

    Ошибки LLM/парсинга → decision=error (кандидат остаётся, повторим позже).
    """
    system = (_RULES + "\n\nПерсона репетитора:\n" + _persona()).strip()
    user = json.dumps(order.triage_dict(), ensure_ascii=False)
    try:
        raw = llm.chat(system, user, temperature=0.4, max_tokens=2000)
        data = llm.json_reply(raw)
    except Exception as e:
        log.warning("LLM сбой по заявке %s: %s", order.id, e)
        # llm_error: сеть/лимит провайдера — кандидат НЕ терминальный, повторим
        # после cooldown (main пишет llm-cooldown и прекращает триаж цикла)
        return {"decision": "llm_error", "reason": f"llm: {e}", "text": ""}

    decision = data.get("decision")
    if decision not in ("respond", "skip"):
        return {"decision": "error", "reason": f"невалидный decision: {decision!r}", "text": ""}
    if decision == "skip":
        return {"decision": "skip", "reason": str(data.get("reason") or "")[:500], "text": ""}

    text = str(data.get("text") or "").strip()
    if not (config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN):
        return {
            "decision": "error",
            "reason": f"длина текста {len(text)} вне {config.MIN_TEXT_LEN}..{config.MAX_TEXT_LEN}",
            "text": "",
        }
    if textguard.has_contacts(text):
        return {"decision": "error", "reason": "textguard: контакты в тексте", "text": ""}
    return {"decision": "respond", "reason": str(data.get("reason") or "")[:500], "text": text}
