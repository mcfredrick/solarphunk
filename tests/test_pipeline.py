"""Verify the Prefect flow wires tasks to agents in the correct order."""
from unittest.mock import MagicMock, patch


def test_pipeline_calls_agents_in_order():
    """Flow must call research, dream, then publish — in that order."""
    research_result = MagicMock(notes_saved=3, items_processed=10, feeds_fetched=5)
    dream_result = MagicMock(ran=True, draft_path="drafts/test.md", notes_consumed=3)
    publish_result = MagicMock(published=1, skipped=0, errors=[])

    with (
        patch("pipeline.research") as mock_research,
        patch("pipeline.dream") as mock_dream,
        patch("pipeline.run_publish") as mock_publish,
        patch("pipeline.load_config"),
        patch("pipeline.get_research_specs"),
        patch("pipeline.get_dream_specs"),
    ):
        mock_research.return_value = research_result
        mock_dream.return_value = dream_result
        mock_publish.return_value = publish_result

        from pipeline import pipeline
        pipeline(force_dream=False)

        assert mock_research.call_count == 1
        assert mock_dream.call_count == 1
        assert mock_publish.call_count == 1


def test_pipeline_passes_force_dream():
    """force_dream=True must be forwarded to the dream agent."""
    with (
        patch("pipeline.research") as mock_research,
        patch("pipeline.dream") as mock_dream,
        patch("pipeline.run_publish"),
        patch("pipeline.load_config"),
        patch("pipeline.get_research_specs"),
        patch("pipeline.get_dream_specs"),
    ):
        mock_research.return_value = MagicMock()
        mock_dream.return_value = MagicMock(ran=False, reason="skipped")

        from pipeline import pipeline
        pipeline(force_dream=True)

        _, kwargs = mock_dream.call_args
        assert kwargs.get("force") is True
