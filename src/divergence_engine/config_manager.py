"""
Configuration Manager for the Divergence Engine.

Supports v2 (unified scoring) config schema.
Loads factory defaults from YAML, merges with user overrides stored
in the ``user_settings`` database table, and provides factory-reset.
"""

from __future__ import annotations

import json
import copy
from pathlib import Path
from typing import Any

import yaml


_CONFIG_DIR = Path(__file__).parent / "config"
_DEFAULTS_PATH = _CONFIG_DIR / "default_rules.yaml"

# Database key used to store user overrides
_DB_KEY = "state_rules_overrides"


def _load_yaml(path: Path) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def load_defaults() -> dict:
    """Return the factory-default config."""
    return _load_yaml(_DEFAULTS_PATH)


def get_config(db_conn=None) -> dict:
    """Return the effective config: defaults merged with user overrides.

    For v2 schema, user overrides can modify:
    - settings (min_signal_strength)
    - factor weights (factor_name.weight.demand / .weight.supply)
    """
    config = load_defaults()

    if db_conn is not None:
        overrides = _read_db_overrides(db_conn)
        if overrides:
            _apply_v2_overrides(config, overrides)

    return config


def get_flat_config(db_conn=None) -> dict:
    """Return a flat key-value representation for the settings UI.

    Flattens the v2 hierarchical config into slider-friendly keys:
    - min_signal_strength: 40
    - weight_cwc_demand: 0.20
    - weight_cwc_supply: 0.20
    - ...
    """
    config = get_config(db_conn)
    flat = {}

    # Settings
    settings = config.get("settings", {})
    flat["min_signal_strength"] = settings.get("min_signal_strength", 40)

    # Factor weights
    for fname, fcfg in config.get("factors", {}).items():
        weights = fcfg.get("weight", {})
        for direction in ("demand", "supply"):
            flat[f"weight_{fname}_{direction}"] = weights.get(direction, 0.0)

    return flat


def get_flat_defaults() -> dict:
    """Return flat defaults (same shape as get_flat_config but from YAML only)."""
    defaults = load_defaults()
    flat = {}

    settings = defaults.get("settings", {})
    flat["min_signal_strength"] = settings.get("min_signal_strength", 40)

    for fname, fcfg in defaults.get("factors", {}).items():
        weights = fcfg.get("weight", {})
        for direction in ("demand", "supply"):
            flat[f"weight_{fname}_{direction}"] = weights.get(direction, 0.0)

    return flat


def save_user_config(db_conn, flat_overrides: dict[str, Any]) -> dict:
    """Persist user overrides to the database.

    Accepts flat keys (same shape as get_flat_config output).
    Only stores values that differ from factory defaults.
    Returns the resulting effective config.
    """
    defaults_flat = get_flat_defaults()

    # Only store overrides that actually differ from defaults
    overrides = {
        k: v for k, v in flat_overrides.items()
        if k in defaults_flat and v != defaults_flat[k]
    }

    if overrides:
        db_conn.execute(
            "INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)",
            (_DB_KEY, json.dumps(overrides)),
        )
    else:
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
# Factor UI metadata (for auto-generating settings)
# ------------------------------------------------------------------

def get_factors_ui_meta(db_conn=None) -> list[dict]:
    """Return factor metadata for the UI settings panel.

    Each entry includes: factor name, label, description, current weight,
    and slider constraints.
    """
    config = get_config(db_conn)
    factors = config.get("factors", {})
    meta = []

    for fname, fcfg in factors.items():
        weights = fcfg.get("weight", {})
        ui = fcfg.get("ui", {})
        meta.append({
            "factor": fname,
            "label": ui.get("label", fname),
            "description": ui.get("description", ""),
            "format": ui.get("format", ".2f"),
            "weight_demand": weights.get("demand", 0.0),
            "weight_supply": weights.get("supply", 0.0),
        })

    return meta


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _apply_v2_overrides(config: dict, overrides: dict) -> None:
    """Apply flat user overrides to hierarchical v2 config (in-place)."""
    # Settings overrides
    if "min_signal_strength" in overrides:
        config.setdefault("settings", {})["min_signal_strength"] = overrides["min_signal_strength"]

    # Weight overrides: weight_{factor}_{direction}
    factors = config.get("factors", {})
    for key, val in overrides.items():
        if key.startswith("weight_"):
            parts = key.split("_", 1)[1]  # remove "weight_"
            # Find the factor name — try longest match first
            for fname in sorted(factors.keys(), key=len, reverse=True):
                if parts.startswith(fname + "_"):
                    direction = parts[len(fname) + 1:]
                    if direction in ("demand", "supply"):
                        factors[fname].setdefault("weight", {})[direction] = val
                    break


def _read_db_overrides(db_conn) -> dict | None:
    """Read user overrides from the user_settings table."""
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
