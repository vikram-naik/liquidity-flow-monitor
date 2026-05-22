import sys
import os
import pandas as pd

sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')
from src.divergence_engine.engine import DivergenceEngine

engine = DivergenceEngine(ticker="APOLLOHOSP")
result = engine.run()
df = result.ledger
df['date_str'] = df['date'].astype(str).str[:10]
cols = ['date_str', 'close', 'cts_slope', 'cts_accel', 'psz_v', 'fas', 'fas_buy_threshold', 'range_pos_10']

# Let's filter around 2026-01-30
mask = (df['date_str'] >= '2026-01-20') & (df['date_str'] <= '2026-02-05')
print(df[mask][cols].to_string())
