"""
Configuration Manager for the Integrated State Matrix.

Loads factory defaults from YAML, merges with user overrides stored
in the ``user_settings`` database table, and provides factory-reset.
"""

from __future__ import annotations

import json
import os
import copy
from pathlib import Path
from typing import Any

import yaml


_CONFIG_DIR = Path(__file__).parent / "config"
_DEFAULTS_PATH = _CONFIG_DIR / "default_rules.yaml"

# Database key used to store threshold overrides
_DB_KEY = "state_rules_overrides"


def _load_yaml(path: Path) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def load_defaults() -> dict:
    """Return the factory-default config (thresholds + rules)."""
    return _load_yaml(_DEFAULTS_PATH)


def get_config(db_conn=None) -> dict:
    """Return the effective config: defaults merged with user overrides.

    Parameters
    ----------
    db_conn : sqlite3.Connection, optional
        If provided, reads user overrides from the database.
        If None, returns factory defaults only.
    """
    config = load_defaults()

    if db_conn is not None:
        overrides = _read_db_overrides(db_conn)
        if overrides:
            # Merge: user thresholds override defaults, rules stay factory
            config["thresholds"] = {**config["thresholds"], **overrides}

    return config


def save_user_config(db_conn, thresholds: dict[str, Any]) -> dict:
    """Persist user threshold overrides to the database.

    Only stores thresholds that differ from factory defaults.
    Returns the resulting effective config.
    """
    defaults = load_defaults()["thresholds"]

    # Only store overrides that actually differ from defaults
    overrides = {
        k: v for k, v in thresholds.items()
        if k in defaults and v != defaults[k]
    }

    if overrides:
        db_conn.execute(
            "INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)",
            (_DB_KEY, json.dumps(overrides)),
        )
    else:
        # All values match defaults — remove the override row
        db_conn.execute(
            "DELETE FROM user_settings WHERE key = ?", (_DB_KEY,)
        )

    db_conn.commit()
    return get_config(db_conn)


def factory_reset(db_conn) -> dict:
    """Delete all user overrides and return factory defaults."""
    db_conn.execute("DELETE FROM user_settings WHERE key = ?", (_DB_KEY,))
    db_conn.commit()
    return load_defaults()


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _read_db_overrides(db_conn) -> dict | None:
    """Read user threshold overrides from the user_settings table."""
    cursor = db_conn.execute(
        "SELECT value FROM user_settings WHERE key = ?", (_DB_KEY,)
    )
    row = cursor.fetchone()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None
    return None
