"""ROADMAP H04 (2.16.0): daily model-call budget (expand phase).

Adds the second budget facet beside the Phase 19.4 turn budget:

- ``tenants.daily_model_call_budget`` — the per-tenant daily cap on *governed
  model transports* (triage, language detect/translate, summaries, copilot);
  ``NULL`` means unlimited, which is the default, so a deployment that never
  configures it sees zero behaviour change;
- ``tenant_usage_daily.model_call_count`` — the counter the gate reserves one
  unit against *before* every transport and releases when the transport
  raises. The turn counter keeps its own denomination (turns) and is not
  touched: it feeds the billing export and the admin display.

Expand-only: both statements are additive columns with defaults.
"""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(
    45, "Tenant daily model-call budget and reservation counter (ROADMAP H04)", phase="expand"
)
def migrate(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "tenants", "daily_model_call_budget", "INTEGER")
    _ensure_column(
        connection, "tenant_usage_daily", "model_call_count", "INTEGER NOT NULL DEFAULT 0"
    )
