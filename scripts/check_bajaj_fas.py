
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.entries.utils import is_flattish_line_adaptive
import pandas as pd

engine = DivergenceEngine("BAJAJFINSV")
res = engine.run()
df = res.ledger
row_idx = df[df['date'] == '2024-05-22'].index[0]
subset = df.loc[row_idx-30 : row_idx]

records = subset.to_dict('records')
i = len(records) - 1
f1 = records[i-2].get("fas", 0)
f2 = records[i-1].get("fas", 0)
f3 = records[i].get("fas", 0)
fas_lookback = [records[j].get("fas", 0) for j in range(len(records))]
fas_flat_res = is_flattish_line_adaptive(f1, f2, f3, lookback_window_data=fas_lookback, sensitivity=0.05)

print(f"Date: {records[i]['date']}")
print(f"FAS values: {f1:.4f}, {f2:.4f}, {f3:.4f}")
print(f"FAS Flat Result: {fas_flat_res}")

a1 = records[i-2].get("cts_accel", 0)
a2 = records[i-1].get("cts_accel", 0)
a3 = records[i].get("cts_accel", 0)
accel_lookback = [records[j].get("cts_accel", 0) for j in range(len(records))]
res_accel_flat = is_flattish_line_adaptive(a1, a2, a3, lookback_window_data=accel_lookback, sensitivity=0.05)
print(f"Accel values: {a1:.4f}, {a2:.4f}, {a3:.4f}")
print(f"Accel Flat Result: {res_accel_flat}")

# Check entry signal
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig
signal = SavgolCTSSignal()
res_sig = signal.tag_signals(df, SavgolCTSEntryConfig(), SavgolCTSExitConfig())
sig_row = res_sig[res_sig['date'] == '2024-05-22'].iloc[0]
print(f"Entry Signal: {sig_row['entry_signal']}")

# Find exit
df_trade = res_sig[res_sig['date'] >= '2024-05-22']
entry_price = df_trade.iloc[1]['open'] # Signal i, Entry i+1
print(f"Entry Date: {df_trade.iloc[1]['date']}, Price: {entry_price}")

exit_row = df_trade[df_trade['exit_signal'] > 0].iloc[0]
exit_price = exit_row['close']
print(f"Exit Date: {exit_row['date']}, Price: {exit_price}")
pnl = (exit_price / entry_price - 1) * 100
print(f"PnL: {pnl:.2f}%")

