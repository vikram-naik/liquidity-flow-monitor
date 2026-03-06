# Prompt: Feature Discovery for XGBoost Divergence Engine

---

## Context

You are working inside the repository `liquidity-flow-monitor` on the branch
`feature/divergence-engin`. A separate architecture document
(`divergence_engine_architecture.md`) defines the planned XGBoost pipeline.

Your job is **not** to build anything yet. Your job is to **audit the existing
codebase** and produce a definitive inventory of every computed variable that
could serve as a feature input to the XGBoost model described in the
architecture document.

---

## Instructions

### Step 1 — Read the architecture document first
Read `divergence_engine_architecture.md` in full before touching any code.
Pay specific attention to the section titled **"Feature Engineer"** which lists
the *intended* features. Treat that list as a reference target, not a
constraint — the codebase may have more, fewer, or differently named variables.

---

### Step 2 — Systematically walk the entire codebase

For **every Python file** in `src/` and `scripts/`:

1. Open the file and read it completely.
2. Identify every variable, column, or computed value that represents a
   **market indicator, ratio, slope, or derived metric** — anything that
   describes price behaviour, volume behaviour, or their relationship.
3. Note the **exact variable name** as it appears in the code.
4. Note the **file and function** where it is computed.
5. Note the **timeframe** it operates on (daily, weekly, monthly, rolling N days)
   if determinable from the code.
6. Note the **data source** it depends on (OHLCV, delivery volume, synthetic
   NIFTY50, external macro data, etc).

Do not skip any file. Do not summarise vaguely. Read the actual computation.

---

### Step 3 — Classify each variable

For each variable found, assign it one of these classifications:

| Class | Meaning |
|-------|---------|
| `READY` | Computed daily per stock, directly usable as a feature row value |
| `NEEDS_LAG` | Computed but only as a scalar today — needs lag/slope engineering to be useful |
| `NEEDS_RESHAPE` | Computed at weekly or monthly granularity — needs `merge_asof` to flatten onto daily spine |
| `AGGREGATE_ONLY` | Computed only at index/portfolio level (e.g. synthetic NIFTY50) — needs per-stock disaggregation or use as a market-context feature |
| `EXCLUDE` | Administrative, identifier, or non-predictive column (dates, symbols, raw OHLCV prices) |

---

### Step 4 — Identify gaps against the architecture target

Cross-reference your inventory against the intended feature list in
`divergence_engine_architecture.md`. For each intended feature, state:

- `EXISTS` — variable is present in code, note the exact name and file
- `PARTIAL` — a related variable exists but needs transformation (explain what)
- `MISSING` — not present anywhere in the codebase, needs to be built

---

### Step 5 — Produce the output report

Output a single structured report in the following format. Do not write any
code. Do not modify any files. Only produce this report.

```
═══════════════════════════════════════════════════════
DIVERGENCE ENGINE — FEATURE INVENTORY REPORT
═══════════════════════════════════════════════════════

SECTION A — FULL VARIABLE INVENTORY
────────────────────────────────────
For each variable found:

  Variable Name   : <exact name from code>
  File            : <filename>
  Function        : <function name>
  Timeframe       : <daily / weekly / monthly / rolling-Nd>
  Data Source     : <OHLCV / delivery / synthetic / macro>
  Classification  : <READY / NEEDS_LAG / NEEDS_RESHAPE / AGGREGATE_ONLY / EXCLUDE>
  Notes           : <any computation detail worth flagging>

────────────────────────────────────
SECTION B — GAP ANALYSIS vs ARCHITECTURE DOCUMENT
────────────────────────────────────
For each intended feature from the architecture doc:

  Intended Feature    : <name from architecture doc>
  Status              : <EXISTS / PARTIAL / MISSING>
  Code Variable Name  : <if EXISTS or PARTIAL>
  Action Required     : <if PARTIAL or MISSING, describe exactly what needs building>

────────────────────────────────────
SECTION C — SUMMARY COUNTS
────────────────────────────────────
  Total variables found         :
  READY for feature engineering :
  NEEDS_LAG                     :
  NEEDS_RESHAPE                 :
  AGGREGATE_ONLY                :
  EXCLUDED                      :

  Architecture targets — EXISTS  :
  Architecture targets — PARTIAL :
  Architecture targets — MISSING :

────────────────────────────────────
SECTION D — RECOMMENDED FEATURE LIST (FIRST PASS)
────────────────────────────────────
Based on what exists TODAY in the codebase (no new builds), list the variables
that could be assembled into a first feature matrix with minimal engineering.
Order them by classification: READY first, then NEEDS_LAG, then NEEDS_RESHAPE.
For each, note the single transformation required if any.
═══════════════════════════════════════════════════════
```

---

## Constraints

- Do not write or modify any code.
- Do not make assumptions about variable meaning — read the actual computation.
- If a variable's timeframe or data source is ambiguous from the code alone,
  mark it as `AMBIGUOUS` and note the specific line of uncertainty.
- If you find variables not mentioned anywhere in the architecture document
  that look potentially useful as features, include them in Section A with a
  note: `UNLISTED — candidate for review`.
- The report is the only deliverable. Nothing else.