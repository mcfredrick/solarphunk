from __future__ import annotations

import logging
import os
import time

from openai import OpenAI, RateLimitError

from lib.config import BlogConfig, ModelSpec, ProviderConfig

logger = logging.getLogger(__name__)

_HTTP_REFERER = "https://mcfredrick.github.io/solarphunk"
_X_TITLE = "Solarphunk"


def _build_client(provider: ProviderConfig) -> tuple[OpenAI, dict[str, str]]:
    """Build an OpenAI-compatible client. Returns (client, extra_headers_for_each_call)."""
    api_key = provider.api_key or ""
    if provider.api_key_env:
        api_key = os.environ.get(provider.api_key_env, api_key)

    # CF Access headers must be sent per-request (not on the base httpx client),
    # because the OpenAI SDK wrapper can strip default headers from a custom httpx client.
    per_request_headers: dict[str, str] = {}
    if provider.cf_client_id_env:
        val = os.environ.get(provider.cf_client_id_env, "")
        if val:
            per_request_headers["CF-Access-Client-Id"] = val
    if provider.cf_client_secret_env:
        val = os.environ.get(provider.cf_client_secret_env, "")
        if val:
            per_request_headers["CF-Access-Client-Secret"] = val

    client = OpenAI(base_url=provider.base_url, api_key=api_key or "none")
    return client, per_request_headers


def _call_once(client: OpenAI, model: str, system: str, user: str, max_tokens: int, is_openrouter: bool, cf_headers: dict[str, str] | None = None) -> str:
    extra_headers: dict[str, str] = dict(cf_headers or {})
    if is_openrouter:
        extra_headers["HTTP-Referer"] = _HTTP_REFERER
        extra_headers["X-Title"] = _X_TITLE

    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        extra_headers=extra_headers or None,
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

        is_openrouter = spec.provider == "openrouter"
        client, cf_headers = _build_client(provider_cfg)
        delay = 10.0

        for attempt in range(retries_per_spec):
            try:
                result = _call_once(client, spec.model, system, user, max_tokens, is_openrouter, cf_headers)
                logger.debug("LLM call succeeded (provider=%s, model=%s)", spec.provider, spec.model)
                return result, f"{spec.provider}/{spec.model}"

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
