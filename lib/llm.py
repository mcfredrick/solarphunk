from __future__ import annotations

import logging
import os
import re
import time

import httpx
from openai import OpenAI, RateLimitError

from lib.config import BlogConfig, ModelSpec, ProviderConfig

logger = logging.getLogger(__name__)

_HTTP_REFERER = "https://mcfredrick.github.io/solarphunk"
_X_TITLE = "Solarphunk"
_OLLAMA_TIMEOUT = 300  # seconds — inference can be slow


def _cf_headers(provider: ProviderConfig) -> dict[str, str]:
    headers: dict[str, str] = {}
    if provider.cf_client_id_env:
        val = os.environ.get(provider.cf_client_id_env, "")
        if val:
            headers["CF-Access-Client-Id"] = val
    if provider.cf_client_secret_env:
        val = os.environ.get(provider.cf_client_secret_env, "")
        if val:
            headers["CF-Access-Client-Secret"] = val
    return headers


def _call_ollama(provider: ProviderConfig, model: str, system: str, user: str, max_tokens: int) -> str:
    """Call Ollama's native /api/chat endpoint directly via httpx.

    The OpenAI-compatible /v1/ path is blocked by Cloudflare's bot management
    (TLS fingerprint of Python's ssl stack differs from curl's libcurl).
    The native /api/chat endpoint works fine with httpx.
    """
    base = provider.base_url.rstrip("/")
    # base_url is configured as .../v1 for OpenAI compat; strip to get root
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/api/chat"

    headers = {"Content-Type": "application/json"}
    headers.update(_cf_headers(provider))

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"num_predict": max_tokens},
    }

    r = httpx.post(url, headers=headers, json=payload, timeout=_OLLAMA_TIMEOUT)
    if r.status_code == 403:
        raise PermissionError(f"CF Access blocked request to {url} (403)")
    r.raise_for_status()
    content = r.json()["message"]["content"]
    # DeepSeek-R1 wraps chain-of-thought in <think>...</think> before the actual response
    return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


def _call_openrouter(provider: ProviderConfig, model: str, system: str, user: str, max_tokens: int) -> str:
    api_key = provider.api_key or ""
    if provider.api_key_env:
        api_key = os.environ.get(provider.api_key_env, api_key)

    client = OpenAI(base_url=provider.base_url, api_key=api_key or "none")
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        extra_headers={"HTTP-Referer": _HTTP_REFERER, "X-Title": _X_TITLE},
    )
    if not response.choices:
        raise ValueError(f"Model {model} returned empty choices")
    return response.choices[0].message.content or ""


def _parse_retry_after(exc: Exception) -> float | None:
    try:
        headers = getattr(getattr(exc, "response", None), "headers", {})
        val = headers.get("retry-after") or headers.get("Retry-After")
        return float(val) if val else None
    except Exception:
        return None


def call_llm(
    system: str,
    user: str,
    specs: list[ModelSpec],
    max_tokens: int,
    config: BlogConfig,
    retries_per_spec: int = 3,
) -> tuple[str, str]:
    """Try each (provider, model) spec in order. Returns (content, model_id) on success."""
    last_exc: Exception | None = None

    for spec in specs:
        provider_cfg = config.providers.get(spec.provider)
        if provider_cfg is None:
            logger.warning("Unknown provider %r in spec %s — skipping", spec.provider, spec.model)
            continue

        is_ollama = spec.provider == "private"
        delay = 10.0

        for attempt in range(retries_per_spec):
            try:
                if is_ollama:
                    result = _call_ollama(provider_cfg, spec.model, system, user, max_tokens)
                else:
                    result = _call_openrouter(provider_cfg, spec.model, system, user, max_tokens)
                logger.debug("LLM call succeeded (provider=%s, model=%s)", spec.provider, spec.model)
                return result, f"{spec.provider}/{spec.model}"

            except RateLimitError as exc:
                last_exc = exc
                if attempt == retries_per_spec - 1:
                    logger.warning(
                        "Rate limit exhausted on %s/%s after %d attempts — trying next spec",
                        spec.provider, spec.model, retries_per_spec,
                    )
                    break

                wait = _parse_retry_after(exc) or delay
                logger.warning(
                    "Rate limited on %s/%s (attempt %d/%d). Waiting %.0fs...",
                    spec.provider, spec.model, attempt + 1, retries_per_spec, wait,
                )
                time.sleep(wait)
                delay = min(delay * 2, 120.0)

            except Exception as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                is_retryable = status in (500, 502, 503, 504) or status is None

                if not is_retryable or attempt == retries_per_spec - 1:
                    logger.warning(
                        "Non-retryable error on %s/%s: %s — trying next spec",
                        spec.provider, spec.model, exc,
                    )
                    break

                logger.warning(
                    "Error on %s/%s (attempt %d/%d, status=%s). Retrying in %.0fs...",
                    spec.provider, spec.model, attempt + 1, retries_per_spec, status, delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 120.0)

    raise RuntimeError(
        f"All model specs exhausted. Last error: {last_exc}"
    )
