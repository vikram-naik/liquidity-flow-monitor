---
name: CTS+PSZ Signal Strategy
description: CWVAP Trend Score indicator and CTS+PSZ backtest results — accel entry, slope exit validated on NIFTY 50
type: project
---

## CWVAP Trend Score (CTS)
- Computed in `cwvap.py` as average of ATR-normalized EMA pair spreads (5/8, 8/14, 14/21)
- Range [-1, +1]: +1 strongly rising, 0 sideways, -1 falling
- Derivatives: `cts_slope` (5-bar linreg), `cts_accel` (slope of slope)

## CTS+PSZ Strategy (validated 2026-03-16)
- **Entry**: PSZ crosses -0.25 upward AND cts_accel >= 0 (inflection detected)
- **Exit**: cts_slope < -0.015 (trend turning) OR hard stop (2 ATR) OR time_decay (60 bars)
- **Best config** (PSZ=-0.25, exit slope=-0.015): 1,746 trades, 37.9% WR, +1.63% avg, 3.00x payoff
- Outperforms old 7-rule exit system on payoff ratio (3.00x vs 2.64x)

**Why:** PSZ detects price momentum inflection, CTS confirms CWVAP institutional trend alignment. Together they filter dud periods where CWVAP is falling.

**How to apply:** CTS logic is in `scripts/backtest_cts_psz.py` — needs to be wired into `src/trading/signals.py` for live scanning. The backtest script is standalone, not yet integrated.

## Key Finding: Exit Slope Threshold
- Slope exit at exactly 0 is too sensitive (16% WR, 4 bar avg hold)
- Sweet spot is -0.015: lets winners breathe, cuts at meaningful reversal
- Time-decay exits (60 bar max) averaged +15.66% — these are strong runners being cut short
