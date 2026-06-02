from __future__ import annotations

import html
import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable
from urllib.parse import parse_qs, quote, urlparse

from .models import RequirementStatus, TaskGroupStatus, TaskGroupType
from .runtime import RuntimeController
from .storage import Storage


# ---------------------------------------------------------------------------
# Internationalization (i18n)
# ---------------------------------------------------------------------------

TRANSLATIONS: dict[str, dict[str, str]] = {
    # -- layout / nav --
    "app_title": {"en": "Requirement Discovery", "zh": "需求发现系统"},
    "nav_running_status": {"en": "Running Status", "zh": "运行状态"},
    "nav_possible": {"en": "Possible Requirements", "zh": "潜在需求"},
    "nav_rejected": {"en": "Rejected Requirements", "zh": "已拒绝需求"},
    "nav_todo": {"en": "Todo Jobs", "zh": "待办事项"},
    "lang_en": {"en": "EN", "zh": "EN"},
    "lang_zh": {"en": "中文", "zh": "中文"},
    # -- home page --
    "home_title": {"en": "Running Status", "zh": "运行状态"},
    "global_resource_allocation": {"en": "Global Resource Allocation", "zh": "全局资源分配"},
    "search_slots": {"en": "Search slots", "zh": "搜索槽位"},
    "deep_research_slots": {"en": "Deep research slots", "zh": "深度研究槽位"},
    "report_slots": {"en": "Report slots", "zh": "报告槽位"},
    "queue": {"en": "Queue", "zh": "队列"},
    "search_label": {"en": "Search", "zh": "搜索"},
    "deep_label": {"en": "Deep", "zh": "深度"},
    "report_label": {"en": "Report", "zh": "报告"},
    "save_limits": {"en": "Save Limits", "zh": "保存限制"},
    "create_task_group": {"en": "Create Task Group", "zh": "创建任务组"},
    "general_search": {"en": "General Search", "zh": "通用搜索"},
    "domain_specific": {"en": "Domain Specific", "zh": "领域专项"},
    "group_name_placeholder": {"en": "Group name", "zh": "组名称"},
    "domain_search_plan_placeholder": {"en": "Domain search plan", "zh": "领域搜索计划"},
    "create": {"en": "Create", "zh": "创建"},
    "no_task_group": {"en": "No task group yet. Create a general or domain task above.", "zh": "还没有任务组，请在上方创建通用或领域任务。"},
    # -- agent runtime --
    "agent_runtime": {"en": "Agent Runtime", "zh": "智能体运行时"},
    "state": {"en": "State", "zh": "状态"},
    "cycles": {"en": "Cycles", "zh": "循环次数"},
    "input": {"en": "Input", "zh": "输入"},
    "last_result": {"en": "Last result", "zh": "上次结果"},
    "start": {"en": "Start", "zh": "启动"},
    "stop": {"en": "Stop", "zh": "停止"},
    "run_once": {"en": "Run Once", "zh": "运行一次"},
    # -- task group header --
    "running": {"en": "Running", "zh": "运行中"},
    "requirement_count": {"en": "requirement(s)", "zh": "个需求"},
    "no_search_description": {"en": "No search description yet.", "zh": "暂无搜索描述。"},
    "latest": {"en": "Latest", "zh": "最新"},
    "delete": {"en": "Delete", "zh": "删除"},
    "settings": {"en": "Settings", "zh": "设置"},
    "details": {"en": "Details", "zh": "详情"},
    "possible": {"en": "Possible", "zh": "潜在"},
    "rejected": {"en": "Rejected", "zh": "已拒绝"},
    # -- task group settings --
    "settings_title": {"en": "Settings", "zh": "设置"},
    "opencli_collection": {"en": "OpenCLI Collection", "zh": "OpenCLI 采集"},
    "opencli_hint": {"en": "Requires Node.js and OpenCLI on PATH. npm install -g @jackwener/opencli, then restart the dashboard. Reddit search also needs the OpenCLI Browser Bridge extension connected in Chrome/Chromium.", "zh": "需要 Node.js 和 OpenCLI 在 PATH 中。执行 npm install -g @jackwener/opencli 后重启仪表盘。Reddit 搜索还需要在 Chrome/Chromium 中连接 OpenCLI Browser Bridge 扩展。"},
    "results_per_run": {"en": "Results per run", "zh": "每次运行结果数"},
    "default_model": {"en": "Default model", "zh": "默认模型"},
    "deep_research_model": {"en": "Deep research model", "zh": "深度研究模型"},
    "advanced": {"en": "Advanced", "zh": "高级设置"},
    "command": {"en": "Command", "zh": "命令"},
    "timeout": {"en": "Timeout", "zh": "超时"},
    "memory_model": {"en": "Memory model", "zh": "记忆模型"},
    "report_model": {"en": "Report model", "zh": "报告模型"},
    "save_settings": {"en": "Save Settings", "zh": "保存设置"},
    # -- task group records --
    "generated": {"en": "Generated", "zh": "已生成"},
    "queued": {"en": "Queued", "zh": "排队中"},
    "researching": {"en": "Researching", "zh": "研究中"},
    "accepted": {"en": "Accepted", "zh": "已接受"},
    "last_run": {"en": "Last run", "zh": "上次运行"},
    "last_cycle": {"en": "Last cycle", "zh": "上次循环"},
    "never": {"en": "Never", "zh": "从未"},
    "no_cycle_completed": {"en": "No cycle has completed yet.", "zh": "尚未完成任何循环。"},
    # -- task group diagnostics --
    "collector_failed": {"en": "Collector failed.", "zh": "采集器失败。"},
    "no_input_collected": {"en": "No input is being collected.", "zh": "没有正在采集的输入。"},
    "opencli_disabled": {"en": "OpenCLI is disabled and", "zh": "OpenCLI 已禁用，"},
    "has_file": {"en": "has", "zh": "有"},
    "json_files": {"en": "JSON file(s). Enable OpenCLI in this group Settings, or put Reddit-like JSON files in the group input folder.", "zh": "个 JSON 文件。请在组设置中启用 OpenCLI，或将 Reddit 格式的 JSON 文件放入组输入文件夹。"},
    "no_input_loaded": {"en": "No input loaded.", "zh": "没有加载输入。"},
    "has_file_but_loaded": {"en": "has", "zh": "有"},
    "items_loaded_zero": {"en": "JSON file(s), but the latest run loaded 0 item(s).", "zh": "个 JSON 文件，但最近一次运行加载了 0 条数据。"},
    # -- search panel --
    "search_planner": {"en": "Search Planner", "zh": "搜索规划器"},
    "discovery_agents": {"en": "Discovery Agents", "zh": "发现智能体"},
    "search_planner_agent": {"en": "Search Planner Agent", "zh": "搜索规划智能体"},
    "cycle": {"en": "cycle", "zh": "循环"},
    "preview": {"en": "preview", "zh": "预览"},
    "strategy": {"en": "Strategy", "zh": "策略"},
    "query": {"en": "Query", "zh": "查询"},
    "no_active_discovery": {"en": "No active discovery agent for this group.", "zh": "该组没有活跃的发现智能体。"},
    "waiting_first_cycle": {"en": "Waiting for first scheduler cycle.", "zh": "等待首次调度循环。"},
    "no_run_yet": {"en": "No run yet.", "zh": "尚未运行。"},
    # -- waiting requirements panel --
    "waiting_for_deep_research": {"en": "Possible Requirements Waiting For Deep Research", "zh": "等待深度研究的潜在需求"},
    "discovery_lightweight": {"en": "Discovery agents only do lightweight sample screening. Deep research decides whether each requirement is real.", "zh": "发现智能体仅进行轻量级样本筛选。深度研究决定每个需求是否真实。"},
    "no_found_waiting": {"en": "No found requirement is waiting for verification.", "zh": "没有发现需求正在等待验证。"},
    # -- deep research panel --
    "running_deep_research_agents": {"en": "Running Deep Research Agents", "zh": "运行中的深度研究智能体"},
    "deep_research_agent": {"en": "Deep Research Agent", "zh": "深度研究智能体"},
    "researching_now": {"en": "Researching now", "zh": "正在研究"},
    "assigned_to_slot": {"en": "Assigned to a deep research slot", "zh": "已分配到深度研究槽位"},
    "queued_for_deep_research": {"en": "Queued for Deep Research", "zh": "已排队等待深度研究"},
    "waiting_for_agent_slot": {"en": "Waiting for an available deep research agent slot.", "zh": "正在等待可用的深度研究智能体槽位。"},
    "requirement_label": {"en": "Requirement", "zh": "需求"},
    "no_active_deep_research": {"en": "No active deep research agent for this group.", "zh": "该组没有活跃的深度研究智能体。"},
    "deep_research_disabled": {"en": "Deep research is disabled by the global resource limit.", "zh": "深度研究已被全局资源限制禁用。"},
    "deep_research_dynamic": {"en": "Deep research agents are created dynamically from queued requirements, up to the global Deep limit.", "zh": "深度研究智能体根据排队需求动态创建，上限为全局深度限制。"},
    "next_requirement": {"en": "Next requirement", "zh": "下一个需求"},
    "priority": {"en": "priority", "zh": "优先级"},
    "waiting_behind_slots": {"en": "requirement(s) waiting behind active slots.", "zh": "个需求正在等待活跃槽位释放。"},
    "queued_paused": {"en": "queued requirement(s) paused until this group starts.", "zh": "个排队中的需求已暂停，直到该组启动。"},
    "no_deep_research_queue": {"en": "No deep research queue item for this task group.", "zh": "该任务组没有深度研究队列项。"},
    "no_active_deep_research_agent": {"en": "No active deep research agent.", "zh": "没有活跃的深度研究智能体。"},
    "deep_research_consume": {"en": "Deep research agents consume requirements from the queue and produce conclusions.", "zh": "深度研究智能体从队列中消费需求并生成结论。"},
    # -- pipeline --
    "saved_pipeline_snapshots": {"en": "Pipeline Run Records", "zh": "管道运行记录"},
    "pipeline_snapshot_description": {"en": "Only cycles with actual work are saved as lightweight records.", "zh": "只有发生实际工作的循环会保存为轻量运行记录。"},
    "pipeline": {"en": "Pipeline", "zh": "管道"},
    "status": {"en": "Status", "zh": "状态"},
    "summary": {"en": "Summary", "zh": "摘要"},
    "completed": {"en": "Completed", "zh": "完成时间"},
    "no_pipeline_snapshot": {"en": "No completed pipeline run record yet.", "zh": "尚无已完成的管道运行记录。"},
    # -- requirement list pages --
    "each_row_preserves": {"en": "Each row preserves the whole line from requirement search to conclusion so it can be evaluated later.", "zh": "每行保留了从需求搜索到结论的完整链路，以便后续评估。"},
    "ungrouped_legacy": {"en": "Ungrouped / Legacy", "zh": "未分组 / 历史数据"},
    "task_group": {"en": "Task Group", "zh": "任务组"},
    "all_groups": {"en": "All groups", "zh": "所有组"},
    "show_group": {"en": "Show Group", "zh": "显示组"},
    "page_label": {"en": "Page", "zh": "页"},
    "previous_page": {"en": "Previous", "zh": "上一页"},
    "next_page": {"en": "Next", "zh": "下一页"},
    "total_items": {"en": "total", "zh": "总计"},
    "general": {"en": "general", "zh": "通用"},
    "input": {"en": "Input", "zh": "输入"},
    "no_requirements_page": {"en": "No requirements in this page yet.", "zh": "该页面暂无需求。"},
    # -- lineage table headers --
    "search_agents": {"en": "Search Agents", "zh": "搜索智能体"},
    "queue_pool": {"en": "Queue / Pool", "zh": "队列 / 池"},
    "deep_research_agents_col": {"en": "Deep Research Agents", "zh": "深度研究智能体"},
    "conclusion": {"en": "Conclusion", "zh": "结论"},
    "saved_line": {"en": "Saved Line", "zh": "已保存"},
    "todo_col": {"en": "Todo", "zh": "待办"},
    "score": {"en": "Score", "zh": "评分"},
    "evidence": {"en": "Evidence", "zh": "证据"},
    "subreddits": {"en": "Subreddits", "zh": "子版块"},
    "times_detected": {"en": "Times detected", "zh": "检测次数"},
    "research_runs": {"en": "research run(s)", "zh": "次研究运行"},
    "reason": {"en": "Reason", "zh": "原因"},
    "no_saved_snapshot": {"en": "No saved snapshot yet", "zh": "尚无保存的快照"},
    "pipeline_snapshot_link": {"en": "Pipeline Run", "zh": "管道运行"},
    "move_to_todo": {"en": "Move to todo list", "zh": "移至待办"},
    "todo_status": {"en": "Todo", "zh": "待办"},
    # -- todo page --
    "todo_jobs_title": {"en": "Todo Jobs", "zh": "待办事项"},
    "todo_description": {"en": "Todo jobs are requirements selected for follow-up work. They preserve links back to the full search, sample-analysis, pool, and deep-research lifecycle.", "zh": "待办事项是选定用于后续跟进的需求。它们保留了指向完整搜索、样本分析、池和深度研究生命周期的链接。"},
    "from": {"en": "From", "zh": "来源"},
    "updated": {"en": "Updated", "zh": "更新"},
    "action": {"en": "Action", "zh": "操作"},
    "mark_done": {"en": "Mark done", "zh": "标记完成"},
    "reopen": {"en": "Reopen", "zh": "重新打开"},
    "created": {"en": "Created", "zh": "创建"},
    "note": {"en": "Note", "zh": "备注"},
    "no_todo_jobs": {"en": "No todo jobs yet. Move possible requirements here when you want to track follow-up work.", "zh": "暂无待办事项。将潜在需求移至此处以跟踪后续工作。"},
    "task_group_unknown": {"en": "Task group: unknown", "zh": "任务组：未知"},
    "task_group_unassigned": {"en": "Task group: unassigned", "zh": "任务组：未分配"},
    # -- queue page --
    "research_queue": {"en": "Research Queue", "zh": "研究队列"},
    "new_evidence": {"en": "New Evidence", "zh": "新证据"},
    "previous_status": {"en": "Previous Status", "zh": "之前状态"},
    "assigned_agent": {"en": "Assigned Agent", "zh": "分配智能体"},
    "lock": {"en": "Lock", "zh": "锁"},
    "cost": {"en": "Cost", "zh": "成本"},
    "eta": {"en": "ETA", "zh": "预计时间"},
    "min": {"en": "min", "zh": "分钟"},
    "unlocked": {"en": "unlocked", "zh": "未锁定"},
    # -- detail page --
    "requirement_not_found": {"en": "Requirement not found", "zh": "未找到需求"},
    "executive_summary": {"en": "Executive Summary", "zh": "执行摘要"},
    "approve_research": {"en": "Approve research", "zh": "批准研究"},
    "pause": {"en": "Pause", "zh": "暂停"},
    "reject_as_noise": {"en": "Reject as noise", "zh": "拒绝（噪音）"},
    "force_reopen": {"en": "Force reopen", "zh": "强制重新打开"},
    "increase_priority": {"en": "Increase priority", "zh": "提高优先级"},
    "decrease_priority": {"en": "Decrease priority", "zh": "降低优先级"},
    "audience_and_geography": {"en": "Audience And Geography", "zh": "受众与地理分布"},
    "audience": {"en": "Audience", "zh": "受众"},
    "evidence_timeline": {"en": "Evidence Timeline", "zh": "证据时间线"},
    "decision_history": {"en": "Decision History", "zh": "决策历史"},
    "workflow_timeline": {"en": "Workflow Timeline", "zh": "工作流时间线"},
    "time": {"en": "Time", "zh": "时间"},
    "agent": {"en": "Agent", "zh": "智能体"},
    "event": {"en": "Event", "zh": "事件"},
    "message": {"en": "Message", "zh": "消息"},
    "sample_sentences": {"en": "Requirement Memory Sample Sentences", "zh": "需求记忆样本句子"},
    "sample": {"en": "Sample", "zh": "样本"},
    "task_group_run": {"en": "Task Group Run", "zh": "任务组运行"},
    "no_workflow_events": {"en": "No workflow events recorded yet.", "zh": "尚无工作流事件记录。"},
    "no_sample_recorded": {"en": "No sample sentence recorded yet.", "zh": "尚无样本句子记录。"},
    "research_history": {"en": "Research History", "zh": "研究历史"},
    "run": {"en": "Run", "zh": "运行"},
    "change_since_last": {"en": "Change Since Last Research", "zh": "自上次研究以来的变更"},
    "research_report": {"en": "Research Report", "zh": "研究报告"},
    "rejected_reason": {"en": "Rejected reason", "zh": "拒绝原因"},
    "why_real": {"en": "Why real", "zh": "真实原因"},
    "why_noise": {"en": "Why noise", "zh": "噪音原因"},
    "recommendation": {"en": "Recommendation", "zh": "建议"},
    "requirement_search_agent_log": {"en": "Requirement Search Agent Log", "zh": "需求搜索智能体日志"},
    "deep_research_agent_log": {"en": "Deep Research Agent Log", "zh": "深度研究智能体日志"},
    "pool_samples": {"en": "Pool Samples", "zh": "池样本"},
    "back_to_possible": {"en": "Back to Possible Requirements", "zh": "返回潜在需求"},
    "back_to_rejected": {"en": "Back to Rejected Requirements", "zh": "返回已拒绝需求"},
    "waiting_for_conclusion": {"en": "Waiting for deep research conclusion", "zh": "等待深度研究结论"},
    # -- agent log page --
    "log_title_suffix": {"en": "Log", "zh": "日志"},
    "activity_log_summary": {"en": "activity log event(s)", "zh": "条活动日志"},
    "experiment_log_summary": {"en": "experiment log event(s)", "zh": "条实验日志"},
    "latest_status": {"en": "Latest status", "zh": "最新状态"},
    "related_to": {"en": "Related to", "zh": "关联到"},
    "none": {"en": "none", "zh": "无"},
    "search_log": {"en": "Search Log", "zh": "搜索日志"},
    "deep_research_log": {"en": "Deep Research Log", "zh": "深度研究日志"},
    "readable_log": {"en": "Readable Log", "zh": "可读日志"},
    "raw_terminal_log": {"en": "Raw Terminal Log", "zh": "原始终端日志"},
    # -- experiment log page --
    "experiment_logs": {"en": "Experiment Logs", "zh": "实验日志"},
    "terminal_style_log": {"en": "Terminal Style Log", "zh": "终端风格日志"},
    "step": {"en": "Step", "zh": "步骤"},
    "payload": {"en": "Payload", "zh": "数据"},
    "no_experiment_logs": {"en": "No experiment logs yet.", "zh": "尚无实验日志。"},
    # -- readable log blocks --
    "deep_research_plan": {"en": "Deep Research Plan", "zh": "深度研究计划"},
    "no_search_tasks": {"en": "No search tasks recorded.", "zh": "没有记录搜索任务。"},
    "question": {"en": "Question", "zh": "问题"},
    "subreddit": {"en": "Subreddit", "zh": "子版块"},
    "any": {"en": "any", "zh": "任意"},
    "returned_analyzed_added": {"en": "Returned / analyzed / added", "zh": "返回 / 分析 / 新增"},
    "error": {"en": "Error", "zh": "错误"},
    "evidence_item_analysis": {"en": "Evidence Item Analysis", "zh": "证据项分析"},
    "title": {"en": "Title", "zh": "标题"},
    "relevant": {"en": "Relevant", "zh": "相关"},
    "type": {"en": "Type", "zh": "类型"},
    "analysis": {"en": "Analysis", "zh": "分析"},
    "signals": {"en": "Signals", "zh": "信号"},
    "evidence_collected": {"en": "Evidence Collected", "zh": "已收集证据"},
    "items_analyzed": {"en": "Items analyzed", "zh": "已分析项目"},
    "new_evidence_count": {"en": "New evidence", "zh": "新证据"},
    "evidence_ids": {"en": "Evidence IDs", "zh": "证据 ID"},
    "deep_research_started": {"en": "Deep Research Started", "zh": "深度研究已开始"},
    "deep_research_output": {"en": "Deep Research Output", "zh": "深度研究输出"},
    "is_real_requirement": {"en": "Is real requirement", "zh": "是否为真实需求"},
    "scale": {"en": "Scale", "zh": "规模"},
    "country_area": {"en": "Country / area", "zh": "国家/地区"},
    "no_geography": {"en": "No geography inferred.", "zh": "未推断出地理信息。"},
    "waiting_for_deep_research_log": {"en": "Waiting For Deep Research", "zh": "等待深度研究"},
    "waiting_deep_research_desc": {"en": "No deep research output has been recorded for this requirement yet. If this requirement is queued, its log will appear after a deep research slot processes it.", "zh": "尚未记录该需求的深度研究输出。如果该需求在队列中，其日志将在深度研究槽位处理后出现。"},
    "search_plan_cycle": {"en": "Search Plan Cycle", "zh": "搜索计划循环"},
    "planner": {"en": "Planner", "zh": "规划器"},
    "user_description": {"en": "User description", "zh": "用户描述"},
    "search_goal": {"en": "Search goal", "zh": "搜索目标"},
    "domain": {"en": "Domain", "zh": "领域"},
    "coverage": {"en": "Coverage", "zh": "覆盖范围"},
    "search_agent_col": {"en": "Search Agent", "zh": "搜索智能体"},
    "why": {"en": "Why", "zh": "原因"},
    "no_planned_searches": {"en": "No planned searches recorded.", "zh": "没有记录计划的搜索。"},
    "items_collected": {"en": "Items collected", "zh": "已收集项目"},
    "output_file": {"en": "Output file", "zh": "输出文件"},
    "no_urls_collected": {"en": "No URLs collected.", "zh": "没有收集到 URL。"},
    "possible_requirement": {"en": "Possible Requirement", "zh": "潜在需求"},
    "sample_rejected": {"en": "Sample Rejected", "zh": "样本已拒绝"},
    "confidence": {"en": "Confidence", "zh": "置信度"},
    "sample_analysis": {"en": "Sample analysis", "zh": "样本分析"},
    "sample_result": {"en": "Sample result", "zh": "样本结果"},
    "no_sample_analysis": {"en": "No sample analysis recorded.", "zh": "没有记录样本分析。"},
    "all_reddit": {"en": "All Reddit", "zh": "所有 Reddit"},
    # -- terminal log --
    "no_log_lines": {"en": "No log lines for this filter.", "zh": "该筛选条件下没有日志。"},
    "no_readable_entries": {"en": "No readable log entries for this filter yet.", "zh": "该筛选条件下尚无可读日志。"},
    "no_search_log_entries": {"en": "No search log entries for this agent yet.", "zh": "该智能体尚无搜索日志。"},
    # -- requirement samples page --
    "requirement_samples": {"en": "Requirement Samples", "zh": "需求样本"},
    "requirement_samples_desc": {"en": "One short sentence for every requirement generated or updated by requirement memory.", "zh": "需求记忆生成或更新的每个需求的短句。"},
    "no_requirement_samples": {"en": "No requirement samples yet.", "zh": "尚无需求样本。"},
    # -- pipeline page --
    "pipeline_snapshot": {"en": "Pipeline Run", "zh": "管道运行"},
    "cycle_result": {"en": "Cycle Result", "zh": "循环结果"},
    "requirement_snapshot": {"en": "Requirement Snapshot", "zh": "需求快照"},
    "agent_log_snapshot": {"en": "Agent Log Snapshot", "zh": "智能体日志快照"},
    "full_snapshot": {"en": "Full Snapshot", "zh": "完整快照"},
    "id": {"en": "ID", "zh": "ID"},
    "pipeline_not_found": {"en": "Pipeline not found", "zh": "未找到管道"},
    # -- reports page --
    "daily_report": {"en": "Daily Report", "zh": "每日报告"},
    # -- status values --
    "status_stopped": {"en": "Stopped", "zh": "已停止"},
    "status_stopping": {"en": "Stopping", "zh": "停止中"},
    "status_running": {"en": "Running", "zh": "运行中"},
    "status_queued": {"en": "queued", "zh": "排队中"},
    "status_watching": {"en": "watching", "zh": "观察中"},
    "status_validated": {"en": "validated", "zh": "已验证"},
    "status_archived": {"en": "archived", "zh": "已归档"},
    "status_rejected": {"en": "rejected", "zh": "已拒绝"},
    "status_reopened": {"en": "reopened", "zh": "已重新打开"},
    "status_needs_more": {"en": "needs_more_evidence", "zh": "需要更多证据"},
    "status_researching": {"en": "researching", "zh": "研究中"},
    "status_queued_for_research": {"en": "queued_for_research", "zh": "排队等待研究"},
    # -- task group type --
    "type_general_search": {"en": "general_search", "zh": "通用搜索"},
    "type_domain_search": {"en": "domain_search", "zh": "领域搜索"},
    # -- log panel --
    "click_for_full_log": {"en": "Click for full log.", "zh": "点击查看完整日志。"},
    "no_logs_yet": {"en": "No logs yet.", "zh": "暂无日志。"},
    # -- requirement card --
    "last_seen": {"en": "Last seen", "zh": "最后发现"},
    # -- rejection --
    "rejected_because": {"en": "Rejected because", "zh": "拒绝原因："},
    "rejected_default": {"en": "Rejected because the deep research evidence was not strong enough.", "zh": "拒绝原因：深度研究证据不够充分。"},
    # -- task group labels --
    "task_group_label": {"en": "Task group", "zh": "任务组"},
    # -- search planner --
    "search_agent": {"en": "Search Agent", "zh": "搜索智能体"},
    # -- log summary panel --
    "log_panel_title": {"en": "Activity Logs", "zh": "活动日志"},
}


def t(key: str, lang: str = "en") -> str:
    """Return translated text for *key* in the given language."""
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key
    return entry.get(lang, entry.get("en", key))


def status_text(value: str, lang: str = "en") -> str:
    """Translate common status/enum display values."""
    mapping = {
        "stopped": "status_stopped",
        "Stopping": "status_stopping",
        "Running": "status_running",
        "running": "status_running",
        "queued": "status_queued",
        "watching": "status_watching",
        "validated": "status_validated",
        "archived": "status_archived",
        "rejected": "status_rejected",
        "reopened": "status_reopened",
        "needs_more_evidence": "status_needs_more",
        "researching": "status_researching",
        "queued_for_research": "status_queued_for_research",
        "general_search": "type_general_search",
        "domain_search": "type_domain_search",
    }
    key = mapping.get(value)
    return t(key, lang) if key else value


def lang_from_cookie(headers: dict[str, str] | None = None, cookie_header: str = "") -> str:
    """Extract language preference from cookie. Defaults to 'en'."""
    if cookie_header:
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("lang="):
                val = part[5:].strip()
                if val in ("en", "zh"):
                    return val
    return "en"


def lang_attr(lang: str) -> str:
    return "zh-CN" if lang == "zh" else "en"


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

    def _lang(self) -> str:
        return lang_from_cookie(cookie_header=self.headers.get("Cookie", ""))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/runtime":
            self._json(self.app_controller.status())
        elif parsed.path == "/runtime":
            self._handle_runtime(parse_qs(parsed.query))
        else:
            lang = self._lang()
            with self._request_storage() as storage:
                storage.migrate()
                if parsed.path == "/":
                    self._html(home_page(storage, self.app_controller, lang))
                elif parsed.path == "/possible":
                    query = parse_qs(parsed.query)
                    page = max(parse_int(query.get("page", ["1"])[0], 1), 1)
                    self._html(
                        requirement_list_page(
                            storage,
                            t("nav_possible", lang),
                            filter_requirements_by_group(possible_requirements(storage), query.get("task_group_id", [""])[0]),
                            query.get("task_group_id", [""])[0],
                            "/possible",
                            page,
                            lang,
                        )
                    )
                elif parsed.path == "/rejected":
                    query = parse_qs(parsed.query)
                    page = max(parse_int(query.get("page", ["1"])[0], 1), 1)
                    self._html(
                        requirement_list_page(
                            storage,
                            t("nav_rejected", lang),
                            filter_requirements_by_group(rejected_requirements(storage), query.get("task_group_id", [""])[0]),
                            query.get("task_group_id", [""])[0],
                            "/rejected",
                            page,
                            lang,
                        )
                    )
                elif parsed.path == "/todo":
                    self._html(todo_page(storage, lang))
                elif parsed.path == "/queue":
                    self._html(queue_page(storage, lang))
                elif parsed.path == "/requirement":
                    requirement_id = parse_qs(parsed.query).get("id", [""])[0]
                    self._html(detail_page(storage, requirement_id, lang))
                elif parsed.path == "/agent-log":
                    query = parse_qs(parsed.query)
                    self._html(
                        agent_log_page(
                            storage,
                            query.get("role", [""])[0],
                            query.get("agent_id", [""])[0],
                            query.get("ref", [""])[0],
                            lang,
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
                            lang,
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
                            lang,
                        )
                    )
                elif parsed.path == "/pipeline":
                    pipeline_run_id = parse_qs(parsed.query).get("id", [""])[0]
                    self._html(pipeline_page(storage, pipeline_run_id, lang))
                elif parsed.path == "/reports":
                    self._html(reports_page(storage, lang))
                elif parsed.path == "/api/requirements":
                    self._json([asdict(requirement) for requirement in storage.list_requirements()])
                elif parsed.path == "/action":
                    self._handle_action(storage, parse_qs(parsed.query))
                elif parsed.path == "/todo-action":
                    self._handle_todo_action(storage, parse_qs(parsed.query))
                elif parsed.path == "/task":
                    self._handle_task(storage, parse_qs(parsed.query))
                elif parsed.path == "/resources":
                    self._handle_resources(storage, parse_qs(parsed.query))
                elif parsed.path == "/group-settings":
                    query = parse_qs(parsed.query)
                    if query.get("action", [""])[0] == "save":
                        self._handle_group_settings(storage, query)
                    else:
                        task_group_id = query.get("id", [""])[0]
                        self.send_response(303)
                        self.send_header("Location", quote(f"/#{task_group_anchor(task_group_id)}" if task_group_id else "/", safe="/:?#"))
                        self.end_headers()
                else:
                    self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _html(self, body: str) -> None:
        lang = self._lang()
        content = layout(body, lang).encode()
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
        self.send_header("Location", quote(f"/requirement?id={requirement_id}", safe="/:?#="))
        self.end_headers()

    def _handle_todo_action(self, storage: Storage, query: dict[str, list[str]]) -> None:
        action = query.get("action", [""])[0]
        requirement_id = query.get("id", [""])[0]
        if action == "add":
            try:
                storage.add_todo_job(requirement_id, query.get("note", [""])[0])
            except ValueError:
                self.send_error(404)
                return
            location = "/todo"
        elif action in {"done", "open"}:
            storage.update_todo_status(requirement_id, "done" if action == "done" else "open")
            location = "/todo"
        else:
            self.send_error(400)
            return
        self.send_response(303)
        self.send_header("Location", quote(location, safe="/:?#"))
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
        self.send_header("Location", quote("/", safe="/:?#"))
        self.end_headers()

    def _handle_task(self, storage: Storage, query: dict[str, list[str]]) -> None:
        action = query.get("action", [""])[0]
        task_group_id = query.get("id", [""])[0]
        if action == "create":
            task_type = TaskGroupType(query.get("type", [TaskGroupType.GENERAL.value])[0])
            name = query.get("name", [""])[0].strip() or "Search Group"
            raw_description = query.get("description", [""])[0].strip()
            description = raw_description if task_type == TaskGroupType.DOMAIN else ""
            domain = description if task_type == TaskGroupType.DOMAIN and description else None
            input_dir = query.get("input_dir", [""])[0].strip()
            if not input_dir:
                folder = name
                slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in folder).strip("_")
                input_dir = f"data/task_inbox/{slug or 'general'}"
            subreddits = split_csv(query.get("subreddits", [""])[0])
            keywords = split_csv(query.get("keywords", [""])[0])
            negative_keywords = split_csv(query.get("negative_keywords", [""])[0])
            storage.create_task_group(
                name,
                task_type,
                domain,
                input_dir,
                description,
                subreddits,
                keywords,
                negative_keywords,
                enable_collector=task_type == TaskGroupType.DOMAIN,
            )
        elif action == "start":
            storage.update_task_group_status(task_group_id, TaskGroupStatus.RUNNING)
            self.app_controller.start()
        elif action == "stop":
            storage.update_task_group_status(task_group_id, TaskGroupStatus.STOPPED)
            if not storage.list_task_groups([TaskGroupStatus.RUNNING.value]):
                self.app_controller.stop()
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
        self.send_header("Location", quote(location, safe="/:?#"))
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
        self.send_header("Location", quote("/", safe="/:?#"))
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
        self.send_header("Location", quote(f"/#{task_group_anchor(task_group_id)}", safe="/:?#"))
        self.end_headers()


def layout(content: str, lang: str = "en") -> str:
    lang_switcher = f"""<span style="margin-left:auto;font-size:13px;display:flex;gap:4px;align-items:center;">
      <a href="#" onclick="document.cookie='lang=en;path=/;max-age=31536000';location.reload();return false" style="opacity:.9;{'text-decoration:underline;font-weight:700' if lang == 'en' else ''}">{t('lang_en', lang)}</a>
      <span style="opacity:.5">|</span>
      <a href="#" onclick="document.cookie='lang=zh;path=/;max-age=31536000';location.reload();return false" style="opacity:.9;{'text-decoration:underline;font-weight:700' if lang == 'zh' else ''}">{t('lang_zh', lang)}</a>
    </span>"""
    return f"""<!doctype html>
<html lang="{lang_attr(lang)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{t('app_title', lang)}</title>
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
	    .top-workspace {{ display: grid; grid-template-columns: minmax(360px, .9fr) minmax(520px, 1.35fr); gap: 12px; align-items: stretch; margin-bottom: 18px; }}
	    .toolbar-card {{ background: #ffffff; border: 1px solid #d9e1e7; border-radius: 8px; padding: 12px; box-shadow: 0 10px 24px rgba(18, 52, 59, .05); }}
	    .toolbar-head {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }}
	    .toolbar-title {{ font-weight: 760; color: #17262d; }}
	    .toolbar-summary {{ color: #65727c; font-size: 12px; white-space: nowrap; }}
	    .resource-card form {{ display: grid; grid-template-columns: repeat(3, minmax(76px, 1fr)) auto; gap: 8px; align-items: end; }}
	    .compact-field {{ display: grid; gap: 4px; color: #52616b; font-size: 12px; }}
	    .compact-field input, .compact-field select {{ width: 100%; box-sizing: border-box; min-height: 32px; padding: 5px 8px; }}
	    .resource-card .button, .create-card .button {{ min-height: 32px; padding: 7px 11px; }}
	    .create-card .stacked-form {{ grid-template-columns: minmax(132px, 170px) minmax(140px, 180px) minmax(220px, 1fr) auto; gap: 8px; align-items: end; }}
	    .create-card textarea {{ min-height: 32px; max-height: 72px; padding: 6px 8px; }}
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
    .group-records {{ display: grid; grid-template-columns: repeat(4, minmax(86px, max-content)) minmax(180px, .8fr) minmax(280px, 1.6fr); gap: 10px; margin-bottom: 14px; align-items: stretch; }}
    .group-record {{ border: 1px solid #dce2e8; border-radius: 7px; padding: 10px; background: #fbfcfd; }}
    .group-record.metric-record {{ min-width: 86px; text-align: center; }}
    .group-record.text-record {{ min-width: 0; }}
    .group-record-value {{ font-weight: 750; font-size: 17px; margin-top: 2px; overflow-wrap: anywhere; }}
    .metric-record .group-record-value {{ font-size: 22px; line-height: 1.1; }}
    .text-record .group-record-value {{ font-size: 13px; font-weight: 650; line-height: 1.35; }}
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
    .domain-description[hidden] {{ display: none; }}
    .inline-settings {{ position: relative; }}
    .inline-settings summary {{ cursor: pointer; list-style: none; }}
    .inline-settings summary::-webkit-details-marker {{ display: none; }}
    .settings-popout {{ position: absolute; right: 0; top: 42px; width: min(720px, calc(100vw - 44px)); z-index: 5; box-shadow: 0 16px 34px rgba(21, 32, 43, .16); }}
    .settings-form {{ align-items: flex-start; }}
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
    .terminal-log {{ background: #101820; color: #e6edf3; border-radius: 8px; padding: 14px; margin-top: 12px; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow-x: auto; }}
    .terminal-entry {{ border-bottom: 1px solid rgba(255, 255, 255, .12); padding: 9px 0; white-space: pre-wrap; }}
    .terminal-entry:last-child {{ border-bottom: 0; }}
    .terminal-meta {{ color: #8bd3ff; }}
    .terminal-message {{ color: #f8fafc; }}
    .terminal-payload {{ color: #b6e3a8; margin-top: 4px; }}
    .terminal-error {{ color: #ffb4a8; margin-top: 4px; }}
    .readable-log {{ display: grid; gap: 12px; margin: 12px 0 18px; }}
    .readable-block {{ background: white; border: 1px solid #dce2e8; border-radius: 8px; padding: 12px; }}
    .readable-block h3 {{ margin-bottom: 8px; }}
    .url-list {{ margin: 8px 0 0; padding-left: 18px; }}
    .url-list li {{ margin-bottom: 5px; overflow-wrap: anywhere; }}
    .muted {{ color: #687782; }}
    a {{ color: #0d5c75; }}
    pre {{ white-space: pre-wrap; background: white; border: 1px solid #dce2e8; padding: 14px; border-radius: 8px; }}
	    @media (max-width: 760px) {{
	      .top-workspace {{ grid-template-columns: 1fr; }}
	      .task-group-header {{ align-items: flex-start; flex-direction: column; }}
	      .group-actions {{ justify-content: flex-start; min-width: 0; }}
	      .settings-popout {{ position: static; width: auto; margin-top: 8px; }}
	      .stacked-form, .create-card .stacked-form, .resource-card form {{ grid-template-columns: 1fr; }}
	      .group-records {{ grid-template-columns: repeat(2, minmax(86px, 1fr)); }}
	      .group-record.text-record {{ grid-column: 1 / -1; }}
	    }}
  </style>
  <script>
    function updateTaskCreateFields() {{
      var type = document.getElementById("task-type");
      var descriptionBox = document.getElementById("domain-description");
      var description = document.getElementById("task-description");
      if (!type || !descriptionBox || !description) {{
        return;
      }}
      var showDescription = type.value === "domain_search";
      descriptionBox.hidden = !showDescription;
      description.disabled = !showDescription;
      if (!showDescription) {{
        description.value = "";
      }}
    }}
    document.addEventListener("DOMContentLoaded", function () {{
      updateTaskCreateFields();
      var type = document.getElementById("task-type");
      if (type) {{
        type.addEventListener("change", updateTaskCreateFields);
      }}
    }});
  </script>
</head>
<body>
  <header>
    <strong>{t('app_title', lang)}</strong>
    <a href="/">{t('nav_running_status', lang)}</a>
    <a href="/possible">{t('nav_possible', lang)}</a>
    <a href="/rejected">{t('nav_rejected', lang)}</a>
    <a href="/todo">{t('nav_todo', lang)}</a>
    {lang_switcher}
  </header>
  <main>{content}</main>
</body>
</html>"""


def home_page(storage: Storage, controller: RuntimeController, lang: str = "en") -> str:
    requirements = storage.list_requirements()
    task_groups = visible_task_groups(storage)
    return (
        f"<h1>{t('home_title', lang)}</h1>"
        + "<section class='top-workspace'>"
        + resource_allocation_panel(storage, lang)
        + task_create_panel(lang)
        + "</section>"
        + task_group_boards(storage, task_groups, requirements, lang)
    )


def runtime_controls(runtime: dict[str, object], lang: str = "en") -> str:
    state_raw = "Stopping" if runtime["stopping"] else "Running" if runtime["running"] else "Stopped"
    state = status_text(state_raw, lang)
    return f"""
    <section class="controlbar">
      <div>
        <strong>{t('agent_runtime', lang)}</strong>
        <div class="muted">{t('state', lang)}: {state} | {t('cycles', lang)}: {runtime["cycle_count"]} | {t('input', lang)}: {html.escape(str(runtime["input_dir"]))}</div>
        <div class="muted">{t('last_result', lang)}: {html.escape(json.dumps(runtime.get("last_result")))}</div>
      </div>
      <div class="actions">
        <form action="/runtime"><input type="hidden" name="action" value="start"><button class="button">{t('start', lang)}</button></form>
        <form action="/runtime"><input type="hidden" name="action" value="stop"><button class="button stop">{t('stop', lang)}</button></form>
        <form action="/runtime"><input type="hidden" name="action" value="run-once"><button class="button secondary">{t('run_once', lang)}</button></form>
      </div>
    </section>
    """


def resource_allocation_panel(storage: Storage, lang: str = "en") -> str:
    resources = storage.get_resource_config()
    running_search = len(storage.list_task_groups([TaskGroupStatus.RUNNING.value]))
    queue = storage.list_queue()
    researching = len([item for item in storage.list_requirements() if item.status == RequirementStatus.RESEARCHING])
    return f"""
    <section class="toolbar-card resource-card">
      <div class="toolbar-head">
        <div class="toolbar-title">{t('global_resource_allocation', lang)}</div>
        <div class="toolbar-summary">{t('search_slots', lang)} {running_search}/{resources["max_search_agents"]} · {t('deep_research_slots', lang)} {researching}/{resources["max_deep_research_agents"]} · {t('queue', lang)} {len(queue)}</div>
      </div>
      <form action="/resources">
        <label class="compact-field">{t('search_label', lang)}<input type="number" min="0" name="max_search_agents" value="{resources["max_search_agents"]}"></label>
        <label class="compact-field">{t('deep_label', lang)}<input type="number" min="0" name="max_deep_research_agents" value="{resources["max_deep_research_agents"]}"></label>
        <label class="compact-field">{t('report_label', lang)}<input type="number" min="0" name="max_report_agents" value="{resources["max_report_agents"]}"></label>
        <button class="button secondary">{t('save_limits', lang)}</button>
      </form>
    </section>
    """


def discovery_agent_card(storage: Storage, task_group: object, lang: str = "en") -> str:
    if task_group.status != TaskGroupStatus.RUNNING:
        return f"<p class='muted'>{t('no_active_discovery', lang)}</p>"
    latest = latest_task_group_activity(storage, task_group, lang)
    latest_plan = latest_search_plan(storage, task_group)
    if latest_plan:
        assignments = latest_plan["assignments"]
    else:
        assignments = planner_waiting_assignments(search_agent_count_for_group(storage, task_group))
    return "".join(
        f"""
        <a class="item" href="/agent-log?role=discovery&agent_id={html.escape(str(assignment['agent_id']))}&ref={html.escape(task_group.task_group_id)}">
          <div class="title">{html.escape(str(assignment['agent_id']).replace('-', ' ').title())}</div>
          <div><span class="status running">{html.escape(status_text('running', lang))}</span></div>
          <div class="summary">{t('strategy', lang)}: {html.escape(str(assignment.get('strategy', 'search')))}</div>
          <div class="summary">{t('query', lang)}: {html.escape(str(assignment.get('query', '')))}</div>
          <div class="summary">{html.escape(latest)}</div>
        </a>
        """
        for assignment in assignments
    )


def search_agent_count_for_group(storage: Storage, task_group: object) -> int:
    running_groups = storage.list_task_groups([TaskGroupStatus.RUNNING.value])
    allocations = allocate_search_slots_for_dashboard(len(running_groups), int(storage.get_resource_config().get("max_search_agents", 3)))
    for index, running_group in enumerate(running_groups):
        if running_group.task_group_id == task_group.task_group_id:
            return allocations[index]
    return 1


def allocate_search_slots_for_dashboard(group_count: int, max_search_agents: int) -> list[int]:
    if group_count <= 0:
        return []
    allocations = [1 for _ in range(group_count)]
    remaining = max(max_search_agents - group_count, 0)
    index = 0
    while remaining > 0:
        allocations[index % group_count] += 1
        remaining -= 1
        index += 1
    return allocations


def deep_research_agent_cards(storage: Storage, task_group: object, requirements: list[object], lang: str = "en") -> str:
    active_rows = active_deep_research_rows(storage, requirements)
    queue_rows = task_group_queue_rows(storage, task_group, requirements)
    if not active_rows and not queue_rows:
        return f"<p class='muted'>{t('no_active_deep_research', lang)}</p>"
    max_agents = max(int(storage.get_resource_config().get("max_deep_research_agents", 1)), 0)
    if max_agents == 0:
        return f"<p class='muted'>{t('deep_research_disabled', lang)}</p>"
    queue_rows = sorted(queue_rows, key=lambda row: -int(row.get("priority", 0)))
    rows = active_rows + queue_rows
    return "".join(
        deep_research_agent_card(row, index, active_slot=True, lang=lang)
        for index, row in enumerate(rows[:max_agents], start=1)
    )


def deep_research_agent_card(row: dict[str, object], index: int, active_slot: bool = False, lang: str = "en") -> str:
    running = bool(row.get("locked_by"))
    status = status_text("running", lang) if running else status_text("queued", lang)
    agent = str(row.get("locked_by") or row.get("assigned_agent") or f"research-agent-{index}")
    summary = t("researching_now", lang) if row.get("locked_by") else t("assigned_to_slot", lang)
    return f"""
    <a class="item" href="/agent-log?role=deep_research&ref={html.escape(str(row['requirement_id']))}">
      <div class="title">{t('deep_research_agent', lang)} {index}</div>
      <div><span class="status{' running' if running else ''}">{html.escape(status)}</span></div>
      <div class="summary">{html.escape(agent)} | {html.escape(summary)}</div>
      <div class="summary">{t('requirement_label', lang)}: {html.escape(str(row['requirement_id']))}</div>
    </a>
    """


def task_group_boards(storage: Storage, task_groups: list[object], requirements: list[object], lang: str = "en") -> str:
    if not task_groups:
        return f"<section class='card'><p class='muted'>{t('no_task_group', lang)}</p></section>"
    return "".join(task_group_board(storage, task_group, requirements, lang) for task_group in task_groups)


def task_group_board(storage: Storage, task_group: object, requirements: list[object], lang: str = "en") -> str:
    group_requirements = [item for item in requirements if task_group.task_group_id in item.task_group_ids]
    waiting = [item for item in group_requirements if item.status in waiting_statuses()]
    return (
        f"<section class=\"task-group-box\" id=\"{html.escape(task_group_anchor(task_group.task_group_id))}\">"
        + task_group_header(storage, task_group, group_requirements, lang)
        + "<div class='task-group-body'>"
        + task_group_diagnostic(storage, task_group, lang)
        + task_group_record_summary(storage, task_group, group_requirements, lang)
        + "<section class='workbench'>"
        + task_group_search_panel(storage, task_group, lang)
        + waiting_requirements_panel(waiting, lang)
        + task_group_deep_research_panel(storage, task_group, group_requirements, lang)
        + "</section></div></section>"
    )


def task_group_header(storage: Storage, task_group: object, requirements: list[object], lang: str = "en") -> str:
    status_class = " running" if task_group.status == TaskGroupStatus.RUNNING else ""
    running = task_group.status == TaskGroupStatus.RUNNING
    latest = latest_task_group_activity(storage, task_group, lang)
    run_indicator = f"<span class='run-indicator'><span class='run-dot'></span>{t('running', lang)}</span>" if running else ""
    motion = "<div class='pipeline-motion'><span></span></div>" if running else ""
    return f"""
    <div class="task-group-header">
      <div>
        <h2>{html.escape(task_group.name)}</h2>
        <div class="summary"><span class="status{status_class}">{html.escape(status_text(task_group.status.value, lang))}</span>{run_indicator} {html.escape(status_text(task_group.task_type.value, lang))} | {len(requirements)} {t('requirement_count', lang)}</div>
        <div class="summary">{html.escape(task_group.description or t('no_search_description', lang))}</div>
        <div class="latest-activity">{t('latest', lang)}: {html.escape(latest)}</div>
        {motion}
      </div>
      <div class="group-actions">
        <form action="/task"><input type="hidden" name="action" value="start"><input type="hidden" name="id" value="{html.escape(task_group.task_group_id)}"><button class="button">{t('start', lang)}</button></form>
        <form action="/task"><input type="hidden" name="action" value="stop"><input type="hidden" name="id" value="{html.escape(task_group.task_group_id)}"><button class="button stop">{t('stop', lang)}</button></form>
        <form action="/task"><input type="hidden" name="action" value="delete"><input type="hidden" name="id" value="{html.escape(task_group.task_group_id)}"><button class="button danger">{t('delete', lang)}</button></form>
        {group_settings_inline(storage, task_group, lang)}
        <a class="button link-button secondary" href="/experiment-log?task_group_id={html.escape(task_group.task_group_id)}">{t('details', lang)}</a>
        <a href="/possible?task_group_id={html.escape(task_group.task_group_id)}">{t('possible', lang)}</a>
        <a href="/rejected?task_group_id={html.escape(task_group.task_group_id)}">{t('rejected', lang)}</a>
      </div>
    </div>
    """


def task_group_record_summary(storage: Storage, task_group: object, requirements: list[object], lang: str = "en") -> str:
    requirement_ids = {item.requirement_id for item in requirements}
    queue = [row for row in storage.list_queue() if row["requirement_id"] in requirement_ids]
    generated_possible = [
        item
        for item in requirements
        if item.status
        in {
            RequirementStatus.NEEDS_MORE_EVIDENCE,
            RequirementStatus.QUEUED_FOR_RESEARCH,
            RequirementStatus.RESEARCHING,
            RequirementStatus.REOPENED,
            RequirementStatus.VALIDATED,
            RequirementStatus.WATCHING,
        }
    ]
    accepted = [
        item
        for item in requirements
        if item.status in {RequirementStatus.VALIDATED, RequirementStatus.WATCHING}
        and storage.list_research_runs(item.requirement_id)
    ]
    researching = [item for item in requirements if item.status == RequirementStatus.RESEARCHING]
    rejected = [
        item
        for item in requirements
        if item.status in {RequirementStatus.REJECTED, RequirementStatus.ARCHIVED}
        and storage.list_research_runs(item.requirement_id)
    ]
    runs = storage.list_task_group_runs(task_group.task_group_id, limit=1)
    last_run = runs[0].completed_at or runs[0].started_at if runs else t("never", lang)
    last_summary = runs[0].summary if runs else t("no_cycle_completed", lang)
    metric_records = [
        (t("generated", lang), len(generated_possible)),
        (t("queued", lang), len(queue)),
        (t("researching", lang), len(researching)),
        (t("accepted", lang), len(accepted)),
        (t("rejected", lang), len(rejected)),
    ]
    text_records = [
        (t("last_run", lang), last_run),
        (t("last_cycle", lang), last_summary),
    ]
    return (
        "<section class='group-records'>"
        + "".join(
            f"<div class='group-record metric-record'><div class='muted'>{html.escape(label)}</div><div class='group-record-value'>{html.escape(str(value))}</div></div>"
            for label, value in metric_records
        )
        + "".join(
            f"<div class='group-record text-record'><div class='muted'>{html.escape(label)}</div><div class='group-record-value'>{html.escape(str(value))}</div></div>"
            for label, value in text_records
        )
        + "</section>"
    )


def task_group_diagnostic(storage: Storage, task_group: object, lang: str = "en") -> str:
    config = storage.get_task_group_config(task_group.task_group_id)
    recent = storage.list_experiment_logs(task_group_id=task_group.task_group_id, limit=20)
    latest_input = next((item for item in recent if item["step_name"] == "input_loaded"), None)
    latest_files = next((item for item in recent if item["step_name"] == "files_read"), None)
    latest_collector_failed = next((item for item in recent if item["step_name"] == "collector_failed"), None)
    latest_collector_skipped = next((item for item in recent if item["step_name"] == "collector_skipped"), None)
    if latest_collector_failed:
        return f"<section class='notice warning'><strong>{t('collector_failed', lang)}</strong> {html.escape(latest_collector_failed['message'])}</section>"
    if latest_input and int(latest_input["payload_json"].get("items_loaded", 0)) == 0:
        file_count = len(latest_files["payload_json"].get("files", [])) if latest_files else 0
        if latest_collector_skipped or config.get("collector_enabled") != "1":
            return (
                f"<section class='notice warning'><strong>{t('no_input_collected', lang)}</strong> "
                f"{t('opencli_disabled', lang)} {html.escape(task_group.input_dir)} {t('has_file', lang)} {file_count} {t('json_files', lang)}</section>"
            )
        return (
            f"<section class='notice warning'><strong>{t('no_input_loaded', lang)}</strong> "
            f"{html.escape(task_group.input_dir)} {t('has_file_but_loaded', lang)} {file_count} {t('items_loaded_zero', lang)}</section>"
        )
    return ""


def latest_task_group_activity(storage: Storage, task_group: object, lang: str = "en") -> str:
    logs = storage.list_experiment_logs(task_group_id=task_group.task_group_id, limit=1)
    if logs:
        return str(logs[0]["message"])
    if task_group.status == TaskGroupStatus.RUNNING:
        return t("waiting_first_cycle", lang)
    return t("no_run_yet", lang)


def opencli_install_hint(lang: str = "en") -> str:
    return f"<div class='summary'>{t('opencli_hint', lang)}</div>"


def task_group_search_panel(storage: Storage, task_group: object, lang: str = "en") -> str:
    return (
        f"<section class='panel'><h2>{t('search_planner', lang)}</h2>"
        + search_planner_card(storage, task_group, lang)
        + f"<h2>{t('discovery_agents', lang)}</h2>"
        + discovery_agent_card(storage, task_group, lang)
        + "</section>"
    )


def latest_search_plan(storage: Storage, task_group: object) -> dict[str, object] | None:
    plans = storage.list_search_plans(task_group.task_group_id, limit=1)
    return plans[0] if plans else None


def search_planner_card(storage: Storage, task_group: object, lang: str = "en") -> str:
    plan = latest_search_plan(storage, task_group)
    if plan:
        assignments = plan["assignments"]
        rows = "".join(
            f"<li><strong>{html.escape(str(item.get('strategy', 'search')))}:</strong> {html.escape(str(item.get('query', '')))}</li>"
            for item in assignments
        )
        return f"""
        <a class="item" href="/agent-log?role=search_planner&ref={html.escape(task_group.task_group_id)}">
          <div class="title">{t('search_planner_agent', lang)}</div>
          <div><span class="status">{t('cycle', lang)} {html.escape(str(plan['cycle_index']))}</span></div>
          <div class="summary">{html.escape(str(plan['search_goal']))}</div>
          <ul class="url-list">{rows}</ul>
        </a>
        """
    preview = planner_waiting_preview(task_group, search_agent_count_for_group(storage, task_group))
    rows = "".join(
        f"<li><strong>{html.escape(str(item.get('strategy', 'search')))}:</strong> {html.escape(str(item.get('query', '')))}</li>"
        for item in preview["assignments"]
    )
    return f"""
    <div class="item">
      <div class="title">{t('search_planner_agent', lang)}</div>
      <div><span class="status">{t('preview', lang)}</span></div>
      <div class="summary">{html.escape(str(preview['search_goal']))}</div>
      <ul class="url-list">{rows}</ul>
    </div>
    """


def planner_waiting_preview(task_group: object, search_agent_count: int) -> dict[str, object]:
    return {
        "search_goal": "等待后台 AI 搜索规划器理解用户需求并生成搜索计划。",
        "assignments": planner_waiting_assignments(search_agent_count),
    }


def planner_waiting_assignments(search_agent_count: int) -> list[dict[str, str]]:
    return [
        {
            "agent_id": f"search-agent-{index}",
            "strategy": "waiting_for_ai_planner",
            "query": "等待 AI 生成搜索查询",
        }
        for index in range(1, max(search_agent_count, 1) + 1)
    ]


def ai_activity_panel(runtime: dict[str, object], task_groups: list[object], storage: Storage, lang: str = "en") -> str:
    running = bool(runtime.get("running"))
    events = runtime.get("events", [])
    event_rows = "".join(
        f"<li>{html.escape(str(item.get('at', '')))} - {html.escape(str(item.get('event', '')))} {html.escape(json.dumps(item.get('detail', {}), ensure_ascii=False, default=str))}</li>"
        for item in list(events)[:3]
        if isinstance(item, dict)
    )
    latest_logs = []
    for task_group in task_groups[:4]:
        logs = storage.list_experiment_logs(task_group_id=task_group.task_group_id, limit=1)
        if logs:
            latest_logs.append(f"{task_group.name}: {logs[0]['message']}")
    log_rows = "".join(f"<li>{html.escape(item)}</li>" for item in latest_logs)
    status = "AI 正在后台运行。刷新页面可查看最新阶段。" if running else "AI 后台当前未运行。点击任务组 Start 后会开始后台规划、采集和分析。"
    return f"""
    <section class="notice">
      <strong>AI 运行状态</strong>
      <div class="summary">{status}</div>
      <ul class="url-list">{event_rows}{log_rows}</ul>
    </section>
    """


def waiting_requirements_panel(requirements: list[object], lang: str = "en") -> str:
    items = "".join(requirement_card(item, lang) for item in requirements[:12]) or f"<p class='muted'>{t('no_found_waiting', lang)}</p>"
    return (
        f"<section class='panel'><h2>{t('waiting_for_deep_research', lang)}</h2>"
        f"<p class='muted'>{t('discovery_lightweight', lang)}</p>"
        + items
        + "</section>"
    )


def deep_research_agents_panel(storage: Storage, lang: str = "en") -> str:
    active_queue = active_deep_research_rows(storage, storage.list_requirements())
    items = "".join(
        f"""
        <a class="item" href="/agent-log?role=deep_research&ref={html.escape(str(row['requirement_id']))}">
          <div class="title">{t('deep_research_agent', lang)}</div>
          <div><span class="status running">{html.escape(status_text('running', lang))}</span></div>
          <div class="summary">{html.escape(str(row.get('locked_by') or row.get('assigned_agent') or 'assigned'))}</div>
          <div class="summary">{t('requirement_label', lang)}: {html.escape(str(row['requirement_id']))}</div>
        </a>
        """
        for row in active_queue
    ) or f"<p class='muted'>{t('no_active_deep_research_agent', lang)}</p>"
    return (
        f"<section class='panel'><h2>{t('running_deep_research_agents', lang)}</h2>"
        f"<p class='muted'>{t('deep_research_consume', lang)}</p>"
        + items
        + "</section>"
    )


def task_group_deep_research_panel(storage: Storage, task_group: object, requirements: list[object], lang: str = "en") -> str:
    queue = task_group_queue_rows(storage, task_group, requirements)
    max_agents = max(int(storage.get_resource_config().get("max_deep_research_agents", 1)), 0)
    active_count = len(active_deep_research_rows(storage, requirements))
    backlog_count = len(queue)
    if task_group.status != TaskGroupStatus.RUNNING:
        paused = f"<div class='summary'>{len(queue)} {t('queued_paused', lang)}</div>" if queue else ""
        return (
            f"<section class='panel'><h2>{t('running_deep_research_agents', lang)}</h2>"
            f"<p class='muted'>{t('no_active_deep_research', lang)}</p>"
            + paused
            + "</section>"
        )
    queue_text = "".join(
        f"<div class='summary'>{t('next_requirement', lang)}: {html.escape(row['requirement_id'])} {t('priority', lang)} {row['priority']}</div>"
        for row in queue[:max_agents]
    ) or f"<p class='muted'>{t('no_deep_research_queue', lang)}</p>"
    backlog = f"<div class='summary'>{backlog_count} {t('waiting_behind_slots', lang)}</div>" if backlog_count else ""
    active_label = "该组活跃槽位" if lang == "zh" else "active slots for this group"
    active = f"<div class='summary'>{active_count}/{max_agents} {active_label}</div>"
    return (
        f"<section class='panel'><h2>{t('running_deep_research_agents', lang)}</h2>"
        f"<p class='muted'>{t('deep_research_dynamic', lang)}</p>"
        + active
        + deep_research_agent_cards(storage, task_group, requirements, lang)
        + queue_text
        + backlog
        + "</section>"
    )


def active_deep_research_rows(storage: Storage, requirements: list[object]) -> list[dict[str, object]]:
    return [
        {
            "requirement_id": item.requirement_id,
            "assigned_agent": item.assigned_to or "deep_research",
            "locked_by": item.assigned_to or "deep_research",
            "priority": "",
            "reason": "active deep research",
        }
        for item in requirements
        if item.status == RequirementStatus.RESEARCHING
    ]


def task_group_queue_rows(storage: Storage, task_group: object, requirements: list[object]) -> list[dict[str, object]]:
    requirement_ids = {item.requirement_id for item in requirements}
    rows = []
    for row in storage.list_queue():
        if row.get("task_group_id") == task_group.task_group_id:
            rows.append(row)
        elif not row.get("task_group_id") and row["requirement_id"] in requirement_ids:
            rows.append(row)
    return rows


def group_settings_inline(storage: Storage, task_group: object, lang: str = "en") -> str:
    config = storage.get_task_group_config(task_group.task_group_id)
    checked = " checked" if config.get("collector_enabled") == "1" else ""
    return f"""
    <details class="inline-settings" id="settings-{html.escape(task_group.task_group_id)}">
      <summary class="button link-button secondary">{t('settings', lang)}</summary>
      <section class="card settings-popout">
      <h3>{html.escape(task_group.name)} {t('settings_title', lang)}</h3>
      <form action="/group-settings" class="actions settings-form">
        <input type="hidden" name="action" value="save">
        <input type="hidden" name="id" value="{html.escape(task_group.task_group_id)}">
        <label>
          <input class="hidden-check" type="checkbox" name="collector_enabled" value="1"{checked}>
          <span class="button toggle-button">{t('opencli_collection', lang)}</span>
        </label>
        {opencli_install_hint(lang)}
        <label>{t('results_per_run', lang)} <input type="number" min="1" name="collector_limit" value="{html.escape(config["collector_limit"])}"></label>
        <label>{t('default_model', lang)} {model_select("model_search", config["model_search"])}</label>
        <label>{t('deep_research_model', lang)} {model_select("model_deep_research", config["model_deep_research"])}</label>
        <details>
          <summary>{t('advanced', lang)}</summary>
          <div class="actions">
            <label>{t('command', lang)} <input name="collector_command" value="{html.escape(config["collector_command"])}"></label>
            <label>{t('timeout', lang)} <input type="number" min="1" name="collector_timeout_seconds" value="{html.escape(config["collector_timeout_seconds"])}"></label>
            <label>{t('memory_model', lang)} {model_select("model_pool", config["model_pool"])}</label>
            <label>{t('report_model', lang)} {model_select("model_report", config["model_report"])}</label>
          </div>
        </details>
        <button class="button">{t('save_settings', lang)}</button>
      </form>
      </section>
    </details>
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


def pipeline_history_compact(pipelines: list[dict[str, object]], lang: str = "en") -> str:
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
        items = f"<tr><td colspan='4' class='muted'>{t('no_pipeline_snapshot', lang)}</td></tr>"
    return (
        f"<h2>{t('saved_pipeline_snapshots', lang)}</h2>"
        f"<p class='muted'>{t('pipeline_snapshot_description', lang)}</p>"
        f"<table><thead><tr><th>{t('pipeline', lang)}</th><th>{t('status', lang)}</th><th>{t('summary', lang)}</th><th>{t('completed', lang)}</th></tr></thead><tbody>"
        + items
        + "</tbody></table>"
    )


def possible_requirements(storage: Storage) -> list[object]:
    return storage.list_requirements_with_research_runs(
        [RequirementStatus.VALIDATED.value, RequirementStatus.WATCHING.value]
    )


def rejected_requirements(storage: Storage) -> list[object]:
    return storage.list_requirements_with_research_runs(
        [RequirementStatus.REJECTED.value, RequirementStatus.ARCHIVED.value]
    )


def requirement_list_page(
    storage: Storage,
    title: str,
    requirements: list[object],
    selected_task_group_id: str,
    page_path: str,
    page: int = 1,
    lang: str = "en",
) -> str:
    per_page = 50
    total = len(requirements)
    total_pages = max((total + per_page - 1) // per_page, 1)
    page = min(max(page, 1), total_pages)
    start = (page - 1) * per_page
    paged_requirements = requirements[start : start + per_page]
    return (
        f"<h1>{html.escape(title)}</h1>"
        + task_group_filter(storage, selected_task_group_id, page_path, lang)
        + pagination_controls(page_path, selected_task_group_id, page, per_page, total, lang)
        + f"<p class='muted'>{t('each_row_preserves', lang)}</p>"
        + grouped_requirement_lineage(storage, paged_requirements, selected_task_group_id, lang=lang)
        + pagination_controls(page_path, selected_task_group_id, page, per_page, total, lang)
    )


def todo_page(storage: Storage, lang: str = "en") -> str:
    rows = []
    for job in storage.list_todo_jobs():
        requirement = storage.get_requirement(str(job["requirement_id"]))
        score = requirement.current_scores.get("overall_score", 0) if requirement else 0
        task_groups = task_group_labels(storage, requirement, lang) if requirement else t("task_group_unknown", lang)
        action = "done" if job["status"] == "open" else "open"
        action_label = t("mark_done", lang) if action == "done" else t("reopen", lang)
        rows.append(
            f"""
            <tr>
              <td>
                <a href="/requirement?id={html.escape(str(job['requirement_id']))}"><strong>{html.escape(str(job['title']))}</strong></a>
                <div class="summary">{html.escape(task_groups)}</div>
                <div class="summary">{t('requirement_label', lang)}: {html.escape(str(job['requirement_id']))} | {t('score', lang)} {html.escape(str(score))}</div>
              </td>
              <td><span class="status">{html.escape(str(job['status']))}</span><div class="summary">{t('from', lang)} {html.escape(str(job['source_status']))}</div></td>
              <td>{html.escape(str(job['note']))}</td>
              <td>{html.escape(str(job['created_at']))}<div class="summary">{t('updated', lang)} {html.escape(str(job['updated_at']))}</div></td>
              <td><a class="agent-chip" href="/todo-action?action={action}&id={html.escape(str(job['requirement_id']))}">{action_label}</a></td>
            </tr>
            """
        )
    body = "".join(rows) or f"<tr><td colspan='5' class='muted'>{t('no_todo_jobs', lang)}</td></tr>"
    return (
        f"<h1>{t('todo_jobs_title', lang)}</h1>"
        f"<p class='muted'>{t('todo_description', lang)}</p>"
        f"<table><thead><tr><th>{t('requirement_label', lang)}</th><th>{t('status', lang)}</th><th>{t('note', lang)}</th><th>{t('created', lang)}</th><th>{t('action', lang)}</th></tr></thead><tbody>"
        + body
        + "</tbody></table>"
    )


def grouped_requirement_lineage(storage: Storage, requirements: list[object], selected_task_group_id: str = "", lang: str = "en") -> str:
    task_groups = lineage_task_groups(storage, requirements)
    if selected_task_group_id == "__ungrouped__":
        return f"<h2>{t('ungrouped_legacy', lang)}</h2>" + requirement_lineage_table(storage, [item for item in requirements if not item.task_group_ids], lang)
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
            f"<p class='muted'>{status_text(task_group.task_type.value, lang)} | {html.escape(task_group.domain or t('general', lang))} | {t('input', lang)} {html.escape(task_group.input_dir)}</p>"
            + requirement_lineage_table(storage, group_requirements, lang)
        )
    ungrouped = [item for item in requirements if item.requirement_id not in used_ids and not item.task_group_ids]
    if selected_task_group_id == "__ungrouped__" and ungrouped:
        sections.append(f"<h2>{t('ungrouped_legacy', lang)}</h2>" + requirement_lineage_table(storage, ungrouped, lang))
    return "".join(sections) if sections else requirement_lineage_table(storage, [], lang)


def task_group_filter(storage: Storage, selected_task_group_id: str, page_path: str, lang: str = "en") -> str:
    options = [f"<option value=''>{t('all_groups', lang)}</option>"]
    for task_group in lineage_task_groups(storage, storage.list_requirements()):
        selected = " selected" if task_group.task_group_id == selected_task_group_id else ""
        label = f"{task_group.name} ({status_text(task_group.status.value, lang)})"
        options.append(f"<option value='{html.escape(task_group.task_group_id)}'{selected}>{html.escape(label)}</option>")
    selected = " selected" if selected_task_group_id == "__ungrouped__" else ""
    options.append(f"<option value='__ungrouped__'{selected}>{t('ungrouped_legacy', lang)}</option>")
    return (
        "<form class='controlbar' method='get' action='" + html.escape(page_path) + "'>"
        f"<strong>{t('task_group', lang)}</strong>"
        "<select name='task_group_id' onchange='this.form.submit()'>" + "".join(options) + "</select>"
        f"<button class='button secondary'>{t('show_group', lang)}</button>"
        "</form>"
    )


def pagination_controls(
    page_path: str,
    selected_task_group_id: str,
    page: int,
    per_page: int,
    total: int,
    lang: str = "en",
) -> str:
    if total <= per_page:
        return f"<p class='muted'>{t('total_items', lang)} {total}</p>"
    total_pages = max((total + per_page - 1) // per_page, 1)
    page = min(max(page, 1), total_pages)
    query_prefix = f"{page_path}?task_group_id={quote(selected_task_group_id)}&page="
    previous_link = (
        f"<a class='button secondary' href='{query_prefix}{page - 1}'>{t('previous_page', lang)}</a>"
        if page > 1
        else f"<span class='button secondary muted'>{t('previous_page', lang)}</span>"
    )
    next_link = (
        f"<a class='button secondary' href='{query_prefix}{page + 1}'>{t('next_page', lang)}</a>"
        if page < total_pages
        else f"<span class='button secondary muted'>{t('next_page', lang)}</span>"
    )
    return (
        "<div class='controlbar'>"
        f"{previous_link}"
        f"<strong>{t('page_label', lang)} {page} / {total_pages}</strong>"
        f"<span class='muted'>{t('total_items', lang)} {total}</span>"
        f"{next_link}"
        "</div>"
    )


def requirement_lineage_table(storage: Storage, requirements: list[object], lang: str = "en") -> str:
    rows = "".join(requirement_lineage_row(storage, item, lang) for item in requirements)
    if not rows:
        rows = f"<tr><td colspan='7' class='muted'>{t('no_requirements_page', lang)}</td></tr>"
    return (
        f"<table class='lineage'><thead><tr>"
        f"<th>{t('requirement_label', lang)}</th><th>{t('search_agents', lang)}</th><th>{t('queue_pool', lang)}</th><th>{t('deep_research_agents_col', lang)}</th><th>{t('conclusion', lang)}</th><th>{t('saved_line', lang)}</th><th>{t('todo_col', lang)}</th>"
        "</tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )


def requirement_lineage_row(storage: Storage, requirement: object, lang: str = "en") -> str:
    runs = storage.list_research_runs(requirement.requirement_id)
    latest_run = runs[0] if runs else None
    conclusion = latest_run.recommendation if latest_run else requirement.latest_recommendation or t("waiting_for_conclusion", lang)
    rejection_reason = rejection_summary_html(requirement, latest_run, lang)
    search_agents = agent_links_for_requirement(storage, requirement, ["discovery"])
    pool_agents = agent_links_for_requirement(storage, requirement, ["requirement_memory", "pool_manager", "change_detection"])
    deep_agents = agent_links_for_requirement(storage, requirement, ["deep_research", "report"])
    saved_line = f"<span class='muted'>{t('details', lang)}</span>"
    todo = todo_action_for_requirement(storage, requirement, lang)
    status_class = " rejected" if requirement.status == RequirementStatus.REJECTED else ""
    return f"""
    <tr>
      <td>
        <a href="/requirement?id={html.escape(requirement.requirement_id)}"><strong>{html.escape(requirement.canonical_requirement)}</strong></a>
        <div class="summary"><span class="status{status_class}">{status_text(requirement.status.value, lang)}</span> {t('score', lang)} {requirement.current_scores.get("overall_score", 0)}</div>
        <div class="summary">{task_group_labels(storage, requirement, lang)}</div>
        <div class="summary">{t('evidence', lang)} {requirement.evidence_count} | {t('subreddits', lang)} {requirement.subreddit_count}</div>
      </td>
      <td>{search_agents}</td>
      <td>{pool_agents}<div class="summary">{t('times_detected', lang)} {requirement.times_detected}</div></td>
      <td>{deep_agents}<div class="summary">{len(runs)} {t('research_runs', lang)}</div></td>
      <td>{html.escape(conclusion)}{rejection_reason}</td>
      <td>{saved_line}</td>
      <td>{todo}</td>
    </tr>
    """


def rejection_summary_text(requirement: object, latest_run: object | None, lang: str = "en") -> str:
    if requirement.status != RequirementStatus.REJECTED:
        return ""
    summary = ""
    if latest_run:
        summary = str(
            latest_run.findings.get("rejection_summary")
            or latest_run.findings.get("why_noise")
            or latest_run.findings.get("realness_reason")
            or ""
        )
    if not summary:
        summary = str(requirement.latest_recommendation or t("rejected_default", lang))
    if not summary.lower().startswith("rejected"):
        summary = f"{t('rejected_because', lang)} {summary.rstrip('.')}."
    return summary


def rejection_summary_html(requirement: object, latest_run: object | None, lang: str = "en") -> str:
    summary = rejection_summary_text(requirement, latest_run, lang)
    if not summary:
        return ""
    return f"<div class='summary'><strong>{t('reason', lang)}:</strong> {html.escape(summary)}</div>"


def todo_action_for_requirement(storage: Storage, requirement: object, lang: str = "en") -> str:
    todo = storage.get_todo_job(requirement.requirement_id)
    if todo:
        return (
            f"<a class='agent-chip' href='/todo'>{t('todo_status', lang)}: {html.escape(str(todo['status']))}</a>"
            f"<div class='summary'>{html.escape(str(todo['updated_at']))}</div>"
        )
    return f"<a class='agent-chip' href='/todo-action?action=add&id={html.escape(requirement.requirement_id)}'>{t('move_to_todo', lang)}</a>"


def task_create_panel(lang: str = "en") -> str:
    return f"""
    <section class="toolbar-card create-card">
      <div class="toolbar-head">
        <div class="toolbar-title">{t('create_task_group', lang)}</div>
        <div class="toolbar-summary">{t('general_search', lang)} / {t('domain_specific', lang)}</div>
      </div>
      <form action="/task" class="stacked-form">
        <input type="hidden" name="action" value="create">
        <label class="compact-field">{t('task_group', lang)}<select id="task-type" name="type" aria-label="Task group type">
          <option value="general_search">{t('general_search', lang)}</option>
          <option value="domain_search">{t('domain_specific', lang)}</option>
        </select></label>
        <label class="compact-field">{t('group_name_placeholder', lang)}<input name="name" placeholder="{t('group_name_placeholder', lang)}"></label>
        <span id="domain-description" class="domain-description" hidden>
          <label class="compact-field">{t('domain_search_plan_placeholder', lang)}<textarea id="task-description" name="description" placeholder="{t('domain_search_plan_placeholder', lang)}" disabled></textarea></label>
        </span>
        <button class="button">{t('create', lang)}</button>
      </form>
    </section>
    """


def task_group_card(task: object, lang: str = "en") -> str:
    status_class = " running" if task.status == TaskGroupStatus.RUNNING else ""
    return f"""
    <div class="item">
      <div class="title">{html.escape(task.name)}</div>
      <div><span class="status{status_class}">{status_text(task.status.value, lang)}</span> {status_text(task.task_type.value, lang)}</div>
      <div class="summary">{html.escape(task.description or t('no_search_description', lang))}</div>
      <div class="summary">{t('input', lang)}: {html.escape(task.input_dir)}</div>
      <div class="actions">
        <form action="/task"><input type="hidden" name="action" value="start"><input type="hidden" name="id" value="{html.escape(task.task_group_id)}"><button class="button">{t('start', lang)}</button></form>
        <form action="/task"><input type="hidden" name="action" value="stop"><input type="hidden" name="id" value="{html.escape(task.task_group_id)}"><button class="button stop">{t('stop', lang)}</button></form>
        <form action="/task"><input type="hidden" name="action" value="delete"><input type="hidden" name="id" value="{html.escape(task.task_group_id)}"><button class="button danger">{t('delete', lang)}</button></form>
      </div>
      <div class="linkbar">
        <a href="/experiment-log?task_group_id={html.escape(task.task_group_id)}">{t('details', lang)}</a>
      </div>
    </div>
    """


def task_group_labels(storage: Storage, requirement: object, lang: str = "en") -> str:
    labels = []
    for task_group_id in requirement.task_group_ids:
        task = storage.get_task_group(task_group_id)
        if task:
            labels.append(f"{task.name} ({status_text(task.task_type.value, lang)})")
        else:
            labels.append(task_group_id)
    return t("task_group_label", lang) + ": " + ", ".join(labels) if labels else t("task_group_unassigned", lang)


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def waiting_statuses() -> set[RequirementStatus]:
    return {
        RequirementStatus.QUEUED_FOR_RESEARCH,
        RequirementStatus.RESEARCHING,
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
        return [item for item in requirements if item.task_group_ids]
    if task_group_id == "__ungrouped__":
        return [item for item in requirements if not item.task_group_ids]
    return [item for item in requirements if task_group_id in item.task_group_ids]


def agent_links_for_requirement(storage: Storage, requirement: object, roles: list[str]) -> str:
    links = []
    for role in roles:
        href = f"/agent-log?role={html.escape(role)}&ref={html.escape(requirement.requirement_id)}"
        label = role.replace("_", " ").title()
        if role == "deep_research":
            label = f"{label} ({len(requirement.research_history)})"
        links.append(f"<a class='agent-chip' href='{href}'>{html.escape(label)}</a>")
    return "".join(links)


def related_log_count(storage: Storage, requirement: object, role: str) -> int:
    logs = storage.list_agent_logs(agent_role=role, limit=500)
    refs = {requirement.requirement_id, *requirement.research_history, *requirement.evidence_ids}
    count = 0
    for item in logs:
        log_refs = {str(ref) for ref in item["input_refs"] + item["output_refs"]}
        if refs & log_refs:
            count += 1
    if role == "deep_research":
        count += len(storage.list_research_runs(requirement.requirement_id))
        count += len([event for event in storage.list_requirement_events(requirement.requirement_id) if event["agent_role"] == "deep_research"])
        count += len(requirement_experiment_logs(storage, requirement, role))
    if count == 0 and role == "discovery" and requirement.evidence_ids:
        return len(logs)
    return count


def requirement_experiment_logs(storage: Storage, requirement: object, role: str | None = None) -> list[dict[str, object]]:
    logs_by_id: dict[int, dict[str, object]] = {}
    for task_group_id in requirement.task_group_ids:
        for item in storage.list_experiment_logs(task_group_id=task_group_id, agent_role=role, limit=500):
            payload = item["payload_json"]
            if (
                payload.get("requirement_id") == requirement.requirement_id
                or str(requirement.requirement_id) in str(item.get("message", ""))
            ):
                logs_by_id[int(item["log_id"])] = item
    return sorted(logs_by_id.values(), key=lambda item: int(item["log_id"]), reverse=True)


def latest_pipeline_for_requirement(storage: Storage, requirement_id: str, lang: str = "en") -> str:
    return f"<span class='muted'>{t('no_saved_snapshot', lang)}</span>"


def log_summary_panel(title: str, href: str, logs: list[dict[str, object]], lang: str = "en") -> str:
    items = "".join(
        f"""
        <a class="item" href="{href}">
          <div class="title">{html.escape(str(item['task_id']))}</div>
          <div><span class="status">{html.escape(str(item['status']))}</span></div>
          <div class="summary">{html.escape(str(item['agent_id']))} | {html.escape(str(item['completed_at'] or item['started_at']))}</div>
        </a>
        """
        for item in logs
    ) or f"<p class='muted'>{t('no_logs_yet', lang)}</p>"
    return f"<section class='panel'><h2>{html.escape(title)}</h2><p class='muted'>{t('click_for_full_log', lang)}</p>{items}</section>"


def requirement_card(requirement: object, lang: str = "en") -> str:
    status_class = " rejected" if requirement.status == RequirementStatus.REJECTED else ""
    score = requirement.current_scores.get("overall_score", 0)
    summary = requirement.description[:140] + ("..." if len(requirement.description) > 140 else "")
    return f"""
    <a class="item" href="/requirement?id={html.escape(requirement.requirement_id)}">
      <div class="title">{html.escape(requirement.canonical_requirement)}</div>
      <div><span class="status{status_class}">{status_text(requirement.status.value, lang)}</span> {t('score', lang)} {score}</div>
      <div class="summary">{html.escape(summary)}</div>
      <div class="summary">{t('evidence', lang)} {requirement.evidence_count} | {t('subreddits', lang)} {requirement.subreddit_count} | {t('last_seen', lang)} {html.escape(requirement.last_seen)}</div>
    </a>
    """


def queue_page(storage: Storage, lang: str = "en") -> str:
    rows = storage.list_queue()
    body = "".join(
        "<tr>"
        f"<td>{row['priority']}</td><td><a href='/requirement?id={html.escape(row['requirement_id'])}'>{html.escape(row['requirement_id'])}</a></td>"
        f"<td>{html.escape(row['reason'])}</td><td>{row['new_evidence_count']}</td><td>{html.escape(str(row['previous_research_status'] or ''))}</td>"
        f"<td>{html.escape(str(row['assigned_agent'] or ''))}</td><td>{html.escape(str(row['locked_by'] or t('unlocked', lang)))}</td>"
        f"<td>{row['estimated_cost']}</td><td>{row['expected_completion_minutes']} {t('min', lang)}</td>"
        "</tr>"
        for row in rows
    )
    return (
        f"<h1>{t('research_queue', lang)}</h1><table><thead><tr><th>{t('priority', lang)}</th><th>{t('requirement_label', lang)}</th><th>{t('reason', lang)}</th>"
        f"<th>{t('new_evidence', lang)}</th><th>{t('previous_status', lang)}</th><th>{t('assigned_agent', lang)}</th><th>{t('lock', lang)}</th><th>{t('cost', lang)}</th><th>{t('eta', lang)}</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def detail_page(storage: Storage, requirement_id: str, lang: str = "en") -> str:
    requirement = storage.get_requirement(requirement_id)
    if requirement is None:
        return f"<h1>{t('requirement_not_found', lang)}</h1>"
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
    ) or f"<tr><td colspan='4' class='muted'>{t('no_workflow_events', lang)}</td></tr>"
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
    ) or f"<tr><td colspan='4' class='muted'>{t('no_sample_recorded', lang)}</td></tr>"
    report = ""
    if latest:
        findings = latest.findings
        rejection_summary = rejection_summary_text(requirement, latest, lang)
        rejected_reason = f"<p><strong>{t('rejected_reason', lang)}:</strong> {html.escape(rejection_summary)}</p>" if rejection_summary else ""
        report = (
            f"<h2>{t('research_report', lang)}</h2>"
            f"{rejected_reason}"
            f"<p><strong>{t('why_real', lang)}:</strong> {html.escape(findings['why_real'])}</p>"
            f"<p><strong>{t('why_noise', lang)}:</strong> {html.escape(findings['why_noise'])}</p>"
            f"<p><strong>{t('recommendation', lang)}:</strong> {html.escape(latest.recommendation)}</p>"
            f"<pre>{html.escape(json.dumps(findings, indent=2))}</pre>"
        )
    return f"""
    <h1>{html.escape(requirement.canonical_requirement)}</h1>
    <p><span class="status">{status_text(requirement.status.value, lang)}</span> {t('score', lang)}: {requirement.current_scores.get('overall_score', 0)}</p>
    <div class="linkbar">
      <a href="/agent-log?role=discovery&ref={html.escape(requirement.requirement_id)}">{t('requirement_search_agent_log', lang)}</a>
      <a href="/agent-log?role=deep_research&ref={html.escape(requirement.requirement_id)}">{t('deep_research_agent_log', lang)}</a>
      <a href="/requirement-samples?requirement_id={html.escape(requirement.requirement_id)}">{t('pool_samples', lang)}</a>
      <a href="/todo-action?action=add&id={html.escape(requirement.requirement_id)}">{t('move_to_todo', lang)}</a>
      <a href="/possible">{t('back_to_possible', lang)}</a>
      <a href="/rejected">{t('back_to_rejected', lang)}</a>
    </div>
    <h2>{t('executive_summary', lang)}</h2>
    <p>
      <a href="/action?type=approve&id={html.escape(requirement.requirement_id)}">{t('approve_research', lang)}</a> |
      <a href="/action?type=pause&id={html.escape(requirement.requirement_id)}">{t('pause', lang)}</a> |
      <a href="/action?type=reject&id={html.escape(requirement.requirement_id)}">{t('reject_as_noise', lang)}</a> |
      <a href="/action?type=force-reopen&id={html.escape(requirement.requirement_id)}">{t('force_reopen', lang)}</a> |
      <a href="/action?type=priority&id={html.escape(requirement.requirement_id)}&priority=90">{t('increase_priority', lang)}</a> |
      <a href="/action?type=priority&id={html.escape(requirement.requirement_id)}&priority=25">{t('decrease_priority', lang)}</a>
    </p>
    <p>{html.escape(requirement.description)}</p>
    <h2>{t('audience_and_geography', lang)}</h2>
    <p>{t('audience', lang)}: {html.escape(', '.join(requirement.audience_segments))}</p>
    <pre>{html.escape(json.dumps(requirement.geo_distribution, indent=2))}</pre>
    <h2>{t('evidence_timeline', lang)}</h2>
    <ul>{evidence_rows}</ul>
    <h2>{t('decision_history', lang)}</h2>
    <pre>{html.escape(json.dumps(requirement.decision_history, indent=2))}</pre>
    <h2>{t('workflow_timeline', lang)}</h2>
    <table><thead><tr><th>{t('time', lang)}</th><th>{t('agent', lang)}</th><th>{t('event', lang)}</th><th>{t('message', lang)}</th></tr></thead><tbody>{event_rows}</tbody></table>
    <h2>{t('sample_sentences', lang)}</h2>
    <table><thead><tr><th>{t('time', lang)}</th><th>{t('sample', lang)}</th><th>{t('status', lang)}</th><th>{t('task_group_run', lang)}</th></tr></thead><tbody>{sample_rows}</tbody></table>
    <h2>{t('research_history', lang)}</h2>
    <table><thead><tr><th>{t('run', lang)}</th><th>{t('agent', lang)}</th><th>{t('completed', lang)}</th><th>{t('recommendation', lang)}</th></tr></thead><tbody>{run_rows}</tbody></table>
    <h2>{t('change_since_last', lang)}</h2>
    <pre>{html.escape(json.dumps(requirement.reopen_events, indent=2))}</pre>
    {report}
    """


def agent_log_page(storage: Storage, role: str, agent_id: str, ref: str = "", lang: str = "en") -> str:
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
        if requirement:
            experiment_logs = requirement_experiment_logs(storage, requirement, role or None)
        else:
            experiment_logs = storage.list_experiment_logs(task_group_id=ref, agent_role=role or None, limit=100)
        if agent_id:
            experiment_logs = [
                item
                for item in experiment_logs
                if item["payload_json"].get("agent_id") == agent_id
                or agent_id in {str(value) for value in item["payload_json"].get("agent_ids", [])}
            ]
    title = role.replace("_", " ").title() if role else agent_id or "Agent"
    terminal_entries = terminal_log_stream(logs, experiment_logs, lang=lang)
    readable_title = t("search_log", lang)
    if role == "deep_research":
        readable_title = t("deep_research_log", lang)
        readable_entries = readable_deep_research_log_sections(experiment_logs, lang=lang)
        if ref and not experiment_logs:
            readable_entries += deep_research_queue_status_block(storage, ref, lang=lang)
    else:
        readable_entries = readable_search_log_sections(experiment_logs, lang=lang)
    ref_text = f" {t('related_to', lang)} {ref}." if ref else ""
    summary = f"{len(logs)} {t('activity_log_summary', lang)}, {len(experiment_logs)} {t('experiment_log_summary', lang)}. {t('latest_status', lang)}: {logs[0]['status'] if logs else t('none', lang)}.{ref_text}"
    return f"""
    <h1>{html.escape(title)} {t('log_title_suffix', lang)}</h1>
    <p class="muted">{html.escape(summary)}</p>
    <div class="linkbar"><a href="/">{t('nav_running_status', lang)}</a><a href="/possible">{t('nav_possible', lang)}</a><a href="/rejected">{t('nav_rejected', lang)}</a></div>
    <h2>{html.escape(readable_title)}</h2>
    {readable_entries}
    <h2>{t('raw_terminal_log', lang)}</h2>
    {terminal_entries}
    """


def experiment_log_page(storage: Storage, task_group_id: str = "", task_group_run_id: str = "", agent_role: str = "", lang: str = "en") -> str:
    logs = storage.list_experiment_logs(
        task_group_id=task_group_id or None,
        task_group_run_id=task_group_run_id or None,
        agent_role=agent_role or None,
        limit=500,
    )
    title_parts = [t("experiment_logs", lang)]
    if task_group_id:
        task = storage.get_task_group(task_group_id)
        title_parts.append(task.name if task else task_group_id)
    if agent_role:
        title_parts.append(agent_role.replace("_", " ").title())
    terminal_entries = terminal_log_stream([], logs, lang=lang)
    readable_entries = readable_log_sections([], logs, lang=lang)
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
    ) or f"<tr><td colspan='5' class='muted'>{t('no_experiment_logs', lang)}</td></tr>"
    return f"""
    <h1>{html.escape(' - '.join(title_parts))}</h1>
    <div class="linkbar"><a href="/">{t('nav_running_status', lang)}</a><a href="/possible">{t('nav_possible', lang)}</a><a href="/rejected">{t('nav_rejected', lang)}</a></div>
    <h2>{t('readable_log', lang)}</h2>
    {readable_entries}
    <h2>{t('terminal_style_log', lang)}</h2>
    {terminal_entries}
    <table><thead><tr><th>{t('time', lang)}</th><th>{t('agent', lang)}</th><th>{t('step', lang)}</th><th>{t('message', lang)}</th><th>{t('payload', lang)}</th></tr></thead><tbody>{rows}</tbody></table>
    """


def terminal_log_stream(activity_logs: list[dict[str, object]], experiment_logs: list[dict[str, object]], lang: str = "en") -> str:
    entries = []
    for item in activity_logs:
        entries.append(
            {
                "time": str(item.get("completed_at") or item.get("started_at") or ""),
                "role": str(item.get("agent_role") or ""),
                "name": str(item.get("agent_id") or ""),
                "step": str(item.get("task_id") or ""),
                "message": f"status={item.get('status')}",
                "payload": {
                    "input_refs": item.get("input_refs", []),
                    "output_refs": item.get("output_refs", []),
                    "retry_count": item.get("retry_count", 0),
                    "cost_estimate": item.get("cost_estimate", 0),
                },
                "error": str(item.get("error") or ""),
            }
        )
    for item in experiment_logs:
        entries.append(
            {
                "time": str(item.get("created_at") or ""),
                "role": str(item.get("agent_role") or ""),
                "name": str(item.get("task_group_run_id") or item.get("task_group_id") or ""),
                "step": str(item.get("step_name") or ""),
                "message": str(item.get("message") or ""),
                "payload": item.get("payload_json", {}),
                "error": "",
            }
        )
    entries.sort(key=lambda item: item["time"])
    if not entries:
        return f"<div class='terminal-log'><div class='terminal-entry terminal-message'>{t('no_log_lines', lang)}</div></div>"
    return (
        "<div class='terminal-log'>"
        + "".join(terminal_log_entry(item, lang=lang) for item in entries)
        + "</div>"
    )


def readable_log_sections(activity_logs: list[dict[str, object]], experiment_logs: list[dict[str, object]], lang: str = "en") -> str:
    search_blocks = [readable_search_agent_block(item, lang=lang) for item in experiment_logs if item.get("step_name") in {"search_agent_completed", "search_agent_failed"}]
    analysis_blocks = [readable_item_analysis_block(item, lang=lang) for item in experiment_logs if item.get("step_name") in {"item_analyzed", "sample_analyzed"}]
    lifecycle_blocks = [readable_lifecycle_block(item, lang=lang) for item in experiment_logs if item.get("step_name") in {"requirements_generated", "pool_requirement_sample", "requirements_queued", "deep_research_output", "run_completed"}]
    activity_blocks = [readable_activity_block(item, lang=lang) for item in activity_logs]
    content = "".join(search_blocks + analysis_blocks + lifecycle_blocks + activity_blocks)
    if not content:
        content = f"<section class='readable-block'><p class='muted'>{t('no_readable_entries', lang)}</p></section>"
    return f"<div class='readable-log'>{content}</div>"


def readable_search_log_sections(experiment_logs: list[dict[str, object]], lang: str = "en") -> str:
    planner_entries = unique_search_plan_entries(
        item for item in experiment_logs if item.get("step_name") == "search_plan_created"
    )
    search_entries = unique_search_log_entries(
        item for item in experiment_logs if item.get("step_name") in {"search_agent_completed", "search_agent_failed"}
    )
    analysis_entries = unique_search_log_entries(
        item for item in experiment_logs if item.get("step_name") in {"item_analyzed", "sample_analyzed"}
    )
    planner_blocks = [readable_search_plan_block(item, lang=lang) for item in planner_entries]
    search_blocks = [readable_search_agent_block(item, lang=lang) for item in search_entries]
    analysis_blocks = [readable_item_analysis_block(item, lang=lang) for item in analysis_entries]
    content = "".join(planner_blocks + search_blocks + analysis_blocks)
    if not content:
        content = f"<section class='readable-block'><p class='muted'>{t('no_search_log_entries', lang)}</p></section>"
    return f"<div class='readable-log'>{content}</div>"


def readable_deep_research_log_sections(experiment_logs: list[dict[str, object]], lang: str = "en") -> str:
    started_blocks = [
        readable_deep_research_started_block(item, lang=lang)
        for item in experiment_logs
        if item.get("step_name") == "deep_research_started"
    ]
    plan_blocks = [
        readable_deep_research_plan_block(item, lang=lang)
        for item in experiment_logs
        if item.get("step_name") == "deep_research_plan_created"
    ]
    search_blocks = [
        readable_deep_research_search_block(item, lang=lang)
        for item in experiment_logs
        if item.get("step_name") in {"deep_research_search_started", "deep_research_search_completed", "deep_research_search_failed"}
    ]
    analysis_blocks = [
        readable_deep_research_item_block(item, lang=lang)
        for item in experiment_logs
        if item.get("step_name") == "deep_research_item_analyzed"
    ]
    evidence_blocks = [
        readable_deep_research_evidence_block(item, lang=lang)
        for item in experiment_logs
        if item.get("step_name") == "deep_research_evidence_collected"
    ]
    output_blocks = [
        readable_deep_research_output_block(item, lang=lang)
        for item in experiment_logs
        if item.get("step_name") == "deep_research_output"
    ]
    content = "".join(started_blocks + plan_blocks + search_blocks + analysis_blocks + evidence_blocks + output_blocks)
    if not content:
        content = (
            "<section class='readable-block'>"
            f"<h3>{t('waiting_for_deep_research_log', lang)}</h3>"
            f"<p class='muted'>{t('waiting_deep_research_desc', lang)}</p>"
            "</section>"
        )
    return f"<div class='readable-log'>{content}</div>"


def deep_research_queue_status_block(storage: Storage, requirement_id: str, lang: str = "en") -> str:
    queue_row = next((row for row in storage.list_queue() if row["requirement_id"] == requirement_id), None)
    if not queue_row:
        requirement = storage.get_requirement(requirement_id)
        if requirement is not None and requirement.status == RequirementStatus.RESEARCHING:
            agent = requirement.assigned_to or "deep_research"
            return f"""
            <section class="readable-block">
              <h3>{t('deep_research_agent', lang)}</h3>
              <div><strong>{t('status', lang)}:</strong> {html.escape(t('researching_now', lang))}</div>
              <div><strong>{t('agent', lang)}:</strong> {html.escape(agent)}</div>
            </section>
            """
        return ""
    locked_by = str(queue_row.get("locked_by") or "")
    agent = locked_by or str(queue_row.get("assigned_agent") or "")
    state = t("researching_now", lang) if locked_by else t("waiting_for_agent_slot", lang)
    return f"""
    <section class="readable-block">
      <h3>{t('queued_for_deep_research', lang)}</h3>
      <div><strong>{t('status', lang)}:</strong> {html.escape(state)}</div>
      <div><strong>{t('agent', lang)}:</strong> {html.escape(agent or t('none', lang))}</div>
      <div><strong>{t('priority', lang)}:</strong> {html.escape(str(queue_row.get('priority', '')))}</div>
      <div><strong>{t('reason', lang)}:</strong> {html.escape(str(queue_row.get('reason', '')))}</div>
    </section>
    """


def readable_deep_research_plan_block(item: dict[str, object], lang: str = "en") -> str:
    payload = item["payload_json"]
    rows = "".join(
        f"<li><strong>{html.escape(str(task.get('strategy', 'research')))}:</strong> "
        f"{html.escape(str(task.get('question', '')))}<br>"
        f"<span class='summary'>{t('query', lang)}: {html.escape(str(task.get('query', '')))}"
        f"{' | ' + t('subreddit', lang) + ': ' + html.escape(str(task.get('subreddit'))) if task.get('subreddit') else ''}</span></li>"
        for task in payload.get("plan", [])
        if isinstance(task, dict)
    )
    return f"""
    <section class="readable-block">
      <h3>{t('deep_research_plan', lang)}</h3>
      <ul class="url-list">{rows or f"<li>{t('no_search_tasks', lang)}</li>"}</ul>
    </section>
    """


def readable_deep_research_search_block(item: dict[str, object], lang: str = "en") -> str:
    payload = item["payload_json"]
    urls = "".join(
        f"<li><a href=\"{html.escape(str(url))}\">{html.escape(str(url))}</a></li>"
        for url in payload.get("urls", [])[:8]
    )
    error = f"<div><strong>{t('error', lang)}:</strong> {html.escape(str(payload.get('error')))}</div>" if payload.get("error") else ""
    return f"""
    <section class="readable-block">
      <h3>{html.escape(str(item.get('step_name', '')).replace('_', ' ').title())}</h3>
      <div><strong>{t('question', lang)}:</strong> {html.escape(str(payload.get('question', '')))}</div>
      <div><strong>{t('query', lang)}:</strong> {html.escape(str(payload.get('query', '')))}</div>
      <div><strong>{t('subreddit', lang)}:</strong> {html.escape(str(payload.get('subreddit', '') or t('any', lang)))}</div>
      <div><strong>{t('returned_analyzed_added', lang)}:</strong> {html.escape(str(payload.get('items_returned', '')))} / {html.escape(str(payload.get('items_analyzed', '')))} / {html.escape(str(payload.get('evidence_added', '')))}</div>
      {error}
      <ul class="url-list">{urls}</ul>
    </section>
    """


def readable_deep_research_item_block(item: dict[str, object], lang: str = "en") -> str:
    payload = item["payload_json"]
    url = str(payload.get("url", ""))
    link = f"<a href=\"{html.escape(url)}\">{html.escape(url)}</a>" if url else ""
    return f"""
    <section class="readable-block">
      <h3>{t('evidence_item_analysis', lang)}</h3>
      <div><strong>{t('title', lang)}:</strong> {html.escape(str(payload.get('title', '')))}</div>
      <div><strong>URL:</strong> {link}</div>
      <div><strong>{t('relevant', lang)}:</strong> {html.escape(str(payload.get('is_relevant_evidence', '')))}</div>
      <div><strong>{t('type', lang)}:</strong> {html.escape(str(payload.get('evidence_type', '')))}</div>
      <div><strong>{t('analysis', lang)}:</strong> {html.escape(str(payload.get('analysis_summary', '')))}</div>
      <div><strong>{t('signals', lang)}:</strong> {html.escape(', '.join(str(signal) for signal in payload.get('signals', [])))}</div>
    </section>
    """


def readable_deep_research_evidence_block(item: dict[str, object], lang: str = "en") -> str:
    payload = item["payload_json"]
    return f"""
    <section class="readable-block">
      <h3>{t('evidence_collected', lang)}</h3>
      <div><strong>{t('items_analyzed', lang)}:</strong> {html.escape(str(payload.get('items_analyzed', 0)))}</div>
      <div><strong>{t('new_evidence_count', lang)}:</strong> {html.escape(str(len(payload.get('evidence_ids', []))))}</div>
      <div><strong>{t('evidence_ids', lang)}:</strong> {html.escape(', '.join(str(eid) for eid in payload.get('evidence_ids', [])))}</div>
    </section>
    """


def readable_deep_research_started_block(item: dict[str, object], lang: str = "en") -> str:
    payload = item["payload_json"]
    return f"""
    <section class="readable-block">
      <h3>{t('deep_research_started', lang)}</h3>
      <div><strong>{t('requirement_label', lang)}:</strong> {html.escape(str(payload.get('requirement_id', '')))}</div>
      <div><strong>{t('agent', lang)}:</strong> {html.escape(str(payload.get('agent_id', '')))}</div>
      <div><strong>{t('time', lang)}:</strong> {html.escape(str(item.get('created_at', '')))}</div>
    </section>
    """


def readable_deep_research_output_block(item: dict[str, object], lang: str = "en") -> str:
    payload = item["payload_json"]
    scores = payload.get("scores", {})
    score_text = ""
    if isinstance(scores, dict):
        score_text = ", ".join(
            f"{key}: {value}"
            for key, value in scores.items()
            if key in {"overall_score", "overall_label", "pain_intensity_score", "engagement_score", "monetization_score"}
        )
    regions = payload.get("country_area_distribution", [])
    region_text = json.dumps(regions, default=str) if regions else t("no_geography", lang)
    return f"""
    <section class="readable-block">
      <h3>{t('deep_research_output', lang)}</h3>
      <div><strong>{t('requirement_label', lang)}:</strong> {html.escape(str(payload.get('requirement_id', '')))}</div>
      <div><strong>{t('run', lang)}:</strong> {html.escape(str(payload.get('research_run_id', '')))}</div>
      <div><strong>{t('agent', lang)}:</strong> {html.escape(str(payload.get('agent_id', '')))}</div>
      <div><strong>{t('is_real_requirement', lang)}:</strong> {html.escape(str(payload.get('is_real_requirement', '')))}</div>
      <div><strong>{t('status', lang)}:</strong> {html.escape(str(payload.get('status', '')))}</div>
      <div><strong>{t('scale', lang)}:</strong> {html.escape(str(payload.get('requirement_scale', '')))}</div>
      <div><strong>{t('recommendation', lang)}:</strong> {html.escape(str(payload.get('recommendation', '')))}</div>
      <div><strong>{t('reason', lang)}:</strong> {html.escape(str(payload.get('realness_reason', '')))}</div>
      <div><strong>{t('score', lang)}:</strong> {html.escape(score_text)}</div>
      <div><strong>{t('country_area', lang)}:</strong> {html.escape(region_text)}</div>
    </section>
    """


def unique_search_plan_entries(entries: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    unique: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in entries:
        payload = item.get("payload_json", {})
        key = (
            str(payload.get("plan_id", "")),
            str(payload.get("cycle_index", "")),
            str(item.get("task_group_run_id", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def unique_search_log_entries(entries: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    unique: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in entries:
        payload = item.get("payload_json", {})
        key = (
            str(item.get("step_name", "")),
            str(payload.get("agent_id", "")),
            str(payload.get("query") or payload.get("search_query") or ""),
            str(payload.get("url") or payload.get("output_path") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def readable_search_plan_block(item: dict[str, object], lang: str = "en") -> str:
    payload = item["payload_json"]
    assignments = payload.get("assignments", [])
    if not isinstance(assignments, list):
        assignments = []
    rows = "".join(readable_search_plan_assignment(assignment, lang=lang) for assignment in assignments if isinstance(assignment, dict))
    if not rows:
        queries = payload.get("queries", [])
        if isinstance(queries, list):
            rows = "".join(
                f"<tr><td></td><td>{t('all_reddit', lang)}</td><td>{html.escape(str(query))}</td><td></td><td></td></tr>"
                for query in queries
            )
    if not rows:
        rows = f"<tr><td colspan='5' class='muted'>{t('no_planned_searches', lang)}</td></tr>"
    search_brief = payload.get("search_brief", {})
    if not isinstance(search_brief, dict):
        search_brief = {}
    coverage = search_brief.get("coverage_targets", [])
    if not isinstance(coverage, list):
        coverage = []
    return f"""
    <section class="readable-block">
      <h3>{t('search_plan_cycle', lang)} {html.escape(str(payload.get('cycle_index', '')))}</h3>
      <div><strong>{t('planner', lang)}:</strong> {html.escape(str(payload.get('planner_agent_id', item.get('agent_role', 'search_planner'))))}</div>
      <div><strong>{t('user_description', lang)}:</strong> {html.escape(str(payload.get('input_description', '')))}</div>
      <div><strong>{t('search_goal', lang)}:</strong> {html.escape(str(payload.get('search_goal', '')))}</div>
      <div><strong>{t('domain', lang)}:</strong> {html.escape(str(search_brief.get('domain', '')))}</div>
      <div><strong>{t('coverage', lang)}:</strong> {html.escape(', '.join(str(value) for value in coverage))}</div>
      <table>
        <thead><tr><th>{t('search_agent_col', lang)}</th><th>{t('subreddit', lang)}</th><th>{t('question', lang)} / {t('query', lang)}</th><th>{t('strategy', lang)}</th><th>{t('why', lang)}</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """


def readable_search_plan_assignment(assignment: dict[str, object], lang: str = "en") -> str:
    return f"""
    <tr>
      <td>{html.escape(str(assignment.get('agent_id', '')))}</td>
      <td>{html.escape(str(assignment.get('subreddit', '') or t('all_reddit', lang)))}</td>
      <td>{html.escape(str(assignment.get('query', '')))}</td>
      <td>{html.escape(str(assignment.get('strategy', '')))}</td>
      <td>{html.escape(str(assignment.get('why', '')))}</td>
    </tr>
    """


def readable_search_agent_block(item: dict[str, object], lang: str = "en") -> str:
    payload = item["payload_json"]
    urls = payload.get("urls", [])
    titles = payload.get("titles", [])
    if len(titles) < len(urls):
        titles = [*titles, *urls[len(titles) :]]
    url_rows = "".join(
        f"<li><a href='{html.escape(str(url))}'>{html.escape(str(title or url))}</a></li>"
        for url, title in zip(urls, titles)
    ) or f"<li class='muted'>{t('no_urls_collected', lang)}</li>"
    return f"""
    <section class="readable-block">
      <h3>{html.escape(str(payload.get('agent_id', item.get('agent_role', t('search_agent', lang)))))}</h3>
      <div><strong>{t('status', lang)}:</strong> {html.escape(str(payload.get('status', 'completed')))}</div>
      <div><strong>{t('query', lang)}:</strong> {html.escape(str(payload.get('query', '')))}</div>
      <div><strong>{t('subreddit', lang)}:</strong> {html.escape(str(payload.get('subreddit', '') or t('all_reddit', lang)))}</div>
      <div><strong>{t('strategy', lang)}:</strong> {html.escape(str(payload.get('strategy', '')))}</div>
      <div><strong>{t('items_collected', lang)}:</strong> {html.escape(str(payload.get('items_collected', 0)))}</div>
      {f"<div><strong>Error:</strong> {html.escape(str(payload.get('error', '')))}</div>" if payload.get('error') else ""}
      <div><strong>{t('output_file', lang)}:</strong> {html.escape(str(payload.get('output_path', '')))}</div>
      <ul class="url-list">{url_rows}</ul>
    </section>
    """


def readable_item_analysis_block(item: dict[str, object], lang: str = "en") -> str:
    payload = item["payload_json"]
    is_possible = payload.get("is_possible_requirement", payload.get("is_requirement"))
    decision = t("possible_requirement", lang) if is_possible else t("sample_rejected", lang)
    reason = payload.get("sample_analysis") or payload.get("analysis_summary") or payload.get("sample_rejection_reason") or payload.get("rejection_reason") or t("no_sample_analysis", lang)
    query = payload.get("search_query")
    return f"""
    <section class="readable-block">
      <h3>{html.escape(decision)} {t('sample_analysis', lang)}</h3>
      <div><strong>{t('agent', lang)}:</strong> {html.escape(str(payload.get('agent_id', '')))}</div>
      {f'<div><strong>{t("query", lang)}:</strong> {html.escape(str(query))}</div>' if query else ''}
      <div><strong>URL:</strong> <a href="{html.escape(str(payload.get('url', '')))}">{html.escape(str(payload.get('url', '')))}</a></div>
      <div><strong>{t('title', lang)}:</strong> {html.escape(str(payload.get('title', '')))}</div>
      <div><strong>{t('subreddit', lang)}:</strong> {html.escape(str(payload.get('subreddit', '')))}</div>
      <div><strong>{t('signals', lang)}:</strong> {html.escape(', '.join(str(signal) for signal in payload.get('signals', [])))}</div>
      <div><strong>{t('confidence', lang)}:</strong> {html.escape(str(payload.get('confidence') or ''))}</div>
      <div><strong>{t('sample_analysis', lang)}:</strong> {html.escape(str(reason))}</div>
      <div><strong>{t('sample_result', lang)}:</strong> {html.escape(str(payload.get('requirement_title') or payload.get('sample_rejection_reason') or payload.get('rejection_reason') or decision))}</div>
    </section>
    """


def readable_lifecycle_block(item: dict[str, object], lang: str = "en") -> str:
    payload = item["payload_json"]
    payload_text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    return f"""
    <section class="readable-block">
      <h3>{html.escape(str(item.get('step_name', '')).replace('_', ' ').title())}</h3>
      <div>{html.escape(str(item.get('message', '')))}</div>
      <pre>{html.escape(payload_text)}</pre>
    </section>
    """


def readable_activity_block(item: dict[str, object], lang: str = "en") -> str:
    return f"""
    <section class="readable-block">
      <h3>{html.escape(str(item.get('agent_id', t('agent', lang))))}</h3>
      <div><strong>{t('status', lang)}:</strong> {html.escape(str(item.get('status', '')))}</div>
      <div><strong>{t('task', lang)}:</strong> {html.escape(str(item.get('task_id', '')))}</div>
      <div><strong>{t('input', lang)}:</strong> {html.escape(', '.join(str(ref) for ref in item.get('input_refs', [])))}</div>
      <div><strong>{t('output', lang)}:</strong> {html.escape(', '.join(str(ref) for ref in item.get('output_refs', [])))}</div>
    </section>
    """


def terminal_log_entry(item: dict[str, object], lang: str = "en") -> str:
    meta = f"[{item['time']}] {item['role']} {item['name']} :: {item['step']}".strip()
    payload = item.get("payload")
    payload_text = json.dumps(payload, indent=2, sort_keys=True, default=str) if payload else ""
    error = str(item.get("error") or "")
    return (
        "<div class='terminal-entry'>"
        f"<div class='terminal-meta'>{html.escape(meta)}</div>"
        f"<div class='terminal-message'>{html.escape(str(item.get('message') or ''))}</div>"
        + (f"<div class='terminal-payload'>{html.escape(payload_text)}</div>" if payload_text and payload_text != "{}" else "")
        + (f"<div class='terminal-error'>{html.escape(error)}</div>" if error else "")
        + "</div>"
    )


def requirement_samples_page(
    storage: Storage,
    task_group_id: str = "",
    task_group_run_id: str = "",
    requirement_id: str = "",
    lang: str = "en",
) -> str:
    samples = storage.list_requirement_samples(
        task_group_id=task_group_id or None,
        task_group_run_id=task_group_run_id or None,
        requirement_id=requirement_id or None,
        limit=500,
    )
    title = t("requirement_samples", lang)
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
    ) or f"<tr><td colspan='6' class='muted'>{t('no_requirement_samples', lang)}</td></tr>"
    return f"""
    <h1>{html.escape(title)}</h1>
    <p class="muted">{t('requirement_samples_desc', lang)}</p>
    <div class="linkbar"><a href="/">{t('nav_running_status', lang)}</a><a href="/possible">{t('nav_possible', lang)}</a><a href="/rejected">{t('nav_rejected', lang)}</a></div>
    <table><thead><tr><th>{t('time', lang)}</th><th>{t('requirement_label', lang)}</th><th>{t('sample', lang)}</th><th>{t('status', lang)}</th><th>{t('task_group', lang)}</th><th>{t('run', lang)}</th></tr></thead><tbody>{rows}</tbody></table>
    """


def pipeline_page(storage: Storage, pipeline_run_id: str, lang: str = "en") -> str:
    pipeline = storage.get_pipeline_run(pipeline_run_id)
    if pipeline is None:
        return f"<h1>{t('pipeline_not_found', lang)}</h1>"
    return f"""
    <h1>{t('pipeline_snapshot', lang)}</h1>
    <p><span class="status">{html.escape(str(pipeline['status']))}</span> {html.escape(str(pipeline['pipeline_run_id']))}</p>
    <p>{html.escape(str(pipeline['summary']))}</p>
    <div class="linkbar"><a href="/">{t('nav_running_status', lang)}</a><a href="/possible">{t('nav_possible', lang)}</a><a href="/rejected">{t('nav_rejected', lang)}</a></div>
    <h2>{t('cycle_result', lang)}</h2>
    <pre>{html.escape(json.dumps(pipeline['result'], indent=2, default=str))}</pre>
    """


def reports_page(storage: Storage, lang: str = "en") -> str:
    from .agents import ReportAgent

    report = ReportAgent(storage, "dashboard-report").daily_report()
    return f"<h1>{t('daily_report', lang)}</h1><pre>{html.escape(report)}</pre>"


def requirement_table(requirements: list[object], lang: str = "en") -> str:
    body = "".join(
        "<tr>"
        f"<td><a href='/requirement?id={html.escape(item.requirement_id)}'>{html.escape(item.canonical_requirement)}</a></td>"
        f"<td><span class='status'>{status_text(item.status.value, lang)}</span></td>"
        f"<td>{item.current_scores.get('overall_score', 0)}</td>"
        f"<td>{item.times_detected}</td><td>{item.evidence_count}</td><td>{item.subreddit_count}</td>"
        f"<td>{html.escape(', '.join(region['region'] for region in item.geo_distribution[:3]))}</td>"
        f"<td>{html.escape(item.last_seen)}</td><td>{html.escape(str(item.latest_recommendation or ''))}</td>"
        "</tr>"
        for item in requirements
    )
    return (
        f"<table><thead><tr><th>{t('requirement_label', lang)}</th><th>{t('status', lang)}</th><th>{t('score', lang)}</th><th>{t('times_detected', lang)}</th>"
        f"<th>{t('evidence', lang)}</th><th>{t('subreddits', lang)}</th><th>{t('country_area', lang)}</th><th>{t('last_seen', lang)}</th><th>{t('recommendation', lang)}</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )
