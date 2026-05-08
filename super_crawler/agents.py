from __future__ import annotations

import hashlib
from collections import Counter
from statistics import mean
from typing import Any

from .models import (
    AgentActivityLog,
    CandidateRequirement,
    RawEvidence,
    RequirementRecord,
    RequirementStatus,
    ResearchRun,
    SignalLabel,
    utc_now,
)
from .storage import Storage
from .text import (
    infer_audience,
    infer_geo,
    make_requirement_title,
    matched_signal_patterns,
    normalize_requirement,
    simple_similarity,
)


RESEARCH_QUESTIONS = [
    "How often does this requirement appear?",
    "How many subreddits discuss it?",
    "Are users emotionally invested in the problem?",
    "Are users already paying for solutions?",
    "What are current alternatives?",
    "What do users dislike about existing solutions?",
    "Is this a product, content, service, affiliate, or community opportunity?",
]


class BaseAgent:
    role = "base"

    def __init__(self, storage: Storage, agent_id: str):
        self.storage = storage
        self.agent_id = agent_id

    def log(self, task_id: str, status: str, input_refs: list[str], output_refs: list[str], error: str | None = None) -> None:
        now = utc_now()
        self.storage.log_activity(
            AgentActivityLog(
                agent_id=self.agent_id,
                agent_role=self.role,
                task_id=task_id,
                status=status,
                started_at=now,
                completed_at=now,
                input_refs=input_refs,
                output_refs=output_refs,
                error=error,
                retry_count=0,
                cost_estimate=0.0,
            )
        )


class DiscoveryAgent(BaseAgent):
    role = "discovery"

    def ingest_reddit_items(self, items: list[dict[str, Any]]) -> list[CandidateRequirement]:
        candidates: list[CandidateRequirement] = []
        for item in items:
            text = f"{item.get('title', '')}\n{item.get('body', '')}"
            matched = matched_signal_patterns(text)
            if not matched:
                continue

            evidence_id = self._id("ev", item.get("source_url", "") + text)
            fetched_at = utc_now()
            evidence = RawEvidence(
                evidence_id=evidence_id,
                source="reddit",
                source_url=item.get("source_url", ""),
                subreddit=item.get("subreddit", "unknown"),
                post_id=item.get("post_id"),
                comment_id=item.get("comment_id"),
                title=item.get("title", ""),
                body=item.get("body", ""),
                author_metadata_allowed=bool(item.get("author_metadata_allowed", False)),
                score=int(item.get("score", 0)),
                comment_count=int(item.get("comment_count", 0)),
                created_at=item.get("created_at", fetched_at),
                fetched_at=fetched_at,
                language=item.get("language", "en"),
                geo_hints=infer_geo(text, item.get("subreddit", "")),
                matched_patterns=matched,
                raw_payload=item,
            )
            self.storage.upsert_evidence(evidence)

            candidate = CandidateRequirement(
                candidate_id=self._id("cand", evidence_id + normalize_requirement(text)),
                requirement_title=make_requirement_title(evidence.title, evidence.body),
                requirement_description=text.strip()[:600],
                evidence_ids=[evidence_id],
                signal_type=self._signal_type(matched),
                detected_audience=infer_audience(text),
                detected_pain=self._pain_label(text),
                initial_confidence=self._confidence(evidence, matched),
                duplicate_candidate_ids=[],
                status=RequirementStatus.NEW_CANDIDATE,
                created_at=fetched_at,
                updated_at=fetched_at,
            )
            self.storage.upsert_candidate(candidate)
            candidates.append(candidate)

        self.log("ingest_reddit_items", "completed", [], [candidate.candidate_id for candidate in candidates])
        return candidates

    def _confidence(self, evidence: RawEvidence, matched: list[str]) -> float:
        score = 0.25 + 0.08 * len(matched) + min(evidence.score, 100) / 500 + min(evidence.comment_count, 100) / 500
        return round(min(score, 0.95), 2)

    def _signal_type(self, matched: list[str]) -> str:
        if "complaint" in matched:
            return "repeated complaint"
        if "tool_request" in matched:
            return "tool request"
        if "workaround" in matched:
            return "workaround behavior"
        return matched[0].replace("_", " ")

    def _pain_label(self, text: str) -> str:
        lower = text.lower()
        if any(term in lower for term in ["hate", "tired", "frustrating", "impossible", "urgent"]):
            return "high"
        if any(term in lower for term in ["hard", "annoying", "struggle", "manual"]):
            return "medium"
        return "low"

    def _id(self, prefix: str, value: str) -> str:
        return f"{prefix}_{hashlib.sha1(value.encode()).hexdigest()[:12]}"


class PoolManagerAgent(BaseAgent):
    role = "pool_manager"

    def reconcile_candidates(self) -> list[RequirementRecord]:
        candidates = self.storage.list_candidates([RequirementStatus.NEW_CANDIDATE.value])
        requirements = self.storage.list_requirements()
        changed: list[RequirementRecord] = []
        for candidate in candidates:
            match = self._find_match(candidate, requirements)
            if match:
                record = self._merge_candidate(match, candidate)
                candidate.status = RequirementStatus.DUPLICATE_CANDIDATE
            else:
                record = self._create_requirement(candidate)
                requirements.append(record)

            record = self._prioritize(record)
            self.storage.upsert_requirement(record)
            self.storage.upsert_candidate(candidate)
            if record.status in {RequirementStatus.QUEUED_FOR_RESEARCH, RequirementStatus.REOPENED}:
                self.storage.enqueue_research(
                    requirement_id=record.requirement_id,
                    priority=int(record.current_scores["overall_score"]),
                    reason=self._queue_reason(record),
                    new_evidence_count=len(candidate.evidence_ids),
                    previous_research_status=record.latest_recommendation,
                )
            changed.append(record)

        self.log("reconcile_candidates", "completed", [c.candidate_id for c in candidates], [r.requirement_id for r in changed])
        return changed

    def _find_match(self, candidate: CandidateRequirement, requirements: list[RequirementRecord]) -> RequirementRecord | None:
        best: tuple[float, RequirementRecord | None] = (0.0, None)
        for requirement in requirements:
            similarity = simple_similarity(candidate.requirement_title, requirement.canonical_requirement)
            if similarity > best[0]:
                best = (similarity, requirement)
        return best[1] if best[0] >= 0.35 else None

    def _create_requirement(self, candidate: CandidateRequirement) -> RequirementRecord:
        now = utc_now()
        requirement_id = f"REQ-{now[:4]}-{self._sequence_id()}"
        evidence = self.storage.list_evidence(candidate.evidence_ids)
        return RequirementRecord(
            requirement_id=requirement_id,
            canonical_requirement=candidate.requirement_title,
            description=candidate.requirement_description,
            status=RequirementStatus.NEW_CANDIDATE,
            first_seen=candidate.created_at,
            last_seen=candidate.updated_at,
            times_detected=1,
            evidence_count=len(candidate.evidence_ids),
            subreddit_count=len({item.subreddit for item in evidence}),
            geo_distribution=self._geo_distribution(evidence),
            audience_segments=candidate.detected_audience,
            current_scores={},
            previous_scores={},
            research_history=[],
            decision_history=[{"at": now, "decision": "created", "source_candidate": candidate.candidate_id}],
            reopen_events=[],
            latest_recommendation=None,
            aliases=[],
            evidence_ids=list(candidate.evidence_ids),
        )

    def _merge_candidate(self, requirement: RequirementRecord, candidate: CandidateRequirement) -> RequirementRecord:
        evidence_ids = sorted(set(requirement.evidence_ids + candidate.evidence_ids))
        evidence = self.storage.list_evidence(evidence_ids)
        now = utc_now()
        requirement.previous_scores = requirement.current_scores
        requirement.times_detected += 1
        requirement.evidence_ids = evidence_ids
        requirement.evidence_count = len(evidence_ids)
        requirement.subreddit_count = len({item.subreddit for item in evidence})
        requirement.last_seen = candidate.updated_at
        requirement.geo_distribution = self._geo_distribution(evidence)
        requirement.audience_segments = sorted(set(requirement.audience_segments + candidate.detected_audience))
        requirement.aliases = sorted(set(requirement.aliases + [candidate.requirement_title]))
        requirement.decision_history.append({"at": now, "decision": "merged_candidate", "source_candidate": candidate.candidate_id})
        return requirement

    def _prioritize(self, requirement: RequirementRecord) -> RequirementRecord:
        scores = score_requirement(requirement, self.storage.list_evidence(requirement.evidence_ids))
        requirement.current_scores = scores
        label = scores["overall_label"]
        if requirement.status in {RequirementStatus.VALIDATED, RequirementStatus.REJECTED, RequirementStatus.ARCHIVED}:
            return requirement
        if scores["overall_score"] >= 25 or label in {
            SignalLabel.WATCH.value,
            SignalLabel.PROMISING.value,
            SignalLabel.VALIDATED.value,
            SignalLabel.HIGH_PRIORITY.value,
        }:
            requirement.status = RequirementStatus.QUEUED_FOR_RESEARCH
        else:
            requirement.status = RequirementStatus.NEEDS_MORE_EVIDENCE
        return requirement

    def _queue_reason(self, requirement: RequirementRecord) -> str:
        if requirement.reopen_events:
            return requirement.reopen_events[-1].get("reason", "new evidence justifies another investigation")
        return f"{requirement.current_scores['overall_label']} with {requirement.evidence_count} evidence item(s)"

    def _sequence_id(self) -> str:
        return f"{len(self.storage.list_requirements()) + 1:06d}"

    def _geo_distribution(self, evidence: list[RawEvidence]) -> list[dict[str, Any]]:
        counts: Counter[str] = Counter()
        for item in evidence:
            counts.update(item.geo_hints)
        total = max(sum(counts.values()), 1)
        return [
            {"region": region, "confidence": round(count / total, 2), "evidence_count": count}
            for region, count in counts.most_common()
        ]


class ChangeDetectionAgent(BaseAgent):
    role = "change_detection"

    def evaluate_reopenings(self) -> list[RequirementRecord]:
        reopened: list[RequirementRecord] = []
        for requirement in self.storage.list_requirements():
            if requirement.status not in {RequirementStatus.WATCHING, RequirementStatus.VALIDATED, RequirementStatus.REJECTED}:
                continue
            change = detect_change(requirement)
            if change["should_reopen"]:
                requirement.status = RequirementStatus.REOPENED
                requirement.reopen_events.append({"at": utc_now(), "reason": change["reason"], "change": change})
                requirement.decision_history.append({"at": utc_now(), "decision": "reopened", "reason": change["reason"]})
                self.storage.upsert_requirement(requirement)
                self.storage.enqueue_research(
                    requirement.requirement_id,
                    priority=int(requirement.current_scores.get("overall_score", 50)),
                    reason=change["reason"],
                    new_evidence_count=int(change.get("new_evidence_count", 0)),
                    previous_research_status=requirement.latest_recommendation,
                )
                reopened.append(requirement)
        self.log("evaluate_reopenings", "completed", [], [item.requirement_id for item in reopened])
        return reopened


class DeepResearchAgent(BaseAgent):
    role = "deep_research"

    def run_next(self) -> ResearchRun | None:
        requirement_id = self.storage.lock_next_research(self.agent_id)
        if requirement_id is None:
            self.log("run_next", "skipped", [], [])
            return None
        requirement = self.storage.get_requirement(requirement_id)
        if requirement is None:
            self.log("run_next", "failed", [requirement_id], [], "requirement not found")
            return None
        return self.research(requirement)

    def research(self, requirement: RequirementRecord) -> ResearchRun:
        started = utc_now()
        requirement.status = RequirementStatus.RESEARCHING
        requirement.assigned_to = self.agent_id
        self.storage.upsert_requirement(requirement)

        evidence = self.storage.list_evidence(requirement.evidence_ids)
        scores = score_requirement(requirement, evidence)
        positives = [item for item in evidence if item.matched_patterns]
        geo = requirement.geo_distribution
        alternatives = infer_existing_solutions(evidence)
        opportunities = infer_opportunities(requirement, evidence)
        recommendation = make_recommendation(scores)
        run_id = f"run_{hashlib.sha1((requirement.requirement_id + started).encode()).hexdigest()[:12]}"

        run = ResearchRun(
            research_run_id=run_id,
            requirement_id=requirement.requirement_id,
            agent_id=self.agent_id,
            started_at=started,
            completed_at=utc_now(),
            input_evidence_ids=requirement.evidence_ids,
            research_questions=RESEARCH_QUESTIONS,
            findings={
                "requirement_summary": requirement.canonical_requirement,
                "why_real": summarize_why_real(positives),
                "why_noise": summarize_why_noise(requirement, evidence),
                "audience": requirement.audience_segments,
                "pain_intensity": classify_pain(scores["pain_intensity_score"]),
                "willingness_to_pay_signals": find_payment_signals(evidence),
                "product_opportunities": opportunities["product"],
                "content_opportunities": opportunities["content"],
                "suggested_next_validation_step": next_validation_step(scores),
            },
            scores=scores,
            geo_analysis=geo,
            market_signal_analysis={
                "signal_size": signal_size(scores["overall_score"]),
                "frequency": requirement.evidence_count,
                "subreddit_spread": requirement.subreddit_count,
                "engagement_score": scores["engagement_score"],
                "monetization_signal": classify_pain(scores["monetization_score"]),
            },
            existing_solution_analysis=alternatives,
            recommendation=recommendation,
            limitations=[
                "Reddit evidence is not a total market-size estimate.",
                "Geography is inferred from text, subreddit, currency, and local references.",
                "This MVP uses deterministic heuristics until live Reddit and LLM integrations are configured.",
            ],
            changed_since_last_run=detect_change(requirement),
        )
        self.storage.upsert_research_run(run)

        requirement.status = self._status_from_scores(scores)
        requirement.assigned_to = None
        requirement.research_history.append(run.research_run_id)
        requirement.latest_recommendation = recommendation
        requirement.current_scores = scores
        requirement.decision_history.append({"at": utc_now(), "decision": requirement.status.value, "research_run": run_id})
        self.storage.upsert_requirement(requirement)
        self.storage.dequeue_research(requirement.requirement_id)
        self.log("run_next", "completed", [requirement.requirement_id], [run.research_run_id])
        return run

    def _status_from_scores(self, scores: dict[str, Any]) -> RequirementStatus:
        score = scores["overall_score"]
        if score >= 72:
            return RequirementStatus.VALIDATED
        if score >= 45:
            return RequirementStatus.WATCHING
        return RequirementStatus.REJECTED


class ReportAgent(BaseAgent):
    role = "report"

    def daily_report(self) -> str:
        requirements = self.storage.list_requirements()
        runs = self.storage.list_research_runs()
        queue = self.storage.list_queue()
        lines = [
            "# Daily Requirement Discovery Report",
            "",
            f"Generated: {utc_now()}",
            "",
            "## Snapshot",
            f"- New or tracked requirements: {len(requirements)}",
            f"- Queued for research: {len(queue)}",
            f"- Research runs completed: {len(runs)}",
            "",
            "## Strongest Candidates",
        ]
        for requirement in sorted(requirements, key=lambda item: item.current_scores.get("overall_score", 0), reverse=True)[:10]:
            lines.append(
                f"- {requirement.canonical_requirement} | {requirement.status.value} | "
                f"{requirement.current_scores.get('overall_score', 0)} | evidence {requirement.evidence_count}"
            )
        lines.extend(["", "## Reopened Requirements"])
        reopened = [item for item in requirements if item.status == RequirementStatus.REOPENED or item.reopen_events]
        lines.extend(
            f"- {item.canonical_requirement}: {item.reopen_events[-1]['reason'] if item.reopen_events else 'reopened'}"
            for item in reopened
        )
        lines.extend(["", "## Agent Errors Or Data Gaps"])
        lines.append("- Live Reddit API ingestion is not configured in this MVP; use JSON input for controlled subreddit scans.")
        report = "\n".join(lines) + "\n"
        self.log("daily_report", "completed", [], [])
        return report

    def opportunity_report(self, requirement_id: str) -> str:
        requirement = self.storage.get_requirement(requirement_id)
        if requirement is None:
            raise ValueError(f"Unknown requirement: {requirement_id}")
        runs = self.storage.list_research_runs(requirement_id)
        latest = runs[0] if runs else None
        evidence = self.storage.list_evidence(requirement.evidence_ids)
        lines = [
            f"# {requirement.canonical_requirement}",
            "",
            f"Status: {requirement.status.value}",
            f"Overall score: {requirement.current_scores.get('overall_score', 0)}",
            "",
            "## Why This Might Be Real",
            latest.findings["why_real"] if latest else "No deep research run yet.",
            "",
            "## Why This Might Be Noise",
            latest.findings["why_noise"] if latest else "No deep research run yet.",
            "",
            "## Source Evidence",
        ]
        lines.extend(f"- {item.subreddit}: {item.title} ({item.source_url})" for item in evidence)
        return "\n".join(lines) + "\n"


def score_requirement(requirement: RequirementRecord, evidence: list[RawEvidence]) -> dict[str, Any]:
    frequency = min(requirement.evidence_count * 12, 100)
    velocity = min(requirement.times_detected * 15, 100)
    spread = min(requirement.subreddit_count * 20, 100)
    pain = min(sum(30 if "complaint" in item.matched_patterns else 12 for item in evidence), 100)
    engagement = min(mean([item.score + item.comment_count for item in evidence] or [0]) * 2, 100)
    monetization = min(sum(25 for item in evidence if find_payment_signals([item])), 100)
    geo_confidence = min(sum(item["confidence"] for item in requirement.geo_distribution) * 100, 100)
    solution_gap = min(sum(20 for item in evidence if {"alternative", "workaround"} & set(item.matched_patterns)), 100)
    buildability = 70 if evidence else 0
    overall = round(
        frequency * 0.16
        + velocity * 0.12
        + spread * 0.14
        + pain * 0.16
        + engagement * 0.1
        + monetization * 0.12
        + geo_confidence * 0.06
        + solution_gap * 0.08
        + buildability * 0.06,
        1,
    )
    return {
        "frequency_score": round(frequency, 1),
        "velocity_score": round(velocity, 1),
        "subreddit_spread_score": round(spread, 1),
        "pain_intensity_score": round(pain, 1),
        "engagement_score": round(engagement, 1),
        "monetization_score": round(monetization, 1),
        "geo_confidence_score": round(geo_confidence, 1),
        "solution_gap_score": round(solution_gap, 1),
        "buildability_score": round(buildability, 1),
        "overall_score": overall,
        "overall_label": score_label(overall),
    }


def score_label(score: float) -> str:
    if score >= 82:
        return SignalLabel.HIGH_PRIORITY.value
    if score >= 70:
        return SignalLabel.VALIDATED.value
    if score >= 55:
        return SignalLabel.PROMISING.value
    if score >= 35:
        return SignalLabel.WATCH.value
    return SignalLabel.WEAK.value


def detect_change(requirement: RequirementRecord) -> dict[str, Any]:
    previous = requirement.previous_scores or {}
    current = requirement.current_scores or {}
    current_score = float(current.get("overall_score", 0))
    previous_score = float(previous.get("overall_score", current_score))
    score_delta = round(current_score - previous_score, 1)
    new_evidence_count = max(requirement.evidence_count - int(previous.get("evidence_count", 0)), 0)
    reasons: list[str] = []
    if score_delta >= 12:
        reasons.append("signal strength increased materially")
    if requirement.subreddit_count >= 3 and float(previous.get("subreddit_spread_score", 0)) < current.get("subreddit_spread_score", 0):
        reasons.append("evidence spread across more subreddits")
    if current.get("monetization_score", 0) > previous.get("monetization_score", 0):
        reasons.append("new willingness-to-pay language appeared")
    return {
        "should_reopen": bool(reasons),
        "reason": "; ".join(reasons) if reasons else "no meaningful change detected",
        "score_delta": score_delta,
        "new_evidence_count": new_evidence_count,
    }


def infer_existing_solutions(evidence: list[RawEvidence]) -> dict[str, Any]:
    body = "\n".join(item.body for item in evidence).lower()
    solutions = []
    for term in ["spreadsheet", "notion", "airtable", "app", "consultant", "agency", "newsletter", "pinterest"]:
        if term in body:
            solutions.append(term)
    return {
        "existing_solutions": sorted(set(solutions)) or ["manual workarounds", "generic search", "forum advice"],
        "common_complaints": ["too manual", "hard to compare options", "existing tools do not fit the workflow"],
    }


def infer_opportunities(requirement: RequirementRecord, evidence: list[RawEvidence]) -> dict[str, list[str]]:
    topic = requirement.canonical_requirement.removeprefix("Users need a better way to handle ")
    return {
        "product": [f"Focused workflow tool for {topic}", f"Evidence-backed tracker for {topic}"],
        "content": [f"Best ways to solve {topic}", f"Comparison guide for {topic} tools"],
    }


def find_payment_signals(evidence: list[RawEvidence]) -> list[str]:
    signals = []
    for item in evidence:
        text = f"{item.title} {item.body}".lower()
        for term in ["pay", "paid", "subscribe", "subscription", "buy", "hire", "refund", "pricing", "$", "£", "€"]:
            if term in text:
                signals.append(term)
    return sorted(set(signals))


def summarize_why_real(evidence: list[RawEvidence]) -> str:
    if not evidence:
        return "No supporting evidence was found."
    subreddits = sorted({item.subreddit for item in evidence})
    patterns = sorted({pattern for item in evidence for pattern in item.matched_patterns})
    return f"Found {len(evidence)} supporting Reddit evidence item(s) across {len(subreddits)} subreddit(s): {', '.join(subreddits)}. Signals include {', '.join(patterns)}."


def summarize_why_noise(requirement: RequirementRecord, evidence: list[RawEvidence]) -> str:
    risks = []
    if requirement.evidence_count < 3:
        risks.append("low evidence count")
    if requirement.subreddit_count < 2:
        risks.append("limited subreddit spread")
    if not find_payment_signals(evidence):
        risks.append("no clear willingness-to-pay language")
    return ", ".join(risks) if risks else "The main risk is Reddit sampling bias rather than a specific contradiction."


def classify_pain(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


def signal_size(score: float) -> str:
    if score >= 72:
        return "large"
    if score >= 45:
        return "medium"
    return "small"


def make_recommendation(scores: dict[str, Any]) -> str:
    if scores["overall_score"] >= 72:
        return "validated enough for human review and external validation"
    if scores["overall_score"] >= 45:
        return "keep tracking and run lightweight validation"
    return "watch for more evidence before acting"


def next_validation_step(scores: dict[str, Any]) -> str:
    if scores["monetization_score"] < 30:
        return "Search for stronger payment or switching signals before product investment."
    if scores["subreddit_spread_score"] < 40:
        return "Check adjacent subreddits to confirm the requirement is not isolated."
    return "Create a short opportunity brief and test demand with content or interviews."
