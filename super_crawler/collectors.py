from __future__ import annotations

import json
import shlex
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

    def search(self, query: str, limit: int = 25) -> dict[str, Any]:
        command = [*shlex.split(self.command), query, "--limit", str(limit), "--json"]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"opencli command timed out after {self.timeout_seconds} seconds") from exc
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "opencli command failed").strip())
        raw_items = parse_opencli_output(completed.stdout)
        return {
            "command": command,
            "stderr": completed.stderr.strip(),
            "items": [normalize_reddit_item(item, query) for item in raw_items],
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
