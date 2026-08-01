"""Diversity limits (Mode B) and diversity field aliases.

Editable in one place: changing a threshold touches only this module; adding a
raw key spelling touches only ``DIVERSITY_ALIASES``.

All hour-based limits are absolute hours; share limits are percentages of total
delivery hours unless a check notes otherwise.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Diversity canonical fields and their accepted raw keys (canon_key already
# unifies dash/underscore/case, so these list the *semantic* synonyms).
# ---------------------------------------------------------------------------
DIVERSITY_ALIASES = {
    "task_id": ["task-id", "task_id"],
    "operator_id": ["operator-id", "operator_id"],
    "environment_id": ["environment-id", "environment_id"],
    "environment_l1": ["environment_l1", "environment-l1", "environment_category", "l1"],
    "task_difficulty": ["task_difficulty", "task-difficulty", "difficulty"],
    "skill_group": ["skill_group", "skill-group"],
    "business_size": ["business_size", "business_type"],
    "worker_type": ["worker_type", "operator_type"],
    "human_interaction": ["human_interaction", "interaction"],
    "start_time_unix": ["start_time_unix", "start-time-unix"],
    "end_time_unix": ["end_time_unix", "end-time-unix"],
}

# ---------------------------------------------------------------------------
# Difficulty normalization: declared, trusted field. Only spelling is mapped,
# nothing is inferred. Keys are canon_key()-normalized raw tokens.
# ---------------------------------------------------------------------------
DIFFICULTY_SYNONYMS = {
    "easy": "easy", "e": "easy", "1": "easy", "simple": "easy",
    "medium": "medium", "med": "medium", "2": "medium",
    "hard": "hard", "h": "hard", "3": "hard", "difficult": "hard", "skilled": "hard",
}
DIFFICULTY_SEVERITY = {"easy": 1, "medium": 2, "hard": 3}

# business_size normalization -> {small, medium, large}
BUSINESS_SIZE_SYNONYMS = {
    "small": "small", "sm": "small", "s": "small",
    "medium": "medium", "med": "medium", "m": "medium",
    "large": "large", "lg": "large", "l": "large", "big": "large",
}

# worker_type normalization -> {real, contractor}
# Keys are canon_key()-normalized (dashes/underscores/case stripped), so the
# delivery spelling "real_worker" -> "realworker".
WORKER_TYPE_SYNONYMS = {
    "real": "real", "employee": "real", "internal": "real",
    "realworker": "real", "realemployee": "real",
    "contractor": "contractor", "contract": "contractor", "external": "contractor",
    "contractworker": "contractor",
}

# human_interaction truthy / falsy tokens
TRUTHY = {"true", "yes", "y", "1", "t"}
FALSY = {"false", "no", "n", "0", "f"}

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Environment category (L1)
ENV_L1_SINGLE_MAX_PCT = 20.0
ENV_L1_TOP5_MAX_PCT = 60.0
ENV_L1_TOP10_MAX_PCT = 85.0
ENV_L1_MIN_DISTINCT = 15

# Difficulty mix (share of known-difficulty hours)
DIFF_EASY_MAX_PCT = 30.0
DIFF_HARD_MIN_PCT = 5.0
DIFF_MEDIUM_TARGET_PCT = 40.0  # reported as INFO, not enforced

# Per-task share of total hours, by the task's difficulty label
PER_TASK_SHARE_MAX_PCT = {"hard": 6.0, "medium": 4.0, "easy": 1.5}
PER_TASK_ABS_MAX_H = 200.0

# Task x Environment hours, by difficulty
TASK_ENV_MAX_H = {"hard": 40.0, "medium": 20.0, "easy": 10.0}
# Factory pairs override the difficulty cap with a flat cap.
FACTORY_CAP_REPETITIVE_H = 1.0  # default
FACTORY_CAP_DYNAMIC_H = 2.0     # with --factory-dynamic
FACTORY_KEYWORDS = ("factory", "manufactur", "industrial")

# Operator x Task x Environment hours, by difficulty
OP_TASK_ENV_MAX_H = {"hard": 10.0, "medium": 2.0, "easy": 1.0}

# Environment x Operator hours
ENV_OP_MAX_H = 40.0

# Business-size caps per environment_id (requires business_size)
BUSINESS_SIZE_MAX_H = {"small": 500.0, "medium": 2000.0, "large": 5000.0}

# Workforce mix (requires worker_type), by hours
WORKFORCE_REAL_MIN_PCT = 70.0
WORKFORCE_CONTRACTOR_MAX_PCT = 30.0

# Human-human interaction (requires flag), by episode count -- a target (WARN)
HUMAN_INTERACTION_MIN_PCT = 5.0

# Duration reconciliation (§6)
DURATION_ABS_TOL_S = 1.0     # ignore disagreements <= 1 second
DURATION_REL_TOL = 0.02      # ...or <= 2% relative
