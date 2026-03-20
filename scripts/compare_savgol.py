import numpy as np
import scipy.signal as signal
import matplotlib.pyplot as plt

def compare_savgol_methods():
    window = 15
    poly = 2
    
    # Create a ramp signal (constant velocity)
    x = np.arange(100)
    y = 0.5 * x  # velocity should be 0.5
    
    # Centered SG
    # savgol_filter uses pos=window//2 by default for interior points
    # but at the edges it automatically uses causal/anti-causal.
    v_centered_full = signal.savgol_filter(y, window, poly, deriv=1)
    
    # Causal SG coefficients
    coeffs_causal = signal.savgol_coeffs(window, poly, deriv=1, pos=window-1)
    v_causal = signal.lfilter(coeffs_causal, [1.0], y)
    
    print(f"Ideal Velocity: 0.5")
    print(f"Centered velocity (middle of data): {v_centered_full[50]:.6f}")
    print(f"Causal velocity (middle of data): {v_causal[50]:.6f}")
    
    # Let's test a sine wave to see peaks
    y_sin = np.sin(x/10.0)
    v_centered_sin = signal.savgol_filter(y_sin, window, poly, deriv=1)
    v_causal_sin = signal.lfilter(coeffs_causal, [1.0], y_sin)
    
    print(f"Sine Wave Peak Velocity:")
    print(f"Centered Max V: {np.max(np.abs(v_centered_sin)):.6f}")
    print(f"Causal Max V: {np.max(np.abs(v_causal_sin)):.6f}")

if __name__ == "__main__":
    compare_savgol_methods()
