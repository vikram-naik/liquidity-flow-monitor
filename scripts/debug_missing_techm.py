
import pandas as pd
import numpy as np
from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.factory import SignalFactory

def debug_techm():
    symbol = "TECHM"
    engine = DivergenceEngine(symbol)
    result = engine.run()
    df = result.ledger
    
    signal = SignalFactory.get_signal("savgol_cts")
    df_tagged = signal.tag_signals(df)
    
    # Check for any entry signals
    entries = df_tagged[df_tagged["entry_signal"] > 0]
    exits = df_tagged[df_tagged["exit_signal"] > 0]
    
    print(f"--- TECHM Signals found by tag_signals ---")
    
    # Combine entries and exits for a chronological view
    events = []
    for _, row in entries.iterrows():
        events.append({"date": row["date"], "type": "ENTRY", "reason": row["entry_reason"], "intensity": row["entry_signal"]})
    for _, row in exits.iterrows():
        events.append({"date": row["date"], "type": "EXIT", "reason": row["exit_reason"], "intensity": 0})
        
    events.sort(key=lambda x: x["date"])
    for e in events:
        print(f"{e['date']} | {e['type']} | {e['reason']} | intensity={e['intensity']}")
        
    # Check current state at the end
    records = df_tagged.to_dict("records")
    # We can't easily see the internal 'in_trade' state of tag_signals after it ran, 
    # but we can see if there was an entry without a corresponding exit.
    
    last_event = events[-1] if events else None
    if last_event and last_event["type"] == "ENTRY":
        print(f"\nSimulation is currently IN TRADE (entered {last_event['date']})")
    else:
        print(f"\nSimulation is currently NOT in trade.")

if __name__ == "__main__":
    debug_techm()
