from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from super_crawler.agents import ChangeDetectionAgent, DeepResearchAgent, DiscoveryAgent, ReportAgent, RequirementMemoryAgent, normalize_llm_requirement_analysis, search_relevance_check
from super_crawler.collectors import OpenCliRedditCollector, OpenCliSourceRouter, build_requirement_search_queries, normalize_reddit_item, parse_opencli_output
from super_crawler.dashboard import agent_log_page, detail_page, experiment_log_page, filter_requirements_by_group, grouped_requirement_lineage, home_page, possible_requirements, rejected_requirements, requirement_list_page, visible_task_groups, search_agent_count_for_group, todo_page
from super_crawler.models import RequirementRecord, RequirementStatus, ResearchRun, TaskGroupStatus, TaskGroupType, utc_now
from super_crawler.runner import AlwaysOnRunner, DeviceHealth, allocate_search_slots, plan_adaptive_resources
from super_crawler.runtime import RuntimeController
from super_crawler.seed import SAMPLE_REDDIT_ITEMS
from super_crawler.search_planner import SearchPlannerAgent
from super_crawler.storage import Storage


class FakePlannerLLM:
    def __init__(self, response: dict[str, object]):
        self.response = response
        self.prompts: list[str] = []

    def available(self) -> bool:
        return True

    def json_chat(self, model: str, system: str, user: str) -> dict[str, object]:
        self.prompts.append(user)
        return self.response


class FakeDeepResearchLLM:
    def __init__(self):
        self.prompts: list[str] = []

    def available(self) -> bool:
        return True

    def json_chat(self, model: str, system: str, user: str) -> dict[str, object]:
        self.prompts.append(user)
        if "final_decision" in user:
            return {
                "final_decision": "watching",
                "confidence": 0.78,
                "why_real": "多来源证据显示用户持续抱怨订阅费，并寻找本地存储替代方案。",
                "why_noise": "当前证据仍主要来自小样本搜索，需要更多平台验证。",
                "strongest_evidence": ["用户明确表示愿意为更好的本地存储方案付费"],
                "weakest_assumptions": ["尚未验证更大规模用户群体"],
                "existing_solutions": ["本地 NAS", "带 SD 卡摄像头"],
                "market_gap": "现有方案配置复杂且比较困难。",
                "recommended_next_step": "继续搜索产品评论和竞品价格页。",
            }
        if "search_tracks" in user:
            return {
                "requirement_rewrite": "用户需要减少智能摄像头订阅成本并保留本地录像能力",
                "target_users": ["智能家居用户"],
                "hypotheses": ["用户在多个平台抱怨订阅费", "已有方案不能覆盖本地存储需求"],
                "search_tracks": [
                    {
                        "track": "product_review_scan",
                        "source": "product_reviews",
                        "queries": ["smart camera subscription fees local storage reviews complaints"],
                        "question": "产品评论中是否有订阅费痛点",
                    },
                    {
                        "track": "market_solution_scan",
                        "source": "google_web",
                        "queries": ["smart camera local storage alternative subscription"],
                        "question": "网页和竞品是否说明已有方案缺口",
                    },
                ],
            }
        return {
            "is_relevant_evidence": True,
            "evidence_type": "buying_intent",
            "analysis_summary": "该结果支持订阅费和本地存储痛点。",
            "signals": ["complaint", "buying_intent", "alternative"],
            "country_area_hints": [],
            "existing_solutions": ["local storage camera"],
            "confidence": 0.86,
        }


def planner_response(assignments: list[dict[str, object]], domain: str = "Creator camera support accessories") -> dict[str, object]:
    return {
        "search_goal": f"Find Reddit pain points for {domain}.",
        "search_brief": {
            "domain_understanding": domain,
            "target_users": ["content creators", "photographers"],
            "product_or_problem_scope": ["camera stands", "fill lights", "phone mounts"],
            "must_match": ["creator setup or camera support accessory pain"],
            "reject_if": ["unrelated software workflow or generic electronics posts"],
            "deep_research_lessons": ["Use validated directions and avoid noisy queries from prior deep research."],
            "planning_method": "llm",
        },
        "assignments": assignments,
    }


class SystemTests(unittest.TestCase):
    def test_end_to_end_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()

            candidates = DiscoveryAgent(storage, "discovery-test").ingest_reddit_items(SAMPLE_REDDIT_ITEMS)
            requirements = RequirementMemoryAgent(storage, "memory-test").reconcile_candidates()
            queued = storage.list_queue()
            run = DeepResearchAgent(storage, "research-test").run_next()
            report = ReportAgent(storage, "report-test").daily_report()

            self.assertEqual(len(candidates), 4)
            self.assertGreaterEqual(len(requirements), 2)
            self.assertTrue(queued)
            self.assertIsNotNone(run)
            self.assertTrue(run.recommendation)
            self.assertIn("每日需求发现报告", report)
            self.assertEqual(storage.dashboard_counts()["evidence"], 4)
            self.assertTrue(
                any(
                    item.status
                    in {RequirementStatus.VALIDATED, RequirementStatus.WATCHING, RequirementStatus.REJECTED}
                    for item in storage.list_requirements()
                )
            )

    def test_completed_deep_research_sets_change_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()

            DiscoveryAgent(storage, "discovery-test").ingest_reddit_items(SAMPLE_REDDIT_ITEMS)
            RequirementMemoryAgent(storage, "memory-test").reconcile_candidates()
            run = DeepResearchAgent(storage, "research-test").run_next()

            self.assertIsNotNone(run)
            requirement = storage.get_requirement(run.requirement_id)
            self.assertIsNotNone(requirement)
            self.assertEqual(requirement.previous_scores, requirement.current_scores)

            reopened = ChangeDetectionAgent(storage, "change-test").evaluate_reopenings()
            self.assertNotIn(run.requirement_id, [item.requirement_id for item in reopened])

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
                    "todo_jobs",
                    "search_plans",
                    "search_insights",
                    "app_config",
                    "task_group_config",
                },
            )
            config = storage.get_app_config()
            self.assertEqual(config["collector_enabled"], "0")
            self.assertEqual(config["model_search"], "deepseek-v4-flash")
            self.assertEqual(config["model_deep_research"], "deepseek-v4-flash")
            self.assertEqual(config["model_report"], "deepseek-v4-pro")

    def test_domain_task_group_enables_opencli_collection_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            input_dir = Path(directory) / "task_inbox" / "3c"

            task_group = storage.create_task_group(
                name="3C",
                task_type=TaskGroupType.DOMAIN,
                domain="3C products",
                input_dir=str(input_dir),
                description="Find customer needs for support brackets and fill lights.",
                enable_collector=True,
            )

            config = storage.get_task_group_config(task_group.task_group_id)
            self.assertEqual(config["collector_enabled"], "1")
            self.assertTrue(input_dir.is_dir())

    def test_home_page_uses_group_controls_without_global_runtime_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            controller = RuntimeController(db_path, input_dir=Path(directory) / "inbox", interval_seconds=1)

            html = home_page(storage, controller)

            self.assertIn("Global Resource Allocation", html)
            self.assertIn("Create Task Group", html)
            self.assertNotIn("Collector And Model Settings", html)
            self.assertIn("General Search", html)
            self.assertIn("Domain Specific", html)
            self.assertIn("Group name", html)
            self.assertIn('id="domain-description" class="domain-description" hidden', html)
            self.assertIn('id="task-description" name="description" placeholder="Domain search plan" disabled', html)
            self.assertNotIn("What are we planning to search?", html)
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
            self.assertIn("Settings", html)
            self.assertIn("settings-popout", html)
            self.assertIn("Sports Search Settings", html)
            self.assertIn("OpenCLI Collection", html)
            self.assertIn("Results per run", html)
            self.assertIn("<summary>Advanced</summary>", html)
            self.assertIn('name="model_search"', html)
            self.assertIn('name="model_deep_research"', html)
            self.assertIn("deepseek-v4-flash", html)
            self.assertIn("deepseek-v4-pro", html)
            self.assertIn("Details", html)
            self.assertIn("Search Planner", html)
            self.assertIn("Search Planner Agent", html)
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
            DiscoveryAgent(storage, "discovery-old").ingest_reddit_items([], task_group.task_group_id)
            storage.log_experiment(task_group.task_group_id, "run-1", "scheduler", "run_completed", "Loaded 0 item(s)", {})
            storage.log_experiment(task_group.task_group_id, "run-1", "collector", "collector_skipped", "Reddit OpenCLI collector is disabled", {"collector_enabled": "0"})
            storage.log_experiment(task_group.task_group_id, "run-1", "scheduler", "files_read", "Read 0 JSON file(s)", {"files": []})
            storage.log_experiment(task_group.task_group_id, "run-1", "scheduler", "input_loaded", "Loaded 0 item(s)", {"items_loaded": 0, "items_skipped": 0})
            html = home_page(storage, controller)

            self.assertIn("run-indicator", html)
            self.assertIn("pipeline-motion", html)
            self.assertIn("Search Agent 1", html)
            self.assertIn("Search Agent 2", html)
            self.assertIn("Search Agent 3", html)
            self.assertIn("agent_id=search-agent-1", html)
            self.assertIn("agent_id=search-agent-2", html)
            self.assertIn("agent_id=search-agent-3", html)
            self.assertEqual(search_agent_count_for_group(storage, task_group), 3)
            self.assertIn("No input is being collected", html)
            self.assertIn("OpenCLI is disabled", html)
            self.assertIn("Latest: Loaded 0 item(s)", html)
            self.assertIn("No active deep research agent for this group.", html)
            self.assertNotIn("ingest_reddit_items", html)
            self.assertNotIn(">completed<", html)

            detail_html = experiment_log_page(storage, task_group.task_group_id)
            agent_html = agent_log_page(storage, "discovery", "", task_group.task_group_id)
            self.assertIn("Terminal Style Log", detail_html)
            self.assertIn("terminal-log", detail_html)
            self.assertIn("collector_skipped", detail_html)
            self.assertIn("&quot;items_loaded&quot;: 0", detail_html)
            self.assertIn("Search Log", agent_html)
            self.assertIn("Raw Terminal Log", agent_html)
            self.assertIn("status=completed", agent_html)
            self.assertNotIn("Experiment Steps", agent_html)
            self.assertNotIn("Full Log Payload", agent_html)
            self.assertNotIn("Full Experiment Payload", agent_html)
            self.assertNotIn("<th>ID</th><th>Time</th><th>Role</th><th>Agent</th>", agent_html)

    def test_dashboard_distinguishes_empty_search_files_from_failed_input_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            task_group = storage.create_task_group(
                name="3C product search",
                task_type=TaskGroupType.DOMAIN,
                domain="3C products",
                input_dir=str(Path(directory) / "3c"),
                enable_collector=True,
            )
            storage.update_task_group_status(task_group.task_group_id, TaskGroupStatus.RUNNING)
            storage.log_experiment(task_group.task_group_id, "run-1", "scheduler", "files_read", "Read 0 JSON file(s)", {"files": []})
            storage.log_experiment(task_group.task_group_id, "run-1", "scheduler", "input_loaded", "Loaded 0 item(s)", {"items_loaded": 0, "items_skipped": 0})

            html = home_page(storage, RuntimeController(Path(directory) / "test.sqlite3", input_dir=Path(directory) / "unused", interval_seconds=1))

            self.assertIn("No search result files have been collected yet.", html)
            self.assertNotIn("No input loaded.", html)
            self.assertNotIn("but the latest run loaded 0 item(s)", html)

    def test_search_planner_expands_user_description_for_breadth_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            task_group = storage.create_task_group(
                name="3C product search",
                task_type=TaskGroupType.DOMAIN,
                domain="3C products",
                input_dir=str(Path(directory) / "3c"),
                description="I'm selling 3C products",
            )

            first_llm = FakePlannerLLM(
                planner_response(
                    [
                        {"strategy": "setup_pain", "query": "camera tripod unstable creator setup problem", "subreddit": "videography", "why": "Find creator setup pain."},
                        {"strategy": "lighting_mount", "query": "ring light stand broke hard to adjust", "subreddit": "contentcreation", "why": "Find lighting stand complaints."},
                        {"strategy": "phone_mount", "query": "phone mount filming overhead shots problem", "subreddit": "photography", "why": "Find phone mount setup problems."},
                    ]
                )
            )
            second_llm = FakePlannerLLM(
                planner_response(
                    [
                        {"strategy": "desk_mount", "query": "desk camera mount shaky problem", "subreddit": "videography", "why": "Try a different creator accessory angle."},
                        {"strategy": "quick_release", "query": "tripod quick release annoying camera rig", "subreddit": "photography", "why": "Find friction in camera rig changes."},
                        {"strategy": "portable_lighting", "query": "portable fill light stand too heavy creator", "subreddit": "contentcreation", "why": "Find portability pain."},
                    ]
                )
            )

            first = SearchPlannerAgent(llm_client=first_llm).plan(task_group, 3, cycle_index=1, recent_queries=[])
            second = SearchPlannerAgent(llm_client=second_llm).plan(task_group, 3, cycle_index=2, recent_queries=first["queries"])

            self.assertEqual(first["search_brief"]["planning_method"], "llm")
            self.assertIn("camera support", first["search_brief"]["domain_understanding"])
            self.assertEqual(len(first["assignments"]), 3)
            self.assertTrue(any("tripod" in query or "ring light" in query or "phone mount" in query for query in first["queries"]))
            self.assertTrue(all(assignment["subreddit"] for assignment in first["assignments"]))
            self.assertIn("deep_research_feedback", first_llm.prompts[0])
            self.assertNotEqual(first["queries"], second["queries"])

    def test_saved_search_plan_shows_on_group_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            controller = RuntimeController(Path(directory) / "test.sqlite3", input_dir=Path(directory) / "inbox", interval_seconds=1)
            task_group = storage.create_task_group(
                name="3C product search",
                task_type=TaskGroupType.DOMAIN,
                domain="3C products",
                input_dir=str(Path(directory) / "3c"),
                description="I'm selling 3C products",
            )
            llm = FakePlannerLLM(
                planner_response(
                    [
                        {"strategy": "setup_pain", "query": "camera tripod unstable creator setup problem", "subreddit": "videography", "why": "Find creator setup pain."},
                        {"strategy": "lighting_mount", "query": "ring light stand broke hard to adjust", "subreddit": "contentcreation", "why": "Find lighting stand complaints."},
                    ]
                )
            )
            plan = SearchPlannerAgent(llm_client=llm).plan(task_group, 2, cycle_index=1, recent_queries=[])
            storage.save_search_plan(
                task_group.task_group_id,
                "run-1",
                plan["planner_agent_id"],
                plan["cycle_index"],
                plan["input_description"],
                plan["search_goal"],
                plan["search_brief"],
                plan["assignments"],
            )

            html = home_page(storage, controller)

            self.assertIn("Search Planner Agent", html)
            self.assertIn("Creator camera support accessories", html)
            self.assertIn(str(plan["assignments"][0]["query"]), html)

    def test_search_planner_uses_deep_research_insights(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            task_group = storage.create_task_group(
                name="3C product search",
                task_type=TaskGroupType.DOMAIN,
                domain="3C products",
                input_dir=str(Path(directory) / "3c"),
                description="I'm selling 3C products",
            )
            storage.upsert_requirement(
                RequirementRecord(
                    requirement_id="REQ-2026-000001",
                    canonical_requirement="Users need better phone battery warranty support",
                    description="Users complain about battery warranty pain.",
                    status=RequirementStatus.WATCHING,
                    first_seen=utc_now(),
                    last_seen=utc_now(),
                    times_detected=1,
                    evidence_count=1,
                    subreddit_count=1,
                    geo_distribution=[],
                    audience_segments=[],
                    current_scores={},
                    previous_scores={},
                    research_history=[],
                    decision_history=[],
                    reopen_events=[],
                    latest_recommendation=None,
                    aliases=[],
                    evidence_ids=[],
                    task_group_ids=[task_group.task_group_id],
                    task_group_run_ids=["run-1"],
                )
            )
            storage.upsert_research_run(
                ResearchRun(
                    research_run_id="research-run-1",
                    requirement_id="REQ-2026-000001",
                    agent_id="research-agent-1",
                    started_at=utc_now(),
                    completed_at=utc_now(),
                    input_evidence_ids=[],
                    research_questions=[],
                    findings={},
                    scores={},
                    geo_analysis=[],
                    market_signal_analysis={},
                    existing_solution_analysis={},
                    recommendation="keep tracking and run lightweight validation",
                    limitations=[],
                    changed_since_last_run={},
                )
            )
            storage.save_search_insight(
                task_group.task_group_id,
                "run-1",
                "REQ-2026-000001",
                "research-run-1",
                "research-agent-1",
                "deep_research_feedback",
                {
                    "productive_queries": ["phone battery warranty pain"],
                    "noisy_queries": ["regret buying laptop phone headphones charger broke warranty"],
                    "suggested_searches": [
                        {
                            "query": "phone battery warranty pain",
                            "subreddit": "techsupport",
                            "strategy": "learned_support_gap",
                            "why": "Deep research found relevant warranty evidence.",
                        }
                    ],
                    "productive_dimensions": {
                        "subreddits": ["techsupport"],
                        "query_terms": ["phone", "battery", "warranty"],
                        "strategies": ["support_gap"],
                    },
                    "unproductive_dimensions": {
                        "subreddits": ["BuyItForLife"],
                        "query_terms": ["regret", "buying", "broke"],
                        "strategies": ["purchase_regret"],
                    },
                    "recommended_allocation_change": {
                        "increase": ["support_gap"],
                        "decrease": ["purchase_regret"],
                    },
                },
            )

            llm = FakePlannerLLM(
                planner_response(
                    [
                        {
                            "query": "phone battery warranty pain",
                            "subreddit": "techsupport",
                            "strategy": "learned_support_gap",
                            "why": "Deep research found relevant warranty evidence.",
                        },
                        {
                            "query": "phone battery replacement support denied",
                            "subreddit": "mobilerepair",
                            "strategy": "battery_repair_followup",
                            "why": "Deepen the productive battery warranty direction.",
                        },
                    ],
                    domain="Phone warranty support",
                )
            )
            plan = SearchPlannerAgent(llm_client=llm).plan(
                task_group,
                2,
                cycle_index=1,
                recent_queries=[],
                search_insights=storage.list_search_insights(task_group.task_group_id),
            )

            self.assertEqual(plan["assignments"][0]["query"], "phone battery warranty pain")
            self.assertEqual(plan["assignments"][0]["strategy"], "learned_support_gap")
            self.assertNotIn("regret buying laptop phone headphones charger broke warranty", plan["queries"])
            self.assertIn("phone battery warranty pain", llm.prompts[0])

    def test_search_planner_keeps_baseline_slot_with_structured_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            task_group = storage.create_task_group(
                name="3C product search",
                task_type=TaskGroupType.DOMAIN,
                domain="3C products",
                input_dir=str(Path(directory) / "3c"),
                description="I'm selling 3C products",
            )
            now = utc_now()
            storage.upsert_requirement(
                RequirementRecord(
                    requirement_id="REQ-2026-000002",
                    canonical_requirement="Users need better electronics warranty support",
                    description="Users complain about repair and warranty support.",
                    status=RequirementStatus.WATCHING,
                    first_seen=now,
                    last_seen=now,
                    times_detected=1,
                    evidence_count=1,
                    subreddit_count=1,
                    geo_distribution=[],
                    audience_segments=[],
                    current_scores={},
                    previous_scores={},
                    research_history=[],
                    decision_history=[],
                    reopen_events=[],
                    latest_recommendation=None,
                    aliases=[],
                    evidence_ids=[],
                    task_group_ids=[task_group.task_group_id],
                    task_group_run_ids=["run-1"],
                )
            )
            storage.upsert_research_run(
                ResearchRun(
                    research_run_id="research-run-2",
                    requirement_id="REQ-2026-000002",
                    agent_id="research-agent-1",
                    started_at=now,
                    completed_at=now,
                    input_evidence_ids=[],
                    research_questions=[],
                    findings={},
                    scores={},
                    geo_analysis=[],
                    market_signal_analysis={},
                    existing_solution_analysis={},
                    recommendation="keep tracking",
                    limitations=[],
                    changed_since_last_run={},
                )
            )
            storage.save_search_insight(
                task_group.task_group_id,
                "run-1",
                "REQ-2026-000002",
                "research-run-2",
                "research-agent-1",
                "deep_research_feedback",
                {
                    "productive_queries": ["phone battery warranty pain"],
                    "noisy_queries": ["regret buying laptop phone headphones charger broke warranty"],
                    "suggested_searches": [
                        {
                            "query": "phone battery warranty pain",
                            "subreddit": "techsupport",
                            "strategy": "learned_support_gap",
                            "why": "Deep research found relevant warranty evidence.",
                        }
                    ],
                    "productive_dimensions": {
                        "subreddits": ["techsupport"],
                        "query_terms": ["phone", "battery", "warranty", "repair"],
                        "strategies": ["support_gap"],
                    },
                    "unproductive_dimensions": {
                        "subreddits": ["BuyItForLife"],
                        "query_terms": ["regret", "buying"],
                        "strategies": ["purchase_regret"],
                    },
                    "recommended_allocation_change": {
                        "increase": ["support_gap"],
                        "decrease": ["purchase_regret"],
                    },
                },
            )

            llm = FakePlannerLLM(
                planner_response(
                    [
                        {
                            "query": "phone battery warranty pain",
                            "subreddit": "techsupport",
                            "strategy": "learned_support_gap",
                            "why": "Deep research found relevant warranty evidence.",
                        },
                        {
                            "query": "phone battery repair denied warranty",
                            "subreddit": "mobilerepair",
                            "strategy": "learned_support_gap_adjacent",
                            "why": "Broaden the productive support-gap direction.",
                        },
                        {
                            "query": "phone manufacturer warranty support battery issue",
                            "subreddit": "techsupport",
                            "strategy": "warranty_support_baseline",
                            "why": "Maintain one LLM-chosen baseline search grounded in the original requirement.",
                        },
                    ],
                    domain="Electronics warranty support",
                )
            )
            plan = SearchPlannerAgent(llm_client=llm).plan(
                task_group,
                3,
                cycle_index=1,
                recent_queries=[],
                search_insights=storage.list_search_insights(task_group.task_group_id),
            )

            strategies = [assignment["strategy"] for assignment in plan["assignments"]]
            self.assertEqual(plan["assignments"][0]["query"], "phone battery warranty pain")
            self.assertTrue(any(strategy.startswith("learned_support_gap") for strategy in strategies))
            self.assertIn("warranty_support_baseline", strategies)
            self.assertNotIn("purchase_regret", strategies)

    def test_search_planner_log_lists_each_planned_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            task_group = storage.create_task_group(
                name="3C product search",
                task_type=TaskGroupType.DOMAIN,
                domain="3C products",
                input_dir=str(Path(directory) / "3c"),
                description="I'm selling 3C products",
            )
            llm = FakePlannerLLM(
                planner_response(
                    [
                        {"strategy": "setup_pain", "query": "camera tripod unstable creator setup problem", "subreddit": "videography", "why": "Find creator setup pain."},
                        {"strategy": "lighting_mount", "query": "ring light stand broke hard to adjust", "subreddit": "contentcreation", "why": "Find lighting stand complaints."},
                        {"strategy": "phone_mount", "query": "phone mount filming overhead shots problem", "subreddit": "photography", "why": "Find phone mount setup problems."},
                    ]
                )
            )
            plan = SearchPlannerAgent(llm_client=llm).plan(task_group, 3, cycle_index=3, recent_queries=[])
            storage.log_experiment(
                task_group.task_group_id,
                "run-1",
                "search_planner",
                "search_plan_created",
                "Search planner created 3 search assignment(s)",
                {"plan_id": 1, **plan},
            )

            html = agent_log_page(storage, "search_planner", "", task_group.task_group_id)

            self.assertIn("Search Plan Cycle 3", html)
            self.assertIn("Question / Query", html)
            for assignment in plan["assignments"]:
                self.assertIn(str(assignment["agent_id"]), html)
                self.assertIn(str(assignment["query"]), html)
                self.assertIn(str(assignment["strategy"]), html)
                self.assertIn(str(assignment["why"]), html)
            self.assertIn("Raw Terminal Log", html)

    def test_search_agent_activity_logs_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            task_group = storage.create_task_group(
                name="Photo",
                task_type=TaskGroupType.DOMAIN,
                domain="photo workflow",
                input_dir=str(Path(directory) / "photo"),
            )
            now = "2026-05-11T00:00:00+00:00"
            from super_crawler.models import AgentActivityLog

            storage.log_activity(
                AgentActivityLog("search-agent-1", "discovery", "reddit_opencli_search", "completed", now, now, [task_group.task_group_id, "query 1"], ["out1.json", "https://reddit.com/one"], None, 0, 0)
            )
            storage.log_activity(
                AgentActivityLog("search-agent-2", "discovery", "reddit_opencli_search", "completed", now, now, [task_group.task_group_id, "query 2"], ["out2.json", "https://reddit.com/two"], None, 0, 0)
            )
            storage.log_experiment(
                task_group.task_group_id,
                "run-1",
                "discovery",
                "search_agent_completed",
                "search-agent-1 collected 1 item(s)",
                {
                    "agent_id": "search-agent-1",
                    "query": "query 1",
                    "items_collected": 1,
                    "output_path": "out1.json",
                    "urls": ["https://reddit.com/one"],
                    "titles": ["First URL"],
                },
            )
            storage.log_experiment(
                task_group.task_group_id,
                "run-1",
                "discovery",
                "sample_analyzed",
                "Sample analyzed: First URL",
                {
                    "agent_id": "search-agent-1",
                    "search_query": "query 1",
                    "url": "https://reddit.com/one",
                    "title": "First URL",
                    "subreddit": "photo",
                    "method": "llm",
                    "is_possible_requirement": True,
                    "signals": ["workflow_pain"],
                    "sample_analysis": "The post asks for a better workflow.",
                    "requirement_title": "Users need a better photo workflow.",
                    "confidence": 0.8,
                },
            )

            agent_1 = agent_log_page(storage, "discovery", "search-agent-1", task_group.task_group_id)
            agent_2 = agent_log_page(storage, "discovery", "search-agent-2", task_group.task_group_id)

            self.assertIn("search-agent-1", agent_1)
            self.assertIn("query 1", agent_1)
            self.assertIn("https://reddit.com/one", agent_1)
            self.assertIn("The post asks for a better workflow.", agent_1)
            self.assertNotIn("query 2", agent_1)
            self.assertIn("search-agent-2", agent_2)

            storage.log_experiment(
                task_group.task_group_id,
                "run-2",
                "discovery",
                "search_agent_failed",
                "search-agent-1 failed query: broken query",
                {
                    "agent_id": "search-agent-1",
                    "query": "broken query",
                    "status": "failed",
                    "error": "Pre-navigation to https://reddit.com failed: No SW",
                    "items_collected": 0,
                    "urls": [],
                    "titles": [],
                },
            )
            failed_agent = agent_log_page(storage, "discovery", "search-agent-1", task_group.task_group_id)
            self.assertIn("broken query", failed_agent)
            self.assertIn("No SW", failed_agent)

    def test_queued_requirements_show_deep_research_agent_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            controller = RuntimeController(db_path, input_dir=Path(directory) / "inbox", interval_seconds=1)
            task_group = storage.create_task_group(
                name="Photo",
                task_type=TaskGroupType.DOMAIN,
                domain="photo workflow",
                input_dir=str(Path(directory) / "photo"),
            )
            candidates = DiscoveryAgent(storage, "discovery-test").ingest_reddit_items(
                [
                    {
                        "source_url": "https://reddit.com/photo-workflow",
                        "subreddit": "photography",
                        "title": "Is there an app for delivering client photo galleries?",
                        "body": "I am tired of using a spreadsheet and manual links for client photo delivery. I would pay for this.",
                        "score": 25,
                        "comment_count": 10,
                    }
                ],
                task_group.task_group_id,
                "run-1",
            )
            self.assertEqual(len(candidates), 1)
            changed = RequirementMemoryAgent(storage, "memory-test").reconcile_candidates()
            self.assertEqual(changed[0].status, RequirementStatus.QUEUED_FOR_RESEARCH)
            storage.update_task_group_status(task_group.task_group_id, TaskGroupStatus.RUNNING)

            html = home_page(storage, controller)

            self.assertIn("Possible Requirements Waiting For Deep Research", html)
            self.assertIn("client photo galleries", html)
            self.assertIn("Deep Research Agent 1", html)
            self.assertIn(">queued<", html)
            self.assertIn("Assigned to a deep research slot", html)

            queued_log_html = agent_log_page(storage, "deep_research", "", changed[0].requirement_id)
            self.assertIn("Deep Research Log", queued_log_html)
            self.assertIn("Waiting For Deep Research", queued_log_html)
            self.assertIn("Queued for Deep Research", queued_log_html)
            self.assertIn("Waiting for an available deep research agent slot.", queued_log_html)

            run = DeepResearchAgent(storage, "research-agent-1").run_next()
            self.assertIsNotNone(run)
            possible_html = grouped_requirement_lineage(storage, storage.list_requirements())
            deep_log_html = agent_log_page(storage, "deep_research", "", changed[0].requirement_id)

            self.assertIn("Deep Research (", possible_html)
            self.assertNotIn("Deep Research (0)", possible_html)
            self.assertIn("Deep Research Log", deep_log_html)
            self.assertIn("Deep Research Output", deep_log_html)
            self.assertIn("Is real requirement", deep_log_html)
            self.assertIn("deep_research_output", deep_log_html)
            self.assertIn("is_real_requirement", deep_log_html)

    def test_deep_research_actively_searches_and_logs_evidence(self) -> None:
        class FakeCollector:
            def __init__(self, command: str = "opencli reddit search", timeout_seconds: int = 120):
                self.command = command
                self.timeout_seconds = timeout_seconds

            def search(self, query: str, limit: int = 5, subreddit: str = "", sort: str = "", time: str = "") -> dict[str, object]:
                return {
                    "command": ["opencli", "reddit", "search", query],
                    "stderr": "",
                    "items": [
                        {
                            "source": "reddit_opencli",
                            "source_url": f"https://reddit.com/deep/{query.replace(' ', '-')}",
                            "subreddit": subreddit or "smarthome",
                            "post_id": query[:12],
                            "comment_id": None,
                            "title": "Is there an app or workaround for smart camera subscription fees?",
                            "body": "I am tired of paying subscription fees and wish there was a local storage alternative. I would pay for a better way.",
                            "author_metadata_allowed": False,
                            "score": 18,
                            "comment_count": 7,
                            "created_at": utc_now(),
                            "language": "en",
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            task_group = storage.create_task_group(
                name="3C",
                task_type=TaskGroupType.DOMAIN,
                domain="smart home devices",
                input_dir=str(Path(directory) / "3c"),
            )
            storage.update_task_group_config(task_group.task_group_id, {"collector_enabled": "1"})
            candidates = DiscoveryAgent(storage, "discovery-test").ingest_reddit_items(
                [
                    {
                        "source_url": "https://reddit.com/original",
                        "subreddit": "smarthome",
                        "title": "I need smart cameras without subscriptions",
                        "body": "I am tired of subscription fees and manual workarounds.",
                        "score": 10,
                        "comment_count": 5,
                    }
                ],
                task_group.task_group_id,
                "run-1",
            )
            self.assertEqual(len(candidates), 1)
            changed = RequirementMemoryAgent(storage, "memory-test").reconcile_candidates()
            storage.update_requirement_status(changed[0].requirement_id, RequirementStatus.QUEUED_FOR_RESEARCH, "test active deep research")
            storage.enqueue_research(changed[0].requirement_id, 50, "test active deep research", 1, None)

            run = DeepResearchAgent(storage, "research-agent-1", collector_factory=FakeCollector).run_next()

            self.assertIsNotNone(run)
            requirement = storage.get_requirement(changed[0].requirement_id)
            self.assertIsNotNone(requirement)
            self.assertGreater(requirement.evidence_count, 1)
            logs = storage.list_experiment_logs(task_group_id=task_group.task_group_id, agent_role="deep_research", limit=50)
            step_names = {item["step_name"] for item in logs}
            self.assertIn("deep_research_plan_created", step_names)
            self.assertIn("deep_research_search_completed", step_names)
            self.assertIn("deep_research_item_analyzed", step_names)
            self.assertIn("deep_research_evidence_collected", step_names)
            insights = storage.list_search_insights(task_group.task_group_id, limit=1)
            self.assertTrue(insights)
            feedback = insights[0]["payload_json"]
            self.assertIn("productive_dimensions", feedback)
            self.assertIn("recommended_allocation_change", feedback)
            self.assertIn("repeat_pain_validation", feedback["productive_dimensions"]["strategies"])
            self.assertIn("repeat_pain_validation", feedback["recommended_allocation_change"]["increase"])
            deep_log_html = agent_log_page(storage, "deep_research", "", changed[0].requirement_id)
            self.assertIn("Deep Research Plan", deep_log_html)
            self.assertIn("Evidence Item Analysis", deep_log_html)
            self.assertIn("Evidence Collected", deep_log_html)

    def test_deep_research_uses_llm_cross_source_plan_and_synthesis(self) -> None:
        class FakeCollector:
            def __init__(self, command: str = "opencli reddit search", timeout_seconds: int = 120):
                self.command = command
                self.timeout_seconds = timeout_seconds
                self.queries: list[str] = []

            def search(self, query: str, limit: int = 5, subreddit: str = "", sort: str = "", time: str = "") -> dict[str, object]:
                self.queries.append(query)
                return {
                    "command": ["opencli", "search", query],
                    "stderr": "",
                    "items": [
                        {
                            "source": "web_search",
                            "source_url": f"https://example.com/{len(self.queries)}",
                            "subreddit": subreddit or "web",
                            "post_id": None,
                            "comment_id": None,
                            "title": "Smart camera users complain about subscription fees and local storage",
                            "body": "Reviews mention subscription fatigue, local storage workarounds, and willingness to pay once for a better alternative.",
                            "author_metadata_allowed": False,
                            "score": 20,
                            "comment_count": 8,
                            "created_at": utc_now(),
                            "language": "en",
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            task_group = storage.create_task_group(
                name="3C",
                task_type=TaskGroupType.DOMAIN,
                domain="smart home devices",
                input_dir=str(Path(directory) / "3c"),
            )
            storage.update_task_group_config(task_group.task_group_id, {"collector_enabled": "1"})
            now = utc_now()
            storage.upsert_requirement(
                RequirementRecord(
                    requirement_id="req-camera",
                    canonical_requirement="Users need smart cameras without subscriptions",
                    description="Users complain about subscription fees and want local storage.",
                    status=RequirementStatus.QUEUED_FOR_RESEARCH,
                    first_seen=now,
                    last_seen=now,
                    times_detected=1,
                    evidence_count=0,
                    subreddit_count=0,
                    geo_distribution=[],
                    audience_segments=["smart home users"],
                    current_scores={},
                    previous_scores={},
                    research_history=[],
                    decision_history=[],
                    reopen_events=[],
                    latest_recommendation=None,
                    evidence_ids=[],
                    task_group_ids=[task_group.task_group_id],
                    task_group_run_ids=["run-camera"],
                )
            )
            storage.enqueue_research("req-camera", 50, "test llm deep research", 0, None)

            run = DeepResearchAgent(
                storage,
                "research-agent-1",
                collector_factory=FakeCollector,
                llm_client=FakeDeepResearchLLM(),
            ).run_next()

            self.assertIsNotNone(run)
            self.assertEqual(storage.get_requirement("req-camera").status, RequirementStatus.WATCHING)
            self.assertEqual(run.findings["llm_synthesis"]["decision_source"], "llm")
            self.assertIn("现有方案配置复杂", run.findings["market_gap"])
            logs = storage.list_experiment_logs(task_group_id=task_group.task_group_id, agent_role="deep_research", limit=50)
            plan_logs = [item for item in logs if item["step_name"] == "deep_research_plan_created"]
            self.assertTrue(plan_logs)
            plan = plan_logs[0]["payload_json"]["research_plan"]
            self.assertEqual(plan["planning_method"], "llm")
            self.assertIn("product_reviews", {item["source"] for item in plan["search_tracks"]})

    def test_rejected_deep_research_has_rejected_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            task_group = storage.create_task_group(
                name="Weak",
                task_type=TaskGroupType.DOMAIN,
                domain="weak signal",
                input_dir=str(Path(directory) / "weak"),
            )
            storage.update_task_group_config(task_group.task_group_id, {"collector_enabled": "0"})
            now = utc_now()
            storage.upsert_requirement(
                RequirementRecord(
                    requirement_id="req-weak",
                    canonical_requirement="Users need a better vague thing",
                    description="Thin evidence with no pain or buying signal.",
                    status=RequirementStatus.QUEUED_FOR_RESEARCH,
                    first_seen=now,
                    last_seen=now,
                    times_detected=1,
                    evidence_count=0,
                    subreddit_count=0,
                    geo_distribution=[],
                    audience_segments=[],
                    current_scores={},
                    previous_scores={},
                    research_history=[],
                    decision_history=[],
                    reopen_events=[],
                    latest_recommendation=None,
                    evidence_ids=[],
                    task_group_ids=[task_group.task_group_id],
                    task_group_run_ids=["run-weak"],
                )
            )
            storage.enqueue_research("req-weak", 1, "test weak signal", 0, None)

            run = DeepResearchAgent(storage, "research-agent-1").run_next()
            requirement = storage.get_requirement("req-weak")

            self.assertIsNotNone(run)
            self.assertIsNotNone(requirement)
            self.assertEqual(requirement.status, RequirementStatus.REJECTED)
            self.assertIn("拒绝原因", run.recommendation)
            self.assertNotEqual(run.recommendation, "在采取行动前继续观察更多证据")
            self.assertIn("rejection_summary", run.findings)
            self.assertIn("拒绝原因", run.findings["rejection_summary"])
            rejected_html = grouped_requirement_lineage(storage, rejected_requirements(storage), task_group.task_group_id)
            detail_html = detail_page(storage, "req-weak")
            self.assertIn("Reason:", rejected_html)
            self.assertIn("Rejected because", rejected_html)
            self.assertIn("Rejected reason:", detail_html)

    def test_rejected_deep_research_does_not_create_followup_search_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            now = utc_now()
            requirement = RequirementRecord(
                requirement_id="req-rejected-insight",
                canonical_requirement="Users need a better vague thing",
                description="Thin evidence.",
                status=RequirementStatus.QUEUED_FOR_RESEARCH,
                first_seen=now,
                last_seen=now,
                times_detected=1,
                evidence_count=0,
                subreddit_count=0,
                geo_distribution=[],
                audience_segments=[],
                current_scores={},
                previous_scores={},
                research_history=[],
                decision_history=[],
                reopen_events=[],
                latest_recommendation=None,
                evidence_ids=[],
                task_group_ids=[],
                task_group_run_ids=[],
            )
            storage.upsert_requirement(requirement)
            storage.enqueue_research(requirement.requirement_id, 1, "test weak signal", 0, None)

            run = DeepResearchAgent(storage, "research-agent-1").run_next()
            insights = storage.list_search_insights(limit=1)

            self.assertIsNotNone(run)
            self.assertEqual(storage.get_requirement(requirement.requirement_id).status, RequirementStatus.REJECTED)
            self.assertEqual(insights[0]["payload_json"]["suggested_searches"], [])

    def test_requirement_memory_preserves_finalized_requirement_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            task_group = storage.create_task_group(
                name="Photo",
                task_type=TaskGroupType.DOMAIN,
                domain="photo workflow",
                input_dir=str(Path(directory) / "photo"),
            )
            first = DiscoveryAgent(storage, "discovery-test").ingest_reddit_items(
                [
                    {
                        "source_url": "https://reddit.com/photo-one",
                        "subreddit": "photography",
                        "title": "Is there an app for client photo gallery delivery?",
                        "body": "I am tired of manual spreadsheets for client gallery delivery.",
                        "score": 12,
                        "comment_count": 4,
                    }
                ],
                task_group.task_group_id,
                "run-1",
            )
            self.assertEqual(len(first), 1)
            requirement = RequirementMemoryAgent(storage, "memory-test").reconcile_candidates()[0]
            storage.update_requirement_status(requirement.requirement_id, RequirementStatus.WATCHING, "already researched")
            storage.dequeue_research(requirement.requirement_id)

            second = DiscoveryAgent(storage, "discovery-test").ingest_reddit_items(
                [
                    {
                        "source_url": "https://reddit.com/photo-two",
                        "subreddit": "photography",
                        "title": "Need an app for client photo gallery delivery",
                        "body": "Manual spreadsheet delivery is annoying for client galleries.",
                        "score": 8,
                        "comment_count": 3,
                    }
                ],
                task_group.task_group_id,
                "run-2",
            )
            self.assertEqual(len(second), 1)
            changed = RequirementMemoryAgent(storage, "memory-test").reconcile_candidates()
            updated = storage.get_requirement(requirement.requirement_id)

            self.assertEqual(updated.status, RequirementStatus.WATCHING)
            self.assertEqual(storage.list_queue(), [])
            self.assertEqual(changed[0].requirement_id, requirement.requirement_id)

    def test_deep_research_failure_unlocks_queue_item(self) -> None:
        class FailingStorage(Storage):
            def save_search_insight(self, *args, **kwargs):
                raise RuntimeError("search insight write failed")

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = FailingStorage(db_path)
            storage.migrate()
            now = utc_now()
            storage.upsert_requirement(
                RequirementRecord(
                    requirement_id="req-fail",
                    canonical_requirement="Users need a better vague thing",
                    description="Thin evidence.",
                    status=RequirementStatus.QUEUED_FOR_RESEARCH,
                    first_seen=now,
                    last_seen=now,
                    times_detected=1,
                    evidence_count=0,
                    subreddit_count=0,
                    geo_distribution=[],
                    audience_segments=[],
                    current_scores={},
                    previous_scores={},
                    research_history=[],
                    decision_history=[],
                    reopen_events=[],
                    latest_recommendation=None,
                    evidence_ids=[],
                    task_group_ids=[],
                    task_group_run_ids=[],
                )
            )
            storage.enqueue_research("req-fail", 1, "test failure", 0, None)

            run = DeepResearchAgent(storage, "research-agent-1").run_next()
            row = storage.list_queue()[0]
            requirement = storage.get_requirement("req-fail")

            self.assertIsNone(run)
            self.assertIsNone(row["locked_by"])
            self.assertEqual(requirement.status, RequirementStatus.QUEUED_FOR_RESEARCH)
            self.assertIn("deep_research_failed", [event["event_type"] for event in storage.list_requirement_events("req-fail")])

    def test_possible_requirement_can_move_to_todo_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            task_group = storage.create_task_group(
                name="Pets",
                task_type=TaskGroupType.DOMAIN,
                domain="pet medication",
                input_dir=str(Path(directory) / "pets"),
            )
            candidates = DiscoveryAgent(storage, "discovery-test").ingest_reddit_items(
                SAMPLE_REDDIT_ITEMS[:1],
                task_group.task_group_id,
                "run-1",
            )
            self.assertEqual(len(candidates), 1)
            requirements = RequirementMemoryAgent(storage, "memory-test").reconcile_candidates()
            requirement = requirements[0]

            possible_html = grouped_requirement_lineage(storage, storage.list_requirements())
            self.assertIn("Move to todo list", possible_html)

            storage.add_todo_job(requirement.requirement_id, "Prepare follow-up validation")
            todo_html = todo_page(storage)
            possible_html = grouped_requirement_lineage(storage, storage.list_requirements())

            self.assertIn("Todo Jobs", todo_html)
            self.assertIn(requirement.canonical_requirement, todo_html)
            self.assertIn("Prepare follow-up validation", todo_html)
            self.assertIn("Todo: open", possible_html)

    def test_runtime_saves_lightweight_pipeline_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            DiscoveryAgent(storage, "discovery-test").ingest_reddit_items(SAMPLE_REDDIT_ITEMS)
            RequirementMemoryAgent(storage, "memory-test").reconcile_candidates()
            storage.close()

            controller = RuntimeController(db_path, input_dir=Path(directory) / "inbox", interval_seconds=1)
            result = controller.run_once()

            storage = Storage(db_path)
            self.assertIn("pipeline_run_id", result)
            self.assertEqual(len(storage.list_pipeline_runs()), 1)
            record = storage.get_pipeline_run(result["pipeline_run_id"])
            self.assertIsNotNone(record)
            self.assertEqual(record["requirement_snapshot"], [])
            self.assertEqual(record["queue_snapshot"], [])
            self.assertEqual(record["agent_log_snapshot"], [])

    def test_empty_worker_cycle_does_not_write_pipeline_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            controller = RuntimeController(db_path, input_dir=Path(directory) / "inbox", interval_seconds=1)
            result = controller._should_persist_worker_result(
                {
                    "items_loaded": 0,
                    "candidates": 0,
                    "requirements_changed": 0,
                    "reopened": 0,
                    "research_run": None,
                    "research_runs": "",
                }
            )
            self.assertFalse(result)

    def test_task_group_run_only_reconciles_current_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            stale_inbox = Path(directory) / "stale"
            current_inbox = Path(directory) / "current"
            stale_inbox.mkdir()
            current_inbox.mkdir()
            (stale_inbox / "items.json").write_text(json.dumps(SAMPLE_REDDIT_ITEMS[:2]))
            (current_inbox / "items.json").write_text(json.dumps(SAMPLE_REDDIT_ITEMS[2:3]))
            storage = Storage(db_path)
            storage.migrate()
            stale_group = storage.create_task_group("Stale", TaskGroupType.DOMAIN, "pets", str(stale_inbox))
            current_group = storage.create_task_group("Current", TaskGroupType.DOMAIN, "lunchbox", str(current_inbox))
            stale_candidates = DiscoveryAgent(storage, "discovery-stale").ingest_reddit_items(
                SAMPLE_REDDIT_ITEMS[:2],
                stale_group.task_group_id,
                "stale-run",
            )
            self.assertEqual(len(stale_candidates), 2)
            storage.close()

            runner = AlwaysOnRunner(Storage(db_path), input_dir=Path(directory) / "unused", interval_seconds=1)
            result = runner.run_task_group(current_group.task_group_id)
            storage = Storage(db_path)

            self.assertEqual(result["requirements_changed"], result["candidates"])
            self.assertEqual(
                len(storage.list_candidates([RequirementStatus.NEW_CANDIDATE.value])),
                2,
            )

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

    def test_task_group_opencli_run_ignores_stale_inbox_files(self) -> None:
        class FakeCollector:
            def __init__(self, command: str = "", timeout_seconds: int = 120):
                pass

            def collect_queries_to_inbox(self, task_group, run_id, queries, limit_per_query=10, event_callback=None):
                output_dir = Path(task_group.input_dir)
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / f"opencli_{run_id}_agent_1.json"
                output_path.write_text(
                    json.dumps(
                        [
                            {
                                "source": "reddit_opencli",
                                "source_url": "https://reddit.com/current",
                                "subreddit": "HeadphoneAdvice",
                                "title": "Need headphones that do not hurt after a full work day",
                                "body": "Noise cancelling headphones make my ears sore.",
                                "score": 12,
                                "comment_count": 4,
                                "collection_query": "headphones uncomfortable problem",
                                "search_query": "headphones uncomfortable problem",
                                "search_subreddit": "HeadphoneAdvice",
                                "search_strategy": "headphone_problem",
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                return {
                    "queries": ["headphones uncomfortable problem"],
                    "items_collected": 1,
                    "limit_per_query": limit_per_query,
                    "search_agents": [
                        {
                            "agent_id": "search-agent-1",
                            "query": "headphones uncomfortable problem",
                            "subreddit": "HeadphoneAdvice",
                            "strategy": "headphone_problem",
                            "output_path": str(output_path),
                            "urls": ["https://reddit.com/current"],
                            "titles": ["Need headphones that do not hurt after a full work day"],
                            "subreddits": ["HeadphoneAdvice"],
                            "command": ["opencli"],
                            "stderr": "",
                            "started_at": utc_now(),
                            "completed_at": utc_now(),
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            inbox = Path(directory) / "3c"
            inbox.mkdir()
            (inbox / "old_run.json").write_text(
                json.dumps(
                    [
                        {
                            "source": "reddit_opencli",
                            "source_url": "https://reddit.com/stale",
                            "subreddit": "BestofRedditorUpdates",
                            "title": "Old stale story that should not be loaded",
                            "body": "",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            storage = Storage(db_path)
            storage.migrate()
            task_group = storage.create_task_group(
                name="3C",
                task_type=TaskGroupType.DOMAIN,
                domain="3C products",
                input_dir=str(inbox),
                description="I want to find people's requirement about 3c products",
            )
            storage.update_task_group_status(task_group.task_group_id, TaskGroupStatus.RUNNING)

            with patch("super_crawler.runner.OpenCliRedditCollector", FakeCollector):
                result = RuntimeController(db_path, input_dir=Path(directory) / "unused", interval_seconds=1).run_once()

            storage = Storage(db_path)
            logs = storage.list_experiment_logs(task_group_id=task_group.task_group_id)
            input_loaded = next(item for item in logs if item["step_name"] == "input_loaded")

            self.assertEqual(result["items_loaded"], 1)
            self.assertEqual(input_loaded["payload_json"]["items_loaded"], 1)
            self.assertNotIn("old_run.json", json.dumps(input_loaded["payload_json"]))

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

    def test_rejected_page_hides_ungrouped_legacy_data_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            DiscoveryAgent(storage, "discovery-legacy").ingest_reddit_items(SAMPLE_REDDIT_ITEMS[:1])
            requirements = RequirementMemoryAgent(storage, "memory-legacy").reconcile_candidates()
            storage.update_requirement_status(requirements[0].requirement_id, RequirementStatus.REJECTED, "legacy demo data")
            now = utc_now()
            storage.upsert_research_run(
                ResearchRun(
                    research_run_id="run-legacy-rejected",
                    requirement_id=requirements[0].requirement_id,
                    agent_id="research-test",
                    started_at=now,
                    completed_at=now,
                    input_evidence_ids=[],
                    research_questions=[],
                    findings={},
                    scores={},
                    geo_analysis=[],
                    market_signal_analysis={},
                    existing_solution_analysis={},
                    recommendation="rejected",
                    limitations=[],
                    changed_since_last_run={},
                )
            )

            default_html = grouped_requirement_lineage(storage, rejected_requirements(storage))
            legacy_html = grouped_requirement_lineage(storage, rejected_requirements(storage), "__ungrouped__")

            self.assertIn("No requirements in this page yet.", default_html)
            self.assertNotIn("pet medication", default_html.lower())
            self.assertIn("Ungrouped / Legacy", legacy_html)
            self.assertIn("medication", legacy_html.lower())

    def test_requirement_page_group_filter_limits_visible_group_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            pets = storage.create_task_group(
                name="Pets",
                task_type=TaskGroupType.DOMAIN,
                domain="pet care",
                input_dir=str(Path(directory) / "pets"),
            )
            sports = storage.create_task_group(
                name="Sports",
                task_type=TaskGroupType.DOMAIN,
                domain="sports",
                input_dir=str(Path(directory) / "sports"),
            )
            now = utc_now()
            for requirement_id, title, task_group_id, status, research_history in [
                ("req-pets", "Pet owners need medication reminders", pets.task_group_id, RequirementStatus.WATCHING, ["run-pets"]),
                ("req-sports", "Coaches need player availability tools", sports.task_group_id, RequirementStatus.WATCHING, ["run-sports"]),
                ("req-queued", "Queued items should stay off page two", pets.task_group_id, RequirementStatus.QUEUED_FOR_RESEARCH, []),
            ]:
                storage.upsert_requirement(
                    RequirementRecord(
                        requirement_id=requirement_id,
                        canonical_requirement=title,
                        description=title,
                        status=status,
                        first_seen=now,
                        last_seen=now,
                        times_detected=1,
                        evidence_count=1,
                        subreddit_count=1,
                        geo_distribution=[],
                        audience_segments=[],
                        current_scores={},
                        previous_scores={},
                        research_history=research_history,
                        decision_history=[],
                        reopen_events=[],
                        latest_recommendation=None,
                        evidence_ids=[],
                        task_group_ids=[task_group_id],
                        task_group_run_ids=[],
                    )
                )
            for run_id, requirement_id in [("run-pets", "req-pets"), ("run-sports", "req-sports")]:
                storage.upsert_research_run(
                    ResearchRun(
                        research_run_id=run_id,
                        requirement_id=requirement_id,
                        agent_id="research-test",
                        started_at=now,
                        completed_at=now,
                        input_evidence_ids=[],
                        research_questions=[],
                        findings={},
                        scores={},
                        geo_analysis=[],
                        market_signal_analysis={},
                        existing_solution_analysis={},
                        recommendation="accepted",
                        limitations=[],
                        changed_since_last_run={},
                    )
                )

            html = requirement_list_page(
                storage,
                "Possible Requirements",
                filter_requirements_by_group(possible_requirements(storage), pets.task_group_id),
                pets.task_group_id,
                "/possible",
            )

            self.assertIn("method='get'", html)
            self.assertIn("onchange='this.form.submit()'", html)
            self.assertIn("Pets", html)
            self.assertIn("Pet owners need medication reminders", html)
            self.assertNotIn("<h2>Sports</h2>", html)
            self.assertNotIn("Coaches need player availability tools", html)
            self.assertNotIn("Queued items should stay off page two", html)

    def test_group_summary_shows_generated_and_accepted_counts_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            controller = RuntimeController(db_path, input_dir=Path(directory) / "inbox", interval_seconds=1)
            task_group = storage.create_task_group(
                name="3C",
                task_type=TaskGroupType.DOMAIN,
                domain="3C products",
                input_dir=str(Path(directory) / "3c"),
            )
            now = utc_now()
            rows = [
                ("req-generated", RequirementStatus.NEEDS_MORE_EVIDENCE, []),
                ("req-queued", RequirementStatus.QUEUED_FOR_RESEARCH, []),
                ("req-accepted", RequirementStatus.WATCHING, ["run-accepted"]),
                ("req-rejected", RequirementStatus.REJECTED, ["run-rejected"]),
            ]
            for requirement_id, status, research_history in rows:
                storage.upsert_requirement(
                    RequirementRecord(
                        requirement_id=requirement_id,
                        canonical_requirement=requirement_id,
                        description=requirement_id,
                        status=status,
                        first_seen=now,
                        last_seen=now,
                        times_detected=1,
                        evidence_count=1,
                        subreddit_count=1,
                        geo_distribution=[],
                        audience_segments=[],
                        current_scores={},
                        previous_scores={},
                        research_history=research_history,
                        decision_history=[],
                        reopen_events=[],
                        latest_recommendation=None,
                        evidence_ids=[],
                        task_group_ids=[task_group.task_group_id],
                        task_group_run_ids=[],
                    )
                )
            for run_id, requirement_id, recommendation in [
                ("run-accepted", "req-accepted", "accepted"),
                ("run-rejected", "req-rejected", "rejected"),
            ]:
                storage.upsert_research_run(
                    ResearchRun(
                        research_run_id=run_id,
                        requirement_id=requirement_id,
                        agent_id="research-test",
                        started_at=now,
                        completed_at=now,
                        input_evidence_ids=[],
                        research_questions=[],
                        findings={},
                        scores={},
                        geo_analysis=[],
                        market_signal_analysis={},
                        existing_solution_analysis={},
                        recommendation=recommendation,
                        limitations=[],
                        changed_since_last_run={},
                    )
                )
            storage.enqueue_research("req-queued", 50, "test", 1, None)

            html = home_page(storage, controller)

            self.assertIn("Generated", html)
            self.assertIn("Accepted", html)
            self.assertIn("Rejected", html)
            self.assertIn("<div class='group-record-value'>3</div>", html)
            self.assertIn("<div class='group-record-value'>1</div>", html)

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

    def test_opencli_missing_binary_error_is_actionable(self) -> None:
        collector = OpenCliRedditCollector(command="definitely_missing_opencli_binary reddit search")

        with self.assertRaisesRegex(RuntimeError, "npm install -g @jackwener/opencli"):
            collector.search("sports scheduling", limit=1)

    def test_opencli_source_router_dispatches_and_normalizes_sources(self) -> None:
        calls = []

        def fake_run(command, timeout_seconds):
            calls.append(command)

            class Completed:
                stderr = ""

                def __init__(self, stdout):
                    self.stdout = stdout

            if command[:3] == ["opencli", "youtube", "search"]:
                return Completed(json.dumps([{"title": "No subscription camera review", "channel": "Camera Lab", "views": "100 views", "url": "https://youtube.com/watch?v=1"}]))
            if command[:3] == ["opencli", "google", "search"]:
                return Completed(json.dumps([{"title": "No subscription camera guide", "snippet": "Local storage options", "url": "https://example.com/guide"}]))
            if command[:3] == ["opencli", "amazon", "search"]:
                return Completed(json.dumps([{"asin": "B000TEST", "title": "Local storage camera", "price_text": "$39", "rating_value": "4.2", "review_count": "120"}]))
            return Completed(json.dumps([{"title": "Reddit camera question", "subreddit": "homesecurity", "url": "https://reddit.com/r/homesecurity/test"}]))

        with patch("super_crawler.collectors.run_opencli_command", fake_run):
            router = OpenCliSourceRouter()
            youtube = router.search("camera review", source="youtube", limit=1)
            google = router.search("camera guide", source="google_web", limit=1)
            amazon = router.search("camera product", source="product_reviews", limit=1)
            reddit = router.search("camera reddit", source="reddit", limit=1)

        self.assertEqual(youtube["items"][0]["source"], "youtube_opencli")
        self.assertEqual(google["items"][0]["source"], "google_web_opencli")
        self.assertEqual(amazon["items"][0]["source"], "amazon_opencli")
        self.assertEqual(reddit["items"][0]["source"], "reddit_opencli")
        self.assertIn(["opencli", "youtube", "search", "camera review", "--limit", "1", "-f", "json"], calls)
        self.assertIn(["opencli", "google", "search", "camera guide", "--limit", "1", "-f", "json"], calls)
        self.assertIn(["opencli", "amazon", "search", "camera product", "--limit", "1", "-f", "json"], calls)

    def test_search_slots_and_query_variants_support_multiple_agents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            task_group = storage.create_task_group(
                name="Photography",
                task_type=TaskGroupType.DOMAIN,
                domain="photography",
                input_dir=str(Path(directory) / "photo"),
                description="photography workflow pain",
            )

            self.assertEqual(allocate_search_slots(1, 3), [3])
            self.assertEqual(allocate_search_slots(2, 3), [2, 1])
            queries = build_requirement_search_queries(task_group, 3)
            self.assertEqual(len(queries), 3)
            self.assertIn("photography workflow pain problem pain workflow", queries)
            self.assertIn("is there an app for photography", queries)
            self.assertIn("best way to manage photography", queries)

    def test_adaptive_resources_limit_collection_without_hiding_search_agents(self) -> None:
        resources = {"max_search_agents": 3, "max_deep_research_agents": 2}

        high_backlog = plan_adaptive_resources(
            resources,
            backlog_count=85,
            device_health=DeviceHealth(cpu_load_ratio=0.2, memory_available_bytes=8_000_000_000, status="healthy"),
        )
        medium_backlog = plan_adaptive_resources(
            resources,
            backlog_count=30,
            device_health=DeviceHealth(cpu_load_ratio=0.2, memory_available_bytes=8_000_000_000, status="healthy"),
        )
        low_backlog = plan_adaptive_resources(
            resources,
            backlog_count=5,
            device_health=DeviceHealth(cpu_load_ratio=0.2, memory_available_bytes=8_000_000_000, status="healthy"),
        )

        self.assertEqual(high_backlog.search_slots, 3)
        self.assertEqual(high_backlog.collector_limit, 8)
        self.assertEqual(high_backlog.deep_research_slots, 2)
        self.assertEqual(medium_backlog.search_slots, 3)
        self.assertEqual(medium_backlog.collector_limit, 12)
        self.assertEqual(low_backlog.search_slots, 3)
        self.assertIsNone(low_backlog.collector_limit)

    def test_adaptive_resources_respect_busy_device_and_disabled_search(self) -> None:
        busy = plan_adaptive_resources(
            {"max_search_agents": 3, "max_deep_research_agents": 4},
            backlog_count=85,
            device_health=DeviceHealth(cpu_load_ratio=0.8, memory_available_bytes=8_000_000_000, status="busy"),
        )
        very_busy = plan_adaptive_resources(
            {"max_search_agents": 3, "max_deep_research_agents": 4},
            backlog_count=85,
            device_health=DeviceHealth(cpu_load_ratio=1.3, memory_available_bytes=8_000_000_000, status="very_busy"),
        )
        search_disabled = plan_adaptive_resources(
            {"max_search_agents": 0, "max_deep_research_agents": 2},
            backlog_count=10,
            device_health=DeviceHealth(cpu_load_ratio=0.2, memory_available_bytes=8_000_000_000, status="healthy"),
        )

        self.assertEqual(busy.deep_research_slots, 1)
        self.assertEqual(very_busy.deep_research_slots, 0)
        self.assertEqual(search_disabled.search_slots, 0)
        self.assertEqual(search_disabled.deep_research_slots, 2)

    def test_runtime_starts_one_deep_worker_per_configured_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            storage = Storage(db_path)
            storage.migrate()
            storage.update_resource_config({"max_deep_research_agents": 3})
            storage.close()
            controller = RuntimeController(db_path, input_dir=Path(directory) / "inbox", interval_seconds=60)

            self.assertTrue(controller.start())
            status = controller.status()
            controller.stop()

            self.assertTrue(status["workers"]["discovery"])
            self.assertTrue(status["workers"]["deep_research_1"])
            self.assertTrue(status["workers"]["deep_research_2"])
            self.assertTrue(status["workers"]["deep_research_3"])

    def test_runner_separates_discovery_from_deep_research_queue(self) -> None:
        class RecordingRunner(AlwaysOnRunner):
            def __init__(self, storage: Storage, input_dir: Path):
                super().__init__(storage, input_dir)
                self.queue_count_when_search_started: int | None = None

            def _run_deep_research_slots(self, limit: int) -> list[str]:
                run_ids = []
                for index, row in enumerate(self.storage.list_queue()[:limit], start=1):
                    self.storage.dequeue_research(str(row["requirement_id"]))
                    run_ids.append(f"fake-run-{index}")
                return run_ids

            def _run_search_task_group(self, task_group, search_agent_count: int, collector_limit_override: int | None = None) -> dict[str, int | str | None]:
                self.queue_count_when_search_started = len(self.storage.list_queue())
                return {
                    "items_loaded": 0,
                    "candidates": 0,
                    "requirements_changed": 0,
                    "task_group_run_id": "search-run-1",
                }

        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            task_group = storage.create_task_group(
                name="3C",
                task_type=TaskGroupType.DOMAIN,
                domain="consumer electronics",
                input_dir=str(Path(directory) / "3c"),
            )
            storage.upsert_requirement(
                RequirementRecord(
                    requirement_id="req-queued",
                    canonical_requirement="Users need better consumer electronics setup help",
                    description="Need help setting up devices.",
                    status=RequirementStatus.QUEUED_FOR_RESEARCH,
                    first_seen=utc_now(),
                    last_seen=utc_now(),
                    times_detected=1,
                    evidence_count=1,
                    subreddit_count=1,
                    geo_distribution=[],
                    audience_segments=[],
                    current_scores={},
                    previous_scores={},
                    research_history=[],
                    decision_history=[],
                    reopen_events=[],
                    latest_recommendation=None,
                    task_group_ids=[task_group.task_group_id],
                )
            )
            storage.enqueue_research("req-queued", 50, "existing backlog", 1, None)
            plan = plan_adaptive_resources(
                storage.get_resource_config(),
                backlog_count=1,
                device_health=DeviceHealth(cpu_load_ratio=0.2, memory_available_bytes=8_000_000_000, status="healthy"),
            )
            runner = RecordingRunner(storage, Path(directory) / "inbox")

            result = runner.run_task_groups([task_group], plan)

            self.assertEqual(runner.queue_count_when_search_started, 1)
            self.assertIsNone(result["research_run"])
            self.assertEqual(len(storage.list_queue()), 1)

            research_result = runner.run_deep_research_once()

            self.assertEqual(research_result["research_run"], "fake-run-1")
            self.assertEqual(storage.list_queue(), [])

    def test_deep_research_worker_ignores_stopped_task_group_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            stopped = storage.create_task_group("Stopped", TaskGroupType.DOMAIN, "old", str(Path(directory) / "old"))
            running = storage.create_task_group("Running", TaskGroupType.DOMAIN, "new", str(Path(directory) / "new"))
            storage.update_task_group_status(running.task_group_id, TaskGroupStatus.RUNNING)
            for requirement_id, task_group_id, priority in [
                ("req-stopped", stopped.task_group_id, 100),
                ("req-running", running.task_group_id, 10),
            ]:
                storage.upsert_requirement(
                    RequirementRecord(
                        requirement_id=requirement_id,
                        canonical_requirement=f"{requirement_id} requirement",
                        description="Need validation.",
                        status=RequirementStatus.QUEUED_FOR_RESEARCH,
                        first_seen=utc_now(),
                        last_seen=utc_now(),
                        times_detected=1,
                        evidence_count=1,
                        subreddit_count=1,
                        geo_distribution=[],
                        audience_segments=[],
                        current_scores={},
                        previous_scores={},
                        research_history=[],
                        decision_history=[],
                        reopen_events=[],
                        latest_recommendation=None,
                        task_group_ids=[task_group_id],
                    )
                )
                storage.enqueue_research(requirement_id, priority, "test", 1, None)

            locked = storage.lock_next_research("research-agent-1", [running.task_group_id])

            self.assertEqual(locked, "req-running")
            rows = {row["requirement_id"]: row for row in storage.list_queue()}
            self.assertIsNone(rows["req-stopped"]["locked_by"])
            self.assertNotIn("req-running", rows)

    def test_collected_items_keep_search_agent_identity(self) -> None:
        class FakeCollector(OpenCliRedditCollector):
            def search(self, query: str, limit: int = 25, subreddit: str = "", sort: str = "", time: str = "") -> dict[str, object]:
                return {
                    "command": ["opencli", "reddit", "search", query, "--subreddit", subreddit],
                    "stderr": "",
                    "items": [
                        {
                            "source": "reddit_opencli",
                            "source_url": f"https://reddit.com/{query.replace(' ', '-')}",
                            "subreddit": subreddit or "photo",
                            "title": f"Need help with {query}",
                            "body": "",
                            "collection_query": query,
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            task_group = storage.create_task_group(
                name="Photography",
                task_type=TaskGroupType.DOMAIN,
                domain="photography",
                input_dir=str(Path(directory) / "photo"),
            )

            result = FakeCollector().collect_queries_to_inbox(
                task_group,
                "run-1",
                [
                    {"agent_id": "search-agent-1", "query": "query one", "subreddit": "photography", "strategy": "advice"},
                    {"agent_id": "search-agent-2", "query": "query two", "subreddit": "AskPhotography", "strategy": "support"},
                ],
                limit_per_query=1,
            )

            self.assertEqual(result["search_agents"][0]["agent_id"], "search-agent-1")
            self.assertEqual(result["search_agents"][0]["subreddit"], "photography")
            self.assertEqual(result["search_agents"][0]["strategy"], "advice")
            first_output = Path(result["search_agents"][0]["output_path"]).read_text(encoding="utf-8")
            self.assertIn('"search_agent_id": "search-agent-1"', first_output)
            self.assertIn('"search_query": "query one"', first_output)
            self.assertIn('"search_subreddit": "photography"', first_output)

    def test_failed_search_agent_is_returned_for_logging(self) -> None:
        class FailingCollector(OpenCliRedditCollector):
            def search(self, query: str, limit: int = 25, subreddit: str = "", sort: str = "", time: str = "") -> dict[str, object]:
                raise RuntimeError("Pre-navigation to https://reddit.com failed: No SW")

        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "test.sqlite3")
            storage.migrate()
            task_group = storage.create_task_group(
                name="Photography",
                task_type=TaskGroupType.DOMAIN,
                domain="photography",
                input_dir=str(Path(directory) / "photo"),
            )
            events = []

            result = FailingCollector().collect_queries_to_inbox(
                task_group,
                "run-1",
                [{"agent_id": "search-agent-1", "query": "query one", "subreddit": "photography", "strategy": "advice"}],
                limit_per_query=1,
                event_callback=lambda step, message, payload: events.append((step, message, payload)),
            )

            self.assertEqual(result["items_collected"], 0)
            self.assertEqual(result["search_agents"][0]["status"], "failed")
            self.assertIn("No SW", result["search_agents"][0]["error"])
            self.assertIn("collector_query_failed", [event[0] for event in events])

    def test_search_relevance_gate_rejects_unrelated_3c_results(self) -> None:
        unrelated = search_relevance_check(
            {
                "search_query": "headphones earbuds uncomfortable noise cancelling connection problem",
                "search_subreddit": "HeadphoneAdvice",
                "subreddit": "BestofRedditorUpdates",
                "title": "AITA for getting angry with my girlfriend",
                "body": "Personal relationship story.",
            }
        )
        related = search_relevance_check(
            {
                "search_query": "Amazon electronics wrong item refund denied phone laptop gpu",
                "search_subreddit": "amazonprime",
                "subreddit": "TwentiesIndia",
                "title": "Ordered an RTX 5090 on Amazon, got detergent and refund was denied",
                "body": "",
            }
        )

        self.assertFalse(unrelated["is_relevant"])
        self.assertTrue(related["is_relevant"])

    def test_llm_requirement_analysis_normalization(self) -> None:
        analysis = normalize_llm_requirement_analysis(
            {
                "is_possible_requirement": True,
                "signals": ["workflow_pain"],
                "requirement_title": "Users need a better way to organize client photo galleries.",
                "requirement_description": "Photographers struggle to deliver and organize albums for clients.",
                "audience": ["photographers"],
                "pain_level": "medium",
                "confidence": 0.82,
            },
            "Photo workflow is painful",
            "I need a better way to deliver galleries.",
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis["is_possible_requirement"], True)
        self.assertEqual(analysis["signals"], ["workflow_pain"])
        self.assertEqual(analysis["requirement_title"], "Users need a better way to organize client photo galleries.")
        self.assertEqual(analysis["confidence"], 0.82)

        rejected = normalize_llm_requirement_analysis(
            {"is_possible_requirement": False},
            "Björk photographed by Spike Jonze, 1995",
            "",
        )
        self.assertEqual(rejected["is_possible_requirement"], False)
        self.assertEqual(rejected["signals"], [])
        self.assertIn("deep research", rejected["sample_rejection_reason"])


if __name__ == "__main__":
    unittest.main()
