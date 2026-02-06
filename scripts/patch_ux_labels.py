import os

file_path = '/home/vn/python-projects/liquidity-flow-monitor/src/dashboard/app.py'

with open(file_path, 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    # 1. Parity Spread in Silver (Line 388 approx)
    if 'st.metric("🎯 Parity Spread", f"₹{parity_spread:.4f}", delta=f"{parity_spread_pct:+.2f}%", delta_color=p_delta_color)' in line:
        indent = line[:line.find('st.metric')]
        new_lines.append(f'{indent}p_label = " (discount)" if parity_spread > 0 else " (premium)" if parity_spread < 0 else ""\n')
        new_lines.append(line.replace('f"{parity_spread_pct:+.2f}%"', 'f"{parity_spread_pct:+.2f}%{p_label}"'))
    
    # 2. iNAV Spread in Gold (Line 550 approx) - Note: Silver iNAV spread already has label applied by previous tool
    elif 'st.metric("📏 Spread", f"₹{spread:.2f}", delta=f"{spread_pct:+.2f}%", delta_color=delta_color)' in line:
        # Avoid double applying to Silver if it was already updated (though previous tool supposedly succeeded for Silver iNAV)
        if 's_label' not in "".join(new_lines[-5:]): # Check context
             indent = line[:line.find('st.metric')]
             new_lines.append(f'{indent}s_label = " (discount)" if spread > 0 else " (premium)" if spread < 0 else ""\n')
             new_lines.append(line.replace('f"{spread_pct:+.2f}%"', 'f"{spread_pct:+.2f}%{s_label}"'))
        else:
            new_lines.append(line)

    # 3. Parity Spread in Gold (Line 556 approx)
    elif 'st.metric("🎯 Parity Spread", f"₹{parity_spread:.4f}", delta=f"{parity_spread_pct:+.2f}%", delta_color=p_delta_color)' in line:
        indent = line[:line.find('st.metric')]
        new_lines.append(f'{indent}p_label = " (discount)" if parity_spread > 0 else " (premium)" if parity_spread < 0 else ""\n')
        new_lines.append(line.replace('f"{parity_spread_pct:+.2f}%"', 'f"{parity_spread_pct:+.2f}%{p_label}"'))

    else:
        new_lines.append(line)

with open(file_path, 'w') as f:
    f.writelines(new_lines)

print("Applied final labels surgically.")
