import numpy as np
import scipy.signal as signal

def test_causal_windows():
    poly = 2
    x = np.arange(100)
    y_sin = np.sin(x/10.0)
    
    # Causal SG with window 15
    coeffs_15 = signal.savgol_coeffs(15, poly, deriv=1, pos=14)
    v_15 = signal.lfilter(coeffs_15, [1.0], y_sin)
    
    # Causal SG with window 11
    coeffs_11 = signal.savgol_coeffs(11, poly, deriv=1, pos=10)
    v_11 = signal.lfilter(coeffs_11, [1.0], y_sin)
    
    # Centered SG with window 11 (the "original")
    v_cent_11 = signal.savgol_filter(y_sin, 11, poly, deriv=1)
    
    print(f"Peak Velocity Comparison (Sine Wave):")
    print(f"Causal (W=15): {np.max(np.abs(v_15)):.6f}")
    print(f"Causal (W=11): {np.max(np.abs(v_11)):.6f}")
    print(f"Centered (W=11): {np.max(np.abs(v_cent_11)):.6f}")

if __name__ == "__main__":
    test_causal_windows()
