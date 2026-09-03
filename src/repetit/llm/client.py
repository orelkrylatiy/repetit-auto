"""Мульти-провайдерный LLM-слой: GLM (Z.AI), Claude (Anthropic/прокси), ChatGPT (OpenAI).

Ключи и настройки — из окружения или ~/repetit-agent/.env (в git не попадает):

  LLM_PROVIDER       = glm | anthropic | openai     (по умолчанию glm)
  LLM_MODEL          — модель (по умолчанию — дефолт провайдера)
  GLM_API_KEY / ZAI_API_KEY        + GLM_BASE_URL   (api.z.ai, OpenAI-протокол)
  ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL
    — работает и с настоящим Anthropic, и с Anthropic-совместимыми
      прокси GLM (например claude-buffet)
  OPENAI_API_KEY     + OPENAI_BASE_URL              (OpenAI-протокол)

Использование: llm.chat(system="...", user="...") -> str
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

_TIMEOUT_S = 90


def _load_env_file() -> dict:
    env: dict = {}
    path = Path(__file__).resolve().parents[3] / ".env"  # src/repetit/llm/ → корень репо
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip("'\"")
    return env


_ENV = {**_load_env_file(), **{k: v for k, v in os.environ.items() if v}}


def _cfg(name: str, default: str | None = None) -> str | None:
    return _ENV.get(name, default)


def provider() -> str:
    return (_cfg("LLM_PROVIDER") or "glm").lower()


def status() -> dict:
    """Что настроено сейчас (ключи маскируются)."""
    p = provider()
    info: dict = {"provider": p, "model": _model(p), "base": _base(p)}
    key, kname = _key(p)
    info["key_var"] = kname
    info["key_masked"] = (key[:10] + "…" + key[-4:]) if key else None
    return info


def _fallback_key() -> tuple[str | None, str]:
    """Второй z.ai-ключ (фоллбэк при 429/лимитах): GLM_API_KEY_2 или ZAI_API_KEY_2."""
    for n in ("GLM_API_KEY_2", "ZAI_API_KEY_2"):
        if _cfg(n):
            return _cfg(n), n
    return None, "GLM_API_KEY_2"


def _is_limit_error(err: Exception) -> bool:
    s = str(err)
    return "429" in s or "1308" in s or "1310" in s or "Limit" in s


# Публичный алиас: вызывающий код (autopilot, чаты) различает «лимит
# провайдера» (пауза флоу) от прочих сбоев (ретрай/скип кандидата).
is_limit_error = _is_limit_error


def _key(p: str) -> tuple[str | None, str]:
    if p == "glm":
        for n in ("GLM_API_KEY", "ZAI_API_KEY"):
            if _cfg(n):
                return _cfg(n), n
        return None, "GLM_API_KEY"
    if p == "anthropic":
        for n in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
            if _cfg(n):
                return _cfg(n), n
        return None, "ANTHROPIC_AUTH_TOKEN"
    if p == "openai":
        return _cfg("OPENAI_API_KEY"), "OPENAI_API_KEY"
    raise ValueError(f"неизвестный провайдер: {p}")


def _base(p: str) -> str:
    if p == "glm":
        return (_cfg("GLM_BASE_URL") or "https://api.z.ai/api/paas/v4").rstrip("/")
    if p == "anthropic":
        return (_cfg("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    if p == "openai":
        return (_cfg("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    raise ValueError(p)


def _model(p: str) -> str:
    if _cfg("LLM_MODEL"):
        return _cfg("LLM_MODEL")
    return {
        "glm": "glm-4.6",
        "anthropic": "claude-sonnet-4-5",
        "openai": "gpt-4o",
    }[p]


def models_chain() -> list[str]:
    """Цепочка моделей: основная (дешёвая) → фолбэк (LLM_FALLBACK_MODEL)."""
    primary = _model(provider())
    fb = _cfg("LLM_FALLBACK_MODEL")
    chain = [primary]
    if fb and fb != primary:
        chain.append(fb)
    return chain


def _post(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {e.code} от {url}: {body}") from e


def _chat_openai_style(
    system: str, user: str, temperature: float, max_tokens: int, model: str | None = None
) -> str:
    """OpenAI-совместимый протокол (glm, openai). С фоллбэком на второй ключ."""
    m = model or _model(provider())
    keys: list[tuple[str, str]] = []
    key, kname = _key(provider())
    if key:
        keys.append((key, kname))
    if provider() == "glm":
        k2, n2 = _fallback_key()
        if k2 and k2 != key:
            keys.append((k2, n2))
    if not keys:
        raise RuntimeError("API-ключ не задан")
    last_err: Exception | None = None
    for i, (k, _) in enumerate(keys):
        try:
            data = _post(
                _base(provider()) + "/chat/completions",
                {"Content-Type": "application/json", "Authorization": f"Bearer {k}"},
                {
                    "model": m,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            if i > 0:
                _ENV["_last_used"] = "fallback"
            return data["choices"][0]["message"]["content"]
        except Exception as exc:  # лимит/сбой — пробуем следующий ключ
            last_err = exc
            if not _is_limit_error(exc) or i == len(keys) - 1:
                raise
            time.sleep(2)
    raise last_err  # pragma: no cover


def _chat_anthropic(system: str, user: str, temperature: float, max_tokens: int, model: str) -> str:
    """Anthropic Messages API; совместимо и с прокси GLM (claude-buffet и др.).

    Цепочка ключей: ANTHROPIC_AUTH_TOKEN/API_KEY → GLM_API_KEY_2/ZAI_API_KEY_2
    (второй z.ai-аккаунт) — фолбэк при 429/лимите первого, как в openai-пути.
    """
    keys: list[tuple[str, str]] = []
    key, kname = _key("anthropic")
    if key:
        keys.append((key, kname))
    k2, n2 = _fallback_key()
    if k2 and k2 != key:
        keys.append((k2, n2))
    if not keys:
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN/ANTHROPIC_API_KEY не задан")
    last_err: Exception | None = None
    for i, (k, kname_i) in enumerate(keys):
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            # AUTH_TOKEN идёт как Bearer, API_KEY — как x-api-key; шлём оба для прокси
            "Authorization": f"Bearer {k}",
        }
        if kname_i == "ANTHROPIC_API_KEY":
            headers["x-api-key"] = k
        try:
            data = _post(
                _base("anthropic") + "/v1/messages",
                headers,
                {
                    "model": model,
                    "system": system,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            if i > 0:
                _ENV["_last_used"] = "fallback"
            parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
            return "".join(parts)
        except Exception as exc:  # лимит/сбой — пробуем следующий ключ
            last_err = exc
            if not _is_limit_error(exc) or i == len(keys) - 1:
                raise
            time.sleep(2)
    raise last_err  # pragma: no cover


def set_model(model: str) -> None:
    """Переопределить LLM_MODEL (llm-check --model)."""
    _ENV["LLM_MODEL"] = model


def chat(
    system: str,
    user: str,
    temperature: float = 0.7,
    max_tokens: int = 900,
    model: str | None = None,
) -> str:
    """Один вызов выбранного провайдера. Исключение — при ошибке сети/API."""
    p = provider()
    m = model or _model(p)
    if p == "anthropic":
        return _chat_anthropic(system, user, temperature, max_tokens, m)
    return _chat_openai_style(system, user, temperature, max_tokens, m)  # glm, openai


def json_reply(raw: str) -> dict:
    """Парсинг JSON-вердикта LLM: срезаем ```-забор и парсим.

    Падает исключением на мусоре — вызывающий код решает, что делать
    (ретрай другой моделью / needs_human).
    """
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)
