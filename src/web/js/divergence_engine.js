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

    fetch(apiUrl)
        .then(function (res) {
            if (!res.ok) throw new Error("API error: " + res.status);
            return res.json();
        })
        .then(function (data) {
            loadingEl.style.display = "none";
            chartInstances = buildCharts(data);
            buildSidebarAnnotations(data.latest);
        })
        .catch(function (err) {
            loadingEl.innerHTML = '<div class="err">Error: ' + err.message + "</div>";
        });

    var STATE_MARKERS = {
        UPTREND: { shape: "arrowUp", color: "#26a69a", pos: "aboveBar", label: "\u25b2 Uptrend" },
        DOWNTREND: { shape: "arrowDown", color: "#ef5350", pos: "belowBar", label: "\u25bc Downtrend" },
        ACCUMULATION: { shape: "circle", color: "#42a5f5", pos: "belowBar", label: "Accumulation" },
        DISTRIBUTION: { shape: "circle", color: "#ffa726", pos: "aboveBar", label: "Distribution" },
        RECOVERY: { shape: "circle", color: "#26c6da", pos: "belowBar", label: "Recovery" },
        SIDEWAYS: { shape: "circle", color: "#78909c", pos: "belowBar", label: "Sideways" }
    };

    function buildCharts(data) {
        var ledger = data.ledger;
        var ohlc = [], cwvap = [], cpoc = [];
        var dvwap10 = [], dvwap30 = [], dvwap60 = [], dvwap120 = [];
        var cvah = [], cval = [];
        var deliveryVol = [];
        var cwcData = [], c1030 = [], c3060 = [], c60120 = [];
        var mcsComp = [], mcsRaw = [], mcsMfmRaw = [], mcsDelta = [];
        var vel10 = [], vel30 = [], vel60 = [], vel120 = [];
        var markerList = [];
        var markersByTime = {};
        var timeToIndex = {};

        for (var i = 0; i < ledger.length; i++) {
            var r = ledger[i];
            var t = r.date ? Math.floor(new Date(r.date).getTime() / 1000) : null;
            if (!t) continue;
            timeToIndex[t] = i;

            if (r.open != null && r.high != null && r.low != null && r.close != null) {
                ohlc.push({ time: t, open: r.open, high: r.high, low: r.low, close: r.close });
            }
            if (r.cwvap != null) cwvap.push({ time: t, value: r.cwvap });
            if (r.cpoc != null) cpoc.push({ time: t, value: r.cpoc });
            if (r.cvah != null) cvah.push({ time: t, value: r.cvah });
            if (r.cval != null) cval.push({ time: t, value: r.cval });

            if (r.delivery_qty != null) {
                var mfm = r.mfm != null ? r.mfm : 0;
                deliveryVol.push({
                    time: t, value: r.delivery_qty,
                    color: mfm >= 0 ? "rgba(38,166,154,0.35)" : "rgba(239,83,80,0.35)"
                });
            }

            if (r.dvwap_10 != null) dvwap10.push({ time: t, value: r.dvwap_10 });
            if (r.dvwap_30 != null) dvwap30.push({ time: t, value: r.dvwap_30 });
            if (r.dvwap_60 != null) dvwap60.push({ time: t, value: r.dvwap_60 });
            if (r.dvwap_120 != null) dvwap120.push({ time: t, value: r.dvwap_120 });

            if (r.cwc != null) cwcData.push({ time: t, value: r.cwc });
            if (r.c_10_30 != null) c1030.push({ time: t, value: r.c_10_30 });
            if (r.c_30_60 != null) c3060.push({ time: t, value: r.c_30_60 });
            if (r.c_60_120 != null) c60120.push({ time: t, value: r.c_60_120 });

            if (r.mcs_composite != null) mcsComp.push({ time: t, value: r.mcs_composite });
            if (r.mcs != null) mcsRaw.push({ time: t, value: r.mcs });
            if (r.mcs_mfm != null) mcsMfmRaw.push({ time: t, value: r.mcs_mfm });
            if (r.mcs_delta != null) {
                mcsDelta.push({ time: t, value: r.mcs_delta, color: r.mcs_delta >= 0 ? "rgba(38,166,154,0.7)" : "rgba(239,83,80,0.7)" });
            }

            if (r.velocity_10_norm != null) vel10.push({ time: t, value: r.velocity_10_norm, color: r.velocity_10_norm >= 0 ? "rgba(0,200,100,0.6)" : "rgba(200,50,50,0.6)" });
            if (r.velocity_30_norm != null) vel30.push({ time: t, value: r.velocity_30_norm, color: r.velocity_30_norm >= 0 ? "rgba(0,200,100,0.4)" : "rgba(200,50,50,0.4)" });
            if (r.velocity_60_norm != null) vel60.push({ time: t, value: r.velocity_60_norm, color: r.velocity_60_norm >= 0 ? "rgba(0,200,100,0.25)" : "rgba(200,50,50,0.25)" });
            if (r.velocity_120_norm != null) vel120.push({ time: t, value: r.velocity_120_norm, color: r.velocity_120_norm >= 0 ? "rgba(0,200,100,0.15)" : "rgba(200,50,50,0.15)" });

            var tooltipLines = [];
            var conf = r.state_confidence || 0;
            if (conf > 0.65 && r.state) {
                var mcfg = STATE_MARKERS[r.state];
                if (mcfg) {
                    markerList.push({ time: t, position: mcfg.pos, color: mcfg.color, shape: mcfg.shape, text: "" });
                    tooltipLines.push(mcfg.label + " " + Math.round(conf * 100) + "%");
                }
            }
            var dp = r.divergence_probability || 0;
            if (dp > 0.5 && r.divergence_direction && r.divergence_direction !== "none") {
                if (r.divergence_direction === "bullish") {
                    markerList.push({ time: t, position: "belowBar", color: "rgba(38,166,154," + Math.min(dp, 1).toFixed(2) + ")", shape: "arrowUp", text: "" });
                    tooltipLines.push("DIV\u2191 Bullish " + Math.round(dp * 100) + "%");
                } else if (r.divergence_direction === "bearish") {
                    markerList.push({ time: t, position: "aboveBar", color: "rgba(239,83,80," + Math.min(dp, 1).toFixed(2) + ")", shape: "arrowDown", text: "" });
                    tooltipLines.push("DIV\u2193 Bearish " + Math.round(dp * 100) + "%");
                }
            }
            if (tooltipLines.length > 0) markersByTime[t] = tooltipLines.join(" \u2022 ");
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
                    timeVisible: false,
                    fixLeftEdge: true,
                    fixRightEdge: true
                },
                leftPriceScale: { visible: false },
                rightPriceScale: {
                    visible: true,
                    borderColor: "#21262d",
                    minimumWidth: 100,
                    width: 100
                }
            };
        }

        var container = document.getElementById("chart-container");
        var containerWidth = container.clientWidth || 800;
        var containerHeight = container.clientHeight || (window.innerHeight - 50);

        var pc = LC.createChart(document.getElementById("p1"), mkOpts(containerWidth, Math.round(containerHeight * 0.60), false));
        var sVol = pc.addSeries(LC.HistogramSeries, { priceScaleId: "vol", priceLineVisible: false, lastValueVisible: false });
        sVol.setData(deliveryVol);
        pc.priceScale("vol").applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } });

        var cs = pc.addSeries(LC.CandlestickSeries, { upColor: "#26a69a", downColor: "#ef5350", borderUpColor: "#26a69a", borderDownColor: "#ef5350", wickUpColor: "#26a69a", wickDownColor: "#ef5350", lastValueVisible: false });
        cs.setData(ohlc);

        var sCwvap = pc.addSeries(LC.LineSeries, { color: "#00bfa5", lineWidth: 2, lastValueVisible: false });
        sCwvap.setData(cwvap);
        var sCpoc = pc.addSeries(LC.LineSeries, { color: "#ffab40", lineWidth: 1, lineStyle: 2, lastValueVisible: false });
        sCpoc.setData(cpoc);
        var sVah = pc.addSeries(LC.LineSeries, { color: "rgba(255,255,255,0.12)", lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
        sVah.setData(cvah);
        var sVal = pc.addSeries(LC.LineSeries, { color: "rgba(255,255,255,0.12)", lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
        sVal.setData(cval);

        var sD10 = pc.addSeries(LC.LineSeries, { color: "rgba(100,181,246,0.5)", lineWidth: 1, lastValueVisible: false });
        sD10.setData(dvwap10);
        var sD30 = pc.addSeries(LC.LineSeries, { color: "rgba(66,165,245,0.5)", lineWidth: 1, lastValueVisible: false });
        sD30.setData(dvwap30);
        var sD60 = pc.addSeries(LC.LineSeries, { color: "rgba(30,136,229,0.5)", lineWidth: 1, lastValueVisible: false });
        sD60.setData(dvwap60);
        var sD120 = pc.addSeries(LC.LineSeries, { color: "rgba(21,101,192,0.5)", lineWidth: 1, lastValueVisible: false });
        sD120.setData(dvwap120);

        if (markerList.length > 0) LC.createSeriesMarkers(cs, markerList.sort((a, b) => a.time - b.time));

        var cc = LC.createChart(document.getElementById("p2"), mkOpts(containerWidth, Math.round(containerHeight * 0.15), false));
        cc.priceScale("right").applyOptions({ autoScale: false });
        var sCwc = cc.addSeries(LC.LineSeries, { color: "#42a5f5", lineWidth: 2, lastValueVisible: false });
        sCwc.setData(cwcData);
        var sC1030 = cc.addSeries(LC.LineSeries, { color: "rgba(156,204,101,0.5)", lineWidth: 1, lastValueVisible: false });
        sC1030.setData(c1030);
        var sC3060 = cc.addSeries(LC.LineSeries, { color: "rgba(255,183,77,0.5)", lineWidth: 1, lastValueVisible: false });
        sC3060.setData(c3060);
        var sC60120 = cc.addSeries(LC.LineSeries, { color: "rgba(239,83,80,0.5)", lineWidth: 1, lastValueVisible: false });
        sC60120.setData(c60120);
        sCwc.createPriceLine({ price: 0.7, color: "rgba(38,166,154,0.4)", lineWidth: 1, lineStyle: 2 });
        sCwc.createPriceLine({ price: 0.3, color: "rgba(239,83,80,0.4)", lineWidth: 1, lineStyle: 2 });
        cc.priceScale("right").applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } });

        var mc = LC.createChart(document.getElementById("p3"), mkOpts(containerWidth, Math.round(containerHeight * 0.15), false));
        mc.priceScale("right").applyOptions({ autoScale: false });
        var sMcsComp = mc.addSeries(LC.LineSeries, { color: "#ab47bc", lineWidth: 2, lastValueVisible: false });
        sMcsComp.setData(mcsComp);
        var sMcsRaw = mc.addSeries(LC.LineSeries, { color: "rgba(171,71,188,0.35)", lineWidth: 1, lastValueVisible: false });
        sMcsRaw.setData(mcsRaw);
        var sMcsMfm = mc.addSeries(LC.LineSeries, { color: "rgba(236,64,122,0.35)", lineWidth: 1, lastValueVisible: false });
        sMcsMfm.setData(mcsMfmRaw);
        var sMcsDelta = mc.addSeries(LC.HistogramSeries, { priceScaleId: "delta", lastValueVisible: false });
        sMcsDelta.setData(mcsDelta);
        mc.priceScale("delta").applyOptions({ scaleMargins: { top: 0.7, bottom: 0 } });
        sMcsComp.createPriceLine({ price: 0, color: "rgba(255,255,255,0.15)", lineWidth: 1, lineStyle: 2 });
        mc.priceScale("right").applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } });

        var vc = LC.createChart(document.getElementById("p4"), mkOpts(containerWidth, Math.round(containerHeight * 0.10), true));
        var sV10 = vc.addSeries(LC.HistogramSeries, { lastValueVisible: false });
        sV10.setData(vel10);
        var sV30 = vc.addSeries(LC.HistogramSeries, { priceScaleId: "v30", lastValueVisible: false });
        sV30.setData(vel30);
        var sV60 = vc.addSeries(LC.HistogramSeries, { priceScaleId: "v60", lastValueVisible: false });
        sV60.setData(vel60);
        var sV120 = vc.addSeries(LC.HistogramSeries, { priceScaleId: "v120", lastValueVisible: false });
        sV120.setData(vel120);

        var allSeries = [
            {
                id: "leg1", chart: pc, s: [
                    { api: cs, label: "Price", col: "price", color: "#e6edf3", opts: function () { return cs.options(); } },
                    { api: sCwvap, label: "CWVAP", col: "cwvap", color: "#00bfa5", opts: function () { return sCwvap.options(); } },
                    { api: sCpoc, label: "CPOC", col: "cpoc", color: "#ffab40", opts: function () { return sCpoc.options(); } },
                    { api: sD10, label: "DV10", col: "dvwap_10", color: "#64b5f6", opts: function () { return sD10.options(); } },
                    { api: sD30, label: "DV30", col: "dvwap_30", color: "#42a5f5", opts: function () { return sD30.options(); } },
                    { api: sD60, label: "DV60", col: "dvwap_60", color: "#1e88e5", opts: function () { return sD60.options(); } },
                    { api: sD120, label: "DV120", col: "dvwap_120", color: "#1565c0", opts: function () { return sD120.options(); } }
                ]
            },
            {
                id: "leg2", chart: cc, s: [
                    { api: sCwc, label: "CWC", col: "cwc", color: "#42a5f5", opts: function () { return sCwc.options(); } },
                    { api: sC1030, label: "C1030", col: "c_10_30", color: "#9ccc65", opts: function () { return sC1030.options(); } },
                    { api: sC3060, label: "C3060", col: "c_30_60", color: "#ffb74d", opts: function () { return sC3060.options(); } },
                    { api: sC60120, label: "C60120", col: "c_60_120", color: "#ef5350", opts: function () { return sC60120.options(); } }
                ]
            },
            {
                id: "leg3", chart: mc, s: [
                    { api: sMcsComp, label: "MCS Comp", col: "mcs_composite", color: "#ab47bc", opts: function () { return sMcsComp.options(); } },
                    { api: sMcsRaw, label: "MCS", col: "mcs", color: "rgba(171,71,188,0.35)", opts: function () { return sMcsRaw.options(); } },
                    { api: sMcsMfm, label: "MFM", col: "mcs_mfm", color: "#ec407a", opts: function () { return sMcsMfm.options(); } },
                    { api: sMcsDelta, label: "\u0394M", col: "mcs_delta", color: "#7e57c2", opts: function () { return sMcsDelta.options(); } }
                ]
            },
            {
                id: "leg4", chart: vc, s: [
                    { api: sV10, label: "V10", col: "velocity_10_norm", color: "#64b5f6", opts: function () { return sV10.options(); } },
                    { api: sV30, label: "V30", col: "velocity_30_norm", color: "#42a5f5", opts: function () { return sV30.options(); } },
                    { api: sV60, label: "V60", col: "velocity_60_norm", color: "#1e88e5", opts: function () { return sV60.options(); } },
                    { api: sV120, label: "V120", col: "velocity_120_norm", color: "#1565c0", opts: function () { return sV120.options(); } }
                ]
            }
        ];

        function refreshUI(param) {
            var targetTime = param ? param.time : null;
            var targetIdx = targetTime ? timeToIndex[targetTime] : ledger.length - 1;

            allSeries.forEach(function (p) {
                var container = document.getElementById(p.id);
                if (!container) return;
                var html = "";
                p.s.forEach(function (s) {
                    if (!s.opts().visible) return;
                    var val = null;
                    if (param && param.seriesData.has(s.api)) {
                        val = param.seriesData.get(s.api);
                    } else {
                        var row = ledger[targetIdx];
                        if (row) {
                            var col = s.col;
                            if (row[col] != null) val = { value: row[col] };
                            else if (col === "price") val = { close: row.close };
                        }
                    }
                    var price = val ? (val.value !== undefined ? val.value : val.close) : null;
                    var display = (price != null) ? price.toFixed(price > 10 ? 2 : 4) : "\u2014";
                    html += '<div class="legend-item"><span class="legend-dot" style="background:' + s.color + '"></span>' +
                        '<span>' + s.label + ': ' + display + '</span></div>';
                });
                container.innerHTML = html;
            });
            if (param) buildSidebarAnnotations(ledger[targetIdx]);
        }

        refreshUI();

        var winActive = { 10: true, 30: true, 60: true, 120: true };
        function syncVis() {
            sD10.applyOptions({ visible: winActive[10] });
            sD30.applyOptions({ visible: winActive[30] });
            sD60.applyOptions({ visible: winActive[60] });
            sD120.applyOptions({ visible: winActive[120] });

            sV10.applyOptions({ visible: winActive[10] });
            sV30.applyOptions({ visible: winActive[30] });
            sV60.applyOptions({ visible: winActive[60] });
            sV120.applyOptions({ visible: winActive[120] });

            [sC1030, sC3060, sC60120].forEach((s, idx) => {
                var wins = [[10, 30], [30, 60], [60, 120]][idx];
                s.applyOptions({ visible: wins.some(w => winActive[w]) });
            });
            reflowCharts();
            refreshUI();
        }

        function reflowCharts() {
            const container = document.getElementById("chart-container");
            const cw = container.clientWidth;
            const ch = container.clientHeight;
            if (cw <= 0 || ch <= 0) return;

            const v2 = document.getElementById("cbCWC").checked;
            const v3 = document.getElementById("cbMCS").checked ||
                document.getElementById("cbMFM").checked ||
                document.getElementById("cbMCSD").checked;
            const v4 = [10, 30, 60, 120].some(n => document.getElementById("cb" + n).checked);

            const panels = [
                { id: "p1", chart: pc, weight: 6, visible: true },
                { id: "p2", chart: cc, weight: 1.5, visible: v2 },
                { id: "p3", chart: mc, weight: 1.5, visible: v3 },
                { id: "p4", chart: vc, weight: 1.0, visible: v4 }
            ];

            let totalWeight = 0;
            panels.forEach(p => {
                const el = document.getElementById(p.id);
                if (p.visible) {
                    el.style.display = "block";
                    totalWeight += p.weight;
                } else {
                    el.style.display = "none";
                }
            });

            panels.forEach(p => {
                if (p.visible) {
                    const ph = Math.round((p.weight / totalWeight) * ch);
                    p.chart.resize(cw, ph);
                }
            });

            const visiblePanels = panels.filter(p => p.visible);
            if (visiblePanels.length > 0) {
                const bottomPanel = visiblePanels[visiblePanels.length - 1];
                panels.forEach(p => {
                    p.chart.timeScale().applyOptions({ visible: (p === bottomPanel) });
                });
            }
        }

        [10, 30, 60, 120].forEach(n => document.getElementById("cb" + n).addEventListener("change", function () { winActive[n] = this.checked; syncVis(); }));

        var toggleMap = { cbCWVAP: [sCwvap], cbCPOC: [sCpoc], cbVA: [sVah, sVal], cbVol: [sVol], cbCWC: [sCwc], cbMCS: [sMcsComp, sMcsRaw], cbMFM: [sMcsMfm], cbMCSD: [sMcsDelta] };
        Object.keys(toggleMap).forEach(id => document.getElementById(id).addEventListener("change", function () {
            toggleMap[id].forEach(s => s.applyOptions({ visible: this.checked }));
            syncVis();
        }));

        [pc, cc, mc, vc].forEach(src => {
            src.timeScale().subscribeVisibleLogicalRangeChange(r => {
                if (!r) return;
                [pc, cc, mc, vc].forEach(t => { if (t !== src) t.timeScale().setVisibleLogicalRange(r); });
            });
        });

        var pRefs = [{ c: pc, s: cs }, { c: cc, s: sCwc }, { c: mc, s: sMcsComp }, { c: vc, s: sV10 }];
        pRefs.forEach((src, i) => {
            src.c.subscribeCrosshairMove(p => {
                pRefs.forEach((t, j) => {
                    if (!p || !p.time) t.c.clearCrosshairPosition();
                    else if (i !== j) t.c.setCrosshairPosition(NaN, p.time, t.s);
                });
                refreshUI(p);
                if (i === 0) {
                    if (!p || !p.time || !markersByTime[p.time]) tooltipEl.style.display = "none";
                    else {
                        tooltipEl.textContent = markersByTime[p.time];
                        tooltipEl.style.display = "block";
                        var r = document.getElementById("p1").getBoundingClientRect();
                        var x = p.point ? p.point.x + r.left + 16 : r.right - 200;
                        var tw = tooltipEl.offsetWidth;
                        if (x + tw > window.innerWidth - 10) x = window.innerWidth - tw - 10;
                        tooltipEl.style.left = x + "px"; tooltipEl.style.top = (r.top + 12) + "px";
                    }
                }
            });
        });

        var nTotal = ohlc.length, viewBars = Math.min(130, nTotal);
        [pc, cc, mc, vc].forEach(c => c.timeScale().setVisibleLogicalRange({ from: nTotal - viewBars, to: nTotal }));

        // Implement ResizeObserver for automatic, robust resizing
        const ro = new ResizeObserver(() => {
            requestAnimationFrame(() => reflowCharts());
        });
        ro.observe(container);

        // Initial reflow
        reflowCharts();

        return [pc, cc, mc, vc];
    }

    function buildSidebarAnnotations(l) {
        if (!l) return;
        function fmt(v, d) { return (v != null && typeof v === "number" && !isNaN(v)) ? v.toFixed(d || 2) : (v || "\u2014"); }
        var divDir = l.divergence_direction || "none";
        var divProb = (l.divergence_probability != null && divDir !== "none") ? l.divergence_probability.toFixed(2) : "\u2014";
        var divDisplay = divDir !== "none" ? (divDir + " " + divProb) : "\u2014";
        var conf = (l.state_confidence != null && !isNaN(l.state_confidence)) ? (Math.round(l.state_confidence * 100) + "%") : "\u2014";

        document.getElementById("sidebar-annotations").innerHTML = '<table class="ann-table">' +
            "<tr><td>State</td><td class='val'><strong>" + (l.state || "\u2014") + "</strong></td></tr>" +
            "<tr><td>Confidence</td><td class='val'>" + conf + "</td></tr>" +
            "<tr><td>MCS Composite</td><td class='val'>" + fmt(l.mcs_composite, 4) + "</td></tr>" +
            "<tr><td>CWC</td><td class='val'>" + fmt(l.cwc, 4) + "</td></tr>" +
            "<tr><td>CWVAP</td><td class='val'>" + fmt(l.cwvap) + "</td></tr>" +
            "<tr><td>CPOC</td><td class='val'>" + fmt(l.cpoc) + "</td></tr>" +
            "<tr><td>POC Spread</td><td class='val'>" + fmt(l.poc_spread) + " ATR</td></tr>" +
            "<tr><td>Divergence</td><td class='val'>" + divDisplay + "</td></tr>" +
            "<tr><td>Gradient Shape</td><td class='val'>" + (l.gradient_shape || "\u2014") + "</td></tr>" +
            "</table>";
    }
})();
