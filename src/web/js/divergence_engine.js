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

    var params = new URLSearchParams(window.location.search);
    var apiUrl = "/lfm/api/divergence-engine/" + symbol;
    if (params.get("start_date")) apiUrl += "?start_date=" + params.get("start_date");
    if (params.get("end_date")) {
        apiUrl += (apiUrl.includes("?") ? "&" : "?") + "end_date=" + params.get("end_date");
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

    function loadSymbol(targetSymbol) {
        if (!targetSymbol) return;
        symbol = targetSymbol.toUpperCase();
        var apiUrl = "/lfm/api/divergence-engine/" + symbol;
        if (params.get("start_date")) apiUrl += "?start_date=" + params.get("start_date");
        if (params.get("end_date")) {
            apiUrl += (apiUrl.includes("?") ? "&" : "?") + "end_date=" + params.get("end_date");
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
                // Clear existing charts
                ["p1", "p2", "p3"].forEach(id => {
                    var el = document.getElementById(id);
                    if (el) el.innerHTML = '<div id="leg' + id.slice(1) + '" class="legend"></div>';
                });

                chartInstances = buildCharts(data);
                refreshUI(); // Ensure legends and sidebar are populated immediately
                buildSidebarAnnotations(data.latest);
                WatchlistManager.updateActiveState();
                window.history.pushState({}, "", "/lfm/divergence-engine/" + symbol);
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
            if (r.cwvap != null) cwvap.push({ time: t, value: r.cwvap });
            if (r.cpoc != null) cpoc.push({ time: t, value: r.cpoc });

            if (r.delivery_qty != null) {
                var mfm = r.mfm != null ? r.mfm : 0;
                deliveryVol.push({
                    time: t, value: r.delivery_qty,
                    color: mfm >= 0 ? "rgba(38,166,154,0.35)" : "rgba(239,83,80,0.35)"
                });
            }
        }

        // --- Marker Logic (v4.0 Regime & Divergence) ---
        var regimeCfg = {
            "ACCUMULATION": { color: "#42a5f5", shape: "circle", pos: "belowBar" },
            "DISTRIBUTION": { color: "#ffb300", shape: "circle", pos: "aboveBar" }
        };
        var divCfg = {
            "PRICE↑/RDV↓": { color: "#ef5350", shape: "arrowDown", pos: "aboveBar" },
            "RDV↑/PRICE↓": { color: "#26a69a", shape: "arrowUp", pos: "belowBar" }
        };

        var markersByTime = {};
        for (var i = 0; i < ledger.length; i++) {
            var r = ledger[i];
            var t = r.date ? (r.date.includes(" ") ? r.date.split(" ")[0] : r.date) : null;
            if (!t) continue;

            var marker = null;
            var divConf = (r.div_flag && divCfg[r.div_flag]) ? (r.div_conf || 0) : 0;
            var regConf = (regimeCfg[r.regime]) ? (r.regime_conf || 0) : 0;

            // 1. Divergence candidate (threshold >= 0.5)
            var divCandidate = null;
            if (divConf >= 0.5) {
                var cfg = divCfg[r.div_flag];
                divCandidate = {
                    conf: divConf,
                    data: {
                        time: t, position: cfg.pos, color: cfg.color, shape: cfg.shape,
                        text: (divConf * 100).toFixed(0)
                    }
                };
            }

            // 2. Regime candidate (ACC/DIST)
            var regCandidate = null;
            if (regimeCfg[r.regime]) {
                var cfg = regimeCfg[r.regime];
                regCandidate = {
                    conf: regConf,
                    data: {
                        time: t, position: cfg.pos, color: cfg.color, shape: cfg.shape,
                        text: (regConf >= 0.5) ? (regConf * 100).toFixed(0) : ""
                    }
                };
            }

            // 3. Select winner based on confidence
            if (divCandidate && regCandidate) {
                marker = (regCandidate.conf >= divCandidate.conf) ? regCandidate.data : divCandidate.data;
            } else {
                marker = divCandidate ? divCandidate.data : (regCandidate ? regCandidate.data : null);
            }

            if (marker) {
                markersByTime[t] = marker;
            }
        }
        markerList = Object.values(markersByTime).sort((a, b) => a.time - b.time);

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
        var containerWidth = container.clientWidth || 800;
        var containerHeight = container.clientHeight || (window.innerHeight - 50);

        var pc = LC.createChart(document.getElementById("p1"), mkOpts(containerWidth, containerHeight, true));

        // --- Series ---
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

        var sCpoc = pc.addSeries(LC.LineSeries, { color: "#ffab40", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
        var lastCpocVal = (cpoc.length > 0) ? cpoc[cpoc.length - 1].value : null;
        sCpoc.setData(cpoc.map(d => ({ time: d.time, value: lastCpocVal })));

        if (markerList.length > 0) LC.createSeriesMarkers(cs, markerList);

        var allSeriesConfig = [
            { api: cs, label: "Price", col: "price", color: "#e6edf3" },
            { api: sCwvap, label: "CWVAP", col: "cwvap", color: "#00bfa5" },
            { api: sCpoc, label: "CPOC", col: "cpoc", color: "#ffab40" }
        ];

        function refreshUI(param) {
            var targetTime = param ? param.time : null;
            var targetIdx = targetTime ? timeToIndex[targetTime] : ledger.length - 1;

            var container = document.getElementById("leg1");
            if (container) {
                var html = "";
                allSeriesConfig.forEach(function (s) {
                    if (!s.api.options().visible) return;
                    var val = null;
                    if (param && param.seriesData.has(s.api)) {
                        val = param.seriesData.get(s.api);
                    } else if (ledger[targetIdx]) {
                        var row = ledger[targetIdx];
                        if (s.col === "price") val = { close: row.close };
                        else if (row[s.col] != null) val = { value: row[s.col] };
                    }
                    var price = val ? (val.value !== undefined ? val.value : val.close) : null;
                    var display = (price != null) ? price.toFixed(price > 10 ? 2 : 4) : "\u2014";
                    html += `<div class="legend-item"><span class="legend-dot" style="background:${s.color}"></span><span>${s.label}: ${display}</span></div>`;
                });
                container.innerHTML = html;
            }
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

        pc.subscribeCrosshairMove(p => {
            refreshUI(p);
            if (p.time) {
                const marker = markerList.find(m => m.time === p.time);
                if (marker && marker.customData) {
                    tooltipEl.style.display = "block";
                    tooltipEl.innerHTML = `<strong>${marker.customData.label}</strong><br>Conf: ${marker.customData.conf}`;
                    tooltipEl.style.left = (p.point.x + 20) + "px";
                    tooltipEl.style.top = (p.point.y + 60) + "px";
                    return;
                }
            }
            tooltipEl.style.display = "none";
        });

        function reflowCharts() {
            var cw = container.clientWidth;
            var ch = container.clientHeight;
            if (cw > 0 && ch > 0) pc.resize(cw, ch);
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
        var reg = l.regime || "NEUTRAL";
        var rConf = (l.regime_conf != null) ? (l.regime_conf * 100).toFixed(1) + "%" : "\u2014";
        var div = l.div_flag || "\u2014";
        var dConf = (l.div_conf != null && l.div_flag) ? (l.div_conf * 100).toFixed(1) + "%" : "\u2014";
        var coher = fmt(l.coherence, 3) + " (" + (l.score_slope || "FLAT") + ")";

        document.getElementById("state-table").innerHTML =
            "<tr><td>Date</td><td class='val'>" + (l.date ? l.date.split("T")[0] : "\u2014") + "</td></tr>" +
            "<tr><td>Regime</td><td class='val'><strong>" + reg + "</strong> [" + rConf + "]</td></tr>" +
            "<tr><td>Coherence</td><td class='val'>" + coher + "</td></tr>" +
            "<tr><td>Divergence</td><td class='val'>" + div + " [" + dConf + "]</td></tr>" +
            "<tr><td>Price Slope Z</td><td class='val'>" + fmt(l.price_slope_z, 4) + "</td></tr>" +
            "<tr><td>RDV Slope Z</td><td class='val'>" + fmt(l.rdv_slope_z, 4) + "</td></tr>" +
            "<tr><td>Money Flow</td><td class='val'>" + fmt(l.mcs_composite, 4) + "</td></tr>";
    }

    // --- Search & Watchlist Logic ---
    var allStocks = [];
    function fetchAllStocks() {
        fetch("/lfm/api/analysis/stocks").then(res => res.json()).then(data => { allStocks = data; }).catch(err => console.error("Error fetching stocks:", err));
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
        var d = new Date(isoStr), months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
        return d.getDate().toString().padStart(2, '0') + "-" + months[d.getMonth()] + "-" + d.getFullYear();
    }

    var WatchlistManager = {
        currentWlId: null, currentItems: [],
        init: function () { this.fetchLists(); this.bindEvents(); },
        bindEvents: function () {
            var self = this;
            document.getElementById("wl-select").addEventListener("change", function () { self.currentWlId = this.value; self.fetchItems(); });
            document.getElementById("wl-add").onclick = () => { var name = prompt("Enter Watchlist Name:"); if (name) this.api("/lfm/api/watchlists", "POST", { name }).then(() => this.fetchLists()); };
            document.getElementById("wl-rename").onclick = () => { if (!this.currentWlId) return; var name = prompt("Enter New Name:"); if (name) this.api("/lfm/api/watchlists/" + this.currentWlId, "PATCH", { name }).then(() => this.fetchLists()); };
            document.getElementById("wl-delete").onclick = () => { if (!this.currentWlId || !confirm("Delete this watchlist?")) return; this.api("/lfm/api/watchlists/" + this.currentWlId, "DELETE").then(() => { this.currentWlId = null; this.fetchLists(); }); };
            document.getElementById("wl-import").onclick = () => { if (!this.currentWlId) return alert("Select a watchlist first"); fetch("/lfm/api/watchlists/supported-indices").then(r => r.json()).then(indices => { var idx = prompt("Enter Index Name:\n" + indices.join(", ")); if (idx && indices.includes(idx)) { this.api("/lfm/api/watchlists/import-index", "POST", { watchlist_id: parseInt(this.currentWlId), index_name: idx }).then(res => { alert("Imported " + res.imported + " symbols"); this.fetchItems(); }); } }); };
            document.getElementById("wl-download").onclick = () => { if (!this.currentWlId || this.currentItems.length === 0) return alert("Nothing to download"); var sel = document.getElementById("wl-select"), wlName = sel.options[sel.selectedIndex].text; var content = wlName + "\n" + this.currentItems.map(i => i.symbol).join("\n"), blob = new Blob([content], { type: "text/plain" }), url = URL.createObjectURL(blob), a = document.createElement("a"); a.href = url; a.download = wlName.replace(/\s+/g, "_") + ".txt"; a.click(); };
            document.getElementById("wl-sort").addEventListener("change", function () { self.renderItems(); });
            document.getElementById("wl-items").onclick = (e) => { var item = e.target.closest(".wl-item"); if (!item) return; var sym = item.dataset.sym; if (e.target.classList.contains("remove-btn")) { this.api(`/lfm/api/watchlists/${this.currentWlId}/items/${sym}`, "DELETE").then(() => this.fetchItems()); } else { loadSymbol(sym); } };
        },
        api: function (url, method, body) { return fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : null }).then(r => r.json()); },
        fetchLists: function () { fetch("/lfm/api/watchlists").then(r => r.json()).then(lists => { var sel = document.getElementById("wl-select"), current = this.currentWlId; sel.innerHTML = '<option value="">Select Watchlist</option>' + lists.map(l => `<option value="${l.id}" ${l.id == current ? 'selected' : ''}>${l.name}</option>`).join(""); if (current) this.fetchItems(); else document.getElementById("wl-items").innerHTML = ""; }); },
        fetchItems: function () { if (!this.currentWlId) return; fetch(`/lfm/api/watchlists/${this.currentWlId}/items`).then(r => r.json()).then(items => { this.currentItems = items; this.renderItems(); }); },
        renderItems: function () {
            var sortMode = document.getElementById("wl-sort").value, sorted = [...this.currentItems];
            if (sortMode === "name-asc") sorted.sort((a, b) => a.symbol.localeCompare(b.symbol)); else if (sortMode === "name-desc") sorted.sort((a, b) => b.symbol.localeCompare(a.symbol)); else if (sortMode === "date-asc") sorted.sort((a, b) => new Date(a.added_at) - new Date(b.added_at)); else if (sortMode === "date-desc") sorted.sort((a, b) => new Date(b.added_at) - new Date(a.added_at));
            document.getElementById("wl-items").innerHTML = sorted.map(i => `<div class="wl-item ${i.symbol === symbol ? 'active' : ''}" data-sym="${i.symbol}"><div class="wl-item-info"><span class="sym">${i.symbol}</span><span class="date">${formatDate(i.added_at)}</span></div><span class="remove-btn">×</span></div>`).join("");
        },
        updateActiveState: function () { document.querySelectorAll(".wl-item").forEach(item => { if (item.dataset.sym === symbol) item.classList.add("active"); else item.classList.remove("active"); }); }
    };
    fetchAllStocks();
    WatchlistManager.init();
})();
