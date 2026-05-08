from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from super_crawler.agents import DeepResearchAgent, DiscoveryAgent, PoolManagerAgent, ReportAgent
from super_crawler.models import RequirementStatus
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
                },
            )


if __name__ == "__main__":
    unittest.main()
