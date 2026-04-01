"""Main entry point for the solarphunk autonomous blog pipeline."""

import argparse
import logging
import os
import sys

from agents.dream import dream
from agents.publish import run_publish
from agents.research import research
from agents.model_selector import get_research_specs, get_dream_specs
from lib.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _check_credentials(config) -> None:
    """Warn if no LLM credentials are configured — don't hard-exit, let the call fail clearly."""
    has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY"))
    has_private = bool(
        os.environ.get("CF_CLIENT_ID") and os.environ.get("CF_CLIENT_SECRET")
    ) or any(
        p.api_key for p in config.providers.values()
    )
    if not has_openrouter and not has_private:
        print(
            "WARNING: No LLM credentials found. Set OPENROUTER_API_KEY and/or "
            "CF_CLIENT_ID + CF_CLIENT_SECRET.",
            file=sys.stderr,
        )


def cmd_research(args: argparse.Namespace) -> None:
    config = load_config()
    _check_credentials(config)
    specs = get_research_specs(config)
    logger.info("Running research agent (%d model specs)", len(specs))
    result = research(config, specs)
    print(f"Research: {result.notes_saved} notes saved, {result.items_processed} items processed, {result.feeds_fetched} feeds fetched")


def cmd_dream(args: argparse.Namespace) -> None:
    config = load_config()
    _check_credentials(config)
    specs = get_dream_specs(config)
    logger.info("Running dream agent (force=%s, %d model specs)", args.force, len(specs))
    result = dream(config, specs, force=args.force)
    if result.ran:
        print(f"Dream: draft written to {result.draft_path} ({result.notes_consumed} notes consumed)")
    else:
        print(f"Dream skipped: {result.reason}")


def cmd_publish(args: argparse.Namespace) -> None:
    config = load_config()
    result = run_publish(config)
    print(f"Publish: {result.published} published, {result.skipped} skipped")
    if result.errors:
        print(f"Validation errors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  - {err}")


def cmd_pipeline(args: argparse.Namespace) -> None:
    config = load_config()
    _check_credentials(config)

    print("=== Step 1/3: Research ===")
    research_specs = get_research_specs(config)
    research_result = research(config, research_specs)
    print(f"Research: {research_result.notes_saved} notes saved, {research_result.items_processed} items processed")

    print("=== Step 2/3: Dream ===")
    dream_specs = get_dream_specs(config)
    dream_result = dream(config, dream_specs, force=args.force_dream)
    if dream_result.ran:
        print(f"Dream: draft written to {dream_result.draft_path} ({dream_result.notes_consumed} notes consumed)")
    else:
        print(f"Dream skipped: {dream_result.reason}")

    print("=== Step 3/3: Publish ===")
    publish_result = run_publish(config)
    print(f"Publish: {publish_result.published} published, {publish_result.skipped} skipped")
    if publish_result.errors:
        for err in publish_result.errors:
            print(f"  - {err}")

    print("=== Pipeline complete ===")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="solarphunk autonomous blog pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py research
  python run.py dream --force
  python run.py publish
  python run.py pipeline
  python run.py pipeline --force-dream
""",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("research", help="Fetch RSS feeds and filter via LLM")

    dream_parser = subparsers.add_parser("dream", help="Synthesise research into a draft post")
    dream_parser.add_argument("--force", action="store_true", help="Bypass the dream gate")

    subparsers.add_parser("publish", help="Validate drafts and publish to Hugo content dir")

    pipeline_parser = subparsers.add_parser("pipeline", help="Run research → dream → publish in sequence")
    pipeline_parser.add_argument("--force-dream", action="store_true", help="Bypass the dream gate in the pipeline")

    args = parser.parse_args()
    dispatch = {"research": cmd_research, "dream": cmd_dream, "publish": cmd_publish, "pipeline": cmd_pipeline}
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
