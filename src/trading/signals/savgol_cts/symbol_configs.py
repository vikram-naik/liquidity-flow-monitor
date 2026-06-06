# Auto-generated symbol-specific configuration overrides for Custom Bayesian Entries (BW)
import copy
import os
import json
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
from src.database import BW_CONFIGS_DIR

_BW_OVERRIDE_CACHE = {}  # maps symbol -> (mtime, config_dict)

def load_bw_override(symbol: str) -> dict | None:
    json_path = os.path.join(BW_CONFIGS_DIR, f"{symbol}.json")
    if not os.path.exists(json_path):
        if symbol in _BW_OVERRIDE_CACHE:
            del _BW_OVERRIDE_CACHE[symbol]
        return None
        
    try:
        mtime = os.path.getmtime(json_path)
        if symbol in _BW_OVERRIDE_CACHE:
            cached_mtime, cached_data = _BW_OVERRIDE_CACHE[symbol]
            if cached_mtime == mtime:
                return cached_data
                
        with open(json_path, "r") as f:
            data = json.load(f)
            
        # Parse infinities in custom_bayesian feature bins and weights
        if "custom_bayesian" in data:
            cb = data["custom_bayesian"]
            if "feature_bins" in cb:
                parsed_bins = {}
                for feat, bins in cb["feature_bins"].items():
                    parsed_bins[feat] = [
                        float("-inf") if b == "-inf" else (float("inf") if b == "inf" else float(b))
                        for b in bins
                    ]
                cb["feature_bins"] = parsed_bins
                
            if "feature_weights" in cb:
                parsed_weights = {}
                for feat, weights in cb["feature_weights"].items():
                    parsed_weights[feat] = [
                        (
                            float("-inf") if left == "-inf" else (float("inf") if left == "inf" else float(left)),
                            float("-inf") if right == "-inf" else (float("inf") if right == "inf" else float(right)),
                            float(w)
                        )
                        for left, right, w in weights
                    ]
                cb["feature_weights"] = parsed_weights
                
        _BW_OVERRIDE_CACHE[symbol] = (mtime, data)
        return data
    except Exception as e:
        print(f"Error loading BW config for {symbol} from {json_path}: {e}")
        if symbol in _BW_OVERRIDE_CACHE:
            del _BW_OVERRIDE_CACHE[symbol]
        return None

def get_symbol_entry_config(symbol: str, default_cfg: SavgolCTSEntryConfig | None = None) -> SavgolCTSEntryConfig:
    if default_cfg is None:
        cfg = SavgolCTSEntryConfig()
    else:
        cfg = copy.deepcopy(default_cfg)
        
    overrides = load_bw_override(symbol)
    if overrides:
        if 'trend_pullback_enabled' in overrides:
            cfg.trend_pullback_enabled = overrides['trend_pullback_enabled']
        for path in ['cdvl_cts', 'universal_cross', 'flow_momentum', 'coherent_pullback', 'anchor_shock_pullback', 'springboard', 'oversold_decel', 'custom_bayesian']:
            if path in overrides:
                sub = getattr(cfg, path)
                path_overrides = overrides[path]
                if 'enabled' in path_overrides:
                    sub.enabled = path_overrides['enabled']
                if 'score_threshold' in path_overrides and hasattr(sub, 'score_threshold'):
                    sub.score_threshold = path_overrides['score_threshold']
                if 'feature_bins' in path_overrides and hasattr(sub, 'feature_bins'):
                    sub.feature_bins = path_overrides['feature_bins']
                if 'feature_weights' in path_overrides and hasattr(sub, 'feature_weights'):
                    sub.feature_weights = path_overrides['feature_weights']
                    
    return cfg

def get_symbol_exit_config(symbol: str, default_cfg: SavgolCTSExitConfig | None = None) -> SavgolCTSExitConfig:
    if default_cfg is None:
        cfg = SavgolCTSExitConfig()
    else:
        cfg = copy.deepcopy(default_cfg)
    return cfg