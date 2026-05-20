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


def calculate_slope_angle(y_values):
    """
    Calculates the slope angle of the line of best fit for a given array of y-values.
    Assumes x-values are evenly spaced integers starting from 0 (0, 1, 2... n-1).
    Returns the angle in degrees as a float rounded to two decimal places.
    """
    y = np.array(y_values)
    
    # Generate evenly spaced x-values from 0 to len(y) - 1
    x = np.arange(len(y))
    
    # Calculate the slope (m) and intercept (c) using linear regression (degree 1 polynomial)
    slope, intercept = np.polyfit(x, y, 1)
    
    # Convert the slope to an angle in radians using arctangent
    angle_rad = np.arctan(slope)
    
    # Convert radians to degrees
    angle_deg = np.degrees(angle_rad)
    
    # Return the angle rounded to two decimal places
    return round(angle_deg, 2)


import scipy.stats as stats

def evaluate_spearman_trend(y_values: list[float]) -> float:
    """
    Evaluates trend consistency using Spearman's Rank Correlation.
    Returns a value between -1.0 (perfect downtrend) and 1.0 (perfect uptrend).

    How to interpret the threshold: * > 0.80: A visually obvious, strong rising trend.

        0.50 to 0.80: A choppy but generally rising trend.
        < 0.50: Weak, sideways, or heavily reverting data.
    """
    if len(y_values) < 2:
        return 0.0
        
    # Generate sequential x-values (time/intervals)
    x_values = list(range(len(y_values)))
    
    # Calculate Spearman correlation
    y_arr = np.array(y_values)
    if np.all(y_arr == y_arr[0]):
        return 0.0
        
    spearman_coeff, _ = stats.spearmanr(x_values, y_arr)
    
    return round(float(spearman_coeff), 4)