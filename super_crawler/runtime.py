from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .models import utc_now
from .runner import AlwaysOnRunner
from .storage import Storage


class RuntimeController:
    def __init__(self, db_path: str | Path, input_dir: str | Path = "data/reddit_inbox", interval_seconds: int = 60):
        self.db_path = Path(db_path)
        self.input_dir = Path(input_dir)
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=50)
        self._last_result: dict[str, Any] | None = None
        self._worker_results: dict[str, dict[str, Any]] = {}
        self._worker_errors: dict[str, str | None] = {}
        self._last_error: str | None = None
        self._cycle_count = 0

    def start(self) -> bool:
        with self._lock:
            if any(thread.is_alive() for thread in self._threads.values()):
                return False
            self._stop_event.clear()
            self._threads = {
                "discovery": threading.Thread(target=self._run_discovery_loop, name="discovery-runtime", daemon=True),
                "deep_research": threading.Thread(target=self._run_deep_research_loop, name="deep-research-runtime", daemon=True),
            }
            for thread in self._threads.values():
                thread.start()
            self._record("started", {"input_dir": str(self.input_dir), "interval_seconds": self.interval_seconds})
            return True

    def stop(self) -> bool:
        with self._lock:
            if not any(thread.is_alive() for thread in self._threads.values()):
                return False
            self._stop_event.set()
            self._record("stop_requested", {})
            return True

    def status(self) -> dict[str, Any]:
        running_threads = {name: thread.is_alive() for name, thread in self._threads.items()}
        running = any(running_threads.values()) and not self._stop_event.is_set()
        stopping = any(running_threads.values()) and self._stop_event.is_set()
        with self._lock:
            return {
                "running": running,
                "stopping": stopping,
                "cycle_count": self._cycle_count,
                "last_result": self._last_result,
                "worker_results": dict(self._worker_results),
                "worker_errors": dict(self._worker_errors),
                "workers": running_threads,
                "last_error": self._last_error,
                "events": list(reversed(self._events)),
                "input_dir": str(self.input_dir),
                "interval_seconds": self.interval_seconds,
            }

    def run_once(self) -> dict[str, Any]:
        started_at = utc_now()
        with Storage(self.db_path) as storage:
            storage.migrate()
            result = AlwaysOnRunner(storage, self.input_dir, self.interval_seconds).run_once()
            completed_at = utc_now()
            pipeline_run_id = f"pipe_{self._safe_time_id(completed_at)}_{self._cycle_count + 1}"
            storage.save_pipeline_run(
                pipeline_run_id=pipeline_run_id,
                started_at=started_at,
                completed_at=completed_at,
                status="completed",
                result=result,
                summary=self._summary(result),
            )
            result = {**result, "pipeline_run_id": pipeline_run_id}
        with self._lock:
            self._cycle_count += 1
            self._last_result = result
            self._last_error = None
            self._record("cycle_completed", result)
        return result

    def _run_loop(self) -> None:
        self._run_worker_loop("combined", self.interval_seconds, "run_once")

    def _run_discovery_loop(self) -> None:
        self._run_worker_loop("discovery", self.interval_seconds, "run_discovery_once")

    def _run_deep_research_loop(self) -> None:
        self._run_worker_loop("deep_research", 1, "run_deep_research_once")

    def _run_worker_loop(self, worker_name: str, interval_seconds: int, runner_method: str) -> None:
        while not self._stop_event.is_set():
            started_at = utc_now()
            try:
                with Storage(self.db_path) as storage:
                    storage.migrate()
                    runner = AlwaysOnRunner(storage, self.input_dir, self.interval_seconds)
                    result = getattr(runner, runner_method)()
                    completed_at = utc_now()
                    pipeline_run_id = f"pipe_{worker_name}_{self._safe_time_id(completed_at)}_{self._cycle_count + 1}"
                    storage.save_pipeline_run(
                        pipeline_run_id=pipeline_run_id,
                        started_at=started_at,
                        completed_at=completed_at,
                        status="completed",
                        result={**result, "worker": worker_name},
                        summary=self._summary(result),
                    )
                    result = {**result, "pipeline_run_id": pipeline_run_id, "worker": worker_name}
                with self._lock:
                    self._cycle_count += 1
                    self._last_result = result
                    self._worker_results[worker_name] = result
                    self._worker_errors[worker_name] = None
                    self._last_error = None
                    self._record(f"{worker_name}_cycle_completed", result)
            except Exception as exc:  # noqa: BLE001 - visible runtime monitor should capture all failures.
                completed_at = utc_now()
                result = {"error": str(exc), "worker": worker_name}
                try:
                    with Storage(self.db_path) as storage:
                        storage.migrate()
                        storage.save_pipeline_run(
                            pipeline_run_id=f"pipe_failed_{worker_name}_{self._safe_time_id(completed_at)}_{self._cycle_count + 1}",
                            started_at=completed_at,
                            completed_at=completed_at,
                            status="failed",
                            result=result,
                            summary=str(exc),
                        )
                except Exception:
                    pass
                with self._lock:
                    self._last_error = str(exc)
                    self._worker_errors[worker_name] = str(exc)
                    self._record(f"{worker_name}_cycle_failed", {"error": str(exc)})
            self._stop_event.wait(interval_seconds)
        with self._lock:
            self._record(f"{worker_name}_stopped", {})

    def _record(self, event: str, detail: dict[str, Any]) -> None:
        self._events.appendleft({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, "detail": detail})

    def _summary(self, result: dict[str, Any]) -> str:
        return (
            f"Loaded {result.get('items_loaded', 0)} item(s), created {result.get('candidates', 0)} candidate(s), "
            f"changed {result.get('requirements_changed', 0)} requirement(s), reopened {result.get('reopened', 0)}, "
            f"research run {result.get('research_run') or 'none'}."
        )

    def _safe_time_id(self, value: str) -> str:
        return (
            value.replace("-", "")
            .replace(":", "")
            .replace("+", "Z")
            .replace(".", "")
        )
