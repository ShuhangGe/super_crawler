from __future__ import annotations

import json
import time
from pathlib import Path

from .agents import ChangeDetectionAgent, DeepResearchAgent, DiscoveryAgent, PoolManagerAgent
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
        groups = self.storage.list_task_groups([TaskGroupStatus.RUNNING.value])
        if groups:
            return self.run_task_groups(groups)

        items = load_json_items(self.input_dir)
        candidates = DiscoveryAgent(self.storage, "discovery-daemon").ingest_reddit_items(items) if items else []
        changed = PoolManagerAgent(self.storage, "pool-manager").reconcile_candidates()
        reopened = ChangeDetectionAgent(self.storage, "change-detector").evaluate_reopenings()
        run = DeepResearchAgent(self.storage, "research-agent-1").run_next()
        return {
            "items_loaded": len(items),
            "candidates": len(candidates),
            "requirements_changed": len(changed),
            "reopened": len(reopened),
            "research_run": run.research_run_id if run else None,
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
        research_run = DeepResearchAgent(self.storage, "research-agent-1").run_next()
        return {
            "items_loaded": total_items,
            "candidates": total_candidates,
            "requirements_changed": total_changed,
            "reopened": len(reopened),
            "research_run": research_run.research_run_id if research_run else None,
            "task_group_runs": len(task_runs),
            "task_group_run_ids": ",".join(task_runs),
        }

    def _run_search_task_group(self, task_group: TaskGroup) -> dict[str, int | str | None]:
        started_at = utc_now()
        task_group_run_id = f"tgr_{task_group.task_group_id}_{started_at.replace('-', '').replace(':', '').replace('+', 'Z')}"
        items = [tag_task_item(item, task_group, task_group_run_id) for item in load_json_items(task_group.input_dir)]
        candidates = DiscoveryAgent(self.storage, f"discovery-{task_group.task_group_id}").ingest_reddit_items(
            items,
            task_group_id=task_group.task_group_id,
            task_group_run_id=task_group_run_id,
        )
        changed = PoolManagerAgent(self.storage, "pool-manager").reconcile_candidates()
        queued = len([item for item in changed if item.task_group_ids and task_group.task_group_id in item.task_group_ids])
        rejected = len([item for item in changed if item.status.value in {"rejected", "archived"}])
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
        return {
            "items_loaded": len(items),
            "candidates": len(candidates),
            "requirements_changed": len(changed),
            "task_group_run_id": task_group_run_id,
        }


def load_json_items(input_dir: str | Path) -> list[dict]:
    directory = Path(input_dir)
    directory.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(directory.glob("*.json")):
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, list):
            raise ValueError(f"{path} must contain a JSON array")
        items.extend(loaded)
    return items


def tag_task_item(item: dict, task_group: TaskGroup, task_group_run_id: str) -> dict:
    tagged = dict(item)
    tagged["task_group_id"] = task_group.task_group_id
    tagged["task_group_run_id"] = task_group_run_id
    tagged["task_group_name"] = task_group.name
    tagged["task_group_type"] = task_group.task_type.value
    if task_group.domain:
        tagged["domain"] = task_group.domain
    return tagged
