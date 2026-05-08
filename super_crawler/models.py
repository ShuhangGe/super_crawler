from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class RequirementStatus(StrEnum):
    NEW_CANDIDATE = "new_candidate"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"
    QUEUED_FOR_RESEARCH = "queued_for_research"
    RESEARCHING = "researching"
    VALIDATED = "validated"
    REJECTED = "rejected"
    WATCHING = "watching"
    REOPENED = "reopened"
    ARCHIVED = "archived"


class SignalLabel(StrEnum):
    WEAK = "weak_signal"
    WATCH = "watch_signal"
    PROMISING = "promising_signal"
    VALIDATED = "validated_signal"
    HIGH_PRIORITY = "high_priority_signal"


class TaskGroupStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskGroupType(StrEnum):
    GENERAL = "general_search"
    DOMAIN = "domain_search"


@dataclass(slots=True)
class RawEvidence:
    evidence_id: str
    source: str
    source_url: str
    subreddit: str
    post_id: str | None
    comment_id: str | None
    title: str
    body: str
    author_metadata_allowed: bool
    score: int
    comment_count: int
    created_at: str
    fetched_at: str
    language: str
    geo_hints: list[str] = field(default_factory=list)
    matched_patterns: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    task_group_id: str | None = None
    task_group_run_id: str | None = None


@dataclass(slots=True)
class CandidateRequirement:
    candidate_id: str
    requirement_title: str
    requirement_description: str
    evidence_ids: list[str]
    signal_type: str
    detected_audience: list[str]
    detected_pain: str
    initial_confidence: float
    duplicate_candidate_ids: list[str]
    status: RequirementStatus
    created_at: str
    updated_at: str
    task_group_id: str | None = None
    task_group_run_id: str | None = None


@dataclass(slots=True)
class RequirementRecord:
    requirement_id: str
    canonical_requirement: str
    description: str
    status: RequirementStatus
    first_seen: str
    last_seen: str
    times_detected: int
    evidence_count: int
    subreddit_count: int
    geo_distribution: list[dict[str, Any]]
    audience_segments: list[str]
    current_scores: dict[str, float]
    previous_scores: dict[str, float]
    research_history: list[str]
    decision_history: list[dict[str, Any]]
    reopen_events: list[dict[str, Any]]
    latest_recommendation: str | None
    assigned_to: str | None = None
    aliases: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    task_group_ids: list[str] = field(default_factory=list)
    task_group_run_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResearchRun:
    research_run_id: str
    requirement_id: str
    agent_id: str
    started_at: str
    completed_at: str | None
    input_evidence_ids: list[str]
    research_questions: list[str]
    findings: dict[str, Any]
    scores: dict[str, float]
    geo_analysis: list[dict[str, Any]]
    market_signal_analysis: dict[str, Any]
    existing_solution_analysis: dict[str, Any]
    recommendation: str
    limitations: list[str]
    changed_since_last_run: dict[str, Any]


@dataclass(slots=True)
class AgentActivityLog:
    agent_id: str
    agent_role: str
    task_id: str
    status: str
    started_at: str
    completed_at: str | None
    input_refs: list[str]
    output_refs: list[str]
    error: str | None
    retry_count: int
    cost_estimate: float


@dataclass(slots=True)
class TaskGroup:
    task_group_id: str
    name: str
    task_type: TaskGroupType
    status: TaskGroupStatus
    domain: str | None
    input_dir: str
    subreddits: list[str]
    keywords: list[str]
    negative_keywords: list[str]
    created_at: str
    updated_at: str


@dataclass(slots=True)
class TaskGroupRun:
    task_group_run_id: str
    task_group_id: str
    started_at: str
    completed_at: str | None
    status: TaskGroupStatus
    items_collected: int
    candidates_created: int
    requirements_found: int
    requirements_queued: int
    requirements_rejected: int
    summary: str
