"""
Module 1.5 — Market Regime Classification.

Classifies market regime using J. Welles Wilder's ADX and DMI system.
Features robust hysteresis, DI spread thresholds, and dynamic masking.
Outputs four states: 'uptrend', 'downtrend', 'notrend', and 'transition'.

Used by the scoring pipeline to determine signal direction and regime
alignment scoring.
"""

import pandas as pd
import numpy as np
import pandas_ta as ta


def classify_market_regime(
    ohlc: pd.DataFrame,
    adx_length: int = 14,
    adx_trend_entry: float = 25.0,
    adx_trend_exit: float = 20.0,
    min_di_gap: float = 2.0
) -> pd.Series:
    """
    Classifies market regime using J. Welles Wilder's ADX and DMI system.
    Features robust hysteresis, DI spread thresholds, and dynamic masking.
    Outputs four states: 'uptrend', 'downtrend', 'notrend', and 'transition'.
    """
    # ── 1. Defensive Parameter & Data Validation ─────────────────────
    if adx_trend_exit >= adx_trend_entry:
        raise ValueError("Factual Error: adx_trend_exit must be strictly less than adx_trend_entry.")
    if min_di_gap < 0:
        raise ValueError("Factual Error: min_di_gap must be non-negative.")

    if ohlc.columns.nlevels > 1:
        raise TypeError("Factual Error: MultiIndex columns detected. Please flatten the DataFrame.")

    df = ohlc.copy()
    df.columns = [c.lower() for c in df.columns]

    required = {"high", "low", "close"}
    if not required.issubset(set(df.columns)):
        raise ValueError(f"Factual Error: Missing required OHLC columns: {required - set(df.columns)}")

    # ── 2. Indicator Calculation & Safe Join ─────────────────────────
    col_adx = f"ADX_{adx_length}"
    col_dmp = f"DMP_{adx_length}"
    col_dmn = f"DMN_{adx_length}"

    adx_df = df.ta.adx(length=adx_length)
    if adx_df is None or adx_df.empty or col_adx not in adx_df.columns:
        # Insufficient data — return all-NaN regime (will be filled as "notrend" downstream)
        return pd.Series(np.nan, index=ohlc.index, name="regime")

    df = df.join(adx_df)

    # ── 3. Vectorized Hysteresis (Trend Strength State) ──────────────
    df['trend_active'] = np.nan
    df.loc[df[col_adx] >= adx_trend_entry, 'trend_active'] = 1
    df.loc[df[col_adx] <= adx_trend_exit, 'trend_active'] = 0
    df['trend_active'] = df['trend_active'].ffill().fillna(0)

    # ── 4. Direction Logic with Minimum DI Spread ────────────────────
    di_spread = df[col_dmp] - df[col_dmn]

    conditions = [
        # Sideways: Trend strength is strictly low
        (df['trend_active'] == 0),

        # Transition / Coil: High trend strength, but directional spread is ambiguous
        (df['trend_active'] == 1) & (di_spread.abs() < min_di_gap),

        # Uptrend: Strong trend, +DI significantly exceeds -DI
        (df['trend_active'] == 1) & (di_spread >= min_di_gap),

        # Downtrend: Strong trend, -DI significantly exceeds +DI
        (df['trend_active'] == 1) & (di_spread <= -min_di_gap)
    ]

    choices = ["notrend", "transition", "uptrend", "downtrend"]
    df["regime"] = np.select(conditions, choices, default="notrend")

    # ── 5. Gap-Safe Dynamic Warm-up Mask ─────────────────────────────
    # .loc slicing is inclusive of the boundary in pandas.
    # This correctly masks all initial NaNs plus the first un-smoothed valid row.
    first_valid = df[col_adx].first_valid_index()
    if first_valid is not None:
        df.loc[:first_valid, "regime"] = np.nan

    # ── 6. Composable Return ─────────────────────────────────────────
    regime_series = df["regime"]
    regime_series.name = "regime"
    return regime_series
