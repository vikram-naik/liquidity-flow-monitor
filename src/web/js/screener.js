class SelectFilter {
  init(params) {
    this.params = params;
    this.filterValues = [];
    this.gui = document.createElement('div');
    this.gui.style.padding = '12px';
    this.gui.style.background = '#0d1117';
    this.gui.style.minWidth = '150px';
    
    const options = params.options || (params.filterParams && params.filterParams.options) || [];
    
    let html = '<div style="display:flex; flex-direction:column; gap:8px;">';
    options.forEach(opt => {
        html += `
          <label style="display:flex; align-items:center; gap:8px; color:#c9d1d9; font-size:13px; cursor:pointer; user-select:none;">
            <input type="checkbox" value="${opt.value}" class="ag-filter-checkbox" style="cursor:pointer; width:14px; height:14px; accent-color:#58a6ff;" />
            ${opt.label}
          </label>
        `;
    });
    html += '</div>';

    this.gui.innerHTML = html;
    
    this.checkboxes = this.gui.querySelectorAll('input[type="checkbox"]');
    this.checkboxes.forEach(cb => {
      cb.addEventListener('change', () => {
        this.filterValues = Array.from(this.checkboxes)
          .filter(c => c.checked)
          .map(c => c.value);
        this.params.filterChangedCallback();
      });
    });
  }

  getGui() { return this.gui; }
  
  isFilterActive() { 
    return this.filterValues.length > 0; 
  }
  
  doesFilterPass(params) {
    try {
      let value;
      if (typeof this.params.getValue === 'function') {
         value = this.params.getValue(params.node);
      } else if (typeof this.params.valueGetter === 'function') {
         value = this.params.valueGetter(params.node);
      } else {
         value = params.node.data[this.params.colDef.field];
      }
      
      if (this.filterValues.length === 0) return true;
      if (value == null) return false;
      
      const cellValue = value.toString().toLowerCase();
      
      // OR logic: match if the cellValue includes ANY of the selected filter values
      return this.filterValues.some(fv => cellValue.includes(fv.toLowerCase()));
    } catch (e) {
      console.error("Filter Error in doesFilterPass:", e);
      return false;
    }
  }

  getModel() {
    return this.isFilterActive() ? { values: this.filterValues } : null;
  }

  setModel(model) {
    this.filterValues = model && model.values ? model.values : [];
    this.checkboxes.forEach(cb => {
      cb.checked = this.filterValues.includes(cb.value);
    });
  }

  afterGuiAttached() {
  }
}

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
      width: 120,
      filter: SelectFilter,
      filterParams: {
          options: [
              { label: 'ENTRY', value: 'entry' },
              { label: 'IN-TRADE', value: 'in-trade' },
              { label: 'EXIT', value: 'exit' },
              { label: 'TECH-ONLY', value: 'none' }
          ]
      },
      cellRenderer: params => {
          const val = params.value;
          if (val === 'entry') return '<span class="signal-entry" style="color:#3fb950; font-weight:bold">ENTRY</span>';
          if (val === 'exit') return '<span class="signal-exit" style="color:#f85149; font-weight:bold">EXIT</span>';
          if (val === 'in-trade') return '<span class="signal-in-trade" style="color:#d29922; font-weight:bold">IN-TRADE</span>';
          return val === 'none' ? '-' : val;
      }
  },
  { 
      colId: 'tech_signals',
      headerName: 'Tech Signals',
      width: 250,
      filter: SelectFilter,
      filterParams: {
          options: [
              { label: 'C-UP', value: 'C-UP' },
              { label: 'C-DOWN', value: 'C-DOWN' },
              { label: 'MAX-CTS', value: 'MAX-CTS' },
              { label: 'MIN-CTS', value: 'MIN-CTS' }
          ]
      },
      valueGetter: p => {
          let res = [];
          if (p.data.c_up) res.push('C-UP');
          if (p.data.c_down) res.push('C-DOWN');
          if (p.data.max_cts) res.push('MAX-CTS');
          if (p.data.min_cts) res.push('MIN-CTS');
          return res.join(', ');
      },
      cellRenderer: params => {
          const data = params.data;
          let html = '<div style="display:flex; gap:4px; align-items:center; height:100%">';
          if (data.c_up) html += '<span style="background:#238636; color:#fff; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:bold">C-UP</span>';
          if (data.c_down) html += '<span style="background:#da3633; color:#fff; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:bold">C-DOWN</span>';
          if (data.max_cts) html += '<span style="background:#0d419d; color:#fff; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:bold">MAX-CTS</span>';
          if (data.min_cts) html += '<span style="background:#30363d; color:#8b949e; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:bold">MIN-CTS</span>';
          html += '</div>';
          return html;
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

const techColumnDefs = columnDefs;

const guardColumnDefs = [
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
      field: 'setup_tag', 
      headerName: 'Setup Tag', 
      width: 120,
      cellRenderer: params => {
          const val = params.value;
          let bg = '#30363d', fg = '#c9d1d9';
          if (val === 'SIAB') { bg = '#0d419d'; fg = '#fff'; }
          else if (val === 'CDMA') { bg = '#238636'; fg = '#fff'; }
          else if (val === 'CLFR') { bg = '#da3633'; fg = '#fff'; }
          else if (val === 'ISP') { bg = '#d29922'; fg = '#fff'; }
          return `<span style="background:${bg}; color:${fg}; padding:4px 8px; border-radius:12px; font-size:10px; font-weight:bold">${val}</span>`;
      }
  },
  { field: 'gate_score', headerName: 'Gate Score', width: 120, valueFormatter: p => p.value ? p.value.toFixed(2) : '-' },
  { 
      field: 'verdict', 
      headerName: 'LLM Verdict', 
      width: 130,
      cellRenderer: params => {
          const val = params.value;
          if (val === 'APPROVE') {
              return `<span style="background:#2ea043; color:#fff; padding:4px 8px; border-radius:4px; font-size:10px; font-weight:bold; letter-spacing:0.5px">APPROVED</span>`;
          }
          return `<span style="background:#f85149; color:#fff; padding:4px 8px; border-radius:4px; font-size:10px; font-weight:bold; letter-spacing:0.5px">VETOED</span>`;
      }
  },
  { 
      field: 'qualitative_score', 
      headerName: 'Qualitative Score', 
      width: 140, 
      valueFormatter: p => p.value ? p.value.toFixed(2) : '-',
      cellStyle: params => {
          const v = params.value;
          if (v >= 80) return { color: '#3fb950', fontWeight: 'bold' };
          if (v < 60) return { color: '#f85149', fontWeight: 'bold' };
          return { color: '#d29922', fontWeight: 'bold' };
      }
  },
  { 
      field: 'red_flags', 
      headerName: 'Forensic Red Flags', 
      width: 380,
      autoHeight: true,
      cellRenderer: params => {
          try {
              const flags = JSON.parse(params.value || '[]');
              if (!flags || flags.length === 0) return `<span style="color:#8b949e">—</span>`;
              let html = '<ul style="margin: 0; padding-left: 14px; font-size: 11px; color: #8b949e; line-height: 1.4; white-space: normal; list-style-type: square; margin-top: 4px; margin-bottom: 4px;">';
              flags.forEach(f => {
                  html += `<li>${f}</li>`;
              });
              html += '</ul>';
              return html;
          } catch (e) {
              return params.value || '-';
          }
      }
  },
  { 
      field: 'citations', 
      headerName: 'Citations & Sources', 
      width: 250,
      cellRenderer: params => {
          try {
              const urls = JSON.parse(params.value || '[]');
              if (!urls || urls.length === 0) return '—';
              return `<div style="display:flex; flex-direction:column; gap:4px; justify-content:center; height:100%">${urls.map((url, i) => `<a href="${url}" target="_blank" style="color:#58a6ff; font-size:11px; text-decoration:none;">Source [${i+1}] ↗</a>`).join('')}</div>`;
          } catch (e) {
              return '—';
          }
      }
  }
];

const gridOptions = {
  columnDefs: techColumnDefs,
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
  window.gridApi = gridApi;
  
  let allTechData = [];
  let cachedGuardData = [];
  let currentTab = 'tech';

  window.applyFilters = function() {
      if (currentTab !== 'tech') return;
      
      const showEntries = document.getElementById('chk-entries')?.checked;
      const showInTrade = document.getElementById('chk-intrade')?.checked;
      const showExits = document.getElementById('chk-exits')?.checked;
      const showTech = document.getElementById('chk-tech')?.checked;

      const filteredData = allTechData.filter(d => {
          if (d.signal_type === 'entry' && showEntries) return true;
          if (d.signal_type === 'in-trade' && showInTrade) return true;
          if (d.signal_type === 'exit' && showExits) return true;
          if (d.signal_type === 'none' && showTech) return true;
          return false;
      });

      gridApi.setGridOption('rowData', filteredData);
  };

  window.switchTab = function(tabName) {
      if (tabName === currentTab) return;
      
      // Update tab buttons state
      document.getElementById('btn-tab-tech').classList.toggle('active', tabName === 'tech');
      document.getElementById('btn-tab-guard').classList.toggle('active', tabName === 'guard');
      currentTab = tabName;
      
      const summaryEl = document.getElementById('screener-summary');
      if (tabName === 'tech') {
          // Switch back to technical grid
          if (summaryEl) {
              summaryEl.style.display = 'grid';
          }
          gridApi.setGridOption('columnDefs', techColumnDefs);
          gridApi.setGridOption('rowData', allTechData);
      } else {
          // Switch to forensic audit grid
          if (summaryEl) {
              summaryEl.style.display = 'none';
          }
          
          if (cachedGuardData.length > 0) {
              gridApi.setGridOption('columnDefs', guardColumnDefs);
              gridApi.setGridOption('rowData', cachedGuardData);
          } else {
              gridApi.setGridOption('rowData', []);
              fetch('/de/api/screener/gate-guard')
                  .then(r => r.json())
                  .then(data => {
                       cachedGuardData = data;
                       gridApi.setGridOption('columnDefs', guardColumnDefs);
                       gridApi.setGridOption('rowData', data);
                  })
                  .catch(e => console.error("Error loading Gate-Guard qualitative data", e));
          }
      }

      // Smoothly trigger grid size adjustment to match new layout proportions
      setTimeout(() => {
          if (window.gridApi) {
              window.gridApi.sizeColumnsToFit();
          }
      }, 50);
  };

  fetch('/de/api/screener')
      .then(r => r.json())
      .then(data => {
          allTechData = data;
          gridApi.setGridOption('rowData', data);
          
          let entries = 0, exits = 0, inTrade = 0, techOnly = 0;
          data.forEach(d => {
              if (d.signal_type === 'entry') entries++;
              else if (d.signal_type === 'exit') exits++;
              else if (d.signal_type === 'in-trade') inTrade++;
              else if (d.signal_type === 'none') techOnly++;
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
                  <label class="card" style="border-color:#58a6ff; cursor:pointer; user-select:none; display:flex; flex-direction:column;">
                    <div class="label" style="display:flex; justify-content:space-between; align-items:center;">
                        <span>Tech Only</span>
                        <input type="checkbox" id="chk-tech" checked onchange="window.applyFilters()">
                    </div>
                    <div class="value" style="color:#58a6ff">${techOnly}</div>
                  </label>
              `;
          }
      })
      .catch(e => console.error("Error loading screener data", e));
});
