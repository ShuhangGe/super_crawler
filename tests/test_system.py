from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from super_crawler.agents import DeepResearchAgent, DiscoveryAgent, PoolManagerAgent, ReportAgent
from super_crawler.collectors import normalize_reddit_item, parse_opencli_output
from super_crawler.dashboard import grouped_requirement_lineage, home_page, visible_task_groups
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
                    "experiment_logs",
                    "requirement_samples",
                    "requirement_events",
                    "app_config",
                },
            )
            config = storage.get_app_config()
            self.assertEqual(config["collector_enabled"], "0")
            self.assertEqual(config["model_search"], "deepseek-v4-flash")
            self.assertEqual(config["model_deep_research"], "deepseek-v4-flash")
            self.assertEqual(config["model_report"], "deepseek-v4-pro")

    def test_home_page_uses_group_controls_without_global_runtime_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            controller = RuntimeController(db_path, input_dir=Path(directory) / "inbox", interval_seconds=1)

            html = home_page(storage, controller)

            self.assertIn("Global Resource Allocation", html)
            self.assertIn("Create Task Group", html)
            self.assertIn("Collector And Model Settings", html)
            self.assertIn("deepseek-v4-flash", html)
            self.assertIn("General Search", html)
            self.assertIn("Domain Specific", html)
            self.assertIn("Group name", html)
            self.assertIn("What are we planning to search?", html)
            self.assertNotIn("Possible requirements", html)
            self.assertNotIn("Queued for research", html)
            self.assertNotIn("Agent Runtime", html)
            self.assertNotIn('action="/runtime"', html)

            task_group = storage.create_task_group(
                name="Sports Search",
                task_type=TaskGroupType.GENERAL,
                domain=None,
                input_dir=str(Path(directory) / "sports"),
                description="Search for sports organization workflow pain.",
            )
            html = home_page(storage, controller)

            self.assertIn("Sports Search", html)
            self.assertIn("Search for sports organization workflow pain.", html)
            self.assertIn(f'id="group-{task_group.task_group_id}"', html)
            self.assertIn('value="start"', html)
            self.assertIn('value="stop"', html)
            self.assertIn('value="delete"', html)
            self.assertIn("Details", html)
            self.assertIn("Discovery Agents", html)
            self.assertIn("Running Deep Research Agents", html)
            self.assertNotIn('value="run-once"', html)
            self.assertNotIn("Search Log", html)
            self.assertNotIn("Run Logs", html)
            self.assertNotIn(">Samples<", html)
            self.assertNotIn("Pool Manager", html)
            self.assertNotIn("Change Detection", html)
            self.assertIn("Latest: No run yet.", html)

            storage.update_task_group_status(task_group.task_group_id, TaskGroupStatus.RUNNING)
            storage.log_experiment(task_group.task_group_id, "run-1", "scheduler", "run_completed", "Loaded 0 item(s)", {})
            storage.log_experiment(task_group.task_group_id, "run-1", "collector", "collector_skipped", "Reddit OpenCLI collector is disabled", {"collector_enabled": "0"})
            storage.log_experiment(task_group.task_group_id, "run-1", "scheduler", "files_read", "Read 0 JSON file(s)", {"files": []})
            storage.log_experiment(task_group.task_group_id, "run-1", "scheduler", "input_loaded", "Loaded 0 item(s)", {"items_loaded": 0, "items_skipped": 0})
            html = home_page(storage, controller)

            self.assertIn("run-indicator", html)
            self.assertIn("pipeline-motion", html)
            self.assertIn("No input is being collected", html)
            self.assertIn("OpenCLI is disabled", html)
            self.assertIn("Latest: Loaded 0 item(s)", html)
            self.assertIn("no deep research for this group yet", html)

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
            storage = Storage(db_path)
            experiment_logs = storage.list_experiment_logs(task_group_id=task_group.task_group_id)
            samples = storage.list_requirement_samples(task_group_id=task_group.task_group_id)

            self.assertEqual(result["task_group_runs"], 1)
            self.assertTrue(requirements)
            self.assertTrue(any(task_group.task_group_id in requirement.task_group_ids for requirement in requirements))
            self.assertTrue(any(item["step_name"] == "task_group_started" for item in experiment_logs))
            self.assertTrue(any(item["step_name"] == "collector_skipped" for item in experiment_logs))
            self.assertTrue(any(item["step_name"] == "pool_requirement_sample" for item in experiment_logs))
            self.assertTrue(samples)
            self.assertTrue(samples[0]["requirement_sentence"].endswith("."))
            events = storage.list_requirement_events(samples[0]["requirement_id"])
            self.assertTrue(events)

    def test_archived_task_group_hidden_on_page_one_but_kept_for_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            inbox = Path(directory) / "pet_care"
            inbox.mkdir()
            (inbox / "items.json").write_text(json.dumps(SAMPLE_REDDIT_ITEMS[:1]))
            storage = Storage(db_path)
            storage.migrate()
            task_group = storage.create_task_group(
                name="Pet Care Search",
                task_type=TaskGroupType.DOMAIN,
                domain="pet care",
                input_dir=str(inbox),
            )
            storage.update_task_group_status(task_group.task_group_id, TaskGroupStatus.RUNNING)
            RuntimeController(db_path, input_dir=Path(directory) / "unused", interval_seconds=1).run_once()
            storage.update_task_group_status(task_group.task_group_id, TaskGroupStatus.ARCHIVED)
            requirements = storage.list_requirements()

            self.assertFalse(visible_task_groups(storage))
            html = grouped_requirement_lineage(storage, requirements, task_group.task_group_id)
            self.assertIn("Pet Care Search", html)
            self.assertIn("domain_search", html)

    def test_opencli_reddit_output_is_normalized(self) -> None:
        payload = {
            "results": [
                {
                    "id": "abc123",
                    "title": "I need a better way to manage league schedules",
                    "selftext": "The spreadsheet is painful and parents keep missing updates.",
                    "permalink": "/r/sports/comments/abc123/test/",
                    "subreddit_name_prefixed": "r/sports",
                    "score": "12",
                    "num_comments": "5",
                }
            ]
        }

        parsed = parse_opencli_output(json.dumps(payload))
        item = normalize_reddit_item(parsed[0], "sports scheduling")

        self.assertEqual(item["source"], "reddit_opencli")
        self.assertEqual(item["subreddit"], "sports")
        self.assertEqual(item["score"], 12)
        self.assertEqual(item["comment_count"], 5)
        self.assertTrue(item["source_url"].startswith("https://www.reddit.com/"))
        self.assertEqual(item["collection_query"], "sports scheduling")

        ndjson = '{"title": "Need cheaper sports registration", "subreddit": "sports"}\n{"title": "Need better roster tools"}'
        self.assertEqual(len(parse_opencli_output(ndjson)), 2)


if __name__ == "__main__":
    unittest.main()
