---
name: exit-efficiency-optimization
description: Workflow for identifying exit timing issues (profit givebacks) and optimizing exit routines using PnL vs MFE analysis. Use when a trade has high "runners" that retrace before exiting.
---

# Exit Efficiency Optimization Skill

This skill provides a standardized workflow for diagnosing and fixing "profit givebacks"—scenarios where a trade captures a significant move (High MFE) but the exit routine is too sluggish, resulting in low or negative realized PnL.

## 1. Diagnose the Efficiency Gap

To find out if an exit routine is underperforming, run the trade dump script sorted by PnL:

```bash
./venv/bin/python scripts/dump_trades.py --entry <entry-alias> --sort pnl
```

**What to look for:**
- **MFE vs PnL Gap:** Identify trades where `MFE%` is high (e.g., > 12%) but `PnL%` is low (e.g., < 2%) or negative. These are "runners" that were allowed to retrace completely.
- **Duration (Bars):** Check if trades are staying open too long after the `MFE%` was likely reached.
- **Exit Reasons:** Audit the `Exit Reason` column. If a large cluster of winners is exiting via a lagging trend crossover (e.g., `CTS crossed ST down`), the logic is too smoothed for fast-spiking runners.

## 2. Audit Structural Guards

Review the exit logic in `src/trading/signals/savgol_cts/exits/universal_cross.py`. All entries now route through common CWVAP guards in `exits/cwvap_guard.py` unless explicitly bypassed.

- **Global Safety**: The Universal Cross path is subject to global safety exits (candlestick wicks at resistance, volume spikes, structural climax) by default.
- **Bypass**: If a trade is giving back profits but not exiting, verify if the CWVAP guard is suppressing the exit due to momentum strength.

## 3. Optimization Options

Propose and test these fixes in order of complexity:

### Option A: Standardized Threshold Tuning (Simplest)
Adjust `cts_sell_threshold` in `config.py` for the Universal Cross exit path.
- **Pros:** Tightens the trailing stop for all trades globally.
- **Cons:** Might increase "churn" and decrease Average PnL if too tight.

### Option B: Hard PnL Cap
Add a fixed profit target (e.g., 10.0%) to the `UniversalCrossExitConfig`.
- **Fix**: Update `exit_universal_cross` to trigger `ExitReason.PNL_CAP_HIT` if `pnl >= cfg.pnl_cap_pct`.
- **Validation**: Best for setups that mean-revert rapidly after a peak.

### Option C: Fast Engine Exhaustion
Implement a secondary, faster momentum engine check (e.g., FAS) in the exit logic.
- **Logic**: Once a trade is in profit (> 2.5%), monitor if the fast momentum (`fas`) collapses below a threshold (e.g., `-0.1`).
- **Fix**: Exit if `pnl > 2.5 and fas < -0.1`.


## 4. UI Parity Constraint (Mandatory)

When adding or using state-dependent exit logic (e.g., relying on `trade.mfe_pct` or a new state counter like `cwf_count`):

**You MUST update `tag_signals()` in `src/trading/signals/base.py`.**

The UI/Charts visualization dynamically runs `tag_signals()` to mark exits. If this method doesn't track and update the new state variable bar-by-bar, the UI simulation will produce incorrect exit points that don't match the backtester, regardless of how many times the cache is flushed.

## 5. Validation Routine

After applying a fix, you MUST validate that you haven't over-fitted the data:
1. Re-run `scripts/dump_trades.py` and compare the "Before" vs "After" MFE/PnL for the same trades.
2. Run the `walk_forward.py` backtester on the **TEST period** and ensure the **Profit Factor** and **Expectancy** improved without a massive drop in trade count.
