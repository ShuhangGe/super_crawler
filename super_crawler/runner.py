from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents import ChangeDetectionAgent, DeepResearchAgent, DiscoveryAgent, RequirementMemoryAgent, one_sentence_requirement
from .collectors import OpenCliRedditCollector
from .models import AgentActivityLog, TaskGroup, TaskGroupRun, TaskGroupStatus, TaskGroupType, utc_now
from .search_planner import SearchPlannerAgent
from .storage import Storage


TARGET_RESEARCH_BACKLOG = 20
MAX_RESEARCH_BACKLOG = 50
MEDIUM_BACKLOG_COLLECTOR_LIMIT = 12
HIGH_BACKLOG_COLLECTOR_LIMIT = 8
MIN_AVAILABLE_MEMORY_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(slots=True)
class DeviceHealth:
    cpu_load_ratio: float | None
    memory_available_bytes: int | None
    status: str


@dataclass(slots=True)
class AdaptiveResourcePlan:
    search_slots: int
    deep_research_slots: int
    collector_limit: int | None
    backlog_count: int
    device_status: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "search_slots": self.search_slots,
            "deep_research_slots": self.deep_research_slots,
            "collector_limit": self.collector_limit,
            "backlog_count": self.backlog_count,
            "device_status": self.device_status,
            "reason": self.reason,
        }


class AlwaysOnRunner:
    """Periodic runner for controlled-source MVP operation."""

    def __init__(self, storage: Storage, input_dir: str | Path, interval_seconds: int = 10_800):
        self.storage = storage
        self.input_dir = Path(input_dir)
        self.interval_seconds = interval_seconds

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.interval_seconds)

    def run_once(self) -> dict[str, int | str | None]:
        resources = self.storage.get_resource_config()
        device_health = measure_device_health()
        backlog_count = len(self.storage.list_queue())
        adaptive_plan = plan_adaptive_resources(resources, backlog_count, device_health)
        groups = self.storage.list_task_groups([TaskGroupStatus.RUNNING.value])
        if groups:
            return self.run_task_groups(groups[: adaptive_plan.search_slots], adaptive_plan)

        items = load_json_items(self.input_dir)
        candidates = DiscoveryAgent(self.storage, "discovery-daemon").ingest_reddit_items(items) if items else []
        changed = RequirementMemoryAgent(self.storage, "requirement-memory").reconcile_candidates()
        reopened = ChangeDetectionAgent(self.storage, "change-detector").evaluate_reopenings()
        adaptive_plan = plan_adaptive_resources(resources, len(self.storage.list_queue()), device_health)
        research_runs = self._run_deep_research_slots(adaptive_plan.deep_research_slots)
        return {
            "items_loaded": len(items),
            "candidates": len(candidates),
            "requirements_changed": len(changed),
            "reopened": len(reopened),
            "research_run": research_runs[0] if research_runs else None,
            "research_runs": ",".join(research_runs),
            "adaptive_resources": json.dumps(adaptive_plan.as_dict(), sort_keys=True),
        }

    def run_task_group(self, task_group_id: str) -> dict[str, int | str | None]:
        task_group = self.storage.get_task_group(task_group_id)
        if task_group is None:
            raise ValueError(f"Unknown task group: {task_group_id}")
        return self.run_task_groups([task_group])

    def run_task_groups(
        self,
        task_groups: list[TaskGroup],
        adaptive_plan: AdaptiveResourcePlan | None = None,
    ) -> dict[str, int | str | None]:
        resources = self.storage.get_resource_config()
        if adaptive_plan is None:
            adaptive_plan = plan_adaptive_resources(resources, len(self.storage.list_queue()), measure_device_health())
        search_slots = max(int(adaptive_plan.search_slots), 0)
        active_task_groups = task_groups if search_slots > 0 else []
        slot_allocations = allocate_search_slots(len(active_task_groups), search_slots)
        total_items = 0
        total_candidates = 0
        total_changed = 0
        task_runs: list[str] = []
        for index, task_group in enumerate(active_task_groups):
            result = self._run_search_task_group(
                task_group,
                search_agent_count=slot_allocations[index],
                collector_limit_override=adaptive_plan.collector_limit,
            )
            total_items += int(result["items_loaded"] or 0)
            total_candidates += int(result["candidates"] or 0)
            total_changed += int(result["requirements_changed"] or 0)
            task_runs.append(str(result["task_group_run_id"]))

        reopened = ChangeDetectionAgent(self.storage, "change-detector").evaluate_reopenings()
        deep_plan = plan_adaptive_resources(resources, len(self.storage.list_queue()), measure_device_health())
        research_runs = self._run_deep_research_slots(deep_plan.deep_research_slots)
        return {
            "items_loaded": total_items,
            "candidates": total_candidates,
            "requirements_changed": total_changed,
            "reopened": len(reopened),
            "research_run": research_runs[0] if research_runs else None,
            "research_runs": ",".join(research_runs),
            "task_group_runs": len(task_runs),
            "task_group_run_ids": ",".join(task_runs),
            "adaptive_resources": json.dumps(
                {
                    "search": adaptive_plan.as_dict(),
                    "deep_research": deep_plan.as_dict(),
                },
                sort_keys=True,
            ),
        }

    def _run_deep_research_slots(self, limit: int) -> list[str]:
        run_ids: list[str] = []
        for index in range(max(limit, 0)):
            run = DeepResearchAgent(self.storage, f"research-agent-{index + 1}").run_next()
            if run is None:
                break
            run_ids.append(run.research_run_id)
        return run_ids

    def _run_search_task_group(
        self,
        task_group: TaskGroup,
        search_agent_count: int = 1,
        collector_limit_override: int | None = None,
    ) -> dict[str, int | str | None]:
        started_at = utc_now()
        task_group_run_id = f"tgr_{task_group.task_group_id}_{started_at.replace('-', '').replace(':', '').replace('+', 'Z')}"
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "scheduler",
            "task_group_started",
            f"Task group {task_group.name} started",
            {
                "task_group_name": task_group.name,
                "input_dir": task_group.input_dir,
                "search_agent_count": search_agent_count,
                "collector_limit_override": collector_limit_override,
                "model_config": self.storage.get_task_group_config(task_group.task_group_id),
            },
        )
        collection_result = self._collect_for_task_group(task_group, task_group_run_id, search_agent_count, collector_limit_override)
        if collection_result is not None and "search_agents" in collection_result:
            output_paths = [agent["output_path"] for agent in collection_result["search_agents"] if agent.get("output_path")]
            scan = load_json_files_with_report(output_paths)
        else:
            scan = load_json_items_with_report(task_group.input_dir)
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "scheduler",
            "input_folder_scanned",
            f"Scanned input folder {task_group.input_dir}",
            {"input_dir": task_group.input_dir, "files": scan["files"]},
        )
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "scheduler",
            "files_read",
            f"Read {len(scan['files'])} JSON file(s)",
            {"files": scan["files"]},
        )
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "scheduler",
            "input_loaded",
            f"Loaded {len(scan['items'])} item(s) from {task_group.input_dir}",
            {"items_loaded": len(scan["items"]), "items_skipped": scan["items_skipped"], "collection_result": collection_result},
        )
        if scan["items_skipped"]:
            self.storage.log_experiment(
                task_group.task_group_id,
                task_group_run_id,
                "scheduler",
                "items_skipped",
                f"Skipped {scan['items_skipped']} invalid item(s)",
                {"skipped": scan["skipped"]},
            )

        items = [tag_task_item(item, task_group, task_group_run_id) for item in scan["items"]]
        config = self.storage.get_task_group_config(task_group.task_group_id)
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "discovery",
            "discovery_started",
            f"Discovery started for {task_group.name}",
            {"items_loaded": len(items), "model": config.get("model_search"), "method": "llm"},
        )
        candidates = DiscoveryAgent(self.storage, f"discovery-{task_group.task_group_id}").ingest_reddit_items(
            items,
            task_group_id=task_group.task_group_id,
            task_group_run_id=task_group_run_id,
            model_name=config.get("model_search", "deepseek-v4-flash"),
            use_llm=True,
        )
        candidate_titles = [candidate.requirement_title for candidate in candidates]
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "discovery",
            "candidates_generated",
            f"Generated {len(candidates)} candidate requirement(s)",
            {"candidate_titles": candidate_titles},
        )
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "requirement_memory",
            "requirement_memory_started",
            f"Requirement memory started for {task_group.name}",
            {"candidate_ids": [candidate.candidate_id for candidate in candidates]},
        )
        changed = RequirementMemoryAgent(self.storage, "requirement-memory").reconcile_candidates()
        queued = len([item for item in changed if item.task_group_ids and task_group.task_group_id in item.task_group_ids])
        rejected = len([item for item in changed if item.status.value in {"rejected", "archived"}])
        group_changed = [item for item in changed if task_group.task_group_id in item.task_group_ids]
        requirement_sentences = [one_sentence_requirement(item) for item in group_changed]
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "requirement_memory",
            "requirements_generated",
            f"Generated or updated {len(group_changed)} requirement(s)",
            {"requirement_ids": [item.requirement_id for item in group_changed]},
        )
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "requirement_memory",
            "pool_requirement_sample",
            f"Requirement memory generated {len(requirement_sentences)} one-sentence requirement(s)",
            {"requirements": requirement_sentences},
        )
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "requirement_memory",
            "requirements_queued",
            f"Queued {queued} requirement(s) for deep research",
            {"queued": queued},
        )
        completed_at = utc_now()
        summary = (
            f"{task_group.name}: loaded {len(items)} item(s), created {len(candidates)} candidate(s), "
            f"changed {len(changed)} requirement(s)."
        )
        self.storage.upsert_task_group_run(
            TaskGroupRun(
                task_group_run_id=task_group_run_id,
                task_group_id=task_group.task_group_id,
                started_at=started_at,
                completed_at=completed_at,
                status=TaskGroupStatus.COMPLETED,
                items_collected=len(items),
                candidates_created=len(candidates),
                requirements_found=len(changed),
                requirements_queued=queued,
                requirements_rejected=rejected,
                summary=summary,
            )
        )
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "scheduler",
            "run_completed",
            summary,
            {
                "items_loaded": len(items),
                "candidates": len(candidates),
                "requirements_changed": len(changed),
                "requirements_queued": queued,
                "requirements_rejected": rejected,
            },
        )
        return {
            "items_loaded": len(items),
            "candidates": len(candidates),
            "requirements_changed": len(changed),
            "task_group_run_id": task_group_run_id,
        }

    def _collect_for_task_group(
        self,
        task_group: TaskGroup,
        task_group_run_id: str,
        search_agent_count: int = 1,
        collector_limit_override: int | None = None,
    ) -> dict[str, object] | None:
        config = self.storage.get_task_group_config(task_group.task_group_id)
        if config.get("collector_enabled") != "1":
            self.storage.log_experiment(
                task_group.task_group_id,
                task_group_run_id,
                "collector",
                "collector_skipped",
                "Reddit OpenCLI collector is disabled",
                {"collector_enabled": config.get("collector_enabled", "0")},
            )
            return None
        cycle_index = len(self.storage.list_search_plans(task_group.task_group_id, limit=500)) + 1
        recent_queries = [
            str(assignment.get("query", ""))
            for plan in self.storage.list_search_plans(task_group.task_group_id, limit=20)
            for assignment in plan.get("assignments", [])
        ]
        search_insights = self.storage.list_search_insights(task_group.task_group_id, limit=20)
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "search_planner",
            "search_plan_started",
            f"AI 搜索规划器正在理解任务“{task_group.name}”，并结合深度研究反馈生成第 {cycle_index} 轮搜索计划。",
            {
                "cycle_index": cycle_index,
                "input_description": task_group.description or task_group.domain or task_group.name,
                "recent_query_count": len(recent_queries),
                "deep_research_feedback_count": len(search_insights),
                "model": config.get("model_search", "deepseek-v4-flash"),
            },
        )
        plan = SearchPlannerAgent(model_name=config.get("model_search", "deepseek-v4-flash")).plan(
            task_group,
            search_agent_count,
            cycle_index,
            recent_queries,
            search_insights,
        )
        plan_id = self.storage.save_search_plan(
            task_group.task_group_id,
            task_group_run_id,
            str(plan["planner_agent_id"]),
            int(plan["cycle_index"]),
            str(plan["input_description"]),
            str(plan["search_goal"]),
            dict(plan["search_brief"]),
            list(plan["assignments"]),
        )
        self.storage.log_activity(
            AgentActivityLog(
                agent_id=str(plan["planner_agent_id"]),
                agent_role="search_planner",
                task_id="plan_search_cycle",
                status="completed",
                started_at=utc_now(),
                completed_at=utc_now(),
                input_refs=[task_group.task_group_id, task_group_run_id, str(plan["input_description"])],
                output_refs=[f"search_plan:{plan_id}", *[str(query) for query in plan["queries"]]],
                error=None,
                retry_count=0,
                cost_estimate=0.0,
            )
        )
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "search_planner",
            "search_plan_created",
            f"Search planner created {len(plan['assignments'])} search assignment(s)",
            {"plan_id": plan_id, **plan},
        )
        collector = OpenCliRedditCollector(
            command=config.get("collector_command", "opencli reddit search"),
            timeout_seconds=parse_int(config.get("collector_timeout_seconds"), 120),
        )
        assignments = [assignment for assignment in plan["assignments"] if str(assignment.get("query", "")).strip()]
        queries = [str(query) for query in plan["queries"]]
        configured_limit = parse_int(config.get("collector_limit"), 25)
        limit = min(configured_limit, collector_limit_override) if collector_limit_override else configured_limit
        limit_per_query = max(1, limit // max(len(queries), 1))
        def log_collector_event(step_name: str, message: str, payload: dict[str, object]) -> None:
            self.storage.log_experiment(
                task_group.task_group_id,
                task_group_run_id,
                "collector",
                step_name,
                message,
                payload,
            )

        try:
            result = collector.collect_queries_to_inbox(
                task_group,
                task_group_run_id,
                assignments,
                limit_per_query=limit_per_query,
                event_callback=log_collector_event,
            )
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            self.storage.log_experiment(
                task_group.task_group_id,
                task_group_run_id,
                "collector",
                "collector_failed",
                f"OpenCLI Reddit collection failed: {exc}",
                {"error": str(exc), "command": config.get("collector_command"), "queries": queries},
            )
            return {"error": str(exc)}
        for search_agent in result["search_agents"]:
            self.storage.log_activity(
                AgentActivityLog(
                    agent_id=str(search_agent["agent_id"]),
                    agent_role="discovery",
                    task_id="reddit_opencli_search",
                    status="completed",
                    started_at=search_agent["started_at"],
                    completed_at=search_agent["completed_at"],
                    input_refs=[task_group.task_group_id, task_group_run_id, str(search_agent["query"])],
                    output_refs=[str(search_agent["output_path"]), *[str(url) for url in search_agent.get("urls", [])]],
                    error=None,
                    retry_count=0,
                    cost_estimate=0.0,
                )
            )
            self.storage.log_experiment(
                task_group.task_group_id,
                task_group_run_id,
                "discovery",
                "search_agent_completed",
                f"{search_agent['agent_id']} collected {search_agent['items_collected']} item(s) for query: {search_agent['query']}",
                search_agent,
            )
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "collector",
            "reddit_opencli_collected",
            f"Collected {result['items_collected']} Reddit item(s) with OpenCLI",
            result,
        )
        return result


def load_json_items(input_dir: str | Path) -> list[dict]:
    return list(load_json_items_with_report(input_dir)["items"])


def load_json_items_with_report(input_dir: str | Path) -> dict[str, object]:
    directory = Path(input_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return load_json_files_with_report(sorted(directory.glob("*.json")))


def load_json_files_with_report(paths: list[str | Path]) -> dict[str, object]:
    items: list[dict] = []
    files: list[str] = []
    skipped: list[dict[str, str]] = []
    for path in [Path(item) for item in paths]:
        files.append(str(path))
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, list):
            raise ValueError(f"{path} must contain a JSON array")
        for index, item in enumerate(loaded):
            if isinstance(item, dict):
                items.append(item)
            else:
                skipped.append({"file": str(path), "index": str(index), "reason": "item is not an object"})
    return {"items": items, "files": files, "items_skipped": len(skipped), "skipped": skipped}


def tag_task_item(item: dict, task_group: TaskGroup, task_group_run_id: str) -> dict:
    tagged = dict(item)
    tagged["task_group_id"] = task_group.task_group_id
    tagged["task_group_run_id"] = task_group_run_id
    tagged["task_group_name"] = task_group.name
    tagged["task_group_type"] = task_group.task_type.value
    if task_group.domain:
        tagged["domain"] = task_group.domain
    return tagged


def parse_int(value: object, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def allocate_search_slots(group_count: int, max_search_agents: int) -> list[int]:
    if group_count <= 0:
        return []
    slots = max(max_search_agents, 1)
    allocations = [1 for _ in range(group_count)]
    remaining = max(slots - group_count, 0)
    index = 0
    while remaining > 0:
        allocations[index % group_count] += 1
        remaining -= 1
        index += 1
    return allocations


def plan_adaptive_resources(
    resources: dict[str, int],
    backlog_count: int,
    device_health: DeviceHealth,
) -> AdaptiveResourcePlan:
    configured_search = max(int(resources.get("max_search_agents", 3)), 0)
    configured_deep = max(int(resources.get("max_deep_research_agents", 1)), 0)
    if backlog_count > MAX_RESEARCH_BACKLOG:
        search_slots = min(configured_search, 1)
        collector_limit = HIGH_BACKLOG_COLLECTOR_LIMIT
        reason = "research backlog is above the maximum target, so search intake is throttled"
    elif backlog_count > TARGET_RESEARCH_BACKLOG:
        search_slots = min(configured_search, 2)
        collector_limit = MEDIUM_BACKLOG_COLLECTOR_LIMIT
        reason = "research backlog is above target, so search intake is reduced"
    else:
        search_slots = configured_search
        collector_limit = None
        reason = "research backlog is within target, so configured search intake is allowed"

    if configured_search > 0:
        search_slots = max(search_slots, 1)

    if device_health.status == "very_busy":
        deep_slots = 0
        reason += "; device is very busy, so deep research is paused this cycle"
    elif device_health.status == "busy":
        deep_slots = min(configured_deep, 1)
        reason += "; device is busy, so deep research is limited"
    else:
        deep_slots = min(configured_deep, backlog_count)
        reason += "; device is healthy, so deep research may consume queued work"

    return AdaptiveResourcePlan(
        search_slots=search_slots,
        deep_research_slots=max(deep_slots, 0),
        collector_limit=collector_limit,
        backlog_count=backlog_count,
        device_status=device_health.status,
        reason=reason,
    )


def measure_device_health() -> DeviceHealth:
    cpu_ratio = current_cpu_load_ratio()
    memory_available = current_memory_available_bytes()
    memory_low = memory_available is not None and memory_available < MIN_AVAILABLE_MEMORY_BYTES
    if (cpu_ratio is not None and cpu_ratio >= 1.2) or memory_low:
        status = "very_busy"
    elif cpu_ratio is not None and cpu_ratio >= 0.7:
        status = "busy"
    else:
        status = "healthy"
    return DeviceHealth(cpu_load_ratio=cpu_ratio, memory_available_bytes=memory_available, status=status)


def current_cpu_load_ratio() -> float | None:
    if not hasattr(os, "getloadavg"):
        return None
    try:
        load_1m = os.getloadavg()[0]
    except OSError:
        return None
    cpu_count = os.cpu_count() or 1
    return round(load_1m / cpu_count, 2)


def current_memory_available_bytes() -> int | None:
    page_size = _sysconf_int("SC_PAGE_SIZE")
    available_pages = _sysconf_int("SC_AVPHYS_PAGES")
    if page_size is None or available_pages is None:
        return None
    return page_size * available_pages


def _sysconf_int(name: str) -> int | None:
    if name not in getattr(os, "sysconf_names", {}):
        return None
    try:
        return int(os.sysconf(name))
    except (OSError, ValueError):
        return None
