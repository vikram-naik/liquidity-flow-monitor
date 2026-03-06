# Label Generator Usage

Zone-aware directional Triple Barrier labeling for supervised learning targets.

## How It Works

1. **Zone classification** — each row is classified as `demand` (close below CWVAP) or `supply` (close above CWVAP) based on the configured distance band.
2. **Directional barrier** — only the reversal direction is checked:
   - Demand zone → check upper barrier only
   - Supply zone → check lower barrier only
3. **RDV conviction gate** (optional) — require raw relative delivery volume above a threshold to ensure institutional participation.
4. **Cooldown dedup** — suppress overlapping labels within N rows per symbol.

## Label Modes

### `direction` (default)
- Barrier hit → `STRONG_UP` (demand zone) or `STRONG_DOWN` (supply zone)
- Timeout → row dropped (not included in output)
- Use when training a model to predict **direction** of the next move

### `followthrough`
- Barrier hit → `FOLLOW_THROUGH`
- Timeout → `TIMEOUT`
- Both classes retained in output — significantly more training rows
- Use when training a model to predict **setup quality** (will this zone setup follow through?)
- Direction is known from the zone at inference time — the model learns conviction, not direction
- Adds `barrier_direction` column (`up` / `down`) for debugging and inference

## Parameters

| Parameter | Default | Description |
|---|---|---|
| `--input` | `data/panel.parquet` | Input panel file |
| `--output` | `data/labeled_panel.parquet` | Output labeled panel file |
| `--label-mode` | `direction` | `direction` (STRONG_UP/DOWN) or `followthrough` (FOLLOW_THROUGH/TIMEOUT) |
| `--profit-mult` | `2.0` | Upper barrier = close + N x ATR |
| `--stop-mult` | `2.0` | Lower barrier = close - N x ATR |
| `--horizon` | `10` | Max forward trading days to check barriers |
| `--cooldown` | `10` | Min rows between labels per symbol |
| `--no-cwvap-filter` | OFF | Disable CWVAP zone filter (bidirectional mode) |
| `--cwvap-atr-min` | `0.0` | Min distance from CWVAP in ATR units |
| `--cwvap-atr-max` | `3.5` | Max distance from CWVAP in ATR units |
| `--cwvap-pct-min` | None | Min distance as % of close (e.g. 0.0). Both pct args must be set. |
| `--cwvap-pct-max` | None | Max distance as % of close (e.g. 0.05). Both pct args must be set. |
| `--min-rdv` | None | Min raw relative delivery volume at entry (e.g. 1.0) |

## Usage Examples

### Direction mode (default)

```bash
# Default run
venv/bin/python3 scripts/label_generator.py

# Recommended: shorter horizon + RDV conviction gate
venv/bin/python3 scripts/label_generator.py --horizon 5 --cooldown 5 --min-rdv 1.0
```

### Followthrough mode

```bash
# Predict setup quality instead of direction
venv/bin/python3 scripts/label_generator.py --label-mode followthrough --horizon 5 --cooldown 5 --min-rdv 1.0

# Tighter zone band for higher-quality setups
venv/bin/python3 scripts/label_generator.py --label-mode followthrough --horizon 5 --cooldown 5 \
    --cwvap-atr-min 1.0 --cwvap-atr-max 3.5 --min-rdv 1.0
```

### Zone tuning (works with both modes)

```bash
# Tight near-CWVAP setups only (within 1.5x ATR)
venv/bin/python3 scripts/label_generator.py --cwvap-atr-max 1.5

# Deep mean-reversion only (2-4x ATR from CWVAP)
venv/bin/python3 scripts/label_generator.py --cwvap-atr-min 2.0 --cwvap-atr-max 4.0

# Fixed % distance mode (0-5% from CWVAP)
venv/bin/python3 scripts/label_generator.py --cwvap-pct-min 0.0 --cwvap-pct-max 0.05

# Strict: deep discount/premium entries with institutional delivery
venv/bin/python3 scripts/label_generator.py --cwvap-atr-min 1.5 --cwvap-atr-max 4.0 --min-rdv 1.2

# No CWVAP filter (original bidirectional behavior)
venv/bin/python3 scripts/label_generator.py --no-cwvap-filter
```

### Verify labels after generation

```bash
# Basic checks (match --cooldown to what was used during generation)
venv/bin/python3 scripts/verify_labels.py --cooldown 5

# Per-symbol detail with barrier direction
venv/bin/python3 scripts/verify_labels.py --cooldown 5 --level2 --symbol RELIANCE
venv/bin/python3 scripts/verify_labels.py --cooldown 5 --level2 --symbol RELIANCE,HDFCBANK,TCS

# Inspect features around a specific labeled date
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-02-28 --group cwvap --context 5
```

## Outputs

| File | Description |
|---|---|
| `data/labeled_panel.parquet` | Panel with `label` column + `barrier_direction` column |
| `data/label_stats.json` | Row counts, class distribution, and all params used |

### Label values by mode

| Mode | Positive class | Negative class | Timeout handling |
|---|---|---|---|
| `direction` | `STRONG_UP` | `STRONG_DOWN` | Dropped |
| `followthrough` | `FOLLOW_THROUGH` | `TIMEOUT` | Kept as negative class |
