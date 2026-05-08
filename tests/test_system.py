from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from super_crawler.agents import DeepResearchAgent, DiscoveryAgent, PoolManagerAgent, ReportAgent
from super_crawler.dashboard import home_page
from super_crawler.models import RequirementStatus, TaskGroupStatus, TaskGroupType
from super_crawler.runtime import RuntimeController
from super_crawler.seed import SAMPLE_REDDIT_ITEMS
from super_crawler.storage import Storage


class SystemTests(unittest.TestCase):
    def test_end_to_end_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()

            candidates = DiscoveryAgent(storage, "discovery-test").ingest_reddit_items(SAMPLE_REDDIT_ITEMS)
            requirements = PoolManagerAgent(storage, "pool-test").reconcile_candidates()
            queued = storage.list_queue()
            run = DeepResearchAgent(storage, "research-test").run_next()
            report = ReportAgent(storage, "report-test").daily_report()

            self.assertEqual(len(candidates), 4)
            self.assertGreaterEqual(len(requirements), 2)
            self.assertTrue(queued)
            self.assertIsNotNone(run)
            self.assertTrue(run.recommendation)
            self.assertIn("Daily Requirement Discovery Report", report)
            self.assertEqual(storage.dashboard_counts()["evidence"], 4)
            self.assertTrue(
                any(
                    item.status
                    in {RequirementStatus.VALIDATED, RequirementStatus.WATCHING, RequirementStatus.REJECTED}
                    for item in storage.list_requirements()
                )
            )

    def test_dashboard_counts_include_product_stores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            counts = storage.dashboard_counts()

            self.assertEqual(
                set(counts),
                {
                    "evidence",
                    "candidates",
                    "requirements",
                    "queued",
                    "research_runs",
                    "activity_logs",
                    "pipeline_runs",
                    "task_groups",
                    "task_group_runs",
                },
            )

    def test_home_page_includes_runtime_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            controller = RuntimeController(db_path, input_dir=Path(directory) / "inbox", interval_seconds=1)

            html = home_page(storage, controller)

            self.assertIn("Agent Runtime", html)
            self.assertIn("Start", html)
            self.assertIn("Stop", html)
            self.assertIn("Search Task Groups", html)
            self.assertIn("Found Requirements Waiting To Verify", html)
            self.assertIn("Running Deep Research Agents", html)

    def test_runtime_saves_pipeline_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            DiscoveryAgent(storage, "discovery-test").ingest_reddit_items(SAMPLE_REDDIT_ITEMS)
            PoolManagerAgent(storage, "pool-test").reconcile_candidates()
            storage.close()

            controller = RuntimeController(db_path, input_dir=Path(directory) / "inbox", interval_seconds=1)
            result = controller.run_once()

            storage = Storage(db_path)
            self.assertIn("pipeline_run_id", result)
            self.assertEqual(len(storage.list_pipeline_runs()), 1)
            snapshot = storage.get_pipeline_run(result["pipeline_run_id"])
            self.assertIsNotNone(snapshot)
            self.assertTrue(snapshot["requirement_snapshot"])

    def test_task_group_runs_and_tags_requirement_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            inbox = Path(directory) / "pet_care"
            inbox.mkdir()
            (inbox / "items.json").write_text(json.dumps(SAMPLE_REDDIT_ITEMS[:2]))
            storage = Storage(db_path)
            storage.migrate()
            task_group = storage.create_task_group(
                name="Pet Care Search",
                task_type=TaskGroupType.DOMAIN,
                domain="pet care",
                input_dir=str(inbox),
                subreddits=["r/dogs", "r/AskVet"],
                keywords=["medication"],
                negative_keywords=[],
            )
            storage.update_task_group_status(task_group.task_group_id, TaskGroupStatus.RUNNING)

            controller = RuntimeController(db_path, input_dir=Path(directory) / "unused", interval_seconds=1)
            result = controller.run_once()
            requirements = Storage(db_path).list_requirements()

            self.assertEqual(result["task_group_runs"], 1)
            self.assertTrue(requirements)
            self.assertTrue(any(task_group.task_group_id in requirement.task_group_ids for requirement in requirements))


if __name__ == "__main__":
    unittest.main()
