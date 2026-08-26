# Overleaf Premium / Git Integration — Migration Brief

**Purpose:** give the department head an honest basis for approving (or not) an
Overleaf paid subscription, and give the system operator an accurate technical
picture of what the migration involves. This is not a new analysis — the
engineering conclusion was already reached in this project's internal stability
audit (`docs/superpowers/plans/2026-08-21-stability-hardening.md`, Task 5c). This
document restates that conclusion for a non-engineering decision-maker and adds
current research on Overleaf's actual pricing and Git integration mechanics.

---

## 1. How Overleaf access works today

The system's `DataIngestionAgent` (`ingestion/data_ingestion_agent.py`) logs into
Overleaf using Playwright browser automation against a saved, persisted login
session (a JSON "storage state" file at `Config.OVERLEAF_STATE_PATH`). It scrapes
the project dashboard, detects which projects changed, and downloads each one's
source ZIP and compiled PDF by driving a real Chromium browser through Overleaf's
web UI — including human-mimicking typing and randomized delays specifically
designed to avoid triggering anti-bot detection.

**Session lifetime — a discrepancy worth surfacing plainly.** The project's own
documentation disagrees with itself on this point, and the department head should
know that rather than being handed a single confident number:

- The internal stability audit (the prior engineering review this brief is based
  on) states the session lifetime is **"sub-week"** before requiring manual
  re-authentication.
- The project's `README.md` states Overleaf sessions expire **"roughly once per
  quarter."**

Both can't be current at once. This brief defers to the stability audit's
sub-week figure as the operative one for the recommendation below, since it's the
more recent and more thoroughly investigated finding — but the operator should
reconcile this discrepancy (e.g. confirm actual observed session lifetime from
recent logs) before repeating either number to the department head as fact.

Either way, re-authentication requires a human: `_perform_manual_login()`
(`ingestion/data_ingestion_agent.py:115`) opens a **visible** browser window and
waits up to 10 minutes for a person to manually solve any reCAPTCHA challenge and
complete login. This cannot be automated — solving CAPTCHAs is deliberately
designed to require a human, so no amount of engineering hardening removes this
step. `reauth_overleaf.py` and `setup_overleaf_session.py` are two identically-named
entry points into this same manual flow.

**Overleaf's Terms of Service.** Overleaf's own legal terms (overleaf.com/legal)
explicitly prohibit "any manual or automated means, including robots, scripts, or
spiders" to access, monitor, crawl, scrape, or mine the service, and require access
only through "publicly supported interfaces." This means the current approach
carries **policy risk** (potential account suspension) that exists independently
of, and in addition to, its operational fragility. A suspended account doesn't
degrade — it removes Overleaf ingestion entirely, for every tracked project, with
no automated recovery path.

**What happens today when a session expires unattended.** This is handled better
than the fragility above might suggest, and it's worth being precise about it:

- Before every scheduled sync, `sync_all_projects()` calls `check_session_health()`
  — a fast, read-only pre-flight check that never opens a visible browser and never
  waits on a human (`data_ingestion_agent.py:83-113`).
- If the session is invalid, the pipeline **does not attempt an interactive login**
  (correctly — no human is present on an unattended scheduled run to solve a
  CAPTCHA). It logs the failure, sends an admin alert naming the exact recovery
  command (`python3 reauth_overleaf.py`), and returns an empty project list
  cleanly (`data_ingestion_agent.py:177-197`).
- Downstream in `main.py`, an empty ingestion result causes the literature,
  progress-tracking, and enhancement agents to skip cleanly for that run ("No
  updated projects... Skipping") rather than crash. The supervisor-reporting and
  garbage-collection phases still run regardless (`main.py:216-259`).

**In short: the pipeline degrades gracefully, not catastrophically — but it stays
degraded (no new manuscript data flows in) until a human physically sits at a
machine with a display and solves a CAPTCHA.** For an unattended server deployment,
that's the core operational problem, independent of the ToS question.

---

## 2. What Overleaf Premium's Git integration actually offers

*(Researched directly from Overleaf's own site and documentation, current as of
this writing — Overleaf's plans and pricing can and do change.)*

**Authentication.** Git access uses a dedicated **Git authentication token** —
not a session, not a password, and not subject to reCAPTCHA. Per Overleaf's own
docs: tokens are generated from Account Settings (up to 10 at a time), are fully
revocable ("when a token is deleted, it can no longer be used to authenticate"),
and expire automatically after one year. A token is used as the password with
`git` as the username against a per-project Git URL — a standard `git clone` /
`git pull` credential flow, the same shape as any other token-authenticated git
remote. Tokens are account-scoped, not project-scoped: one token works across
every project the account can already access.

**Which plan tiers include it.** Overleaf's current individual plans are **Free**
($0), **Student** ($8.25/mo billed annually, ~$13/mo billed monthly), **Standard**
($16.75/mo annually, ~$25/mo monthly), and **Pro** ($33.25/mo annually, ~$45/mo
monthly, unlimited collaborators). Git integration is listed as included on
**Student, Standard, Pro, and Organizations** — i.e. every paid tier, but **not**
the Free plan. There's also a **Group/Organizations** tier (20+ users, custom
pricing via sales contact; smaller 5+ group purchases are possible for
invoice/PO billing) aimed explicitly at "your team, department, or entire
organization," adding SSO, on-premises/FedRAMP options, and centralized feature
controls that a personal subscription doesn't have.

**Important nuance that materially affects what to ask for.** Overleaf's own
documentation states plainly: *"Git integration is a premium feature, so it will
only be available if the project owner has a paid subscription to Overleaf or has
been granted access to the feature."* This is an **owner-plan** gate, not a
collaborator-plan gate. If the projects this system ingests are each owned by the
individual researchers themselves (with the automation account added as a
collaborator) — which the current setup, where `OVERLEAF_EMAIL` is explicitly a
separate automation login distinct from each researcher's own account, implies —
then **upgrading only the automation account to a personal paid plan may not be
sufficient** to unlock Git access to projects the automation account doesn't own.
This single detail is the strongest argument for the **institutional/group
license** framing over a personal subscription: a group license would put the
researchers themselves (the actual project owners) on paid seats, which is what
Overleaf's own access rule requires for Git integration to work on their
projects. **This should be confirmed with Overleaf support or by testing against
one live project before the budget ask is finalized** — it wasn't independently
verifiable beyond the documentation's own wording in this pass.

**Does Git integration expose the compiled PDF, or only source?** Only source.
Overleaf's own description of Git integration frames it as a translation of
Overleaf's internal history/versioning into a git history of the `.tex` source
tree and project structure — nothing in the documentation indicates compiled PDF
output is synced via git. **This is likely the single detail that determines
whether this migration is a full replacement or a partial one**: this system's
`DataIngestionAgent` currently downloads both the source ZIP *and* the compiled
PDF per project (`data_ingestion_agent.py:15,349-390`), and the compiled PDF is
consumed downstream. A Git migration replaces the source-ZIP leg cleanly but
likely still needs *some* other mechanism (Overleaf's web UI or its separate
compile API) for the compiled artifact, unless downstream agents can be adapted
to compile the `.tex` locally instead of relying on Overleaf's compiled PDF. This
needs a decision, not just an implementation detail — flagged for the operator to
resolve during the actual migration design, not assumed away here.

---

## 3. Concrete impact on this system if migrated

**What changes.** `DataIngestionAgent`'s Overleaf-facing methods
(`check_session_health()`, `_perform_manual_login()`, the browser-driving portion
of `sync_all_projects()`) would be replaced by `git clone`/`git pull` calls
(directly via subprocess, or via a library like `GitPython`) against each
project's git remote, authenticated with a Git token stored in `.env`/`Config` the
same way `OVERLEAF_EMAIL`/`OVERLEAF_PASSWORD` are stored today.

**What goes away entirely:**
- The persisted browser-session state file (`Config.OVERLEAF_STATE_PATH` /
  `overleaf_state.json`).
- `check_session_health()` and the daily "session warming" cron job
  (`check_overleaf_session.py`) built around it.
- The reCAPTCHA-dependent manual re-auth flow — `_perform_manual_login()`,
  `reauth_overleaf.py`, `setup_overleaf_session.py` — and the "wait up to 10
  minutes for a human" logic entirely.
- The human-mimicking delays and stealth browser context building
  (`_human_delay()`, `_human_type()`, `_build_stealth_context()`) — these exist
  solely to reduce anti-bot detection risk for the current scraping approach and
  have no purpose once access is token-authenticated.

**What stays the same or needs adapting.** The ZIP-extraction, file-organization,
and `.tex`-cleaning logic downstream of "get the files" is unaffected — it doesn't
care whether the files arrived via a downloaded ZIP or a git-cloned working
directory (`utils/overleaf_connector.py`'s `read_all_tex_files()` etc. already
operate on a local directory regardless of how it was populated). A git-cloned
project directory maps directly onto the same local `overleaf_projects/<project>/`
layout the rest of the system already expects, with one adjustment: the delta-sync
logic currently compares each project's "last modified" timestamp scraped from
the dashboard UI; under git, "did this project change" becomes a much simpler and
more reliable `git pull` diff/commit-hash comparison instead. The compiled-PDF
question from Section 2 needs its own resolution.

**What does NOT change.** This migration only fixes the Overleaf leg. Two other
automation-dependent integrations remain exactly as fragile as before, and
nothing here addresses them:
- **Google Scholar / literature search** — this is SerpAPI-based (with a
  Semantic Scholar → SerpAPI → `scholarly` library fallback chain), unrelated to
  Overleaf.
- **Stanford's `paperreview.ai` integration** (`agents/research_enhancement_agent.py`)
  — still session-based Playwright browser automation against a third-party
  service, with its own separate fragility and its own ToS posture that hasn't
  been separately checked.

This should be said plainly to the department head: approving this migration
solves Overleaf specifically, not "the automation reliability problem" as a whole.

---

## 4. Migration complexity and rough sizing

**What would need to change, concretely:** `ingestion/data_ingestion_agent.py` is
the entire surface area — no other file drives Overleaf browser automation
directly. Roughly the Overleaf-web-specific portion of that file is the login/
session/browser-context machinery (`_human_delay`, `_human_type`,
`_build_stealth_context`, `check_session_health`, `_perform_manual_login`, and the
browser-driving parts of `sync_all_projects`) — call it a little over half the
file by line count. The delta-detection logic, per-project error isolation,
database bookkeeping (`db.update_sync_registry`, `db.add_project`,
`db.update_project_state`), and admin-alerting calls are reusable regardless of
how files arrive, and would carry over largely unchanged.

**Rough relative sizing:**
- **Ingestion code rewrite itself: small–medium.** One file, a well-bounded
  surface area, and the reusable half of the logic doesn't need to change.
- **Test suite rework: medium–large, not a deletion.** The Overleaf-adjacent test
  files alone total roughly **1,150+ lines** across
  `tests/integration/test_data_ingestion_agent.py`,
  `tests/crash/test_playwright_failures.py`,
  `tests/crash/test_session_state_isolation.py`, and
  `tests/crash/test_alerting_reliability.py`. These currently mock Playwright
  browser/page objects, session-file states, and CAPTCHA-timeout scenarios — all
  of that needs re-authoring around git-clone/token-auth failure modes instead
  (bad token, revoked token, network failure, merge/pull conflicts), not simply
  deleted, since the underlying reliability guarantees (per-project isolation,
  admin alerting, clean degradation) still need equivalent test coverage under
  the new access method.
- **Live validation against the two real test projects: small, but not zero, and
  time-gated by the 2x/week test cadence** — confirming the new path against real
  Overleaf projects before trusting it in the ongoing PQTrace/Udi Aharon test
  phase will take at least a couple of scheduled cycles to build confidence,
  independent of how fast the code itself is written.

**Migration risks worth flagging:**
- **Owner-plan gating (Section 2).** If it turns out the automation account
  can't Git-access researcher-owned projects without those researchers also
  being on a paid seat, the scope of what's being asked for changes from "one
  subscription" to "seats for every project owner" — this should be nailed down
  before, not after, budget approval.
- **Compiled PDF gap (Section 2).** If downstream agents that currently consume
  a compiled PDF can't be adapted to work from source alone, the migration is
  partial and a second mechanism for the compiled artifact needs to be designed
  — this is real added scope, not a footnote.
- **Transition period.** A short window where both access methods may need to
  coexist (finishing the current test phase on the old path while the new path
  is validated) is likely, rather than a hard cutover.
- **Project identity re-mapping.** If git remote URLs identify projects
  differently than the dashboard-scraped project names the system currently uses
  as its primary key (`db.add_project(project_name, ...)`), a one-time mapping
  step may be needed so existing DB records line up with their git-based
  counterparts.

---

## 5. Recommendation

This project's own prior engineering audit already reached a clear conclusion,
independent of cost: **migrate to Overleaf Premium with Git integration.** The
current session-based approach carries two compounding problems — Overleaf's own
Terms of Service prohibit the automated access this pipeline relies on
(account-suspension risk, not just a technical annoyance), and the approach is
operationally fragile regardless of how well it's hardened (a human must
physically solve a CAPTCHA periodically, which cannot be engineered away).
Token-based git authentication removes this entire category of risk rather than
mitigating it — no session state, no reCAPTCHA exposure, no browser fingerprint
sensitivity to manage at all.

This document's job was to ground that recommendation in the current codebase and
current Overleaf pricing/mechanics for the department head's actual budget
decision — not to re-argue the technical point, which was already settled. The
open items that materially affect *what specifically* to ask for (Section 2's
owner-plan-gating question, and whether an institutional/group license is the
correct vehicle rather than a single personal subscription) should be resolved
before the ask is finalized, since they change the shape of the request, not
whether to make it.

---

## Where this document lives

This file is at `docs/overleaf-premium-migration-brief.md` in the working tree
(main checkout — not a separate git worktree). It has not been added, committed,
or pushed to git; it is left as an uncommitted new file for review.
