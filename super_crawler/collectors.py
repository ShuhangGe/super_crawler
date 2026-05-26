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
        executable = command[0]
        if shutil.which(executable) is None:
            raise RuntimeError(
                f"OpenCLI executable '{executable}' was not found on PATH. "
                "Install it with: npm install -g @jackwener/opencli. "
                "Then restart the dashboard so it inherits the updated PATH. "
                "If you use a non-global install, set this group's OpenCLI command to the full executable path."
            )
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"opencli command timed out after {self.timeout_seconds} seconds for query: {query}. "
                f"Command: {' '.join(command)}"
            ) from exc
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "opencli command failed").strip())
        raw_items = parse_opencli_output(completed.stdout)
        return {
            "command": command,
            "stderr": completed.stderr.strip(),
            "items": [normalize_reddit_item(item, query) for item in raw_items],
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
