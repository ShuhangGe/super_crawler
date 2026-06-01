from __future__ import annotations

import json
from typing import Any

from .llm import DeepSeekClient
from .models import TaskGroup


DEFAULT_SEARCH_MODEL = "deepseek-v4-flash"


class SearchPlannerAgent:
    role = "search_planner"

    def __init__(
        self,
        agent_id: str = "search-planner",
        llm_client: DeepSeekClient | None = None,
        model_name: str = DEFAULT_SEARCH_MODEL,
    ):
        self.agent_id = agent_id
        self.llm_client = llm_client
        self.model_name = model_name

    def plan(
        self,
        task_group: TaskGroup,
        search_agent_count: int,
        cycle_index: int,
        recent_queries: list[str] | None = None,
        search_insights: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        requested_count = max(int(search_agent_count), 1)
        description = (task_group.description or task_group.domain or task_group.name).strip()
        insights = normalize_search_insights(search_insights or [])
        recent = [str(query) for query in (recent_queries or []) if str(query).strip()]
        llm = self.llm_client if self.llm_client is not None else DeepSeekClient()
        if not llm.available():
            return unavailable_plan(self.agent_id, task_group, cycle_index, description, requested_count, recent, insights)

        try:
            parsed = llm.json_chat(
                self.model_name,
                planner_system_prompt(),
                planner_user_prompt(task_group, requested_count, cycle_index, recent, insights),
            )
        except Exception as exc:  # noqa: BLE001 - planning failure should be explicit in the saved plan.
            return unavailable_plan(
                self.agent_id,
                task_group,
                cycle_index,
                description,
                requested_count,
                recent,
                insights,
                error=str(exc),
            )
        return normalize_llm_plan(parsed, self.agent_id, self.model_name, task_group, cycle_index, description, requested_count, recent, insights)


def planner_system_prompt() -> str:
    return (
        "You are a senior search planning agent for a Reddit requirement discovery pipeline. "
        "Planning is a heavy reasoning step. You must understand the user's original business/product requirement, "
        "read prior search plans, and use deep research feedback to decide what to search next. "
        "Do not use hard-coded domain assumptions. Infer the domain from the user input and evidence feedback. "
        "Prefer English Reddit search queries even when the user input is Chinese. "
        "All user-facing explanation fields must be written in Simplified Chinese, including search_goal, why, "
        "domain_understanding, must_match, reject_if, and deep_research_lessons. Keep the search query itself in "
        "English when searching English-language Reddit is appropriate. "
        "Use productive deep research dimensions to deepen promising directions and use noisy dimensions to avoid bad directions. "
        "Return only JSON."
    )


def planner_user_prompt(
    task_group: TaskGroup,
    search_agent_count: int,
    cycle_index: int,
    recent_queries: list[str],
    search_insights: list[dict[str, Any]],
) -> str:
    return json.dumps(
        {
            "task": {
                "task_group_id": task_group.task_group_id,
                "name": task_group.name,
                "type": task_group.task_type.value,
                "domain": task_group.domain,
                "description": task_group.description,
                "user_keywords": task_group.keywords,
                "user_negative_keywords": task_group.negative_keywords,
                "user_subreddits": task_group.subreddits,
            },
            "planning_context": {
                "cycle_index": cycle_index,
                "search_agent_count": search_agent_count,
                "recent_queries_to_avoid_repeating": recent_queries,
                "deep_research_feedback": search_insights,
            },
            "instructions": {
                "goal": "Create a search plan that finds Reddit posts expressing real user needs related to the user's stated product/domain.",
                "language": "除 query 和 subreddit 之外，所有输出文本字段必须使用简体中文。",
                "deep_research_usage": [
                    "Continue or broaden dimensions that produced relevant evidence.",
                    "Avoid exact noisy queries and explain how the new plan avoids them.",
                    "If feedback conflicts with the original user requirement, prioritize the original user requirement and mark conflicting feedback as noisy.",
                ],
                "query_requirements": [
                    "Each query should be specific enough for Reddit search.",
                    "Queries must not simply paste the user's original paragraph.",
                    "Queries should be in English unless a non-English query is clearly needed.",
                    "Use subreddit only when it improves precision; otherwise leave it empty.",
                ],
                "expected_json": {
                    "search_goal": "中文说明本轮搜索目标",
                    "search_brief": {
                        "domain_understanding": "中文说明用户真正想发现什么需求",
                        "target_users": ["中文描述潜在用户"],
                        "product_or_problem_scope": ["中文描述产品或问题范围"],
                        "must_match": ["中文描述相关结果必须满足的语义条件"],
                        "reject_if": ["中文描述应拒绝的噪音条件"],
                        "deep_research_lessons": ["中文说明 deep research 反馈如何影响本轮计划"],
                        "planning_method": "llm",
                    },
                    "assignments": [
                        {
                            "strategy": "short snake_case strategy name",
                            "query": "reddit search query",
                            "subreddit": "optional subreddit without r/ prefix",
                            "sort": "relevance | top | new | comments",
                            "time": "day | week | month | year | all",
                            "why": "why this search should find relevant requirements",
                        }
                    ],
                },
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def normalize_llm_plan(
    parsed: dict[str, Any],
    agent_id: str,
    model_name: str,
    task_group: TaskGroup,
    cycle_index: int,
    description: str,
    requested_count: int,
    recent_queries: list[str],
    search_insights: list[dict[str, Any]],
) -> dict[str, Any]:
    brief = parsed.get("search_brief")
    if not isinstance(brief, dict):
        brief = {}
    brief = {
        "domain_understanding": str(brief.get("domain_understanding") or task_group.domain or task_group.name),
        "target_users": string_list(brief.get("target_users")),
        "product_or_problem_scope": string_list(brief.get("product_or_problem_scope")),
        "must_match": string_list(brief.get("must_match")),
        "reject_if": string_list(brief.get("reject_if")),
        "deep_research_lessons": string_list(brief.get("deep_research_lessons")),
        "planning_method": "llm",
        "model": model_name,
    }
    assignments = normalize_assignments(parsed.get("assignments"), requested_count)
    return {
        "planner_agent_id": agent_id,
        "cycle_index": cycle_index,
        "task_group_id": task_group.task_group_id,
        "input_description": description,
        "search_goal": str(parsed.get("search_goal") or f"为 {task_group.name} 寻找相关的 Reddit 用户需求信号。"),
        "search_brief": {
            **brief,
            "recent_queries_considered": recent_queries,
            "deep_research_feedback_considered": search_insights,
        },
        "search_insights": search_insights,
        "assignments": [
            {
                **assignment,
                "agent_id": f"search-agent-{index}",
            }
            for index, assignment in enumerate(assignments, start=1)
        ],
        "queries": [assignment["query"] for assignment in assignments],
    }


def normalize_assignments(value: object, requested_count: int) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    assignments: list[dict[str, str]] = []
    seen_queries: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        if not query or query in seen_queries:
            continue
        seen_queries.add(query)
        assignments.append(
            {
                "strategy": safe_strategy(item.get("strategy")),
                "query": query,
                "subreddit": clean_subreddit(item.get("subreddit")),
                "sort": clean_choice(item.get("sort"), {"relevance", "top", "new", "comments"}, "relevance"),
                "time": clean_choice(item.get("time"), {"day", "week", "month", "year", "all"}, "year"),
                "why": str(item.get("why") or "由 AI 根据用户需求和深度研究反馈规划。").strip(),
            }
        )
        if len(assignments) >= requested_count:
            break
    return assignments


def normalize_search_insights(insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for insight in insights:
        payload = insight.get("payload_json", insight)
        if not isinstance(payload, dict):
            continue
        normalized.append(
            {
                "requirement_id": str(payload.get("requirement_id") or insight.get("requirement_id") or ""),
                "research_run_id": str(payload.get("research_run_id") or insight.get("research_run_id") or ""),
                "recommendation": str(payload.get("recommendation") or ""),
                "status_after_research": str(payload.get("status_after_research") or ""),
                "productive_queries": string_list(payload.get("productive_queries")),
                "noisy_queries": string_list(payload.get("noisy_queries")),
                "suggested_searches": dict_list(payload.get("suggested_searches")),
                "productive_dimensions": dict_value(payload.get("productive_dimensions")),
                "unproductive_dimensions": dict_value(payload.get("unproductive_dimensions")),
                "recommended_allocation_change": dict_value(payload.get("recommended_allocation_change")),
            }
        )
    return normalized


def unavailable_plan(
    agent_id: str,
    task_group: TaskGroup,
    cycle_index: int,
    description: str,
    requested_count: int,
    recent_queries: list[str],
    search_insights: list[dict[str, Any]],
    error: str | None = None,
) -> dict[str, Any]:
    reason = error or "AI 搜索规划器不可用：未配置 API key。"
    assignments = [
        {
            "agent_id": f"search-agent-{index}",
            "strategy": "waiting_for_llm_planner",
            "query": "",
            "subreddit": "",
            "sort": "relevance",
            "time": "year",
            "why": reason,
        }
        for index in range(1, requested_count + 1)
    ]
    return {
        "planner_agent_id": agent_id,
        "cycle_index": cycle_index,
        "task_group_id": task_group.task_group_id,
        "input_description": description,
        "search_goal": "搜索规划需要可用的 AI 搜索规划器。",
        "search_brief": {
            "domain_understanding": task_group.domain or task_group.name,
            "target_users": [],
            "product_or_problem_scope": [],
            "must_match": [],
            "reject_if": [],
            "deep_research_lessons": [],
            "planning_method": "llm_unavailable",
            "planner_error": reason,
            "requested_search_agents": requested_count,
            "recent_queries_considered": recent_queries,
            "deep_research_feedback_considered": search_insights,
        },
        "search_insights": search_insights,
        "assignments": assignments,
        "queries": [],
    }


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def dict_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def clean_subreddit(value: object) -> str:
    subreddit = str(value or "").strip()
    return subreddit[2:] if subreddit.startswith("r/") else subreddit


def clean_choice(value: object, allowed: set[str], default: str) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in allowed else default


def safe_strategy(value: object) -> str:
    strategy = str(value or "llm_planned_search").strip().lower().replace("-", "_").replace(" ", "_")
    return "".join(ch for ch in strategy if ch.isalnum() or ch == "_") or "llm_planned_search"
