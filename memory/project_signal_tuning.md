---
name: Signal system tuning results (2026-03-15)
description: Backtesting findings — regime gate, soft filters, trail width, EOD lag, PSZ momentum failure
type: project
---

Regime hard gate (block downtrend) filters out profitable trades (+1.52% avg in downtrend) — removed.

CWC and gradient_shape soft filters are inverted: OFF outperforms ON. Not used as gates.
RDV has marginal positive edge (+1.62% ON vs +1.24% OFF). MCS slight edge. Both tracked, not gated.

Trail ATR widened from 1.0 to 2.0, activation from 0.5 to 1.0 ATR. Shifts primary exit from trail_atr (63%) to psz_reversal (41%). Winners run longer.

EOD lag implemented: signal on bar close, enter next day close. Realistic execution model.

PSZ momentum failure exit added: if PSZ peaks below 0.20 and drops 0.10 from peak after 3+ bars, exit early. Catches 16% of trades at -2.23% avg vs -6.11% for hard stop.

Hard stop at 1.5 ATR tested but doubles hard stop volume (376→718) — kept at 2.0 ATR.

**Why:** Payoff ratio target is 1:3. These changes moved from 2.24x to 2.64x.
**How to apply:** New baseline params: `--trail-atr 2.0 --trail-activation 1.0 --no-regime-gate --min-filters 0 --max-bars 9999`
