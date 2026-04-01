from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_MODELS_URL = "https://openrouter.ai/api/v1/models"


def get_available_models() -> set[str]:
    import os
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    try:
        response = httpx.get(
            _MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return {model["id"] for model in data.get("data", [])}
    except Exception as exc:
        logger.warning("Could not fetch available models from OpenRouter: %s", exc)
        return set()


def select_model(preference_list: list[str]) -> str:
    available = get_available_models()
    for model in preference_list:
        if model in available:
            return model
    raise RuntimeError(
        f"None of the preferred models are available. Checked: {preference_list}"
    )


def select_research_model(config) -> str:
    return select_model(config.models.research_filter)


def select_dream_model(config) -> str:
    return select_model(config.models.dream_synthesis)
