import sys
sys.path.insert(0, '/home/vn/python-projects/liquidity-flow-monitor')

from scratch.study_state_based_bypass import modify_entry_file, backup_file, restore_file

backup_file()
try:
    modify_entry_file(0.02)
    print("\n--- Printing Modified universal_cross.py lines 100-145 ---")
    with open("/home/vn/python-projects/liquidity-flow-monitor/src/trading/signals/savgol_cts/entries/universal_cross.py", "r") as f:
        lines = f.readlines()
        for idx, line in enumerate(lines[100:145], start=101):
            print(f"{idx}: {line}", end="")
finally:
    restore_file()
