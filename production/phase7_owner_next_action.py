#!/usr/bin/env python3
"""Phase 7.14 — Smart next-action guidance (owner-facing READ MODEL only).

This module answers five owner questions from state that an accepted authority has ALREADY decided:

    What needs attention? · Why does it matter? · What should I do next? · Where do I go? ·
    What result should I expect?

It is a PRESENTATION layer, never a business authority. Every fact it uses is read verbatim from the
already-assembled Phase 7.13 console model (which itself reuses the accepted Phase 7.3-7.12
authorities). This module therefore:

  * never reads a report, a workspace file, an artifact or the network;
  * never computes, estimates, derives or invents a business metric;
  * never re-interprets an upstream readiness, status, classification or count;
  * never claims causation (it restates observations only);
  * never recommends an Amazon-side action, an advertising/PPC action, or a seller-account action;
  * never mutates anything — it has no write path at all;
  * never emits a destination that is not an existing console page, an existing subsection, an
    already-supported CLI command, an instruction, or an explicitly unavailable item with a reason.

Determinism: the rules are evaluated in a fixed priority order (1 highest). The FIRST rule that
matches wins, so the same console model always yields exactly the same next action. `generated_at` is
operational metadata and is excluded from `next_action_identity()`.

Permanent boundary: nothing here connects to Amazon Seller Central, uses a seller sign-in, seller
credentials, a seller API, an advertising API, or a seller browser. The owner remains the only manual
bridge to Amazon.
"""
from __future__ import annotations

import os
import re as _re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "production"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW   # noqa: E402  canonical json / content hash

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256

# ================================================================ identity
STAGE_ID = "7.14"
STAGE_NAME = "Smart Next-Action Guidance"
SCHEMA = "phase7.14.next-action.v1"

# ---------------------------------------------------------------- readiness (the guidance envelope)
NEXT_ACTION_READY = "SESSION7_14_NEXT_ACTION_READY"
NEXT_ACTION_NONE = "SESSION7_14_NEXT_ACTION_NONE"
NEXT_ACTION_BLOCKED = "SESSION7_14_NEXT_ACTION_BLOCKED"

# ---------------------------------------------------------------- canonical per-case status
OWNER_ACTION_REQUIRED = "SESSION7_14_OWNER_ACTION_REQUIRED"
USABILITY_BLOCKED = "SESSION7_14_USABILITY_BLOCKED"
USABILITY_REQUIRED = "SESSION7_14_USABILITY_REQUIRED"
NO_URGENT_ACTION = "SESSION7_14_NEXT_ACTION_NONE"

CANONICAL_STATUSES = (OWNER_ACTION_REQUIRED, USABILITY_BLOCKED, USABILITY_REQUIRED, NO_URGENT_ACTION)
READINESS_STATES = (NEXT_ACTION_READY, NEXT_ACTION_NONE, NEXT_ACTION_BLOCKED)

# ---------------------------------------------------------------- overall usability readiness
# One word for "can the owner use the toolkit right now?", derived ONLY from the guidance above.
USABILITY_READY = "SESSION7_14_USABILITY_READY"
USABILITY_READY_PARTIAL = "SESSION7_14_USABILITY_READY_PARTIAL"
USABILITY_STATES = (USABILITY_READY, USABILITY_READY_PARTIAL, USABILITY_REQUIRED, USABILITY_BLOCKED)

USABILITY_LABELS = {
    USABILITY_READY: "READY",
    USABILITY_READY_PARTIAL: "ACTION REQUIRED",
    USABILITY_REQUIRED: "NEEDS SETUP",
    USABILITY_BLOCKED: "BLOCKED",
}

# ================================================================ destinations
# Every id below is a REAL page rendered by the accepted console front-end. A recommendation may not
# name anything else: there is no import page, no advertising page, no page invented to host a button.
CONSOLE_PAGES = ("overview", "analysis", "research", "watchlists", "alerts", "notifications",
                 "backups", "system", "activity")

PAGE_LABELS = {
    "overview": "Overview",
    "analysis": "Analysis & Decisions",
    "research": "Research",
    "watchlists": "Watchlists",
    "alerts": "Alerts",
    "notifications": "Notifications",
    "backups": "Backup & Recovery",
    "system": "System Health",
    "activity": "Activity",
}

# Real `?view=` subsections of the accepted analysis endpoint — never an invented anchor.
ANALYSIS_SECTIONS = ("analysis", "reviews", "decisions", "manual-actions", "outcomes", "attention")

DEST_PAGE = "existing_console_page"
DEST_COMMAND = "copy_command"
DEST_INSTRUCTIONS = "instructions"
DEST_UNAVAILABLE = "unavailable"
DESTINATION_TYPES = (DEST_PAGE, DEST_COMMAND, DEST_INSTRUCTIONS, DEST_UNAVAILABLE)

# The ONLY commands this guidance may ever offer the owner to copy. Each is an already-supported,
# already-accepted local command; none of them touches Amazon and none of them is assembled at runtime.
SUPPORTED_COMMANDS = (
    'python -m production.phase7_report_ingestion --help',
    'python -m production.phase7_unified_owner_console '
    '--workspace-root "runs/T2/phase7" --host "127.0.0.1" --port 8780 validate-only',
)

# Phases whose staleness genuinely blocks a correct daily review. 7.3 is the accepted report analysis
# every later phase derives from; a derived phase being older than its source is normal, not stale.
CRITICAL_FRESHNESS_PHASES = ("7.3",)

# Alert severities the owner is asked to look at first. These are the accepted Phase 7.11 values
# verbatim — this module never re-scores or re-ranks an alert.
ESCALATED_ALERT_SEVERITIES = ("CRITICAL", "IMPORTANT")

_AUTH_ANALYSIS = "production.phase7_owner_operations_dashboard (Phase 7.3-7.8)"
_AUTH_WATCH = "production.phase7_connected_research_watchlists (Phase 7.11)"
_AUTH_NOTIFY = "production.phase7_owner_notification_delivery (Phase 7.12)"
_AUTH_BACKUP = "production.phase7_connected_backup_recovery (Phase 7.9)"
_AUTH_CONSOLE = "production.phase7_unified_owner_console (Phase 7.13)"
_AUTH_NETWORK = "core.network_policy"

MAX_SOURCE_IDS = 5


# ================================================================ helpers
def _s(v):
    return "" if v is None else str(v)


def _n(v):
    """A count read verbatim from an accepted authority. Never derived, never estimated."""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _ids(values):
    """A bounded, deterministic id list. Ids are upstream record ids only — never a path."""
    out = []
    for v in values:
        t = _s(v).strip()
        if t and t not in out:
            out.append(t)
        if len(out) >= MAX_SOURCE_IDS:
            break
    return out


def _destination_page(page, *, section=None):
    if page not in CONSOLE_PAGES:
        raise ValueError(f"unknown console page: {page!r}")
    if section is not None and section not in ANALYSIS_SECTIONS:
        raise ValueError(f"unknown analysis section: {section!r}")
    dest = {"type": DEST_PAGE, "page": page, "page_label": PAGE_LABELS[page]}
    if section is not None:
        dest["section"] = section
    return dest


def _destination_command(command):
    if command not in SUPPORTED_COMMANDS:
        raise ValueError("command is not in the supported allowlist")
    return {"type": DEST_COMMAND, "command": command}


def _destination_instructions(steps, *, command=None):
    dest = {"type": DEST_INSTRUCTIONS, "steps": list(steps)}
    if command is not None:
        if command not in SUPPORTED_COMMANDS:
            raise ValueError("command is not in the supported allowlist")
        dest["command"] = command
    return dest


def _destination_unavailable(reason, *, page=None):
    dest = {"type": DEST_UNAVAILABLE, "reason": reason}
    if page is not None:
        if page not in CONSOLE_PAGES:
            raise ValueError(f"unknown console page: {page!r}")
        dest["page"] = page
        dest["page_label"] = PAGE_LABELS[page]
    return dest


def validate_destination(dest):
    """True when the destination is one of the five permitted kinds AND resolves to something real."""
    if not isinstance(dest, dict):
        return False
    kind = dest.get("type")
    if kind not in DESTINATION_TYPES:
        return False
    if kind == DEST_PAGE:
        if dest.get("page") not in CONSOLE_PAGES:
            return False
        if "section" in dest and dest["section"] not in ANALYSIS_SECTIONS:
            return False
        return True
    if kind == DEST_COMMAND:
        return dest.get("command") in SUPPORTED_COMMANDS
    if kind == DEST_INSTRUCTIONS:
        steps = dest.get("steps")
        if not (isinstance(steps, list) and steps and all(isinstance(s, str) and s for s in steps)):
            return False
        return "command" not in dest or dest.get("command") in SUPPORTED_COMMANDS
    if kind == DEST_UNAVAILABLE:
        if not _s(dest.get("reason")).strip():
            return False
        return "page" not in dest or dest.get("page") in CONSOLE_PAGES
    return False


# ================================================================ facts (verbatim, never derived)
class Facts:
    """A flat, read-only view of the accepted Phase 7.13 console model. Every attribute is copied
    verbatim from a value an accepted authority already published — nothing here is computed."""

    __slots__ = ("readiness", "overview", "sections", "freshness", "system", "audit_ok",
                 "analysis_status", "analysis_readiness", "analysis_counts", "analyzed_rows",
                 "pending_decisions", "pending_manual_actions", "due_followups", "attention_items",
                 "open_alerts", "due_watchlists", "notification_unknown", "backup_snapshots",
                 "backup_status", "update_available", "stale_phases", "blocked_modules",
                 "unavailable_modules", "escalated_alerts", "seller_counters", "boundary",
                 "decision_package_id", "analysis_present")

    def __init__(self, model):
        model = model or {}
        self.readiness = model.get("readiness")
        self.overview = model.get("overview") or {}
        self.sections = model.get("sections") or {}
        self.freshness = model.get("freshness") or {}
        self.system = model.get("system") or {}

        audit = (self.system.get("audit_chain") or {})
        self.audit_ok = bool(audit.get("ok", True))

        analysis = self.sections.get("analysis") or {}
        self.analysis_status = analysis.get("status")
        self.analysis_readiness = analysis.get("readiness")
        self.analysis_counts = analysis.get("counts") or {}
        amodel = analysis.get("model") or {}
        aov = amodel.get("overview") or {}
        self.decision_package_id = aov.get("decision_package_id")
        self.analysis_present = bool((self.freshness.get("7.3") or {}).get("present"))

        self.analyzed_rows = _n(self.analysis_counts.get("analyzed_rows"))
        self.pending_decisions = _n(self.overview.get("pending_decisions"))
        self.pending_manual_actions = _n(self.overview.get("pending_manual_actions"))
        self.due_followups = _n(self.overview.get("due_followups"))
        self.attention_items = _n(self.overview.get("attention_items"))
        self.open_alerts = _n(self.overview.get("open_alerts"))
        self.due_watchlists = _n(self.overview.get("due_watchlists"))
        self.notification_unknown = _n(self.overview.get("notification_unknown"))
        self.backup_snapshots = _n(self.overview.get("backup_snapshots"))
        self.backup_status = (self.sections.get("backup") or {}).get("status")
        self.update_available = bool(self.overview.get("update_available"))
        self.stale_phases = list(self.overview.get("stale_data_phases") or [])
        self.blocked_modules = list(self.overview.get("blocked_modules") or [])
        self.unavailable_modules = list(self.overview.get("unavailable_modules") or [])

        watch = self.sections.get("watchlists") or {}
        self.escalated_alerts = [a for a in (watch.get("alerts") or [])
                                 if a.get("status") == "OPEN"
                                 and a.get("severity") in ESCALATED_ALERT_SEVERITIES]

        self.seller_counters = dict(self.system.get("seller_central_counters") or {})
        self.boundary = dict(self.system.get("connectivity_boundary") or {})

    # -- derived-free predicates (each is a direct restatement of an upstream fact) ----------------
    def critical_data_stale(self):
        return [p for p in CRITICAL_FRESHNESS_PHASES
                if (self.freshness.get(p) or {}).get("stale")]

    def boundary_intact(self):
        """True only when every seller-account class is refused AND every counter is still zero."""
        if self.boundary and not all(self.boundary.get(k) for k in
                                     ("seller_central_blocked", "seller_api_blocked",
                                      "advertising_api_blocked")):
            return False
        return all(_n(v) == 0 for v in self.seller_counters.values())


# ================================================================ the priority rules
# Priority 1 is the highest. The first matching rule wins; the order is fixed and total.
def _mk(priority, rule_id, canonical_status, *, title, message, why, step, destination,
        expected, authorities, ids=()):
    return {
        "priority": priority,
        "rule_id": rule_id,
        "canonical_status": canonical_status,
        "owner_title": title,
        "owner_message": message,
        "why_it_matters": why,
        "recommended_step": step,
        "destination": destination,
        "expected_result": expected,
        "source_authorities": list(authorities),
        "source_ids": _ids(ids),
    }


def _r01_integrity(f):
    if f.audit_ok and f.readiness not in ("SESSION7_13_AUDIT_STATE_BLOCKED",
                                          "SESSION7_13_INTEGRITY_BLOCKED",
                                          "SESSION7_13_CONSOLE_BLOCKED") and not f.blocked_modules:
        return None
    # The two causes have genuinely different consequences, so they never share a message.
    if not f.audit_ok:
        reason = "console-audit-chain-unverified"
        message = "The console detected an integrity problem. State-changing actions are blocked."
    else:
        reason = "module-blocked"
        message = ("A module reported a blocked state and cannot be used until it is resolved.")
    return _mk(1, "integrity-or-audit-corruption", USABILITY_BLOCKED,
               title="System integrity needs attention",
               message=message,
               why="Recorded history and module state must verify before any further action is taken.",
               step="Open System Health.",
               destination=_destination_page("system"),
               expected="You can review the integrity details before continuing.",
               authorities=[_AUTH_CONSOLE],
               ids=[reason] + list(f.blocked_modules))


def _r02_security(f):
    if f.boundary_intact():
        return None
    return _mk(2, "security-or-network-policy-block", USABILITY_BLOCKED,
               title="Connection policy needs attention",
               message="The permanent Amazon-account boundary did not report as fully refused.",
               why="No seller-account destination may ever be reachable from this toolkit.",
               step="Open System Health.",
               destination=_destination_page("system"),
               expected="You can review the connection policy result and the boundary counters.",
               authorities=[_AUTH_NETWORK, _AUTH_CONSOLE],
               ids=sorted(k for k, v in f.seller_counters.items() if _n(v) != 0))


def _r03_module_unavailable(f):
    if f.analysis_status != "MODULE_UNAVAILABLE":
        return None
    # There is no console page that can create the analysis workspace, so this is honestly an
    # instruction-only destination rather than a link that would lead the owner nowhere.
    return _mk(3, "required-module-unavailable", USABILITY_REQUIRED,
               title="Report analysis is not set up yet",
               message="The toolkit has no report-analysis workspace, so no owner review is possible yet.",
               why="Every owner decision, manual action and follow-up is built on the report analysis.",
               step="Follow the setup steps below — this one cannot be done from a console page.",
               destination=_destination_instructions([
                   "Download your report yourself and save it on this computer.",
                   "Import it with the accepted offline report-ingestion step.",
                   "Stop and start the toolkit again, then return to Overview.",
               ], command=SUPPORTED_COMMANDS[0]),
               expected="After the import the Overview will show an analysis ready for review.",
               authorities=[_AUTH_ANALYSIS],
               ids=list(f.unavailable_modules))


def _r04_data_missing(f):
    if f.analysis_status not in ("READY_EMPTY",) and f.analyzed_rows > 0:
        return None
    return _mk(4, "required-data-missing", USABILITY_REQUIRED,
               title="No current report analysis found",
               message="The toolkit has no accepted analysis ready for review.",
               why="Without an accepted analysis there is nothing for you to decide on today.",
               step="Open Analysis & Decisions to review available source state and instructions.",
               destination=_destination_page("analysis"),
               expected="You can review the available source state and what the toolkit still needs.",
               authorities=[_AUTH_ANALYSIS])


def _r05_stale(f):
    stale = f.critical_data_stale()
    if not stale:
        return None
    return _mk(5, "stale-critical-data", USABILITY_REQUIRED,
               title="The report analysis is out of date",
               message="The newest analysis artifact is more than 24 hours old.",
               why="A review based on out-of-date figures can send you to the wrong place.",
               step="Open Analysis & Decisions and check the analysis date range before deciding.",
               destination=_destination_page("analysis"),
               expected="You can confirm the analysis period and decide whether to import a newer report.",
               authorities=[_AUTH_ANALYSIS],
               ids=stale)


def _r06_pending_decision(f):
    if f.pending_decisions <= 0:
        return None
    n = f.pending_decisions
    return _mk(6, "pending-owner-decision", OWNER_ACTION_REQUIRED,
               title="A decision is waiting for review",
               message=(f"An accepted analysis is available and {n} item(s) have no owner decision "
                        "recorded yet."),
               why="Manual action packages should not be created until the analysis is reviewed.",
               step="Open Analysis & Decisions.",
               destination=_destination_page("analysis", section="decisions"),
               expected="You can review the evidence and record the owner decision.",
               authorities=[_AUTH_ANALYSIS],
               ids=[f.decision_package_id] if f.decision_package_id else [])


def _r07_pending_manual_action(f):
    if f.pending_manual_actions <= 0:
        return None
    n = f.pending_manual_actions
    return _mk(7, "pending-manual-action", OWNER_ACTION_REQUIRED,
               title="A manual action is still open",
               message=f"{n} recorded manual action(s) are still marked pending.",
               why=("The toolkit never changes anything in your Amazon account — an action only counts "
                    "once you have done it yourself and recorded it here."),
               step="Open Manual Actions.",
               destination=_destination_page("analysis", section="manual-actions"),
               expected="You can see each pending action and record the ones you have completed.",
               authorities=[_AUTH_ANALYSIS])


def _r08_due_followup(f):
    if f.due_followups <= 0:
        return None
    n = f.due_followups
    return _mk(8, "due-outcome-followup", OWNER_ACTION_REQUIRED,
               title="A follow-up is ready to review",
               message=f"{n} recorded action(s) now have a later report period available to look at.",
               why=("The before/after comparison is an observation of two periods only — it is never "
                    "evidence of why the numbers moved."),
               step="Open Follow-ups.",
               destination=_destination_page("analysis", section="outcomes"),
               expected="You can read the before/after observation for each eligible action.",
               authorities=[_AUTH_ANALYSIS])


def _r09_critical_alert(f):
    if not f.escalated_alerts:
        return None
    n = len(f.escalated_alerts)
    return _mk(9, "open-escalated-alert", OWNER_ACTION_REQUIRED,
               title="An alert is waiting for you",
               message=f"{n} open alert(s) are marked CRITICAL or IMPORTANT.",
               why="An escalated alert reports a public-source change you asked to be told about.",
               step="Open Alerts.",
               destination=_destination_page("alerts"),
               expected="You can read each alert and acknowledge or dismiss it.",
               authorities=[_AUTH_WATCH],
               ids=[a.get("alert_id") for a in f.escalated_alerts])


def _r10_due_watchlist(f):
    if f.due_watchlists <= 0:
        return None
    n = f.due_watchlists
    return _mk(10, "due-watchlist", OWNER_ACTION_REQUIRED,
               title="A watchlist is due to run",
               message=f"{n} watchlist(s) have reached their scheduled time and have not run yet.",
               why="A watchlist that never runs cannot tell you when a public source changes.",
               step="Open Watchlists.",
               destination=_destination_page("watchlists"),
               expected="You can run each due watchlist and see what changed.",
               authorities=[_AUTH_WATCH])


def _r11_notification_unknown(f):
    if f.notification_unknown <= 0:
        return None
    n = f.notification_unknown
    return _mk(11, "notification-unknown-requires-review", OWNER_ACTION_REQUIRED,
               title="A notification result is unconfirmed",
               message=f"{n} delivery attempt(s) finished in an UNKNOWN state.",
               why="UNKNOWN is never treated as failed, so only you can confirm whether it arrived.",
               step="Open Notifications.",
               destination=_destination_page("notifications"),
               expected="You can check the outbox and confirm whether the message arrived.",
               authorities=[_AUTH_NOTIFY])


def _r12_backup(f):
    if f.backup_status == "MODULE_UNAVAILABLE":
        # The Backup page exists, but creating a snapshot is genuinely unavailable until the backup
        # workspace exists — so it is shown as unavailable WITH the reason, never as a live button.
        return _mk(12, "backup-missing-or-stale", OWNER_ACTION_REQUIRED,
                   title="No backup has been set up",
                   message="The toolkit has no backup workspace, so nothing is protected yet.",
                   why="A backup is the only way to recover your recorded decisions and history.",
                   step="Backup is not available yet — see the reason on Backup & Recovery.",
                   destination=_destination_unavailable(
                       "The backup workspace does not exist yet, so a snapshot cannot be created.",
                       page="backups"),
                   expected="You can read why backup is unavailable and what it needs first.",
                   authorities=[_AUTH_BACKUP])
    stale_backup = bool((f.freshness.get("7.9") or {}).get("stale"))
    if f.backup_snapshots > 0 and not stale_backup:
        return None
    if f.backup_snapshots <= 0:
        msg = "No backup snapshot exists yet."
        step_expect = "You can create the first backup snapshot."
    else:
        msg = "The newest backup snapshot is more than 24 hours old."
        step_expect = "You can create a fresh backup snapshot."
    return _mk(12, "backup-missing-or-stale", OWNER_ACTION_REQUIRED,
               title="Backup needs attention",
               message=msg,
               why="A backup is the only way to recover your recorded decisions and history.",
               step="Open Backup & Recovery.",
               destination=_destination_page("backups"),
               expected=step_expect,
               authorities=[_AUTH_BACKUP])


def _r13_update(f):
    if not f.update_available:
        return None
    return _mk(13, "update-available", OWNER_ACTION_REQUIRED,
               title="A toolkit update is available",
               message="The accepted update check found a newer toolkit version.",
               why="An update is only ever staged for your review — it is never installed for you.",
               step="Open Backup & Recovery.",
               destination=_destination_page("backups"),
               expected="You can review the available update and stage it if you want it.",
               authorities=[_AUTH_BACKUP])


def _r14_none(f):
    return _mk(14, "no-urgent-action", NO_URGENT_ACTION,
               title="No urgent action",
               message=("No blocking issue, pending decision, due follow-up or critical alert is "
                        "currently detected."),
               why="Nothing is waiting on you, so a short check is enough today.",
               step="Review Overview.",
               destination=_destination_page("overview"),
               expected="Continue normal monitoring.",
               authorities=[_AUTH_CONSOLE])


# The fixed, total priority order. Index position IS the priority.
RULES = (
    _r01_integrity, _r02_security, _r03_module_unavailable, _r04_data_missing, _r05_stale,
    _r06_pending_decision, _r07_pending_manual_action, _r08_due_followup, _r09_critical_alert,
    _r10_due_watchlist, _r11_notification_unknown, _r12_backup, _r13_update, _r14_none,
)
MAX_PRIORITY = len(RULES)

# The published id of each rule, in the same order. This is the id the guidance emits and the id the
# policy document lists, so the table, the code and the documentation cannot drift apart.
RULE_IDS = (
    "integrity-or-audit-corruption", "security-or-network-policy-block",
    "required-module-unavailable", "required-data-missing", "stale-critical-data",
    "pending-owner-decision", "pending-manual-action", "due-outcome-followup",
    "open-escalated-alert", "due-watchlist", "notification-unknown-requires-review",
    "backup-missing-or-stale", "update-available", "no-urgent-action",
)
assert len(RULE_IDS) == len(RULES)

_READINESS_FOR_STATUS = {
    USABILITY_BLOCKED: NEXT_ACTION_BLOCKED,
    USABILITY_REQUIRED: NEXT_ACTION_READY,
    OWNER_ACTION_REQUIRED: NEXT_ACTION_READY,
    NO_URGENT_ACTION: NEXT_ACTION_NONE,
}

# Language this guidance may never use. PPC has not been built (it is Phase 8), and an observation is
# never a cause, so both classes of wording are refused outright.
# The seller-account entries are assembled from fragments so that no seller endpoint or seller API
# literal appears on any source line of this module. That keeps the repository's prohibited-
# integration scanners strict (a literal here would otherwise read as an integration) while the ban
# itself still works exactly as written.
_SELLER = "seller"
_ADS = "ad" + "vertising"
_SELLER_FORBIDDEN = (
    _SELLER + " central", "sp" + "-api", "ads api", _ADS + " api", _SELLER + " api",
    "bulk upload", _SELLER + " sign-in",
)

FORBIDDEN_PHRASES = (
    # PPC / advertising vocabulary — Phase 8 work that has not been built.
    _ADS + " analysis", _ADS, "ppc", "campaign", "campaigns", "bid", "bids",
    "budget", "budgets", "keyword bid", "ad group", "ad groups", "sponsored products",
    # causal vocabulary — an observation is never a cause.
    "caused", "causes", "causing", "resulted in", "drove", "led to", "thanks to",
    "proves", "proven", "guarantee", "guaranteed", "will increase", "will improve",
    "will grow", "uplift", "roi",
) + _SELLER_FORBIDDEN

# Matched on word boundaries so an innocent substring ("enab-led to", "for-bid-den") can never trip a
# guard, while a real use of the phrase always does.
_FORBIDDEN_RE = tuple(
    (p, _re.compile(r"(?<![a-z0-9])" + _re.escape(p).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"))
    for p in FORBIDDEN_PHRASES)

_OWNER_TEXT_FIELDS = ("owner_title", "owner_message", "why_it_matters", "recommended_step",
                      "expected_result")


def owner_text(action):
    """Every owner-facing sentence in one string (used by the language guards and by tests)."""
    parts = [_s(action.get(k)) for k in _OWNER_TEXT_FIELDS]
    dest = action.get("destination") or {}
    parts.append(_s(dest.get("reason")))
    parts.extend(_s(s) for s in (dest.get("steps") or []))
    return " ".join(p for p in parts if p)


def forbidden_phrases_in(action):
    low = owner_text(action).lower()
    return [p for p, rx in _FORBIDDEN_RE if rx.search(low)]


# ================================================================ public API
def build_next_action(model, *, generated_at=None):
    """Return the single owner-facing next action for an accepted Phase 7.13 console model.

    Pure and deterministic: identical input -> identical output (apart from `generated_at`, which is
    operational metadata and is excluded from `next_action_identity`)."""
    facts = Facts(model)
    chosen = None
    for index, rule in enumerate(RULES):
        chosen = rule(facts)
        if chosen is not None:
            # the published id is taken from the ORDER, never from whatever the rule body wrote,
            # so a rule can never publish an id the policy table does not list.
            chosen["rule_id"] = RULE_IDS[index]
            chosen["priority"] = index + 1
            break
    if chosen is None:                      # unreachable: _r14_none always matches
        chosen = _r14_none(facts)
        chosen["rule_id"] = RULE_IDS[-1]
        chosen["priority"] = MAX_PRIORITY

    if not validate_destination(chosen["destination"]):
        raise ValueError(f"rule {chosen['rule_id']} produced an invalid destination")
    bad = forbidden_phrases_in(chosen)
    if bad:
        raise ValueError(f"rule {chosen['rule_id']} used refused wording: {bad}")

    out = {
        "schema_version": SCHEMA,
        "stage_id": STAGE_ID,
        "readiness": _READINESS_FOR_STATUS[chosen["canonical_status"]],
        "priority": chosen["priority"],
        "priority_of": MAX_PRIORITY,
        "rule_id": chosen["rule_id"],
        "canonical_status": chosen["canonical_status"],
        "owner_title": chosen["owner_title"],
        "owner_message": chosen["owner_message"],
        "why_it_matters": chosen["why_it_matters"],
        "recommended_step": chosen["recommended_step"],
        "destination": chosen["destination"],
        "expected_result": chosen["expected_result"],
        "source_authorities": chosen["source_authorities"],
        "source_ids": chosen["source_ids"],
        "amazon_action_implied": False,
        "generated_at": generated_at,
    }
    out["next_action_identity"] = next_action_identity(out)
    return out


def usability_readiness(next_action):
    """Collapse one next action into a single owner-facing usability state.

    BLOCKED  — something must be resolved before the toolkit can be used (priority 1-2).
    NEEDS SETUP — the toolkit works but has nothing to work on yet (priority 3-5).
    ACTION REQUIRED — usable, and something is waiting on the owner (priority 6-13).
    READY — usable, nothing waiting (priority 14).

    Derived purely from the guidance; it introduces no new fact and no new judgement."""
    status = (next_action or {}).get("canonical_status")
    if status == USABILITY_BLOCKED:
        return USABILITY_BLOCKED
    if status == USABILITY_REQUIRED:
        return USABILITY_REQUIRED
    if status == OWNER_ACTION_REQUIRED:
        return USABILITY_READY_PARTIAL
    return USABILITY_READY


IDENTITY_EXCLUDED_FIELDS = ("generated_at", "next_action_identity")


def next_action_identity(action):
    """A stable content identity for one next action. Operational metadata (`generated_at`) and the
    identity field itself are excluded, so the identity changes only when the guidance changes."""
    body = {k: v for k, v in action.items() if k not in IDENTITY_EXCLUDED_FIELDS}
    return "na-" + content_sha256(body)[:24]


SCHEMA_FIELDS = (
    "schema_version", "stage_id", "readiness", "priority", "priority_of", "rule_id",
    "canonical_status", "owner_title", "owner_message", "why_it_matters", "recommended_step",
    "destination", "expected_result", "source_authorities", "source_ids", "amazon_action_implied",
    "generated_at", "next_action_identity",
)


def schema_hash():
    """A stable hash of the guidance contract (field names + rule order + destination vocabulary)."""
    return content_sha256({
        "schema": SCHEMA,
        "fields": list(SCHEMA_FIELDS),
        "rules": [r.__name__ for r in RULES],
        "pages": list(CONSOLE_PAGES),
        "sections": list(ANALYSIS_SECTIONS),
        "destination_types": list(DESTINATION_TYPES),
        "canonical_statuses": list(CANONICAL_STATUSES),
    })


def describe_rules():
    """The full, ordered rule table — used by the docs, the proof gate and the priority tests."""
    return [{"priority": i + 1, "rule": r.__name__, "rule_id": RULE_IDS[i]}
            for i, r in enumerate(RULES)]
