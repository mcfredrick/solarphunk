"""Prefect flow: research → dream → publish."""

import argparse
import logging
import os
import sys

from prefect import flow, task

from agents.dream import dream
from agents.publish import run_publish
from agents.research import research
from agents.model_selector import get_dream_specs, get_research_specs
from lib.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


def _check_credentials(config) -> None:
    has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY"))
    has_private = bool(
        os.environ.get("CF_CLIENT_ID") and os.environ.get("CF_CLIENT_SECRET")
    ) or any(p.api_key for p in config.providers.values())
    if not has_openrouter and not has_private:
        print(
            "WARNING: No LLM credentials found. Set OPENROUTER_API_KEY and/or "
            "CF_CLIENT_ID + CF_CLIENT_SECRET.",
            file=sys.stderr,
        )


@task(retries=2, retry_delay_seconds=60, name="research")
def research_task(config, specs):
    result = research(config, specs)
    print(f"Research: {result.notes_saved} notes saved, {result.items_processed} items processed, {result.feeds_fetched} feeds fetched")
    return result


@task(retries=1, retry_delay_seconds=30, name="dream")
def dream_task(config, specs, force: bool = False):
    result = dream(config, specs, force=force)
    if result.ran:
        print(f"Dream: draft written to {result.draft_path} ({result.notes_consumed} notes consumed)")
    else:
        print(f"Dream skipped: {result.reason}")
    return result


@task(retries=2, retry_delay_seconds=30, name="publish")
def publish_task(config):
    result = run_publish(config)
    print(f"Publish: {result.published} published, {result.skipped} skipped")
    if result.errors:
        for err in result.errors:
            print(f"  - {err}")
    return result


@flow(name="solarphunk-pipeline", log_prints=True)
def pipeline(force_dream: bool = False):
    config = load_config()
    _check_credentials(config)
    research_specs = get_research_specs(config)
    dream_specs = get_dream_specs(config)
    research_task(config, research_specs)
    dream_task(config, dream_specs, force=force_dream)
    publish_task(config)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="solarphunk pipeline")
    parser.add_argument("--force-dream", action="store_true", help="Bypass the dream gate")
    args = parser.parse_args()
    pipeline(force_dream=args.force_dream)
