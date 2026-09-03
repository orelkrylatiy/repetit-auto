"""LLM-триаж и генерация текста отклика (glm-5.3-flash).

Вердикт JSON: {"decision": "respond"|"skip", "reason": str, "text": str}.
Гейты честности: текст проходит textguard (никаких контактов — блокировка
аккаунта по правилам площадки, RECON §5), длина в config.MIN/MAX_TEXT_LEN.
Стиль «человек, а не ИИ» — правила в промпте + кодовая вариация
(_style_variation, docs/reference/HUMAN_STYLE.md): смысл и факты не трогаем.
"""

from __future__ import annotations

import json
import logging
import random

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
2. Текст только честный: не выдумывай опыт, достижения, отзывов, стаж.
   Нет требуемого опыта — пиши как есть, упирай на смежное.
3. Текст кастомный под конкретную заявку: упоминай детали (предмет, цель, класс,
   формат, расписание). Никаких шаблонных заготовок.
4. Обращение по имени клиента, 3–6 предложений.
5. Не называй цену, если клиент не спросил в тексте заявки.

СТИЛЬ «ЧЕЛОВЕК, А НЕ ИИ» (docs/reference/HUMAN_STYLE.md, обязателен):
- никакого канцелярита: «осуществляю подготовку» → «готовлю», «данный» → «этот»;
- запретные штампы: «важно отметить», «стоит подчеркнуть», «кроме того»,
  «не просто X, а Y», «Честно говоря», «С удовольствием», «Буду рад помочь»,
  «С уважением», «Надеюсь, это поможет»;
- длинное тире «—» НЕ использовать совсем: точка, запятая, двоеточие,
  скобки-оговорки;
- без списков, эмодзи, жирного текста, риторических триад («внимание,
  дисциплина, результат»);
- без пассива («занятие будет проведено» → «проведу занятие»), без одинаковых
  начал предложений подряд («Я помогу… Я подготовлю…»);
- без гарантий результата и обещаний баллов;
- живые мелочи: неровный ритм (длинная фраза. короткая.), конкретные
  непоказательные детали из заявки, при уместности лёгкая оговорка
  («точнее даже не ЕГЭ, а ОГЭ»);
- смайлик НЕ ставь — его добавляет код, не ты.

Ответ строго JSON:
{"decision": "respond" | "skip", "reason": "кратко почему", "text": "..."}
"text" обязателен при decision=respond, пустой при skip.
"""


def _style_variation(text: str) -> str:
    """Кодовая вариация стиля (HUMAN_STYLE.md): что рандомит код, а не модель.

    - дефис «-» вместо длинного тире «—» в ~50% сообщений;
    - лёгкая улыбка «)» в конце ~40% сообщений (мессенджерная норма).
    Частоты рандомит код: окажись это в промпте — вариация сама станет
    шаблоном. Смысл и факты не меняются (только пунктуация хвоста).
    """
    if random.random() < 0.5:
        text = text.replace("—", "-")
    core = text.rstrip()
    if random.random() < 0.4 and not core.endswith((")", ":)", "!)")):
        if core.endswith((".", "!", "?")):
            core = core[:-1]
        core += ")"
    return core


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

    text = _style_variation(str(data.get("text") or "").strip())
    if not (config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN):
        return {
            "decision": "error",
            "reason": f"длина текста {len(text)} вне {config.MIN_TEXT_LEN}..{config.MAX_TEXT_LEN}",
            "text": "",
        }
    if textguard.has_contacts(text):
        return {"decision": "error", "reason": "textguard: контакты в тексте", "text": ""}
    return {"decision": "respond", "reason": str(data.get("reason") or "")[:500], "text": text}
