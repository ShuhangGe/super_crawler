from __future__ import annotations

import json
import time
from pathlib import Path

from .agents import ChangeDetectionAgent, DeepResearchAgent, DiscoveryAgent, PoolManagerAgent, one_sentence_requirement
from .collectors import OpenCliRedditCollector
from .models import TaskGroup, TaskGroupRun, TaskGroupStatus, TaskGroupType, utc_now
from .storage import Storage


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
        groups = self.storage.list_task_groups([TaskGroupStatus.RUNNING.value])
        if groups:
            return self.run_task_groups(groups[: resources.get("max_search_agents", 3)])

        items = load_json_items(self.input_dir)
        candidates = DiscoveryAgent(self.storage, "discovery-daemon").ingest_reddit_items(items) if items else []
        changed = PoolManagerAgent(self.storage, "pool-manager").reconcile_candidates()
        reopened = ChangeDetectionAgent(self.storage, "change-detector").evaluate_reopenings()
        research_runs = self._run_deep_research_slots(resources.get("max_deep_research_agents", 1))
        return {
            "items_loaded": len(items),
            "candidates": len(candidates),
            "requirements_changed": len(changed),
            "reopened": len(reopened),
            "research_run": research_runs[0] if research_runs else None,
            "research_runs": ",".join(research_runs),
        }

    def run_task_group(self, task_group_id: str) -> dict[str, int | str | None]:
        task_group = self.storage.get_task_group(task_group_id)
        if task_group is None:
            raise ValueError(f"Unknown task group: {task_group_id}")
        return self.run_task_groups([task_group])

    def run_task_groups(self, task_groups: list[TaskGroup]) -> dict[str, int | str | None]:
        total_items = 0
        total_candidates = 0
        total_changed = 0
        task_runs: list[str] = []
        for task_group in task_groups:
            result = self._run_search_task_group(task_group)
            total_items += int(result["items_loaded"] or 0)
            total_candidates += int(result["candidates"] or 0)
            total_changed += int(result["requirements_changed"] or 0)
            task_runs.append(str(result["task_group_run_id"]))

        reopened = ChangeDetectionAgent(self.storage, "change-detector").evaluate_reopenings()
        resources = self.storage.get_resource_config()
        research_runs = self._run_deep_research_slots(resources.get("max_deep_research_agents", 1))
        return {
            "items_loaded": total_items,
            "candidates": total_candidates,
            "requirements_changed": total_changed,
            "reopened": len(reopened),
            "research_run": research_runs[0] if research_runs else None,
            "research_runs": ",".join(research_runs),
            "task_group_runs": len(task_runs),
            "task_group_run_ids": ",".join(task_runs),
        }

    def _run_deep_research_slots(self, limit: int) -> list[str]:
        run_ids: list[str] = []
        for index in range(max(limit, 0)):
            run = DeepResearchAgent(self.storage, f"research-agent-{index + 1}").run_next()
            if run is None:
                break
            run_ids.append(run.research_run_id)
        return run_ids

    def _run_search_task_group(self, task_group: TaskGroup) -> dict[str, int | str | None]:
        started_at = utc_now()
        task_group_run_id = f"tgr_{task_group.task_group_id}_{started_at.replace('-', '').replace(':', '').replace('+', 'Z')}"
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "scheduler",
            "task_group_started",
            f"Task group {task_group.name} started",
            {"task_group_name": task_group.name, "input_dir": task_group.input_dir, "model_config": self.storage.get_task_group_config(task_group.task_group_id)},
        )
        collection_result = self._collect_for_task_group(task_group, task_group_run_id)
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
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "discovery",
            "discovery_started",
            f"Discovery started for {task_group.name}",
            {"items_loaded": len(items)},
        )
        candidates = DiscoveryAgent(self.storage, f"discovery-{task_group.task_group_id}").ingest_reddit_items(
            items,
            task_group_id=task_group.task_group_id,
            task_group_run_id=task_group_run_id,
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
            "pool_manager",
            "pool_manager_started",
            f"Pool manager started for {task_group.name}",
            {"candidate_ids": [candidate.candidate_id for candidate in candidates]},
        )
        changed = PoolManagerAgent(self.storage, "pool-manager").reconcile_candidates()
        queued = len([item for item in changed if item.task_group_ids and task_group.task_group_id in item.task_group_ids])
        rejected = len([item for item in changed if item.status.value in {"rejected", "archived"}])
        group_changed = [item for item in changed if task_group.task_group_id in item.task_group_ids]
        requirement_sentences = [one_sentence_requirement(item) for item in group_changed]
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "pool_manager",
            "requirements_generated",
            f"Generated or updated {len(group_changed)} requirement(s)",
            {"requirement_ids": [item.requirement_id for item in group_changed]},
        )
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "pool_manager",
            "pool_requirement_sample",
            f"Pool manager generated {len(requirement_sentences)} one-sentence requirement(s)",
            {"requirements": requirement_sentences},
        )
        self.storage.log_experiment(
            task_group.task_group_id,
            task_group_run_id,
            "pool_manager",
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

    def _collect_for_task_group(self, task_group: TaskGroup, task_group_run_id: str) -> dict[str, object] | None:
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
        collector = OpenCliRedditCollector(
            command=config.get("collector_command", "opencli reddit search"),
            timeout_seconds=parse_int(config.get("collector_timeout_seconds"), 120),
        )
        try:
            result = collector.collect_to_inbox(
                task_group,
                task_group_run_id,
                limit=parse_int(config.get("collector_limit"), 25),
            )
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            self.storage.log_experiment(
                task_group.task_group_id,
                task_group_run_id,
                "collector",
                "collector_failed",
                f"OpenCLI Reddit collection failed: {exc}",
                {"error": str(exc), "command": config.get("collector_command")},
            )
            return {"error": str(exc)}
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
    items: list[dict] = []
    files: list[str] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(directory.glob("*.json")):
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
