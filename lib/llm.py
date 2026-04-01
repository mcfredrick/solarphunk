from __future__ import annotations

import logging
import os
import time

from openai import OpenAI, RateLimitError

logger = logging.getLogger(__name__)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_HTTP_REFERER = "https://mcfredrick.github.io/solarphunk"
_X_TITLE = "Solarphunk"


def get_client() -> OpenAI:
    api_key = os.environ["OPENROUTER_API_KEY"]
    return OpenAI(base_url=_OPENROUTER_BASE_URL, api_key=api_key)


def call_llm(
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    retries: int = 5,
) -> str:
    client = get_client()
    delay = 10.0  # start with 10s — free tier limits are tight

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                extra_headers={
                    "HTTP-Referer": _HTTP_REFERER,
                    "X-Title": _X_TITLE,
                },
            )
            return response.choices[0].message.content or ""

        except RateLimitError as exc:
            if attempt == retries - 1:
                logger.error("Rate limit exhausted after %d attempts (model=%s)", retries, model)
                raise

            # Respect Retry-After header if present
            retry_after = _parse_retry_after(exc)
            wait = retry_after if retry_after else delay
            logger.warning(
                "Rate limited (attempt %d/%d, model=%s). Waiting %.0fs...",
                attempt + 1, retries, model, wait,
            )
            time.sleep(wait)
            delay = min(delay * 2, 120.0)  # cap at 2 minutes

        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            is_retryable = status in (500, 502, 503, 504) or status is None

            if not is_retryable or attempt == retries - 1:
                logger.error("LLM call failed (model=%s): %s", model, exc)
                raise

            logger.warning(
                "LLM call attempt %d/%d failed (model=%s, status=%s), retrying in %.0fs",
                attempt + 1, retries, model, status, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 120.0)

    raise RuntimeError("Exhausted retries")


def _parse_retry_after(exc: Exception) -> float | None:
    """Extract Retry-After seconds from a rate limit exception, if present."""
    try:
        response = getattr(exc, "response", None)
        if response is None:
            return None
        headers = getattr(response, "headers", {})
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value:
            return float(value)
    except Exception:
        pass
    return None
