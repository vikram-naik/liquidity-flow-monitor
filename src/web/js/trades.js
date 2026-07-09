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
    if (btn.dataset.tab === 'orderlog') loadOrderLog();
    if (btn.dataset.tab === 'ledger') loadLedger();
  });
});

// ── Helpers ─────────────────────────────────────────────────────────────────

const INR = v => '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
const pnlClass = v => v >= 0 ? 'positive' : 'negative';
const pnlSign = v => v >= 0 ? '+' : '';

const formatPnL = (abs, pct) => {
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(2)}%`;
};

// ── Dashboard: Summary Cards ───────────────────────────────────────────────

async function loadSummary() {
  try {
    const data = await fetch(API + '/summary').then(r => r.json());
    const cards = document.getElementById('summary-cards');
    const unrealClass = pnlClass(data.unrealized_pnl_abs);
    const grossClass = pnlClass(data.total_gross_pnl_abs);
    const netClass = pnlClass(data.total_net_pnl_abs);
    const cagrClass = pnlClass(data.cagr_pct);
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
        <div class="label">Pending (Entry/Exit)</div>
        <div class="value">${data.pending_entries} / ${data.pending_exits}</div>
      </div>
      ${proposedHtml}
      <div class="card">
        <div class="label">Unrealized P&L</div>
        <div class="value ${unrealClass}">${formatPnL(data.unrealized_pnl_abs, data.unrealized_pnl_pct)}</div>
      </div>
      <div class="card">
        <div class="label">Win Rate</div>
        <div class="value">${data.win_rate}%</div>
      </div>
      <div class="card">
        <div class="label">Gross P&L</div>
        <div class="value ${grossClass}">${formatPnL(data.total_gross_pnl_abs, data.total_gross_pnl_pct)}</div>
      </div>
      <div class="card">
        <div class="label">Net P&L</div>
        <div class="value ${netClass}">${formatPnL(data.total_net_pnl_abs, data.total_net_pnl_pct)}</div>
      </div>
      <div class="card card-cagr">
        <div class="label">CAGR — Strategy vs Nifty 50</div>
        <div class="value ${cagrClass}">${data.cagr_pct >= 0 ? '+' : ''}${data.cagr_pct.toFixed(2)}%</div>
        ${(() => {
          if (data.nifty_cagr_pct == null) {
            return `<div class="cagr-benchmark cagr-na">Nifty 50: N/A</div>`;
          }
          const nc = data.nifty_cagr_pct;
          const alpha = data.cagr_pct - nc;
          const alphaSign = alpha >= 0 ? '+' : '';
          const alphaCls = alpha > 0.5 ? 'alpha-pos' : alpha < -0.5 ? 'alpha-neg' : 'alpha-neutral';
          const ncCls = nc >= 0 ? 'positive' : 'negative';
          return `<div class="cagr-benchmark">
            <span class="${ncCls}">${nc >= 0 ? '+' : ''}${nc.toFixed(2)}%</span>
            <span class="cagr-alpha ${alphaCls}">${alphaSign}${alpha.toFixed(2)}% α</span>
          </div>`;
        })()}
      </div>
      <div class="card">
        <div class="label">Avg Duration</div>
        <div class="value" style="color:#3fb950">${data.avg_duration_bars !== undefined ? data.avg_duration_bars : 0} bars</div>
      </div>
    `;
  } catch (e) {
    console.error('Failed to load summary:', e);
  }
}

// ── Dashboard: Funds Bar ───────────────────────────────────────────────────

async function loadFunds() {
  try {
    const f = await fetch(API + '/funds').then(r => r.json());
    const bar = document.getElementById('funds-bar');
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
      <div class="fund-item">
        <span class="fund-label">Charges Paid</span>
        <span class="fund-value" style="color:#d29922">${INR(f.total_charges_paid)}</span>
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

// ── Dashboard: Equity Curve ────────────────────────────────────────────────

let equityChart = null;

async function loadEquityCurve(days = 90) {
  try {
    const data = await fetch(API + '/equity-curve?days=' + days).then(r => r.json());
    const ctx = document.getElementById('equity-chart').getContext('2d');

    const labels = data.map(d => d.date);
    const equity = data.map(d => d.equity);
    const drawdown = data.map(d => d.drawdown_pct);

    if (equityChart) equityChart.destroy();

    equityChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Equity',
            data: equity,
            borderColor: '#58a6ff',
            backgroundColor: 'rgba(88,166,255,0.05)',
            borderWidth: 1.5,
            pointRadius: 0,
            fill: true,
            yAxisID: 'y',
          },
          {
            label: 'Drawdown %',
            data: drawdown,
            borderColor: '#f85149',
            backgroundColor: 'rgba(248,81,73,0.1)',
            borderWidth: 1,
            pointRadius: 0,
            fill: true,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: 'index' },
        plugins: {
          legend: {
            labels: { color: '#8b949e', font: { size: 11 } },
          },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                if (ctx.datasetIndex === 0) return 'Equity: ' + INR(ctx.parsed.y);
                return 'DD: ' + ctx.parsed.y.toFixed(2) + '%';
              },
            },
          },
        },
        scales: {
          x: {
            ticks: { color: '#484f58', font: { size: 10 }, maxTicksLimit: 12 },
            grid: { color: '#21262d' },
          },
          y: {
            position: 'left',
            ticks: {
              color: '#58a6ff', font: { size: 10 },
              callback: v => INR(v),
            },
            grid: { color: '#21262d' },
          },
          y1: {
            position: 'right',
            ticks: { color: '#f85149', font: { size: 10 }, callback: v => v + '%' },
            grid: { drawOnChartArea: false },
            reverse: true,
          },
        },
      },
    });
  } catch (e) {
    console.error('Failed to load equity curve:', e);
  }
}

// Equity period buttons
document.querySelectorAll('.eq-period').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.eq-period').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadEquityCurve(parseInt(btn.dataset.days));
  });
});

// ── Dashboard: Strategy Config ─────────────────────────────────────────────

const EDITABLE_KEYS = {
  signal_strategy: { type: 'text', label: 'Signal Strategy' },
  sizing_strategy: { type: 'select', label: 'Sizing', options: ['equal_weight', 'kelly'] },
  kelly_fraction: { type: 'number', label: 'Kelly Fraction', step: '0.05', min: '0.05', max: '1' },
  max_concurrent_positions: { type: 'number', label: 'Max Positions', step: '1', min: '1', max: '20' },
  brokerage_model: { type: 'text', label: 'Brokerage Model' },
  execution_mode: { type: 'select', label: 'Execution', options: ['paper', 'live'] },
  watchlist: { type: 'text', label: 'Watchlist' },
  capital: { type: 'number', label: 'Seed Capital', step: '100000', min: '0' },
};

async function loadConfig() {
  try {
    const config = await fetch(API + '/config').then(r => r.json());
    const grid = document.getElementById('config-grid');
    grid.innerHTML = Object.entries(EDITABLE_KEYS).map(([key, meta]) => {
      const val = config[key] || '';
      let input;
      if (meta.type === 'select') {
        const opts = meta.options.map(o =>
          `<option value="${o}" ${o === val ? 'selected' : ''}>${o}</option>`
        ).join('');
        input = `<select data-key="${key}">${opts}</select>`;
      } else {
        const attrs = meta.step ? `step="${meta.step}"` : '';
        const minmax = (meta.min ? `min="${meta.min}"` : '') + ' ' + (meta.max ? `max="${meta.max}"` : '');
        input = `<input type="${meta.type}" data-key="${key}" value="${val}" ${attrs} ${minmax}>`;
      }
      return `<div class="config-item">
        <span class="cfg-label">${meta.label}</span>
        ${input}
      </div>`;
    }).join('');

    // Save on change
    grid.querySelectorAll('input, select').forEach(el => {
      el.addEventListener('change', async () => {
        const updates = {};
        updates[el.dataset.key] = el.value;
        await fetch(API + '/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ updates }),
        });
      });
    });
  } catch (e) {
    console.error('Failed to load config:', e);
  }
}

// ── Dashboard: Capital Events ──────────────────────────────────────────────

async function loadCapitalEvents() {
  try {
    const data = await fetch(API + '/capital-events').then(r => r.json());
    const section = document.getElementById('capital-events-section');
    if (!data.length) {
      section.innerHTML = '<div style="color:#8b949e;font-size:12px">No capital events recorded</div>';
      return;
    }
    section.innerHTML = `<table class="cap-events-table">
      <thead><tr><th>Date</th><th>Type</th><th>Amount</th><th>Balance After</th><th>Note</th></tr></thead>
      <tbody>${data.map(e => `<tr>
        <td>${e.date}</td>
        <td class="${e.event_type === 'injection' ? 'cap-injection' : 'cap-withdrawal'}">${e.event_type}</td>
        <td>${INR(e.amount)}</td>
        <td>${INR(e.balance_after)}</td>
        <td style="color:#8b949e">${e.note || ''}</td>
      </tr>`).join('')}</tbody>
    </table>`;
  } catch (e) {
    console.error('Failed to load capital events:', e);
  }
}

// Capital event modal
document.getElementById('btn-add-capital').addEventListener('click', () => {
  document.getElementById('capital-modal').style.display = 'flex';
});

document.getElementById('ce-cancel').addEventListener('click', () => {
  document.getElementById('capital-modal').style.display = 'none';
});

document.getElementById('ce-submit').addEventListener('click', async () => {
  const event_type = document.getElementById('ce-type').value;
  const amount = parseFloat(document.getElementById('ce-amount').value);
  const note = document.getElementById('ce-note').value;
  if (!amount || amount <= 0) return;

  try {
    const res = await fetch(API + '/capital-events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_type, amount, note }),
    }).then(r => r.json());

    if (res.status === 'ok') {
      document.getElementById('capital-modal').style.display = 'none';
      document.getElementById('ce-amount').value = '';
      document.getElementById('ce-note').value = '';
      loadCapitalEvents();
      loadFunds();
    }
  } catch (e) {
    console.error('Add capital event failed:', e);
  }
});

// ── Dashboard: Open Positions ──────────────────────────────────────────────

async function loadOpenPositions() {
  try {
    const data = await fetch(API + '/positions?status=open').then(r => r.json());
    const pending = await fetch(API + '/positions?status=pending_entry').then(r => r.json());
    const exiting = await fetch(API + '/positions?status=pending_exit').then(r => r.json());
    const proposedExits = await fetch(API + '/positions?status=proposed_exit').then(r => r.json());
    const all = [...data, ...pending, ...exiting, ...proposedExits];

    const tbody = document.querySelector('#open-table tbody');
    if (!all.length) {
      tbody.innerHTML = '<tr><td colspan="14" style="text-align:center;color:#8b949e">No open positions</td></tr>';
      return;
    }
    tbody.innerHTML = all.map(p => {
      const pnl = p.current_pnl_pct || 0;
      const pnlCls = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
      const statusCls = p.status === 'open' ? 'status-open' :
                        p.status === 'pending_entry' ? 'status-pending' :
                        p.status === 'proposed_exit' ? 'status-pending' : 'status-exiting';
      const sizingLabel = p.sizing_method === 'kelly' ? `K(${(p.kelly_f || 0.25).toFixed(2)})` : 'EqWt';

      const actionBtn = p.status === 'open'
        ? `<button class="btn btn-reject" style="padding:2px 8px;font-size:10px" onclick="manualExit(${p.id})">Exit</button>`
        : '';

      return `<tr>
        <td><a href="/de/dashboard/${p.symbol}?focus=${p.entry_date}" class="back-link"><strong>${p.symbol}</strong></a></td>
        <td>${p.entry_tag || '-'}</td>
        <td>${p.entry_date || '-'}</td>
        <td>${p.entry_price ? '₹' + p.entry_price.toFixed(2) : '-'}</td>
        <td>${p.quantity || '-'}</td>
        <td>${p.capital_deployed ? INR(p.capital_deployed) : '-'}</td>
        <td class="${pnlCls}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%</td>
        <td>${(p.mfe_pct || 0).toFixed(2)}%</td>
        <td>${(p.mae_pct || 0).toFixed(2)}%</td>
        <td>${p.bars_held || 0}</td>
        <td>${sizingLabel}</td>
        <td>${p.regime_at_entry || '-'}</td>
        <td><span class="status-badge ${statusCls}">${p.status}</span></td>
        <td>${actionBtn}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    console.error('Failed to load positions:', e);
  }
}

async function manualExit(id) {
  if (!confirm('Are you sure you want to trigger a manual exit for this position?')) return;
  try {
    await fetch(API + '/positions/' + id + '/exit', { method: 'POST' });
    refreshAll();
  } catch (e) {
    console.error('Manual exit failed:', e);
  }
}

// ── Dashboard: Signals ─────────────────────────────────────────────────────

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
      return `<div class="signal-item">
        <a href="/de/dashboard/${s.symbol}?focus=${s.signal_date}" class="back-link"><span class="sym">${s.symbol}</span></a>
        <span class="meta">${s.signal_date} &bull; ${s.signal_type} &bull;
        PSZ=${(s.psz_at_signal || 0).toFixed(3)} &bull; ${s.regime || ''}</span>
      </div>`;
    }).join('');
  } catch (e) {
    console.error('Failed to load signals:', e);
  }
}

// ── Proposed Positions (Approval Flow) ──────────────────────────────────────

async function loadProposed() {
  try {
    const proposedEntries = await fetch(API + '/positions?status=proposed').then(r => r.json());
    const proposedExits = await fetch(API + '/positions?status=proposed_exit').then(r => r.json());
    const data = [...proposedEntries, ...proposedExits];
    const section = document.getElementById('proposed-section');
    const tbody = document.querySelector('#proposed-table tbody');

    if (!data.length) {
      section.style.display = 'none';
      return;
    }

    section.style.display = 'block';
    tbody.innerHTML = data.map(p => {
      const isExit = p.status === 'proposed_exit';
      const sideLabel = isExit 
        ? '<span class="status-badge status-exiting" style="margin-left:8px;font-size:9px">SELL (Exit)</span>' 
        : '<span class="status-badge status-open" style="margin-left:8px;font-size:9px">BUY (Entry)</span>';
      
      const signalDate = isExit ? (p.exit_signal_date || p.updated_at || '').substring(0, 10) : (p.signal_date || '').substring(0, 10);
      const focusDate = isExit ? p.entry_date : signalDate;

      return `<tr data-id="${p.id}">
        <td>
          <a href="/de/dashboard/${p.symbol}?focus=${focusDate}" class="back-link"><strong>${p.symbol}</strong></a>
          ${sideLabel}
        </td>
        <td>${signalDate || '-'}</td>
        <td>${p.regime_at_entry || '-'}</td>
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
  loadEquityCurve();
  loadCapitalEvents();
}

// ── Scan Buttons ────────────────────────────────────────────────────────────

document.getElementById('btn-scan').addEventListener('click', () => triggerScan(false));
document.getElementById('btn-dry-run').addEventListener('click', () => triggerScan(true));

document.getElementById('btn-refresh-pnls').addEventListener('click', async () => {
  const status = document.getElementById('scan-status');
  status.textContent = 'Refreshing valuations...';
  try {
    await fetch(API + '/refresh', { method: 'POST' });
    status.textContent = 'Valuations refreshed.';
    refreshAll();
  } catch (e) {
    status.textContent = 'Refresh failed: ' + e.message;
  }
});

document.getElementById('btn-execute').addEventListener('click', async () => {
  const status = document.getElementById('scan-status');
  status.textContent = 'Execution started...';
  try {
    await fetch(API + '/execute', { method: 'POST' });
    status.textContent = 'Execution running in background. Refresh in a few minutes.';
    setTimeout(refreshAll, 15000);
  } catch (e) {
    status.textContent = 'Execution failed: ' + e.message;
  }
});

async function triggerScan(dryRun) {
  const status = document.getElementById('scan-status');
  status.textContent = 'Scan started...';
  try {
    await fetch(API + '/scan?dry_run=' + dryRun, { method: 'POST' });
    status.textContent = 'Scan running in background. Refresh in a few minutes.';
    setTimeout(refreshAll, 30000);
  } catch (e) {
    status.textContent = 'Scan failed: ' + e.message;
  }
}

// ── Trade Log (ag-Grid) ─────────────────────────────────────────────────────

let tradeGridApi = null;

function pnlCellRenderer(params) {
  const v = params.value || 0;
  const cls = v >= 0 ? 'pnl-pos' : 'pnl-neg';
  return `<span class="${cls}">${v >= 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
}

function inrCellRenderer(params) {
  const v = params.value || 0;
  const cls = v >= 0 ? 'pnl-pos' : 'pnl-neg';
  return `<span class="${cls}">${v >= 0 ? '+' : ''}${INR(v)}</span>`;
}

const tradeColumnDefs = [
  { field: 'symbol', headerName: 'Symbol', width: 110, pinned: 'left',
    cellRenderer: p => `<a href="/de/dashboard/${p.value}?focus=${p.data.entry_date}" class="back-link" style="font-weight:600">${p.value}</a>` },
  { field: 'entry_tag', headerName: 'Entry Tag', width: 110 },
  { field: 'entry_date', headerName: 'Entry', width: 100 },
  { field: 'exit_date', headerName: 'Exit', width: 100 },
  { field: 'entry_price', headerName: 'Entry ₹', width: 90, valueFormatter: p => p.value ? p.value.toFixed(2) : '' },
  { field: 'exit_price', headerName: 'Exit ₹', width: 90, valueFormatter: p => p.value ? p.value.toFixed(2) : '' },
  { field: 'quantity', headerName: 'Qty', width: 65 },
  { field: 'capital_deployed', headerName: 'Capital', width: 100, valueFormatter: p => p.value ? INR(p.value) : '' },
  { field: 'sizing_method', headerName: 'Sizing', width: 85 },
  { field: 'final_pnl_pct', headerName: 'Gross%', width: 85, cellRenderer: pnlCellRenderer },
  { field: 'net_pnl_pct', headerName: 'Net%', width: 80, cellRenderer: pnlCellRenderer },
  { field: 'net_pnl_abs', headerName: 'Net ₹', width: 100, cellRenderer: inrCellRenderer },
  { field: 'total_charges', headerName: 'Charges', width: 85, valueFormatter: p => p.value ? INR(p.value) : '₹0' },
  { field: 'mfe_pct', headerName: 'MFE%', width: 75, valueFormatter: p => (p.value || 0).toFixed(2) },
  { field: 'mae_pct', headerName: 'MAE%', width: 75, valueFormatter: p => (p.value || 0).toFixed(2) },
  { field: 'bars_held', headerName: 'Bars', width: 60 },
  { field: 'exit_reason', headerName: 'Exit Reason', width: 130 },
  { field: 'regime_at_entry', headerName: 'Regime', width: 110 },
];

async function loadTradeLog() {
  try {
    const data = await fetch(API + '/trades?limit=500').then(r => r.json());
    const gridDiv = document.getElementById('trade-grid');

    if (!tradeGridApi) {
      tradeGridApi = agGrid.createGrid(gridDiv, {
        columnDefs: tradeColumnDefs,
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
      tradeGridApi.setGridOption('rowData', data);
    }
  } catch (e) {
    console.error('Failed to load trade log:', e);
  }
}

document.getElementById('btn-export-csv').addEventListener('click', () => {
  if (tradeGridApi) tradeGridApi.exportDataAsCsv({ fileName: 'trade_log.csv' });
});

// ── Order Log (ag-Grid) ─────────────────────────────────────────────────────

let orderGridApi = null;

const orderColumnDefs = [
  { field: 'executed_at', headerName: 'Date', width: 150 },
  { field: 'symbol', headerName: 'Symbol', width: 110, pinned: 'left' },
  { field: 'side', headerName: 'Side', width: 65,
    cellRenderer: p => `<span style="color:${p.value === 'BUY' ? '#3fb950' : '#f85149'};font-weight:600">${p.value}</span>` },
  { field: 'quantity', headerName: 'Qty', width: 70 },
  { field: 'price', headerName: 'Price', width: 90, valueFormatter: p => p.value ? '₹' + p.value.toFixed(2) : '' },
  { field: 'turnover', headerName: 'Turnover', width: 110, valueFormatter: p => p.value ? INR(p.value) : '' },
  { field: 'brokerage', headerName: 'Brokerage', width: 85, valueFormatter: p => '₹' + (p.value || 0).toFixed(2) },
  { field: 'stt', headerName: 'STT', width: 80, valueFormatter: p => '₹' + (p.value || 0).toFixed(2) },
  { field: 'exchange_txn', headerName: 'Exch Txn', width: 80, valueFormatter: p => '₹' + (p.value || 0).toFixed(2) },
  { field: 'gst', headerName: 'GST', width: 75, valueFormatter: p => '₹' + (p.value || 0).toFixed(2) },
  { field: 'sebi_fee', headerName: 'SEBI', width: 70, valueFormatter: p => '₹' + (p.value || 0).toFixed(4) },
  { field: 'stamp_duty', headerName: 'Stamp', width: 80, valueFormatter: p => '₹' + (p.value || 0).toFixed(2) },
  { field: 'total_charges', headerName: 'Total Charges', width: 105, valueFormatter: p => INR(p.value || 0) },
  { field: 'net_amount', headerName: 'Net Amount', width: 110, valueFormatter: p => INR(p.value || 0) },
  { field: 'position_id', headerName: 'Pos ID', width: 70 },
  { field: 'status', headerName: 'Status', width: 90 },
  { field: 'broker_order_id', headerName: 'Order ID', width: 200 },
];

async function loadOrderLog() {
  try {
    const data = await fetch(API + '/orders?limit=500').then(r => r.json());
    const gridDiv = document.getElementById('order-grid');

    if (!orderGridApi) {
      orderGridApi = agGrid.createGrid(gridDiv, {
        columnDefs: orderColumnDefs,
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
      orderGridApi.setGridOption('rowData', data);
    }
  } catch (e) {
    console.error('Failed to load order log:', e);
  }
}

document.getElementById('btn-export-orders-csv').addEventListener('click', () => {
  if (orderGridApi) orderGridApi.exportDataAsCsv({ fileName: 'order_log.csv' });
});

// ── Ledger (ag-Grid) ────────────────────────────────────────────────────────

let ledgerGridApi = null;

function ledgerTypeCellRenderer(params) {
  const colors = { seed: '#58a6ff', injection: '#3fb950', withdrawal: '#f85149', buy: '#d29922', sell: '#bc8cff' };
  const labels = { seed: 'SEED', injection: 'INJECTION', withdrawal: 'WITHDRAWAL', buy: 'BUY', sell: 'SELL' };
  const c = colors[params.value] || '#8b949e';
  const l = labels[params.value] || params.value;
  return `<span style="color:${c};font-weight:600">${l}</span>`;
}

const ledgerColumnDefs = [
  { field: 'date', headerName: 'Date', width: 110 },
  { field: 'type', headerName: 'Type', width: 110, cellRenderer: ledgerTypeCellRenderer },
  { field: 'symbol', headerName: 'Symbol', width: 110 },
  { field: 'description', headerName: 'Description', width: 280, flex: 1 },
  { field: 'cash_in', headerName: 'Cash In', width: 120,
    valueFormatter: p => p.value ? INR(p.value) : '',
    cellStyle: { color: '#3fb950' } },
  { field: 'cash_out', headerName: 'Cash Out', width: 120,
    valueFormatter: p => p.value ? INR(p.value) : '',
    cellStyle: { color: '#f85149' } },
  { field: 'charges', headerName: 'Charges', width: 100,
    valueFormatter: p => p.value ? INR(p.value) : '',
    cellStyle: { color: '#d29922' } },
  { field: 'balance', headerName: 'Balance', width: 130,
    valueFormatter: p => p.value != null ? INR(p.value) : '',
    cellStyle: { fontWeight: '600' } },
];

async function loadLedger() {
  try {
    const data = await fetch(API + '/ledger').then(r => r.json());
    const gridDiv = document.getElementById('ledger-grid');

    if (!ledgerGridApi) {
      ledgerGridApi = agGrid.createGrid(gridDiv, {
        columnDefs: ledgerColumnDefs,
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
      ledgerGridApi.setGridOption('rowData', data);
    }
  } catch (e) {
    console.error('Failed to load ledger:', e);
  }
}

document.getElementById('btn-export-ledger-csv').addEventListener('click', () => {
  if (ledgerGridApi) ledgerGridApi.exportDataAsCsv({ fileName: 'cash_ledger.csv' });
});

// ── Init ────────────────────────────────────────────────────────────────────

loadSummary();
loadFunds();
loadOpenPositions();
loadProposed();
loadSignals();
loadEquityCurve();
loadCapitalEvents();
loadConfig();
