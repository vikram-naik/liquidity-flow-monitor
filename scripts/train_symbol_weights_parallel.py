"""
Thin wrapper for scripts/train_symbol_weights.py.
Forwards execution and exports key functions for backward compatibility.
"""
import sys
from pathlib import Path

# Add root folder to sys.path
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from scripts.train_symbol_weights import (
    FEATURES,
    SL_HIT_PENALTY,
    simulate_single_trade,
    label_candidate_bars_sim,
    train_bayesian_model,
    evaluate_config_partitioned,
    optimize_single_symbol,
    main,
)

if __name__ == "__main__":
    main()
