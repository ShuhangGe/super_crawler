from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any, Iterable, TypeVar

from .models import (
    AgentActivityLog,
    CandidateRequirement,
    RawEvidence,
    RequirementRecord,
    RequirementStatus,
    ResearchRun,
    TaskGroup,
    TaskGroupRun,
    TaskGroupStatus,
    TaskGroupType,
    utc_now,
)

T = TypeVar("T")

DEFAULT_DB_PATH = Path("data/super_crawler.sqlite3")


class Storage:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 30000")

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS raw_evidence (
                evidence_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_url TEXT NOT NULL,
                subreddit TEXT NOT NULL,
                post_id TEXT,
                comment_id TEXT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                author_metadata_allowed INTEGER NOT NULL,
                score INTEGER NOT NULL,
                comment_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                language TEXT NOT NULL,
                geo_hints TEXT NOT NULL,
                matched_patterns TEXT NOT NULL,
                raw_payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS candidate_requirements (
                candidate_id TEXT PRIMARY KEY,
                requirement_title TEXT NOT NULL,
                requirement_description TEXT NOT NULL,
                evidence_ids TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                detected_audience TEXT NOT NULL,
                detected_pain TEXT NOT NULL,
                initial_confidence REAL NOT NULL,
                duplicate_candidate_ids TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS requirements (
                requirement_id TEXT PRIMARY KEY,
                canonical_requirement TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                times_detected INTEGER NOT NULL,
                evidence_count INTEGER NOT NULL,
                subreddit_count INTEGER NOT NULL,
                geo_distribution TEXT NOT NULL,
                audience_segments TEXT NOT NULL,
                current_scores TEXT NOT NULL,
                previous_scores TEXT NOT NULL,
                research_history TEXT NOT NULL,
                decision_history TEXT NOT NULL,
                reopen_events TEXT NOT NULL,
                latest_recommendation TEXT,
                assigned_to TEXT,
                aliases TEXT NOT NULL,
                evidence_ids TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS research_runs (
                research_run_id TEXT PRIMARY KEY,
                requirement_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                input_evidence_ids TEXT NOT NULL,
                research_questions TEXT NOT NULL,
                findings TEXT NOT NULL,
                scores TEXT NOT NULL,
                geo_analysis TEXT NOT NULL,
                market_signal_analysis TEXT NOT NULL,
                existing_solution_analysis TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                limitations TEXT NOT NULL,
                changed_since_last_run TEXT NOT NULL,
                FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id)
            );

            CREATE TABLE IF NOT EXISTS research_queue (
                requirement_id TEXT NOT NULL,
                task_group_id TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL,
                reason TEXT NOT NULL,
                new_evidence_count INTEGER NOT NULL,
                previous_research_status TEXT,
                assigned_agent TEXT,
                locked_by TEXT,
                estimated_cost REAL NOT NULL,
                expected_completion_minutes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(requirement_id, task_group_id),
                FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id)
            );

            CREATE TABLE IF NOT EXISTS agent_activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                agent_role TEXT NOT NULL,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                input_refs TEXT NOT NULL,
                output_refs TEXT NOT NULL,
                error TEXT,
                retry_count INTEGER NOT NULL,
                cost_estimate REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pipeline_runs (
                pipeline_run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT NOT NULL,
                requirement_snapshot TEXT NOT NULL,
                queue_snapshot TEXT NOT NULL,
                agent_log_snapshot TEXT NOT NULL,
                summary TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_groups (
                task_group_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                domain TEXT,
                description TEXT NOT NULL DEFAULT '',
                input_dir TEXT NOT NULL,
                subreddits TEXT NOT NULL,
                keywords TEXT NOT NULL,
                negative_keywords TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_group_runs (
                task_group_run_id TEXT PRIMARY KEY,
                task_group_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                items_collected INTEGER NOT NULL,
                candidates_created INTEGER NOT NULL,
                requirements_found INTEGER NOT NULL,
                requirements_queued INTEGER NOT NULL,
                requirements_rejected INTEGER NOT NULL,
                summary TEXT NOT NULL,
                FOREIGN KEY(task_group_id) REFERENCES task_groups(task_group_id)
            );

            CREATE TABLE IF NOT EXISTS resource_config (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_group_config (
                task_group_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (task_group_id, key),
                FOREIGN KEY(task_group_id) REFERENCES task_groups(task_group_id)
            );

            CREATE TABLE IF NOT EXISTS experiment_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_group_id TEXT,
                task_group_run_id TEXT,
                agent_role TEXT NOT NULL,
                step_name TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS requirement_samples (
                sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_group_id TEXT,
                task_group_run_id TEXT,
                requirement_id TEXT NOT NULL,
                requirement_sentence TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id)
            );

            CREATE TABLE IF NOT EXISTS requirement_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                requirement_id TEXT NOT NULL,
                task_group_id TEXT,
                task_group_run_id TEXT,
                agent_id TEXT,
                agent_role TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id)
            );

            CREATE TABLE IF NOT EXISTS todo_jobs (
                todo_id INTEGER PRIMARY KEY AUTOINCREMENT,
                requirement_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                source_status TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id)
            );

            CREATE TABLE IF NOT EXISTS search_plans (
                plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_group_id TEXT NOT NULL,
                task_group_run_id TEXT NOT NULL,
                planner_agent_id TEXT NOT NULL,
                cycle_index INTEGER NOT NULL,
                input_description TEXT NOT NULL,
                search_goal TEXT NOT NULL,
                search_brief_json TEXT NOT NULL,
                assignments_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_group_id) REFERENCES task_groups(task_group_id)
            );

            CREATE TABLE IF NOT EXISTS search_insights (
                insight_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_group_id TEXT,
                task_group_run_id TEXT,
                requirement_id TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                insight_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id),
                FOREIGN KEY(research_run_id) REFERENCES research_runs(research_run_id)
            );
            """
        )
        self._ensure_column("raw_evidence", "task_group_id", "TEXT")
        self._ensure_column("raw_evidence", "task_group_run_id", "TEXT")
        self._ensure_column("candidate_requirements", "task_group_id", "TEXT")
        self._ensure_column("candidate_requirements", "task_group_run_id", "TEXT")
        self._ensure_column("requirements", "task_group_ids", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column("requirements", "task_group_run_ids", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column("task_groups", "description", "TEXT NOT NULL DEFAULT ''")
        self._ensure_group_scoped_research_queue()
        self._ensure_default_resource_config()
        self._ensure_default_app_config()
        self.conn.commit()

    def add_todo_job(self, requirement_id: str, note: str = "") -> None:
        requirement = self.get_requirement(requirement_id)
        if requirement is None:
            raise ValueError(f"Unknown requirement: {requirement_id}")
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO todo_jobs (
                requirement_id, title, source_status, status, note, created_at, updated_at
            )
            VALUES (?, ?, ?, 'open', ?, ?, ?)
            ON CONFLICT(requirement_id) DO UPDATE SET
                title=excluded.title,
                source_status=excluded.source_status,
                status='open',
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            (requirement_id, requirement.canonical_requirement, requirement.status.value, note.strip(), now, now),
        )
        self.log_requirement_event(
            requirement_id,
            requirement.task_group_ids[-1] if requirement.task_group_ids else None,
            requirement.task_group_run_ids[-1] if requirement.task_group_run_ids else None,
            "dashboard",
            "todo",
            "moved_to_todo",
            f"Moved {requirement_id} to todo list",
            {"todo_status": "open", "note": note.strip()},
        )

    def update_todo_status(self, requirement_id: str, status: str) -> None:
        now = utc_now()
        self.conn.execute(
            "UPDATE todo_jobs SET status=?, updated_at=? WHERE requirement_id=?",
            (status, now, requirement_id),
        )
        self.conn.commit()

    def list_todo_jobs(self, status: str | None = None) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT todo_id, requirement_id, title, source_status, status, note, created_at, updated_at
            FROM todo_jobs
            {where}
            ORDER BY updated_at DESC, todo_id DESC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_todo_job(self, requirement_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT todo_id, requirement_id, title, source_status, status, note, created_at, updated_at
            FROM todo_jobs
            WHERE requirement_id=?
            """,
            (requirement_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def save_search_plan(
        self,
        task_group_id: str,
        task_group_run_id: str,
        planner_agent_id: str,
        cycle_index: int,
        input_description: str,
        search_goal: str,
        search_brief: dict[str, Any],
        assignments: list[dict[str, Any]],
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO search_plans (
                task_group_id, task_group_run_id, planner_agent_id, cycle_index,
                input_description, search_goal, search_brief_json, assignments_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_group_id,
                task_group_run_id,
                planner_agent_id,
                cycle_index,
                input_description,
                search_goal,
                json.dumps(search_brief, sort_keys=True),
                json.dumps(assignments, sort_keys=True),
                utc_now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_search_plans(self, task_group_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if task_group_id:
            rows = self.conn.execute(
                """
                SELECT plan_id, task_group_id, task_group_run_id, planner_agent_id, cycle_index,
                       input_description, search_goal, search_brief_json, assignments_json, created_at
                FROM search_plans
                WHERE task_group_id=?
                ORDER BY plan_id DESC
                LIMIT ?
                """,
                (task_group_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT plan_id, task_group_id, task_group_run_id, planner_agent_id, cycle_index,
                       input_description, search_goal, search_brief_json, assignments_json, created_at
                FROM search_plans
                ORDER BY plan_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        plans = []
        for row in rows:
            item = dict(row)
            item["search_brief"] = json.loads(item.pop("search_brief_json"))
            item["assignments"] = json.loads(item.pop("assignments_json"))
            plans.append(item)
        return plans

    def save_search_insight(
        self,
        task_group_id: str | None,
        task_group_run_id: str | None,
        requirement_id: str,
        research_run_id: str,
        agent_id: str,
        insight_type: str,
        payload: dict[str, Any],
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO search_insights (
                task_group_id, task_group_run_id, requirement_id, research_run_id,
                agent_id, insight_type, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_group_id,
                task_group_run_id,
                requirement_id,
                research_run_id,
                agent_id,
                insight_type,
                json.dumps(payload, sort_keys=True),
                utc_now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_search_insights(self, task_group_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if task_group_id:
            rows = self.conn.execute(
                """
                SELECT insight_id, task_group_id, task_group_run_id, requirement_id,
                       research_run_id, agent_id, insight_type, payload_json, created_at
                FROM search_insights
                WHERE task_group_id=?
                ORDER BY insight_id DESC
                LIMIT ?
                """,
                (task_group_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT insight_id, task_group_id, task_group_run_id, requirement_id,
                       research_run_id, agent_id, insight_type, payload_json, created_at
                FROM search_insights
                ORDER BY insight_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._decode_json_column(dict(row), "payload_json") for row in rows]

    def upsert_evidence(self, evidence: RawEvidence) -> None:
        self._upsert("raw_evidence", "evidence_id", evidence)

    def upsert_candidate(self, candidate: CandidateRequirement) -> None:
        self._upsert("candidate_requirements", "candidate_id", candidate)

    def upsert_requirement(self, requirement: RequirementRecord) -> None:
        self._upsert("requirements", "requirement_id", requirement)

    def upsert_research_run(self, run: ResearchRun) -> None:
        self._upsert("research_runs", "research_run_id", run)

    def upsert_task_group(self, task_group: TaskGroup) -> None:
        self._upsert("task_groups", "task_group_id", task_group)

    def upsert_task_group_run(self, run: TaskGroupRun) -> None:
        self._upsert("task_group_runs", "task_group_run_id", run)

    def log_activity(self, log: AgentActivityLog) -> None:
        data = self._to_row(log)
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        self.conn.execute(
            f"INSERT INTO agent_activity_logs ({columns}) VALUES ({placeholders})",
            list(data.values()),
        )
        self.conn.commit()

    def log_experiment(
        self,
        task_group_id: str | None,
        task_group_run_id: str | None,
        agent_role: str,
        step_name: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO experiment_logs (
                task_group_id, task_group_run_id, agent_role, step_name,
                message, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_group_id,
                task_group_run_id,
                agent_role,
                step_name,
                message,
                json.dumps(payload or {}, sort_keys=True),
                utc_now(),
            ),
        )
        self.conn.commit()

    def save_requirement_sample(
        self,
        task_group_id: str | None,
        task_group_run_id: str | None,
        requirement_id: str,
        requirement_sentence: str,
        status: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO requirement_samples (
                task_group_id, task_group_run_id, requirement_id,
                requirement_sentence, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task_group_id,
                task_group_run_id,
                requirement_id,
                requirement_sentence.strip(),
                status,
                utc_now(),
            ),
        )
        self.conn.commit()

    def log_requirement_event(
        self,
        requirement_id: str,
        task_group_id: str | None,
        task_group_run_id: str | None,
        agent_id: str | None,
        agent_role: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO requirement_events (
                requirement_id, task_group_id, task_group_run_id,
                agent_id, agent_role, event_type, message, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                requirement_id,
                task_group_id,
                task_group_run_id,
                agent_id,
                agent_role,
                event_type,
                message,
                json.dumps(payload or {}, sort_keys=True),
                utc_now(),
            ),
        )
        self.conn.commit()

    def enqueue_research(
        self,
        requirement_id: str,
        priority: int,
        reason: str,
        new_evidence_count: int,
        previous_research_status: str | None,
        task_group_id: str | None = None,
        estimated_cost: float = 0.25,
        expected_completion_minutes: int = 20,
    ) -> None:
        now = utc_now()
        task_group_id = task_group_id if task_group_id is not None else self._latest_requirement_task_group_id(requirement_id)
        self.conn.execute(
            """
            INSERT INTO research_queue (
                requirement_id, task_group_id, priority, reason, new_evidence_count,
                previous_research_status, assigned_agent, locked_by,
                estimated_cost, expected_completion_minutes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
            ON CONFLICT(requirement_id, task_group_id) DO UPDATE SET
                priority=excluded.priority,
                reason=excluded.reason,
                new_evidence_count=excluded.new_evidence_count,
                previous_research_status=excluded.previous_research_status,
                estimated_cost=excluded.estimated_cost,
                expected_completion_minutes=excluded.expected_completion_minutes,
                updated_at=excluded.updated_at
            """,
            (
                requirement_id,
                task_group_id,
                priority,
                reason,
                new_evidence_count,
                previous_research_status,
                estimated_cost,
                expected_completion_minutes,
                now,
                now,
            ),
        )
        self.conn.commit()

    def lock_next_research(self, agent_id: str, eligible_task_group_ids: list[str] | None = None) -> str | None:
        if self.conn.in_transaction:
            return self._lock_next_research_in_current_transaction(agent_id, eligible_task_group_ids)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            requirement_id = self._lock_next_research_in_current_transaction(agent_id, eligible_task_group_ids)
            self.conn.commit()
            return requirement_id
        except Exception:
            self.conn.rollback()
            raise

    def _lock_next_research_in_current_transaction(
        self,
        agent_id: str,
        eligible_task_group_ids: list[str] | None = None,
    ) -> str | None:
        params: list[Any] = []
        group_filter = ""
        if eligible_task_group_ids is not None:
            if not eligible_task_group_ids:
                return None
            placeholders = ", ".join("?" for _ in eligible_task_group_ids)
            group_filter = f"AND research_queue.task_group_id IN ({placeholders})"
            params.extend(eligible_task_group_ids)
        row = self.conn.execute(
            f"""
            SELECT research_queue.requirement_id
            FROM research_queue
            WHERE research_queue.locked_by IS NULL
            {group_filter}
            ORDER BY research_queue.priority DESC, research_queue.created_at ASC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            return None
        requirement_id = row["requirement_id"]
        now = utc_now()
        self.conn.execute(
            "UPDATE research_queue SET locked_by=?, assigned_agent=?, updated_at=? WHERE requirement_id=?",
            (agent_id, agent_id, now, requirement_id),
        )
        return requirement_id

    def dequeue_research(self, requirement_id: str, task_group_id: str | None = None) -> None:
        if task_group_id is None:
            self.conn.execute("DELETE FROM research_queue WHERE requirement_id=?", (requirement_id,))
        else:
            self.conn.execute(
                "DELETE FROM research_queue WHERE requirement_id=? AND task_group_id=?",
                (requirement_id, task_group_id),
            )
        self.conn.commit()

    def dequeue_locked_research(self, requirement_id: str, agent_id: str) -> None:
        self.conn.execute(
            "DELETE FROM research_queue WHERE requirement_id=? AND locked_by=?",
            (requirement_id, agent_id),
        )
        self.conn.commit()

    def unlock_research(self, requirement_id: str, agent_id: str | None = None) -> None:
        if agent_id is None:
            self.conn.execute(
                "UPDATE research_queue SET locked_by=NULL, updated_at=? WHERE requirement_id=?",
                (utc_now(), requirement_id),
            )
        else:
            self.conn.execute(
                "UPDATE research_queue SET locked_by=NULL, updated_at=? WHERE requirement_id=? AND locked_by=?",
                (utc_now(), requirement_id, agent_id),
            )
        self.conn.commit()

    def update_queue_priority(self, requirement_id: str, priority: int) -> None:
        self.conn.execute(
            "UPDATE research_queue SET priority=?, updated_at=? WHERE requirement_id=?",
            (priority, utc_now(), requirement_id),
        )
        self.conn.commit()

    def update_requirement_status(self, requirement_id: str, status: RequirementStatus, reason: str) -> None:
        requirement = self.get_requirement(requirement_id)
        if requirement is None:
            raise ValueError(f"Unknown requirement: {requirement_id}")
        requirement.status = status
        requirement.decision_history.append({"at": utc_now(), "decision": status.value, "reason": reason})
        self.upsert_requirement(requirement)
        self.log_requirement_event(
            requirement_id,
            requirement.task_group_ids[-1] if requirement.task_group_ids else None,
            requirement.task_group_run_ids[-1] if requirement.task_group_run_ids else None,
            None,
            "human",
            "status_changed",
            f"Requirement status changed to {status.value}: {reason}",
            {"status": status.value, "reason": reason},
        )

    def merge_requirements(self, source_id: str, target_id: str) -> RequirementRecord:
        source = self.get_requirement(source_id)
        target = self.get_requirement(target_id)
        if source is None or target is None:
            raise ValueError("Both source and target requirements must exist")
        evidence_ids = sorted(set(target.evidence_ids + source.evidence_ids))
        target.previous_scores = target.current_scores
        target.times_detected += source.times_detected
        target.evidence_ids = evidence_ids
        target.evidence_count = len(evidence_ids)
        target.audience_segments = sorted(set(target.audience_segments + source.audience_segments))
        target.aliases = sorted(set(target.aliases + source.aliases + [source.canonical_requirement]))
        target.research_history = sorted(set(target.research_history + source.research_history))
        target.decision_history.extend(source.decision_history)
        target.decision_history.append({"at": utc_now(), "decision": "merged_requirement", "source": source_id})
        source.status = RequirementStatus.DUPLICATE_CANDIDATE
        source.decision_history.append({"at": utc_now(), "decision": "merged_into", "target": target_id})
        self.upsert_requirement(target)
        self.upsert_requirement(source)
        self.dequeue_research(source_id)
        return target

    def list_queue(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM research_queue ORDER BY priority DESC")]

    def list_evidence(self, evidence_ids: Iterable[str] | None = None) -> list[RawEvidence]:
        if evidence_ids is None:
            rows = self.conn.execute("SELECT * FROM raw_evidence ORDER BY fetched_at DESC").fetchall()
        else:
            ids = list(evidence_ids)
            if not ids:
                return []
            placeholders = ", ".join("?" for _ in ids)
            rows = self.conn.execute(
                f"SELECT * FROM raw_evidence WHERE evidence_id IN ({placeholders})",
                ids,
            ).fetchall()
        return [self._from_row(RawEvidence, row) for row in rows]

    def list_candidates(self, statuses: Iterable[str] | None = None) -> list[CandidateRequirement]:
        if statuses:
            values = list(statuses)
            placeholders = ", ".join("?" for _ in values)
            rows = self.conn.execute(
                f"SELECT * FROM candidate_requirements WHERE status IN ({placeholders}) ORDER BY created_at",
                values,
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM candidate_requirements ORDER BY created_at").fetchall()
        return [self._from_row(CandidateRequirement, row) for row in rows]

    def list_requirements(self) -> list[RequirementRecord]:
        rows = self.conn.execute("SELECT * FROM requirements ORDER BY last_seen DESC").fetchall()
        return [self._from_row(RequirementRecord, row) for row in rows]

    def get_requirement(self, requirement_id: str) -> RequirementRecord | None:
        row = self.conn.execute("SELECT * FROM requirements WHERE requirement_id=?", (requirement_id,)).fetchone()
        return None if row is None else self._from_row(RequirementRecord, row)

    def list_research_runs(self, requirement_id: str | None = None) -> list[ResearchRun]:
        if requirement_id:
            rows = self.conn.execute(
                "SELECT * FROM research_runs WHERE requirement_id=? ORDER BY started_at DESC",
                (requirement_id,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM research_runs ORDER BY started_at DESC").fetchall()
        return [self._from_row(ResearchRun, row) for row in rows]

    def dashboard_counts(self) -> dict[str, int]:
        return {
            "evidence": self._count("raw_evidence"),
            "candidates": self._count("candidate_requirements"),
            "requirements": self._count("requirements"),
            "queued": self._count("research_queue"),
            "research_runs": self._count("research_runs"),
            "activity_logs": self._count("agent_activity_logs"),
            "pipeline_runs": self._count("pipeline_runs"),
            "task_groups": self._count("task_groups"),
            "task_group_runs": self._count("task_group_runs"),
            "experiment_logs": self._count("experiment_logs"),
            "requirement_samples": self._count("requirement_samples"),
            "requirement_events": self._count("requirement_events"),
            "todo_jobs": self._count("todo_jobs"),
            "search_plans": self._count("search_plans"),
            "search_insights": self._count("search_insights"),
            "app_config": self._count("app_config"),
            "task_group_config": self._count("task_group_config"),
        }

    def get_resource_config(self) -> dict[str, int]:
        self._ensure_default_resource_config()
        rows = self.conn.execute("SELECT key, value FROM resource_config").fetchall()
        return {row["key"]: int(row["value"]) for row in rows}

    def update_resource_config(self, values: dict[str, int]) -> None:
        allowed = {"max_search_agents", "max_deep_research_agents", "max_report_agents"}
        now = utc_now()
        for key, value in values.items():
            if key not in allowed:
                continue
            self.conn.execute(
                """
                INSERT INTO resource_config (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, max(int(value), 0), now),
            )
        self.conn.commit()

    def get_app_config(self) -> dict[str, str]:
        self._ensure_default_app_config()
        rows = self.conn.execute("SELECT key, value FROM app_config").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def update_app_config(self, values: dict[str, str]) -> None:
        allowed = {
            "collector_enabled",
            "collector_command",
            "collector_limit",
            "collector_timeout_seconds",
            "model_search",
            "model_pool",
            "model_deep_research",
            "model_report",
        }
        now = utc_now()
        for key, value in values.items():
            if key not in allowed:
                continue
            self.conn.execute(
                """
                INSERT INTO app_config (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, str(value), now),
            )
        self.conn.commit()

    def get_task_group_config(self, task_group_id: str) -> dict[str, str]:
        config = self.get_app_config()
        rows = self.conn.execute(
            "SELECT key, value FROM task_group_config WHERE task_group_id=?",
            (task_group_id,),
        ).fetchall()
        config.update({row["key"]: row["value"] for row in rows})
        return config

    def update_task_group_config(self, task_group_id: str, values: dict[str, str]) -> None:
        allowed = {
            "collector_enabled",
            "collector_command",
            "collector_limit",
            "collector_timeout_seconds",
            "model_search",
            "model_pool",
            "model_deep_research",
            "model_report",
        }
        now = utc_now()
        for key, value in values.items():
            if key not in allowed:
                continue
            self.conn.execute(
                """
                INSERT INTO task_group_config (task_group_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_group_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (task_group_id, key, str(value), now),
            )
        self.conn.commit()

    def list_task_groups(self, statuses: Iterable[str] | None = None) -> list[TaskGroup]:
        if statuses:
            values = list(statuses)
            placeholders = ", ".join("?" for _ in values)
            rows = self.conn.execute(
                f"SELECT * FROM task_groups WHERE status IN ({placeholders}) ORDER BY updated_at DESC",
                values,
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM task_groups ORDER BY updated_at DESC").fetchall()
        return [self._from_row(TaskGroup, row) for row in rows]

    def get_task_group(self, task_group_id: str) -> TaskGroup | None:
        row = self.conn.execute("SELECT * FROM task_groups WHERE task_group_id=?", (task_group_id,)).fetchone()
        return None if row is None else self._from_row(TaskGroup, row)

    def create_task_group(
        self,
        name: str,
        task_type: TaskGroupType,
        domain: str | None,
        input_dir: str,
        description: str = "",
        subreddits: list[str] | None = None,
        keywords: list[str] | None = None,
        negative_keywords: list[str] | None = None,
    ) -> TaskGroup:
        now = utc_now()
        slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")[:36] or "task"
        task_group_id = f"tg_{slug}_{self._count('task_groups') + 1:04d}"
        task_group = TaskGroup(
            task_group_id=task_group_id,
            name=name,
            task_type=task_type,
            status=TaskGroupStatus.STOPPED,
            domain=domain,
            description=description,
            input_dir=input_dir,
            subreddits=subreddits or [],
            keywords=keywords or [],
            negative_keywords=negative_keywords or [],
            created_at=now,
            updated_at=now,
        )
        self.upsert_task_group(task_group)
        return task_group

    def update_task_group_status(self, task_group_id: str, status: TaskGroupStatus) -> None:
        task_group = self.get_task_group(task_group_id)
        if task_group is None:
            raise ValueError(f"Unknown task group: {task_group_id}")
        task_group.status = status
        task_group.updated_at = utc_now()
        self.upsert_task_group(task_group)

    def list_task_group_runs(self, task_group_id: str | None = None, limit: int = 50) -> list[TaskGroupRun]:
        if task_group_id:
            rows = self.conn.execute(
                "SELECT * FROM task_group_runs WHERE task_group_id=? ORDER BY started_at DESC LIMIT ?",
                (task_group_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM task_group_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._from_row(TaskGroupRun, row) for row in rows]

    def list_activity_logs(self, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, agent_id, agent_role, task_id, status, started_at, completed_at,
                   input_refs, output_refs, error, retry_count, cost_estimate
            FROM agent_activity_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["input_refs"] = json.loads(item["input_refs"])
            item["output_refs"] = json.loads(item["output_refs"])
            result.append(item)
        return result

    def list_experiment_logs(
        self,
        task_group_id: str | None = None,
        task_group_run_id: str | None = None,
        agent_role: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if task_group_id:
            clauses.append("task_group_id = ?")
            params.append(task_group_id)
        if task_group_run_id:
            clauses.append("task_group_run_id = ?")
            params.append(task_group_run_id)
        if agent_role:
            clauses.append("agent_role = ?")
            params.append(agent_role)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT log_id, task_group_id, task_group_run_id, agent_role,
                   step_name, message, payload_json, created_at
            FROM experiment_logs
            {where}
            ORDER BY log_id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [self._decode_json_column(dict(row), "payload_json") for row in rows]

    def list_requirement_samples(
        self,
        task_group_id: str | None = None,
        task_group_run_id: str | None = None,
        requirement_id: str | None = None,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if task_group_id:
            clauses.append("task_group_id = ?")
            params.append(task_group_id)
        if task_group_run_id:
            clauses.append("task_group_run_id = ?")
            params.append(task_group_run_id)
        if requirement_id:
            clauses.append("requirement_id = ?")
            params.append(requirement_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT sample_id, task_group_id, task_group_run_id, requirement_id,
                   requirement_sentence, status, created_at
            FROM requirement_samples
            {where}
            ORDER BY sample_id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_requirement_events(self, requirement_id: str, limit: int = 300) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT event_id, requirement_id, task_group_id, task_group_run_id,
                   agent_id, agent_role, event_type, message, payload_json, created_at
            FROM requirement_events
            WHERE requirement_id=?
            ORDER BY event_id ASC
            LIMIT ?
            """,
            (requirement_id, limit),
        ).fetchall()
        return [self._decode_json_column(dict(row), "payload_json") for row in rows]

    def list_agent_logs(self, agent_role: str | None = None, agent_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if agent_role:
            clauses.append("agent_role = ?")
            params.append(agent_role)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT id, agent_id, agent_role, task_id, status, started_at, completed_at,
                   input_refs, output_refs, error, retry_count, cost_estimate
            FROM agent_activity_logs
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["input_refs"] = json.loads(item["input_refs"])
            item["output_refs"] = json.loads(item["output_refs"])
            result.append(item)
        return result

    def save_pipeline_run(
        self,
        pipeline_run_id: str,
        started_at: str,
        completed_at: str,
        status: str,
        result: dict[str, Any],
        summary: str,
    ) -> None:
        requirements = [asdict(item) for item in self.list_requirements()]
        queue = self.list_queue()
        logs = self.list_activity_logs(50)
        self.conn.execute(
            """
            INSERT INTO pipeline_runs (
                pipeline_run_id, started_at, completed_at, status, result,
                requirement_snapshot, queue_snapshot, agent_log_snapshot, summary
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pipeline_run_id) DO UPDATE SET
                completed_at=excluded.completed_at,
                status=excluded.status,
                result=excluded.result,
                requirement_snapshot=excluded.requirement_snapshot,
                queue_snapshot=excluded.queue_snapshot,
                agent_log_snapshot=excluded.agent_log_snapshot,
                summary=excluded.summary
            """,
            (
                pipeline_run_id,
                started_at,
                completed_at,
                status,
                json.dumps(result, sort_keys=True),
                json.dumps(requirements, default=str, sort_keys=True),
                json.dumps(queue, default=str, sort_keys=True),
                json.dumps(logs, default=str, sort_keys=True),
                summary,
            ),
        )
        self.conn.commit()

    def list_pipeline_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT pipeline_run_id, started_at, completed_at, status, result, summary
            FROM pipeline_runs
            ORDER BY completed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["result"] = json.loads(item["result"])
            result.append(item)
        return result

    def get_pipeline_run(self, pipeline_run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pipeline_runs WHERE pipeline_run_id=?",
            (pipeline_run_id,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        for key in ["result", "requirement_snapshot", "queue_snapshot", "agent_log_snapshot"]:
            item[key] = json.loads(item[key])
        return item

    def _count(self, table: str) -> int:
        return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _ensure_group_scoped_research_queue(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(research_queue)").fetchall()}
        primary_keys = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(research_queue)").fetchall()
            if row["pk"]
        }
        if "task_group_id" in columns and primary_keys == {"requirement_id", "task_group_id"}:
            return
        rows = self.conn.execute("SELECT * FROM research_queue").fetchall()
        self.conn.execute("ALTER TABLE research_queue RENAME TO research_queue_legacy")
        self.conn.execute(
            """
            CREATE TABLE research_queue (
                requirement_id TEXT NOT NULL,
                task_group_id TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL,
                reason TEXT NOT NULL,
                new_evidence_count INTEGER NOT NULL,
                previous_research_status TEXT,
                assigned_agent TEXT,
                locked_by TEXT,
                estimated_cost REAL NOT NULL,
                expected_completion_minutes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(requirement_id, task_group_id),
                FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id)
            )
            """
        )
        for row in rows:
            item = dict(row)
            task_group_id = str(item.get("task_group_id") or self._latest_requirement_task_group_id(str(item["requirement_id"])))
            self.conn.execute(
                """
                INSERT OR REPLACE INTO research_queue (
                    requirement_id, task_group_id, priority, reason, new_evidence_count,
                    previous_research_status, assigned_agent, locked_by,
                    estimated_cost, expected_completion_minutes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["requirement_id"],
                    task_group_id,
                    item["priority"],
                    item["reason"],
                    item["new_evidence_count"],
                    item["previous_research_status"],
                    item["assigned_agent"],
                    item["locked_by"],
                    item["estimated_cost"],
                    item["expected_completion_minutes"],
                    item["created_at"],
                    item["updated_at"],
                ),
            )
        self.conn.execute("DROP TABLE research_queue_legacy")

    def _latest_requirement_task_group_id(self, requirement_id: str) -> str:
        row = self.conn.execute("SELECT task_group_ids FROM requirements WHERE requirement_id=?", (requirement_id,)).fetchone()
        if row is None:
            return ""
        try:
            task_group_ids = json.loads(row["task_group_ids"])
        except json.JSONDecodeError:
            return ""
        return str(task_group_ids[-1]) if task_group_ids else ""

    def _ensure_default_resource_config(self) -> None:
        now = utc_now()
        defaults = {
            "max_search_agents": 3,
            "max_deep_research_agents": 1,
            "max_report_agents": 1,
        }
        for key, value in defaults.items():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO resource_config (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, value, now),
            )

    def _ensure_default_app_config(self) -> None:
        now = utc_now()
        defaults = {
            "collector_enabled": "0",
            "collector_command": "opencli reddit search",
            "collector_limit": "25",
            "collector_timeout_seconds": "120",
            "model_search": "deepseek-v4-flash",
            "model_pool": "deepseek-v4-flash",
            "model_deep_research": "deepseek-v4-flash",
            "model_report": "deepseek-v4-pro",
        }
        for key, value in defaults.items():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO app_config (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, value, now),
            )

    def _upsert(self, table: str, pk: str, item: Any) -> None:
        data = self._to_row(item)
        columns = list(data)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != pk)
        self.conn.execute(
            f"""
            INSERT INTO {table} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT({pk}) DO UPDATE SET {updates}
            """,
            [data[column] for column in columns],
        )
        self.conn.commit()

    def _to_row(self, item: Any) -> dict[str, Any]:
        if not is_dataclass(item):
            raise TypeError("Storage only persists dataclass instances")
        row = asdict(item)
        for key, value in list(row.items()):
            if isinstance(value, RequirementStatus):
                row[key] = value.value
            elif isinstance(value, (TaskGroupStatus, TaskGroupType)):
                row[key] = value.value
            elif isinstance(value, (list, dict)):
                row[key] = json.dumps(value, sort_keys=True)
            elif isinstance(value, bool):
                row[key] = int(value)
        return row

    def _from_row(self, model: type[T], row: sqlite3.Row) -> T:
        data = dict(row)
        json_fields = {
            "geo_hints",
            "matched_patterns",
            "raw_payload",
            "evidence_ids",
            "task_group_ids",
            "task_group_run_ids",
            "detected_audience",
            "duplicate_candidate_ids",
            "geo_distribution",
            "audience_segments",
            "current_scores",
            "previous_scores",
            "research_history",
            "decision_history",
            "reopen_events",
            "aliases",
            "input_evidence_ids",
            "research_questions",
            "findings",
            "scores",
            "geo_analysis",
            "market_signal_analysis",
            "existing_solution_analysis",
            "limitations",
            "changed_since_last_run",
            "input_refs",
            "output_refs",
            "subreddits",
            "keywords",
            "negative_keywords",
        }
        for field in fields(model):
            if field.name in json_fields and isinstance(data.get(field.name), str):
                data[field.name] = json.loads(data[field.name])
            if field.name == "author_metadata_allowed":
                data[field.name] = bool(data[field.name])
            if field.name == "status" and data.get(field.name) is not None:
                if model is TaskGroup or model is TaskGroupRun:
                    data[field.name] = TaskGroupStatus(data[field.name])
                else:
                    data[field.name] = RequirementStatus(data[field.name])
            if field.name == "task_type" and data.get(field.name) is not None:
                data[field.name] = TaskGroupType(data[field.name])
        return model(**{field.name: data[field.name] for field in fields(model)})

    def _decode_json_column(self, row: dict[str, Any], key: str) -> dict[str, Any]:
        row[key] = json.loads(row[key]) if row.get(key) else {}
        return row
