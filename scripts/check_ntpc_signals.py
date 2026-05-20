
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.divergence_engine.engine import DivergenceEngine
from src.trading.signals.savgol_cts.signal import SavgolCTSSignal
from src.trading.signals.savgol_cts.config import SavgolCTSEntryConfig, SavgolCTSExitConfig

engine = DivergenceEngine("NTPC")
res = engine.run()
df = res.ledger
signal = SavgolCTSSignal()
res_sig = signal.tag_signals(df, SavgolCTSEntryConfig(), SavgolCTSExitConfig())
trades = res_sig[res_sig['entry_signal'] > 0]
print(f"NTPC signals since 2024-01-01: {len(trades[trades['date'] >= '2024-01-01'])}")
for idx, row in trades[trades['date'] >= '2024-01-01'].iterrows():
    print(f"Date: {row['date']}, Intensity: {row['entry_signal']}")
