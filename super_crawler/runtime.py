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
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=50)
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._cycle_count = 0

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="agent-runtime", daemon=True)
            self._thread.start()
            self._record("started", {"input_dir": str(self.input_dir), "interval_seconds": self.interval_seconds})
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return False
            self._stop_event.set()
            self._record("stop_requested", {})
            return True

    def status(self) -> dict[str, Any]:
        thread = self._thread
        running = bool(thread and thread.is_alive() and not self._stop_event.is_set())
        stopping = bool(thread and thread.is_alive() and self._stop_event.is_set())
        with self._lock:
            return {
                "running": running,
                "stopping": stopping,
                "cycle_count": self._cycle_count,
                "last_result": self._last_result,
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
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - visible runtime monitor should capture all failures.
                completed_at = utc_now()
                result = {"error": str(exc)}
                try:
                    with Storage(self.db_path) as storage:
                        storage.migrate()
                        storage.save_pipeline_run(
                            pipeline_run_id=f"pipe_failed_{self._safe_time_id(completed_at)}_{self._cycle_count + 1}",
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
                    self._record("cycle_failed", {"error": str(exc)})
            self._stop_event.wait(self.interval_seconds)
        with self._lock:
            self._record("stopped", {})

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
