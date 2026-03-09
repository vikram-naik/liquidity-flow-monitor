"""Thin wrapper — canonical implementation lives in src/divergence_engine/regime.py."""

import pandas as pd
import numpy as np

from src.divergence_engine.regime import classify_market_regime  # noqa: F401

# ─────────────────────────────────────────────
# Execution Demo
# ─────────────────────────────────────────────
if __name__ == "__main__":
    np.random.seed(42)
    dates = pd.date_range("2020-01-01", periods=1000, freq="B")

    returns = np.random.normal(0.0005, 0.015, 1000)
    closes = 100 * np.exp(np.cumsum(returns))

    test_df = pd.DataFrame(index=dates)
    test_df["close"] = closes
    test_df["open"]  = test_df["close"].shift(1).fillna(closes[0])

    # EWMA std() mathematically returns NaN for index 0 (requires N>=2).
    # fillna() is strictly required here.
    vol = pd.Series(returns).ewm(span=20).std().fillna(0.015)
    volatility_noise = test_df["close"] * vol.values * 0.1

    test_df["high"]  = test_df[["open", "close"]].max(axis=1) + volatility_noise
    test_df["low"]   = test_df[["open", "close"]].min(axis=1) - volatility_noise

    test_df["regime"] = classify_market_regime(test_df)

    print(test_df[["close", "high", "low", "regime"]].tail(10))
