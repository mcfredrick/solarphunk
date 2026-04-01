from __future__ import annotations

import logging
import os
import time

import httpx
from openai import OpenAI, RateLimitError

from lib.config import BlogConfig, ModelSpec, ProviderConfig

logger = logging.getLogger(__name__)

_HTTP_REFERER = "https://mcfredrick.github.io/solarphunk"
_X_TITLE = "Solarphunk"


def _build_client(provider: ProviderConfig) -> OpenAI:
    """Build an OpenAI-compatible client for the given provider config."""
    api_key = provider.api_key or ""
    if provider.api_key_env:
        api_key = os.environ.get(provider.api_key_env, api_key)

    extra_headers: dict[str, str] = {}
    if provider.cf_client_id_env:
        val = os.environ.get(provider.cf_client_id_env, "")
        if val:
            extra_headers["CF-Access-Client-Id"] = val
    if provider.cf_client_secret_env:
        val = os.environ.get(provider.cf_client_secret_env, "")
        if val:
            extra_headers["CF-Access-Client-Secret"] = val

    http_client = httpx.Client(headers=extra_headers) if extra_headers else None

    kwargs: dict = dict(base_url=provider.base_url, api_key=api_key or "none")
    if http_client:
        kwargs["http_client"] = http_client

    return OpenAI(**kwargs)


def _call_once(client: OpenAI, model: str, system: str, user: str, max_tokens: int, is_openrouter: bool) -> str:
    extra_headers: dict[str, str] = {}
    if is_openrouter:
        extra_headers = {"HTTP-Referer": _HTTP_REFERER, "X-Title": _X_TITLE}

    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        extra_headers=extra_headers or None,
    )
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
) -> str:
    """Try each (provider, model) spec in order. Move to the next on rate limit or error."""
    last_exc: Exception | None = None

    for spec in specs:
        provider_cfg = config.providers.get(spec.provider)
        if provider_cfg is None:
            logger.warning("Unknown provider %r in spec %s — skipping", spec.provider, spec.model)
            continue

        is_openrouter = spec.provider == "openrouter"
        client = _build_client(provider_cfg)
        delay = 10.0

        for attempt in range(retries_per_spec):
            try:
                result = _call_once(client, spec.model, system, user, max_tokens, is_openrouter)
                logger.debug("LLM call succeeded (provider=%s, model=%s)", spec.provider, spec.model)
                return result

            except RateLimitError as exc:
                last_exc = exc
                if attempt == retries_per_spec - 1:
                    logger.warning(
                        "Rate limit exhausted on %s/%s after %d attempts — trying next spec",
                        spec.provider, spec.model, retries_per_spec,
                    )
                    break  # move to next spec

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
