/**
 * Signal Quality Explorer — AG Grid-powered signal analysis UI
 */
(function () {
  "use strict";

  var gridApi = null;
  var allData = null;
  var currentFilters = {};
  var currentOffset = 0;
  var PAGE_SIZE = 10000;
  var totalRows = 0;

  // -------------------------------------------------------------------
  // Column Definitions
  // -------------------------------------------------------------------

  function pctFmt(params) {
    if (params.value == null) return "";
    return params.value.toFixed(2) + "%";
  }

  function numFmt(decimals) {
    return function (params) {
      if (params.value == null) return "";
      return params.value.toFixed(decimals);
    };
  }

  function symbolRenderer(params) {
    var link = document.createElement("span");
    link.className = "sq-symbol-link";
    link.textContent = params.value;
    link.addEventListener("click", function () {
      var date = params.data.signal_date;
      window.open("/de/dashboard/" + params.value, "_blank");
    });
    return link;
  }

  function typeRenderer(params) {
    var span = document.createElement("span");
    span.className = params.value === "Demand" ? "sq-type-demand" : "sq-type-supply";
    span.textContent = params.value;
    return span;
  }

  function hitRenderer(params) {
    if (params.value == null) return "";
    var span = document.createElement("span");
    span.className = params.value === 1 ? "sq-cell-hit" : "sq-cell-miss";
    span.textContent = params.value === 1 ? "HIT" : "MISS";
    return span;
  }

  function tierRenderer(params) {
    if (!params.value) return "";
    var span = document.createElement("span");
    span.className = "sq-tier sq-tier-" + params.value.toLowerCase();
    span.textContent = params.value;
    return span;
  }

  function retStyle(params) {
    if (params.value == null) return {};
    return { color: params.value > 0 ? "#3fb950" : params.value < 0 ? "#f85149" : "#8b949e" };
  }

  var columnDefs = [
    {
      headerName: "Signal",
      children: [
        { field: "symbol", headerName: "Symbol", cellRenderer: symbolRenderer, filter: "agTextColumnFilter", pinned: "left", width: 120 },
        { field: "signal_date", headerName: "Date", filter: "agDateColumnFilter", sort: "desc", width: 110 },
        { field: "signal_type", headerName: "Type", cellRenderer: typeRenderer, filter: "agSetColumnFilter", width: 90 },
        { field: "conviction_score", headerName: "Conv", valueFormatter: numFmt(1), filter: "agNumberColumnFilter", width: 75 },
        { field: "volume_tier", headerName: "Tier", cellRenderer: tierRenderer, filter: "agSetColumnFilter", width: 85 },
        { field: "entry_close", headerName: "Entry", valueFormatter: numFmt(2), filter: "agNumberColumnFilter", width: 90 },
        { field: "avg_del_val", headerName: "Avg Del (Cr)", valueFormatter: numFmt(1), filter: "agNumberColumnFilter", width: 105 },
      ]
    },
    {
      headerName: "Returns",
      children: [
        { field: "ret_3d", headerName: "3d %", valueFormatter: pctFmt, filter: "agNumberColumnFilter", width: 80, cellStyle: retStyle },
        { field: "ret_5d", headerName: "5d %", valueFormatter: pctFmt, filter: "agNumberColumnFilter", width: 80, cellStyle: retStyle },
        { field: "ret_10d", headerName: "10d %", valueFormatter: pctFmt, filter: "agNumberColumnFilter", width: 80, cellStyle: retStyle },
      ]
    },
    {
      headerName: "Hit/Miss",
      children: [
        { field: "hit_3d", headerName: "3d", cellRenderer: hitRenderer, filter: "agSetColumnFilter", width: 70 },
        { field: "hit_5d", headerName: "5d", cellRenderer: hitRenderer, filter: "agSetColumnFilter", width: 70 },
        { field: "hit_10d", headerName: "10d", cellRenderer: hitRenderer, filter: "agSetColumnFilter", width: 70 },
      ]
    },
    {
      headerName: "MFE / MAE",
      children: [
        { field: "mfe_3d", headerName: "MFE 3d", valueFormatter: pctFmt, filter: "agNumberColumnFilter", width: 85 },
        { field: "mfe_5d", headerName: "MFE 5d", valueFormatter: pctFmt, filter: "agNumberColumnFilter", width: 85 },
        { field: "mfe_10d", headerName: "MFE 10d", valueFormatter: pctFmt, filter: "agNumberColumnFilter", width: 85 },
        { field: "mae_3d", headerName: "MAE 3d", valueFormatter: pctFmt, filter: "agNumberColumnFilter", width: 85 },
        { field: "mae_5d", headerName: "MAE 5d", valueFormatter: pctFmt, filter: "agNumberColumnFilter", width: 85 },
        { field: "mae_10d", headerName: "MAE 10d", valueFormatter: pctFmt, filter: "agNumberColumnFilter", width: 85 },
      ]
    },
    {
      headerName: "Features",
      children: [
        { field: "cwc", headerName: "CWC", valueFormatter: numFmt(4), filter: "agNumberColumnFilter", width: 80 },
        { field: "rdv", headerName: "RDV", valueFormatter: numFmt(3), filter: "agNumberColumnFilter", width: 80 },
        { field: "rdv_consistency", headerName: "RDV Con", filter: "agNumberColumnFilter", width: 80 },
        { field: "cwvap_dist", headerName: "CWVAP Dist", valueFormatter: numFmt(2), filter: "agNumberColumnFilter", width: 100 },
        { field: "delivery_pct", headerName: "Del %", valueFormatter: numFmt(1), filter: "agNumberColumnFilter", width: 80 },
        { field: "pdd_30", headerName: "PDD 30", valueFormatter: numFmt(2), filter: "agNumberColumnFilter", width: 80 },
        { field: "coherence", headerName: "Coh", valueFormatter: numFmt(3), filter: "agNumberColumnFilter", width: 80 },
        { field: "price_slope_z", headerName: "P Slope Z", valueFormatter: numFmt(3), filter: "agNumberColumnFilter", width: 90 },
        { field: "rdv_slope_z", headerName: "RDV Slope Z", valueFormatter: numFmt(3), filter: "agNumberColumnFilter", width: 100 },
        { field: "mcs_composite", headerName: "MCS", valueFormatter: numFmt(3), filter: "agNumberColumnFilter", width: 80 },
        { field: "cwc_slope", headerName: "CWC Slope", valueFormatter: numFmt(4), filter: "agNumberColumnFilter", width: 95 },
        { field: "price_distance_30", headerName: "P Dist 30", valueFormatter: numFmt(2), filter: "agNumberColumnFilter", width: 90 },
        { field: "velocity_30_norm", headerName: "Vel 30", valueFormatter: numFmt(3), filter: "agNumberColumnFilter", width: 80 },
        { field: "atr_20", headerName: "ATR", valueFormatter: numFmt(2), filter: "agNumberColumnFilter", width: 80 },
      ]
    },
  ];

  // -------------------------------------------------------------------
  // Grid Setup
  // -------------------------------------------------------------------

  var gridOptions = {
    columnDefs: columnDefs,
    defaultColDef: {
      sortable: true,
      resizable: true,
      filter: true,
      floatingFilter: true,
      minWidth: 60,
    },
    rowData: [],
    animateRows: false,
    rowSelection: { type: "single" },
    enableCellTextSelection: true,
    suppressCellFocus: true,
    getRowStyle: function (params) {
      if (params.data && params.data.hit_5d === 0) {
        return { background: "rgba(248, 81, 73, 0.04)" };
      }
      return null;
    },
  };

  var gridDiv = document.getElementById("signal-grid");
  gridApi = agGrid.createGrid(gridDiv, gridOptions);

  // -------------------------------------------------------------------
  // Server-side Filter Buttons
  // -------------------------------------------------------------------

  function initFilterBar() {
    var bar = document.getElementById("filter-bar");
    if (!bar) return;

    // Type filter
    bar.querySelectorAll(".sq-filter-type").forEach(function (btn) {
      btn.addEventListener("click", function () {
        bar.querySelectorAll(".sq-filter-type").forEach(function (b) { b.classList.remove("active"); });
        this.classList.add("active");
        currentFilters.signal_type = this.dataset.type || "";
        currentOffset = 0;
        loadData();
      });
    });

    // Tier filter
    bar.querySelectorAll(".sq-filter-tier").forEach(function (btn) {
      btn.addEventListener("click", function () {
        bar.querySelectorAll(".sq-filter-tier").forEach(function (b) { b.classList.remove("active"); });
        this.classList.add("active");
        currentFilters.volume_tier = this.dataset.tier || "";
        currentOffset = 0;
        loadData();
      });
    });

    // Hit/Miss filter
    bar.querySelectorAll(".sq-filter-hit").forEach(function (btn) {
      btn.addEventListener("click", function () {
        bar.querySelectorAll(".sq-filter-hit").forEach(function (b) { b.classList.remove("active"); });
        this.classList.add("active");
        var val = this.dataset.hit;
        if (val === "") {
          delete currentFilters.hit_horizon;
          delete currentFilters.hit_value;
        } else {
          currentFilters.hit_horizon = "hit_5d";
          currentFilters.hit_value = val;
        }
        currentOffset = 0;
        loadData();
      });
    });
  }

  // -------------------------------------------------------------------
  // Pagination
  // -------------------------------------------------------------------

  function updatePagination() {
    var info = document.getElementById("page-info");
    var prevBtn = document.getElementById("page-prev");
    var nextBtn = document.getElementById("page-next");
    if (!info) return;

    var end = Math.min(currentOffset + PAGE_SIZE, totalRows);
    info.textContent = (currentOffset + 1) + "-" + end + " of " + totalRows;
    if (prevBtn) prevBtn.disabled = currentOffset === 0;
    if (nextBtn) nextBtn.disabled = end >= totalRows;
  }

  var prevBtn = document.getElementById("page-prev");
  var nextBtn = document.getElementById("page-next");
  if (prevBtn) prevBtn.addEventListener("click", function () {
    currentOffset = Math.max(0, currentOffset - PAGE_SIZE);
    loadData();
  });
  if (nextBtn) nextBtn.addEventListener("click", function () {
    currentOffset += PAGE_SIZE;
    loadData();
  });

  // -------------------------------------------------------------------
  // Data Loading
  // -------------------------------------------------------------------

  function loadData(runDate) {
    var url = "/de/api/signal-quality?limit=" + PAGE_SIZE + "&offset=" + currentOffset;
    if (runDate) {
      url += "&run_date=" + runDate;
    } else {
      var sel = document.getElementById("run-select");
      if (sel && sel.value) url += "&run_date=" + sel.value;
    }
    if (currentFilters.signal_type) url += "&signal_type=" + currentFilters.signal_type;
    if (currentFilters.volume_tier) url += "&volume_tier=" + currentFilters.volume_tier;
    if (currentFilters.hit_horizon) {
      url += "&hit_horizon=" + currentFilters.hit_horizon + "&hit_value=" + currentFilters.hit_value;
    }

    gridApi.showLoadingOverlay();

    fetch(url)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        allData = data;
        totalRows = data.total;

        // Populate run selector (only on first load)
        var sel = document.getElementById("run-select");
        if (sel.options.length <= 1) {
          sel.innerHTML = data.runs.map(function (r) {
            var selected = r.run_date === data.current_run ? " selected" : "";
            return '<option value="' + r.run_date + '"' + selected + '>'
              + r.run_date + ' (' + r.signal_count + ')</option>';
          }).join("");
        }

        // Update count badge
        document.getElementById("signal-count").textContent =
          data.signal_count + " / " + data.total + " signals";

        // Update grid
        gridApi.setGridOption("rowData", data.signals);
        gridApi.hideOverlay();

        // Update summary & pagination
        buildSummary(data.summary, data.total);
        updatePagination();
      })
      .catch(function (err) {
        console.error("Error loading signal quality data:", err);
        gridApi.hideOverlay();
      });
  }

  document.getElementById("run-select").addEventListener("change", function () {
    currentOffset = 0;
    loadData(this.value);
  });

  // -------------------------------------------------------------------
  // Summary Panel
  // -------------------------------------------------------------------

  function buildSummary(summary, total) {
    var panel = document.getElementById("summary-panel");
    if (!summary || !total) {
      panel.innerHTML = '<div class="sq-card"><div class="sq-card-title">No Data</div>'
        + '<div class="sq-card-value sq-neutral">Run the report first</div></div>';
      return;
    }

    var html = '';

    // Overview card
    html += '<div class="sq-card">'
      + '<div class="sq-card-title">Total</div>'
      + '<div class="sq-card-value">' + total.toLocaleString() + '</div>'
      + '</div>';

    // Type cards
    ["Demand", "Supply"].forEach(function (t) {
      var s = summary[t];
      if (!s) return;
      html += '<div class="sq-card">'
        + '<div class="sq-card-title">' + t + '</div>';
      [3, 5, 10].forEach(function (h) {
        var d = s[h];
        if (!d) return;
        var hitColor = d.hit_rate >= 55 ? "sq-hit" : d.hit_rate <= 45 ? "sq-miss" : "sq-neutral";
        html += '<div class="sq-card-row">'
          + '<span class="sq-neutral">' + h + 'd:</span>'
          + '<span class="' + hitColor + '">' + d.hit_rate + '%</span>'
          + '<span class="' + (d.avg_ret >= 0 ? "sq-hit" : "sq-miss") + '">'
          + (d.avg_ret >= 0 ? "+" : "") + d.avg_ret + '%</span>'
          + '<span class="sq-neutral">n=' + d.n + '</span>'
          + '</div>';
      });
      html += '</div>';
    });

    // Tier cards
    var tierData = summary.by_tier || {};
    var tierOrder = ["Large", "Mid", "Small", "Micro"];
    tierOrder.forEach(function (tier) {
      var td = tierData[tier];
      if (!td) return;
      html += '<div class="sq-card">'
        + '<div class="sq-card-title">' + tier + '</div>';
      [3, 5, 10].forEach(function (h) {
        var d = td[h];
        if (!d) return;
        var hitColor = d.hit_rate >= 55 ? "sq-hit" : d.hit_rate <= 45 ? "sq-miss" : "sq-neutral";
        html += '<div class="sq-card-row">'
          + '<span class="sq-neutral">' + h + 'd:</span>'
          + '<span class="' + hitColor + '">' + d.hit_rate + '%</span>'
          + '<span class="sq-neutral">n=' + d.n + '</span>'
          + '</div>';
      });
      html += '</div>';
    });

    panel.innerHTML = html;
  }

  // Init
  initFilterBar();
  loadData();

})();
