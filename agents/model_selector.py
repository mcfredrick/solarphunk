from __future__ import annotations

import logging

from lib.config import BlogConfig, ModelSpec

logger = logging.getLogger(__name__)


def get_research_specs(config: BlogConfig) -> list[ModelSpec]:
    """Return the ordered model spec list for research filtering."""
    return config.models.research_filter


def get_dream_specs(config: BlogConfig) -> list[ModelSpec]:
    """Return the ordered model spec list for dream synthesis."""
    return config.models.dream_synthesis


# Legacy names kept for run.py compatibility during transition
select_research_model = get_research_specs
select_dream_model = get_dream_specs
