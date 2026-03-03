import pandas as pd
import numpy as np

def _calc_angle(series: pd.Series, window: int = 5) -> pd.Series:
    """Calculate the rolling linear regression slope (velocity) of a series."""
    def _slope(arr):
        x = np.arange(len(arr))
        return np.polyfit(x, arr, 1)[0]
    return series.rolling(window, min_periods=window).apply(_slope, raw=True).fillna(0.0)

def apply_integrated_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the 4-pillar Integrated State Matrix to the DVL ledger."""
    
    # 1. Trajectory Angles (3-day rolling regression on the slopes)
    df['price_slope_angle'] = _calc_angle(df['price_slope_z'], 5).round(4)
    df['rdv_slope_angle'] = _calc_angle(df['rdv_slope_z'],5).round(4)
    
    # 2. Value Zones
    cwvap_dist = (df['close'] / df['cwvap'] - 1) * 100
    cpoc_dist = (df['close'] / df['cpoc'] - 1) * 100
    
    def get_value_zone(r):
        cw = r['cwvap_dist']
        cp = r['cpoc_dist']
        
        if pd.isna(cp):
            cp = cw
            
        # Chop Zone - hugging VWAP tightly
        if abs(cw) <= 1.5 and abs(cp) <= 1.5: return "Fair Value (Chop)"
        
        if cw > 3.0 and cp > 3.0: return "High Premium"
        if cw < -3.0 and cp < -3.0: return "Deep Discount"
        if cw > 0 and cp > 0: return "Slight Premium"
        if cw < 0 and cp < 0: return "Slight Discount"
        return "Fair Value (Mixed)"

    df['cwvap_dist'] = cwvap_dist
    df['cpoc_dist'] = cpoc_dist
    df['value_zone'] = df.apply(get_value_zone, axis=1)
    
    # 3. Strength Stamp (Coherence)
    def get_coherence_stamp(c):
        if c > 0.6: return " [Strong]"
        if c < 0.3: return " [Weak]"
        return ""

    df['coherence_stamp'] = df['coherence'].apply(get_coherence_stamp)
    
    # 4. Integrated Logic Matrix
    states = []
    
    for _, r in df.iterrows():
        p_z, r_z = r['price_slope_z'], r['rdv_slope_z']
        p_ang, r_ang = r['price_slope_angle'], r['rdv_slope_angle']
        vz = r['value_zone']
        mfm = r.get('mfm', 0)
        
        # Determine Directional Velocity using the 5-day angle
        price_rising = p_ang > 0.0
        rdv_rising = r_ang > 0.0
        both_rising = price_rising and rdv_rising
        both_falling = (p_ang < 0.0) and (r_ang < 0.0)
        
        state = "Neutral / Mixed"
        
        # Bullish States
        if both_rising:
            if vz == "Deep Discount":
                state = "V-Bottom Reversal"
            elif vz in ["Slight Discount", "Fair Value (Mixed)", "Fair Value (Chop)"]:
                state = "Value Breakout"
            elif vz == "Slight Premium":
                # Premium Geometry Override
                if mfm <= -0.15:
                    state = "Exhaustion Warning"
                else:
                    state = "Confirmed Markup"
            elif vz == "High Premium":
                if r_z > 0:
                    state = "Confirmed Markup"
                # If r_z <= 0 it falls back to Neutral / Mixed
                    
                # High Premium Geometry Override
                if mfm <= -0.15:
                    state = "Exhaustion Warning"
                    
        # Bearish States
        elif price_rising and not rdv_rising:
            if vz == "High Premium":
                # V-Recovery Guard: If delivery Z has turned positive and
                # money flow is healthy, the RDV angle is lagging — this
                # is a breakout continuation, not distribution.
                if r_z > 0 and mfm > 0.15:
                    state = "Confirmed Markup"
                else:
                    state = "Distribution Top"
            elif vz == "Deep Discount":
                state = "Dead Cat Bounce"

        # Divergent Volume Spikes
        elif not price_rising and rdv_rising:
            if "Discount" in vz:
                state = "Stealth Accumulation"
            elif "Premium" in vz:
                state = "Active Distribution"
                
        elif both_falling:
            if p_z < 0 and r_z < 0 and vz == "Deep Discount":
                state = "Confirmed Markdown"
            elif vz in ["Slight Premium", "Fair Value (Mixed)", "Slight Discount"]:
                state = "Value Breakdown"
            
        states.append(state + r['coherence_stamp'])

    df['integrated_state'] = states
    return df
