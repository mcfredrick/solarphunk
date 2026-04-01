"""Main entry point for the solarphunk autonomous blog pipeline."""

import argparse
import logging
import os
import sys

from agents.dream import dream
from agents.publish import run_publish
from agents.research import research
from agents.model_selector import select_dream_model, select_research_model
from lib.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _require_api_key() -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY is not set.", file=sys.stderr)
        print("Export it before running: export OPENROUTER_API_KEY=sk-...", file=sys.stderr)
        sys.exit(1)


def cmd_research(args: argparse.Namespace) -> None:
    _require_api_key()
    config = load_config()
    model = select_research_model(config)
    logger.info("Running research agent (model=%s)", model)
    result = research(config, model)
    print(f"Research complete: {result.notes_saved} notes saved, {result.items_processed} items processed, {result.feeds_fetched} feeds fetched")


def cmd_dream(args: argparse.Namespace) -> None:
    _require_api_key()
    config = load_config()
    model = select_dream_model(config)
    logger.info("Running dream agent (model=%s, force=%s)", model, args.force)
    result = dream(config, model, force=args.force)
    if result.ran:
        print(f"Dream complete: draft written to {result.draft_path} ({result.notes_consumed} notes consumed)")
    else:
        print(f"Dream skipped: {result.reason}")


def cmd_publish(args: argparse.Namespace) -> None:
    config = load_config()
    logger.info("Running publish agent")
    result = run_publish(config)
    print(f"Publish complete: {result.published} published, {result.skipped} skipped")
    if result.errors:
        print(f"Validation errors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  - {err}")


def cmd_pipeline(args: argparse.Namespace) -> None:
    _require_api_key()
    config = load_config()

    print("=== Step 1/3: Research ===")
    research_model = select_research_model(config)
    research_result = research(config, research_model)
    print(f"Research: {research_result.notes_saved} notes saved, {research_result.items_processed} items processed")

    print("=== Step 2/3: Dream ===")
    dream_model = select_dream_model(config)
    dream_result = dream(config, dream_model, force=args.force_dream)
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
    dream_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the dream gate (time/count checks)",
    )

    subparsers.add_parser("publish", help="Validate drafts and publish to Hugo content dir")

    pipeline_parser = subparsers.add_parser(
        "pipeline", help="Run research → dream → publish in sequence"
    )
    pipeline_parser.add_argument(
        "--force-dream",
        action="store_true",
        help="Bypass the dream gate in the pipeline",
    )

    args = parser.parse_args()

    dispatch = {
        "research": cmd_research,
        "dream": cmd_dream,
        "publish": cmd_publish,
        "pipeline": cmd_pipeline,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
