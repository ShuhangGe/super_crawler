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

- Running Status: Start, Stop, Run Once, frontend-editable resource limits, create general/domain task groups, and one separate three-column board per task group.
- Possible Requirements: all non-rejected requirements as lineage rows from task group/search agents to queue/pool, deep research agents, conclusion, and saved pipeline snapshot.
- Rejected Requirements: rejected or archived requirements using the same lineage-row structure.

Start runs the agent loop in the background, Stop halts it after the current cycle, and Run Once executes a single cycle immediately. Finished search/research lines are reviewed from the Possible Requirements and Rejected Requirements pages.

Resource limits can be changed directly on the Running Status page:

- Search slots control how many running task groups are searched in one cycle.
- Deep research slots control how many queued requirements can be consumed by deep research in one cycle.
- Report slots are persisted for the report-agent pool and ready for future parallel report workers.

## Task Groups

Task groups are the starting point for requirement search. A task can be a broad general search or a focused domain search.

```bash
python3 -m super_crawler.cli task create general "General Requirement Search" --input-dir data/task_inbox/general
python3 -m super_crawler.cli task create domain "Pet Care Search" --domain "pet care" --input-dir data/task_inbox/pet_care --subreddits r/dogs,r/AskVet --keywords medication,insurance
python3 -m super_crawler.cli task start tg_pet_care_search_0001
python3 -m super_crawler.cli task run tg_pet_care_search_0001
python3 -m super_crawler.cli task stop tg_pet_care_search_0001
python3 -m super_crawler.cli task delete tg_pet_care_search_0001
```

Put Reddit-like JSON arrays into each task group's input folder. Running task groups tag evidence, candidates, and requirements so the possible/rejected pages preserve the line from task group to final conclusion. Delete archives a task group: it disappears from the Running Status page, but remains selectable in Possible/Rejected pages if it has historical requirements.

## Reddit Collection With OpenCLI

The dashboard can use OpenCLI as a replaceable Reddit collection layer before the existing JSON pipeline runs. In a task group's `Settings` page, enable `OpenCLI Collection` and keep the default command unless your local OpenCLI install uses a different command:

```bash
opencli reddit search
```

Each running task group uses its own settings plus its description/domain/name as the query, writes normalized Reddit-like JSON into that group's input folder, logs the collector command and item count, then runs the same discovery, pool, and deep research pipeline.

You can also collect manually:

```bash
python3 -m super_crawler.cli collect-reddit tg_sports_search_0001 --limit 25
```

If OpenCLI is not installed or Reddit blocks the command, the task group run logs `collector_failed` and continues with any JSON already in the input folder.

## Model Settings

The app stores model choices in SQLite so experiments can record which model configuration was active. Defaults are:

- Search/discovery: `deepseek-v4-flash`
- Pool manager: `deepseek-v4-flash`
- Deep research: `deepseek-v4-flash`
- Report: `deepseek-v4-pro`

These are currently configuration and experiment metadata. The MVP's agent logic is still deterministic heuristics until a live LLM client is connected.

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
