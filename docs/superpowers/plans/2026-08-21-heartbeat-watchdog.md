# Heartbeat + Independent Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the one real alerting single-point-of-failure found during the stability-hardening review: today, if `NotificationAgent.send_admin_alert()`'s SMTP call fails at the exact moment an agent crashes, the failure is swallowed silently and nobody is told the run didn't happen. This plan adds a heartbeat record per scheduled invocation plus a standalone watchdog that notices a missing heartbeat through a path independent of the code that might be broken.

**Architecture:** One new SQLite table (`run_heartbeats`) written by `main.py` itself — wrapping the whole invocation, not any individual agent's internal try/except, so it can't be silently skipped by an agent swallowing its own errors. A separate, minimal, stdlib-only watchdog script (its own cron entry, not invoked by `main.py`) checks for the expected heartbeat within `scheduled_interval + grace_period` and escalates through a deliberately separate `smtplib` call if one didn't arrive — so a bug in `agents/notification_agent.py` can't also silently disable the thing meant to catch that bug.

**Tech Stack:** Python stdlib (`smtplib`, `sqlite3`), no new dependencies. Follows the existing `utils/database_manager.py` schema-init pattern (see `llm_provider_cooldowns`, added in the parent stability-hardening plan's Task 1 — this plan's `run_heartbeats` table should sit in the same `queries` list using the same `CREATE TABLE IF NOT EXISTS` style, and expose `get`/`set`-shaped methods following the same per-method `try/except sqlite3.Error` + logger idiom already established there).

**Spec:** `docs/superpowers/plans/2026-08-21-stability-hardening.md`, Task 6 — this plan is that task's design pass, produced per its `[NEEDS SPAWN PLAN]` flag rather than being implemented inline in that session.

## Global Constraints

- No Docker/container implementation.
- The watchdog must not import or call anything from `agents/notification_agent.py` — its escalation path must be genuinely independent code, even if that means duplicating a small amount of SMTP-sending logic. That duplication is the point, not a DRY violation to fix.
- Do not alert on every run — only on a run that should have happened (per the operator's configured schedule) and didn't produce a heartbeat within the grace period. A quiet period with nothing scheduled must never page anyone.
- Every fix must be verified against a realistic failure simulation (a genuinely killed process, a genuinely broken SMTP path), not just "it runs."

## Context this plan inherits from the parent session

- The `llm_provider_cooldowns` table (added to `utils/database_manager.py` in the parent plan's Task 1) is the pattern to match: a `CREATE TABLE IF NOT EXISTS` block in the existing `queries` list, plus `get_*`/`set_*` methods with `try/except sqlite3.Error` and `self.logger.error(...)` on failure, never raising out to the caller.
- `main.py` already has a run-lock (`acquire_run_lock`, added in the parent plan's Task 4e) that wraps the entire pipeline body — the heartbeat write is a natural companion to that: both care about "is a run happening / did a run happen," and the heartbeat's start/end timestamps can reasonably be written from inside the same `with acquire_run_lock(...)` block, though they are conceptually independent (the lock prevents overlap; the heartbeat proves completion).
- `agents/notification_agent.py:send_admin_alert()` already exists as the normal alerting path (used throughout the parent plan's fixes) — this plan's watchdog deliberately does NOT reuse it, per the Global Constraints above.
- The parent plan's reliability metric (per-(agent, project) invocation; silent-failure rate target 99.99%, full-success rate target 99%) is what this infrastructure is ultimately in service of — the heartbeat's `outcome` field should be structured so it's queryable for that metric later, not just as a binary alive/dead signal.

## Task 1: `run_heartbeats` table + write path in `main.py`

**Problem:** No record exists today of whether a scheduled `main.py` invocation completed, failed, or never ran at all, independent of whether any individual agent's error handling worked correctly.

**Proposed solution:**
- New table in `utils/database_manager.py`, same `queries` list as `llm_provider_cooldowns`:
  ```sql
  CREATE TABLE IF NOT EXISTS run_heartbeats (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      agent TEXT NOT NULL,
      started_at TIMESTAMP NOT NULL,
      ended_at TIMESTAMP,
      outcome TEXT
  );
  ```
  `agent` records which `--agent` value this invocation ran (`all`, `literature`, `gc`, etc.) so the watchdog can eventually track per-agent cadence, not just "something ran." `outcome` is one of `success | degraded | failed` (matching the parent plan's reliability-metric vocabulary), nullable until the run ends (a row with `ended_at IS NULL` past its expected duration is itself a signal worth the watchdog checking, not just a missing row).
- Two methods on `DatabaseManager`, matching the cooldown methods' shape: `start_heartbeat(agent: str) -> int` (inserts a row with `started_at=now`, `ended_at=NULL`, `outcome=NULL`, returns the row id) and `finish_heartbeat(heartbeat_id: int, outcome: str)` (updates `ended_at=now`, `outcome=outcome`). Both wrapped in `try/except sqlite3.Error`, logging on failure, never raising — a heartbeat-write failure must not crash the actual pipeline run it's trying to observe.
- In `main.py`, call `start_heartbeat(args.agent)` immediately after the run-lock is acquired (so a run that got skipped due to lock contention correctly produces no heartbeat — that's not a failure, it's the lock working as designed), and `finish_heartbeat(id, outcome)` at the very end, in a `finally` block wrapping the rest of `main()`'s body, so it fires whether the run completes cleanly, an agent's `run_agent_safely` catches a crash, or an unhandled exception escapes some code path this session's fixes didn't cover. Determine `outcome`: `"failed"` if an unhandled exception propagated past `run_agent_safely` (rare, since that function itself catches broadly — but the `finally` block should catch this case if it ever happens), `"degraded"` if you can detect any agent's `run_agent_safely` returned a failure signal (check that function's actual return value/contract before assuming this is possible — it may currently be a bare `try/except` with no return, in which case this plan's implementation may need a small `run_agent_safely` return-value change to make "degraded" detectable at all; treat this as an open question for the implementer to resolve by reading the current code, not something to guess here), otherwise `"success"`.

**Verification:** kill the process (`SIGKILL`) mid-run after `start_heartbeat` but before `finish_heartbeat` — confirm the row exists with `ended_at IS NULL`. Run normally — confirm exactly one row with `outcome="success"`. Force an agent crash (mock one agent to raise inside `run_agent_safely`) — confirm the row's `outcome` reflects the failure, not "success".

## Task 2: standalone watchdog script

**Problem:** nothing today checks whether an expected heartbeat actually arrived, and the one thing that would normally alert on a missing run (`send_admin_alert`) can itself fail silently (confirmed in the parent session: `_dispatch_email` returns `False` on total SMTP failure rather than raising).

**Proposed solution:**
- A new standalone script, `watchdog.py` (repo root, matching the `reauth_overleaf.py`/`check_overleaf_session.py` sibling-script convention established in the parent session), run on its own cron entry — NOT invoked by `main.py`, NOT imported by anything in `agents/`.
- Reads `run_heartbeats` directly via `DatabaseManager` (read-only from its perspective — it only queries, never writes heartbeats itself) for the most recent row matching the schedule it's configured to watch (e.g. "the `all` agent should have a heartbeat with `ended_at` within the last `scheduled_interval + grace_period`").
- **[NEEDS OPERATOR INPUT]** the exact `scheduled_interval` and `grace_period` values depend on the operator's actual cron cadence for `main.py`, which isn't fully documented in the codebase (the parent session's audit found the pipeline is "manually triggered only, not on a schedule/cron" as of the last live-test-phase memory — confirm with the operator whether a real cron schedule now exists, and if so what it is, before hardcoding these values).
- On a missing/stale heartbeat: escalate via a **deliberately separate** `smtplib` call in `watchdog.py` itself — do not import `NotificationAgent`. Duplicate the minimum needed (SMTP host/port/credentials read from the same `Config`/env-var pattern, a plain-text message) rather than the full templating/retry logic of `notification_agent.py`. This is the load-bearing design decision: if `notification_agent.py` has a bug, `watchdog.py`'s independent code path is what notices it.
- Consider a second escalation channel beyond email (the parent plan's Task 6 section suggested this as an option, e.g. a Slack webhook) — **[NEEDS OPERATOR INPUT]**: does the operator have a second channel available (Slack webhook URL, SMS gateway, etc.)? If not, a second, independently-configured email account (not reusing the primary pipeline's SMTP credentials) is a reasonable fallback — but this needs the operator's infrastructure, not a guess from this plan.

**Why alternatives rejected:** a full third-party uptime/heartbeat SaaS was considered in the parent plan and rejected as disproportionate to the current batch cadence; the same reasoning applies here — a stdlib cron script is proportionate.

**Verification:** simulate a mid-run crash (kill the process before `finish_heartbeat`, per Task 1's verification) and confirm the watchdog fires after the grace period elapses. Run normally and confirm the watchdog finds the heartbeat and does NOT alert. Simulate an SMTP outage on the PRIMARY notification path (mock `notification_agent.py`'s SMTP call to fail) during a real agent crash, and confirm the watchdog's independent escalation still fires even though the primary alert failed silently — this is the actual proof the SPOF is closed, not just that the watchdog works in isolation.

## Open questions for whoever picks this plan up

1. Does `run_agent_safely` (`main.py`) currently have any way to signal "degraded but didn't crash" back to its caller, or does it only catch-and-alert with no return value? This determines whether `outcome="degraded"` is actually achievable in Task 1 without also touching `run_agent_safely`'s signature — read the current code before assuming.
2. What is the operator's actual current cron/scheduling setup for `main.py`? The parent session's most recent memory says it was manually triggered as of the live test phase (started 2026-08-09); this may have changed by the time this plan is executed.
3. Does the operator have a second alerting channel, or should the watchdog's fallback be a second independent email account?
