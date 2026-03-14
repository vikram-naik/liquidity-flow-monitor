---
name: Supply Signal Dynamics
description: Root cause analysis of Supply underperformance in CEI value-exit strategy — price crosses CWVAP immediately, MFE giveback, asymmetric exit rules
type: project
---

Supply CEI signals underperform Demand significantly in value-exit testing.

**Why:** 72% of Supply trades see price cross above CWVAP on bar 1 (median). MFE peaks at bar 3 (+1.80%) but compound exit (CEI > 0 AND price > CWVAP) triggers at bar 9 (median), by which point the trade is -1.77%. Median giveback: 4.42%. Root cause is structural — distribution is sharp/sudden while CEI (EMA-smoothed) lags.

**How to apply:**
- Supply entries gated to below-CWVAP only (bearish context, gravity works for you)
- Supply uses CEI-only exit (CEI > 0), not compound exit — improved hit from 22.8% to 37.8%
- Still giving back too much (MFE +4.43%, exit -0.43%) — needs further tuning
- Holding period analysis: trades held 20+ bars are excellent (58-92% hit), short holds lose
- Study stocks visually (HDFCLIFE, others) to find patterns for tighter exits
