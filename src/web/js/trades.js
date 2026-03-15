/* Trading Dashboard JS */

const API = '/de/api/trading';

// ── Tabs ────────────────────────────────────────────────────────────────────

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'tradelog') loadTradeLog();
  });
});

// ── Dashboard ───────────────────────────────────────────────────────────────

async function loadSummary() {
  try {
    const data = await fetch(API + '/summary').then(r => r.json());
    const cards = document.getElementById('summary-cards');
    const unrealClass = data.unrealized_pnl >= 0 ? 'positive' : 'negative';
    const totalClass = data.total_pnl >= 0 ? 'positive' : 'negative';
    const proposedHtml = data.proposed > 0
      ? `<div class="card" style="border-color:#bc8cff">
          <div class="label">Proposed (Review)</div>
          <div class="value" style="color:#bc8cff">${data.proposed}</div>
        </div>`
      : '';
    cards.innerHTML = `
      <div class="card">
        <div class="label">Open Positions</div>
        <div class="value">${data.open_positions}</div>
      </div>
      <div class="card">
        <div class="label">Pending Entries</div>
        <div class="value">${data.pending_entries}</div>
      </div>
      ${proposedHtml}
      <div class="card">
        <div class="label">Unrealized P&L</div>
        <div class="value ${unrealClass}">${data.unrealized_pnl >= 0 ? '+' : ''}${data.unrealized_pnl}%</div>
      </div>
      <div class="card">
        <div class="label">Win Rate</div>
        <div class="value">${data.win_rate}%</div>
      </div>
      <div class="card">
        <div class="label">Total Closed</div>
        <div class="value">${data.total_closed}</div>
      </div>
      <div class="card">
        <div class="label">Total P&L</div>
        <div class="value ${totalClass}">${data.total_pnl >= 0 ? '+' : ''}${data.total_pnl}%</div>
      </div>
    `;
  } catch (e) {
    console.error('Failed to load summary:', e);
  }
}

const INR = v => '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });

async function loadFunds() {
  try {
    const f = await fetch(API + '/funds').then(r => r.json());
    const bar = document.getElementById('funds-bar');
    const pnlClass = v => v >= 0 ? 'positive' : 'negative';
    const pnlSign = v => v >= 0 ? '+' : '';
    bar.innerHTML = `
      <div class="fund-item">
        <span class="fund-label">Total Capital</span>
        <span class="fund-value">${INR(f.total_capital)}</span>
      </div>
      <div class="fund-divider"></div>
      <div class="fund-item">
        <span class="fund-label">Deployed</span>
        <span class="fund-value">${INR(f.capital_deployed)}</span>
      </div>
      <div class="fund-item">
        <span class="fund-label">Market Value</span>
        <span class="fund-value">${INR(f.market_value)}</span>
      </div>
      <div class="fund-item">
        <span class="fund-label">Available</span>
        <span class="fund-value">${INR(f.available_capital)}</span>
      </div>
      ${f.pending_reserved > 0 ? `
      <div class="fund-item">
        <span class="fund-label">Pending Reserved</span>
        <span class="fund-value" style="color:#d29922">${INR(f.pending_reserved)}</span>
      </div>` : ''}
      <div class="fund-divider"></div>
      <div class="fund-item">
        <span class="fund-label">Unrealized P&L</span>
        <span class="fund-value ${pnlClass(f.unrealized_pnl)}">${pnlSign(f.unrealized_pnl)}${INR(f.unrealized_pnl)}</span>
      </div>
      <div class="fund-item">
        <span class="fund-label">Realized P&L</span>
        <span class="fund-value ${pnlClass(f.realized_pnl)}">${pnlSign(f.realized_pnl)}${INR(f.realized_pnl)}</span>
      </div>
      <div class="fund-divider"></div>
      <div class="fund-item">
        <span class="fund-label">Net Worth</span>
        <span class="fund-value">${INR(f.net_worth)}</span>
      </div>
    `;
  } catch (e) {
    console.error('Failed to load funds:', e);
  }
}

async function loadOpenPositions() {
  try {
    const data = await fetch(API + '/positions?status=open').then(r => r.json());
    const pending = await fetch(API + '/positions?status=pending_entry').then(r => r.json());
    const all = [...data, ...pending];

    const tbody = document.querySelector('#open-table tbody');
    if (!all.length) {
      tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#8b949e">No open positions</td></tr>';
      return;
    }
    tbody.innerHTML = all.map(p => {
      const pnl = p.current_pnl_pct || 0;
      const pnlClass = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
      const statusClass = p.status === 'open' ? 'status-open' : 'status-pending';
      return `<tr>
        <td><strong>${p.symbol}</strong></td>
        <td>${p.entry_date || '-'}</td>
        <td>${p.entry_price ? '₹' + p.entry_price.toFixed(2) : '-'}</td>
        <td>${p.quantity || '-'}</td>
        <td class="${pnlClass}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%</td>
        <td>${(p.mfe_pct || 0).toFixed(2)}%</td>
        <td>${(p.mae_pct || 0).toFixed(2)}%</td>
        <td>${p.bars_held || 0}</td>
        <td>${p.regime_at_entry || '-'}</td>
        <td><span class="status-badge ${statusClass}">${p.status}</span></td>
      </tr>`;
    }).join('');
  } catch (e) {
    console.error('Failed to load positions:', e);
  }
}

async function loadSignals() {
  try {
    const data = await fetch(API + '/signals?days=7').then(r => r.json());
    const list = document.getElementById('signals-list');
    const recent = data.slice(0, 10);
    if (!recent.length) {
      list.innerHTML = '<div style="color:#8b949e;font-size:12px">No recent signals</div>';
      return;
    }
    list.innerHTML = recent.map(s => {
      const filters = [
        s.rdv_pass ? 'RDV' : null,
        s.mcs_pass ? 'MCS' : null,
        s.cwc_pass ? 'CWC' : null,
        s.grad_pass ? 'GRAD' : null,
      ].filter(Boolean).join(', ');
      return `<div class="signal-item">
        <span class="sym">${s.symbol}</span>
        <span class="meta">${s.signal_date} &bull; ${s.signal_type} &bull;
        PSZ=${(s.psz_at_signal || 0).toFixed(3)} &bull; ${s.regime || ''} &bull;
        Filters: ${filters || 'none'}</span>
      </div>`;
    }).join('');
  } catch (e) {
    console.error('Failed to load signals:', e);
  }
}

// ── Proposed Positions (Approval Flow) ──────────────────────────────────────

async function loadProposed() {
  try {
    const data = await fetch(API + '/positions?status=proposed').then(r => r.json());
    const section = document.getElementById('proposed-section');
    const tbody = document.querySelector('#proposed-table tbody');

    if (!data.length) {
      section.style.display = 'none';
      return;
    }

    section.style.display = 'block';
    tbody.innerHTML = data.map(p => {
      const fpass = c => c ? 'filter-pass' : 'filter-fail';
      const ftxt = c => c ? 'PASS' : '-';
      return `<tr data-id="${p.id}">
        <td><strong>${p.symbol}</strong></td>
        <td>${p.signal_date || '-'}</td>
        <td>${p.regime_at_entry || '-'}</td>
        <td>${p.soft_filters_passed || 0}/4</td>
        <td class="${fpass(p.rdv_pass)}">${ftxt(p.rdv_pass)}</td>
        <td class="${fpass(p.mcs_pass)}">${ftxt(p.mcs_pass)}</td>
        <td class="${fpass(p.cwc_pass)}">${ftxt(p.cwc_pass)}</td>
        <td class="${fpass(p.grad_pass)}">${ftxt(p.grad_pass)}</td>
        <td>
          <button class="btn btn-approve" onclick="approvePosition(${p.id})">Approve</button>
          <button class="btn btn-reject" onclick="rejectPosition(${p.id})">Skip</button>
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    console.error('Failed to load proposed:', e);
  }
}

async function approvePosition(id) {
  try {
    await fetch(API + '/positions/' + id + '/approve', { method: 'POST' });
    refreshAll();
  } catch (e) {
    console.error('Approve failed:', e);
  }
}

async function rejectPosition(id) {
  try {
    await fetch(API + '/positions/' + id + '/reject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'manual_skip' }),
    });
    refreshAll();
  } catch (e) {
    console.error('Reject failed:', e);
  }
}

document.getElementById('btn-approve-all').addEventListener('click', async () => {
  try {
    const res = await fetch(API + '/positions/approve-all', { method: 'POST' }).then(r => r.json());
    document.getElementById('scan-status').textContent = `Approved ${res.approved} position(s)`;
    refreshAll();
  } catch (e) {
    console.error('Approve all failed:', e);
  }
});

function refreshAll() {
  loadSummary();
  loadFunds();
  loadOpenPositions();
  loadProposed();
  loadSignals();
}

// ── Scan Buttons ────────────────────────────────────────────────────────────

document.getElementById('btn-scan').addEventListener('click', () => triggerScan(false));
document.getElementById('btn-dry-run').addEventListener('click', () => triggerScan(true));

async function triggerScan(dryRun) {
  const status = document.getElementById('scan-status');
  status.textContent = 'Scan started...';
  try {
    await fetch(API + '/scan?dry_run=' + dryRun, { method: 'POST' });
    status.textContent = 'Scan running in background. Refresh in a few minutes.';
    // Auto-refresh after 30s
    setTimeout(refreshAll, 30000);
  } catch (e) {
    status.textContent = 'Scan failed: ' + e.message;
  }
}

// ── Trade Log (ag-Grid) ─────────────────────────────────────────────────────

let gridApi = null;

function pnlCellRenderer(params) {
  const v = params.value || 0;
  const cls = v >= 0 ? 'pnl-pos' : 'pnl-neg';
  return `<span class="${cls}">${v >= 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
}

const columnDefs = [
  { field: 'symbol', headerName: 'Symbol', width: 110, pinned: 'left' },
  { field: 'entry_date', headerName: 'Entry', width: 100 },
  { field: 'exit_date', headerName: 'Exit', width: 100 },
  { field: 'entry_price', headerName: 'Entry ₹', width: 90, valueFormatter: p => p.value ? p.value.toFixed(2) : '' },
  { field: 'exit_price', headerName: 'Exit ₹', width: 90, valueFormatter: p => p.value ? p.value.toFixed(2) : '' },
  { field: 'final_pnl_pct', headerName: 'P&L%', width: 85, cellRenderer: pnlCellRenderer },
  { field: 'mfe_pct', headerName: 'MFE%', width: 75, valueFormatter: p => (p.value || 0).toFixed(2) },
  { field: 'mae_pct', headerName: 'MAE%', width: 75, valueFormatter: p => (p.value || 0).toFixed(2) },
  { field: 'bars_held', headerName: 'Bars', width: 60 },
  { field: 'exit_reason', headerName: 'Exit Reason', width: 130 },
  { field: 'regime_at_entry', headerName: 'Regime', width: 110 },
  { field: 'soft_filters_passed', headerName: 'Filters', width: 70 },
];

async function loadTradeLog() {
  try {
    const data = await fetch(API + '/trades?limit=500').then(r => r.json());
    const gridDiv = document.getElementById('trade-grid');

    if (!gridApi) {
      gridApi = agGrid.createGrid(gridDiv, {
        columnDefs,
        rowData: data,
        defaultColDef: {
          sortable: true,
          filter: true,
          resizable: true,
        },
        animateRows: true,
        pagination: true,
        paginationPageSize: 50,
      });
    } else {
      gridApi.setGridOption('rowData', data);
    }
  } catch (e) {
    console.error('Failed to load trade log:', e);
  }
}

// CSV export
document.getElementById('btn-export-csv').addEventListener('click', () => {
  if (gridApi) gridApi.exportDataAsCsv({ fileName: 'trade_log.csv' });
});

// ── Init ────────────────────────────────────────────────────────────────────

loadSummary();
loadFunds();
loadOpenPositions();
loadProposed();
loadSignals();
