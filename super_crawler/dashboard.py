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
                elif parsed.path == "/group-settings":
                    query = parse_qs(parsed.query)
                    if query.get("action", [""])[0] == "save":
                        self._handle_group_settings(storage, query)
                    else:
                        self._html(group_settings_page(storage, query.get("id", [""])[0]))
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
            name = query.get("name", [""])[0].strip() or "Search Group"
            description = query.get("description", [""])[0].strip()
            domain = description if task_type == TaskGroupType.DOMAIN and description else None
            input_dir = query.get("input_dir", [""])[0].strip()
            if not input_dir:
                folder = name
                slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in folder).strip("_")
                input_dir = f"data/task_inbox/{slug or 'general'}"
            subreddits = split_csv(query.get("subreddits", [""])[0])
            keywords = split_csv(query.get("keywords", [""])[0])
            negative_keywords = split_csv(query.get("negative_keywords", [""])[0])
            storage.create_task_group(name, task_type, domain, input_dir, description, subreddits, keywords, negative_keywords)
        elif action == "start":
            storage.update_task_group_status(task_group_id, TaskGroupStatus.RUNNING)
            from .runner import AlwaysOnRunner

            AlwaysOnRunner(storage, "data/reddit_inbox").run_task_group(task_group_id)
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
        location = "/" if action in {"create", "delete"} else f"/#{task_group_anchor(task_group_id)}"
        self.send_header("Location", location)
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

    def _handle_group_settings(self, storage: Storage, query: dict[str, list[str]]) -> None:
        task_group_id = query.get("id", [""])[0]
        if not storage.get_task_group(task_group_id):
            self.send_error(404)
            return
        storage.update_task_group_config(
            task_group_id,
            {
                "collector_enabled": "1" if query.get("collector_enabled", ["0"])[0] == "1" else "0",
                "collector_command": query.get("collector_command", ["opencli reddit search"])[0].strip() or "opencli reddit search",
                "collector_limit": str(parse_int(query.get("collector_limit", ["25"])[0], 25)),
                "collector_timeout_seconds": str(parse_int(query.get("collector_timeout_seconds", ["120"])[0], 120)),
                "model_search": query.get("model_search", ["deepseek-v4-flash"])[0].strip() or "deepseek-v4-flash",
                "model_pool": query.get("model_pool", ["deepseek-v4-flash"])[0].strip() or "deepseek-v4-flash",
                "model_deep_research": query.get("model_deep_research", ["deepseek-v4-flash"])[0].strip() or "deepseek-v4-flash",
                "model_report": query.get("model_report", ["deepseek-v4-pro"])[0].strip() or "deepseek-v4-pro",
            }
        )
        self.send_response(303)
        self.send_header("Location", f"/#{task_group_anchor(task_group_id)}")
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
    .task-group-box:target {{ border-color: #0d5c75; box-shadow: 0 0 0 3px rgba(13, 92, 117, .12); }}
    .task-group-header {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 14px; background: #eef5f7; border-bottom: 1px solid #d6e3e8; }}
    .task-group-header h2 {{ margin: 0; }}
    .group-actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: flex-end; min-width: 320px; }}
    .run-indicator {{ display: inline-flex; align-items: center; gap: 6px; margin-left: 6px; color: #0b6b3a; font-weight: 700; }}
    .run-dot {{ width: 9px; height: 9px; border-radius: 50%; background: #18a058; animation: pulse 1.2s ease-in-out infinite; }}
    .pipeline-motion {{ height: 4px; max-width: 460px; margin-top: 8px; overflow: hidden; border-radius: 999px; background: #dce9ee; }}
    .pipeline-motion span {{ display: block; width: 45%; height: 100%; border-radius: inherit; background: linear-gradient(90deg, transparent, #0d5c75, transparent); animation: shimmer 1.4s linear infinite; }}
    .latest-activity {{ margin-top: 6px; color: #41515c; font-size: 13px; }}
    @keyframes pulse {{ 0%, 100% {{ transform: scale(.85); opacity: .55; }} 50% {{ transform: scale(1.25); opacity: 1; }} }}
    @keyframes shimmer {{ 0% {{ transform: translateX(-110%); }} 100% {{ transform: translateX(240%); }} }}
    .task-group-body {{ padding: 14px; }}
    .group-records {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 14px; }}
    .group-record {{ border: 1px solid #dce2e8; border-radius: 7px; padding: 10px; background: #fbfcfd; }}
    .group-record-value {{ font-weight: 750; font-size: 17px; margin-top: 2px; overflow-wrap: anywhere; }}
    .notice {{ border: 1px solid #dce2e8; border-radius: 7px; padding: 10px 12px; margin-bottom: 14px; background: #fbfcfd; }}
    .notice.warning {{ border-color: #f0c36a; background: #fff8e6; color: #5f4300; }}
    .panel {{ background: white; border: 1px solid #dce2e8; border-radius: 8px; padding: 14px; min-height: 260px; }}
    .item {{ display: block; padding: 10px; border: 1px solid #e4e9ee; border-radius: 7px; margin-bottom: 8px; color: inherit; text-decoration: none; background: #fbfcfd; }}
    .item:hover {{ border-color: #9eb8c4; background: #f1f6f8; }}
    .title {{ font-weight: 700; }}
    .summary {{ color: #52616b; font-size: 13px; margin-top: 4px; }}
    .controlbar {{ display: flex; align-items: center; justify-content: space-between; gap: 14px; background: white; border: 1px solid #dce2e8; border-radius: 8px; padding: 14px; margin-bottom: 18px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .stacked-form {{ display: grid; grid-template-columns: minmax(160px, 220px) minmax(180px, 260px) minmax(280px, 1fr) auto; gap: 10px; align-items: start; }}
    input, select, textarea {{ border: 1px solid #cfd8df; border-radius: 6px; padding: 8px; min-height: 20px; font: inherit; }}
    textarea {{ min-height: 42px; resize: vertical; }}
    input[type="number"] {{ width: 80px; }}
    .button {{ border: 0; border-radius: 6px; padding: 9px 14px; color: white; background: #0d5c75; cursor: pointer; font-weight: 650; }}
    .button.stop {{ background: #b42318; }}
    .button.secondary {{ background: #52616b; }}
    .button.danger {{ background: #8a2d22; }}
    .toggle-button {{ background: #52616b; }}
    input[type="checkbox"]:checked + .toggle-button {{ background: #0d5c75; }}
    .hidden-check {{ position: absolute; opacity: 0; pointer-events: none; }}
    .link-button {{ display: inline-block; text-decoration: none; }}
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
    requirements = storage.list_requirements()
    task_groups = visible_task_groups(storage)
    return (
        "<h1>Running Status</h1>"
        + resource_allocation_panel(storage)
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
        if ref:
            role_logs = [
                item
                for item in role_logs
                if ref in {str(value) for value in item["input_refs"] + item["output_refs"]}
            ]
        latest = role_logs[0] if role_logs else None
        runtime_agents.append(
            {
                "role": role,
                "count": len(role_logs),
                "latest_status": latest["status"] if latest else "idle",
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


def deep_research_agent_cards(storage: Storage, task_group: object, requirements: list[object]) -> str:
    related_refs = {task_group.task_group_id}
    for run in storage.list_task_group_runs(task_group.task_group_id, limit=50):
        related_refs.add(run.task_group_run_id)
    for requirement in requirements:
        related_refs.add(requirement.requirement_id)
        related_refs.update(requirement.research_history)
    logs = []
    for item in storage.list_agent_logs(agent_role="deep_research", limit=300):
        refs = {str(value) for value in item["input_refs"] + item["output_refs"]}
        if refs & related_refs:
            logs.append(item)
    latest = logs[0] if logs else None
    status = latest["status"] if latest else "idle"
    task = latest["task_id"] if latest else "no deep research for this group yet"
    time = latest["completed_at"] if latest else ""
    return f"""
    <a class="item" href="/agent-log?role=deep_research&ref={html.escape(task_group.task_group_id)}">
      <div class="title">Deep Research</div>
      <div><span class="status{' running' if status != 'idle' else ''}">{html.escape(str(status))}</span></div>
      <div class="summary">{html.escape(str(task))}</div>
      <div class="summary">{len(logs)} group log event(s) {html.escape(str(time or ''))}</div>
    </a>
    """


def task_group_boards(storage: Storage, task_groups: list[object], requirements: list[object]) -> str:
    if not task_groups:
        return "<section class='card'><p class='muted'>No task group yet. Create a general or domain task above.</p></section>"
    return "".join(task_group_board(storage, task_group, requirements) for task_group in task_groups)


def task_group_board(storage: Storage, task_group: object, requirements: list[object]) -> str:
    group_requirements = [item for item in requirements if task_group.task_group_id in item.task_group_ids]
    waiting = [item for item in group_requirements if item.status in waiting_statuses()]
    return (
        f"<section class=\"task-group-box\" id=\"{html.escape(task_group_anchor(task_group.task_group_id))}\">"
        + task_group_header(storage, task_group, group_requirements)
        + "<div class='task-group-body'>"
        + task_group_diagnostic(storage, task_group)
        + task_group_record_summary(storage, task_group, group_requirements)
        + "<section class='workbench'>"
        + task_group_search_panel(storage, task_group)
        + waiting_requirements_panel(waiting)
        + task_group_deep_research_panel(storage, task_group, group_requirements)
        + "</section></div></section>"
    )


def task_group_header(storage: Storage, task_group: object, requirements: list[object]) -> str:
    status_class = " running" if task_group.status == TaskGroupStatus.RUNNING else ""
    running = task_group.status == TaskGroupStatus.RUNNING
    latest = latest_task_group_activity(storage, task_group)
    run_indicator = "<span class='run-indicator'><span class='run-dot'></span>Running</span>" if running else ""
    motion = "<div class='pipeline-motion'><span></span></div>" if running else ""
    return f"""
    <div class="task-group-header">
      <div>
        <h2>{html.escape(task_group.name)}</h2>
        <div class="summary"><span class="status{status_class}">{html.escape(task_group.status.value)}</span>{run_indicator} {html.escape(task_group.task_type.value)} | {len(requirements)} requirement(s)</div>
        <div class="summary">{html.escape(task_group.description or 'No search description yet.')}</div>
        <div class="latest-activity">Latest: {html.escape(latest)}</div>
        {motion}
      </div>
      <div class="group-actions">
        <form action="/task"><input type="hidden" name="action" value="start"><input type="hidden" name="id" value="{html.escape(task_group.task_group_id)}"><button class="button">Start</button></form>
        <form action="/task"><input type="hidden" name="action" value="stop"><input type="hidden" name="id" value="{html.escape(task_group.task_group_id)}"><button class="button stop">Stop</button></form>
        <form action="/task"><input type="hidden" name="action" value="delete"><input type="hidden" name="id" value="{html.escape(task_group.task_group_id)}"><button class="button danger">Delete</button></form>
        <a class="button link-button secondary" href="/group-settings?id={html.escape(task_group.task_group_id)}">Settings</a>
        <a class="button link-button secondary" href="/experiment-log?task_group_id={html.escape(task_group.task_group_id)}">Details</a>
        <a href="/possible?task_group_id={html.escape(task_group.task_group_id)}">Possible</a>
        <a href="/rejected?task_group_id={html.escape(task_group.task_group_id)}">Rejected</a>
      </div>
    </div>
    """


def task_group_record_summary(storage: Storage, task_group: object, requirements: list[object]) -> str:
    requirement_ids = {item.requirement_id for item in requirements}
    queue = [row for row in storage.list_queue() if row["requirement_id"] in requirement_ids]
    possible = [item for item in requirements if item.status not in {RequirementStatus.REJECTED, RequirementStatus.ARCHIVED}]
    researching = [item for item in requirements if item.status == RequirementStatus.RESEARCHING]
    rejected = [item for item in requirements if item.status in {RequirementStatus.REJECTED, RequirementStatus.ARCHIVED}]
    runs = storage.list_task_group_runs(task_group.task_group_id, limit=1)
    last_run = runs[0].completed_at or runs[0].started_at if runs else "Never"
    last_summary = runs[0].summary if runs else "No cycle has completed yet."
    records = [
        ("Possible", len(possible)),
        ("Queued", len(queue)),
        ("Researching", len(researching)),
        ("Rejected", len(rejected)),
        ("Last run", last_run),
        ("Last cycle", last_summary),
    ]
    return (
        "<section class='group-records'>"
        + "".join(
            f"<div class='group-record'><div class='muted'>{html.escape(label)}</div><div class='group-record-value'>{html.escape(str(value))}</div></div>"
            for label, value in records
        )
        + "</section>"
    )


def task_group_diagnostic(storage: Storage, task_group: object) -> str:
    config = storage.get_task_group_config(task_group.task_group_id)
    recent = storage.list_experiment_logs(task_group_id=task_group.task_group_id, limit=20)
    latest_input = next((item for item in recent if item["step_name"] == "input_loaded"), None)
    latest_files = next((item for item in recent if item["step_name"] == "files_read"), None)
    latest_collector_failed = next((item for item in recent if item["step_name"] == "collector_failed"), None)
    latest_collector_skipped = next((item for item in recent if item["step_name"] == "collector_skipped"), None)
    if latest_collector_failed:
        return f"<section class='notice warning'><strong>Collector failed.</strong> {html.escape(latest_collector_failed['message'])}</section>"
    if latest_input and int(latest_input["payload_json"].get("items_loaded", 0)) == 0:
        file_count = len(latest_files["payload_json"].get("files", [])) if latest_files else 0
        if latest_collector_skipped or config.get("collector_enabled") != "1":
            return (
                "<section class='notice warning'><strong>No input is being collected.</strong> "
                f"OpenCLI is disabled and {html.escape(task_group.input_dir)} has {file_count} JSON file(s). "
                "Enable OpenCLI in this group Settings, or put Reddit-like JSON files in the group input folder.</section>"
            )
        return (
            "<section class='notice warning'><strong>No input loaded.</strong> "
            f"{html.escape(task_group.input_dir)} has {file_count} JSON file(s), but the latest run loaded 0 item(s).</section>"
        )
    return ""


def latest_task_group_activity(storage: Storage, task_group: object) -> str:
    logs = storage.list_experiment_logs(task_group_id=task_group.task_group_id, limit=1)
    if logs:
        return str(logs[0]["message"])
    if task_group.status == TaskGroupStatus.RUNNING:
        return "Waiting for first scheduler cycle."
    return "No run yet."


def task_group_search_panel(storage: Storage, task_group: object) -> str:
    return (
        "<section class='panel'><h2>Discovery Agents</h2>"
        + agent_cards(storage, ["discovery"], task_group.task_group_id)
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
        + deep_research_agent_cards(storage, task_group, requirements)
        + queue_text
        + "</section>"
    )


def group_settings_page(storage: Storage, task_group_id: str) -> str:
    task_group = storage.get_task_group(task_group_id)
    if task_group is None:
        return "<h1>Task group not found</h1>"
    config = storage.get_task_group_config(task_group_id)
    checked = " checked" if config.get("collector_enabled") == "1" else ""
    return f"""
    <h1>{html.escape(task_group.name)} Settings</h1>
    <div class="linkbar"><a href="/#{html.escape(task_group_anchor(task_group_id))}">Back to Group</a></div>
    <section class="card">
      <h2>Reddit Collection</h2>
      <form action="/group-settings" class="actions">
        <input type="hidden" name="action" value="save">
        <input type="hidden" name="id" value="{html.escape(task_group_id)}">
        <label>
          <input class="hidden-check" type="checkbox" name="collector_enabled" value="1"{checked}>
          <span class="button toggle-button">OpenCLI Collection</span>
        </label>
        <label>Results per run <input type="number" min="1" name="collector_limit" value="{html.escape(config["collector_limit"])}"></label>
        <label>Default model {model_select("model_search", config["model_search"])}</label>
        <label>Deep research model {model_select("model_deep_research", config["model_deep_research"])}</label>
        <details>
          <summary>Advanced</summary>
          <div class="actions">
            <label>Command <input name="collector_command" value="{html.escape(config["collector_command"])}"></label>
            <label>Timeout <input type="number" min="1" name="collector_timeout_seconds" value="{html.escape(config["collector_timeout_seconds"])}"></label>
            <label>Pool model {model_select("model_pool", config["model_pool"])}</label>
            <label>Report model {model_select("model_report", config["model_report"])}</label>
          </div>
        </details>
        <button class="button">Save Settings</button>
      </form>
    </section>
    """


def model_select(name: str, selected: str) -> str:
    options = ["deepseek-v4-flash", "deepseek-v4-pro"]
    return (
        f"<select name=\"{html.escape(name)}\">"
        + "".join(
            f"<option value=\"{html.escape(option)}\"{' selected' if option == selected else ''}>{html.escape(option)}</option>"
            for option in options
        )
        + "</select>"
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
      <form action="/task" class="stacked-form">
        <input type="hidden" name="action" value="create">
        <select name="type" aria-label="Task group type">
          <option value="general_search">General Search</option>
          <option value="domain_search">Domain Specific</option>
        </select>
        <input name="name" placeholder="Group name">
        <textarea name="description" placeholder="What are we planning to search?"></textarea>
        <button class="button">Create</button>
      </form>
    </section>
    """


def task_group_card(task: object) -> str:
    status_class = " running" if task.status == TaskGroupStatus.RUNNING else ""
    return f"""
    <div class="item">
      <div class="title">{html.escape(task.name)}</div>
      <div><span class="status{status_class}">{html.escape(task.status.value)}</span> {html.escape(task.task_type.value)}</div>
      <div class="summary">{html.escape(task.description or 'No search description yet.')}</div>
      <div class="summary">Input: {html.escape(task.input_dir)}</div>
      <div class="actions">
        <form action="/task"><input type="hidden" name="action" value="start"><input type="hidden" name="id" value="{html.escape(task.task_group_id)}"><button class="button">Start</button></form>
        <form action="/task"><input type="hidden" name="action" value="stop"><input type="hidden" name="id" value="{html.escape(task.task_group_id)}"><button class="button stop">Stop</button></form>
        <form action="/task"><input type="hidden" name="action" value="delete"><input type="hidden" name="id" value="{html.escape(task.task_group_id)}"><button class="button danger">Delete</button></form>
      </div>
      <div class="linkbar">
        <a href="/experiment-log?task_group_id={html.escape(task.task_group_id)}">Details</a>
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


def task_group_anchor(task_group_id: str) -> str:
    return "group-" + "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in task_group_id)


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
