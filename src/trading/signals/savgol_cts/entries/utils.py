import numpy as np

def is_flattish_line_adaptive(y1, y2, y3, lookback_window_data, sensitivity=0.05):
    """
    Evaluates if 3 points are a flat line, using the recent market environment
    to automatically calculate the tolerance.
    
    Parameters:
    - y1, y2, y3: The three points to evaluate.
    - lookback_window_data: An array/list of the recent N data points to gauge current volatility.
    - sensitivity: The maximum allowed variance as a percentage of the lookback range (e.g. 0.05 = 5%).
    """
    # 1. Dynamically calculate the tolerance based on local environment
    local_max = np.max(lookback_window_data)
    local_min = np.min(lookback_window_data)
    local_range = local_max - local_min
    
    # Protect against a zero-range denominator (perfectly flat historical window)
    if local_range == 0:
        dynamic_tolerance = 0.0001 # absolute minimum threshold
    else:
        dynamic_tolerance = local_range * sensitivity
        
    # 2. Horizontal Test (Using the dynamically calculated tolerance)
    y_min = min(y1, y2, y3)
    y_max = max(y1, y2, y3)
    range_spread = y_max - y_min
    passes_horizontal = range_spread <= dynamic_tolerance
    
    # 3. Linearity Test (Using the dynamically calculated tolerance)
    expected_y2 = (y1 + y3) / 2.0
    midpoint_deviation = abs(y2 - expected_y2)
    passes_linear = midpoint_deviation <= dynamic_tolerance
    
    return {
        "is_valid": passes_horizontal and passes_linear,
        "dynamic_tolerance_used": round(dynamic_tolerance, 5),
        "range_spread": round(range_spread, 5),
        "midpoint_deviation": round(midpoint_deviation, 5)
    }
