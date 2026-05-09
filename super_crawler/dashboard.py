from __future__ import annotations

import html
import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .models import RequirementStatus, TaskGroupStatus, TaskGroupType
from .runtime import RuntimeController
from .storage import Storage


def serve_dashboard(
    storage: Storage,
    host: str = "127.0.0.1",
    port: int = 8000,
    input_dir: str = "data/reddit_inbox",
    interval_seconds: int = 60,
) -> None:
    db_path = storage.db_path
    controller = RuntimeController(storage.db_path, input_dir=input_dir, interval_seconds=interval_seconds)
    storage.close()

    class Handler(DashboardHandler):
        app_db_path = db_path
        app_controller = controller

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Dashboard listening on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        controller.stop()


class DashboardHandler(BaseHTTPRequestHandler):
    app_db_path: object
    app_controller: RuntimeController

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/runtime":
            self._json(self.app_controller.status())
        elif parsed.path == "/runtime":
            self._handle_runtime(parse_qs(parsed.query))
        else:
            with self._request_storage() as storage:
                storage.migrate()
                if parsed.path == "/":
                    self._html(home_page(storage, self.app_controller))
                elif parsed.path == "/possible":
                    query = parse_qs(parsed.query)
                    self._html(
                        requirement_list_page(
                            storage,
                            "Possible Requirements",
                            filter_requirements_by_group(possible_requirements(storage), query.get("task_group_id", [""])[0]),
                            query.get("task_group_id", [""])[0],
                            "/possible",
                        )
                    )
                elif parsed.path == "/rejected":
                    query = parse_qs(parsed.query)
                    self._html(
                        requirement_list_page(
                            storage,
                            "Rejected Requirements",
                            filter_requirements_by_group(rejected_requirements(storage), query.get("task_group_id", [""])[0]),
                            query.get("task_group_id", [""])[0],
                            "/rejected",
                        )
                    )
                elif parsed.path == "/queue":
                    self._html(queue_page(storage))
                elif parsed.path == "/requirement":
                    requirement_id = parse_qs(parsed.query).get("id", [""])[0]
                    self._html(detail_page(storage, requirement_id))
                elif parsed.path == "/agent-log":
                    query = parse_qs(parsed.query)
                    self._html(
                        agent_log_page(
                            storage,
                            query.get("role", [""])[0],
                            query.get("agent_id", [""])[0],
                            query.get("ref", [""])[0],
                        )
                    )
                elif parsed.path == "/experiment-log":
                    query = parse_qs(parsed.query)
                    self._html(
                        experiment_log_page(
                            storage,
                            query.get("task_group_id", [""])[0],
                            query.get("task_group_run_id", [""])[0],
                            query.get("agent_role", [""])[0],
                        )
                    )
                elif parsed.path == "/requirement-samples":
                    query = parse_qs(parsed.query)
                    self._html(
                        requirement_samples_page(
                            storage,
                            query.get("task_group_id", [""])[0],
                            query.get("task_group_run_id", [""])[0],
                            query.get("requirement_id", [""])[0],
                        )
                    )
                elif parsed.path == "/pipeline":
                    pipeline_run_id = parse_qs(parsed.query).get("id", [""])[0]
                    self._html(pipeline_page(storage, pipeline_run_id))
                elif parsed.path == "/reports":
                    self._html(reports_page(storage))
                elif parsed.path == "/api/requirements":
                    self._json([asdict(requirement) for requirement in storage.list_requirements()])
                elif parsed.path == "/action":
                    self._handle_action(storage, parse_qs(parsed.query))
                elif parsed.path == "/task":
                    self._handle_task(storage, parse_qs(parsed.query))
                elif parsed.path == "/resources":
                    self._handle_resources(storage, parse_qs(parsed.query))
                else:
                    self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _html(self, body: str) -> None:
        content = layout(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _json(self, data: object) -> None:
        content = json.dumps(data, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _request_storage(self) -> Storage:
        return Storage(self.app_db_path)

    def _handle_action(self, storage: Storage, query: dict[str, list[str]]) -> None:
        action = query.get("type", [""])[0]
        requirement_id = query.get("id", [""])[0]
        target_id = query.get("target", [""])[0]
        priority = int(query.get("priority", ["75"])[0])
        if action == "approve":
            storage.update_requirement_status(requirement_id, RequirementStatus.QUEUED_FOR_RESEARCH, "human approved deep research")
            storage.enqueue_research(requirement_id, priority, "human approved deep research", 0, None)
        elif action == "pause":
            storage.dequeue_research(requirement_id)
            storage.update_requirement_status(requirement_id, RequirementStatus.WATCHING, "human paused research")
        elif action == "reject":
            storage.dequeue_research(requirement_id)
            storage.update_requirement_status(requirement_id, RequirementStatus.REJECTED, "human rejected as noise")
        elif action == "force-reopen":
            storage.update_requirement_status(requirement_id, RequirementStatus.REOPENED, "human forced reopen")
            storage.enqueue_research(requirement_id, priority, "human forced reopen", 0, None)
        elif action == "priority":
            storage.update_queue_priority(requirement_id, priority)
        elif action == "merge" and target_id:
            storage.merge_requirements(requirement_id, target_id)
        else:
            self.send_error(400)
            return
        self.send_response(303)
        self.send_header("Location", f"/requirement?id={requirement_id}")
        self.end_headers()

    def _handle_runtime(self, query: dict[str, list[str]]) -> None:
        action = query.get("action", [""])[0]
        if action == "start":
            self.app_controller.start()
        elif action == "stop":
            self.app_controller.stop()
        elif action == "run-once":
            self.app_controller.run_once()
        else:
            self.send_error(400)
            return
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def _handle_task(self, storage: Storage, query: dict[str, list[str]]) -> None:
        action = query.get("action", [""])[0]
        task_group_id = query.get("id", [""])[0]
        if action == "create":
            task_type = TaskGroupType(query.get("type", [TaskGroupType.GENERAL.value])[0])
            name = query.get("name", [""])[0].strip() or ("General Search" if task_type == TaskGroupType.GENERAL else "Domain Search")
            domain = query.get("domain", [""])[0].strip() or None
            input_dir = query.get("input_dir", [""])[0].strip()
            if not input_dir:
                folder = domain or name
                slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in folder).strip("_")
                input_dir = f"data/task_inbox/{slug or 'general'}"
            subreddits = split_csv(query.get("subreddits", [""])[0])
            keywords = split_csv(query.get("keywords", [""])[0])
            negative_keywords = split_csv(query.get("negative_keywords", [""])[0])
            storage.create_task_group(name, task_type, domain, input_dir, subreddits, keywords, negative_keywords)
        elif action == "start":
            storage.update_task_group_status(task_group_id, TaskGroupStatus.RUNNING)
        elif action == "stop":
            storage.update_task_group_status(task_group_id, TaskGroupStatus.STOPPED)
        elif action == "delete":
            storage.update_task_group_status(task_group_id, TaskGroupStatus.ARCHIVED)
        elif action == "run-once":
            from .runner import AlwaysOnRunner

            AlwaysOnRunner(storage, "data/reddit_inbox").run_task_group(task_group_id)
        else:
            self.send_error(400)
            return
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def _handle_resources(self, storage: Storage, query: dict[str, list[str]]) -> None:
        storage.update_resource_config(
            {
                "max_search_agents": parse_int(query.get("max_search_agents", ["3"])[0], 3),
                "max_deep_research_agents": parse_int(query.get("max_deep_research_agents", ["1"])[0], 1),
                "max_report_agents": parse_int(query.get("max_report_agents", ["1"])[0], 1),
            }
        )
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


def layout(content: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Requirement Discovery</title>
  <style>
    body {{ margin: 0; font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2933; background: #f5f7f9; }}
    header {{ display: flex; align-items: center; gap: 20px; padding: 14px 22px; background: #12343b; color: white; position: sticky; top: 0; z-index: 2; }}
    header a {{ color: white; text-decoration: none; opacity: .9; }}
    main {{ max-width: 1420px; margin: 0 auto; padding: 22px; }}
    h1 {{ margin: 0 0 18px; font-size: 26px; }}
    h2 {{ margin-top: 28px; font-size: 18px; }}
    h3 {{ margin: 0 0 10px; font-size: 15px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .card {{ background: white; border: 1px solid #dce2e8; border-radius: 8px; padding: 14px; }}
    .board {{ display: grid; grid-template-columns: minmax(240px, 1fr) minmax(420px, 1.7fr) minmax(300px, 1.2fr); gap: 14px; align-items: start; }}
    .workbench {{ display: grid; grid-template-columns: minmax(260px, 1fr) minmax(420px, 1.5fr) minmax(260px, 1fr); gap: 14px; align-items: start; }}
    .task-group-box {{ border: 2px solid #c8d8df; border-radius: 10px; background: #ffffff; margin-top: 18px; overflow: hidden; }}
    .task-group-header {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 14px; background: #eef5f7; border-bottom: 1px solid #d6e3e8; }}
    .task-group-header h2 {{ margin: 0; }}
    .task-group-body {{ padding: 14px; }}
    .panel {{ background: white; border: 1px solid #dce2e8; border-radius: 8px; padding: 14px; min-height: 260px; }}
    .item {{ display: block; padding: 10px; border: 1px solid #e4e9ee; border-radius: 7px; margin-bottom: 8px; color: inherit; text-decoration: none; background: #fbfcfd; }}
    .item:hover {{ border-color: #9eb8c4; background: #f1f6f8; }}
    .title {{ font-weight: 700; }}
    .summary {{ color: #52616b; font-size: 13px; margin-top: 4px; }}
    .controlbar {{ display: flex; align-items: center; justify-content: space-between; gap: 14px; background: white; border: 1px solid #dce2e8; border-radius: 8px; padding: 14px; margin-bottom: 18px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    input, select {{ border: 1px solid #cfd8df; border-radius: 6px; padding: 8px; min-height: 20px; }}
    input[type="number"] {{ width: 80px; }}
    .button {{ border: 0; border-radius: 6px; padding: 9px 14px; color: white; background: #0d5c75; cursor: pointer; font-weight: 650; }}
    .button.stop {{ background: #b42318; }}
    .button.secondary {{ background: #52616b; }}
    .metric {{ font-size: 28px; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dce2e8; }}
    th, td {{ padding: 10px; border-bottom: 1px solid #e6ebf0; text-align: left; vertical-align: top; }}
    th {{ background: #eef3f6; font-weight: 650; }}
    .status {{ display: inline-block; padding: 2px 7px; border-radius: 999px; background: #e8f2ff; }}
    .status.rejected {{ background: #fde8e6; }}
    .status.running {{ background: #dff7ea; }}
    .linkbar {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
    .linkbar a {{ background: #eef3f6; padding: 4px 8px; border-radius: 6px; text-decoration: none; }}
    .agent-chip {{ display: inline-block; margin: 2px 4px 2px 0; padding: 4px 8px; border-radius: 6px; background: #eef3f6; text-decoration: none; }}
    .lineage td:first-child {{ min-width: 220px; }}
    .lineage td {{ font-size: 13px; }}
    .muted {{ color: #687782; }}
    a {{ color: #0d5c75; }}
    pre {{ white-space: pre-wrap; background: white; border: 1px solid #dce2e8; padding: 14px; border-radius: 8px; }}
  </style>
</head>
<body>
  <header>
    <strong>Requirement Discovery</strong>
    <a href="/">Running Status</a>
    <a href="/possible">Possible Requirements</a>
    <a href="/rejected">Rejected Requirements</a>
  </header>
  <main>{content}</main>
</body>
</html>"""


def home_page(storage: Storage, controller: RuntimeController) -> str:
    counts = storage.dashboard_counts()
    requirements = storage.list_requirements()
    by_status = {status.value: 0 for status in RequirementStatus}
    for requirement in requirements:
        by_status[requirement.status.value] = by_status.get(requirement.status.value, 0) + 1
    task_groups = visible_task_groups(storage)
    cards = [
        ("Possible requirements", len(possible_requirements(storage))),
        ("Queued for research", counts["queued"]),
        ("Currently researching", by_status.get("researching", 0)),
        ("Validated requirements", by_status.get("validated", 0)),
        ("Reopened requirements", by_status.get("reopened", 0)),
        ("Rejected/noisy", by_status.get("rejected", 0)),
    ]
    return (
        "<h1>Running Status</h1>"
        + resource_allocation_panel(storage)
        + "<section class='grid'>"
        + "".join(f"<div class='card'><div class='muted'>{label}</div><div class='metric'>{value}</div></div>" for label, value in cards)
        + "</section>"
        + task_create_panel()
        + task_group_boards(storage, task_groups, requirements)
    )


def runtime_controls(runtime: dict[str, object]) -> str:
    state = "Stopping" if runtime["stopping"] else "Running" if runtime["running"] else "Stopped"
    return f"""
    <section class="controlbar">
      <div>
        <strong>Agent Runtime</strong>
        <div class="muted">State: {state} | Cycles: {runtime["cycle_count"]} | Input: {html.escape(str(runtime["input_dir"]))}</div>
        <div class="muted">Last result: {html.escape(json.dumps(runtime.get("last_result")))}</div>
      </div>
      <div class="actions">
        <form action="/runtime"><input type="hidden" name="action" value="start"><button class="button">Start</button></form>
        <form action="/runtime"><input type="hidden" name="action" value="stop"><button class="button stop">Stop</button></form>
        <form action="/runtime"><input type="hidden" name="action" value="run-once"><button class="button secondary">Run Once</button></form>
      </div>
    </section>
    """


def resource_allocation_panel(storage: Storage) -> str:
    resources = storage.get_resource_config()
    running_search = len(storage.list_task_groups([TaskGroupStatus.RUNNING.value]))
    queue = storage.list_queue()
    locked_research = len([item for item in queue if item.get("locked_by")])
    return f"""
    <section class="controlbar">
      <div>
        <strong>Global Resource Allocation</strong>
        <div class="muted">Search slots: {running_search}/{resources["max_search_agents"]} | Deep research slots: {locked_research}/{resources["max_deep_research_agents"]} | Report slots: 0/{resources["max_report_agents"]} | Queue: {len(queue)}</div>
      </div>
      <form action="/resources" class="actions">
        <label>Search <input type="number" min="0" name="max_search_agents" value="{resources["max_search_agents"]}"></label>
        <label>Deep <input type="number" min="0" name="max_deep_research_agents" value="{resources["max_deep_research_agents"]}"></label>
        <label>Report <input type="number" min="0" name="max_report_agents" value="{resources["max_report_agents"]}"></label>
        <button class="button secondary">Save Limits</button>
      </form>
    </section>
    """


def agent_cards(storage: Storage, roles: list[str], ref: str | None = None) -> str:
    logs = storage.list_activity_logs(200)
    runtime_agents = []
    for role in roles:
        role_logs = [item for item in logs if item["agent_role"] == role]
        latest = role_logs[0] if role_logs else None
        runtime_agents.append(
            {
                "role": role,
                "count": len(role_logs),
                "latest_status": latest["status"] if latest else "waiting",
                "latest_task": latest["task_id"] if latest else "no task yet",
                "latest_time": latest["completed_at"] if latest else "",
            }
        )
    return "".join(
        f"""
        <a class="item" href="/agent-log?role={html.escape(agent['role'])}{'&ref=' + html.escape(ref) if ref else ''}">
          <div class="title">{html.escape(agent['role'].replace('_', ' ').title())}</div>
          <div><span class="status running">{html.escape(agent['latest_status'])}</span></div>
          <div class="summary">{html.escape(agent['latest_task'])}</div>
          <div class="summary">{agent['count']} log event(s) {html.escape(str(agent['latest_time'] or ''))}</div>
        </a>
        """
        for agent in runtime_agents
    )


def task_group_boards(storage: Storage, task_groups: list[object], requirements: list[object]) -> str:
    if not task_groups:
        return "<section class='card'><p class='muted'>No task group yet. Create a general or domain task above.</p></section>"
    return "".join(task_group_board(storage, task_group, requirements) for task_group in task_groups)


def task_group_board(storage: Storage, task_group: object, requirements: list[object]) -> str:
    group_requirements = [item for item in requirements if task_group.task_group_id in item.task_group_ids]
    waiting = [item for item in group_requirements if item.status in waiting_statuses()]
    return (
        "<section class='task-group-box'>"
        + task_group_header(task_group, group_requirements)
        + "<div class='task-group-body'><section class='workbench'>"
        + task_group_search_panel(storage, task_group)
        + waiting_requirements_panel(waiting)
        + task_group_deep_research_panel(storage, task_group, group_requirements)
        + "</section></div></section>"
    )


def task_group_header(task_group: object, requirements: list[object]) -> str:
    status_class = " running" if task_group.status == TaskGroupStatus.RUNNING else ""
    return f"""
    <div class="task-group-header">
      <div>
        <h2>{html.escape(task_group.name)}</h2>
        <div class="summary"><span class="status{status_class}">{html.escape(task_group.status.value)}</span> {html.escape(task_group.task_type.value)} | {html.escape(task_group.domain or 'general')} | {len(requirements)} requirement(s)</div>
      </div>
      <div class="linkbar">
        <a href="/possible?task_group_id={html.escape(task_group.task_group_id)}">Possible</a>
        <a href="/rejected?task_group_id={html.escape(task_group.task_group_id)}">Rejected</a>
      </div>
    </div>
    """


def task_group_search_panel(storage: Storage, task_group: object) -> str:
    return (
        "<section class='panel'><h2>Search Task Group</h2>"
        + task_group_card(task_group)
        + "<h3>Search Agent Logs</h3>"
        + agent_cards(storage, ["discovery", "pool_manager", "change_detection"], task_group.task_group_id)
        + "</section>"
    )


def waiting_requirements_panel(requirements: list[object]) -> str:
    items = "".join(requirement_card(item) for item in requirements[:12]) or "<p class='muted'>No found requirement is waiting for verification.</p>"
    return (
        "<section class='panel'><h2>Found Requirements Waiting To Verify</h2>"
        "<p class='muted'>These are the requirements found by search agents before, or while, deep research verifies them.</p>"
        + items
        + "</section>"
    )


def deep_research_agents_panel(storage: Storage) -> str:
    items = agent_cards(storage, ["deep_research", "report"])
    queue = storage.list_queue()
    queue_text = "".join(
        f"<div class='summary'>Consuming queue: {html.escape(row['requirement_id'])} priority {row['priority']}</div>"
        for row in queue[:5]
    )
    return (
        "<section class='panel'><h2>Running Deep Research Agents</h2>"
        "<p class='muted'>Deep research agents consume requirements from the queue and produce conclusions.</p>"
        + items
        + queue_text
        + "</section>"
    )


def task_group_deep_research_panel(storage: Storage, task_group: object, requirements: list[object]) -> str:
    requirement_ids = {item.requirement_id for item in requirements}
    queue = [row for row in storage.list_queue() if row["requirement_id"] in requirement_ids]
    queue_text = "".join(
        f"<div class='summary'>Consuming queue: {html.escape(row['requirement_id'])} priority {row['priority']}</div>"
        for row in queue[:5]
    ) or "<p class='muted'>No deep research queue item for this task group.</p>"
    return (
        "<section class='panel'><h2>Running Deep Research Agents</h2>"
        "<p class='muted'>Deep research agents consuming this task group's requirement queue.</p>"
        + agent_cards(storage, ["deep_research", "report"], task_group.task_group_id)
        + queue_text
        + "</section>"
    )


def pipeline_history_compact(pipelines: list[dict[str, object]]) -> str:
    items = "".join(
        f"""
        <tr>
          <td><a href="/pipeline?id={html.escape(str(item['pipeline_run_id']))}">{html.escape(str(item['pipeline_run_id']))}</a></td>
          <td>{html.escape(str(item['status']))}</td>
          <td>{html.escape(str(item['summary']))}</td>
          <td>{html.escape(str(item['completed_at']))}</td>
        </tr>
        """
        for item in pipelines
    )
    if not items:
        items = "<tr><td colspan='4' class='muted'>No completed pipeline snapshot yet. Click Run Once to create one.</td></tr>"
    return (
        "<h2>Saved Pipeline Snapshots</h2>"
        "<p class='muted'>Each finished cycle is saved for future evaluation.</p>"
        "<table><thead><tr><th>Pipeline</th><th>Status</th><th>Summary</th><th>Completed</th></tr></thead><tbody>"
        + items
        + "</tbody></table>"
    )


def possible_requirements(storage: Storage) -> list[object]:
    rejected_statuses = {RequirementStatus.REJECTED, RequirementStatus.ARCHIVED}
    return [item for item in storage.list_requirements() if item.status not in rejected_statuses]


def rejected_requirements(storage: Storage) -> list[object]:
    return [item for item in storage.list_requirements() if item.status in {RequirementStatus.REJECTED, RequirementStatus.ARCHIVED}]


def requirement_list_page(storage: Storage, title: str, requirements: list[object], selected_task_group_id: str, page_path: str) -> str:
    return (
        f"<h1>{html.escape(title)}</h1>"
        + task_group_filter(storage, selected_task_group_id, page_path)
        + "<p class='muted'>Each row preserves the whole line from requirement search to conclusion so it can be evaluated later.</p>"
        + grouped_requirement_lineage(storage, requirements, selected_task_group_id)
    )


def grouped_requirement_lineage(storage: Storage, requirements: list[object], selected_task_group_id: str = "") -> str:
    task_groups = lineage_task_groups(storage, requirements)
    if selected_task_group_id == "__ungrouped__":
        return "<h2>Ungrouped / Legacy</h2>" + requirement_lineage_table(storage, [item for item in requirements if not item.task_group_ids])
    if selected_task_group_id:
        task_groups = [item for item in task_groups if item.task_group_id == selected_task_group_id]
    sections = []
    used_ids: set[str] = set()
    for task_group in task_groups:
        group_requirements = [item for item in requirements if task_group.task_group_id in item.task_group_ids]
        if not group_requirements:
            continue
        used_ids.update(item.requirement_id for item in group_requirements)
        sections.append(
            f"<h2>{html.escape(task_group.name)}</h2>"
            f"<p class='muted'>{html.escape(task_group.task_type.value)} | {html.escape(task_group.domain or 'general')} | Input {html.escape(task_group.input_dir)}</p>"
            + requirement_lineage_table(storage, group_requirements)
        )
    ungrouped = [item for item in requirements if item.requirement_id not in used_ids]
    if ungrouped:
        sections.append("<h2>Ungrouped / Legacy</h2>" + requirement_lineage_table(storage, ungrouped))
    return "".join(sections) if sections else requirement_lineage_table(storage, [])


def task_group_filter(storage: Storage, selected_task_group_id: str, page_path: str) -> str:
    options = ["<option value=''>All groups</option>"]
    for task_group in lineage_task_groups(storage, storage.list_requirements()):
        selected = " selected" if task_group.task_group_id == selected_task_group_id else ""
        label = f"{task_group.name} ({task_group.status.value})"
        options.append(f"<option value='{html.escape(task_group.task_group_id)}'{selected}>{html.escape(label)}</option>")
    selected = " selected" if selected_task_group_id == "__ungrouped__" else ""
    options.append(f"<option value='__ungrouped__'{selected}>Ungrouped / Legacy</option>")
    return (
        "<form class='controlbar' action='" + html.escape(page_path) + "'>"
        "<strong>Task Group</strong>"
        "<select name='task_group_id'>" + "".join(options) + "</select>"
        "<button class='button secondary'>Show Group</button>"
        "</form>"
    )


def requirement_lineage_table(storage: Storage, requirements: list[object]) -> str:
    rows = "".join(requirement_lineage_row(storage, item) for item in requirements)
    if not rows:
        rows = "<tr><td colspan='6' class='muted'>No requirements in this page yet.</td></tr>"
    return (
        "<table class='lineage'><thead><tr>"
        "<th>Requirement</th><th>Search Agents</th><th>Queue / Pool</th><th>Deep Research Agents</th><th>Conclusion</th><th>Saved Line</th>"
        "</tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )


def requirement_lineage_row(storage: Storage, requirement: object) -> str:
    runs = storage.list_research_runs(requirement.requirement_id)
    latest_run = runs[0] if runs else None
    conclusion = latest_run.recommendation if latest_run else requirement.latest_recommendation or "Waiting for deep research conclusion"
    search_agents = agent_links_for_requirement(storage, requirement, ["discovery"])
    pool_agents = agent_links_for_requirement(storage, requirement, ["pool_manager", "change_detection"])
    deep_agents = agent_links_for_requirement(storage, requirement, ["deep_research", "report"])
    saved_line = latest_pipeline_for_requirement(storage, requirement.requirement_id)
    status_class = " rejected" if requirement.status == RequirementStatus.REJECTED else ""
    return f"""
    <tr>
      <td>
        <a href="/requirement?id={html.escape(requirement.requirement_id)}"><strong>{html.escape(requirement.canonical_requirement)}</strong></a>
        <div class="summary"><span class="status{status_class}">{html.escape(requirement.status.value)}</span> Score {requirement.current_scores.get("overall_score", 0)}</div>
        <div class="summary">{task_group_labels(storage, requirement)}</div>
        <div class="summary">Evidence {requirement.evidence_count} | Subreddits {requirement.subreddit_count}</div>
      </td>
      <td>{search_agents}</td>
      <td>{pool_agents}<div class="summary">Times detected {requirement.times_detected}</div></td>
      <td>{deep_agents}<div class="summary">{len(runs)} research run(s)</div></td>
      <td>{html.escape(conclusion)}</td>
      <td>{saved_line}</td>
    </tr>
    """


def task_create_panel() -> str:
    return """
    <section class="card">
      <h2>Create Task Group</h2>
      <form action="/task" class="actions">
        <input type="hidden" name="action" value="create">
        <select name="type">
          <option value="general_search">General Search Task</option>
          <option value="domain_search">Domain Search Task</option>
        </select>
        <input name="name" placeholder="Task name">
        <input name="domain" placeholder="Domain, e.g. pet care">
        <input name="subreddits" placeholder="Subreddits comma-separated">
        <input name="keywords" placeholder="Keywords comma-separated">
        <input name="negative_keywords" placeholder="Negative keywords">
        <input name="input_dir" placeholder="Input folder, optional">
        <button class="button">Create</button>
      </form>
      <p class="muted">A task group reads JSON files from its input folder, tags every evidence item, and feeds requirements into the shared verification queue.</p>
    </section>
    """


def task_group_card(task: object) -> str:
    action = "stop" if task.status == TaskGroupStatus.RUNNING else "start"
    action_label = "Stop" if task.status == TaskGroupStatus.RUNNING else "Start"
    status_class = " running" if task.status == TaskGroupStatus.RUNNING else ""
    domain = f" | Domain: {html.escape(str(task.domain))}" if task.domain else ""
    return f"""
    <div class="item">
      <div class="title">{html.escape(task.name)}</div>
      <div><span class="status{status_class}">{html.escape(task.status.value)}</span> {html.escape(task.task_type.value)}{domain}</div>
      <div class="summary">Input: {html.escape(task.input_dir)}</div>
      <div class="summary">Subreddits: {html.escape(', '.join(task.subreddits) or 'any')}</div>
      <div class="summary">Keywords: {html.escape(', '.join(task.keywords) or 'default signal patterns')}</div>
      <div class="linkbar">
        <a href="/task?action={action}&id={html.escape(task.task_group_id)}">{action_label}</a>
        <a href="/task?action=run-once&id={html.escape(task.task_group_id)}">Run Once</a>
        <a href="/task?action=delete&id={html.escape(task.task_group_id)}">Delete</a>
        <a href="/agent-log?role=discovery&ref={html.escape(task.task_group_id)}">Search Log</a>
        <a href="/experiment-log?task_group_id={html.escape(task.task_group_id)}">Run Logs</a>
        <a href="/requirement-samples?task_group_id={html.escape(task.task_group_id)}">Samples</a>
      </div>
    </div>
    """


def task_group_labels(storage: Storage, requirement: object) -> str:
    labels = []
    for task_group_id in requirement.task_group_ids:
        task = storage.get_task_group(task_group_id)
        if task:
            labels.append(f"{task.name} ({task.task_type.value})")
        else:
            labels.append(task_group_id)
    return "Task group: " + ", ".join(labels) if labels else "Task group: unassigned"


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def waiting_statuses() -> set[RequirementStatus]:
    return {
        RequirementStatus.NEW_CANDIDATE,
        RequirementStatus.NEEDS_MORE_EVIDENCE,
        RequirementStatus.QUEUED_FOR_RESEARCH,
        RequirementStatus.WATCHING,
        RequirementStatus.REOPENED,
    }


def visible_task_groups(storage: Storage) -> list[object]:
    return [item for item in storage.list_task_groups() if item.status != TaskGroupStatus.ARCHIVED]


def lineage_task_groups(storage: Storage, requirements: list[object]) -> list[object]:
    referenced = {task_group_id for requirement in requirements for task_group_id in requirement.task_group_ids}
    return [item for item in storage.list_task_groups() if item.status != TaskGroupStatus.ARCHIVED or item.task_group_id in referenced]


def filter_requirements_by_group(requirements: list[object], task_group_id: str) -> list[object]:
    if not task_group_id:
        return requirements
    if task_group_id == "__ungrouped__":
        return [item for item in requirements if not item.task_group_ids]
    return [item for item in requirements if task_group_id in item.task_group_ids]


def agent_links_for_requirement(storage: Storage, requirement: object, roles: list[str]) -> str:
    links = []
    for role in roles:
        related = related_log_count(storage, requirement, role)
        href = f"/agent-log?role={html.escape(role)}&ref={html.escape(requirement.requirement_id)}"
        links.append(f"<a class='agent-chip' href='{href}'>{html.escape(role.replace('_', ' ').title())} ({related})</a>")
    return "".join(links)


def related_log_count(storage: Storage, requirement: object, role: str) -> int:
    logs = storage.list_agent_logs(agent_role=role, limit=500)
    refs = {requirement.requirement_id, *requirement.research_history, *requirement.evidence_ids}
    count = 0
    for item in logs:
        log_refs = {str(ref) for ref in item["input_refs"] + item["output_refs"]}
        if refs & log_refs:
            count += 1
    if count == 0 and role == "discovery" and requirement.evidence_ids:
        return len(logs)
    return count


def latest_pipeline_for_requirement(storage: Storage, requirement_id: str) -> str:
    for pipeline in storage.list_pipeline_runs(30):
        snapshot = storage.get_pipeline_run(str(pipeline["pipeline_run_id"]))
        if not snapshot:
            continue
        if any(item.get("requirement_id") == requirement_id for item in snapshot["requirement_snapshot"]):
            return f"<a class='agent-chip' href='/pipeline?id={html.escape(str(pipeline['pipeline_run_id']))}'>Pipeline Snapshot</a>"
    return "<span class='muted'>No saved snapshot yet</span>"


def log_summary_panel(title: str, href: str, logs: list[dict[str, object]]) -> str:
    items = "".join(
        f"""
        <a class="item" href="{href}">
          <div class="title">{html.escape(str(item['task_id']))}</div>
          <div><span class="status">{html.escape(str(item['status']))}</span></div>
          <div class="summary">{html.escape(str(item['agent_id']))} | {html.escape(str(item['completed_at'] or item['started_at']))}</div>
        </a>
        """
        for item in logs
    ) or "<p class='muted'>No logs yet.</p>"
    return f"<section class='panel'><h2>{html.escape(title)}</h2><p class='muted'>Click for full log.</p>{items}</section>"


def requirement_card(requirement: object) -> str:
    status_class = " rejected" if requirement.status == RequirementStatus.REJECTED else ""
    score = requirement.current_scores.get("overall_score", 0)
    summary = requirement.description[:140] + ("..." if len(requirement.description) > 140 else "")
    return f"""
    <a class="item" href="/requirement?id={html.escape(requirement.requirement_id)}">
      <div class="title">{html.escape(requirement.canonical_requirement)}</div>
      <div><span class="status{status_class}">{html.escape(requirement.status.value)}</span> Score {score}</div>
      <div class="summary">{html.escape(summary)}</div>
      <div class="summary">Evidence {requirement.evidence_count} | Subreddits {requirement.subreddit_count} | Last seen {html.escape(requirement.last_seen)}</div>
    </a>
    """


def queue_page(storage: Storage) -> str:
    rows = storage.list_queue()
    body = "".join(
        "<tr>"
        f"<td>{row['priority']}</td><td><a href='/requirement?id={html.escape(row['requirement_id'])}'>{html.escape(row['requirement_id'])}</a></td>"
        f"<td>{html.escape(row['reason'])}</td><td>{row['new_evidence_count']}</td><td>{html.escape(str(row['previous_research_status'] or ''))}</td>"
        f"<td>{html.escape(str(row['assigned_agent'] or ''))}</td><td>{html.escape(str(row['locked_by'] or 'unlocked'))}</td>"
        f"<td>{row['estimated_cost']}</td><td>{row['expected_completion_minutes']} min</td>"
        "</tr>"
        for row in rows
    )
    return (
        "<h1>Research Queue</h1><table><thead><tr><th>Priority</th><th>Requirement</th><th>Reason</th>"
        "<th>New Evidence</th><th>Previous Status</th><th>Assigned Agent</th><th>Lock</th><th>Cost</th><th>ETA</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def detail_page(storage: Storage, requirement_id: str) -> str:
    requirement = storage.get_requirement(requirement_id)
    if requirement is None:
        return "<h1>Requirement not found</h1>"
    evidence = storage.list_evidence(requirement.evidence_ids)
    runs = storage.list_research_runs(requirement_id)
    events = storage.list_requirement_events(requirement_id)
    samples = storage.list_requirement_samples(requirement_id=requirement_id, limit=20)
    evidence_rows = "".join(
        f"<li>{html.escape(item.subreddit)}: <a href='{html.escape(item.source_url)}'>{html.escape(item.title)}</a></li>"
        for item in evidence
    )
    latest = runs[0] if runs else None
    run_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(run.research_run_id)}</td>
          <td>{html.escape(run.agent_id)}</td>
          <td>{html.escape(str(run.completed_at or run.started_at))}</td>
          <td>{html.escape(run.recommendation)}</td>
        </tr>
        """
        for run in runs
    )
    event_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(event['created_at']))}</td>
          <td>{html.escape(str(event['agent_role']))}</td>
          <td>{html.escape(str(event['event_type']))}</td>
          <td>{html.escape(str(event['message']))}</td>
        </tr>
        """
        for event in events
    ) or "<tr><td colspan='4' class='muted'>No workflow events recorded yet.</td></tr>"
    sample_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(sample['created_at']))}</td>
          <td>{html.escape(str(sample['requirement_sentence']))}</td>
          <td>{html.escape(str(sample['status']))}</td>
          <td>{html.escape(str(sample['task_group_run_id'] or ''))}</td>
        </tr>
        """
        for sample in samples
    ) or "<tr><td colspan='4' class='muted'>No sample sentence recorded yet.</td></tr>"
    report = ""
    if latest:
        findings = latest.findings
        report = (
            "<h2>Research Report</h2>"
            f"<p><strong>Why real:</strong> {html.escape(findings['why_real'])}</p>"
            f"<p><strong>Why noise:</strong> {html.escape(findings['why_noise'])}</p>"
            f"<p><strong>Recommendation:</strong> {html.escape(latest.recommendation)}</p>"
            f"<pre>{html.escape(json.dumps(findings, indent=2))}</pre>"
        )
    return f"""
    <h1>{html.escape(requirement.canonical_requirement)}</h1>
    <p><span class="status">{requirement.status.value}</span> Score: {requirement.current_scores.get('overall_score', 0)}</p>
    <div class="linkbar">
      <a href="/agent-log?role=discovery&ref={html.escape(requirement.requirement_id)}">Requirement Search Agent Log</a>
      <a href="/agent-log?role=deep_research&ref={html.escape(requirement.requirement_id)}">Deep Research Agent Log</a>
      <a href="/requirement-samples?requirement_id={html.escape(requirement.requirement_id)}">Pool Samples</a>
      <a href="/possible">Back to Possible Requirements</a>
      <a href="/rejected">Back to Rejected Requirements</a>
    </div>
    <h2>Executive Summary</h2>
    <p>
      <a href="/action?type=approve&id={html.escape(requirement.requirement_id)}">Approve research</a> |
      <a href="/action?type=pause&id={html.escape(requirement.requirement_id)}">Pause</a> |
      <a href="/action?type=reject&id={html.escape(requirement.requirement_id)}">Reject as noise</a> |
      <a href="/action?type=force-reopen&id={html.escape(requirement.requirement_id)}">Force reopen</a> |
      <a href="/action?type=priority&id={html.escape(requirement.requirement_id)}&priority=90">Increase priority</a> |
      <a href="/action?type=priority&id={html.escape(requirement.requirement_id)}&priority=25">Decrease priority</a>
    </p>
    <p>{html.escape(requirement.description)}</p>
    <h2>Audience And Geography</h2>
    <p>Audience: {html.escape(', '.join(requirement.audience_segments))}</p>
    <pre>{html.escape(json.dumps(requirement.geo_distribution, indent=2))}</pre>
    <h2>Evidence Timeline</h2>
    <ul>{evidence_rows}</ul>
    <h2>Decision History</h2>
    <pre>{html.escape(json.dumps(requirement.decision_history, indent=2))}</pre>
    <h2>Workflow Timeline</h2>
    <table><thead><tr><th>Time</th><th>Agent</th><th>Event</th><th>Message</th></tr></thead><tbody>{event_rows}</tbody></table>
    <h2>Pool Manager Sample Sentences</h2>
    <table><thead><tr><th>Time</th><th>Sample</th><th>Status</th><th>Task Group Run</th></tr></thead><tbody>{sample_rows}</tbody></table>
    <h2>Research History</h2>
    <table><thead><tr><th>Run</th><th>Agent</th><th>Completed</th><th>Recommendation</th></tr></thead><tbody>{run_rows}</tbody></table>
    <h2>Change Since Last Research</h2>
    <pre>{html.escape(json.dumps(requirement.reopen_events, indent=2))}</pre>
    {report}
    """


def agent_log_page(storage: Storage, role: str, agent_id: str, ref: str = "") -> str:
    logs = storage.list_agent_logs(agent_role=role or None, agent_id=agent_id or None, limit=200)
    experiment_logs = []
    if ref:
        related_refs = {ref}
        requirement = storage.get_requirement(ref)
        if requirement:
            related_refs.update(requirement.evidence_ids)
            related_refs.update(requirement.research_history)
            related_refs.update(requirement.task_group_ids)
            related_refs.update(requirement.task_group_run_ids)
        filtered = []
        for item in logs:
            refs = {str(value) for value in item["input_refs"] + item["output_refs"]}
            if related_refs & refs:
                filtered.append(item)
        logs = filtered
        experiment_logs = storage.list_experiment_logs(task_group_id=ref, agent_role=role or None, limit=100)
    title = role.replace("_", " ").title() if role else agent_id or "Agent"
    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(item['id']))}</td>
          <td>{html.escape(str(item['completed_at'] or item['started_at']))}</td>
          <td>{html.escape(str(item['agent_role']))}</td>
          <td>{html.escape(str(item['agent_id']))}</td>
          <td>{html.escape(str(item['task_id']))}</td>
          <td>{html.escape(str(item['status']))}</td>
          <td>{html.escape(str(item['error'] or ''))}</td>
        </tr>
        """
        for item in logs
    )
    experiment_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(item['log_id']))}</td>
          <td>{html.escape(str(item['created_at']))}</td>
          <td>{html.escape(str(item['agent_role']))}</td>
          <td>{html.escape(str(item['step_name']))}</td>
          <td>{html.escape(str(item['message']))}</td>
        </tr>
        """
        for item in experiment_logs
    )
    full_logs = html.escape(json.dumps(logs, indent=2, default=str))
    full_experiment_logs = html.escape(json.dumps(experiment_logs, indent=2, default=str))
    ref_text = f" Related to {ref}." if ref else ""
    summary = f"{len(logs)} activity log event(s), {len(experiment_logs)} experiment log event(s). Latest status: {logs[0]['status'] if logs else 'none'}.{ref_text}"
    return f"""
    <h1>{html.escape(title)} Log</h1>
    <p class="muted">{html.escape(summary)}</p>
    <div class="linkbar"><a href="/">Running Status</a><a href="/possible">Possible Requirements</a><a href="/rejected">Rejected Requirements</a></div>
    <table><thead><tr><th>ID</th><th>Time</th><th>Role</th><th>Agent</th><th>Task</th><th>Status</th><th>Error</th></tr></thead><tbody>{rows}</tbody></table>
    <h2>Experiment Steps</h2>
    <table><thead><tr><th>ID</th><th>Time</th><th>Role</th><th>Step</th><th>Message</th></tr></thead><tbody>{experiment_rows or "<tr><td colspan='5' class='muted'>No experiment steps for this filter.</td></tr>"}</tbody></table>
    <h2>Full Log Payload</h2>
    <pre>{full_logs}</pre>
    <h2>Full Experiment Payload</h2>
    <pre>{full_experiment_logs}</pre>
    """


def experiment_log_page(storage: Storage, task_group_id: str = "", task_group_run_id: str = "", agent_role: str = "") -> str:
    logs = storage.list_experiment_logs(
        task_group_id=task_group_id or None,
        task_group_run_id=task_group_run_id or None,
        agent_role=agent_role or None,
        limit=500,
    )
    title_parts = ["Experiment Logs"]
    if task_group_id:
        task = storage.get_task_group(task_group_id)
        title_parts.append(task.name if task else task_group_id)
    if agent_role:
        title_parts.append(agent_role.replace("_", " ").title())
    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(item['created_at']))}</td>
          <td>{html.escape(str(item['agent_role']))}</td>
          <td>{html.escape(str(item['step_name']))}</td>
          <td>{html.escape(str(item['message']))}</td>
          <td><pre>{html.escape(json.dumps(item['payload_json'], indent=2, default=str))}</pre></td>
        </tr>
        """
        for item in logs
    ) or "<tr><td colspan='5' class='muted'>No experiment logs yet.</td></tr>"
    return f"""
    <h1>{html.escape(' - '.join(title_parts))}</h1>
    <div class="linkbar"><a href="/">Running Status</a><a href="/possible">Possible Requirements</a><a href="/rejected">Rejected Requirements</a></div>
    <table><thead><tr><th>Time</th><th>Agent</th><th>Step</th><th>Message</th><th>Payload</th></tr></thead><tbody>{rows}</tbody></table>
    """


def requirement_samples_page(
    storage: Storage,
    task_group_id: str = "",
    task_group_run_id: str = "",
    requirement_id: str = "",
) -> str:
    samples = storage.list_requirement_samples(
        task_group_id=task_group_id or None,
        task_group_run_id=task_group_run_id or None,
        requirement_id=requirement_id or None,
        limit=500,
    )
    title = "Requirement Samples"
    if task_group_id:
        task = storage.get_task_group(task_group_id)
        title += f" - {task.name if task else task_group_id}"
    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(item['created_at']))}</td>
          <td><a href="/requirement?id={html.escape(str(item['requirement_id']))}">{html.escape(str(item['requirement_id']))}</a></td>
          <td>{html.escape(str(item['requirement_sentence']))}</td>
          <td>{html.escape(str(item['status']))}</td>
          <td>{html.escape(str(item['task_group_id'] or ''))}</td>
          <td>{html.escape(str(item['task_group_run_id'] or ''))}</td>
        </tr>
        """
        for item in samples
    ) or "<tr><td colspan='6' class='muted'>No requirement samples yet.</td></tr>"
    return f"""
    <h1>{html.escape(title)}</h1>
    <p class="muted">One short sentence for every requirement generated or updated by the pool manager.</p>
    <div class="linkbar"><a href="/">Running Status</a><a href="/possible">Possible Requirements</a><a href="/rejected">Rejected Requirements</a></div>
    <table><thead><tr><th>Time</th><th>Requirement</th><th>Sample</th><th>Status</th><th>Task Group</th><th>Run</th></tr></thead><tbody>{rows}</tbody></table>
    """


def pipeline_page(storage: Storage, pipeline_run_id: str) -> str:
    pipeline = storage.get_pipeline_run(pipeline_run_id)
    if pipeline is None:
        return "<h1>Pipeline not found</h1>"
    requirements = pipeline["requirement_snapshot"]
    logs = pipeline["agent_log_snapshot"]
    req_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(item['requirement_id']))}</td>
          <td>{html.escape(str(item['canonical_requirement']))}</td>
          <td>{html.escape(str(item['status']))}</td>
          <td>{html.escape(str(item['evidence_count']))}</td>
        </tr>
        """
        for item in requirements
    )
    log_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(item['completed_at'] or item['started_at']))}</td>
          <td>{html.escape(str(item['agent_role']))}</td>
          <td>{html.escape(str(item['task_id']))}</td>
          <td>{html.escape(str(item['status']))}</td>
        </tr>
        """
        for item in logs[:30]
    )
    return f"""
    <h1>Pipeline Snapshot</h1>
    <p><span class="status">{html.escape(str(pipeline['status']))}</span> {html.escape(str(pipeline['pipeline_run_id']))}</p>
    <p>{html.escape(str(pipeline['summary']))}</p>
    <div class="linkbar"><a href="/">Running Status</a><a href="/possible">Possible Requirements</a><a href="/rejected">Rejected Requirements</a></div>
    <h2>Cycle Result</h2>
    <pre>{html.escape(json.dumps(pipeline['result'], indent=2, default=str))}</pre>
    <h2>Requirement Snapshot</h2>
    <table><thead><tr><th>ID</th><th>Requirement</th><th>Status</th><th>Evidence</th></tr></thead><tbody>{req_rows}</tbody></table>
    <h2>Agent Log Snapshot</h2>
    <table><thead><tr><th>Time</th><th>Role</th><th>Task</th><th>Status</th></tr></thead><tbody>{log_rows}</tbody></table>
    <h2>Full Snapshot</h2>
    <pre>{html.escape(json.dumps(pipeline, indent=2, default=str))}</pre>
    """


def reports_page(storage: Storage) -> str:
    from .agents import ReportAgent

    report = ReportAgent(storage, "dashboard-report").daily_report()
    return f"<h1>Daily Report</h1><pre>{html.escape(report)}</pre>"


def requirement_table(requirements: list[object]) -> str:
    body = "".join(
        "<tr>"
        f"<td><a href='/requirement?id={html.escape(item.requirement_id)}'>{html.escape(item.canonical_requirement)}</a></td>"
        f"<td><span class='status'>{item.status.value}</span></td>"
        f"<td>{item.current_scores.get('overall_score', 0)}</td>"
        f"<td>{item.times_detected}</td><td>{item.evidence_count}</td><td>{item.subreddit_count}</td>"
        f"<td>{html.escape(', '.join(region['region'] for region in item.geo_distribution[:3]))}</td>"
        f"<td>{html.escape(item.last_seen)}</td><td>{html.escape(str(item.latest_recommendation or ''))}</td>"
        "</tr>"
        for item in requirements
    )
    return (
        "<table><thead><tr><th>Requirement</th><th>Status</th><th>Score</th><th>Times</th>"
        "<th>Evidence</th><th>Subreddits</th><th>Top Regions</th><th>Last Seen</th><th>Last Decision</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )
