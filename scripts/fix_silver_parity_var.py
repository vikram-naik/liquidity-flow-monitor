import os

file_path = '/home/vn/python-projects/liquidity-flow-monitor/src/dashboard/app.py'

with open(file_path, 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    # Fix the variable mismatch in Silver Parity Spread (Line 389 approx)
    if 'st.metric("🎯 Parity Spread", f"₹{parity_spread:.4f}", delta=f"{spread_pct:+.2f}%' in line:
        new_lines.append(line.replace('delta=f"{spread_pct:+.2f}%', 'delta=f"{parity_spread_pct:+.2f}%'))
    else:
        new_lines.append(line)

with open(file_path, 'w') as f:
    f.writelines(new_lines)

print("Fixed Silver Parity Spread variable mismatch.")
