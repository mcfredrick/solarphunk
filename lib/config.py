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


class ModelsConfig(BaseModel):
    research_filter: list[str]
    dream_synthesis: list[str]
    max_tokens_filter: int
    max_tokens_dream: int


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
    models: ModelsConfig
    hugo: HugoConfig
    publish: PublishConfig


def load_config(path: str = "config/blog.yaml") -> BlogConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text())
    return BlogConfig.model_validate(raw)
