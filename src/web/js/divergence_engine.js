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

    var PANEL_DEFINITIONS = {
        "slopes": { label: "Price / RDV Z-Scores" },
        "coherence": { label: "Coherence" },
        "rdv": { label: "RDV" },
        "cwc": { label: "CWC" },
        "rdv_consistency": { label: "RDV Consistency" },
        "atr_20": { label: "ATR (20)" },
        "cwvap_dist": { label: "CWVAP Dist %" },
        "delivery_pct": { label: "Delivery %" },
        "pdd": { label: "PDD (30)" }
    };

    function getActivePanels() {
        try {
            var conf = JSON.parse(localStorage.getItem("de_panel_config"));
            if (Array.isArray(conf) && conf.length > 0) return conf;
        } catch (e) {}
        return ["slopes", "coherence"]; // defaults
    }

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
        var rdvArr = [], cwcArr = [], rdvConsArr = [], atrArr = [], distArr = [], delPctArr = [], pddArr = [];

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

            if (r.price_slope_z != null) pZ.push({ time: t, value: r.price_slope_z }); else pZ.push({ time: t });
            if (r.rdv_slope_z != null) rZ.push({ time: t, value: r.rdv_slope_z }); else rZ.push({ time: t });

            if (r.coherence_raw != null) cRaw.push({ time: t, value: r.coherence_raw }); else cRaw.push({ time: t });
            if (r.coherence != null) cSmooth.push({ time: t, value: r.coherence }); else cSmooth.push({ time: t });

            if (r.rdv != null) rdvArr.push({ time: t, value: r.rdv }); else rdvArr.push({ time: t });
            if (r.cwc != null) cwcArr.push({ time: t, value: r.cwc }); else cwcArr.push({ time: t });
            if (r.rdv_consistency != null) rdvConsArr.push({ time: t, value: r.rdv_consistency }); else rdvConsArr.push({ time: t });
            if (r.atr_20 != null) atrArr.push({ time: t, value: r.atr_20 }); else atrArr.push({ time: t });
            if (r.cwvap_dist != null) distArr.push({ time: t, value: r.cwvap_dist }); else distArr.push({ time: t });
            if (r.delivery_pct != null) delPctArr.push({ time: t, value: r.delivery_pct }); else delPctArr.push({ time: t });
            if (r.pdd_30 != null) pddArr.push({ time: t, value: r.pdd_30 }); else pddArr.push({ time: t });

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
                var sigStr = r.signal_strength != null ? r.signal_strength : "";
                markerList.push({
                    time: t,
                    position: isDemand ? "belowBar" : "aboveBar",
                    color: stateColorMap[stateStr],
                    shape: isDemand ? "arrowUp" : "arrowDown",
                    stateText: stateStr + (sigStr !== "" ? " (" + sigStr + ")" : "")
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
        var existingSubs = container.querySelectorAll(".p-sub");
        existingSubs.forEach(function (node) { node.remove(); });

        function getW(id) { var el = document.getElementById(id); return el ? el.clientWidth : 800; }
        function getH(id) { var el = document.getElementById(id); return el ? el.clientHeight : 300; }

        var activePanels = getActivePanels();
        var pc = LC.createChart(document.getElementById("p1"), mkOpts(getW("p1"), getH("p1"), activePanels.length === 0));
        var charts = [pc];
        var allLegConfigs = [];

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

        var leg1Config = [
            { api: cs, label: "Price", col: "price", color: "#e6edf3" },
            { api: sCwvap, label: "CWVAP", col: "cwvap", color: "#00bfa5" },
            { api: sCpoc, label: "CPOC", col: "cpoc", color: "#ffab40", dashed: true }
        ];
        allLegConfigs.push({ id: "leg1", config: leg1Config });

        activePanels.forEach(function (panelKey, i) {
            var panelId = "pSub" + i;
            var isLast = (i === activePanels.length - 1);
            
            var panelDiv = document.createElement("div");
            panelDiv.id = panelId;
            panelDiv.className = "panel p-sub";
            panelDiv.innerHTML = '<div id="legSub' + i + '" class="legend"></div>';
            container.appendChild(panelDiv);

            var cHeight = getH(panelId) || 120;
            var c = LC.createChart(panelDiv, mkOpts(getW(panelId) || 800, cHeight, isLast));
            charts.push(c);

            var legConfig = [];

            if (panelKey === "slopes") {
                var sPZ = c.addSeries(LC.LineSeries, { color: "#90caf9", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sPZ.setData(pZ);
                var sRZ = c.addSeries(LC.LineSeries, { color: "#f48fb1", lineStyle: 2, lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sRZ.setData(rZ);
                var sZero = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sZero.setData(pZ.map(d => ({ time: d.time, value: 0 })));
                legConfig.push({ api: sPZ, label: "Price Z", col: "price_slope_z", color: "#90caf9" });
                legConfig.push({ api: sRZ, label: "RDV Z", col: "rdv_slope_z", color: "#f48fb1", dashed: true });
            } else if (panelKey === "coherence") {
                var sCRaw = c.addSeries(LC.LineSeries, { color: "#b39ddb", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sCRaw.setData(cRaw);
                var sCSmooth = c.addSeries(LC.LineSeries, { color: "#ce93d8", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sCSmooth.setData(cSmooth);
                legConfig.push({ api: sCRaw, label: "Coh Raw", col: "coherence_raw", color: "#b39ddb", dashed: true });
                legConfig.push({ api: sCSmooth, label: "Coh", col: "coherence", color: "#ce93d8" });
            } else if (panelKey === "rdv") {
               var s1 = c.addSeries(LC.LineSeries, { color: "#81c784", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
               s1.setData(rdvArr);
               var sZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
               sZ.setData(rdvArr.map(d => ({ time: d.time, value: 1.0 })));
               legConfig.push({ api: s1, label: "RDV", col: "rdv", color: "#81c784" });
            } else if (panelKey === "cwc") {
               var s1 = c.addSeries(LC.LineSeries, { color: "#e57373", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
               s1.setData(cwcArr);
               var sZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
               sZ.setData(cwcArr.map(d => ({ time: d.time, value: 1.0 })));
               legConfig.push({ api: s1, label: "CWC", col: "cwc", color: "#e57373" });
            } else if (panelKey === "rdv_consistency") {
               var s1 = c.addSeries(LC.HistogramSeries, { color: "#64b5f6", lastValueVisible: false, priceLineVisible: false });
               s1.setData(rdvConsArr);
               legConfig.push({ api: s1, label: "RDV Consist", col: "rdv_consistency", color: "#64b5f6" });
            } else if (panelKey === "atr_20") {
               var s1 = c.addSeries(LC.LineSeries, { color: "#ba68c8", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
               s1.setData(atrArr);
               legConfig.push({ api: s1, label: "ATR(20)", col: "atr_20", color: "#ba68c8" });
            } else if (panelKey === "cwvap_dist") {
               var sDist = c.addSeries(LC.LineSeries, { color: "#4dd0e1", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
               sDist.setData(distArr);
               var sZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
               sZ.setData(distArr.map(d => ({ time: d.time, value: 0 })));
               legConfig.push({ api: sDist, label: "VWAP Dist", col: "cwvap_dist", color: "#4dd0e1" });
            } else if (panelKey === "delivery_pct") {
               var sDel = c.addSeries(LC.LineSeries, { color: "#ffb74d", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
               sDel.setData(delPctArr);
               legConfig.push({ api: sDel, label: "Del %", col: "delivery_pct", color: "#ffb74d" });
            } else if (panelKey === "pdd") {
               var sPdd = c.addSeries(LC.LineSeries, { color: "#ff7043", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
               sPdd.setData(pddArr);
               var sZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
               sZ.setData(pddArr.map(d => ({ time: d.time, value: 0 })));
               legConfig.push({ api: sPdd, label: "PDD", col: "pdd_30", color: "#ff7043" });
            }

            allLegConfigs.push({ id: "legSub" + i, config: legConfig });
        });

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

            allLegConfigs.forEach(function (lg) {
                updateLegend(lg.id, lg.config, param, targetIdx);
            });

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

        function syncCrosshair(chart, series, param) {
            if (!series) {
                chart.setCrosshairPosition(0, param.time, chart.series ? chart.series[0] : null);
                return;
            }
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
                            var lg = allLegConfigs[charts.indexOf(c2)];
                            var s2 = lg && lg.config.length > 0 ? lg.config[0].api : null;
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
                activePanels.forEach(function(pane, i) {
                    var panelId = "pSub" + i;
                    var c = charts[i + 1];
                    if (c) c.resize(getW(panelId), getH(panelId));
                });
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
        var scoringDir = l.scoring_direction || null;
        var sigStr = l.signal_strength != null ? " (" + fmt(l.signal_strength, 1) + ")" : "";
        // For No Signal, append the scoring direction so user knows which side was evaluated
        if (intState === "No Signal" && scoringDir) {
            sigStr += " " + scoringDir;
        }
        var stateColor = intState === "Demand" ? "#00e676" : (intState === "Supply" ? "#ef5350" : "#8b949e");

        var regime = l.regime || "\u2014";
        var regimeColor = regime === "uptrend" ? "#3fb950" : (regime === "downtrend" ? "#ef5350" : (regime === "transition" ? "#d29922" : "#8b949e"));

        document.getElementById("state-table").innerHTML =
            "<tr><td colspan='2' style='text-align:center; padding: 10px; background: rgba(0,0,0,0.2);'><strong style='color:" + stateColor + "'>" + intState + sigStr + "</strong></td></tr>" +
            "<tr><td>Date</td><td class='val'>" + (l.date ? l.date.split("T")[0] : "\u2014") + "</td></tr>" +
            "<tr><td>Regime</td><td class='val' style='color:" + regimeColor + "'>" + regime + "</td></tr>";

        // Scoring breakdown (replaces gate diagnostics)
        var diagEl = document.getElementById("gate-diagnostics");
        if (!diagEl) return;
        if (!l.scoring_details || !Array.isArray(l.scoring_details) || l.scoring_details.length === 0) {
            diagEl.innerHTML = "";
            return;
        }

        var html = '<div class="scoring-section">';
        var totalWeighted = 0, totalWeight = 0;
        l.scoring_details.forEach(function(f) {
            var pct = Math.round(f.score * 100);
            var barColor = pct >= 70 ? "#3fb950" : (pct >= 40 ? "#d29922" : "#8b949e");
            var label = (f.ui && f.ui.label) ? f.ui.label : f.factor;
            var weightPct = Math.round(f.weight * 100);
            totalWeighted += f.weighted;
            totalWeight += f.weight;
            html += '<div class="scoring-row">'
                + '<span class="scoring-label">' + label + ' <span class="scoring-weight">(' + weightPct + '%)</span></span>'
                + '<div class="scoring-bar-wrap">'
                + '<div class="scoring-bar" style="width:' + pct + '%; background:' + barColor + '"></div>'
                + '</div>'
                + '<span class="scoring-val">' + fmt(f.raw_value, 2) + '</span>'
                + '<span class="scoring-contrib">' + (f.weighted * 100).toFixed(1) + '</span>'
                + '</div>';
        });
        // Total row
        var totalPct = totalWeight > 0 ? Math.round((totalWeighted / totalWeight) * 100) : 0;
        var totalColor = totalPct >= 70 ? "#3fb950" : (totalPct >= 40 ? "#d29922" : "#8b949e");
        html += '<div class="scoring-total">'
            + '<span class="scoring-label">Total</span>'
            + '<div class="scoring-bar-wrap">'
            + '<div class="scoring-bar" style="width:' + totalPct + '%; background:' + totalColor + '"></div>'
            + '</div>'
            + '<span class="scoring-val"></span>'
            + '<span class="scoring-contrib" style="color:' + totalColor + '">' + totalPct + '</span>'
            + '</div>';
        html += '</div>';
        diagEl.innerHTML = html;
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
        currentWlId: null, defaultWlId: null, currentItems: [], activeSortMode: "date-desc",
        init: function () { 
            var defStr = localStorage.getItem("de_default_watchlist");
            if (defStr) {
                try { this.defaultWlId = parseInt(defStr) || null; } catch(e) {}
            }
            this.fetchLists(); 
            this.bindEvents(); 
        },
        bindEvents: function () {
            var self = this;
            document.getElementById("wl-select").addEventListener("change", function () {
                self.currentWlId = this.value;
                self.updateUIState();
                var addBtn = document.getElementById("wl-add-active");
                if (addBtn) addBtn.disabled = !self.currentWlId;
                self.fetchItems();
            });
            document.getElementById("wl-add").onclick = () => { var name = prompt("Enter Watchlist Name:"); if (name) this.api("/de/api/watchlists", "POST", { name }).then(() => this.fetchLists()).catch(err => alert("Error: " + err.message)); };
            document.getElementById("wl-rename").onclick = () => { if (!this.currentWlId) return; var name = prompt("Enter New Name:"); if (name) this.api("/de/api/watchlists/" + this.currentWlId, "PATCH", { name }).then(() => { if (this.currentWlId == this.defaultWlId) localStorage.removeItem("de_default_watchlist"); this.fetchLists(); }).catch(err => alert("Error: " + err.message)); };
            document.getElementById("wl-delete").onclick = () => { if (!this.currentWlId || !confirm("Delete this watchlist?")) return; this.api("/de/api/watchlists/" + this.currentWlId, "DELETE").then(() => { if (this.currentWlId == this.defaultWlId) { this.defaultWlId = null; localStorage.removeItem("de_default_watchlist"); } this.currentWlId = null; this.fetchLists(); }).catch(err => alert("Error deleting watchlist: " + err.message)); };
            
            var btnDefault = document.getElementById("wl-set-default");
            if(btnDefault) {
                btnDefault.onclick = () => {
                   if(!this.currentWlId) return;
                   if(this.currentWlId == this.defaultWlId) {
                       this.defaultWlId = null;
                       localStorage.removeItem("de_default_watchlist");
                   } else {
                       this.defaultWlId = parseInt(this.currentWlId);
                       localStorage.setItem("de_default_watchlist", this.defaultWlId.toString());
                   }
                   this.fetchLists(true);
                };
            }

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
        fetchLists: function (keepCurrentSelection) {
            fetch("/de/api/watchlists").then(r => r.json()).then(lists => {
                this.allWatchlists = lists;
                var sel = document.getElementById("wl-select");
                
                // Determine current selection safely
                var current = null;
                if(keepCurrentSelection && this.currentWlId) {
                    current = this.currentWlId;
                } else {
                    if (this.defaultWlId && lists.some(l => l.id == this.defaultWlId)) {
                        current = this.defaultWlId;
                    } else if (lists.length > 0) {
                        current = lists[0].id;
                        this.defaultWlId = current;
                        localStorage.setItem("de_default_watchlist", current.toString());
                    }
                }
                
                this.currentWlId = current;
                
                sel.innerHTML = '<option value="">Select Watchlist</option>' + lists.map(l => {
                    var isDef = (l.id == this.defaultWlId) ? " ★" : "";
                    return `<option value="${l.id}" ${l.id == current ? 'selected' : ''}>${l.name}${isDef}</option>`;
                }).join("");
                
                this.updateUIState();
                
                if (current) this.fetchItems();
                else document.getElementById("wl-items").innerHTML = "";
                
                var addBtn = document.getElementById("wl-add-active");
                if (addBtn) addBtn.disabled = !current;
                
                if (document.getElementById("wl-import-dropdown").style.display === "block") this.renderImportList();
            });
        },
        updateUIState: function() {
            var btnDefault = document.getElementById("wl-set-default");
            if(btnDefault) {
                if(this.currentWlId && this.currentWlId == this.defaultWlId) {
                    btnDefault.classList.add("active");
                    btnDefault.title = "Current watchlist is default";
                } else {
                    btnDefault.classList.remove("active");
                    btnDefault.title = "Set as Default Watchlist";
                }
            }
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

    // --- Settings Panel Logic (auto-generated from API) ---
    var settingsOverlay = document.getElementById("settings-overlay");
    var settingsThresholds = document.getElementById("settings-thresholds");
    var settingsDefaults = {};
    var settingsCurrent = {};
    var settingsFactors = [];  // factor UI metadata from API
    var currentPanelsConfig = [];

    document.getElementById("open-settings").onclick = function () {
        fetch("/de/api/config/state-rules").then(r => r.json()).then(function (data) {
            settingsDefaults = data.defaults || {};
            settingsCurrent = Object.assign({}, data.current || {});
            settingsFactors = data.factors || [];
            renderSettings();
            renderPanelsSettings();
            settingsOverlay.classList.remove("hidden");
        });
    };

    document.querySelectorAll(".settings-tab").forEach(function(btn) {
        btn.addEventListener("click", function() {
            document.querySelectorAll(".settings-tab").forEach(b => b.classList.remove("active"));
            this.classList.add("active");
            document.querySelectorAll(".settings-body").forEach(b => b.classList.add("hidden"));
            document.getElementById(this.dataset.target).classList.remove("hidden");
        });
    });

    function closeSettings() { settingsOverlay.classList.add("hidden"); }
    document.getElementById("settings-close").onclick = closeSettings;
    document.getElementById("settings-cancel").onclick = closeSettings;
    settingsOverlay.addEventListener("click", function (e) { if (e.target === settingsOverlay) closeSettings(); });

    function renderSettings() {
        var html = "";

        // --- Global settings ---
        html += '<div class="setting-group"><h4>Signal Settings</h4>';
        var mss = settingsCurrent.min_signal_strength != null ? settingsCurrent.min_signal_strength : 40;
        var mssDefault = settingsDefaults.min_signal_strength != null ? settingsDefaults.min_signal_strength : 40;
        var mssModified = mss !== mssDefault;
        html += '<div class="setting-row">'
            + '<label>Min Signal Strength</label>'
            + '<input type="range" id="s-min_signal_strength" min="10" max="80" step="5" value="' + mss + '">'
            + '<span class="val-display' + (mssModified ? ' modified' : '') + '" id="sv-min_signal_strength">' + mss + '</span>'
            + '</div>';

        html += '</div>';

        // --- Factor weights (auto-generated from API) ---
        ["Demand", "Supply"].forEach(function(dir) {
            var dirLower = dir.toLowerCase();
            html += '<div class="setting-group"><h4>' + dir + ' Weights</h4>';
            settingsFactors.forEach(function(f) {
                var key = "weight_" + f.factor + "_" + dirLower;
                var val = settingsCurrent[key] != null ? settingsCurrent[key] : (settingsDefaults[key] || 0);
                var defVal = settingsDefaults[key] || 0;
                var isModified = val !== defVal;
                html += '<div class="setting-row">'
                    + '<label title="' + (f.description || '') + '">' + f.label + '</label>'
                    + '<input type="range" id="s-' + key + '" min="0" max="0.5" step="0.05" value="' + val + '">'
                    + '<span class="val-display' + (isModified ? ' modified' : '') + '" id="sv-' + key + '">' + val.toFixed(2) + '</span>'
                    + '</div>';
            });
            html += '</div>';
        });

        settingsThresholds.innerHTML = html;

        // Bind live update on all sliders
        var allKeys = ["min_signal_strength"];
        settingsFactors.forEach(function(f) {
            allKeys.push("weight_" + f.factor + "_demand");
            allKeys.push("weight_" + f.factor + "_supply");
        });
        allKeys.forEach(function(key) {
            var slider = document.getElementById("s-" + key);
            if (!slider) return;
            var display = document.getElementById("sv-" + key);
            slider.addEventListener("input", function () {
                var v = parseFloat(this.value);
                settingsCurrent[key] = v;
                var isInt = (key === "min_signal_strength");
                display.textContent = isInt ? v : v.toFixed(2);
                display.classList.toggle("modified", v !== (settingsDefaults[key] || 0));
            });
        });
    }

    function renderPanelsSettings() {
        currentPanelsConfig = getActivePanels();
        updatePanelsUI();
    }

    function updatePanelsUI() {
        var listContainer = document.getElementById("active-panels-list");
        if (!listContainer) return;
        var html = "";
        currentPanelsConfig.forEach(function (key, i) {
            var label = PANEL_DEFINITIONS[key] ? PANEL_DEFINITIONS[key].label : key;
            html += '<div class="panel-setting-item" data-key="' + key + '">'
                 + '<div class="panel-setting-controls">'
                 + '<button class="mini-btn move-up" data-idx="' + i + '">▲</button>'
                 + '<button class="mini-btn move-down" data-idx="' + i + '">▼</button>'
                 + '</div>'
                 + '<span class="panel-setting-label">' + label + '</span>'
                 + '<button class="mini-button delete-panel" data-idx="' + i + '">×</button>'
                 + '</div>';
        });
        listContainer.innerHTML = html;

        var select = document.getElementById("add-panel-select");
        var selHtml = '<option value="">-- Select Panel --</option>';
        Object.keys(PANEL_DEFINITIONS).forEach(function(key) {
            if (currentPanelsConfig.indexOf(key) === -1) {
                selHtml += '<option value="' + key + '">' + PANEL_DEFINITIONS[key].label + '</option>';
            }
        });
        select.innerHTML = selHtml;
        document.getElementById("add-panel-btn").disabled = currentPanelsConfig.length >= Object.keys(PANEL_DEFINITIONS).length;

        listContainer.querySelectorAll(".move-up").forEach(function(btn) {
            btn.onclick = function() {
                var idx = parseInt(this.dataset.idx);
                if (idx > 0) {
                    var tmp = currentPanelsConfig[idx];
                    currentPanelsConfig[idx] = currentPanelsConfig[idx-1];
                    currentPanelsConfig[idx-1] = tmp;
                    updatePanelsUI();
                }
            };
        });
        listContainer.querySelectorAll(".move-down").forEach(function(btn) {
            btn.onclick = function() {
                var idx = parseInt(this.dataset.idx);
                if (idx < currentPanelsConfig.length - 1) {
                    var tmp = currentPanelsConfig[idx];
                    currentPanelsConfig[idx] = currentPanelsConfig[idx+1];
                    currentPanelsConfig[idx+1] = tmp;
                    updatePanelsUI();
                }
            };
        });
        listContainer.querySelectorAll(".delete-panel").forEach(function(btn) {
            btn.onclick = function() {
                var idx = parseInt(this.dataset.idx);
                currentPanelsConfig.splice(idx, 1);
                updatePanelsUI();
            };
        });
    }

    document.getElementById("add-panel-btn").onclick = function() {
        var val = document.getElementById("add-panel-select").value;
        if (val) {
            currentPanelsConfig.push(val);
            updatePanelsUI();
        }
    };

    document.getElementById("settings-save").onclick = function () {
        localStorage.setItem("de_panel_config", JSON.stringify(currentPanelsConfig));
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
        if (!confirm("Reset all weights and panels to factory defaults?")) return;
        localStorage.removeItem("de_panel_config");
        fetch("/de/api/config/state-rules/reset", { method: "POST" })
            .then(r => r.json())
            .then(function (data) {
                settingsCurrent = Object.assign({}, data.current || settingsDefaults);
                renderSettings();
                closeSettings();
                loadSymbol(symbol);
            });
    };

    window.WatchlistManager = WatchlistManager;
})();
