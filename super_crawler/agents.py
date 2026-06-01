from __future__ import annotations

import hashlib
import json
from collections import Counter
from statistics import mean
from typing import Any

from .collectors import OpenCliRedditCollector
from .llm import DeepSeekClient
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

    def ingest_reddit_items(
        self,
        items: list[dict[str, Any]],
        task_group_id: str | None = None,
        task_group_run_id: str | None = None,
        model_name: str | None = None,
        use_llm: bool = False,
    ) -> list[CandidateRequirement]:
        candidates: list[CandidateRequirement] = []
        llm = DeepSeekClient() if use_llm else None
        for item in items:
            text = f"{item.get('title', '')}\n{item.get('body', '')}"
            relevance = search_relevance_check(item)
            if not relevance["is_relevant"]:
                source_url = str(item.get("source_url") or "")
                analysis_payload = {
                    "schema_version": "requirement_lifecycle_v1",
                    "pipeline_stage": "discovery_sample_analysis",
                    "record_type": "sample_analysis",
                    "agent_id": item.get("search_agent_id", self.agent_id),
                    "search_query": item.get("search_query") or item.get("collection_query", ""),
                    "search_subreddit": item.get("search_subreddit", ""),
                    "search_strategy": item.get("search_strategy", ""),
                    "url": source_url,
                    "title": item.get("title", ""),
                    "subreddit": item.get("subreddit", ""),
                    "method": "search_relevance_gate",
                    "model": None,
                    "is_possible_requirement": False,
                    "is_requirement": False,
                    "signals": [],
                    "sample_analysis": relevance["reason"],
                    "analysis_summary": relevance["reason"],
                    "sample_rejection_reason": relevance["reason"],
                    "rejection_reason": relevance["reason"],
                    "requirement_title": "",
                    "confidence": None,
                }
                if task_group_id or task_group_run_id:
                    self.storage.log_experiment(
                        task_group_id,
                        task_group_run_id,
                        self.role,
                        "sample_analyzed",
                        f"Sample rejected by relevance gate: {item.get('title', '')}",
                        analysis_payload,
                    )
                continue
            analysis = self._analyze_item_with_llm(item, model_name or "deepseek-v4-flash", llm) if llm and llm.available() else None
            matched = analysis["signals"] if analysis else matched_signal_patterns(text)
            source_url = str(item.get("source_url") or "")
            is_possible = bool(matched and not (analysis and not analysis["is_possible_requirement"]))
            sample_analysis = (analysis or {}).get("sample_analysis") or ("Matched possible requirement signals." if matched else "No possible requirement signal found.")
            sample_rejection_reason = (analysis or {}).get("sample_rejection_reason", "")
            analysis_payload = {
                "schema_version": "requirement_lifecycle_v1",
                "pipeline_stage": "discovery_sample_analysis",
                "record_type": "sample_analysis",
                "agent_id": item.get("search_agent_id", self.agent_id),
                "search_query": item.get("search_query") or item.get("collection_query", ""),
                "search_subreddit": item.get("search_subreddit", ""),
                "search_strategy": item.get("search_strategy", ""),
                "url": source_url,
                "title": item.get("title", ""),
                "subreddit": item.get("subreddit", ""),
                "method": "llm" if analysis else "rules",
                "model": model_name if analysis else None,
                "is_possible_requirement": is_possible,
                "is_requirement": is_possible,
                "signals": matched,
                "sample_analysis": sample_analysis,
                "analysis_summary": sample_analysis,
                "sample_rejection_reason": sample_rejection_reason,
                "rejection_reason": sample_rejection_reason,
                "requirement_title": (analysis or {}).get("requirement_title", ""),
                "confidence": (analysis or {}).get("confidence"),
            }
            if task_group_id or task_group_run_id:
                self.storage.log_experiment(
                    task_group_id,
                    task_group_run_id,
                    self.role,
                    "sample_analyzed",
                    f"Sample analyzed: {item.get('title', '')}",
                    analysis_payload,
                )
            if not matched or (analysis and not analysis["is_possible_requirement"]):
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
                task_group_id=task_group_id or item.get("task_group_id"),
                task_group_run_id=task_group_run_id or item.get("task_group_run_id"),
            )
            self.storage.upsert_evidence(evidence)

            candidate = CandidateRequirement(
                candidate_id=self._id("cand", evidence_id + normalize_requirement((analysis or {}).get("requirement_title", text))),
                requirement_title=(analysis or {}).get("requirement_title") or make_requirement_title(evidence.title, evidence.body),
                requirement_description=(analysis or {}).get("requirement_description") or text.strip()[:600],
                evidence_ids=[evidence_id],
                signal_type=(analysis or {}).get("signal_type") or self._signal_type(matched),
                detected_audience=(analysis or {}).get("audience") or infer_audience(text),
                detected_pain=(analysis or {}).get("pain_level") or self._pain_label(text),
                initial_confidence=(analysis or {}).get("confidence") or self._confidence(evidence, matched),
                duplicate_candidate_ids=[],
                status=RequirementStatus.NEW_CANDIDATE,
                created_at=fetched_at,
                updated_at=fetched_at,
                task_group_id=task_group_id or item.get("task_group_id"),
                task_group_run_id=task_group_run_id or item.get("task_group_run_id"),
            )
            self.storage.upsert_candidate(candidate)
            candidates.append(candidate)

        self.log("ingest_reddit_items", "completed", [ref for ref in [task_group_id, task_group_run_id] if ref], [candidate.candidate_id for candidate in candidates])
        return candidates

    def _analyze_item_with_llm(self, item: dict[str, Any], model_name: str, llm: DeepSeekClient) -> dict[str, Any] | None:
        title = str(item.get("title", ""))
        body = str(item.get("body", ""))
        subreddit = str(item.get("subreddit", ""))
        system = (
            "You are a discovery search agent. Your job is lightweight sample screening, not final validation. "
            "Decide whether this Reddit post is worth saving as a possible requirement candidate for a deep "
            "research agent. Return only JSON."
        )
        user = json.dumps(
            {
                "instructions": {
                    "is_possible_requirement": "true for plausible user pain, unmet need, workaround, buying intent, or request for a better way; false only for clear noise",
                    "reject": "news, memes, generic photos, facts, entertainment, celebrity posts, or topic mentions without any user problem",
                    "requirement_title": "one sentence beginning with 'Users need'",
                    "requirement_description": "short explanation grounded in the post",
                    "signals": "short labels like tool_request, complaint, workaround, alternative, workflow_pain",
                    "sample_analysis": "concise observable evidence for why this sample may be worth deep research; not hidden chain-of-thought",
                    "sample_rejection_reason": "short reason if is_possible_requirement is false",
                    "pain_level": "low, medium, or high",
                    "confidence": "number from 0 to 1",
                },
                "post": {
                    "title": title,
                    "body": body[:1800],
                    "subreddit": subreddit,
                    "search_query": item.get("search_query") or item.get("collection_query", ""),
                    "search_subreddit": item.get("search_subreddit", ""),
                    "search_strategy": item.get("search_strategy", ""),
                    "score": item.get("score", 0),
                    "comment_count": item.get("comment_count", 0),
                    "url": item.get("source_url", ""),
                },
                "important_filter": "Reject if the post is not clearly related to the search_query/search_subreddit context, even if it contains a generic user pain.",
            }
        )
        try:
            parsed = llm.json_chat(model_name, system, user)
        except Exception:
            return None
        return normalize_llm_requirement_analysis(parsed, title, body)

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


def search_relevance_check(item: dict[str, Any]) -> dict[str, Any]:
    query = str(item.get("search_query") or item.get("collection_query") or "").lower()
    expected_subreddit = str(item.get("search_subreddit") or "").lower()
    actual_subreddit = str(item.get("subreddit") or "").lower().removeprefix("r/")
    haystack = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("body") or ""),
            actual_subreddit,
        ]
    ).lower()
    if not query:
        return {"is_relevant": True, "reason": "No search context was attached to this item."}
    if expected_subreddit and expected_subreddit == actual_subreddit:
        return {"is_relevant": True, "reason": "Post came from the planned subreddit."}
    electronics_context = {
        "3c",
        "consumer electronics",
        "electronics",
        "phone",
        "laptop",
        "headphone",
        "earbud",
        "charger",
        "cable",
        "usb",
        "monitor",
        "gpu",
        "rtx",
        "ssd",
        "smart",
        "camera",
        "doorbell",
        "amazon",
        "warranty",
        "refund",
    }
    if any(term in query for term in electronics_context):
        electronics_terms = [
            "phone",
            "iphone",
            "android",
            "laptop",
            "headphone",
            "headphones",
            "earbud",
            "earbuds",
            "charger",
            "charging",
            "cable",
            "usb",
            "usb-c",
            "monitor",
            "gpu",
            "rtx",
            "ssd",
            "keyboard",
            "mouse",
            "router",
            "smart home",
            "camera",
            "doorbell",
            "ring",
            "amazon",
            "warranty",
            "refund",
            "return",
            "electronics",
        ]
        if any(term in haystack for term in electronics_terms):
            return {"is_relevant": True, "reason": "Post matches the consumer-electronics search context."}
        return {
            "is_relevant": False,
            "reason": "Rejected before LLM analysis because the post is not related to the consumer-electronics search context.",
        }
    query_terms = [term for term in query.replace("/", " ").split() if len(term) >= 4]
    if query_terms and any(term in haystack for term in query_terms[:8]):
        return {"is_relevant": True, "reason": "Post matches the search query terms."}
    return {"is_relevant": True, "reason": "No strict relevance gate applies to this search context."}


def normalize_llm_requirement_analysis(parsed: dict[str, Any], title: str, body: str) -> dict[str, Any] | None:
    is_possible = bool(parsed.get("is_possible_requirement", parsed.get("is_requirement")))
    if not is_possible:
        sample_rejection_reason = str(
            parsed.get("sample_rejection_reason")
            or parsed.get("rejection_reason")
            or "The post does not express a clear user problem or request worth deep research."
        )
        sample_analysis = str(parsed.get("sample_analysis") or parsed.get("analysis_summary") or "")
        return {
            "is_possible_requirement": False,
            "is_requirement": False,
            "signals": [],
            "sample_analysis": sample_analysis,
            "analysis_summary": sample_analysis,
            "sample_rejection_reason": sample_rejection_reason,
            "rejection_reason": sample_rejection_reason,
        }
    signals = parsed.get("signals")
    if not isinstance(signals, list) or not signals:
        signals = ["llm_requirement"]
    audience = parsed.get("audience")
    if not isinstance(audience, list):
        audience = infer_audience(f"{title}\n{body}")
    confidence = parsed.get("confidence", 0.55)
    try:
        confidence = max(0.05, min(float(confidence), 0.98))
    except (TypeError, ValueError):
        confidence = 0.55
    return {
        "is_possible_requirement": True,
        "is_requirement": True,
        "signals": [str(signal) for signal in signals],
        "requirement_title": str(parsed.get("requirement_title") or make_requirement_title(title, body))[:180],
        "requirement_description": str(parsed.get("requirement_description") or f"{title}\n{body}")[:700],
        "signal_type": str(parsed.get("signal_type") or signals[0]).replace("_", " "),
        "audience": [str(item) for item in audience],
        "pain_level": str(parsed.get("pain_level") or "medium"),
        "confidence": round(confidence, 2),
        "sample_analysis": str(parsed.get("sample_analysis") or parsed.get("analysis_summary") or ""),
        "analysis_summary": str(parsed.get("sample_analysis") or parsed.get("analysis_summary") or ""),
        "sample_rejection_reason": str(parsed.get("sample_rejection_reason") or parsed.get("rejection_reason") or ""),
        "rejection_reason": str(parsed.get("sample_rejection_reason") or parsed.get("rejection_reason") or ""),
    }


def normalize_deep_research_item_analysis(parsed: dict[str, Any]) -> dict[str, Any]:
    signals = parsed.get("signals")
    if not isinstance(signals, list):
        signals = []
    regions = parsed.get("country_area_hints")
    if not isinstance(regions, list):
        regions = []
    solutions = parsed.get("existing_solutions")
    if not isinstance(solutions, list):
        solutions = []
    try:
        confidence = max(0.0, min(float(parsed.get("confidence", 0.5)), 1.0))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "is_relevant_evidence": bool(parsed.get("is_relevant_evidence")),
        "evidence_type": str(parsed.get("evidence_type") or ("supporting_evidence" if parsed.get("is_relevant_evidence") else "noise")),
        "analysis_summary": str(parsed.get("analysis_summary") or parsed.get("summary") or ""),
        "signals": [str(signal) for signal in signals],
        "country_area_hints": [str(region) for region in regions],
        "existing_solutions": [str(solution) for solution in solutions],
        "confidence": round(confidence, 2),
    }


def infer_existing_solution_terms(text: str) -> list[str]:
    lower = text.lower()
    terms = [
        "spreadsheet",
        "notion",
        "airtable",
        "excel",
        "google sheets",
        "amazon",
        "apple",
        "google",
        "ring",
        "wyze",
        "eufy",
        "anker",
        "reddit",
        "manual workaround",
        "subscription",
        "warranty",
        "refund",
    ]
    return [term for term in terms if term in lower]


def parse_int(value: object, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


class RequirementMemoryAgent(BaseAgent):
    role = "requirement_memory"

    def reconcile_candidates(self) -> list[RequirementRecord]:
        candidates = self.storage.list_candidates([RequirementStatus.NEW_CANDIDATE.value])
        requirements = self.storage.list_requirements()
        changed: list[RequirementRecord] = []
        for candidate in candidates:
            match = self._find_match(candidate, requirements)
            if match:
                record = self._merge_candidate(match, candidate)
                candidate.status = RequirementStatus.DUPLICATE_CANDIDATE
                event_type = "memory_merged_candidate"
                event_message = f"Requirement memory merged candidate into {record.requirement_id}"
            else:
                record = self._create_requirement(candidate)
                requirements.append(record)
                event_type = "memory_created_requirement"
                event_message = f"Requirement memory created requirement {record.requirement_id}"

            record = self._score_and_queue(record)
            self.storage.upsert_requirement(record)
            self.storage.upsert_candidate(candidate)
            self.storage.save_requirement_sample(
                candidate.task_group_id,
                candidate.task_group_run_id,
                record.requirement_id,
                one_sentence_requirement(record),
                record.status.value,
            )
            self.storage.log_requirement_event(
                record.requirement_id,
                candidate.task_group_id,
                candidate.task_group_run_id,
                self.agent_id,
                self.role,
                event_type,
                event_message,
                {
                    "candidate_id": candidate.candidate_id,
                    "requirement_sentence": one_sentence_requirement(record),
                    "status": record.status.value,
                },
            )
            if record.status == RequirementStatus.QUEUED_FOR_RESEARCH:
                self.storage.enqueue_research(
                    requirement_id=record.requirement_id,
                    priority=int(record.current_scores["overall_score"]),
                    reason=self._queue_reason(record),
                    new_evidence_count=len(candidate.evidence_ids),
                    previous_research_status=record.latest_recommendation,
                    task_group_id=candidate.task_group_id,
                )
                self.storage.log_requirement_event(
                    record.requirement_id,
                    candidate.task_group_id,
                    candidate.task_group_run_id,
                    self.agent_id,
                    self.role,
                    "queued_for_deep_research",
                    f"Requirement memory queued {record.requirement_id} for deep research",
                    {
                        "priority": int(record.current_scores["overall_score"]),
                        "reason": self._queue_reason(record),
                    },
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
            task_group_ids=[candidate.task_group_id] if candidate.task_group_id else [],
            task_group_run_ids=[candidate.task_group_run_id] if candidate.task_group_run_id else [],
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
        if candidate.task_group_id:
            requirement.task_group_ids = sorted(set(requirement.task_group_ids + [candidate.task_group_id]))
        if candidate.task_group_run_id:
            requirement.task_group_run_ids = sorted(set(requirement.task_group_run_ids + [candidate.task_group_run_id]))
        requirement.decision_history.append({"at": now, "decision": "merged_candidate", "source_candidate": candidate.candidate_id})
        return requirement

    def _score_and_queue(self, requirement: RequirementRecord) -> RequirementRecord:
        scores = score_requirement(requirement, self.storage.list_evidence(requirement.evidence_ids))
        requirement.current_scores = scores
        if requirement.status == RequirementStatus.RESEARCHING:
            return requirement
        if requirement.status in {RequirementStatus.VALIDATED, RequirementStatus.WATCHING, RequirementStatus.REJECTED, RequirementStatus.ARCHIVED}:
            return requirement
        requirement.status = RequirementStatus.QUEUED_FOR_RESEARCH
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
                    task_group_id=requirement.task_group_ids[-1] if requirement.task_group_ids else None,
                )
                reopened.append(requirement)
        self.log("evaluate_reopenings", "completed", [], [item.requirement_id for item in reopened])
        return reopened


PoolManagerAgent = RequirementMemoryAgent


class DeepResearchAgent(BaseAgent):
    role = "deep_research"

    def __init__(self, storage: Storage, agent_id: str, collector_factory: Any | None = None, llm_client: DeepSeekClient | None = None):
        super().__init__(storage, agent_id)
        self.collector_factory = collector_factory or OpenCliRedditCollector
        self.llm_client = llm_client

    def run_next(self, eligible_task_group_ids: list[str] | None = None) -> ResearchRun | None:
        queue_item = self.storage.claim_next_research(self.agent_id, eligible_task_group_ids)
        if queue_item is None:
            self.log("run_next", "skipped", [], [])
            return None
        requirement_id = str(queue_item["requirement_id"])
        requirement = self.storage.get_requirement(requirement_id)
        if requirement is None:
            self._requeue_claimed_research(queue_item)
            self.log("run_next", "failed", [requirement_id], [], "requirement not found")
            return None
        try:
            return self.research(requirement)
        except Exception as exc:
            self._mark_research_failed(requirement, str(exc), queue_item)
            self.log("run_next", "failed", [requirement_id], [], str(exc))
            return None

    def research(self, requirement: RequirementRecord) -> ResearchRun:
        started = utc_now()
        requirement.status = RequirementStatus.RESEARCHING
        requirement.assigned_to = self.agent_id
        self.storage.upsert_requirement(requirement)
        task_group_id = requirement.task_group_ids[-1] if requirement.task_group_ids else None
        task_group_run_id = requirement.task_group_run_ids[-1] if requirement.task_group_run_ids else None
        self.storage.log_requirement_event(
            requirement.requirement_id,
            task_group_id,
            task_group_run_id,
            self.agent_id,
            self.role,
            "deep_research_started",
            f"Deep research started for {requirement.requirement_id}",
            {"evidence_ids": requirement.evidence_ids},
        )
        if task_group_id or task_group_run_id:
            self.storage.log_experiment(
                task_group_id,
                task_group_run_id,
                self.role,
                "deep_research_started",
                f"Deep research started for {requirement.requirement_id}",
                {"requirement_id": requirement.requirement_id, "agent_id": self.agent_id},
            )

        active_research = self._run_active_research(requirement, task_group_id, task_group_run_id)
        if active_research["evidence_ids"]:
            requirement.evidence_ids = sorted(set(requirement.evidence_ids + active_research["evidence_ids"]))
            evidence_after_research = self.storage.list_evidence(requirement.evidence_ids)
            requirement.evidence_count = len(evidence_after_research)
            requirement.subreddit_count = len({item.subreddit for item in evidence_after_research})
            requirement.geo_distribution = self._geo_distribution(evidence_after_research)
            requirement.last_seen = utc_now()
            requirement.decision_history.append(
                {
                    "at": utc_now(),
                    "decision": "active_deep_research_added_evidence",
                    "evidence_ids": active_research["evidence_ids"],
                    "search_queries": active_research["queries"],
                }
            )
            self.storage.upsert_requirement(requirement)

        evidence = self.storage.list_evidence(requirement.evidence_ids)
        scores = score_requirement(requirement, evidence)
        positives = [item for item in evidence if item.matched_patterns]
        geo = requirement.geo_distribution
        alternatives = infer_existing_solutions(evidence)
        opportunities = infer_opportunities(requirement, evidence)
        validation = validate_requirement(requirement, evidence, scores, alternatives)
        final_status = self._status_from_scores(scores)
        rejection_summary = one_sentence_rejection_summary(requirement, evidence, validation)
        recommendation = make_recommendation(scores, final_status, validation, rejection_summary)
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
                "is_real_requirement": validation["is_real_requirement"],
                "realness_reason": validation["realness_reason"],
                "rejection_summary": rejection_summary if final_status == RequirementStatus.REJECTED else "",
                "why_real": summarize_why_real(positives),
                "why_noise": summarize_why_noise(requirement, evidence),
                "audience": requirement.audience_segments,
                "country_area_distribution": validation["country_area_distribution"],
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
                "requirement_scale": validation["requirement_scale"],
                "scale_reason": validation["scale_reason"],
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
        search_insight = self._build_search_insight(requirement, run, active_research)
        insight_id = self.storage.save_search_insight(
            task_group_id,
            task_group_run_id,
            requirement.requirement_id,
            run.research_run_id,
            self.agent_id,
            "deep_research_feedback",
            search_insight,
        )

        requirement.status = final_status
        requirement.assigned_to = None
        requirement.research_history.append(run.research_run_id)
        requirement.latest_recommendation = recommendation
        requirement.current_scores = scores
        requirement.previous_scores = scores
        requirement.decision_history.append({"at": utc_now(), "decision": requirement.status.value, "research_run": run_id})
        self.storage.upsert_requirement(requirement)
        self.storage.log_requirement_event(
            requirement.requirement_id,
            task_group_id,
            task_group_run_id,
            self.agent_id,
            self.role,
            "deep_research_completed",
            f"Deep research completed for {requirement.requirement_id}: {recommendation}",
            {
                "research_run_id": run.research_run_id,
                "recommendation": recommendation,
                "status": requirement.status.value,
                "scores": scores,
                "validation": validation,
            },
        )
        if task_group_id or task_group_run_id:
            self.storage.log_experiment(
                task_group_id,
                task_group_run_id,
                self.role,
                "deep_research_output",
                f"Deep research output for {requirement.requirement_id}: {recommendation}",
                {
                    "schema_version": "requirement_lifecycle_v1",
                    "pipeline_stage": "deep_research_validation",
                    "record_type": "final_validation",
                    "requirement_id": requirement.requirement_id,
                    "research_run_id": run.research_run_id,
                    "agent_id": self.agent_id,
                    "recommendation": recommendation,
                    "status": requirement.status.value,
                    "is_real_requirement": validation["is_real_requirement"],
                    "realness_reason": validation["realness_reason"],
                    "rejection_summary": rejection_summary if final_status == RequirementStatus.REJECTED else "",
                    "requirement_scale": validation["requirement_scale"],
                    "country_area_distribution": validation["country_area_distribution"],
                    "scores": scores,
                    "active_research": active_research,
                    "search_insight_id": insight_id,
                    "search_insight": search_insight,
                },
            )
            self.storage.log_experiment(
                task_group_id,
                task_group_run_id,
                self.role,
                "deep_research_search_insight",
                f"Deep research produced search feedback for {requirement.requirement_id}",
                {
                    "schema_version": "requirement_lifecycle_v1",
                    "pipeline_stage": "search_self_improvement",
                    "record_type": "search_insight",
                    "requirement_id": requirement.requirement_id,
                    "research_run_id": run.research_run_id,
                    "search_insight_id": insight_id,
                    **search_insight,
                },
            )
        self.log("run_next", "completed", [requirement.requirement_id], [run.research_run_id])
        return run

    def _build_search_insight(
        self,
        requirement: RequirementRecord,
        run: ResearchRun,
        active_research: dict[str, Any],
    ) -> dict[str, Any]:
        searches = [item for item in active_research.get("searches", []) if isinstance(item, dict)]
        productive = [item for item in searches if int(item.get("evidence_added", 0) or 0) > 0]
        noisy = [item for item in searches if int(item.get("evidence_added", 0) or 0) == 0 and not item.get("error")]
        productive_terms = search_feedback_terms(productive)
        noisy_terms = search_feedback_terms(noisy)
        productive_strategies = sorted({str(item.get("strategy", "")) for item in productive if item.get("strategy")})
        noisy_strategies = sorted({str(item.get("strategy", "")) for item in noisy if item.get("strategy")})
        productive_subreddits = sorted({str(item.get("subreddit", "")) for item in productive if item.get("subreddit")})
        noisy_subreddits = sorted({str(item.get("subreddit", "")) for item in noisy if item.get("subreddit")})
        suggested = [
            {
                "query": str(item.get("query", "")),
                "subreddit": str(item.get("subreddit", "")),
                "strategy": f"learned_{item.get('strategy', 'deep_research')}",
                "sort": "relevance",
                "time": "year",
                "why": f"Deep research added {item.get('evidence_added', 0)} relevant evidence item(s).",
            }
            for item in productive
            if item.get("query")
        ]
        if not suggested and self._status_from_scores(run.scores) != RequirementStatus.REJECTED:
            compact = " ".join(requirement.canonical_requirement.replace("Users need", "").replace("users need", "").split())
            suggested.append(
                {
                    "query": f"{compact} repeated problem workaround alternative",
                    "subreddit": "",
                    "strategy": "learned_followup",
                    "sort": "relevance",
                    "time": "year",
                    "why": "Deep research did not find a productive exact query, so broaden the next follow-up search.",
                }
            )
        return {
            "requirement_id": requirement.requirement_id,
            "research_run_id": run.research_run_id,
            "recommendation": run.recommendation,
            "status_after_research": self._status_from_scores(run.scores).value,
            "productive_queries": [str(item.get("query", "")) for item in productive if item.get("query")],
            "noisy_queries": [str(item.get("query", "")) for item in noisy if item.get("query")],
            "suggested_searches": suggested[:3],
            "productive_dimensions": {
                "subreddits": productive_subreddits,
                "query_terms": productive_terms,
                "strategies": productive_strategies,
            },
            "unproductive_dimensions": {
                "subreddits": noisy_subreddits,
                "query_terms": noisy_terms,
                "strategies": noisy_strategies,
            },
            "recommended_allocation_change": {
                "increase": productive_strategies,
                "decrease": [strategy for strategy in noisy_strategies if strategy not in productive_strategies],
            },
            "deepen_when": [
                "repeat similar searches when evidence_added is greater than zero",
                "prefer subreddits and query terms that produced relevant deep research evidence",
            ],
            "deprioritize_when": [
                "avoid exact queries that returned analyzed items but no relevant evidence",
            ],
        }

    def _requeue_claimed_research(self, queue_item: dict[str, Any]) -> None:
        self.storage.enqueue_research(
            str(queue_item["requirement_id"]),
            int(queue_item.get("priority", 1) or 1),
            str(queue_item.get("reason") or "deep research retry"),
            int(queue_item.get("new_evidence_count", 0) or 0),
            str(queue_item["previous_research_status"]) if queue_item.get("previous_research_status") is not None else None,
            str(queue_item["task_group_id"]) if queue_item.get("task_group_id") is not None else None,
            float(queue_item.get("estimated_cost", 0.25) or 0.25),
            int(queue_item.get("expected_completion_minutes", 20) or 20),
        )

    def _mark_research_failed(self, requirement: RequirementRecord, error: str, queue_item: dict[str, Any] | None = None) -> None:
        requirement.status = RequirementStatus.QUEUED_FOR_RESEARCH
        requirement.assigned_to = None
        requirement.decision_history.append({"at": utc_now(), "decision": "deep_research_failed", "error": error})
        self.storage.upsert_requirement(requirement)
        if queue_item is not None:
            self._requeue_claimed_research(queue_item)
        task_group_id = requirement.task_group_ids[-1] if requirement.task_group_ids else None
        task_group_run_id = requirement.task_group_run_ids[-1] if requirement.task_group_run_ids else None
        self.storage.log_requirement_event(
            requirement.requirement_id,
            task_group_id,
            task_group_run_id,
            self.agent_id,
            self.role,
            "deep_research_failed",
            f"Deep research failed for {requirement.requirement_id}: {error}",
            {"error": error},
        )
        if task_group_id or task_group_run_id:
            self.storage.log_experiment(
                task_group_id,
                task_group_run_id,
                self.role,
                "deep_research_failed",
                f"Deep research failed for {requirement.requirement_id}: {error}",
                {"requirement_id": requirement.requirement_id, "agent_id": self.agent_id, "error": error},
            )

    def _run_active_research(
        self,
        requirement: RequirementRecord,
        task_group_id: str | None,
        task_group_run_id: str | None,
    ) -> dict[str, Any]:
        config = self.storage.get_task_group_config(task_group_id) if task_group_id else self.storage.get_app_config()
        plan = self._plan_active_research(requirement)
        self._log_deep_research_step(
            requirement,
            task_group_id,
            task_group_run_id,
            "deep_research_plan_created",
            f"Created {len(plan)} deep research search task(s) for {requirement.requirement_id}",
            {"requirement_id": requirement.requirement_id, "agent_id": self.agent_id, "plan": plan},
        )
        if config.get("collector_enabled") != "1":
            self._log_deep_research_step(
                requirement,
                task_group_id,
                task_group_run_id,
                "deep_research_search_skipped",
                "OpenCLI collection is disabled for this group, so deep research used existing evidence only.",
                {"collector_enabled": config.get("collector_enabled", "0"), "plan": plan},
            )
            return {"queries": [item["query"] for item in plan], "evidence_ids": [], "items_analyzed": 0, "searches": [], "skipped": "collector_disabled"}

        collector = self.collector_factory(
            command=config.get("collector_command", "opencli reddit search"),
            timeout_seconds=parse_int(config.get("collector_timeout_seconds"), 120),
        )
        model_name = config.get("model_deep_research", "deepseek-v4-flash")
        llm = self.llm_client if self.llm_client is not None else DeepSeekClient()
        evidence_ids: list[str] = []
        searches: list[dict[str, Any]] = []
        items_analyzed = 0
        seen_urls = {item.source_url for item in self.storage.list_evidence(requirement.evidence_ids)}
        for index, task in enumerate(plan, start=1):
            self._log_deep_research_step(
                requirement,
                task_group_id,
                task_group_run_id,
                "deep_research_search_started",
                f"Deep research search {index} started: {task['query']}",
                {"agent_id": self.agent_id, **task},
            )
            try:
                result = collector.search(
                    str(task["query"]),
                    limit=5,
                    subreddit=str(task.get("subreddit", "")),
                    sort=str(task.get("sort", "relevance")),
                    time=str(task.get("time", "year")),
                )
            except Exception as exc:  # noqa: BLE001 - failed searches should be logged and the requirement should still be scored.
                searches.append({"query": task["query"], "subreddit": task.get("subreddit", ""), "error": str(exc)})
                self._log_deep_research_step(
                    requirement,
                    task_group_id,
                    task_group_run_id,
                    "deep_research_search_failed",
                    f"Deep research search failed: {task['query']}",
                    {"agent_id": self.agent_id, "error": str(exc), **task},
                )
                continue

            query_items = []
            for raw_item in result["items"]:
                item = {
                    **raw_item,
                    "search_agent_id": self.agent_id,
                    "search_query": task["query"],
                    "search_subreddit": task.get("subreddit", ""),
                    "search_strategy": task.get("strategy", "deep_research"),
                    "deep_research_question": task["question"],
                    "task_group_id": task_group_id,
                    "task_group_run_id": task_group_run_id,
                }
                url = str(item.get("source_url") or "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                analysis = self._analyze_research_item(requirement, item, model_name, llm)
                items_analyzed += 1
                self._log_deep_research_step(
                    requirement,
                    task_group_id,
                    task_group_run_id,
                    "deep_research_item_analyzed",
                    f"Analyzed deep research item: {item.get('title', '')}",
                    {"agent_id": self.agent_id, "requirement_id": requirement.requirement_id, **analysis, "url": url, "title": item.get("title", "")},
                )
                query_items.append({"url": url, "title": item.get("title", ""), "analysis": analysis})
                if not analysis["is_relevant_evidence"]:
                    continue
                evidence = self._evidence_from_research_item(requirement, item, analysis)
                self.storage.upsert_evidence(evidence)
                evidence_ids.append(evidence.evidence_id)

            search_record = {
                "query": task["query"],
                "question": task["question"],
                "subreddit": task.get("subreddit", ""),
                "strategy": task.get("strategy", "deep_research"),
                "command": result.get("command", []),
                "stderr": result.get("stderr", ""),
                "items_returned": len(result["items"]),
                "items_analyzed": len(query_items),
                "evidence_added": len([item for item in query_items if item["analysis"]["is_relevant_evidence"]]),
                "urls": [item["url"] for item in query_items if item["url"]],
                "titles": [item["title"] for item in query_items if item["title"]],
            }
            searches.append(search_record)
            self._log_deep_research_step(
                requirement,
                task_group_id,
                task_group_run_id,
                "deep_research_search_completed",
                f"Deep research search completed with {search_record['evidence_added']} relevant evidence item(s): {task['query']}",
                {"agent_id": self.agent_id, **search_record},
            )

        summary = {
            "queries": [item["query"] for item in plan],
            "evidence_ids": evidence_ids,
            "items_analyzed": items_analyzed,
            "searches": searches,
        }
        self._log_deep_research_step(
            requirement,
            task_group_id,
            task_group_run_id,
            "deep_research_evidence_collected",
            f"Deep research collected {len(evidence_ids)} new evidence item(s) for {requirement.requirement_id}",
            {"agent_id": self.agent_id, "requirement_id": requirement.requirement_id, **summary},
        )
        return summary

    def _plan_active_research(self, requirement: RequirementRecord) -> list[dict[str, str]]:
        base = requirement.canonical_requirement.strip()
        compact = " ".join(base.replace("Users need", "").replace("users need", "").split())
        evidence = self.storage.list_evidence(requirement.evidence_ids)
        subreddits = [item.subreddit for item in evidence if item.subreddit and item.subreddit != "unknown"]
        primary_subreddit = subreddits[0] if subreddits else ""
        audience = " ".join(requirement.audience_segments[:2]) or "users"
        return [
            {
                "question": "Do more users describe the same pain or workaround?",
                "query": f"{compact} problem pain workaround",
                "subreddit": primary_subreddit,
                "strategy": "repeat_pain_validation",
                "sort": "relevance",
                "time": "year",
            },
            {
                "question": "Are users looking for alternatives or existing solutions?",
                "query": f"{compact} alternative app tool solution",
                "subreddit": "",
                "strategy": "existing_solution_scan",
                "sort": "relevance",
                "time": "year",
            },
            {
                "question": "Is there buying intent, payment frustration, or switching intent?",
                "query": f"{audience} {compact} pay buy subscription refund warranty",
                "subreddit": primary_subreddit,
                "strategy": "market_signal_scan",
                "sort": "relevance",
                "time": "year",
            },
        ]

    def _analyze_research_item(
        self,
        requirement: RequirementRecord,
        item: dict[str, Any],
        model_name: str,
        llm: DeepSeekClient,
    ) -> dict[str, Any]:
        title = str(item.get("title", ""))
        body = str(item.get("body", ""))
        text = f"{title}\n{body}"
        matched = matched_signal_patterns(text)
        relevance = search_relevance_check(item)
        analysis: dict[str, Any] | None = None
        if llm.available():
            system = (
                "You are a deep research evidence analyst. Decide whether this search result is useful evidence "
                "for validating the requirement. Return only JSON and do not include hidden reasoning."
            )
            user = json.dumps(
                {
                    "requirement": {
                        "requirement_id": requirement.requirement_id,
                        "title": requirement.canonical_requirement,
                        "description": requirement.description,
                    },
                    "research_question": item.get("deep_research_question", ""),
                    "search_context": {
                        "query": item.get("search_query", ""),
                        "subreddit": item.get("search_subreddit", ""),
                        "strategy": item.get("search_strategy", ""),
                    },
                    "post": {
                        "title": title,
                        "body": body[:1800],
                        "subreddit": item.get("subreddit", ""),
                        "score": item.get("score", 0),
                        "comment_count": item.get("comment_count", 0),
                        "url": item.get("source_url", ""),
                    },
                    "expected_json": {
                        "is_relevant_evidence": "boolean",
                        "evidence_type": "repeat_pain | workaround | alternative | buying_intent | geography | noise",
                        "analysis_summary": "short observable analysis",
                        "signals": ["short labels"],
                        "country_area_hints": ["regions or countries if stated"],
                        "existing_solutions": ["tools, brands, methods if mentioned"],
                        "confidence": "0 to 1",
                    },
                }
            )
            try:
                parsed = llm.json_chat(model_name, system, user)
                analysis = normalize_deep_research_item_analysis(parsed)
            except Exception as exc:  # noqa: BLE001 - deterministic fallback is acceptable and logged in payload.
                analysis = {"llm_error": str(exc)}

        if analysis and "is_relevant_evidence" in analysis:
            return analysis
        relevant = bool(relevance["is_relevant"] and matched)
        return {
            "is_relevant_evidence": relevant,
            "evidence_type": matched[0] if matched else "noise",
            "analysis_summary": "Matched requirement validation signals." if relevant else relevance["reason"],
            "signals": matched,
            "country_area_hints": infer_geo(text, str(item.get("subreddit", ""))),
            "existing_solutions": infer_existing_solution_terms(text),
            "confidence": 0.65 if relevant else 0.25,
        }

    def _evidence_from_research_item(self, requirement: RequirementRecord, item: dict[str, Any], analysis: dict[str, Any]) -> RawEvidence:
        text = f"{item.get('title', '')}\n{item.get('body', '')}"
        source_url = str(item.get("source_url") or "")
        fetched_at = utc_now()
        signals = [str(signal) for signal in analysis.get("signals", [])] or matched_signal_patterns(text) or ["deep_research_evidence"]
        geo_hints = [str(region) for region in analysis.get("country_area_hints", [])] or infer_geo(text, str(item.get("subreddit", "")))
        return RawEvidence(
            evidence_id=f"dre_{hashlib.sha1((requirement.requirement_id + source_url + text).encode()).hexdigest()[:12]}",
            source="reddit_deep_research",
            source_url=source_url,
            subreddit=str(item.get("subreddit") or "unknown"),
            post_id=item.get("post_id"),
            comment_id=item.get("comment_id"),
            title=str(item.get("title") or ""),
            body=str(item.get("body") or ""),
            author_metadata_allowed=False,
            score=int(item.get("score", 0) or 0),
            comment_count=int(item.get("comment_count", 0) or 0),
            created_at=str(item.get("created_at") or fetched_at),
            fetched_at=fetched_at,
            language=str(item.get("language") or "en"),
            geo_hints=geo_hints,
            matched_patterns=signals,
            raw_payload={**item, "deep_research_analysis": analysis},
            task_group_id=item.get("task_group_id"),
            task_group_run_id=item.get("task_group_run_id"),
        )

    def _geo_distribution(self, evidence: list[RawEvidence]) -> list[dict[str, Any]]:
        counts: Counter[str] = Counter()
        for item in evidence:
            counts.update(item.geo_hints)
        total = max(sum(counts.values()), 1)
        return [
            {"region": region, "confidence": round(count / total, 2), "evidence_count": count}
            for region, count in counts.most_common()
        ]

    def _log_deep_research_step(
        self,
        requirement: RequirementRecord,
        task_group_id: str | None,
        task_group_run_id: str | None,
        step_name: str,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        self.storage.log_requirement_event(
            requirement.requirement_id,
            task_group_id,
            task_group_run_id,
            self.agent_id,
            self.role,
            step_name,
            message,
            payload,
        )
        if task_group_id or task_group_run_id:
            self.storage.log_experiment(task_group_id, task_group_run_id, self.role, step_name, message, payload)

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


def validate_requirement(
    requirement: RequirementRecord,
    evidence: list[RawEvidence],
    scores: dict[str, Any],
    alternatives: dict[str, Any],
) -> dict[str, Any]:
    regions = requirement.geo_distribution or [{"region": "unknown", "confidence": 0.0, "evidence_count": len(evidence)}]
    realness_factors = []
    if requirement.evidence_count >= 2:
        realness_factors.append("repeated evidence")
    if requirement.subreddit_count >= 2:
        realness_factors.append("appears across multiple communities")
    if scores["pain_intensity_score"] >= 30:
        realness_factors.append("observable pain language")
    if find_payment_signals(evidence):
        realness_factors.append("payment or switching signal")
    if scores["solution_gap_score"] >= 20:
        realness_factors.append("current workaround or alternative mentioned")
    is_real = scores["overall_score"] >= 45 or len(realness_factors) >= 3
    scale = signal_size(scores["overall_score"])
    if requirement.evidence_count >= 8 or requirement.subreddit_count >= 4:
        scale = "large"
    elif requirement.evidence_count >= 3 or requirement.subreddit_count >= 2:
        scale = "medium"
    return {
        "is_real_requirement": is_real,
        "realness_reason": "; ".join(realness_factors) if realness_factors else "Evidence is still thin and needs more samples.",
        "requirement_scale": scale,
        "scale_reason": (
            f"{requirement.evidence_count} evidence item(s), {requirement.subreddit_count} subreddit(s), "
            f"engagement score {scores['engagement_score']}."
        ),
        "country_area_distribution": regions,
        "current_alternatives": alternatives.get("existing_solutions", []),
        "solution_gap": alternatives.get("common_complaints", []),
    }


def one_sentence_requirement(requirement: RequirementRecord) -> str:
    sentence = " ".join(requirement.canonical_requirement.split())
    if not sentence.endswith((".", "!", "?")):
        sentence += "."
    return sentence[:240]


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


def search_feedback_terms(searches: list[dict[str, Any]]) -> list[str]:
    stop_words = {
        "with",
        "that",
        "this",
        "from",
        "users",
        "need",
        "needs",
        "problem",
        "pain",
        "workaround",
        "alternative",
        "solution",
    }
    terms: Counter[str] = Counter()
    for search in searches:
        query = str(search.get("query", "")).lower().replace("/", " ").replace("-", " ")
        for raw in query.split():
            term = raw.strip(".,:;!?()[]{}\"'")
            if len(term) < 4 or term in stop_words:
                continue
            terms[term] += 1
    return [term for term, _count in terms.most_common(8)]


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


def one_sentence_rejection_summary(
    requirement: RequirementRecord,
    evidence: list[RawEvidence],
    validation: dict[str, Any] | None = None,
) -> str:
    reasons = []
    if requirement.evidence_count < 3:
        reasons.append(f"only {requirement.evidence_count} evidence item(s)")
    if requirement.subreddit_count < 2:
        reasons.append(f"only {requirement.subreddit_count} subreddit(s)")
    if not find_payment_signals(evidence):
        reasons.append("no clear willingness-to-pay signal")
    if validation and validation.get("realness_reason") == "Evidence is still thin and needs more samples.":
        reasons.append("thin realness evidence")
    if not reasons:
        reasons.append("insufficient validation strength")
    return "Rejected because " + ", ".join(dict.fromkeys(reasons)) + "."


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


def make_recommendation(
    scores: dict[str, Any],
    status: RequirementStatus | None = None,
    validation: dict[str, Any] | None = None,
    rejection_summary: str | None = None,
) -> str:
    if status == RequirementStatus.REJECTED or (validation and not validation.get("is_real_requirement", True)):
        return rejection_summary or "Rejected because there is insufficient evidence that this is a real requirement."
    if status == RequirementStatus.VALIDATED or scores["overall_score"] >= 72:
        return "validated enough for human review and external validation"
    if status == RequirementStatus.WATCHING or scores["overall_score"] >= 45:
        return "keep tracking and run lightweight validation"
    return "watch for more evidence before acting"


def next_validation_step(scores: dict[str, Any]) -> str:
    if scores["monetization_score"] < 30:
        return "Search for stronger payment or switching signals before product investment."
    if scores["subreddit_spread_score"] < 40:
        return "Check adjacent subreddits to confirm the requirement is not isolated."
    return "Create a short opportunity brief and test demand with content or interviews."
