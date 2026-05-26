# Product Plan: Always-On Reddit Requirement Discovery System

## 1. Product Concept

This product is an always-on, multi-agent demand intelligence system for discovering small but real user requirements from Reddit.

The product should not depend on a user entering a broad market direction. Instead, it continuously scans Reddit, detects possible requirements, saves all evidence, tracks requirement history, and assigns deep research agents to validate whether a requirement is real, how strong it is, where it appears, and whether it has changed since the last investigation.

Core promise:

> Continuously discover, track, and validate emerging user requirements from Reddit, then turn them into readable opportunity reports for humans.

The system is not just a Reddit crawler. It is a living requirement knowledge base with memory, change detection, and human-readable dashboards.

## 2. Core Workflow

```text
Reddit Sources
  -> Discovery Agents
  -> Raw Evidence Store
  -> Candidate Requirement Pool
  -> Requirement Memory Agent
  -> Deep Research Queue
  -> Deep Research Agents
  -> Requirement Knowledge Base
  -> Human Dashboard and Reports
```

High-level flow:

1. Discovery agents continuously scan Reddit posts and comments.
2. They identify possible user requirements and save supporting evidence.
3. Candidate requirements are added to a shared pool.
4. Requirement memory deduplicates, merges, preserves history, and checks historical records.
5. Every canonical requirement is assigned to deep research; priority only controls order.
6. Deep research agents validate the requirement, estimate signal strength, infer geography, and identify opportunity types.
7. Results are saved into the knowledge base.
8. Human users read, filter, review, and act on the results through a dashboard.

## 3. Agent Roles

### 3.1 Discovery Agents

Discovery agents search Reddit for weak signals of possible requirements.

Responsibilities:

- Monitor selected subreddits, posts, and comment chains.
- Detect complaint language, recommendation requests, workaround behavior, and repeated frustration.
- Save raw evidence before summarizing.
- Extract candidate requirement statements.
- Assign an initial confidence score.
- Avoid making final validation decisions.

Example signals:

- "Is there a tool for..."
- "How do people deal with..."
- "I wish there was..."
- "Why is it so hard to..."
- "What is the best way to..."
- "I am tired of..."
- "Any alternatives to..."
- "Does anyone else have this problem..."

Example output:

```json
{
  "requirement_title": "People want an easier way to track pet medication schedules",
  "source": "reddit",
  "subreddits": ["r/dogs", "r/pets", "r/AskVet"],
  "evidence_ids": ["ev_001", "ev_002"],
  "initial_signal_type": "repeated complaint",
  "confidence": 0.42,
  "created_at": "2026-05-08T00:00:00Z"
}
```

### 3.2 Requirement Memory Agent

The requirement memory agent is the system's canonicalization and history layer.

Responsibilities:

- Maintain the candidate requirement pool.
- Detect duplicates and near-duplicates.
- Merge similar requirements into canonical requirement records.
- Check whether a requirement has appeared before.
- Compare new evidence with previous research results.
- Queue every canonical requirement for deep research.
- Preserve enough history for deep research and search planning to learn from prior runs.
- Prevent multiple agents from researching the same requirement at the same time.

Key questions:

- Has this requirement been seen before?
- Is this a duplicate, a variation, or a new requirement?
- What was the previous conclusion?
- Why did this requirement appear again?
- Did signal strength, geography, audience, urgency, or monetization potential change?
- Should the system run deep research again?

Example requirement record:

```json
{
  "requirement_id": "REQ-2026-000142",
  "canonical_requirement": "Parents need easier lunchbox meal planning for picky kids",
  "status": "needs_deep_research",
  "first_seen": "2026-05-01T00:00:00Z",
  "last_seen": "2026-05-08T00:00:00Z",
  "times_detected": 17,
  "related_evidence_count": 43,
  "previous_research_summary": "Weak but growing signal. Mostly US parenting communities.",
  "last_research_decision": "watching",
  "reopen_reason": "New discussion spike in r/Parenting and r/MealPrepSunday",
  "assigned_to": null
}
```

### 3.3 Deep Research Agents

Deep research agents validate one requirement at a time.

Responsibilities:

- Investigate whether the requirement is real or just noise.
- Collect supporting and opposing evidence.
- Estimate demand signal size.
- Identify audience segments.
- Infer geographic distribution.
- Check existing products and current user workarounds.
- Detect willingness-to-pay signals.
- Identify product, service, content, affiliate, or community opportunities.
- Compare current findings with previous research runs.
- Produce a structured research report.

Research questions:

- How often does this requirement appear?
- How many subreddits discuss it?
- Are users emotionally invested in the problem?
- Are users already paying for solutions?
- What are current alternatives?
- What do users dislike about existing solutions?
- Is this a product opportunity, content opportunity, or not worth pursuing?

Example output:

```json
{
  "requirement_id": "REQ-2026-000142",
  "realness_score": 78,
  "signal_size": "medium",
  "geo_signal": ["United States", "Canada", "United Kingdom"],
  "audience": ["parents of young children", "working parents"],
  "pain_intensity": "high",
  "monetization_signal": "medium",
  "existing_solutions": ["meal planning apps", "Pinterest boards", "paid recipe newsletters"],
  "product_opportunities": [
    "AI picky-eater lunchbox planner",
    "weekly school lunch subscription content",
    "printable meal planning system"
  ],
  "content_opportunities": [
    "30 lunchbox ideas for picky eaters",
    "best lunchbox meal planner apps"
  ],
  "recommendation": "keep tracking and test content first"
}
```

### 3.4 Change Detection Agent

The change detection agent compares new evidence against old conclusions.

Responsibilities:

- Detect when an old requirement becomes active again.
- Identify what changed since the previous research run.
- Separate repeated noise from meaningful new signal.
- Recommend whether to reopen deep research.

Possible change reasons:

- More evidence appeared across more subreddits.
- A new region started discussing the problem.
- Users now mention paying for solutions.
- A competitor product became popular or failed.
- Existing solutions changed pricing or removed features.
- New regulation, platform policy, or economic condition changed the pain.

### 3.5 Report and Dashboard Agent

The report agent converts structured data into human-readable outputs.

Responsibilities:

- Generate daily and weekly reports.
- Summarize new, repeated, reopened, validated, and rejected requirements.
- Produce readable opportunity briefs.
- Explain why a requirement is important.
- Preserve evidence links and reasoning traces.

## 4. Persistent Storage Design

The system must save all search results and research decisions. Long-running value depends on memory.

### 4.1 Raw Evidence Store

Stores original Reddit data before AI processing.

Suggested fields:

- `evidence_id`
- `source`
- `source_url`
- `subreddit`
- `post_id`
- `comment_id`
- `title`
- `body`
- `author_metadata_allowed`
- `score`
- `comment_count`
- `created_at`
- `fetched_at`
- `language`
- `geo_hints`
- `matched_patterns`
- `raw_payload`

### 4.2 Candidate Requirement Pool

Stores possible requirements extracted from raw evidence.

Suggested fields:

- `candidate_id`
- `requirement_title`
- `requirement_description`
- `evidence_ids`
- `signal_type`
- `detected_audience`
- `detected_pain`
- `initial_confidence`
- `duplicate_candidate_ids`
- `status`
- `created_at`
- `updated_at`

### 4.3 Requirement Knowledge Base

Stores canonical requirements and long-term history.

Suggested fields:

- `requirement_id`
- `canonical_requirement`
- `description`
- `status`
- `first_seen`
- `last_seen`
- `times_detected`
- `evidence_count`
- `subreddit_count`
- `geo_distribution`
- `audience_segments`
- `current_scores`
- `previous_scores`
- `research_history`
- `decision_history`
- `reopen_events`
- `latest_recommendation`

### 4.4 Research Runs

Stores every deep research attempt.

Suggested fields:

- `research_run_id`
- `requirement_id`
- `agent_id`
- `started_at`
- `completed_at`
- `input_evidence_ids`
- `research_questions`
- `findings`
- `scores`
- `geo_analysis`
- `market_signal_analysis`
- `existing_solution_analysis`
- `recommendation`
- `limitations`
- `changed_since_last_run`

### 4.5 Agent Activity Logs

Stores operational history for observability.

Suggested fields:

- `agent_id`
- `agent_role`
- `task_id`
- `status`
- `started_at`
- `completed_at`
- `input_refs`
- `output_refs`
- `error`
- `retry_count`
- `cost_estimate`

## 5. Requirement Status Model

Recommended statuses:

- `new_candidate`
- `duplicate_candidate`
- `needs_more_evidence`
- `queued_for_research`
- `researching`
- `validated`
- `rejected`
- `watching`
- `reopened`
- `archived`

Status meanings:

- `new_candidate`: newly discovered and not yet canonicalized by requirement memory.
- `duplicate_candidate`: merged into an existing requirement.
- `needs_more_evidence`: legacy status for older runs before all canonical requirements were queued for research.
- `queued_for_research`: ready for deep research.
- `researching`: currently assigned to a deep research agent.
- `validated`: research suggests the requirement is real.
- `rejected`: research suggests the requirement is noise or not actionable.
- `watching`: not strong enough now, but worth monitoring.
- `reopened`: previously researched, but new evidence justifies another investigation.
- `archived`: no longer actively tracked.

## 6. Scoring Model

The product should estimate demand signal strength rather than claiming exact market size.

Recommended score dimensions:

- `frequency_score`: how often the requirement appears.
- `velocity_score`: whether mentions are increasing.
- `subreddit_spread_score`: how many communities discuss it.
- `pain_intensity_score`: strength of frustration, urgency, or emotional language.
- `engagement_score`: comments, upvotes, and replies from people with the same problem.
- `monetization_score`: signs that people pay, buy, subscribe, hire, replace, refund, or switch.
- `geo_confidence_score`: confidence in regional distribution.
- `solution_gap_score`: dissatisfaction with existing products or workarounds.
- `buildability_score`: how feasible it is to turn the requirement into a product, service, or content opportunity.

Recommended overall labels:

- `weak_signal`
- `watch_signal`
- `promising_signal`
- `validated_signal`
- `high_priority_signal`

## 7. Geography Detection

Reddit cannot perfectly identify where every user is located. The system should infer geography with confidence levels.

Geo signals:

- Location-specific subreddits, such as `r/AskUK`, `r/AusFinance`, or `r/PersonalFinanceCanada`.
- User text mentions, such as "in Germany" or "as an American".
- Currency mentions, such as USD, GBP, EUR, CAD, AUD.
- Local brand names.
- Legal, tax, healthcare, school, or regulation references.
- Language and spelling variants.

Example output:

```json
{
  "geo_distribution": [
    {
      "region": "United States",
      "confidence": 0.82,
      "evidence_count": 19
    },
    {
      "region": "United Kingdom",
      "confidence": 0.61,
      "evidence_count": 7
    },
    {
      "region": "Canada",
      "confidence": 0.55,
      "evidence_count": 5
    }
  ]
}
```

## 8. Human Dashboard

The dashboard is required because humans need to review the system's findings, audit evidence, and decide what to act on.

The dashboard should focus on clarity, traceability, and prioritization. It should not only show raw Reddit posts.

### 8.1 Home Dashboard

Purpose:

Show the current health and output of the system.

Core widgets:

- New candidate requirements today.
- Requirements queued for deep research.
- Requirements currently being researched.
- Validated requirements this week.
- Reopened requirements.
- Rejected/noisy requirements.
- Top rising requirements by velocity.
- Agent activity and failures.

### 8.2 Requirement Pool View

Purpose:

Let humans browse, filter, sort, and review all candidate and canonical requirements.

Required filters:

- Status.
- Score.
- First seen date.
- Last seen date.
- Subreddit.
- Region.
- Audience.
- Signal type.
- Assigned agent.
- Validated/rejected/watching.

Useful columns:

- Requirement title.
- Status.
- Overall score.
- Times detected.
- Evidence count.
- Subreddit count.
- Top regions.
- Last seen.
- Last decision.
- Reopen reason.

### 8.3 Research Queue View

Purpose:

Show which requirements are waiting for deep research and why.

Required fields:

- Queue priority.
- Requirement title.
- Reason for queueing.
- New evidence count.
- Previous research status.
- Assigned agent.
- Lock status.
- Estimated cost.
- Expected completion time.

Human actions:

- Approve deep research.
- Pause research.
- Increase or decrease priority.
- Merge with another requirement.
- Reject as noise.
- Force reopen.

### 8.4 Requirement Detail Page

Purpose:

Give a complete, readable view of one requirement.

Required sections:

- Canonical requirement statement.
- Current status and score.
- Executive summary.
- Evidence timeline.
- Related Reddit threads and comments.
- Audience segments.
- Geographic distribution.
- Pain intensity analysis.
- Existing solutions.
- User workarounds.
- Monetization signals.
- Product opportunities.
- Content opportunities.
- Research history.
- Decision history.
- Change since last research.
- Open questions.

The detail page must preserve links to source evidence so a human can verify the conclusion.

### 8.5 Evidence Timeline

Purpose:

Show how a requirement develops over time.

Timeline events:

- First detected.
- Similar requirement detected.
- New subreddit appeared.
- Spike in mentions.
- Deep research started.
- Deep research completed.
- Status changed.
- Requirement reopened.
- Requirement rejected or archived.

### 8.6 Research Report View

Purpose:

Display a readable opportunity report for each deep research run.

Report structure:

- Requirement summary.
- Why this might be real.
- Why this might be noise.
- Demand signal size.
- Geographic signal.
- Audience.
- Current solutions.
- Gaps in current solutions.
- Product opportunities.
- Content opportunities.
- Suggested next validation step.
- Confidence and limitations.
- Source evidence.

### 8.7 Daily and Weekly Reports

Purpose:

Help humans quickly understand what changed.

Daily report:

- New requirements discovered.
- Strongest new candidates.
- Requirements that appeared again.
- Requirements reopened.
- Requirements validated.
- Requirements rejected.
- Agent errors or data gaps.

Weekly report:

- Top validated opportunities.
- Fastest rising requirements.
- Requirements with growing commercial signal.
- Region-specific opportunities.
- Best content opportunities.
- Best product opportunities.
- Requirements to keep watching.

## 9. MVP Scope

The first MVP should avoid unlimited Reddit crawling. It should use a controlled set of high-signal subreddits and prove the workflow.

Recommended MVP:

1. Monitor a selected list of 100 to 300 subreddits.
2. Run discovery jobs every few hours.
3. Save all matching posts and comments.
4. Extract candidate requirements with structured AI output.
5. Deduplicate candidates into canonical requirements.
6. Maintain a requirement pool and status model.
7. Run deep research on the top 5 to 20 candidates per day.
8. Save every research run.
9. Generate daily reports.
10. Provide a dashboard with pool, queue, detail, and report views.

MVP should answer:

- What possible requirements did the system find today?
- Which ones are new?
- Which ones appeared before?
- Which ones changed?
- Which ones deserve deep research?
- Which ones are validated enough for human action?

## 10. Later Roadmap

Possible future features:

- Semrush, Ahrefs, or Google keyword data integration.
- Product review mining from Amazon, G2, App Store, or Chrome Web Store.
- Competitor tracking.
- Alerting when a watched requirement spikes.
- Human feedback loop to train scoring.
- Team assignment and notes.
- Content brief generation.
- Landing page idea generation.
- Reddit-safe response drafting.
- Export to Notion, Feishu, Airtable, or CSV.
- API access for requirement data.
- Cost and quality controls per agent group.

## 11. Key Product Risks

### 11.1 Reddit Noise

Reddit contains many one-off complaints. The system must avoid treating every complaint as a business opportunity.

Mitigation:

- Require evidence from multiple posts or comments.
- Score frequency, velocity, and subreddit spread.
- Preserve source evidence.
- Mark weak requirements as `watching` instead of `validated`.

### 11.2 False Market Size Claims

Reddit cannot accurately measure total market size by itself.

Mitigation:

- Use "signal size" instead of exact market size.
- Combine Reddit evidence with search, product, and competitor signals later.
- Show confidence and limitations in every report.

### 11.3 Agent Drift

Agents may produce inconsistent conclusions if roles are too broad.

Mitigation:

- Give each agent a narrow role.
- Require structured JSON outputs.
- Store decision history.
- Use requirement memory as the source of truth.

### 11.4 Duplicate Requirements

Similar requirements may appear in different wording.

Mitigation:

- Use semantic deduplication.
- Maintain canonical requirement records.
- Track variants and aliases.
- Allow human merge and split actions in the dashboard.

### 11.5 Reddit Policy and Community Norms

The system should be used for research and listening, not spam automation.

Mitigation:

- Preserve Reddit source links and context.
- Avoid automated promotional posting in MVP.
- Follow Reddit API and data usage rules.
- Design any future posting feature around helpful participation.

## 12. Design Principles

- Save raw evidence first, summarize second.
- The requirement pool is the core product asset.
- Historical memory is the differentiator.
- Every conclusion needs traceable evidence.
- Human review must be easy.
- Agents should have narrow jobs and structured outputs.
- The system should explain why something appeared again.
- The dashboard should help humans decide what to research, validate, build, or ignore.
