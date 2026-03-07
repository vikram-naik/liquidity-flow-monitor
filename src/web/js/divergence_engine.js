/**
 * Divergence Engine — Chart Initialisation (lightweight-charts v5 API)
 *
 * Final Refinements (Phase 10):
 *   - ResizeObserver for robust layout detection
 *   - Proportional Dynamic Reflow
 *   - Sidebar "Push" Layout Support
 */

(function () {
    "use strict";

    var parts = window.location.pathname.split("/");
    var symbol = parts[parts.length - 1] || "RELIANCE";
    var aggMode = "daily";

    var params = new URLSearchParams(window.location.search);
    var apiUrl = "/de/api/divergence-engine/" + symbol + "?agg_mode=" + aggMode;
    if (params.get("start_date")) apiUrl += "&start_date=" + params.get("start_date");
    if (params.get("end_date")) {
        apiUrl += "&end_date=" + params.get("end_date");
    }

    document.title = "Divergence Engine \u2014 " + symbol;
    document.getElementById("ticker-label").textContent = symbol;

    var loadingEl = document.getElementById("loading");
    var tooltipEl = document.getElementById("tooltip");
    var sidebarEl = document.getElementById("sidebar");
    var sidebarBtn = document.getElementById("toggle-sidebar");

    sidebarBtn.addEventListener("click", function () {
        sidebarEl.classList.toggle("collapsed");
        sidebarBtn.classList.toggle("active");
        // No manual reflow call here; ResizeObserver will catch the width change
    });

    var chartInstances = [];

    // --- Aggregation Mode Toggle ---
    document.querySelectorAll(".agg-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            document.querySelectorAll(".agg-btn").forEach(function (b) { b.classList.remove("active"); });
            this.classList.add("active");
            aggMode = this.dataset.mode;
            loadSymbol(symbol);
        });
    });

    function loadSymbol(targetSymbol) {
        if (!targetSymbol) return;
        symbol = targetSymbol.toUpperCase();
        var apiUrl = "/de/api/divergence-engine/" + symbol + "?agg_mode=" + aggMode;
        if (params.get("start_date")) apiUrl += "&start_date=" + params.get("start_date");
        if (params.get("end_date")) {
            apiUrl += "&end_date=" + params.get("end_date");
        }

        loadingEl.style.display = "flex";
        loadingEl.innerHTML = "Loading Engine Data...";
        document.title = "Divergence Engine \u2014 " + symbol;
        document.getElementById("ticker-label").textContent = symbol;

        fetch(apiUrl)
            .then(function (res) {
                if (!res.ok) throw new Error("API error: " + res.status);
                return res.json();
            })
            .then(function (data) {
                loadingEl.style.display = "none";

                // Display Last Data Date
                const dateDisplay = document.getElementById("last-data-date");
                if (dateDisplay && data.last_data_date) {
                    dateDisplay.textContent = formatDate(data.last_data_date);
                }

                // Clear existing charts
                ["p1", "p2", "p3"].forEach(id => {
                    var el = document.getElementById(id);
                    if (el) el.innerHTML = '<div id="leg' + id.slice(1) + '" class="legend"></div>';
                });

                chartInstances = buildCharts(data);
                refreshUI(); // Ensure legends and sidebar are populated immediately
                buildSidebarAnnotations(data.latest);
                WatchlistManager.updateActiveState();
                window.history.pushState({}, "", "/de/dashboard/" + symbol);
            })
            .catch(function (err) {
                loadingEl.innerHTML = '<div class="err">Error: ' + err.message + "</div>";
            });
    }

    // Initial load
    loadSymbol(symbol);


    function buildCharts(data) {
        var ledger = data.ledger;
        var ohlc = [], cwvap = [], cpoc = [];
        var deliveryVol = [];
        var markerList = [];
        var timeToIndex = {};
        var pZ = [], rZ = [], cRaw = [], cSmooth = [];

        for (var i = 0; i < ledger.length; i++) {
            var r = ledger[i];
            // Use the date string directly if it is in YYYY-MM-DD format
            // otherwise parse it. Lightweight Charts supports YYYY-MM-DD strings.
            var t = r.date ? (r.date.includes(" ") ? r.date.split(" ")[0] : r.date) : null;
            if (!t) continue;
            timeToIndex[t] = i;

            if (r.open != null && r.high != null && r.low != null && r.close != null) {
                ohlc.push({ time: t, open: r.open, high: r.high, low: r.low, close: r.close });
            }
            if (r.cwvap != null) cwvap.push({ time: t, value: r.cwvap }); else cwvap.push({ time: t });
            if (r.cpoc != null) cpoc.push({ time: t, value: r.cpoc }); else cpoc.push({ time: t });

            if (r.price_slope_z != null) pZ.push({ time: t, value: r.price_slope_z }); else pZ.push({ time: t, value: 0 });
            if (r.rdv_slope_z != null) rZ.push({ time: t, value: r.rdv_slope_z }); else rZ.push({ time: t, value: 0 });

            // For coherence, ensure we still push *something* (e.g. 0 or NaN/blank representation)
            // LightweightCharts line series allows whitespace gaps using empty objects `{ time: t }`
            if (r.coherence_raw != null) { cRaw.push({ time: t, value: r.coherence_raw }); } else { cRaw.push({ time: t }); }
            if (r.coherence != null) { cSmooth.push({ time: t, value: r.coherence }); } else { cSmooth.push({ time: t }); }

            if (r.delivery_qty != null) {
                var mfm = r.mfm != null ? r.mfm : 0;
                deliveryVol.push({
                    time: t, value: r.delivery_qty,
                    color: mfm >= 0 ? "rgba(38,166,154,0.35)" : "rgba(239,83,80,0.35)"
                });
            }
        }

        // --- Marker Logic (Demand/Supply Conviction Markers) ---
        var stateColorMap = {
            "Demand": "#00e676",
            "Supply": "#ef5350"
        };

        var markerList = [];
        for (var i = 0; i < ledger.length; i++) {
            var r = ledger[i];
            var t = r.date ? (r.date.includes(" ") ? r.date.split(" ")[0] : r.date) : null;
            if (!t) continue;

            var stateStr = r.integrated_state || "No Signal";
            if (stateStr === "Demand" || stateStr === "Supply") {
                var isDemand = stateStr === "Demand";
                var convScore = r.conviction_score != null ? r.conviction_score : "";
                markerList.push({
                    time: t,
                    position: isDemand ? "belowBar" : "aboveBar",
                    color: stateColorMap[stateStr],
                    shape: isDemand ? "arrowUp" : "arrowDown",
                    stateText: stateStr + (convScore !== "" ? " (" + convScore + ")" : "")
                });
            }
        }


        var LC = LightweightCharts;

        function mkOpts(w, h, showTimeScale) {
            return {
                width: w, height: h,
                layout: {
                    background: { color: "#0d1117" },
                    textColor: "#8b949e",
                    padding: { left: 0, right: 0, top: 0, bottom: 0 }
                },
                grid: { vertLines: { color: "#21262d" }, horzLines: { color: "#21262d" } },
                crosshair: { mode: 0 },
                timeScale: {
                    visible: !!showTimeScale,
                    borderColor: "#21262d",
                    timeVisible: false
                },
                leftPriceScale: { visible: false, width: 0, minimumWidth: 0 },
                rightPriceScale: { visible: true, borderColor: "#21262d", width: 100, minimumWidth: 100 }
            };
        }

        var container = document.getElementById("chart-container");

        function getW(id) { var el = document.getElementById(id); return el ? el.clientWidth : 800; }
        function getH(id) { var el = document.getElementById(id); return el ? el.clientHeight : 300; }

        var pc = LC.createChart(document.getElementById("p1"), mkOpts(getW("p1"), getH("p1"), false));
        var pc2 = LC.createChart(document.getElementById("p2"), mkOpts(getW("p2"), getH("p2"), false));
        var pc3 = LC.createChart(document.getElementById("p3"), mkOpts(getW("p3"), getH("p3"), true));

        // --- Series p1 ---
        var sVol = pc.addSeries(LC.HistogramSeries, { priceScaleId: "vol", priceLineVisible: false, lastValueVisible: false });
        sVol.setData(deliveryVol);
        pc.priceScale("vol").applyOptions({ visible: false, scaleMargins: { top: 0.75, bottom: 0 } });

        var cs = pc.addSeries(LC.CandlestickSeries, {
            upColor: "#26a69a", downColor: "#ef5350", borderUpColor: "#26a69a", borderDownColor: "#ef5350",
            wickUpColor: "#26a69a", wickDownColor: "#ef5350", lastValueVisible: false, priceLineVisible: false
        });
        cs.setData(ohlc);

        var sCwvap = pc.addSeries(LC.LineSeries, { color: "#00bfa5", lineWidth: 2, lastValueVisible: false });
        sCwvap.setData(cwvap);

        var sCpoc = pc.addSeries(LC.LineSeries, { color: "#ffab40", lineWidth: 2, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
        var lastCpocVal = null;
        for (var i = cpoc.length - 1; i >= 0; i--) {
            if (cpoc[i].value != null) { lastCpocVal = cpoc[i].value; break; }
        }
        if (lastCpocVal != null) {
            sCpoc.setData(cpoc.map(d => ({ time: d.time, value: lastCpocVal })));
        } else {
            // No valid CPOC data — hide the series and its toggle
            sCpoc.applyOptions({ visible: false });
            var cpocCb = document.getElementById("cbCPOC");
            if (cpocCb) cpocCb.closest("label").style.display = "none";
        }

        if (markerList.length > 0) LC.createSeriesMarkers(cs, markerList);

        // --- Series p2 ---
        var sPZ = pc2.addSeries(LC.LineSeries, { color: "#90caf9", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
        sPZ.setData(pZ);
        var sRZ = pc2.addSeries(LC.LineSeries, { color: "#f48fb1", lineStyle: 2, lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
        sRZ.setData(rZ);
        var sZero2 = pc2.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
        sZero2.setData(pZ.map(d => ({ time: d.time, value: 0 })));

        // --- Series p3 ---
        var sCRaw = pc3.addSeries(LC.LineSeries, { color: "#b39ddb", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
        sCRaw.setData(cRaw);
        var sCSmooth = pc3.addSeries(LC.LineSeries, { color: "#ce93d8", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
        sCSmooth.setData(cSmooth);

        var leg1Config = [
            { api: cs, label: "Price", col: "price", color: "#e6edf3" },
            { api: sCwvap, label: "CWVAP", col: "cwvap", color: "#00bfa5" },
            { api: sCpoc, label: "CPOC", col: "cpoc", color: "#ffab40", dashed: true }
        ];
        var leg2Config = [
            { api: sPZ, label: "Price Z", col: "price_slope_z", color: "#90caf9" },
            { api: sRZ, label: "RDV Z", col: "rdv_slope_z", color: "#f48fb1", dashed: true }
        ];
        var leg3Config = [
            { api: sCRaw, label: "Coh Raw", col: "coherence_raw", color: "#b39ddb", dashed: true },
            { api: sCSmooth, label: "Coh", col: "coherence", color: "#ce93d8" }
        ];

        function updateLegend(containerId, config, param, targetIdx) {
            var container = document.getElementById(containerId);
            if (!container) return;
            var html = "";
            config.forEach(function (s) {
                if (!s.api.options().visible) return;
                var val = null;
                if (param && param.seriesData && param.seriesData.has(s.api)) {
                    val = param.seriesData.get(s.api);
                } else if (ledger[targetIdx]) {
                    var row = ledger[targetIdx];
                    if (s.col === "price") val = { close: row.close };
                    else if (row[s.col] != null) val = { value: row[s.col] };
                }
                var price = val ? (val.value !== undefined ? val.value : val.close) : null;
                var display = (price != null) ? price.toFixed(price > 10 ? 2 : 4) : "\u2014";
                var dotStyle = s.dashed
                    ? `background: repeating-linear-gradient(90deg, ${s.color}, ${s.color} 2px, transparent 2px, transparent 4px)`
                    : `background:${s.color}`;
                html += `<div class="legend-item"><span class="legend-dot" style="${dotStyle}"></span><span>${s.label}: ${display}</span></div>`;
            });
            container.innerHTML = html;
        }

        function refreshUI(param) {
            var targetTime = param ? param.time : null;
            var targetIdx = targetTime ? timeToIndex[targetTime] : ledger.length - 1;

            updateLegend("leg1", leg1Config, param, targetIdx);
            updateLegend("leg2", leg2Config, param, targetIdx);
            updateLegend("leg3", leg3Config, param, targetIdx);

            if (ledger[targetIdx]) buildSidebarAnnotations(ledger[targetIdx]);
        }

        window.refreshUI = refreshUI;

        var toggleMap = { cbCWVAP: sCwvap, cbCPOC: sCpoc, cbVol: sVol };
        Object.keys(toggleMap).forEach(id => {
            var cb = document.getElementById(id);
            if (cb) {
                toggleMap[id].applyOptions({ visible: cb.checked });
                cb.addEventListener("change", function () {
                    toggleMap[id].applyOptions({ visible: this.checked });
                    refreshUI();
                });
            }
        });

        var charts = [pc, pc2, pc3];

        function syncCrosshair(chart, series, param) {
            if (param.point === undefined || !param.time || param.point.x < 0 || param.point.y < 0) {
                chart.clearCrosshairPosition();
            } else {
                var data = param.seriesData && param.seriesData.get(series);
                var price = data ? (data.value !== undefined ? data.value : data.close) : null;
                if (price !== null) {
                    chart.setCrosshairPosition(price, param.time, series);
                } else {
                    chart.setCrosshairPosition(0, param.time, series);
                }
            }
        }

        charts.forEach(c1 => {
            c1.subscribeCrosshairMove(param => {
                if (param.time === undefined || param.point === undefined || param.point.x < 0 || param.point.y < 0) {
                    charts.forEach(c2 => { if (c1 !== c2) c2.clearCrosshairPosition(); });
                    refreshUI();
                } else {
                    charts.forEach(c2 => {
                        if (c1 !== c2) {
                            var s2 = c2 === pc ? cs : (c2 === pc2 ? sPZ : sCRaw);
                            syncCrosshair(c2, s2, param);
                        }
                    });
                    refreshUI(param);
                }

                if (c1 === pc && param.time) {
                    const marker = markerList.find(m => m.time === param.time);
                    if (marker && marker.stateText) {
                        tooltipEl.style.display = "block";
                        tooltipEl.innerHTML = `<strong>${marker.stateText}</strong>`;
                        tooltipEl.style.left = (param.point.x + 20) + "px";
                        tooltipEl.style.top = (param.point.y + 60) + "px";
                        return;
                    }
                }
                tooltipEl.style.display = "none";
            });

            c1.timeScale().subscribeVisibleLogicalRangeChange(range => {
                if (range !== null) {
                    charts.forEach(c2 => {
                        if (c1 !== c2) c2.timeScale().setVisibleLogicalRange(range);
                    });
                }
            });
        });

        function reflowCharts() {
            var mainContainer = document.getElementById("chart-container");
            if (mainContainer && mainContainer.clientWidth > 0 && mainContainer.clientHeight > 0) {
                pc.resize(getW("p1"), getH("p1"));
                pc2.resize(getW("p2"), getH("p2"));
                pc3.resize(getW("p3"), getH("p3"));
            }
        }

        if (ohlc.length > 0) {
            var viewBars = Math.min(130, ohlc.length);
            pc.timeScale().setVisibleRange({ from: ohlc[ohlc.length - viewBars].time, to: ohlc[ohlc.length - 1].time });
        }

        const ro = new ResizeObserver(() => requestAnimationFrame(reflowCharts));
        ro.observe(container);

        return [pc];
    }

    function buildSidebarAnnotations(l) {
        if (!l) return;
        function fmt(v, d) { return (v != null && typeof v === "number" && !isNaN(v)) ? v.toFixed(d || 2) : (v || "\u2014"); }
        var intState = l.integrated_state || "No Signal";
        var convScore = l.conviction_score != null ? " (" + fmt(l.conviction_score, 1) + ")" : "";
        var stateColor = intState === "Demand" ? "#00e676" : (intState === "Supply" ? "#ef5350" : "#8b949e");

        document.getElementById("state-table").innerHTML =
            "<tr><td colspan='2' style='text-align:center; padding: 10px; background: rgba(0,0,0,0.2);'><strong style='color:" + stateColor + "'>" + intState + convScore + "</strong></td></tr>" +
            "<tr><td>Date</td><td class='val'>" + (l.date ? l.date.split("T")[0] : "\u2014") + "</td></tr>" +
            "<tr><td>CWC</td><td class='val'>" + fmt(l.cwc, 4) + "</td></tr>" +
            "<tr><td>RDV</td><td class='val'>" + fmt(l.rdv, 4) + "</td></tr>" +
            "<tr><td>RDV Consistency</td><td class='val'>" + fmt(l.rdv_consistency, 0) + "</td></tr>" +
            "<tr><td>CWVAP Dist</td><td class='val'>" + fmt(l.cwvap_dist, 4) + "</td></tr>" +
            "<tr><td>Delivery %</td><td class='val'>" + fmt(l.delivery_pct, 2) + "</td></tr>" +
            "<tr><td>Coherence</td><td class='val'>" + fmt(l.coherence, 3) + "</td></tr>";
    }

    // --- Search & Watchlist Logic ---
    var allStocks = [];
    function fetchAllStocks() {
        fetch("/de/api/analysis/stocks").then(res => res.json()).then(data => { allStocks = data; }).catch(err => console.error("Error fetching stocks:", err));
    }
    var searchInput = document.getElementById("symbol-input"), searchResults = document.getElementById("search-results");
    searchInput.addEventListener("input", function () {
        var val = this.value.toUpperCase();
        if (!val) { searchResults.style.display = "none"; return; }
        var matches = allStocks.filter(s => s.symbol.includes(val)).slice(0, 10);
        if (matches.length === 0) { searchResults.style.display = "none"; return; }
        searchResults.innerHTML = matches.map(m => `<div class="search-item" data-sym="${m.symbol}"><span class="sym">${m.symbol}</span></div>`).join("");
        searchResults.style.display = "block";
    });
    searchResults.addEventListener("click", function (e) {
        var item = e.target.closest(".search-item");
        if (item) { searchResults.style.display = "none"; searchInput.value = ""; loadSymbol(item.dataset.sym); }
    });
    document.addEventListener("click", function (e) { if (!searchInput.contains(e.target)) searchResults.style.display = "none"; });

    function formatDate(isoStr) {
        if (!isoStr) return "";
        var d = new Date(isoStr);
        var day = d.getDate().toString().padStart(2, '0');
        var month = (d.getMonth() + 1).toString().padStart(2, '0');
        var year = d.getFullYear();
        return day + "-" + month + "-" + year;
    }

    var WatchlistManager = {
        currentWlId: null, currentItems: [], activeSortMode: "date-desc",
        init: function () { this.fetchLists(); this.bindEvents(); },
        bindEvents: function () {
            var self = this;
            document.getElementById("wl-select").addEventListener("change", function () {
                self.currentWlId = this.value;
                var addBtn = document.getElementById("wl-add-active");
                if (addBtn) addBtn.disabled = !self.currentWlId;
                self.fetchItems();
            });
            document.getElementById("wl-add").onclick = () => { var name = prompt("Enter Watchlist Name:"); if (name) this.api("/de/api/watchlists", "POST", { name }).then(() => this.fetchLists()).catch(err => alert("Error: " + err.message)); };
            document.getElementById("wl-rename").onclick = () => { if (!this.currentWlId) return; var name = prompt("Enter New Name:"); if (name) this.api("/de/api/watchlists/" + this.currentWlId, "PATCH", { name }).then(() => this.fetchLists()).catch(err => alert("Error: " + err.message)); };
            document.getElementById("wl-delete").onclick = () => { if (!this.currentWlId || !confirm("Delete this watchlist?")) return; this.api("/de/api/watchlists/" + this.currentWlId, "DELETE").then(() => { this.currentWlId = null; this.fetchLists(); }).catch(err => alert("Error deleting watchlist: " + err.message)); };
            document.getElementById("wl-import").onclick = () => { if (!this.currentWlId) return alert("Select a watchlist first"); fetch("/de/api/watchlists/supported-indices").then(r => r.json()).then(indices => { var idx = prompt("Enter Index Name:\n" + indices.join(", ")); if (idx && indices.includes(idx)) { this.api("/de/api/watchlists/import-index", "POST", { watchlist_id: parseInt(this.currentWlId), index_name: idx }).then(res => { alert("Imported " + res.imported + " symbols"); this.fetchItems(); }).catch(err => alert("Error importing: " + err.message)); } }); };
            document.getElementById("wl-download").onclick = () => { if (!this.currentWlId || this.currentItems.length === 0) return alert("Nothing to download"); var sel = document.getElementById("wl-select"), wlName = sel.options[sel.selectedIndex].text; var content = wlName + "\n" + this.currentItems.map(i => i.symbol).join("\n"), blob = new Blob([content], { type: "text/plain" }), url = URL.createObjectURL(blob), a = document.createElement("a"); a.href = url; a.download = wlName.replace(/\s+/g, "_") + ".txt"; a.click(); };

            // Handle Sort dropdown modal
            var sortTrigger = document.getElementById("wl-sort-menu-trigger");
            var sortDropdown = document.getElementById("wl-sort-dropdown");
            if (sortTrigger && sortDropdown) {
                sortTrigger.onclick = function (e) {
                    e.stopPropagation();
                    sortDropdown.style.display = sortDropdown.style.display === "block" ? "none" : "block";
                };
            }
            document.addEventListener("click", (e) => {
                if (sortDropdown && sortDropdown.style.display === "block") {
                    if (!sortTrigger.contains(e.target) && !sortDropdown.contains(e.target)) {
                        sortDropdown.style.display = "none";
                    }
                }
                if (importDropdown && importDropdown.style.display === "block") {
                    if (!importTrigger.contains(e.target) && !importDropdown.contains(e.target)) {
                        importDropdown.style.display = "none";
                    }
                }
            });

            // Handle Import dropdown modal
            var importTrigger = document.getElementById("wl-import");
            var importDropdown = document.getElementById("wl-import-dropdown");
            if (importTrigger && importDropdown) {
                importTrigger.onclick = function (e) {
                    e.stopPropagation();
                    if (importDropdown.style.display === "block") {
                        importDropdown.style.display = "none";
                    } else {
                        self.renderImportList();
                        importDropdown.style.display = "block";
                        if (sortDropdown) sortDropdown.style.display = "none";
                    }
                };
            }

            // Rewrite sorting binding over dynamic dropdown buttons
            var sortBtns = sortDropdown ? sortDropdown.querySelectorAll(".sort-btn") : document.querySelectorAll(".sort-btn");
            sortBtns.forEach(btn => {
                btn.addEventListener("click", function () {
                    sortBtns.forEach(b => b.classList.remove("active"));
                    this.classList.add("active");
                    self.activeSortMode = this.dataset.sort;
                    self.renderItems();
                    if (sortDropdown) sortDropdown.style.display = "none";
                });
            });

            // Add Current active stock symbol action over API
            var wlAddActive = document.getElementById("wl-add-active");
            if (wlAddActive) {
                wlAddActive.addEventListener("click", () => {
                    if (!self.currentWlId) return alert("Select a watchlist first");
                    self.api(`/de/api/watchlists/${self.currentWlId}/items`, "POST", { symbol: symbol }).then(res => {
                        if (res.status === "already_exists") alert(symbol + " is already in the watchlist.");
                        else self.fetchItems();
                    });
                });
            }

            document.getElementById("wl-items").onclick = (e) => { var item = e.target.closest(".wl-item"); if (!item) return; var sym = item.dataset.sym; if (e.target.classList.contains("remove-btn")) { this.api(`/de/api/watchlists/${this.currentWlId}/items/${sym}`, "DELETE").then(() => this.fetchItems()); } else { loadSymbol(sym); } };
        },
        api: function (url, method, body) {
            return fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : null })
                .then(async r => {
                    const data = await r.json();
                    if (!r.ok) throw new Error(data.detail || "Request failed");
                    return data;
                });
        },
        fetchLists: function () {
            fetch("/de/api/watchlists").then(r => r.json()).then(lists => {
                this.allWatchlists = lists;
                var sel = document.getElementById("wl-select"), current = this.currentWlId;
                sel.innerHTML = '<option value="">Select Watchlist</option>' + lists.map(l => `<option value="${l.id}" ${l.id == current ? 'selected' : ''}>${l.name}</option>`).join("");
                if (current) this.fetchItems();
                else document.getElementById("wl-items").innerHTML = "";
                var addBtn = document.getElementById("wl-add-active");
                if (addBtn) addBtn.disabled = !current;
                if (document.getElementById("wl-import-dropdown").style.display === "block") this.renderImportList();
            });
        },
        renderImportList: function () {
            var dropdown = document.getElementById("wl-import-dropdown");
            fetch("/de/api/watchlists/supported-indices").then(r => r.json()).then(indices => {
                var watchlistNames = (this.allWatchlists || []).map(l => l.name);
                dropdown.innerHTML = indices.map(idx => {
                    var isImported = watchlistNames.includes(idx);
                    return `
                        <div class="search-item ${isImported ? 'imported' : ''}" style="justify-content: space-between; padding: 6px 12px;">
                            <span style="color: ${isImported ? '#8b949e' : '#58a6ff'}; font-weight: 500;">${idx}</span>
                            ${isImported ?
                            '<span style="color: #3fb950; font-size: 10px;">Imported</span>' :
                            `<button class="mini-btn" onclick="WatchlistManager.handleAutoImport('${idx}')" style="width: 20px; height: 20px; padding: 0; line-height: 18px;">+</button>`
                        }
                        </div>
                    `;
                }).join("");
            });
        },
        handleAutoImport: function (indexName) {
            this.api("/de/api/watchlists", "POST", { name: indexName }).then(res => {
                var wlId = res.id;
                this.api("/de/api/watchlists/import-index", "POST", { watchlist_id: wlId, index_name: indexName }).then(importRes => {
                    this.currentWlId = wlId;
                    this.fetchLists();
                    document.getElementById("wl-import-dropdown").style.display = "none";
                });
            });
        },
        fetchItems: function () { if (!this.currentWlId) return; fetch(`/de/api/watchlists/${this.currentWlId}/items`).then(r => r.json()).then(items => { this.currentItems = items; this.renderItems(); }); },
        renderItems: function () {
            var sortMode = this.activeSortMode, sorted = [...this.currentItems];
            if (sortMode === "name-asc") sorted.sort((a, b) => a.symbol.localeCompare(b.symbol)); else if (sortMode === "name-desc") sorted.sort((a, b) => b.symbol.localeCompare(a.symbol)); else if (sortMode === "date-asc") sorted.sort((a, b) => new Date(a.added_at) - new Date(b.added_at)); else if (sortMode === "date-desc") sorted.sort((a, b) => new Date(b.added_at) - new Date(a.added_at));
            document.getElementById("wl-items").innerHTML = sorted.map(i => `<div class="wl-item ${i.symbol === symbol ? 'active' : ''}" data-sym="${i.symbol}"><div class="wl-item-info"><span class="sym">${i.symbol}</span><span class="date">${formatDate(i.added_at)}</span></div><span class="remove-btn">×</span></div>`).join("");
            this.updateActiveState();
        },
        updateActiveState: function () { document.querySelectorAll(".wl-item").forEach(item => { if (item.dataset.sym === symbol) item.classList.add("active"); else item.classList.remove("active"); }); }
    };
    fetchAllStocks();
    WatchlistManager.init();

    // --- Settings Panel Logic ---
    var THRESHOLD_META = {
        cwc_gate: { label: "CWC Gate (\u2264)", min: 0.3, max: 1.0, step: 0.05, fmt: v => v.toFixed(2) },
        rdv_gate: { label: "RDV Gate (\u2265)", min: 0.2, max: 2.0, step: 0.1, fmt: v => v.toFixed(1) },
        rdv_consistency_gate: { label: "RDV Consistency (\u2265)", min: 0, max: 5, step: 1, fmt: v => v },
        verification_horizon: { label: "Verification Horizon (days)", min: 1, max: 20, step: 1, fmt: v => v },
        verification_atr_mult: { label: "ATR Multiplier", min: 0.5, max: 4.0, step: 0.5, fmt: v => v.toFixed(1) },
    };

    var GROUPS = [
        { title: "Conviction Gates", keys: ["cwc_gate", "rdv_gate", "rdv_consistency_gate"] },
        { title: "Verification", keys: ["verification_horizon", "verification_atr_mult"] },
    ];

    var settingsOverlay = document.getElementById("settings-overlay");
    var settingsBody = document.getElementById("settings-body");
    var settingsDefaults = {};
    var settingsCurrent = {};

    document.getElementById("open-settings").onclick = function () {
        fetch("/de/api/config/state-rules").then(r => r.json()).then(function (data) {
            settingsDefaults = data.defaults || {};
            settingsCurrent = Object.assign({}, data.thresholds || {});
            renderSettings();
            settingsOverlay.classList.remove("hidden");
        });
    };

    function closeSettings() { settingsOverlay.classList.add("hidden"); }
    document.getElementById("settings-close").onclick = closeSettings;
    document.getElementById("settings-cancel").onclick = closeSettings;
    settingsOverlay.addEventListener("click", function (e) { if (e.target === settingsOverlay) closeSettings(); });

    function renderSettings() {
        var html = "";
        GROUPS.forEach(function (grp) {
            html += '<div class="setting-group"><h4>' + grp.title + '</h4>';
            grp.keys.forEach(function (key) {
                var meta = THRESHOLD_META[key];
                if (!meta) return;
                var val = settingsCurrent[key] != null ? settingsCurrent[key] : settingsDefaults[key];
                var defVal = settingsDefaults[key];
                var isModified = val !== defVal;
                html += '<div class="setting-row">'
                    + '<label>' + meta.label + '</label>'
                    + '<input type="range" id="s-' + key + '" min="' + meta.min + '" max="' + meta.max + '" step="' + meta.step + '" value="' + val + '">'
                    + '<span class="val-display' + (isModified ? ' modified' : '') + '" id="sv-' + key + '">' + meta.fmt(val) + '</span>'
                    + '</div>';
            });
            html += '</div>';
        });
        settingsBody.innerHTML = html;

        // Bind live update on sliders
        Object.keys(THRESHOLD_META).forEach(function (key) {
            var slider = document.getElementById("s-" + key);
            if (!slider) return;
            var display = document.getElementById("sv-" + key);
            var meta = THRESHOLD_META[key];
            slider.addEventListener("input", function () {
                var v = parseFloat(this.value);
                settingsCurrent[key] = v;
                display.textContent = meta.fmt(v);
                display.classList.toggle("modified", v !== settingsDefaults[key]);
            });
        });
    }

    document.getElementById("settings-save").onclick = function () {
        fetch("/de/api/config/state-rules", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ thresholds: settingsCurrent })
        }).then(r => r.json()).then(function () {
            closeSettings();
            loadSymbol(symbol);
        });
    };

    document.getElementById("settings-reset").onclick = function () {
        if (!confirm("Reset all thresholds to factory defaults?")) return;
        fetch("/de/api/config/state-rules/reset", { method: "POST" })
            .then(r => r.json())
            .then(function (data) {
                settingsCurrent = Object.assign({}, data.thresholds || settingsDefaults);
                renderSettings();
                closeSettings();
                loadSymbol(symbol);
            });
    };

    // --- Verification Panel ---
    function loadVerification() {
        var panel = document.getElementById("verification-panel");
        if (!panel) return;
        fetch("/de/api/verification/" + symbol)
            .then(r => r.json())
            .then(function (data) {
                var stats = data.stats || {};
                var signals = (data.signals || []).slice(-20).reverse();
                var hitRate = stats.hit_rate_pct != null ? stats.hit_rate_pct : 0;
                var badgeClass = hitRate >= 60 ? "badge-hit" : (hitRate >= 40 ? "badge-pending" : "badge-miss");

                var html = '<div class="verif-stats">'
                    + '<span class="verif-badge ' + badgeClass + '">' + hitRate + '% hit rate</span>'
                    + '<span class="verif-counts">' + stats.hits + 'H / ' + stats.misses + 'M / ' + stats.pending + 'P (' + stats.total + ' total)</span>'
                    + '</div>';

                if (signals.length > 0) {
                    html += '<table class="verif-table"><tr><th>Date</th><th>Signal</th><th>Conv</th><th>Result</th></tr>';
                    signals.forEach(function (s) {
                        var resClass = s.result === "hit" ? "badge-hit" : (s.result === "miss" ? "badge-miss" : "badge-pending");
                        var stateCol = s.state === "Demand" ? "#00e676" : "#ef5350";
                        html += '<tr>'
                            + '<td>' + s.date + '</td>'
                            + '<td style="color:' + stateCol + '">' + s.state + '</td>'
                            + '<td>' + (s.conviction_score || '-') + '</td>'
                            + '<td><span class="verif-badge ' + resClass + '">' + s.result + '</span></td>'
                            + '</tr>';
                    });
                    html += '</table>';
                }
                panel.innerHTML = html;
            })
            .catch(function () {
                panel.innerHTML = '<span class="placeholder">Verification unavailable</span>';
            });
    }

    // Load verification after initial symbol load
    var origLoadSymbol = loadSymbol;
    loadSymbol = function (sym) {
        origLoadSymbol(sym);
        setTimeout(loadVerification, 1500);
    };
    setTimeout(loadVerification, 2000);

    window.WatchlistManager = WatchlistManager;
})();
