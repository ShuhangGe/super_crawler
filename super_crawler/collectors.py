from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .models import TaskGroup, utc_now


class OpenCliRedditCollector:
    """Collect public Reddit search results through a user-provided opencli command."""

    def __init__(self, command: str = "opencli reddit search", timeout_seconds: int = 120):
        self.command = command
        self.timeout_seconds = timeout_seconds

    def collect_to_inbox(self, task_group: TaskGroup, run_id: str, limit: int = 25) -> dict[str, Any]:
        query = task_group.description or task_group.domain or task_group.name
        result = self.search(query=query, limit=limit)
        output_dir = Path(task_group.input_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"opencli_{safe_file_id(run_id)}.json"
        output_path.write_text(json.dumps(result["items"], indent=2, sort_keys=True), encoding="utf-8")
        return {
            "query": query,
            "items_collected": len(result["items"]),
            "output_path": str(output_path),
            "command": result["command"],
            "stderr": result["stderr"],
        }

    def collect_queries_to_inbox(
        self,
        task_group: TaskGroup,
        run_id: str,
        queries: list[str | dict[str, Any]],
        limit_per_query: int = 10,
        event_callback: Any | None = None,
    ) -> dict[str, Any]:
        output_dir = Path(task_group.input_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        results = []
        all_items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        assignments = [normalize_search_assignment(item, index) for index, item in enumerate(queries, start=1)]
        for assignment in assignments:
            index = int(assignment["index"])
            query = str(assignment["query"])
            agent_id = str(assignment["agent_id"])
            started_at = utc_now()
            if event_callback:
                event_callback(
                    "collector_query_started",
                    f"{agent_id} started OpenCLI query: {query}",
                    {"agent_id": agent_id, "query": query, "subreddit": assignment.get("subreddit", ""), "limit": limit_per_query, "command": self.command},
                )
            result = self.search(
                query=query,
                limit=limit_per_query,
                subreddit=str(assignment.get("subreddit", "")),
                sort=str(assignment.get("sort", "")),
                time=str(assignment.get("time", "")),
            )
            completed_at = utc_now()
            query_items = []
            for item in result["items"]:
                item = {
                    **item,
                    "search_agent_id": agent_id,
                    "search_agent_index": index,
                    "search_query": query,
                    "search_subreddit": assignment.get("subreddit", ""),
                    "search_strategy": assignment.get("strategy", ""),
                }
                url = str(item.get("source_url") or "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                query_items.append(item)
                all_items.append(item)
            output_path = output_dir / f"opencli_{safe_file_id(run_id)}_agent_{index}.json"
            output_path.write_text(json.dumps(query_items, indent=2, sort_keys=True), encoding="utf-8")
            if event_callback:
                event_callback(
                    "collector_query_completed",
                    f"{agent_id} completed OpenCLI query with {len(query_items)} item(s): {query}",
                    {
                        "agent_id": agent_id,
                        "query": query,
                        "subreddit": assignment.get("subreddit", ""),
                        "strategy": assignment.get("strategy", ""),
                        "items_collected": len(query_items),
                        "output_path": str(output_path),
                        "urls": [item.get("source_url", "") for item in query_items],
                    },
                )
            results.append(
                {
                    "agent_id": agent_id,
                    "query": query,
                    "subreddit": assignment.get("subreddit", ""),
                    "strategy": assignment.get("strategy", ""),
                    "sort": assignment.get("sort", ""),
                    "time": assignment.get("time", ""),
                    "items_collected": len(query_items),
                    "urls": [item.get("source_url", "") for item in query_items],
                    "titles": [item.get("title", "") for item in query_items],
                    "subreddits": sorted({str(item.get("subreddit", "")) for item in query_items if item.get("subreddit")}),
                    "output_path": str(output_path),
                    "command": result["command"],
                    "stderr": result["stderr"],
                    "started_at": started_at,
                    "completed_at": completed_at,
                }
            )
        return {
            "queries": [assignment["query"] for assignment in assignments],
            "assignments": assignments,
            "items_collected": len(all_items),
            "limit_per_query": limit_per_query,
            "search_agents": results,
        }

    def search(self, query: str, limit: int = 25, subreddit: str = "", sort: str = "", time: str = "") -> dict[str, Any]:
        command = [*shlex.split(self.command), query, "--limit", str(limit), "-f", "json"]
        if subreddit:
            command.extend(["--subreddit", subreddit])
        if sort:
            command.extend(["--sort", sort])
        if time:
            command.extend(["--time", time])
        completed = run_opencli_command(command, self.timeout_seconds)
        raw_items = parse_opencli_output(completed.stdout)
        return {
            "command": command,
            "stderr": completed.stderr.strip(),
            "items": [normalize_reddit_item(item, query) for item in raw_items],
        }


class OpenCliSourceRouter:
    """Route a planned research source to the best available OpenCLI adapter."""

    def __init__(self, command: str = "opencli reddit search", timeout_seconds: int = 120):
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.reddit = OpenCliRedditCollector(command=command, timeout_seconds=timeout_seconds)

    def search(
        self,
        query: str,
        limit: int = 25,
        subreddit: str = "",
        sort: str = "",
        time: str = "",
        source: str = "reddit",
    ) -> dict[str, Any]:
        source_key = normalize_source_key(source)
        if source_key == "reddit":
            result = self.reddit.search(query=query, limit=limit, subreddit=subreddit, sort=sort, time=time)
            result["source_adapter"] = "reddit"
            return result
        if source_key == "youtube":
            return self._search_opencli(
                ["opencli", "youtube", "search", query, "--limit", str(limit), "-f", "json"],
                query,
                normalize_youtube_item,
                "youtube",
            )
        if source_key == "google_web":
            return self._search_opencli(
                ["opencli", "google", "search", query, "--limit", str(limit), "-f", "json"],
                query,
                normalize_web_search_item,
                "google_web",
            )
        if source_key in {"amazon", "product_reviews"}:
            return self._search_opencli(
                ["opencli", "amazon", "search", query, "--limit", str(limit), "-f", "json"],
                query,
                normalize_amazon_item,
                "amazon",
            )
        if source_key == "producthunt":
            return self._search_opencli(
                ["opencli", "producthunt", "posts", "--limit", str(limit), "-f", "json"],
                query,
                normalize_producthunt_item,
                "producthunt",
            )
        result = self.reddit.search(query=query, limit=limit, subreddit=subreddit, sort=sort, time=time)
        result["source_adapter"] = "reddit_fallback"
        return result

    def _search_opencli(
        self,
        command: list[str],
        query: str,
        normalizer: Any,
        source_adapter: str,
    ) -> dict[str, Any]:
        completed = run_opencli_command(command, self.timeout_seconds)
        raw_items = parse_opencli_output(completed.stdout)
        return {
            "command": command,
            "stderr": completed.stderr.strip(),
            "source_adapter": source_adapter,
            "items": [normalizer(item, query) for item in raw_items],
        }


def normalize_search_assignment(item: str | dict[str, Any], index: int) -> dict[str, Any]:
    if isinstance(item, dict):
        query = str(item.get("query", "")).strip()
        return {
            "index": index,
            "agent_id": str(item.get("agent_id") or f"search-agent-{index}"),
            "strategy": str(item.get("strategy") or ""),
            "query": query,
            "subreddit": str(item.get("subreddit") or "").strip(),
            "sort": str(item.get("sort") or "relevance").strip(),
            "time": str(item.get("time") or "year").strip(),
            "why": str(item.get("why") or ""),
        }
    return {
        "index": index,
        "agent_id": f"search-agent-{index}",
        "strategy": "",
        "query": str(item).strip(),
        "subreddit": "",
        "sort": "",
        "time": "",
        "why": "",
    }


def parse_opencli_output(output: str) -> list[dict[str, Any]]:
    output = output.strip()
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        rows = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        return rows
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        for key in ["items", "results", "data", "posts"]:
            value = parsed.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [parsed]
    return []


def run_opencli_command(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    executable = command[0]
    if shutil.which(executable) is None:
        raise RuntimeError(
            f"OpenCLI executable '{executable}' was not found on PATH. "
            "Install it with: npm install -g @jackwener/opencli. "
            "Then restart the dashboard so it inherits the updated PATH."
        )
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"opencli command timed out after {timeout_seconds} seconds. "
            f"Command: {' '.join(command)}"
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "opencli command failed").strip())
    return completed


def normalize_source_key(source: str) -> str:
    value = source.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "web": "google_web",
        "google": "google_web",
        "forums": "google_web",
        "forum_scan": "google_web",
        "competitor_pages": "google_web",
        "youtube_forums": "youtube",
        "video": "youtube",
        "app_store": "google_web",
        "reviews": "product_reviews",
        "product_review": "product_reviews",
        "amazon_reviews": "product_reviews",
    }
    return aliases.get(value, value or "reddit")


def normalize_reddit_item(item: dict[str, Any], query: str) -> dict[str, Any]:
    title = str(first_present(item, ["title", "name", "headline"]) or "")
    body = str(first_present(item, ["body", "selftext", "text", "content", "description"]) or "")
    url = str(first_present(item, ["source_url", "url", "permalink", "link"]) or "")
    if url.startswith("/"):
        url = "https://www.reddit.com" + url
    subreddit = str(first_present(item, ["subreddit", "subreddit_name_prefixed", "community"]) or "unknown")
    if subreddit.startswith("r/"):
        subreddit = subreddit[2:]
    created_at = str(first_present(item, ["created_at", "created", "created_utc"]) or utc_now())
    return {
        "source": "reddit_opencli",
        "source_url": url,
        "subreddit": subreddit,
        "post_id": first_present(item, ["post_id", "id"]),
        "comment_id": first_present(item, ["comment_id"]),
        "title": title,
        "body": body,
        "author_metadata_allowed": False,
        "score": int_or_zero(first_present(item, ["score", "ups", "upvotes"])),
        "comment_count": int_or_zero(first_present(item, ["comment_count", "num_comments", "comments"])),
        "created_at": created_at,
        "language": str(first_present(item, ["language", "lang"]) or "en"),
        "collection_query": query,
        "raw_payload": item,
    }


def normalize_web_search_item(item: dict[str, Any], query: str) -> dict[str, Any]:
    title = str(first_present(item, ["title", "name", "headline"]) or "")
    snippet = str(first_present(item, ["snippet", "body", "description", "text"]) or "")
    url = str(first_present(item, ["url", "source_url", "link"]) or "")
    return {
        "source": "google_web_opencli",
        "source_url": url,
        "subreddit": "web",
        "post_id": None,
        "comment_id": None,
        "title": title,
        "body": snippet,
        "author_metadata_allowed": False,
        "score": 0,
        "comment_count": 0,
        "created_at": str(first_present(item, ["date", "published", "created_at"]) or utc_now()),
        "language": str(first_present(item, ["language", "lang"]) or "en"),
        "collection_query": query,
        "raw_payload": item,
    }


def normalize_youtube_item(item: dict[str, Any], query: str) -> dict[str, Any]:
    title = str(first_present(item, ["title", "name"]) or "")
    channel = str(first_present(item, ["channel", "author"]) or "")
    views = str(first_present(item, ["views", "view_count"]) or "")
    published = str(first_present(item, ["published", "date", "created_at"]) or "")
    body = " | ".join(part for part in [channel, views, published] if part)
    return {
        "source": "youtube_opencli",
        "source_url": str(first_present(item, ["url", "source_url", "link"]) or ""),
        "subreddit": "youtube",
        "post_id": first_present(item, ["video_id", "id"]),
        "comment_id": None,
        "title": title,
        "body": body,
        "author_metadata_allowed": False,
        "score": int_or_zero(first_present(item, ["likes", "score"])),
        "comment_count": int_or_zero(first_present(item, ["comments", "comment_count"])),
        "created_at": published or utc_now(),
        "language": str(first_present(item, ["language", "lang"]) or "en"),
        "collection_query": query,
        "raw_payload": item,
    }


def normalize_amazon_item(item: dict[str, Any], query: str) -> dict[str, Any]:
    title = str(first_present(item, ["title", "name"]) or "")
    price = str(first_present(item, ["price_text", "price"]) or "")
    rating = str(first_present(item, ["rating_value", "rating"]) or "")
    reviews = str(first_present(item, ["review_count", "reviews"]) or "")
    body = " | ".join(part for part in [price, f"rating {rating}" if rating else "", f"reviews {reviews}" if reviews else ""] if part)
    asin = str(first_present(item, ["asin", "id"]) or "")
    url = str(first_present(item, ["url", "source_url", "link"]) or "")
    if not url and asin:
        url = f"https://www.amazon.com/dp/{asin}"
    return {
        "source": "amazon_opencli",
        "source_url": url,
        "subreddit": "amazon",
        "post_id": asin or None,
        "comment_id": None,
        "title": title,
        "body": body,
        "author_metadata_allowed": False,
        "score": int_or_zero(reviews),
        "comment_count": int_or_zero(reviews),
        "created_at": utc_now(),
        "language": str(first_present(item, ["language", "lang"]) or "en"),
        "collection_query": query,
        "raw_payload": item,
    }


def normalize_producthunt_item(item: dict[str, Any], query: str) -> dict[str, Any]:
    name = str(first_present(item, ["name", "title"]) or "")
    tagline = str(first_present(item, ["tagline", "description", "body"]) or "")
    return {
        "source": "producthunt_opencli",
        "source_url": str(first_present(item, ["url", "source_url", "link"]) or ""),
        "subreddit": "producthunt",
        "post_id": first_present(item, ["id", "rank"]),
        "comment_id": None,
        "title": name,
        "body": tagline,
        "author_metadata_allowed": False,
        "score": int_or_zero(first_present(item, ["votes", "reviews", "rank"])),
        "comment_count": int_or_zero(first_present(item, ["comments", "reviews"])),
        "created_at": str(first_present(item, ["date", "created_at"]) or utc_now()),
        "language": str(first_present(item, ["language", "lang"]) or "en"),
        "collection_query": query,
        "raw_payload": item,
    }


def first_present(item: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def int_or_zero(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def safe_file_id(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")[:120] or "run"


def build_requirement_search_queries(task_group: TaskGroup, count: int) -> list[str]:
    base = (task_group.description or task_group.domain or task_group.name).strip()
    if not base:
        base = "user workflow pain"
    domain = (task_group.domain or task_group.description or task_group.name).strip()
    templates = [
        "{base} problem pain workflow",
        "is there an app for {domain}",
        "best way to manage {domain}",
        "alternative to {domain} app",
        "tired of {domain} manual workflow",
        "how do people handle {domain}",
        "{domain} spreadsheet workaround",
        "{domain} annoying problem",
    ]
    queries: list[str] = []
    for template in templates:
        query = template.format(base=base, domain=domain).strip()
        if query and query not in queries:
            queries.append(query)
        if len(queries) >= max(count, 1):
            break
    return queries
