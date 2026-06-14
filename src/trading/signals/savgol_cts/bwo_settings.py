import os

# Bayesian Weight Optimization (BWO) Centralized Settings

# Penalty coefficient for stop-loss hits during optimization score calculation
SL_HIT_PENALTY = float(os.getenv("BWO_SL_HIT_PENALTY", "-250.0"))

# Safety gate minimum trades required for a model to be valid
MIN_TRADES = int(os.getenv("BWO_MIN_TRADES", "3"))

# Safety gate maximum stop-loss hits ratio allowed (25% max SL ratio)
MAX_SL_RATIO = float(os.getenv("BWO_MAX_SL_RATIO", "0.25"))

# Safety gate default target P&L requirement
TARGET_PNL_DEFAULT = float(os.getenv("BWO_TARGET_PNL_DEFAULT", "5.0"))

# Symbols exempted from the 5% target P&L safety gate (can accept >= 0.0 P&L)
ZERO_TARGET_PNL_SYMBOLS = [
    s.strip().upper() 
    for s in os.getenv("BWO_ZERO_TARGET_PNL_SYMBOLS", "ETERNAL,INFY,JIOFIN").split(",") 
    if s.strip()
]

# Trade count capping for score calculation
TRADE_CAP_SCORE = int(os.getenv("BWO_TRADE_CAP_SCORE", "15"))

# Reward factor per trade up to the trade cap
TRADE_REWARD_COEFF = float(os.getenv("BWO_TRADE_REWARD_COEFF", "10.0"))

# Stop-loss MAE threshold defining an SL hit (percentage drawdown)
SL_MAE_THRESHOLD = float(os.getenv("BWO_SL_MAE_THRESHOLD", "8.0"))

# BWO training period start date filter
BWO_START_DATE = os.getenv("BWO_START_DATE", "2019-01-01")

# Score delta required to promote Challenger over existing Champion
CHALLENGER_PROMOTION_DELTA = float(os.getenv("BWO_CHALLENGER_PROMOTION_DELTA", "0.5"))

import json

BWO_SYMBOL_PARAMS_PATH = os.getenv("BWO_SYMBOL_PARAMS_PATH", os.path.join(os.path.dirname(__file__), "bwo_symbol_params.json"))

def get_bwo_settings_for_symbol(symbol: str) -> dict:
    symbol = symbol.strip().upper()
    
    # Base defaults
    settings = {
        "SL_HIT_PENALTY": SL_HIT_PENALTY,
        "MIN_TRADES": MIN_TRADES,
        "MAX_SL_RATIO": MAX_SL_RATIO,
        "TARGET_PNL": 0.0 if symbol in ZERO_TARGET_PNL_SYMBOLS else TARGET_PNL_DEFAULT,
        "TRADE_CAP_SCORE": TRADE_CAP_SCORE,
        "TRADE_REWARD_COEFF": TRADE_REWARD_COEFF,
        "SL_MAE_THRESHOLD": SL_MAE_THRESHOLD,
        "BWO_START_DATE": BWO_START_DATE,
        "CHALLENGER_PROMOTION_DELTA": CHALLENGER_PROMOTION_DELTA,
        "NUM_BINS_CHOICES": [3, 4],
        "SUCCESS_MULT_CHOICES": [1.2, 1.5, 1.8],
        "DD_LIMIT_CHOICES": [5.0, 6.0, 7.0]
    }
    
    if os.path.exists(BWO_SYMBOL_PARAMS_PATH):
        try:
            with open(BWO_SYMBOL_PARAMS_PATH, "r") as f:
                overrides = json.load(f)
            if isinstance(overrides, dict) and symbol in overrides:
                sym_overrides = overrides[symbol]
                if isinstance(sym_overrides, dict):
                    # Update settings with overrides
                    for k, v in sym_overrides.items():
                        k_upper = k.upper()
                        if k_upper in settings:
                            if k_upper in ["MIN_TRADES", "TRADE_CAP_SCORE"]:
                                settings[k_upper] = int(v)
                            elif k_upper in [
                                "SL_HIT_PENALTY", "MAX_SL_RATIO", "TARGET_PNL", 
                                "TRADE_REWARD_COEFF", "SL_MAE_THRESHOLD", 
                                "CHALLENGER_PROMOTION_DELTA"
                            ]:
                                settings[k_upper] = float(v)
                            elif k_upper == "BWO_START_DATE":
                                settings[k_upper] = str(v)
                            elif k_upper in ["NUM_BINS_CHOICES", "SUCCESS_MULT_CHOICES", "DD_LIMIT_CHOICES"]:
                                if isinstance(v, list):
                                    settings[k_upper] = v
        except Exception as e:
            print(f"Error loading symbol BWO overrides from {BWO_SYMBOL_PARAMS_PATH}: {e}")
            
    return settings

