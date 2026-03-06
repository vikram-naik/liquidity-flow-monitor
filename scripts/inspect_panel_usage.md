# Inspect Panel Usage

Inspect panel feature values for a given symbol and date. Useful for configuring
the label generator and understanding the engine signals at specific price action events.

## Usage

```bash
venv/bin/python3 scripts/inspect_panel.py --symbol SYMBOL --date YYYY-MM-DD [options]
```

## Parameters

| Parameter | Default | Description |
|---|---|---|
| `--symbol` | required | Stock symbol (e.g. RELIANCE) |
| `--date` | required | Date to inspect (YYYY-MM-DD) |
| `--context` | `0` | Rows before/after the target date to show (0 = single row) |
| `--group` | None | Show a predefined feature group (see below) |
| `--cols` | None | Comma-separated column names to display |
| `--panel` | `data/panel.parquet` | Path to panel parquet file |

## Feature Groups (`--group`)

| Group | Key Columns |
|---|---|
| `price` | open, high, low, close, volume, delivery_qty, delivery_pct |
| `atr` | true_high, true_low, tr, atr_20 |
| `cwvap` | cwvap, cwvap_dist, cwvap_slope, cwvap_slope_norm, cwvap lags and slopes |
| `cpoc` | cpoc, cpoc_dist, poc_spread, cvah, cval, va_width, price_location |
| `dvl` | dvl_10/30/60/120, dvl_rate_10/30/60/120 |
| `rdv` | rdv, rdv_slope_z, rdv_slope_3d/5d/10d, rdv_consistency |
| `mcs` | mcs, mcs_mfm, mcs_delta, mcs_composite, mcs_composite_slope |
| `mfm` | mfm, mfm_slope_3d/10d, mfm_vs_10d_avg, mfm_acceleration |
| `coherence` | coherence, price_slope_z, rdv_slope_z, slope angles, coherence trends |
| `velocity` | velocity_10/30/60/120 and normalised variants |
| `pdd` | pdd_10/30/60/120, pdd_weighted_avg, price_distance_10/30/60/120 |
| `setup` | cwvap_dist, rdv, mcs_composite, coherence, mfm, all_aligned, setup_duration |
| `dvwap` | dvwap_10/30/60/120 |
| `poc` | poc_10/30/60/120 |
| `cwc` | cwc, c_10_30, c_30_60, c_60_120, cwc_delta, cwc_slope, gradient_shape |

## Examples

### All features for a single date (vertical table)

```bash
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04
```

### Setup-relevant features with 5 rows of context

```bash
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 --context 5
```
> When `--context` is set without `--group` or `--cols`, the `setup` group is shown by default.

### Feature group with context

```bash
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 --group cwvap --context 5
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 --group rdv --context 3
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-04-07 --group setup --context 5
```

### Custom columns

```bash
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 \
    --cols close,cwvap,cwvap_dist,atr_20,rdv,mfm,mcs_composite --context 3
```

### Compare two dates side by side (run twice)

```bash
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 --group setup
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-04-07 --group setup
```

### Use a different panel file

```bash
venv/bin/python3 scripts/inspect_panel.py --symbol RELIANCE --date 2025-03-04 \
    --panel data/panel_dev.parquet
```

## Output Format

- **Single row** (`--context 0`): vertical two-column table of `Feature | Value`
- **With context**: horizontal table, one row per date, target row marked with `◄`

## Notes

- If the exact date is not found, the script prints the nearest available date and exits.
- Symbol lookup is case-insensitive (automatically uppercased).
- Numbers ≥ 1000 are formatted with commas; floats use 4 decimal places.
