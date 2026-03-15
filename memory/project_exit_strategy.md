---
name: CEI Threshold Exit Strategy
description: Signal_Exit is net-destructive; CEI threshold 0.08 implemented as primary exit gate — 4x baseline P&L
type: project
---

Signal_Exit (Supply/Supply_Assister-triggered exit) is net-destructive: correct only 46% at 10d, 94% from Supply_Assister cutting winners.

**Why:** CEI magnitude serves as a natural filter — weak counter-signals (|CEI| < 0.08) are noise; strong ones (|CEI| >= 0.08) indicate genuine regime change. CWVAP trailing stop handles breakdowns independently.

**How to apply:** CEI threshold 0.08 is now the default exit gate in `backtest_cei.py`. Old filters (MinHold, VA Primary-only) are disabled by default — they cost -42K P&L when combined with CEI threshold. NIFTY 50 result: +443,188 P&L (4x baseline).
