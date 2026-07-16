#!/usr/bin/env python3
"""
core.status — the ONE source of truth for statuses and exit codes (v2).

Every module imports statuses and exit codes from here so behaviour is consistent
and a BLOCKED result can never accidentally exit 0. Missing data is INCOMPLETE,
never treated as negative evidence.
"""
from __future__ import annotations

# ---- universal stage statuses ------------------------------------------------
NOT_STARTED        = "NOT_STARTED"
INCOMPLETE         = "INCOMPLETE"
READY_FOR_REVIEW   = "READY_FOR_REVIEW"
GO                 = "GO"
TEST               = "TEST"
REVISE             = "REVISE"
OWNER_REVIEW       = "OWNER_REVIEW"
APPROVED           = "APPROVED"
SKIP               = "SKIP"
BLOCKED            = "BLOCKED"
PUBLISHED_MANUALLY = "PUBLISHED_MANUALLY"
MONITORING         = "MONITORING"
SCALE              = "SCALE"
OPTIMIZE           = "OPTIMIZE"
PAUSE              = "PAUSE"
KILL               = "KILL"
ARCHIVED           = "ARCHIVED"

ALL_STATUSES = {
    NOT_STARTED, INCOMPLETE, READY_FOR_REVIEW, GO, TEST, REVISE, OWNER_REVIEW,
    APPROVED, SKIP, BLOCKED, PUBLISHED_MANUALLY, MONITORING, SCALE, OPTIMIZE,
    PAUSE, KILL, ARCHIVED,
}

# domain-specific gate-outcome statuses (valid results some gates return)
VERIFIED, COMPLIANT, COMPLIANT_DRAFT, CONSISTENT = "VERIFIED","COMPLIANT","COMPLIANT_DRAFT","CONSISTENT"
PARTIALLY_PROVEN, PROVEN, OK = "PARTIALLY_PROVEN","PROVEN","OK"
GATE_OUTCOME_STATUSES = {VERIFIED, COMPLIANT, COMPLIANT_DRAFT, CONSISTENT, PARTIALLY_PROVEN, PROVEN, OK}
ALL_STATUSES = ALL_STATUSES | GATE_OUTCOME_STATUSES

# statuses that must never allow a project to reach publication-ready
HARD_STOP = {BLOCKED, SKIP, KILL}

# ---- standardized process exit codes -----------------------------------------
EXIT_OK              = 0   # completed successfully / approved
EXIT_SOFTWARE_ERROR  = 1   # unexpected software error
EXIT_INCOMPLETE      = 2   # incomplete data
EXIT_REVIEW          = 3   # review required
EXIT_BLOCKED_SAFETY  = 4   # blocked by safety, IP, compliance or false claim
EXIT_BLOCKED_ECON    = 5   # blocked by economics or fulfillment
EXIT_INVALID_INPUT   = 6   # invalid input file
EXIT_NO_APPROVAL     = 7   # owner approval missing

# map a status -> the exit code a CLI should use
_STATUS_EXIT = {
    GO: EXIT_OK, APPROVED: EXIT_OK,
    PUBLISHED_MANUALLY: EXIT_OK, SCALE: EXIT_OK, MONITORING: EXIT_OK,
    INCOMPLETE: EXIT_INCOMPLETE,
    READY_FOR_REVIEW: EXIT_REVIEW,   # v2.3.1: review-required is NOT success (was EXIT_OK)
    TEST: EXIT_REVIEW, REVISE: EXIT_REVIEW, OWNER_REVIEW: EXIT_REVIEW,
    BLOCKED: EXIT_BLOCKED_SAFETY,
    SKIP: EXIT_BLOCKED_ECON,
}

def exit_code_for(status: str, *, economics: bool = False, needs_approval: bool = False) -> int:
    """Return the correct process exit code for a status.
    economics=True routes a BLOCKED/SKIP to the economics code (5).
    needs_approval=True routes to 7 when approval is the blocker."""
    if needs_approval:
        return EXIT_NO_APPROVAL
    if status in (BLOCKED, SKIP) and economics:
        return EXIT_BLOCKED_ECON
    return _STATUS_EXIT.get(status, EXIT_SOFTWARE_ERROR)

def is_hard_stop(status: str) -> bool:
    return status in HARD_STOP
