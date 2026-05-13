const columnDefs = [
  { 
      field: 'symbol', 
      headerName: 'Symbol', 
      width: 150, 
      pinned: 'left',
      cellRenderer: params => {
          const s = params.value;
          const d = params.data.date;
          return `<a href="/de/dashboard/${s}?focus=${d}" class="back-link" target="_blank">${s}</a>`;
      }
  },
  { field: 'date', headerName: 'Date', width: 120 },
  { field: 'price', headerName: 'Price (₹)', width: 120, valueFormatter: p => p.value ? p.value.toFixed(2) : '-' },
  { 
      field: 'signal_type', 
      headerName: 'State', 
      width: 150,
      cellRenderer: params => {
          const val = params.value;
          if (val === 'entry') return '<span class="signal-entry">ENTRY</span>';
          if (val === 'exit') return '<span class="signal-exit">EXIT</span>';
          if (val === 'in-trade') return '<span class="signal-in-trade">IN-TRADE</span>';
          return val;
      }
  },
  { field: 'entry_date', headerName: 'Entry Date', width: 120 },
  { field: 'entry_price', headerName: 'Entry Price', width: 120, valueFormatter: p => p.value ? p.value.toFixed(2) : '-' },
  { field: 'bars_held', headerName: 'Bars Held', width: 100 },
  { 
      field: 'mfe_pct', 
      headerName: 'MFE (%)', 
      width: 110,
      cellRenderer: params => {
          if (params.value == null) return '-';
          const v = params.value;
          return `<span style="color:#3fb950">${v > 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
      }
  },
  { 
      field: 'mae_pct', 
      headerName: 'MAE (%)', 
      width: 110,
      cellRenderer: params => {
          if (params.value == null) return '-';
          const v = params.value;
          return `<span style="color:#f85149">${v > 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
      }
  },
  { 
      field: 'pnl', 
      headerName: 'P&L (%)', 
      width: 120,
      cellRenderer: params => {
          if (params.value == null) return '-';
          const v = params.value;
          const cls = v >= 0 ? 'pnl-pos' : 'pnl-neg';
          const sign = v > 0 ? '+' : '';
          return `<span class="${cls}">${sign}${v.toFixed(2)}%</span>`;
      }
  }
];

const gridOptions = {
  columnDefs: columnDefs,
  rowData: [],
  defaultColDef: {
      sortable: true,
      filter: true,
      resizable: true,
  },
  animateRows: true,
  pagination: true,
  paginationPageSize: 100,
};

document.addEventListener('DOMContentLoaded', () => {
  const gridDiv = document.querySelector('#screener-grid');
  const gridApi = agGrid.createGrid(gridDiv, gridOptions);
  let allData = [];

  window.applyFilters = function() {
      const showEntries = document.getElementById('chk-entries')?.checked;
      const showInTrade = document.getElementById('chk-intrade')?.checked;
      const showExits = document.getElementById('chk-exits')?.checked;

      const filteredData = allData.filter(d => {
          if (d.signal_type === 'entry' && showEntries) return true;
          if (d.signal_type === 'in-trade' && showInTrade) return true;
          if (d.signal_type === 'exit' && showExits) return true;
          return false;
      });

      gridApi.setGridOption('rowData', filteredData);
  };

  fetch('/de/api/screener')
      .then(r => r.json())
      .then(data => {
          allData = data;
          gridApi.setGridOption('rowData', data);
          
          let entries = 0, exits = 0, inTrade = 0;
          data.forEach(d => {
              if (d.signal_type === 'entry') entries++;
              else if (d.signal_type === 'exit') exits++;
              else if (d.signal_type === 'in-trade') inTrade++;
          });
          
          const summaryDiv = document.getElementById('screener-summary');
          if (summaryDiv) {
              summaryDiv.innerHTML = `
                  <div class="card">
                    <div class="label">Total Scanned</div>
                    <div class="value">${data.length}</div>
                  </div>
                  <label class="card" style="border-color:#3fb950; cursor:pointer; user-select:none; display:flex; flex-direction:column;">
                    <div class="label" style="display:flex; justify-content:space-between; align-items:center;">
                        <span>New Entries</span>
                        <input type="checkbox" id="chk-entries" checked onchange="window.applyFilters()">
                    </div>
                    <div class="value" style="color:#3fb950">${entries}</div>
                  </label>
                  <label class="card" style="border-color:#d29922; cursor:pointer; user-select:none; display:flex; flex-direction:column;">
                    <div class="label" style="display:flex; justify-content:space-between; align-items:center;">
                        <span>In-Trade</span>
                        <input type="checkbox" id="chk-intrade" checked onchange="window.applyFilters()">
                    </div>
                    <div class="value" style="color:#d29922">${inTrade}</div>
                  </label>
                  <label class="card" style="border-color:#f85149; cursor:pointer; user-select:none; display:flex; flex-direction:column;">
                    <div class="label" style="display:flex; justify-content:space-between; align-items:center;">
                        <span>Exits</span>
                        <input type="checkbox" id="chk-exits" checked onchange="window.applyFilters()">
                    </div>
                    <div class="value" style="color:#f85149">${exits}</div>
                  </label>
              `;
          }
      })
      .catch(e => console.error("Error loading screener data", e));
});
