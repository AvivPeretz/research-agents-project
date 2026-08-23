# ResearchAgents Stability Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate every known instability pattern in the ResearchAgents pipeline (LLM waterfall, orchestration, alerting, Overleaf session fragility, and unscanned scale landmines) so the system can be trusted to run unattended across 15 concurrent projects instead of the 2 it runs today.

**Architecture:** No new subsystems are introduced beyond what's already in the codebase (BaseAgent + DI, SQLite, Playwright, the Groq→Gemini→OpenAI waterfall). The changes tighten existing seams: fix the two config/logic bugs already confirmed, close the silent-failure gaps found in this review, and add one new piece of infrastructure — a heartbeat/watchdog mechanism — because "guaranteed alerting" does not currently exist independent of each agent's own error handling. The Overleaf leg gets a two-track plan: harden the current free-tier session approach as an interim measure, and bring a Git-integration migration recommendation to the operator as the real fix.

**Tech Stack:** Python, SQLite (`utils/database_manager.py`), Playwright 1.58.0 (pinned) + `playwright-stealth` 1.0.6, pytest (existing test suite — `tests/integration/`, `tests/crash/`).

**Spec:** This plan *is* the spec — it was produced directly from a live codebase/log audit (this session) plus external research on Overleaf/Google session-automation reliability, not from a pre-written design doc. Section-level "Evidence" blocks substitute for a spec's requirements.

## Global Constraints

- No Docker/container implementation in this plan — only ensure fixes don't obstruct the future one-shot/cold-start container model.
- No code changes to anything outside what's listed per task — this plan does not touch unrelated files.
- Every fix must be verified against a realistic failure simulation, not just "it runs."
- Do not treat external-provider outages (Groq/Gemini/OpenAI/Overleaf/Google Scholar/Stanford all down) as bugs to "fix" — the system must detect and alert on them cleanly, not eliminate them (impossible).
- Stay strictly ToS-compliant and ethical for anything touching Overleaf/Google automation — no stealth beyond legitimate fingerprint-stability hygiene, no CAPTCHA-solving services, no credential stuffing.

---

## Scope note (read before executing)

This request spans roughly six independent subsystems (LLM reliability, orchestration bug, observability, Overleaf session vs. Git migration, scale landmines, cold-start correctness). Per the writing-plans scope rule, subsystems that need their own design decisions get a **right-sized task here** (problem → evidence → solution → alternatives → verification, per the brief) rather than being forced into premature step-by-step code. Tasks that are fully mechanical (config value, one-line logic fix, missing try/except) get full bite-sized TDD steps now, ready to execute directly. Tasks that require an operator decision, an external account/credential, or a live model-catalog lookup are flagged **[NEEDS OPERATOR INPUT]** or **[NEEDS SPAWN PLAN]** and should get their own focused plan once the direction here is approved.

---

## Reliability Metric — what counts as "a run"

The brief asks this to be defined explicitly rather than assumed. Proposal:

**Unit of measurement: one scheduled agent invocation for one project** (an *(agent, project)* pair), not the full 6-agent pipeline and not a bare process launch. This is the actual atomic unit of work in the target container architecture — each agent will be its own container on its own schedule, so "the pipeline" as a single measurable thing stops existing once that migration happens. Measuring at the full-pipeline level would also let one agent's failure (e.g. SupervisorStatusAgent) invisibly mask inside an otherwise-successful run.

**Success definition** (this is the part worth being precise about, because the current code makes "success" ambiguous — see Task 1): a run is a **system success** only if it completed *and* did not silently degrade — i.e. no swallowed exception, no fallback path taken without alerting. A run where every external provider is down but the system correctly detects that and alerts is a **handled failure**, not a silent one, and should be tracked separately from **unhandled failures** (bugs, crashes, silent degradation).

**On the 99.99% target — this needs to be said plainly, not softened:** at 15 projects × 5 project-scoped agents × 2 runs/week, that's ~150 (agent, project) events/week, ~7,800/year. 99.99% success allows fewer than one failure per year across *all* of them combined — including failures caused entirely by external providers (Groq, Gemini, OpenAI, Overleaf, Google Scholar, Stanford's paperreview.ai) that are outside this system's control and each individually have lower published uptime than that. Applying an infrastructure-SLA-grade number to a system with six external dependencies is not realistic as stated, and hitting it would require either padding the metric (excluding external failures, which is reasonable) or accepting a lower number.

**Recommendation:** track two separate numbers instead of one:
1. **Silent/unhandled-failure rate** (the thing actually in this system's control) — target 99.99%. This is achievable, because it just means "every failure gets detected and alerted," which is an engineering property, not a dependency-uptime property.
2. **Full-success rate** (nothing degraded, nothing alerted) — track it, report it, but do not gate go/no-go on 99.99% here; a more honest initial target is 99% given three LLM providers and up to three browser-automation targets are all in the critical path per project.

This distinction is what several tasks below are actually building toward (Task 1's "no silent degradation," Task 6's heartbeat/watchdog).

---

## Task 1: Fix LLM waterfall gaps — 413 misclassification, silent exhaustion, cross-run circuit-breaker persistence

**Problem:** Three distinct gaps in `agents/base_agent.py`'s `ask_llm()`/`_ask_provider()`:
1. A 413 "Request too large" is always classified as a permanent, non-retryable error (`base_agent.py:227-230`) and the provider is abandoned for that call — but Groq's TPM (tokens-per-minute) ceiling also surfaces as 413, and TPM limits are transient (they clear on their own), not permanent. Because no cooldown is recorded for a 413, the *next* LLM call in the same run hits the exact same TPM wall and repeats the same wasted attempt.
2. When the full waterfall is exhausted, `ask_llm()` raises `RuntimeError` (`base_agent.py:243-248`), but the only two call sites, in `agents/literature_research_agent.py:129` and `:232`, catch it locally and silently fall back to degraded output (keyword-only search, or `fallback_data`) with **no alert**. This never reaches `run_agent_safely()` in `main.py`, so a total waterfall failure produces a quietly worse result, not a signal anyone sees — this is exactly the "silent degradation" the reliability metric above is designed to catch.
3. `_provider_cooldowns` (`base_agent.py:26-27`) is in-process class state. It works fine today because `main.py` is one long-lived process handling all projects in a run — but it does not survive a cold restart, and the target architecture is cold-start containers per scheduled agent invocation. Not a bug yet, but it will silently stop doing anything the moment that migration happens further than it has already (per-agent containers are fine today since one container still handles all 15 projects in one process per run; a future per-project-per-agent container would break this).

**Evidence:** `agents/base_agent.py:98-103` (fixed waterfall order, built once), `:170-248` (`ask_llm`), `:227-230` (413 classified permanent), `:161-168` (`_start_provider_cooldown`), `:187-194` (cooldown skip check), `agents/literature_research_agent.py:129,232` (silent `except RuntimeError`). `config.py:39,46,49,52` — `LLM_MODEL_NAME="openai/gpt-oss-120b"`, `LLM_EXTRACTION_MODEL_NAME` defaults via `os.getenv(..., LLM_MODEL_NAME)` (confirmed **already fixed** as of commit `ecb9517`, 2026-08-19 19:37 — no `.env` override found; the 404s seen in logs at 18:52 that same day predate this fix by ~45 minutes and should not recur — verify on the next live run rather than re-fixing). `GEMINI_MODEL_NAME="gemini-1.5-flash"` — **still broken**, retired model, untouched since first reported.

**Proposed solution:**
- Distinguish transient-413 from permanent-413 by message content: only `"context_length_exceeded"` or `"maximum context length"` (a genuinely oversized single request — chunking is the real fix, not retry) stays classified as permanent. A 413 containing `"tokens per minute"`, `"tpm"`, or `"rate"` gets reclassified as rate-limit-shaped and routed through the existing `_start_provider_cooldown()` path instead of `break`-and-skip.
- Add a single shared helper, `_alert_waterfall_exhausted(context: str)`, called once from each of the two swallow sites in `literature_research_agent.py`, that sends exactly one deduplicated admin alert per run per project (not per paper) when the full waterfall is exhausted for that project — closing the silent-degradation gap without changing the existing "keep going with degraded output" behavior (a full crash is disproportionate to one paper's LLM failure; degrade-but-alert is correct here, per the reliability metric's own definition of "handled failure").
- Persist `_provider_cooldowns` to SQLite (a new small table, `llm_provider_cooldowns(provider TEXT PRIMARY KEY, cooldown_until TIMESTAMP)`) instead of (or in addition to, for in-process speed) the class dict, read at `BaseAgent.__init__` and written in `_start_provider_cooldown`. This is cheap now and removes the cold-start trap before it becomes a real bug.
- **[NEEDS OPERATOR INPUT]** `GEMINI_MODEL_NAME` fix requires querying Google's current model catalog at implementation time rather than hardcoding a guess in this plan (model names/availability change; guessing here risks writing another dead model string). Task: call the Gemini list-models endpoint (or check current docs) as the first step of implementing this fix, then set `GEMINI_MODEL_NAME` to whatever is current and confirm with a live test call before merging.

**Why alternatives were rejected:** A 4th LLM provider was considered and rejected — it doesn't fix the silent-failure root cause, it just delays hitting the same wall with one more provider to eventually exhaust. A full token-bucket rate limiter per provider was considered and rejected as over-engineered for the current 2-3x/week batch cadence; the cooldown-based approach already in place is proportionate once its two gaps (413 misclassification, no persistence) are closed.

**Verification:**
- [ ] **Step 1: Write failing test for 413 reclassification**

```python
# tests/unit/test_base_agent_llm_waterfall.py
def test_413_with_tpm_message_starts_cooldown(base_agent, mock_groq_client):
    mock_groq_client.chat.completions.create.side_effect = Exception(
        "Error code: 413 - Request too large for model, tokens per minute (TPM): Limit 8000"
    )
    with pytest.raises(RuntimeError):
        base_agent.ask_llm("prompt", max_retries=1)
    assert base_agent._provider_cooldowns.get("groq") is not None
```

- [ ] **Step 2: Run it, confirm it fails** — `pytest tests/unit/test_base_agent_llm_waterfall.py::test_413_with_tpm_message_starts_cooldown -v` — expected FAIL (no cooldown recorded today).
- [ ] **Step 3: Implement the reclassification** in `base_agent.py` (replace the single `is_size_error` check at line 227 with two checks — permanent vs. rate-limit-shaped — routing the latter through `_start_provider_cooldown`).
- [ ] **Step 4: Run it again, confirm PASS.**
- [ ] **Step 5: Write failing test for single deduplicated alert on waterfall exhaustion**

```python
def test_waterfall_exhaustion_sends_exactly_one_alert(literature_agent, mock_notifier, all_providers_down):
    literature_agent.process_project(project_with_5_papers)
    assert mock_notifier.send_admin_alert.call_count == 1
```

- [ ] **Step 6: Run, confirm FAIL** (today: 0 alerts sent).
- [ ] **Step 7: Implement `_alert_waterfall_exhausted` and call it from both catch sites** in `literature_research_agent.py:129,232`, with a per-run/per-project dedup guard (e.g. a set on the agent instance keyed by project name).
- [ ] **Step 8: Run, confirm PASS.**
- [ ] **Step 9: Add the `llm_provider_cooldowns` table** to `utils/database_manager.py`'s schema init, with `get_cooldown(provider)` / `set_cooldown(provider, until)` methods; wire `BaseAgent` to read/write through it instead of the bare class dict. Write a test that restarts a fresh `BaseAgent` instance (simulating cold start) mid-cooldown and confirms the new instance still skips the cooling-down provider.
- [ ] **Step 10: Run full `tests/unit/test_base_agent_llm_waterfall.py` and existing `tests/crash/` suite** to confirm no regression, then commit.

### Post-implementation update (2026-08-23)

Cerebras was evaluated as a 4th waterfall fallback after this plan was written, integrated (`config.py`, `agents/base_agent.py`), and live-tested end-to-end. It was then **fully removed**: real calls returned a `402 payment_required_error` on every model available to the account, meaning Cerebras's free tier requires billing/credit setup despite the operator's own dashboard advertising usable free-tier quotas — this violates the hard requirement that every fallback provider be usable with zero payment setup. The final waterfall is Groq → Gemini → NVIDIA NIM → OpenAI. This is a permanent decision; do not re-propose Cerebras without new evidence the billing requirement has changed.

---

## Task 2: Fix `main.py` first-sync eligibility bug

**Problem:** On a brand-new project's very first sync, the pipeline downloads it in the ingestion phase but then excludes it from every downstream phase (literature, progress, enhancement, supervisor) in that same run, and can early-return before any agent runs at all if it's the *only* project being processed.

**Evidence:** `main.py:116` — `all_projects = get_all_active_projects()` runs **before** ingestion. `main.py:128` — `scraper.sync_all_projects()` downloads new projects, result assigned to `updated_projects`, never merged back into `all_projects`. `main.py:135` — `valid_targets = [p for p in target_projects if p in all_projects]` filters against the stale pre-sync list. `main.py:136` — if `valid_targets` is empty and `args.agent` isn't in `['gc', 'ingestion', 'supervisor']`, the run exits at line 138 before literature/progress/enhancement execute.

**Proposed solution:** recompute `all_projects` (union with `updated_projects`, cheapest fix; recomputing via a second `get_all_active_projects()` call is equally correct but does one extra filesystem walk) immediately after the ingestion block, before the `valid_targets` filter runs.

**Why alternatives rejected:** moving the `get_all_active_projects()` call to after ingestion unconditionally (instead of unioning) was considered — rejected because it changes behavior for the `--agent gc/supervisor`-only paths that don't run ingestion at all and currently rely on the pre-call list; unioning preserves existing behavior for every other path and only adds the missing case.

**Verification:**
- [ ] **Step 1: Write failing test**

```python
# tests/integration/test_main_first_sync.py
def test_first_ever_project_included_in_same_run(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path / "overleaf_projects"))
    # overleaf_projects/ starts empty -> get_all_active_projects() returns []
    mock_scraper = Mock()
    mock_scraper.sync_all_projects.return_value = ["brand_new_project"]
    # brand_new_project now exists on disk after sync (simulate the download)
    (tmp_path / "overleaf_projects" / "brand_new_project").mkdir(parents=True)

    result = run_main(["--project", "brand_new_project", "--agent", "all"], scraper=mock_scraper)
    assert "brand_new_project" in result.processed_projects  # literature/progress/enhancement ran
```

- [ ] **Step 2: Run, confirm FAIL** — `pytest tests/integration/test_main_first_sync.py -v` — today: pipeline exits at line 138, `processed_projects` empty.
- [ ] **Step 3: Implement the fix** — after `main.py:128`'s `scraper.sync_all_projects()` call, add `all_projects = list(set(all_projects) | set(updated_projects))` before line 135's `valid_targets` computation.
- [ ] **Step 4: Run, confirm PASS.**
- [ ] **Step 5: Run full `tests/integration/` suite** to confirm no regression in the `--agent gc/supervisor`-only paths, then commit.

---

## Task 3: Register PQTrace / Udi Aharon supervisor mapping — direct DB write, not JSON edit

**Problem:** `SupervisorStatusAgent` has no supervisor to report to for either live test project, so it silently has nothing to say about them even on a zero-error run.

**Evidence:** `researchers_map.json` (repo root) contains only `{"STFT-DWT Reseach": "chenha.g.ariel.ac.il"}` (note: that email is missing its `@` — likely already-broken even for its one entry). `config.py:27-30` marks this file as **migration-only**; `utils/database_manager.py:migrate_from_json()` (419-476) only reads it once, gated on `db.get_project_count() == 0` (`main.py:109`). Since the DB is already populated (the pipeline has been running since 2026-08-09), **editing this JSON file now has no effect** — it will never be read again.

**Proposed solution:** this is not a code fix, it's a data-entry action, and it's explicitly gated on information only the operator has (the correct supervisor email addresses, and whether supervisor reporting is even in scope for the test phase — the memory of this project already notes the operator previously declined to register these projects with real researcher emails). **[NEEDS OPERATOR INPUT]**: confirm (a) supervisor reporting is in scope for the remaining test window, and (b) the two correct supervisor email addresses. Once confirmed, the fix is a one-off call to `DatabaseManager.add_project(project_name, supervisor_email)` (the same method `migrate_from_json` already uses) via a short standalone script — not a JSON edit, and not part of the main pipeline code.

**Verification:** after running the one-off script, `SupervisorStatusAgent`'s next run should produce a report section for both projects instead of "No projects with assigned supervisors found" — confirm by running `--agent supervisor` once against the two projects and checking the generated report output.

---

## Task 4: Additional landmines found in the codebase sweep

Each of these is independent and mechanical — small, isolated, TDD-ready.

### 4a. Dead config knob: `LITERATURE_MAX_WORKERS` ignored

**Problem:** `agents/literature_research_agent.py:358` hardcodes `ThreadPoolExecutor(max_workers=4)`, ignoring `Config.LITERATURE_MAX_WORKERS` (`config.py:136`), which exists specifically to be tunable and is referenced nowhere else. `agents/progress_tracking_agent.py:245` does this correctly (`getattr(Config, 'PROGRESS_MAX_WORKERS', 4)`) — same pattern, just not applied here. At 15 projects, an operator trying to tune literature-agent concurrency via config has no effect.

**Fix:** `ThreadPoolExecutor(max_workers=getattr(Config, 'LITERATURE_MAX_WORKERS', 4))`, matching the existing pattern in `progress_tracking_agent.py`.

- [ ] Write a test asserting `LiteratureResearchAgent` respects a monkeypatched `Config.LITERATURE_MAX_WORKERS` value (e.g. spy on `ThreadPoolExecutor` construction args). Confirm FAIL, apply the one-line fix, confirm PASS, commit.

### 4b. Duplicate hardcoded literal: `all_papers[:15]`

**Problem:** `agents/literature_research_agent.py:264` hardcodes `all_papers[:15]`, duplicating `Config.MAX_LITERATURE_PAPERS` (`config.py:111`, also 15 today) without referencing it — currently harmless because the values match by coincidence, but the config value is dead.

**Fix:** `all_papers[:Config.MAX_LITERATURE_PAPERS]`.

- [ ] Write a test that monkeypatches `Config.MAX_LITERATURE_PAPERS` to a different value (e.g. 3) and asserts the returned paper count respects it. Confirm FAIL, apply fix, confirm PASS, commit.

### 4c. Cascading failure: `SupervisorStatusAgent` aborts remaining supervisors on one LLM failure

**Problem:** `agents/supervisor_status_agent.py:219-262` loops over `projects_by_sup.items()` with no per-supervisor try/except. `_generate_report_via_llm` (106-158) raises `RuntimeError` on any LLM/validation failure, which propagates out of the loop to the single outer try/except around `run()` (205-275) — meaning one supervisor's bad LLM response skips **every subsequent supervisor** in that run, not just the failing one. This is the same isolation the other three agents already do correctly via `ThreadPoolExecutor` + `as_completed` (e.g. `literature_research_agent.py:358-373`).

**Fix:** wrap each iteration of the `projects_by_sup.items()` loop in its own try/except, log + alert per-supervisor failure, `continue` to the next supervisor.

- [ ] Write a test with 2 supervisors where the first's LLM call raises; assert the second supervisor's report is still generated. Confirm FAIL (today: second supervisor never runs), apply fix, confirm PASS, commit.

### 4d. Silent failure: bare `except Exception` + `print` in ingestion outer loop

**Problem:** `ingestion/data_ingestion_agent.py:423-424` — the outer `try` wrapping the whole `for row in rows` sync loop has only `except Exception as e: print(...)`, no `self.logger.error`, no notifier alert. Any exception escaping the per-project inner try (already correctly isolated) silently aborts the rest of that sync cycle with output invisible in an unattended/cold-started container.

**Fix:** replace the bare `print` with `self.logger.error(...)` + `self.notifier.send_admin_alert(...)` (matching the pattern `run_agent_safely` uses in `main.py`).

- [ ] Write a test that forces an exception between per-project iterations (not inside the per-project try) and asserts `notifier.send_admin_alert` is called. Confirm FAIL, apply fix, confirm PASS, commit.

### 4e. No run-lock — concurrent `main.py` invocations can race

**Problem:** no mutual exclusion exists for `main.py` (grep for `flock|pidfile|lockfile` outside tests finds nothing beyond in-process `threading.Lock()`s). Harmless at 2-project scale (ingestion finishes in ~1-2 minutes), but ingestion is sequential with real per-project Playwright waits (0.8-2.2s human-mimicking delays, 7-10s before every PDF download) — at 15 projects this could stretch into several minutes, raising real risk of a next scheduled run overlapping a still-running one, racing `shutil.rmtree`/zip extraction into the same directory and two browsers sharing one `OVERLEAF_STATE_PATH` file.

**Fix:** a simple file-based lock (stdlib-friendly: `fcntl.flock` on POSIX, matching the lab-server deployment target) acquired at the very start of `main.py`; if already held, log and exit cleanly (skip, not error) rather than racing.

- [ ] Write a test that starts `main.py`'s lock acquisition in one process/thread, then asserts a second acquisition attempt fails fast and cleanly rather than blocking or racing. Confirm FAIL (no lock exists today), implement, confirm PASS, commit.

**Not flagged as problems** (investigated, no action needed): SQLite concurrent-write safety already has a passing test (`test_db_concurrent_writes_do_not_corrupt`); progress/enhancement agents already char-cap manuscript text before every LLM call (`MAX_DELTA_CHARS`, `_truncate_paper_text`), so the token-budget scaling work already done for `LiteratureResearchAgent` doesn't need to be repeated elsewhere.

---

## Task 5: The Overleaf connection

This is the highest-priority section, evaluated in the requested order, but the recommendation at the end does not defer to that order — see below.

### 5a. Harden the current free-tier session approach (interim measure)

**Evidence from external research** (developer reports, Playwright/anti-bot community writeups, Overleaf's own legal terms — see full findings above; confidence level noted per claim):
- **Documented, high confidence:** IP reputation (datacenter/proxy IPs flagged regardless of behavior) and automation fingerprint markers (`navigator.webdriver`, missing plugin arrays, `--enable-automation` CDP flag) are real, checked signals. `launch_persistent_context` does not suppress these by itself — it only preserves cookies/localStorage.
- **Documented fact, directly relevant:** this project already uses `storage_state` (not `launch_persistent_context` — the `OVERLEAF_USER_DATA_DIR` config value referencing persistent-context is dead code, never read outside tests) and already pins `playwright==1.58.0` exactly (not floating `latest`) and already includes `playwright-stealth==1.0.6`. Two of the community-recommended hygiene practices are already in place; this was not obvious before checking and is worth confirming rather than assuming a rebuild is needed.
- **Documented, moderate-high confidence:** request velocity/scripted patterns are a separate trigger from fingerprinting.
- **Folk wisdom / vendor claims, not independently verified:** session "warming" (periodic light authenticated activity extending session lifetime) — directionally plausible, no controlled study found, but low-cost and reuses code that already exists and is tested.
- **Documented fact, the most important finding of this whole section:** Overleaf's own terms of service (overleaf.com/legal) explicitly prohibit "any manual or automated means, including robots, scripts, or spiders to access, monitor, crawl, scrape, spider or mine" the service and require accessing it only through "publicly supported interfaces." This is not a technical risk to engineer around — it's a policy statement that the current automation approach, however well-hardened, is arguably out of ToS scope regardless of stealth quality.

**Proposed hardening (worth doing regardless of the 5c decision, since it also covers Google Scholar and Stanford's paperreview.ai, which are unaffected by an Overleaf-only Git migration):**
- Schedule `check_session_health()` (`ingestion/data_ingestion_agent.py:83-113`, already exists, already read-only/non-destructive) to run on its own lightweight cadence (e.g. daily) independent of the 2-3x/week full ingestion cadence — this is the "session warming" technique using code that's already written and tested, at zero new complexity.
- Remove the dead `OVERLEAF_USER_DATA_DIR` config value and its references (`config.py:33`, `setup_overleaf_session.py`) — it's never read in production, only referenced in test mocks; keeping it around implies a persistent-context approach that isn't actually in use, which is misleading for the next engineer.
- **[NEEDS OPERATOR INPUT]** verify the lab server's outbound IP is not a flagged datacenter range (the single highest-confidence trigger found) — this can't be checked from the codebase; a quick manual check (visiting an IP-reputation lookup from the lab server) resolves it.

### 5b. Automated self-healing via `reauth_overleaf.py`

**Evidence:** `check_session_health()` already runs proactively before ingestion (`data_ingestion_agent.py:177-197`) and already alerts + returns cleanly (no partial/broken run) on failure — this part of the ask is **already implemented** (added in commit `84183e5`). `reauth_overleaf.py`/`setup_overleaf_session.py` are aliases for `_perform_manual_login()` (115-162), which is deliberately non-headless and requires a human to solve any CAPTCHA and confirm login within a 600s window — this is correct and cannot be automated further (that's the entire point of CAPTCHA).

**Gap, not a redesign:** the alert fired on session failure should be maximally actionable — confirm it currently states the exact recovery command (`python reauth_overleaf.py`) and the requirement that it be run on a machine with a display, not just "manual login required." **Fix:** if the alert text doesn't already include the exact command, add it — this reduces mean-time-to-recovery without touching the CAPTCHA step itself, which correctly stays human-only.

- [ ] Read the current `send_admin_alert` call in `check_session_health()`'s failure path; if it lacks the exact recovery command, add it as a one-line string change; write a test asserting the alert body contains `"python reauth_overleaf.py"`.

### 5c. Overleaf Premium with native Git integration — go/no-go

**Findings that drive this recommendation:**
- The ToS finding above is not a minor caveat — it means continued investment in session-based Playwright automation carries account-suspension risk on top of the reCAPTCHA operational risk already known. A banned account doesn't degrade gracefully; it removes Overleaf ingestion entirely, for all 15 projects, with no automated recovery path at all (compare: a reCAPTCHA challenge is at least recoverable by a human in minutes).
- Overleaf's paid tier exposes each project as a git remote with a generatable, revocable auth token — token-based auth has none of the fragility this whole section is about: no reCAPTCHA exposure, no IP/fingerprint sensitivity, no browser profile/session state to manage at all. This also **structurally simplifies** the target cold-start container architecture — a git token in a secret/env var needs no session file, no health check, no warming cadence, removing an entire category of state management rather than mitigating it.
- Cost was explicitly out of scope for this research pass — the plan should note it needs confirming against Overleaf's current pricing at decision time, not guess a number here.
- **Important caveat, stated honestly:** this migration only fixes the Overleaf leg. Google Scholar (`utils/literature_fetcher.py`) and Stanford's paperreview.ai (`agents/research_enhancement_agent.py`) remain session-based Playwright automation with the same class of fragility — 5a's hardening still matters for those regardless of this decision, and their own ToS postures haven't been checked in this pass (flagged as an open follow-up, not asserted either way).

**Recommendation: the operator should bring Overleaf Premium/Git integration to the department head as the real fix**, not the lowest-priority option it's listed as in the request's ordering. This is the plan saying plainly what the evidence supports rather than softening it because it costs money: the free-tier session approach, however well-hardened by 5a, remains a policy-risk and single-point-of-failure liability that a $/month subscription with an officially supported API removes outright. 5a and 5b are still worth doing in the meantime — they reduce operational pain during the 2-3 week test window and continue to matter for the two automation targets a Git migration doesn't touch — but they are damage control on an approach the vendor's own terms say shouldn't be relied on long-term, not a substitute for the real fix.

**[NEEDS SPAWN PLAN]**: if approved, the actual Git-integration migration (auth flow, mapping git operations to the existing ingestion data model, credential storage) is its own focused plan — it's gated on an account-tier decision this document can't make, and touches enough of `ingestion/data_ingestion_agent.py` to warrant its own review cycle separate from this one.

---

## Task 6: Guaranteed observability — heartbeat + independent watchdog

**Problem:** "Guaranteed alerting" is claimed (commit `84183e5`'s message) but isn't actually independent of each agent's internal error handling in two ways: (1) failures absorbed inside an agent's own try/except (e.g. Task 1's waterfall exhaustion, before this plan's fix) never reach `run_agent_safely()` in `main.py`, and (2) `agents/notification_agent.py:_dispatch_email()` (74-96) simply `return`s `False` on total SMTP failure rather than raising — so if the mail relay is down at the exact moment an agent crashes, `send_admin_alert` silently fails, `run_agent_safely`'s own try/except around the alert call never triggers (nothing was raised), and the only trace is a log line nobody is watching in an unattended deployment. There is no non-email escalation channel today.

**Evidence:** `main.py:31-63` (`run_agent_safely`, including its own comment asserting the "either completed or someone was told" guarantee), `agents/notification_agent.py:74-96,243-253`.

**Proposed solution:**
- After every scheduled `main.py` invocation (regardless of agent, regardless of outcome), write one heartbeat record to a new SQLite table: `run_heartbeats(run_id, agent, started_at, ended_at, outcome)` where `outcome` is `success | degraded | failed`. This write happens in `main.py` itself, wrapping the whole invocation — independent of any individual agent's internal try/except, so it can't be silently skipped by an agent swallowing its own errors (it fires whether the agent's `run()` succeeds, raises, or returns a degraded result).
- A separate, minimal, stdlib-only watchdog script (its own cron entry) checks whether the expected heartbeat arrived within `scheduled_interval + grace_period`. If not, it escalates through a path that does **not** share code with `notification_agent.py` — a deliberately separate, minimal `smtplib` call — so a bug in the main notification path can't also silently break the thing meant to catch that bug. This closes the exact SPOF found: today, if email breaks at the same moment an agent crashes, nothing else notices.

**Why alternatives rejected:** a full third-party monitoring service (e.g. a paid uptime/heartbeat SaaS) was considered — rejected for now as disproportionate to the current scale and cadence (2-3x/week today, 15-project target is still a batch cadence, not high-frequency); the stdlib watchdog is proportionate and adds no new dependency. Making every agent raise instead of degrade-and-continue was considered and rejected — it would turn recoverable single-paper/single-project failures into full-run aborts, which is worse for availability, not better.

**[NEEDS SPAWN PLAN]**: this is new infrastructure (a new table, a new standalone script, a new cron entry) rather than a fix to existing code — right-sized as its own focused plan once this direction is approved, including the exact grace-period/escalation-channel design.

**Verification approach (for the spawned plan to implement against):** simulate a mid-run crash (kill the process before it can write the heartbeat) and confirm the watchdog fires after the grace period; run normally and confirm exactly one heartbeat with `outcome=success` is written; simulate an SMTP outage during a real agent crash and confirm the watchdog's independent escalation path still fires even though `send_admin_alert` failed silently.

---

## Task 7: Cold-start / one-shot correctness

**Finding:** the sweep found the codebase is already close to cold-start-correct — `BaseAgent.__init__` re-initializes DB, logging, and LLM clients fresh every run (no problematic module-level singletons found outside the LLM cooldown state), and no other in-memory cache assumes warm state across separate `main.py` invocations. The one real gap is Task 1's `_provider_cooldowns` persistence fix, which is already scoped above.

**Not a new task** — this section exists to record that the sweep was done and came back mostly clean, per the brief's explicit ask to check this. The only action item is Task 1's SQLite persistence step, plus a note for whoever eventually writes the Docker plan: a file-based run-lock (Task 4e) needs revisiting once the actual container networking model is chosen, since a lock file on local disk only works if agents share a host/volume — flagged here so that future plan doesn't have to rediscover it.

---

## Prioritized Implementation Order

1. **Task 2** (`main.py` first-sync bug) — one-line-scope fix, currently causing real data loss on every new project's first run. Do first.
2. **Task 4a/4b** (dead config knobs) — trivial, zero-risk, do alongside Task 2.
3. **Task 1** (LLM waterfall: 413 reclassification + silent-exhaustion alert) — highest-impact reliability fix; the Gemini model-name sub-task needs a live lookup, do it as part of this task's implementation session.
4. **Task 4c/4d** (SupervisorStatusAgent isolation, ingestion outer-loop alerting) — closes real cascading-failure and silent-failure gaps found in the sweep.
5. **Task 4e** (run-lock) — do before scaling past a handful of projects; not urgent at today's 2-project cadence but blocks safe scale-up.
6. **Task 5a/5b** (Overleaf hardening + alert actionability) — do during the remaining test window regardless of the 5c decision, since it also protects Google Scholar/Stanford automation.
7. **Task 3** (supervisor mapping) — blocked on operator input; can happen any time once the email addresses/scope decision is confirmed.
8. **Task 6** (heartbeat/watchdog) and **Task 5c** (Overleaf Git migration, if approved) — both are their own spawned plans; sequence them after 1-5 are merged so the watchdog has stable ground truth to monitor and the Git migration isn't competing with active bug-fixing in the same files.

## Go/No-Go Recommendation for the Operator to Bring to the Department Head

**Recommendation: pursue Overleaf Premium/Git integration.** The engineering case is not close: the current session-based approach carries policy risk (Overleaf's ToS explicitly prohibits the automation this pipeline relies on) on top of the already-known operational fragility (sub-week session lifetime, CAPTCHA-blocking). Token-based git auth removes the entire fragility category rather than mitigating it, and simplifies the target cold-start container architecture in the process. This is stated as the honest technical recommendation, independent of cost — cost and final approval are the department head's call, but the engineering answer favors the paid tier clearly enough that it shouldn't be presented as the deprioritized option.

In the meantime, Tasks 1, 2, 4, and 5a/5b should proceed regardless of that decision's timeline — they fix real, currently-active bugs and reduce risk for the parts of the pipeline (Google Scholar, Stanford) a Git migration won't touch either way.
