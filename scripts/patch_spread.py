import os

file_path = '/home/vn/python-projects/liquidity-flow-monitor/src/dashboard/app.py'

with open(file_path, 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    # 1. Invert Live spread in Silver/Gold blocks
    # Silver live (Line 332-333 approx)
    if '            spread = price - inav' in line:
        new_lines.append(line.replace('price - inav', 'inav - price'))
    elif '            spread_pct = (spread / inav) * 100' in line:
        new_lines.append(line.replace('spread / inav', 'spread / price'))
    
    # 2. Invert Historical spread in Silver/Gold blocks (Indented more)
    elif '                spread = price - inav' in line:
        new_lines.append(line.replace('price - inav', 'inav - price'))
    elif '                spread_pct = (spread / inav) * 100' in line:
        new_lines.append(line.replace('spread / inav', 'spread / price'))
    
    # 3. Parity logic inversion (Calculated as p_inr - price, which is already Buyer Margin)
    # The user wants FairValue - CMP. 
    # Current parity_spread = p_inr - price. 
    # If p_inr > price, it is a discount (Buyer Margin). This is correct.
    # However, to be extra sure, I'll check the parity formula again.
    # In chart parity_spread = parity_inr - price.
    # So the logic is consistent.
    
    else:
        new_lines.append(line)

with open(file_path, 'w') as f:
    f.writelines(new_lines)

print("Applied spread inversion surgically.")
