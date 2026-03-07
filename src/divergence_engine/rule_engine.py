"""
Rule Engine — Specification-pattern classifier for conviction-gated markers.

Evaluates YAML-defined rules in priority order against a MarketContext.
First matching rule wins. All conditions use numeric range gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.divergence_engine.state_types import (
    MarketContext,
    StateName,
)


# ------------------------------------------------------------------
# Specification Predicates
# ------------------------------------------------------------------

@dataclass(frozen=True)
class _Range:
    """Check that a float attribute falls within [lo, hi]."""
    attr: str
    lo: float | None = None
    hi: float | None = None
    def ok(self, ctx: MarketContext) -> bool:
        val = getattr(ctx, self.attr)
        if self.lo is not None and val < self.lo:
            return False
        if self.hi is not None and val > self.hi:
            return False
        return True


# ------------------------------------------------------------------
# Compiled Rule
# ------------------------------------------------------------------

@dataclass
class _CompiledRule:
    name: str
    state: StateName
    priority: int
    predicates: list  # list of predicate objects with .ok(ctx)

    def matches(self, ctx: MarketContext) -> bool:
        return all(p.ok(ctx) for p in self.predicates)


# ------------------------------------------------------------------
# Look-up table for parsing YAML strings -> enums
# ------------------------------------------------------------------

_STATE_MAP: dict[str, StateName] = {s.value: s for s in StateName}


# ------------------------------------------------------------------
# Rule Engine
# ------------------------------------------------------------------

class RuleEngine:
    """Classify a MarketContext using priority-ordered rules from config."""

    def __init__(self, config: dict) -> None:
        self._rules = self._compile(config.get("rules", []), config.get("thresholds", {}))

    def classify(self, ctx: MarketContext) -> StateName:
        """Return the first matching state, or NO_SIGNAL."""
        for rule in self._rules:
            if rule.matches(ctx):
                return rule.state
        return StateName.NO_SIGNAL

    def explain(self, ctx: MarketContext) -> list[dict]:
        """Return a trace showing which rules matched or didn't."""
        results = []
        matched = False
        for rule in self._rules:
            is_match = rule.matches(ctx)
            results.append({
                "priority": rule.priority,
                "state": rule.state.label,
                "matched": is_match,
                "winner": is_match and not matched,
            })
            if is_match and not matched:
                matched = True
        return results

    @staticmethod
    def _compile(raw_rules: list[dict], thresholds: dict) -> list[_CompiledRule]:
        compiled: list[_CompiledRule] = []

        for r in raw_rules:
            name = r["name"]
            state = _STATE_MAP.get(name)
            if state is None:
                raise ValueError(f"Unknown state name in rules: {name!r}")

            conds = r.get("conditions", {})
            preds: list = []

            # All conditions are numeric range gates
            for attr, gate in conds.items():
                if not isinstance(gate, dict):
                    continue
                lo = gate.get("min")
                hi = gate.get("max")
                # Resolve threshold references (strings starting with $)
                if isinstance(lo, str) and lo.startswith("$"):
                    lo = thresholds.get(lo[1:], lo)
                if isinstance(hi, str) and hi.startswith("$"):
                    hi = thresholds.get(hi[1:], hi)
                preds.append(_Range(attr=attr, lo=lo, hi=hi))

            compiled.append(_CompiledRule(
                name=name,
                state=state,
                priority=r.get("priority", 999),
                predicates=preds,
            ))

        compiled.sort(key=lambda r: r.priority)
        return compiled
