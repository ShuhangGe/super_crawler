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
    utc_now,
)

T = TypeVar("T")

DEFAULT_DB_PATH = Path("data/super_crawler.sqlite3")


class Storage:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

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
                requirement_id TEXT PRIMARY KEY,
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
            """
        )
        self.conn.commit()

    def upsert_evidence(self, evidence: RawEvidence) -> None:
        self._upsert("raw_evidence", "evidence_id", evidence)

    def upsert_candidate(self, candidate: CandidateRequirement) -> None:
        self._upsert("candidate_requirements", "candidate_id", candidate)

    def upsert_requirement(self, requirement: RequirementRecord) -> None:
        self._upsert("requirements", "requirement_id", requirement)

    def upsert_research_run(self, run: ResearchRun) -> None:
        self._upsert("research_runs", "research_run_id", run)

    def log_activity(self, log: AgentActivityLog) -> None:
        data = self._to_row(log)
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        self.conn.execute(
            f"INSERT INTO agent_activity_logs ({columns}) VALUES ({placeholders})",
            list(data.values()),
        )
        self.conn.commit()

    def enqueue_research(
        self,
        requirement_id: str,
        priority: int,
        reason: str,
        new_evidence_count: int,
        previous_research_status: str | None,
        estimated_cost: float = 0.25,
        expected_completion_minutes: int = 20,
    ) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO research_queue (
                requirement_id, priority, reason, new_evidence_count,
                previous_research_status, assigned_agent, locked_by,
                estimated_cost, expected_completion_minutes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
            ON CONFLICT(requirement_id) DO UPDATE SET
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

    def lock_next_research(self, agent_id: str) -> str | None:
        row = self.conn.execute(
            """
            SELECT requirement_id FROM research_queue
            WHERE locked_by IS NULL
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        requirement_id = row["requirement_id"]
        now = utc_now()
        self.conn.execute(
            "UPDATE research_queue SET locked_by=?, assigned_agent=?, updated_at=? WHERE requirement_id=?",
            (agent_id, agent_id, now, requirement_id),
        )
        self.conn.commit()
        return requirement_id

    def dequeue_research(self, requirement_id: str) -> None:
        self.conn.execute("DELETE FROM research_queue WHERE requirement_id=?", (requirement_id,))
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
        }

    def list_activity_logs(self, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT agent_id, agent_role, task_id, status, started_at, completed_at,
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

    def _count(self, table: str) -> int:
        return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

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
        }
        for field in fields(model):
            if field.name in json_fields and isinstance(data.get(field.name), str):
                data[field.name] = json.loads(data[field.name])
            if field.name == "author_metadata_allowed":
                data[field.name] = bool(data[field.name])
            if field.name == "status" and data.get(field.name) is not None:
                data[field.name] = RequirementStatus(data[field.name])
        return model(**{field.name: data[field.name] for field in fields(model)})
