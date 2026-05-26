from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agents import ChangeDetectionAgent, DeepResearchAgent, DiscoveryAgent, ReportAgent, RequirementMemoryAgent
from .collectors import OpenCliRedditCollector
from .dashboard import serve_dashboard
from .models import RequirementStatus, TaskGroupStatus, TaskGroupType
from .runner import AlwaysOnRunner
from .seed import SAMPLE_REDDIT_ITEMS
from .storage import DEFAULT_DB_PATH, Storage


def main() -> None:
    parser = argparse.ArgumentParser(description="Always-on Reddit requirement discovery system")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("init", help="Create or migrate the SQLite database")
    subcommands.add_parser("seed", help="Ingest built-in sample Reddit evidence")

    ingest = subcommands.add_parser("ingest-json", help="Ingest Reddit-like JSON items")
    ingest.add_argument("path", help="Path to a JSON array of Reddit items")

    subcommands.add_parser("run-cycle", help="Run discovery reconciliation, change detection, and one research job")
    subcommands.add_parser("research-next", help="Run the next queued deep research task")

    collect = subcommands.add_parser("collect-reddit", help="Collect Reddit search results into a task group inbox with OpenCLI")
    collect.add_argument("task_group_id")
    collect.add_argument("--limit", type=int)
    collect.add_argument("--command", dest="collector_command")
    collect.add_argument("--timeout-seconds", type=int)

    daemon = subcommands.add_parser("daemon", help="Run the controlled-source discovery loop forever")
    daemon.add_argument("--input-dir", default="data/reddit_inbox", help="Directory containing Reddit JSON arrays")
    daemon.add_argument("--interval-seconds", type=int, default=10_800, help="Delay between scans")

    task = subcommands.add_parser("task", help="Manage general/domain search task groups")
    task_subcommands = task.add_subparsers(dest="task_command", required=True)
    task_create = task_subcommands.add_parser("create")
    task_create.add_argument("type", choices=[TaskGroupType.GENERAL.value, TaskGroupType.DOMAIN.value, "general", "domain"])
    task_create.add_argument("name")
    task_create.add_argument("--description", default="")
    task_create.add_argument("--domain")
    task_create.add_argument("--input-dir")
    task_create.add_argument("--subreddits", default="")
    task_create.add_argument("--keywords", default="")
    task_create.add_argument("--negative-keywords", default="")
    task_subcommands.add_parser("list")
    task_start = task_subcommands.add_parser("start")
    task_start.add_argument("task_group_id")
    task_stop = task_subcommands.add_parser("stop")
    task_stop.add_argument("task_group_id")
    task_delete = task_subcommands.add_parser("delete")
    task_delete.add_argument("task_group_id")
    task_run = task_subcommands.add_parser("run")
    task_run.add_argument("task_group_id")

    action = subcommands.add_parser("action", help="Human review action for a requirement")
    action.add_argument("type", choices=["approve", "pause", "priority", "reject", "force-reopen", "merge"])
    action.add_argument("requirement_id")
    action.add_argument("--target-id", help="Target requirement for merge")
    action.add_argument("--priority", type=int, default=75)

    report = subcommands.add_parser("report", help="Generate a daily report")
    report.add_argument("--out", default="reports/daily.md", help="Markdown output path")

    serve = subcommands.add_parser("serve", help="Serve the local dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--input-dir", default="data/reddit_inbox", help="Dashboard Start button input directory")
    serve.add_argument("--interval-seconds", type=int, default=60, help="Seconds between dashboard-controlled cycles")

    args = parser.parse_args()
    storage = Storage(args.db)
    storage.migrate()
    try:
        if args.command == "init":
            print(f"Database ready: {storage.db_path}")
        elif args.command == "seed":
            candidates = DiscoveryAgent(storage, "discovery-seed").ingest_reddit_items(SAMPLE_REDDIT_ITEMS)
            changed = RequirementMemoryAgent(storage, "requirement-memory").reconcile_candidates()
            print(f"Ingested {len(candidates)} candidates and reconciled {len(changed)} requirements")
        elif args.command == "ingest-json":
            items = json.loads(Path(args.path).read_text())
            if not isinstance(items, list):
                raise SystemExit("JSON input must be an array of Reddit-like item objects")
            candidates = DiscoveryAgent(storage, "discovery-json").ingest_reddit_items(items)
            changed = RequirementMemoryAgent(storage, "requirement-memory").reconcile_candidates()
            print(f"Ingested {len(candidates)} candidates and reconciled {len(changed)} requirements")
        elif args.command == "run-cycle":
            RequirementMemoryAgent(storage, "requirement-memory").reconcile_candidates()
            reopened = ChangeDetectionAgent(storage, "change-detector").evaluate_reopenings()
            run = DeepResearchAgent(storage, "research-agent-1").run_next()
            print(f"Reopened {len(reopened)} requirements")
            print(f"Research run: {run.research_run_id if run else 'none queued'}")
        elif args.command == "research-next":
            run = DeepResearchAgent(storage, "research-agent-1").run_next()
            print(run.research_run_id if run else "No queued research tasks")
        elif args.command == "collect-reddit":
            task_group = storage.get_task_group(args.task_group_id)
            if task_group is None:
                raise SystemExit(f"Unknown task group: {args.task_group_id}")
            config = storage.get_app_config()
            collector = OpenCliRedditCollector(
                command=args.collector_command or config["collector_command"],
                timeout_seconds=args.timeout_seconds or int(config["collector_timeout_seconds"]),
            )
            result = collector.collect_to_inbox(task_group, f"manual_{slugify(task_group.name)}", args.limit or int(config["collector_limit"]))
            storage.log_experiment(
                task_group.task_group_id,
                None,
                "collector",
                "reddit_opencli_collected",
                f"Collected {result['items_collected']} Reddit item(s) with OpenCLI",
                result,
            )
            print(json.dumps(result, indent=2))
        elif args.command == "daemon":
            AlwaysOnRunner(storage, args.input_dir, args.interval_seconds).run_forever()
        elif args.command == "task":
            if args.task_command == "create":
                task_type = parse_task_type(args.type)
                input_dir = args.input_dir or f"data/task_inbox/{slugify(args.domain or args.name)}"
                task_group = storage.create_task_group(
                    name=args.name,
                    task_type=task_type,
                    domain=args.domain,
                    input_dir=input_dir,
                    description=args.description,
                    subreddits=split_csv(args.subreddits),
                    keywords=split_csv(args.keywords),
                    negative_keywords=split_csv(args.negative_keywords),
                )
                print(f"Created {task_group.task_group_id} at {task_group.input_dir}")
            elif args.task_command == "list":
                for task_group in storage.list_task_groups():
                    print(f"{task_group.task_group_id}\t{task_group.status.value}\t{task_group.task_type.value}\t{task_group.name}\t{task_group.input_dir}")
            elif args.task_command == "start":
                storage.update_task_group_status(args.task_group_id, TaskGroupStatus.RUNNING)
                print(f"Started {args.task_group_id}")
            elif args.task_command == "stop":
                storage.update_task_group_status(args.task_group_id, TaskGroupStatus.STOPPED)
                print(f"Stopped {args.task_group_id}")
            elif args.task_command == "delete":
                storage.update_task_group_status(args.task_group_id, TaskGroupStatus.ARCHIVED)
                print(f"Archived {args.task_group_id}")
            elif args.task_command == "run":
                result = AlwaysOnRunner(storage, "data/reddit_inbox").run_task_group(args.task_group_id)
                print(json.dumps(result, indent=2))
        elif args.command == "action":
            if args.type == "approve":
                storage.update_requirement_status(args.requirement_id, RequirementStatus.QUEUED_FOR_RESEARCH, "human approved deep research")
                storage.enqueue_research(args.requirement_id, args.priority, "human approved deep research", 0, None)
            elif args.type == "pause":
                storage.dequeue_research(args.requirement_id)
                storage.update_requirement_status(args.requirement_id, RequirementStatus.WATCHING, "human paused research")
            elif args.type == "priority":
                storage.update_queue_priority(args.requirement_id, args.priority)
            elif args.type == "reject":
                storage.dequeue_research(args.requirement_id)
                storage.update_requirement_status(args.requirement_id, RequirementStatus.REJECTED, "human rejected as noise")
            elif args.type == "force-reopen":
                storage.update_requirement_status(args.requirement_id, RequirementStatus.REOPENED, "human forced reopen")
                storage.enqueue_research(args.requirement_id, args.priority, "human forced reopen", 0, None)
            elif args.type == "merge":
                if not args.target_id:
                    raise SystemExit("--target-id is required for merge")
                storage.merge_requirements(args.requirement_id, args.target_id)
            print(f"Applied action {args.type} to {args.requirement_id}")
        elif args.command == "report":
            output = Path(args.out)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(ReportAgent(storage, "report-agent").daily_report())
            print(f"Wrote {output}")
        elif args.command == "serve":
            serve_dashboard(
                storage,
                host=args.host,
                port=args.port,
                input_dir=args.input_dir,
                interval_seconds=args.interval_seconds,
            )
    finally:
        if args.command != "serve":
            storage.close()


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def slugify(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_") or "task"


def parse_task_type(value: str) -> TaskGroupType:
    if value == "general":
        return TaskGroupType.GENERAL
    if value == "domain":
        return TaskGroupType.DOMAIN
    return TaskGroupType(value)


if __name__ == "__main__":
    main()
