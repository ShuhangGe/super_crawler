# Super Crawler

Always-on Reddit requirement discovery system MVP based on `PRODUCT_PLAN.md`.

The implementation is dependency-light Python and stores product memory in SQLite:

- Raw Reddit evidence is saved before summarization.
- Discovery agents extract candidate requirements from Reddit-like items.
- The pool manager deduplicates, merges, scores, prioritizes, and queues requirements.
- Change detection can reopen watched or previously researched requirements.
- Deep research agents validate queued requirements and save structured research runs.
- Report and dashboard views expose pool, queue, detail, evidence, history, and daily summaries.

## Quick Start

```bash
python3 -m super_crawler.cli init
python3 -m super_crawler.cli seed
python3 -m super_crawler.cli run-cycle
python3 -m super_crawler.cli report --out reports/daily.md
python3 -m super_crawler.cli serve --port 8000
```

Open `http://127.0.0.1:8000` for the dashboard.

The dashboard has three main pages:

- Running Status: Start, Stop, Run Once, running research agents on the left, found requirements waiting for verification in the middle, and running deep research agents on the right.
- Possible Requirements: all non-rejected requirements as lineage rows from search agents to queue/pool, deep research agents, conclusion, and saved pipeline snapshot.
- Rejected Requirements: rejected or archived requirements using the same lineage-row structure.

Start runs the agent loop in the background, Stop halts it after the current cycle, and Run Once executes a single cycle immediately. Each finished cycle is saved as a pipeline snapshot that can be opened from the Running Status page.

## Ingest Custom Reddit Data

```bash
python3 -m super_crawler.cli ingest-json path/to/reddit_items.json
```

Input must be a JSON array of objects with fields such as:

```json
{
  "source_url": "https://reddit.com/r/example/comments/abc",
  "subreddit": "r/example",
  "post_id": "abc",
  "comment_id": null,
  "title": "Is there a tool for this workflow?",
  "body": "I am tired of using spreadsheets and would pay for a better way.",
  "score": 42,
  "comment_count": 18,
  "created_at": "2026-05-08T01:00:00+00:00",
  "language": "en"
}
```

## Always-On Runner

Place one or more JSON arrays in `data/reddit_inbox`, then run:

```bash
python3 -m super_crawler.cli daemon --input-dir data/reddit_inbox --interval-seconds 10800
```

This is the controlled-source MVP loop. It repeatedly ingests saved Reddit API/search results, reconciles the pool, detects reopenings, and runs the next queued research task.

## Human Review Actions

Use the dashboard detail page links, or run:

```bash
python3 -m super_crawler.cli action approve REQ-2026-000001
python3 -m super_crawler.cli action pause REQ-2026-000001
python3 -m super_crawler.cli action priority REQ-2026-000001 --priority 90
python3 -m super_crawler.cli action reject REQ-2026-000001
python3 -m super_crawler.cli action force-reopen REQ-2026-000001
python3 -m super_crawler.cli action merge REQ-2026-000002 --target-id REQ-2026-000001
```

## Verification

```bash
python3 -m unittest discover -s tests -v
```
