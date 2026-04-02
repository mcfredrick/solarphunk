from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class FeedConfig(BaseModel):
    name: str
    url: str
    type: str


class ResearchConfig(BaseModel):
    domains: list[str]
    feeds: list[FeedConfig]
    max_items_per_feed: int
    min_relevance_score: float
    dedup_window_days: int


class DreamConfig(BaseModel):
    min_hours_since_last_dream: float
    min_new_research_items: int
    lock_file: str
    context_posts: int


class ModelSpec(BaseModel):
    provider: str  # "openrouter" | "private"
    model: str


class EditConfig(BaseModel):
    max_iterations: int


class ModelsConfig(BaseModel):
    # Ordered preference lists: try each spec in order, skip on rate limit / error
    research_filter: list[ModelSpec]
    dream_synthesis: list[ModelSpec]
    edit_judge: list[ModelSpec]
    edit_rewriter: list[ModelSpec]
    max_tokens_filter: int
    max_tokens_dream: int
    max_tokens_edit_judge: int
    max_tokens_edit_rewriter: int


class ProviderConfig(BaseModel):
    base_url: str
    api_key: str | None = None
    api_key_env: str | None = None          # env var name for the API key
    cf_client_id_env: str | None = None     # env var name for CF-Access-Client-Id
    cf_client_secret_env: str | None = None # env var name for CF-Access-Client-Secret


class ThemeConfig(BaseModel):
    description: str
    voice: str
    audience: str
    post_length_words: str
    avoid: str
    lateral_moves: list[str]


class BlogMeta(BaseModel):
    name: str
    tagline: str
    base_url: str
    language: str
    author: str
    author_bio: str


class HugoConfig(BaseModel):
    content_dir: str


class PublishConfig(BaseModel):
    auto_publish_drafts: bool
    commit_message_template: str


class BlogConfig(BaseModel):
    blog: BlogMeta
    theme: ThemeConfig
    research: ResearchConfig
    dream: DreamConfig
    edit: EditConfig
    providers: dict[str, ProviderConfig]
    models: ModelsConfig
    hugo: HugoConfig
    publish: PublishConfig


def load_config(path: str = "config/blog.yaml") -> BlogConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text())
    return BlogConfig.model_validate(raw)
