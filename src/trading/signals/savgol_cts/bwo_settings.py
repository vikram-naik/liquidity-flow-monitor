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
