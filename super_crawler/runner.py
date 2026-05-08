from __future__ import annotations

import json
import time
from pathlib import Path

from .agents import ChangeDetectionAgent, DeepResearchAgent, DiscoveryAgent, PoolManagerAgent
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
        self.input_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for path in sorted(self.input_dir.glob("*.json")):
            loaded = json.loads(path.read_text())
            if not isinstance(loaded, list):
                raise ValueError(f"{path} must contain a JSON array")
            items.extend(loaded)

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
