from __future__ import annotations

from typing import Any

from .models import TaskGroup
from .text import keywords


class SearchPlannerAgent:
    role = "search_planner"

    def __init__(self, agent_id: str = "search-planner"):
        self.agent_id = agent_id

    def plan(
        self,
        task_group: TaskGroup,
        search_agent_count: int,
        cycle_index: int,
        recent_queries: list[str] | None = None,
        search_insights: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        description = (task_group.description or task_group.domain or task_group.name).strip()
        if not description:
            description = "user workflow pain"
        brief = build_search_brief(description, task_group)
        insights = search_insights or []
        default_candidates = build_query_candidates(brief)
        feedback_candidates = build_feedback_candidates(insights)
        candidates = merge_candidates(feedback_candidates + default_candidates)
        recent = set(recent_queries or [])
        avoid = avoided_queries(insights)
        avoid_strategies = avoided_strategies(insights)
        offset = ((cycle_index - 1) * max(search_agent_count, 1)) % max(len(candidates), 1)
        ordered = candidates[offset:] + candidates[:offset]
        selected = select_search_candidates(
            ordered,
            default_candidates,
            max(search_agent_count, 1),
            recent,
            avoid,
            avoid_strategies,
        )
        assignments = [
            {
                "agent_id": f"search-agent-{index}",
                "strategy": item["strategy"],
                "query": item["query"],
                "subreddit": item.get("subreddit", ""),
                "sort": item.get("sort", "relevance"),
                "time": item.get("time", "year"),
                "why": item["why"],
            }
            for index, item in enumerate(selected, start=1)
        ]
        return {
            "planner_agent_id": self.agent_id,
            "cycle_index": cycle_index,
            "task_group_id": task_group.task_group_id,
            "input_description": description,
            "search_goal": brief["search_goal"],
            "search_brief": brief,
            "search_insights": [item.get("payload_json", {}) for item in (search_insights or [])],
            "assignments": assignments,
            "queries": [item["query"] for item in assignments],
        }


def build_search_brief(description: str, task_group: TaskGroup) -> dict[str, Any]:
    lower = description.lower()
    domain_terms = keywords(description, limit=8) or keywords(task_group.name, limit=8)
    if "3c" in lower or "consumer electronics" in lower:
        domain = "consumer electronics / 3C products"
        audiences = ["electronics buyers", "online shoppers", "product reviewers", "small electronics sellers"]
        coverage = ["phones", "laptops", "headphones", "chargers", "smart devices", "accessories", "after-sales support"]
        known_tools = ["Amazon", "Best Buy", "AliExpress", "Temu", "Reddit reviews", "YouTube reviews"]
        subreddits = ["BuyItForLife", "HeadphoneAdvice", "buildapc", "techsupport", "amazonprime", "smarthome", "UsbCHardware", "SuggestALaptop"]
        product_terms = [
            "phone",
            "iphone",
            "android",
            "laptop",
            "headphone",
            "earbud",
            "charger",
            "cable",
            "usb-c",
            "monitor",
            "keyboard",
            "mouse",
            "camera",
            "router",
            "ssd",
            "gpu",
            "rtx",
            "smart home",
            "amazon",
            "warranty",
            "refund",
        ]
    elif "photo" in lower or "photograph" in lower:
        domain = "photography"
        audiences = ["photographers", "wedding photographers", "freelance photographers", "clients"]
        coverage = ["client galleries", "photo proofing", "booking", "editing backlog", "file delivery", "client feedback"]
        known_tools = ["Pixieset", "SmugMug", "Google Drive", "Dropbox", "Lightroom"]
        subreddits = ["photography", "WeddingPhotography", "AskPhotography", "Lightroom", "photographybusiness"]
        product_terms = ["photo", "photography", "camera", "lens", "client", "gallery", "proofing", "editing", "lightroom", "wedding"]
    else:
        domain = task_group.domain or " ".join(domain_terms[:3]) or task_group.name
        audiences = [f"{domain} users", f"{domain} customers", f"{domain} operators"]
        coverage = domain_terms[:6] or [domain, "workflow", "buying", "support", "comparison"]
        known_tools = ["spreadsheet", "manual workflow", "apps", "marketplaces", "forums"]
        subreddits = []
        product_terms = domain_terms[:10]
    return {
        "domain": domain,
        "audiences": audiences,
        "coverage_targets": coverage,
        "known_tools": known_tools,
        "subreddits": subreddits,
        "product_terms": product_terms,
        "pain_terms": ["problem", "pain", "annoying", "complaint", "tired of", "hard to", "manual"],
        "search_goal": f"Find possible product requirements and user pain points for {domain}.",
    }


def apply_search_insights(candidates: list[dict[str, str]], insights: list[dict[str, Any]]) -> list[dict[str, str]]:
    return merge_candidates(build_feedback_candidates(insights) + candidates)


def build_feedback_candidates(insights: list[dict[str, Any]]) -> list[dict[str, str]]:
    learned: list[dict[str, str]] = []
    for insight in insights:
        payload = insight.get("payload_json", insight)
        if not isinstance(payload, dict):
            continue
        for item in payload.get("suggested_searches", []):
            if not isinstance(item, dict):
                continue
            query = str(item.get("query") or "").strip()
            if not query:
                continue
            learned.append(
                {
                    "strategy": str(item.get("strategy") or "learned_followup"),
                    "query": query,
                    "subreddit": str(item.get("subreddit") or ""),
                    "sort": str(item.get("sort") or "relevance"),
                    "time": str(item.get("time") or "year"),
                    "why": str(item.get("why") or "Deep research found this direction worth another search."),
                }
            )
        dimensions = payload.get("productive_dimensions", {})
        if isinstance(dimensions, dict):
            query_terms = [str(term) for term in dimensions.get("query_terms", []) if str(term).strip()]
            strategies = [str(strategy) for strategy in dimensions.get("strategies", []) if str(strategy).strip()]
            subreddits = [str(subreddit) for subreddit in dimensions.get("subreddits", []) if str(subreddit).strip()]
            if query_terms:
                learned.append(
                    {
                        "strategy": f"learned_{strategies[0]}_adjacent" if strategies else "learned_adjacent",
                        "query": " ".join(query_terms[:6] + ["problem", "alternative"]),
                        "subreddit": subreddits[0] if subreddits else "",
                        "sort": "relevance",
                        "time": "year",
                        "why": "Deep research found these terms, subreddit, or strategy productive; run an adjacent follow-up search.",
                    }
                )
    return merge_candidates(learned)


def merge_candidates(candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        query = item["query"]
        if query in seen:
            continue
        seen.add(query)
        result.append(item)
    return result


def select_search_candidates(
    ordered: list[dict[str, str]],
    default_candidates: list[dict[str, str]],
    count: int,
    recent: set[str],
    avoid: set[str],
    avoid_strategies: set[str],
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    for candidate in ordered:
        if should_skip_candidate(candidate, selected, ordered, recent, avoid, avoid_strategies):
            continue
        selected.append(candidate)
        if len(selected) >= count:
            break
    if count >= 3 and selected and default_candidates and not any(item in default_candidates for item in selected):
        baseline = first_eligible_default(default_candidates, selected, recent, avoid, avoid_strategies)
        if baseline is not None:
            selected[-1] = baseline
    if len(selected) < count:
        for candidate in ordered:
            if candidate not in selected:
                selected.append(candidate)
            if len(selected) >= count:
                break
    return selected


def should_skip_candidate(
    candidate: dict[str, str],
    selected: list[dict[str, str]],
    candidates: list[dict[str, str]],
    recent: set[str],
    avoid: set[str],
    avoid_strategies: set[str],
) -> bool:
    query = candidate["query"]
    strategy = candidate.get("strategy", "")
    if query in avoid and len(selected) + len(avoid) < len(candidates):
        return True
    if strategy in avoid_strategies and len(selected) + len(avoid_strategies) < len(candidates):
        return True
    if query in recent and len(selected) + len(recent) < len(candidates):
        return True
    return False


def first_eligible_default(
    default_candidates: list[dict[str, str]],
    selected: list[dict[str, str]],
    recent: set[str],
    avoid: set[str],
    avoid_strategies: set[str],
) -> dict[str, str] | None:
    for candidate in default_candidates:
        if candidate in selected:
            continue
        if candidate["query"] in avoid or candidate["query"] in recent:
            continue
        if candidate.get("strategy", "") in avoid_strategies:
            continue
        return candidate
    return None


def avoided_queries(insights: list[dict[str, Any]]) -> set[str]:
    avoid: set[str] = set()
    productive: set[str] = set()
    for insight in insights:
        payload = insight.get("payload_json", insight)
        if not isinstance(payload, dict):
            continue
        productive.update(str(query) for query in payload.get("productive_queries", []) if query)
        avoid.update(str(query) for query in payload.get("noisy_queries", []) if query)
    return avoid - productive


def avoided_strategies(insights: list[dict[str, Any]]) -> set[str]:
    avoid: set[str] = set()
    productive: set[str] = set()
    for insight in insights:
        payload = insight.get("payload_json", insight)
        if not isinstance(payload, dict):
            continue
        allocation = payload.get("recommended_allocation_change", {})
        if isinstance(allocation, dict):
            productive.update(str(strategy) for strategy in allocation.get("increase", []) if strategy)
            avoid.update(str(strategy) for strategy in allocation.get("decrease", []) if strategy)
        productive_dimensions = payload.get("productive_dimensions", {})
        if isinstance(productive_dimensions, dict):
            productive.update(str(strategy) for strategy in productive_dimensions.get("strategies", []) if strategy)
        unproductive_dimensions = payload.get("unproductive_dimensions", {})
        if isinstance(unproductive_dimensions, dict):
            avoid.update(str(strategy) for strategy in unproductive_dimensions.get("strategies", []) if strategy)
    return avoid - productive


def build_query_candidates(brief: dict[str, Any]) -> list[dict[str, str]]:
    domain = str(brief["domain"])
    audiences = [str(item) for item in brief["audiences"]]
    coverage = [str(item) for item in brief["coverage_targets"]]
    tools = [str(item) for item in brief["known_tools"]]
    subreddits = [str(item) for item in brief.get("subreddits", [])]
    primary_audience = audiences[0]
    primary_target = coverage[0]
    second_target = coverage[1] if len(coverage) > 1 else primary_target
    primary_tool = tools[0]
    if "3C products" in domain or "consumer electronics" in domain:
        return [
            {
                "strategy": "purchase_regret",
                "query": "regret buying laptop phone headphones charger broke warranty",
                "subreddit": "BuyItForLife",
                "sort": "relevance",
                "time": "year",
                "why": "Find durable-goods complaints and purchase regret from electronics buyers.",
            },
            {
                "strategy": "advice_pain",
                "query": "confused choosing laptop overheating battery warranty",
                "subreddit": "SuggestALaptop",
                "sort": "relevance",
                "time": "year",
                "why": "Find concrete laptop buying pains and decision criteria.",
            },
            {
                "strategy": "headphone_problem",
                "query": "headphones earbuds uncomfortable noise cancelling connection problem",
                "subreddit": "HeadphoneAdvice",
                "sort": "relevance",
                "time": "year",
                "why": "Find accessory pain from users asking for better alternatives.",
            },
            {
                "strategy": "pc_hardware_problem",
                "query": "monitor gpu ssd laptop dock compatibility problem return",
                "subreddit": "buildapc",
                "sort": "relevance",
                "time": "year",
                "why": "Find compatibility and setup problems in PC hardware buying.",
            },
            {
                "strategy": "support_gap",
                "query": "phone laptop headphones warranty repair refund support problem",
                "subreddit": "techsupport",
                "sort": "relevance",
                "time": "year",
                "why": "Find after-sales support and repair pain.",
            },
            {
                "strategy": "ecommerce_trust",
                "query": "Amazon electronics wrong item refund denied phone laptop gpu",
                "subreddit": "amazonprime",
                "sort": "relevance",
                "time": "year",
                "why": "Find trust, delivery, refund, and fraud requirements around online electronics purchases.",
            },
            {
                "strategy": "smart_home_privacy",
                "query": "smart camera doorbell subscription privacy alternative problem",
                "subreddit": "smarthome",
                "sort": "relevance",
                "time": "year",
                "why": "Find smart device privacy, subscription, and reliability pain.",
            },
            {
                "strategy": "charging_cable_problem",
                "query": "usb c charger cable unsafe failed compatibility problem",
                "subreddit": "UsbCHardware",
                "sort": "relevance",
                "time": "year",
                "why": "Find charger, cable, and compatibility issues.",
            },
        ]
    return [
        {
            "strategy": "broad_pain",
            "query": f"{primary_audience} {primary_target} problem pain",
            "subreddit": subreddits[0] if subreddits else "",
            "sort": "relevance",
            "time": "year",
            "why": "Find broad user pain and unmet needs.",
        },
        {
            "strategy": "buying_intent",
            "query": f"best {domain} for {second_target} buying advice problem",
            "subreddit": subreddits[1] if len(subreddits) > 1 else "",
            "sort": "relevance",
            "time": "year",
            "why": "Find product-selection pain and purchase criteria.",
        },
        {
            "strategy": "alternative_comparison",
            "query": f"alternative to {primary_tool} for {domain}",
            "subreddit": subreddits[2] if len(subreddits) > 2 else "",
            "sort": "relevance",
            "time": "year",
            "why": "Find dissatisfaction with current alternatives.",
        },
        {
            "strategy": "complaint_reviews",
            "query": f"{domain} review complaint annoying problem",
            "subreddit": subreddits[3] if len(subreddits) > 3 else "",
            "sort": "relevance",
            "time": "year",
            "why": "Find complaints in reviews and discussion threads.",
        },
        {
            "strategy": "feature_request",
            "query": f"is there an app or tool for {domain} {primary_target}",
            "subreddit": subreddits[4] if len(subreddits) > 4 else "",
            "sort": "relevance",
            "time": "year",
            "why": "Find explicit requests for tools or features.",
        },
        {
            "strategy": "manual_workaround",
            "query": f"{primary_audience} {primary_target} spreadsheet manual workflow",
            "subreddit": subreddits[5] if len(subreddits) > 5 else "",
            "sort": "relevance",
            "time": "year",
            "why": "Find workaround behavior that suggests product opportunity.",
        },
        {
            "strategy": "community_question",
            "query": f"how do people handle {domain} {second_target}",
            "subreddit": subreddits[6] if len(subreddits) > 6 else "",
            "sort": "relevance",
            "time": "year",
            "why": "Find open-ended community questions.",
        },
        {
            "strategy": "support_gap",
            "query": f"{domain} after sales support warranty return problem",
            "subreddit": subreddits[7] if len(subreddits) > 7 else "",
            "sort": "relevance",
            "time": "year",
            "why": "Find service and support requirement signals.",
        },
    ]
