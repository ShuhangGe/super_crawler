from __future__ import annotations

import html
import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .models import RequirementStatus
from .runtime import RuntimeController
from .storage import Storage


def serve_dashboard(
    storage: Storage,
    host: str = "127.0.0.1",
    port: int = 8000,
    input_dir: str = "data/reddit_inbox",
    interval_seconds: int = 60,
) -> None:
    controller = RuntimeController(storage.db_path, input_dir=input_dir, interval_seconds=interval_seconds)

    class Handler(DashboardHandler):
        app_storage = storage
        app_controller = controller

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Dashboard listening on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        storage.close()


class DashboardHandler(BaseHTTPRequestHandler):
    app_storage: Storage
    app_controller: RuntimeController

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._html(home_page(self.app_storage, self.app_controller))
        elif parsed.path == "/pool":
            self._html(pool_page(self.app_storage, parse_qs(parsed.query)))
        elif parsed.path == "/queue":
            self._html(queue_page(self.app_storage))
        elif parsed.path == "/requirement":
            requirement_id = parse_qs(parsed.query).get("id", [""])[0]
            self._html(detail_page(self.app_storage, requirement_id))
        elif parsed.path == "/reports":
            self._html(reports_page(self.app_storage))
        elif parsed.path == "/api/requirements":
            self._json([asdict(requirement) for requirement in self.app_storage.list_requirements()])
        elif parsed.path == "/action":
            self._handle_action(parse_qs(parsed.query))
        elif parsed.path == "/runtime":
            self._handle_runtime(parse_qs(parsed.query))
        elif parsed.path == "/api/runtime":
            self._json(self.app_controller.status())
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

    def _handle_action(self, query: dict[str, list[str]]) -> None:
        action = query.get("type", [""])[0]
        requirement_id = query.get("id", [""])[0]
        target_id = query.get("target", [""])[0]
        priority = int(query.get("priority", ["75"])[0])
        if action == "approve":
            self.app_storage.update_requirement_status(requirement_id, RequirementStatus.QUEUED_FOR_RESEARCH, "human approved deep research")
            self.app_storage.enqueue_research(requirement_id, priority, "human approved deep research", 0, None)
        elif action == "pause":
            self.app_storage.dequeue_research(requirement_id)
            self.app_storage.update_requirement_status(requirement_id, RequirementStatus.WATCHING, "human paused research")
        elif action == "reject":
            self.app_storage.dequeue_research(requirement_id)
            self.app_storage.update_requirement_status(requirement_id, RequirementStatus.REJECTED, "human rejected as noise")
        elif action == "force-reopen":
            self.app_storage.update_requirement_status(requirement_id, RequirementStatus.REOPENED, "human forced reopen")
            self.app_storage.enqueue_research(requirement_id, priority, "human forced reopen", 0, None)
        elif action == "priority":
            self.app_storage.update_queue_priority(requirement_id, priority)
        elif action == "merge" and target_id:
            self.app_storage.merge_requirements(requirement_id, target_id)
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


def layout(content: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Requirement Discovery</title>
  <style>
    body {{ margin: 0; font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2933; background: #f7f8fa; }}
    header {{ display: flex; align-items: center; gap: 20px; padding: 14px 22px; background: #12343b; color: white; }}
    header a {{ color: white; text-decoration: none; opacity: .9; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 22px; }}
    h1 {{ margin: 0 0 18px; font-size: 26px; }}
    h2 {{ margin-top: 28px; font-size: 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .card {{ background: white; border: 1px solid #dce2e8; border-radius: 8px; padding: 14px; }}
    .controlbar {{ display: flex; align-items: center; justify-content: space-between; gap: 14px; background: white; border: 1px solid #dce2e8; border-radius: 8px; padding: 14px; margin-bottom: 18px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .button {{ border: 0; border-radius: 6px; padding: 9px 14px; color: white; background: #0d5c75; cursor: pointer; font-weight: 650; }}
    .button.stop {{ background: #b42318; }}
    .button.secondary {{ background: #52616b; }}
    .metric {{ font-size: 28px; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dce2e8; }}
    th, td {{ padding: 10px; border-bottom: 1px solid #e6ebf0; text-align: left; vertical-align: top; }}
    th {{ background: #eef3f6; font-weight: 650; }}
    .status {{ display: inline-block; padding: 2px 7px; border-radius: 999px; background: #e8f2ff; }}
    .muted {{ color: #687782; }}
    a {{ color: #0d5c75; }}
    pre {{ white-space: pre-wrap; background: white; border: 1px solid #dce2e8; padding: 14px; border-radius: 8px; }}
  </style>
</head>
<body>
  <header>
    <strong>Requirement Discovery</strong>
    <a href="/">Home</a>
    <a href="/pool">Pool</a>
    <a href="/queue">Research Queue</a>
    <a href="/reports">Reports</a>
  </header>
  <main>{content}</main>
</body>
</html>"""


def home_page(storage: Storage, controller: RuntimeController) -> str:
    counts = storage.dashboard_counts()
    runtime = controller.status()
    requirements = storage.list_requirements()
    activity = storage.list_activity_logs(12)
    by_status = {status.value: 0 for status in RequirementStatus}
    for requirement in requirements:
        by_status[requirement.status.value] = by_status.get(requirement.status.value, 0) + 1
    rising = sorted(requirements, key=lambda item: item.current_scores.get("velocity_score", 0), reverse=True)[:5]
    cards = [
        ("New candidate requirements", by_status.get("new_candidate", 0)),
        ("Queued for research", counts["queued"]),
        ("Currently researching", by_status.get("researching", 0)),
        ("Validated requirements", by_status.get("validated", 0)),
        ("Reopened requirements", by_status.get("reopened", 0)),
        ("Rejected/noisy", by_status.get("rejected", 0)),
    ]
    return (
        "<h1>System Health</h1>"
        + runtime_controls(runtime)
        + "<section class='grid'>"
        + "".join(f"<div class='card'><div class='muted'>{label}</div><div class='metric'>{value}</div></div>" for label, value in cards)
        + "</section>"
        + runtime_monitor(runtime)
        + "<h2>Top Rising Requirements</h2>"
        + requirement_table(rising)
        + "<h2>Agent Activity</h2>"
        + f"<p>{counts['activity_logs']} logged agent events. {counts['evidence']} raw evidence items preserved.</p>"
        + activity_table(activity)
    )


def runtime_controls(runtime: dict[str, object]) -> str:
    state = "Stopping" if runtime["stopping"] else "Running" if runtime["running"] else "Stopped"
    return f"""
    <section class="controlbar">
      <div>
        <strong>Agent Runtime</strong>
        <div class="muted">State: {state} | Cycles: {runtime["cycle_count"]} | Input: {html.escape(str(runtime["input_dir"]))}</div>
      </div>
      <div class="actions">
        <form action="/runtime"><input type="hidden" name="action" value="start"><button class="button">Start</button></form>
        <form action="/runtime"><input type="hidden" name="action" value="stop"><button class="button stop">Stop</button></form>
        <form action="/runtime"><input type="hidden" name="action" value="run-once"><button class="button secondary">Run Once</button></form>
      </div>
    </section>
    """


def runtime_monitor(runtime: dict[str, object]) -> str:
    last_result = runtime.get("last_result")
    last_error = runtime.get("last_error")
    events = runtime.get("events", [])
    rows = "".join(
        f"<tr><td>{html.escape(str(event['at']))}</td><td>{html.escape(str(event['event']))}</td><td><code>{html.escape(json.dumps(event['detail']))}</code></td></tr>"
        for event in events[:8]
    )
    return (
        "<h2>Runtime Monitor</h2>"
        f"<p>Last result: <code>{html.escape(json.dumps(last_result))}</code></p>"
        f"<p>Last error: <code>{html.escape(str(last_error or 'none'))}</code></p>"
        "<table><thead><tr><th>Time</th><th>Event</th><th>Detail</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def activity_table(activity: list[dict[str, object]]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item['completed_at'] or item['started_at']))}</td>"
        f"<td>{html.escape(str(item['agent_role']))}</td>"
        f"<td>{html.escape(str(item['agent_id']))}</td>"
        f"<td>{html.escape(str(item['task_id']))}</td>"
        f"<td>{html.escape(str(item['status']))}</td>"
        f"<td>{html.escape(str(item['error'] or ''))}</td>"
        "</tr>"
        for item in activity
    )
    return (
        "<table><thead><tr><th>Time</th><th>Role</th><th>Agent</th><th>Task</th><th>Status</th><th>Error</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def pool_page(storage: Storage, query: dict[str, list[str]]) -> str:
    requirements = storage.list_requirements()
    status = query.get("status", [""])[0]
    if status:
        requirements = [item for item in requirements if item.status.value == status]
    options = "".join(f"<option value='{s.value}'>{s.value}</option>" for s in RequirementStatus)
    return (
        "<h1>Requirement Pool</h1>"
        "<form><label>Status <select name='status'><option value=''>all</option>"
        + options
        + "</select></label> <button>Filter</button></form>"
        + requirement_table(requirements)
    )


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
    evidence_rows = "".join(
        f"<li>{html.escape(item.subreddit)}: <a href='{html.escape(item.source_url)}'>{html.escape(item.title)}</a></li>"
        for item in evidence
    )
    latest = runs[0] if runs else None
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
    <h2>Change Since Last Research</h2>
    <pre>{html.escape(json.dumps(requirement.reopen_events, indent=2))}</pre>
    {report}
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
