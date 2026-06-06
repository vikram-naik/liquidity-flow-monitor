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
    var lastPart = parts[parts.length - 1];
    var symbol = (lastPart && lastPart !== "dashboard") ? lastPart : "NIFTY 50";
    var aggMode = "daily";

    // NEW: Shared variables for jumping to dates
    var globalLedger = [];
    var globalTimeToIndex = {};
    var mainSeries = null;
    var focusDate = new URLSearchParams(window.location.search).get("focus");
    var isInitialLoad = true;

    var PANEL_DEFINITIONS = {
        "cts": { label: "CTS — Trend Score (Gate 2)" },
        "cts_slope": { label: "CTS Slope (Bull Gate)" },
        "cts_accel": { label: "CTS Acceleration (Gate 1)" },
        "cwc": { label: "CWC — Composite Coherence" },
        "cwc_slope": { label: "CWC Slope" },
        "psz": { label: "PSZ — Price Slope Z" },
        "price_slope_z": { label: "Price Slope Z (Raw)" },
        "rsz": { label: "RSZ — RDV Slope Z" },
        "rdv_slope_z": { label: "RDV Slope Z (Raw)" },
        "prt": { label: "PRT — Price Range Trend" },
        "prt_slope": { label: "PRT Slope" },
        "prt_accel": { label: "PRT Acceleration" },
        "fas": { label: "FAS — Fractal Alignment Score" },
        "cdvl": { label: "CDVL — Composite Delivery Velocity" },
        "dv_shock": { label: "DV-Shock — Delivery Liquidity Shock" },
        "esr": { label: "ESR — Relative Vol Spread Efficiency" }
    };

    function getActivePanels() {
        try {
            var conf = JSON.parse(localStorage.getItem("de_panel_config"));
            if (Array.isArray(conf) && conf.length > 0) return conf;
        } catch (e) { }
        return ["cts"]; // Only CTS by default
    }

    // Sidebar Tabs Logic
    var tabWatchlist = document.getElementById("tab-watchlist");
    var tabEngineState = document.getElementById("tab-engine-state");
    var contentWatchlist = document.getElementById("tab-content-watchlist");
    var contentEngineState = document.getElementById("tab-content-engine-state");

    if (tabWatchlist && tabEngineState && contentWatchlist && contentEngineState) {
        tabWatchlist.addEventListener("click", function () {
            tabWatchlist.classList.add("active");
            tabEngineState.classList.remove("active");
            contentWatchlist.classList.remove("hidden");
            contentEngineState.classList.add("hidden");
        });
        tabEngineState.addEventListener("click", function () {
            tabEngineState.classList.add("active");
            tabWatchlist.classList.remove("active");
            contentEngineState.classList.remove("hidden");
            contentWatchlist.classList.add("hidden");
        });
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

        // UX Fix: Reset focus date when switching symbols (unless it's the initial page load)
        if (!isInitialLoad && targetSymbol.toUpperCase() !== symbol) {
            focusDate = null;
            const picker = document.getElementById("jump-to-date");
            if (picker) picker.value = "";
        }
        isInitialLoad = false;

        symbol = targetSymbol.toUpperCase();
        var apiUrl = "/de/api/divergence-engine/" + symbol + "?agg_mode=" + aggMode;
        if (params.get("start_date")) apiUrl += "&start_date=" + params.get("start_date");
        if (params.get("end_date")) {
            apiUrl += "&end_date=" + params.get("end_date");
        }
        if (focusDate) apiUrl += "&focus_date=" + focusDate;


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
                globalLedger = data.ledger;

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

                if (focusDate) {
                    setTimeout(() => jumpToDate(focusDate), 500);
                }

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
        var ohlc = [], cwvap = [], vaHigh = [], vaLow = [];
        var ctsArr = [];
        var deliveryVol = [];
        globalTimeToIndex = {};
        var pZ = [], rZ = [], cRaw = [], cSmooth = [];
        var rdvArr = [], cwcArr = [], rdvConsArr = [], atrArr = [], distArr = [], delPctArr = [], pddArr = [], prtArr = [], prtBuyThreshArr = [], prtSellThreshArr = [], prtSlopeArr = [], prtSlopeBuyThreshArr = [], prtSlopeSellThreshArr = [], prtAccelArr = [], fasArr = [], fasBuyThreshArr = [], fasSellThreshArr = [];
        // NextGen gate series
        var ctsSlopeArr = [], ctsAccelArr = [], ctsAccelThreshArr = [], ctsBuyThreshArr = [], ctsSellThreshArr = [], cwcSlopeArr = [];
        var cdvlArr = [], vel60Arr = [], pdd120Arr = [], pdd120ThreshArr = [];
        // PSZ series
        var pszArr = [], pszSmoothArr = [], pszVArr = [], pszBuyThreshArr = [], pszSellThreshArr = [];
        // RSZ series
        var rszArr = [], rszVArr = [];
        var dvShockArr = [], esrArr = [], sdvwapArr = [];
        var entryMarkers = [];
        var exitMarkers = [];
        var oracleTroughMarkers = [];
        var oraclePeakMarkers = [];
        var oracleSmoothArr = [];

        for (var i = 0; i < ledger.length; i++) {
            var r = ledger[i];
            var t = r.date ? (r.date.includes(" ") ? r.date.split(" ")[0] : r.date) : null;
            if (!t) continue;
            globalTimeToIndex[t] = i;

            if (r.open != null && r.high != null && r.low != null && r.close != null) {
                ohlc.push({ time: t, open: r.open, high: r.high, low: r.low, close: r.close });
            }
            if (r.cwvap != null) cwvap.push({ time: t, value: r.cwvap }); else cwvap.push({ time: t });
            if (r.cts != null) ctsArr.push({ time: t, value: r.cts }); else ctsArr.push({ time: t });
            if (r.va_high != null) vaHigh.push({ time: t, value: r.va_high }); else vaHigh.push({ time: t });
            if (r.va_low != null) vaLow.push({ time: t, value: r.va_low }); else vaLow.push({ time: t });

            if (r.price_slope_z != null) pZ.push({ time: t, value: r.price_slope_z }); else pZ.push({ time: t });
            if (r.rdv_slope_z != null) rZ.push({ time: t, value: r.rdv_slope_z }); else rZ.push({ time: t });

            if (r.coherence_raw != null) cRaw.push({ time: t, value: r.coherence_raw }); else cRaw.push({ time: t });
            if (r.coherence != null) cSmooth.push({ time: t, value: r.coherence }); else cSmooth.push({ time: t });

            if (r.rdv != null) rdvArr.push({ time: t, value: r.rdv }); else rdvArr.push({ time: t });
            if (r.cwc != null) cwcArr.push({ time: t, value: r.cwc }); else cwcArr.push({ time: t });
            if (r.cwc_slope != null) cwcSlopeArr.push({ time: t, value: r.cwc_slope }); else cwcSlopeArr.push({ time: t });
            if (r.rdv_consistency != null) rdvConsArr.push({ time: t, value: r.rdv_consistency }); else rdvConsArr.push({ time: t });
            if (r.atr_20 != null) atrArr.push({ time: t, value: r.atr_20 }); else atrArr.push({ time: t });
            if (r.cwvap_dist != null) distArr.push({ time: t, value: r.cwvap_dist }); else distArr.push({ time: t });
            if (r.delivery_pct != null) delPctArr.push({ time: t, value: r.delivery_pct }); else delPctArr.push({ time: t });
            if (r.pdd_30 != null) pddArr.push({ time: t, value: r.pdd_30 }); else pddArr.push({ time: t });

            // NextGen gate series
            if (r.cts_slope != null) ctsSlopeArr.push({ time: t, value: r.cts_slope }); else ctsSlopeArr.push({ time: t });
            if (r.cts_accel != null) ctsAccelArr.push({ time: t, value: r.cts_accel }); else ctsAccelArr.push({ time: t });
            if (r.cts_accel_threshold != null) ctsAccelThreshArr.push({ time: t, value: r.cts_accel_threshold }); else ctsAccelThreshArr.push({ time: t });
            if (r.cts_buy_threshold != null) ctsBuyThreshArr.push({ time: t, value: r.cts_buy_threshold }); else ctsBuyThreshArr.push({ time: t });
            if (r.cts_sell_threshold != null) ctsSellThreshArr.push({ time: t, value: r.cts_sell_threshold }); else ctsSellThreshArr.push({ time: t });
            if (r.cdvl != null) cdvlArr.push({ time: t, value: r.cdvl }); else cdvlArr.push({ time: t });
            if (r.velocity_60_norm != null) vel60Arr.push({ time: t, value: r.velocity_60_norm }); else vel60Arr.push({ time: t });
            if (r.pdd_120 != null) pdd120Arr.push({ time: t, value: r.pdd_120 }); else pdd120Arr.push({ time: t });
            if (r.pdd_120_threshold != null) pdd120ThreshArr.push({ time: t, value: r.pdd_120_threshold }); else pdd120ThreshArr.push({ time: t });
            if (r.prt != null) prtArr.push({ time: t, value: r.prt }); else prtArr.push({ time: t });
            if (r.prt_buy_threshold != null) prtBuyThreshArr.push({ time: t, value: r.prt_buy_threshold }); else prtBuyThreshArr.push({ time: t });
            if (r.prt_sell_threshold != null) prtSellThreshArr.push({ time: t, value: r.prt_sell_threshold }); else prtSellThreshArr.push({ time: t });
            if (r.prt_slope != null) prtSlopeArr.push({ time: t, value: r.prt_slope }); else prtSlopeArr.push({ time: t });
            if (r.prt_slope_buy_threshold != null) prtSlopeBuyThreshArr.push({ time: t, value: r.prt_slope_buy_threshold }); else prtSlopeBuyThreshArr.push({ time: t });
            if (r.prt_slope_sell_threshold != null) prtSlopeSellThreshArr.push({ time: t, value: r.prt_slope_sell_threshold }); else prtSlopeSellThreshArr.push({ time: t });
            if (r.prt_accel != null) prtAccelArr.push({ time: t, value: r.prt_accel }); else prtAccelArr.push({ time: t });
            if (r.fas != null) fasArr.push({ time: t, value: r.fas }); else fasArr.push({ time: t });
            if (r.fas_buy_threshold != null) fasBuyThreshArr.push({ time: t, value: r.fas_buy_threshold }); else fasBuyThreshArr.push({ time: t });
            if (r.fas_sell_threshold != null) fasSellThreshArr.push({ time: t, value: r.fas_sell_threshold }); else fasSellThreshArr.push({ time: t });

            // PSZ
            if (r.price_slope_z != null) pszArr.push({ time: t, value: r.price_slope_z }); else pszArr.push({ time: t });
            if (r.psz_smooth != null) pszSmoothArr.push({ time: t, value: r.psz_smooth }); else pszSmoothArr.push({ time: t });
            if (r.psz_v != null) pszVArr.push({ time: t, value: r.psz_v }); else pszVArr.push({ time: t });
            if (r.psz_buy_threshold != null) pszBuyThreshArr.push({ time: t, value: r.psz_buy_threshold }); else pszBuyThreshArr.push({ time: t });
            if (r.psz_sell_threshold != null) pszSellThreshArr.push({ time: t, value: r.psz_sell_threshold }); else pszSellThreshArr.push({ time: t });

            // RSZ
            if (r.rdv_slope_z != null) rszArr.push({ time: t, value: r.rdv_slope_z }); else rszArr.push({ time: t });
            if (r.rsz_v != null) rszVArr.push({ time: t, value: r.rsz_v }); else rszVArr.push({ time: t });

            // Advanced PV features
            if (r.dv_shock != null) dvShockArr.push({ time: t, value: r.dv_shock }); else dvShockArr.push({ time: t });
            if (r.esr != null) esrArr.push({ time: t, value: r.esr }); else esrArr.push({ time: t });
            if (r.sdvwap != null) sdvwapArr.push({ time: t, value: r.sdvwap }); else sdvwapArr.push({ time: t });

            if (r.entry_signal) {
                entryMarkers.push({
                    time: t,
                    position: 'belowBar',
                    color: '#00e676',
                    shape: 'arrowUp',
                    text: String(r.entry_signal)
                });
            }

            if (r.cooldown) {
                entryMarkers.push({
                    time: t,
                    position: 'aboveBar',
                    color: '#00bcd4',
                    shape: 'circle'
                });
            }

            if (r.exit_signal) {
                exitMarkers.push({
                    time: t, position: 'aboveBar', color: '#FFD700',
                    shape: 'arrowDown', text: ''
                });
            }

            if (r.oracle_trough) {
                oracleTroughMarkers.push({
                    time: t, position: 'belowBar', color: '#3d5afe',
                    shape: 'arrowUp', text: 'ORC'
                });
            }

            if (r.oracle_peak) {
                oraclePeakMarkers.push({
                    time: t, position: 'aboveBar', color: '#f50057',
                    shape: 'arrowDown', text: 'ORC'
                });
            }

            if (r.oracle_smooth != null) {
                oracleSmoothArr.push({ time: t, value: r.oracle_smooth });
            }

            if (r.delivery_qty != null) {
                var mfm = r.mfm != null ? r.mfm : 0;
                deliveryVol.push({
                    time: t, value: r.delivery_qty,
                    color: mfm >= 0 ? "rgba(38,166,154,0.35)" : "rgba(239,83,80,0.35)"
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
        var existingSubs = container.querySelectorAll(".p-sub, .p-sub-lg");
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
        mainSeries = cs;
        var entryMarkersPrimitive = LC.createSeriesMarkers(cs, entryMarkers);
        var exitMarkersPrimitive = LC.createSeriesMarkers(cs, exitMarkers);
        var oracleTroughMarkersPrimitive = LC.createSeriesMarkers(cs, oracleTroughMarkers);
        var oraclePeakMarkersPrimitive = LC.createSeriesMarkers(cs, oraclePeakMarkers);

        var sCwvap = pc.addSeries(LC.LineSeries, { color: "#00bfa5", lineWidth: 2, lastValueVisible: false });
        sCwvap.setData(cwvap);

        var sOracleSmooth = pc.addSeries(LC.LineSeries, { color: "rgba(255, 255, 255, 0.2)", lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
        sOracleSmooth.setData(oracleSmoothArr);

        // --- VA High/Low (Delivery-Profile Value Area boundaries) ---
        var sVaHigh = pc.addSeries(LC.LineSeries, { color: "#7c4dff", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
        sVaHigh.setData(vaHigh);
        var sVaLow = pc.addSeries(LC.LineSeries, { color: "#7c4dff", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
        sVaLow.setData(vaLow);

        // --- Swing-Anchored DVWAP (S-DVWAP) ---
        var sSdvwap = pc.addSeries(LC.LineSeries, { color: "#e040fb", lineWidth: 2, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
        sSdvwap.setData(sdvwapArr);

        var leg1Config = [
            { api: cs, label: "Price", col: "price", color: "#e6edf3" },
            { api: sCwvap, label: "CWVAP", col: "cwvap", color: "#00bfa5" },
            { api: sVaHigh, label: "VA High", col: "va_high", color: "#7c4dff", dashed: true },
            { api: sVaLow, label: "VA Low", col: "va_low", color: "#7c4dff", dashed: true },
            { api: sSdvwap, label: "S-DVWAP", col: "sdvwap", color: "#e040fb", dashed: true },
            { type: "separator" },
            { label: "pw", col: "range_pos_10", color: "#8b949e" },
            { label: "pm", col: "range_pos_22", color: "#8b949e" },
            { label: "pq", col: "range_pos_63", color: "#8b949e" },
            { label: "py", col: "range_pos_252", color: "#8b949e" },
            { label: "ath", col: "is_ath", color: "#f1c40f" }
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

            if (panelKey === "cts") {
                // Fixed scale for CTS: 1 to -1 (Lightweight Charts v5)
                var pScale = c.priceScale("right");
                pScale.applyOptions({
                    autoScale: false,
                    scaleMargins: { top: 0, bottom: 0 }
                });
                if (pScale.setPriceRange) {
                    try {
                        pScale.setPriceRange({ min: -1.2, max: 1.2 });
                    } catch (e) {
                        console.error("Failed to set price range for CTS", e);
                    }
                }

                var sCts = c.addSeries(LC.LineSeries, { color: "#4fc3f7", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sCts.setData(ctsArr);

                var sCtsBuy = c.addSeries(LC.LineSeries, { color: "#42b883", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sCtsBuy.setData(ctsBuyThreshArr);

                var sCtsSell = c.addSeries(LC.LineSeries, { color: "#ef5350", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sCtsSell.setData(ctsSellThreshArr);

                var sCtsZ = c.addSeries(LC.LineSeries, { color: "rgba(139, 148, 158, 0.3)", lineWidth: 1, lineStyle: 0, lastValueVisible: false, priceLineVisible: false });
                sCtsZ.setData(ctsArr.map(d => ({ time: d.time, value: 0 })));

                legConfig.push({ api: sCts, label: "CTS", col: "cts", color: "#4fc3f7" });
                legConfig.push({ api: sCtsBuy, label: "Buy Thresh (P10)", col: "cts_buy_threshold", color: "#42b883", dashed: true });
                legConfig.push({ api: sCtsSell, label: "Sell Thresh (P90)", col: "cts_sell_threshold", color: "#ef5350", dashed: true });
            } else if (panelKey === "prt") {
                // PRT — Price Range Trend (-1 to 1)
                var pScale = c.priceScale("right");
                pScale.applyOptions({
                    autoScale: false,
                    scaleMargins: { top: 0, bottom: 0 }
                });
                if (pScale.setPriceRange) {
                    try {
                        pScale.setPriceRange({ min: -1.2, max: 1.2 });
                    } catch (e) {
                        console.error("Failed to set price range for PRT", e);
                    }
                }

                var sPrt = c.addSeries(LC.LineSeries, { color: "#ff8a65", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sPrt.setData(prtArr);

                var sPrtBuy = c.addSeries(LC.LineSeries, { color: "#42b883", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sPrtBuy.setData(prtBuyThreshArr);

                var sPrtSell = c.addSeries(LC.LineSeries, { color: "#ef5350", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sPrtSell.setData(prtSellThreshArr);

                var sPrtZ = c.addSeries(LC.LineSeries, { color: "rgba(139, 148, 158, 0.3)", lineWidth: 1, lineStyle: 0, lastValueVisible: false, priceLineVisible: false });
                sPrtZ.setData(prtArr.map(d => ({ time: d.time, value: 0 })));

                legConfig.push({ api: sPrt, label: "PRT", col: "prt", color: "#ff8a65" });
                legConfig.push({ api: sPrtBuy, label: "Buy Thresh (P10)", col: "prt_buy_threshold", color: "#42b883", dashed: true });
                legConfig.push({ api: sPrtSell, label: "Sell Thresh (P90)", col: "prt_sell_threshold", color: "#ef5350", dashed: true });
            } else if (panelKey === "prt_slope") {
                // PRT Slope
                var sPrtSlope = c.addSeries(LC.LineSeries, { color: "#80cbc4", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sPrtSlope.setData(prtSlopeArr);
                
                var sPrtSZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sPrtSZ.setData(prtSlopeArr.map(d => ({ time: d.time, value: 0 })));

                var sPrtSBT = c.addSeries(LC.LineSeries, { color: "rgba(128, 203, 196, 0.8)", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sPrtSBT.setData(prtSlopeBuyThreshArr);

                var sPrtSST = c.addSeries(LC.LineSeries, { color: "rgba(128, 203, 196, 0.8)", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sPrtSST.setData(prtSlopeSellThreshArr);

                legConfig.push({ api: sPrtSlope, label: "PRT Slope", col: "prt_slope", color: "#80cbc4" });
                legConfig.push({ api: sPrtSBT, label: "Buy Thr (10%)", col: "prt_slope_buy_threshold", color: "rgba(128, 203, 196, 0.8)" });
                legConfig.push({ api: sPrtSST, label: "Sell Thr (90%)", col: "prt_slope_sell_threshold", color: "rgba(128, 203, 196, 0.8)" });
            } else if (panelKey === "prt_accel") {
                // PRT Accel
                var sPrtAccel = c.addSeries(LC.LineSeries, { color: "#ce93d8", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sPrtAccel.setData(prtAccelArr);
                var sPrtAZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sPrtAZ.setData(prtAccelArr.map(d => ({ time: d.time, value: 0 })));
                legConfig.push({ api: sPrtAccel, label: "PRT Accel", col: "prt_accel", color: "#ce93d8" });
            } else if (panelKey === "fas") {
                // FAS — Fractal Alignment Score (-1 to 1)
                var pScale = c.priceScale("right");
                pScale.applyOptions({
                    autoScale: false,
                    scaleMargins: { top: 0, bottom: 0 }
                });
                if (pScale.setPriceRange) {
                    try {
                        pScale.setPriceRange({ min: -1.2, max: 1.2 });
                    } catch (e) {
                        console.error("Failed to set price range for FAS", e);
                    }
                }

                var sFas = c.addSeries(LC.LineSeries, { color: "#ffb74d", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sFas.setData(fasArr);

                var sFasZ = c.addSeries(LC.LineSeries, { color: "rgba(255, 255, 255, 0.5)", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sFasZ.setData(fasArr.map(d => ({ time: d.time, value: 0 })));

                var sFasBuy = c.addSeries(LC.LineSeries, { color: "#42b883", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sFasBuy.setData(fasBuyThreshArr);

                var sFasSell = c.addSeries(LC.LineSeries, { color: "#ef5350", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sFasSell.setData(fasSellThreshArr);

                legConfig.push({ api: sFas, label: "FAS", col: "fas", color: "#ffb74d" });
                legConfig.push({ api: sFasBuy, label: "Buy Thresh (P10)", col: "fas_buy_threshold", color: "#42b883", dashed: true });
                legConfig.push({ api: sFasSell, label: "Sell Thresh (P90)", col: "fas_sell_threshold", color: "#ef5350", dashed: true });
            } else if (panelKey === "cts_slope") {
                // Bull extra gate: cts_slope >= bull_slope_min
                var sCtsSlope = c.addSeries(LightweightCharts.LineSeries, { color: "#80cbc4", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sCtsSlope.setData(ctsSlopeArr);
                
                // Zero line
                var sCtsSZ = c.addSeries(LightweightCharts.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sCtsSZ.setData(ctsSlopeArr.map(d => ({ time: d.time, value: 0 })));
                
                // Bottom Threshold line at -0.187 (based on updated Nifty 500 calibration)
                sCtsSlope.createPriceLine({
                    price: -0.187,
                    color: '#ef5350',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: true,
                    title: '',
                });

                legConfig.push({ api: sCtsSlope, label: "CTS Slope", col: "cts_slope", color: "#80cbc4" });
            } else if (panelKey === "cts_accel") {
                // Gate 1: cts_accel > cts_accel_threshold (discounted in bear)
                var sCtsAccel = c.addSeries(LC.LineSeries, { color: "#ce93d8", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sCtsAccel.setData(ctsAccelArr);
                var sCtsAccelT = c.addSeries(LC.LineSeries, { color: "#ffd54f", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sCtsAccelT.setData(ctsAccelThreshArr);
                var sCtsAZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sCtsAZ.setData(ctsAccelArr.map(d => ({ time: d.time, value: 0 })));
                legConfig.push({ api: sCtsAccel, label: "CTS Accel", col: "cts_accel", color: "#ce93d8" });
                legConfig.push({ api: sCtsAccelT, label: "Threshold", col: "cts_accel_threshold", color: "#ffd54f", dashed: true });
            } else if (panelKey === "cwc") {
                // CWC — Composite Coherence
                var sCwc = c.addSeries(LC.HistogramSeries, {
                    lastValueVisible: false, priceLineVisible: false,
                    color: "#a5d6a7"
                });
                sCwc.setData(cwcArr.map(d => ({
                    time: d.time,
                    value: d.value != null ? d.value : undefined,
                    color: (d.value != null && d.value >= 0) ? "#a5d6a7" : "#ef9a9a"
                })));
                var sCwcZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sCwcZ.setData(cwcArr.map(d => ({ time: d.time, value: 0 })));
                legConfig.push({ api: sCwc, label: "CWC", col: "cwc", color: "#a5d6a7" });
            } else if (panelKey === "cwc_slope") {
                // CWC Slope
                var sCwcSlope = c.addSeries(LC.LineSeries, { color: "#81c784", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sCwcSlope.setData(cwcSlopeArr);
                
                var sCwcSZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sCwcSZ.setData(cwcSlopeArr.map(d => ({ time: d.time, value: 0 })));
                
                legConfig.push({ api: sCwcSlope, label: "CWC Slope", col: "cwc_slope", color: "#81c784" });
            } else if (panelKey === "psz") {
                // PSZ: psz_v velocity histogram + zero line
                var sPszV = c.addSeries(LC.HistogramSeries, {
                    color: "rgba(206, 147, 216, 0.6)",
                    lastValueVisible: false, priceLineVisible: false
                });
                sPszV.setData(pszVArr.map(d => ({
                    time: d.time,
                    value: d.value != null ? d.value : undefined,
                    color: (d.value != null && d.value >= 0) ? "rgba(206, 147, 216, 0.6)" : "rgba(239, 83, 80, 0.5)"
                })));

                // Zero line
                var sPszZ = c.addSeries(LC.LineSeries, {
                    color: "rgba(139, 148, 158, 0.3)", lineWidth: 1,
                    lastValueVisible: false, priceLineVisible: false
                });
                sPszZ.setData(pszVArr.map(d => ({ time: d.time, value: 0 })));

                legConfig.push({ api: sPszV, label: "PSZ_v", col: "psz_v", color: "#ce93d8" });
            } else if (panelKey === "price_slope_z") {
                // Raw price_slope_z line
                var sPszRaw = c.addSeries(LC.LineSeries, {
                    color: "#64b5f6", lineWidth: 2,
                    lastValueVisible: false, priceLineVisible: false
                });
                sPszRaw.setData(pszArr);
                var sPszRawZ = c.addSeries(LC.LineSeries, {
                    color: "rgba(139, 148, 158, 0.3)", lineWidth: 1,
                    lastValueVisible: false, priceLineVisible: false
                });
                sPszRawZ.setData(pszArr.map(d => ({ time: d.time, value: 0 })));
                legConfig.push({ api: sPszRaw, label: "PSZ Raw", col: "price_slope_z", color: "#64b5f6" });
            } else if (panelKey === "rsz") {
                // RSZ: rsz_v velocity histogram + zero line
                var sRszV = c.addSeries(LC.HistogramSeries, {
                    color: "rgba(255, 183, 77, 0.6)", // orange
                    lastValueVisible: false, priceLineVisible: false
                });
                sRszV.setData(rszVArr.map(d => ({
                    time: d.time,
                    value: d.value != null ? d.value : undefined,
                    color: (d.value != null && d.value >= 0) ? "rgba(255, 183, 77, 0.6)" : "rgba(239, 83, 80, 0.5)"
                })));
                var sRszZ = c.addSeries(LC.LineSeries, {
                    color: "rgba(139, 148, 158, 0.3)", lineWidth: 1,
                    lastValueVisible: false, priceLineVisible: false
                });
                sRszZ.setData(rszVArr.map(d => ({ time: d.time, value: 0 })));
                legConfig.push({ api: sRszV, label: "RSZ_v", col: "rsz_v", color: "#ffb74d" });
            } else if (panelKey === "rdv_slope_z") {
                // Raw rdv_slope_z line
                var sRszRaw = c.addSeries(LC.LineSeries, {
                    color: "#4db6ac", lineWidth: 2, // teal
                    lastValueVisible: false, priceLineVisible: false
                });
                sRszRaw.setData(rszArr);
                var sRszRawZ = c.addSeries(LC.LineSeries, {
                    color: "rgba(139, 148, 158, 0.3)", lineWidth: 1,
                    lastValueVisible: false, priceLineVisible: false
                });
                sRszRawZ.setData(rszArr.map(d => ({ time: d.time, value: 0 })));
                legConfig.push({ api: sRszRaw, label: "RSZ Raw", col: "rdv_slope_z", color: "#4db6ac" });
            } else if (panelKey === "cdvl") {
                // CDVL — Composite Delivery Velocity
                var sCdvl = c.addSeries(LC.LineSeries, { color: "#e3f2fd", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sCdvl.setData(cdvlArr);
                var sCdvlZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sCdvlZ.setData(cdvlArr.map(d => ({ time: d.time, value: 0 })));
                legConfig.push({ api: sCdvl, label: "CDVL", col: "cdvl", color: "#e3f2fd" });
            } else if (panelKey === "dv_shock") {
                // DV-Shock — Delivery Liquidity Shock
                var sDvShock = c.addSeries(LC.LineSeries, { color: "#00e676", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sDvShock.setData(dvShockArr);
                
                // Baseline guide lines at 0, 2.0 (high institutional print), and -1.0 (quiet dry-up)
                var sDvSZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sDvSZ.setData(dvShockArr.map(d => ({ time: d.time, value: 0 })));
                sDvShock.createPriceLine({ price: 2.0, color: "rgba(0, 230, 118, 0.4)", lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: "Shock (+2.0)" });
                sDvShock.createPriceLine({ price: -1.0, color: "rgba(239, 83, 80, 0.4)", lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: "Dry-up (-1.0)" });
                
                legConfig.push({ api: sDvShock, label: "DV-Shock", col: "dv_shock", color: "#00e676" });
            } else if (panelKey === "esr") {
                // ESR — Relative Vol Spread Efficiency
                var sEsr = c.addSeries(LC.LineSeries, { color: "#ffd54f", lineWidth: 2, lastValueVisible: false, priceLineVisible: false });
                sEsr.setData(esrArr);
                var sEsrZ = c.addSeries(LC.LineSeries, { color: "#424242", lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false });
                sEsrZ.setData(esrArr.map(d => ({ time: d.time, value: 0 })));
                
                legConfig.push({ api: sEsr, label: "ESR", col: "esr", color: "#ffd54f" });
            }

            allLegConfigs.push({ id: "legSub" + i, config: legConfig });
        });

        function updateLegend(containerId, config, param, targetIdx, ledgerIn) {
            var container = document.getElementById(containerId);
            if (!container) return;
            var currentLedger = ledgerIn || ledger;
            var html = "";
            config.forEach(function (s) {
                // Support separators (no label, no col)
                if (s.type === "separator") {
                    html += `<div class="legend-sep" style="width:1px; height:12px; background:#30363d; margin:0 4px"></div>`;
                    return;
                }

                if (s.api && !s.api.options().visible) return;
                var val = null;
                if (s.api && param && param.seriesData && param.seriesData.has(s.api)) {
                    val = param.seriesData.get(s.api);
                } else if (currentLedger && currentLedger[targetIdx]) {
                    var row = currentLedger[targetIdx];
                    if (s.col === "price") val = { close: row.close };
                    else if (row[s.col] != null) val = { value: row[s.col] };
                }
                var price = val ? (val.value !== undefined ? val.value : val.close) : null;
                var display = "\u2014";
                if (price !== null) {
                    if (typeof price === "boolean") {
                        display = price ? "y" : "n";
                    } else if (typeof price === "number") {
                        display = price.toFixed(price > 10 ? 2 : 4);
                    } else {
                        display = price;
                    }
                }
                var dotStyle = s.dashed
                    ? `background: repeating-linear-gradient(90deg, ${s.color}, ${s.color} 2px, transparent 2px, transparent 4px)`
                    : (s.color ? `background:${s.color}` : "display:none");
                
                var dotHtml = s.color ? `<span class="legend-dot" style="${dotStyle}"></span>` : "";

                html += `<div class="legend-item">${dotHtml}<span>${s.label}: ${display}</span></div>`;
            });
            container.innerHTML = html;
        }

        function refreshUI(param) {
            var targetTime = param ? param.time : null;
            // Use global variables and protect against undefined
            var targetIdx = (targetTime && globalTimeToIndex) ? globalTimeToIndex[targetTime] : (globalLedger.length - 1);
            if (targetIdx === undefined) targetIdx = globalLedger.length - 1;

            allLegConfigs.forEach(function (lg) {
                // Ensure ledger passed to updateLegend is globalLedger
                updateLegend(lg.id, lg.config, param, targetIdx, globalLedger);
            });

            if (globalLedger[targetIdx]) buildSidebarAnnotations(globalLedger[targetIdx]);
        }

        window.refreshUI = refreshUI;

        function bindToggle(id, handler) {
            var cb = document.getElementById(id);
            if (!cb) return;
            var saved = localStorage.getItem("de_toggle_" + id);
            if (saved !== null) {
                cb.checked = (saved === "true");
            }
            handler(cb.checked);
            cb.addEventListener("change", function () {
                handler(this.checked);
                localStorage.setItem("de_toggle_" + id, this.checked);
                refreshUI();
            });
        }

        bindToggle("cbCWVAP", val => sCwvap.applyOptions({ visible: val }));
        bindToggle("cbVA", val => {
            sVaHigh.applyOptions({ visible: val });
            sVaLow.applyOptions({ visible: val });
        });
        bindToggle("cbVol", val => sVol.applyOptions({ visible: val }));
        bindToggle("cbSDVWAP", val => sSdvwap.applyOptions({ visible: val }));

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
                    tooltipEl.style.display = "none";
                } else {
                    charts.forEach(c2 => {
                        if (c1 !== c2) {
                            var lg = allLegConfigs[charts.indexOf(c2)];
                            var s2 = lg && lg.config.length > 0 ? lg.config[0].api : null;
                            syncCrosshair(c2, s2, param);
                        }
                    });
                    refreshUI(param);

                    var idx = globalTimeToIndex[param.time];
                    var row = idx !== undefined ? globalLedger[idx] : null;
                    if (row && param.sourceEvent && (row.exit_signal && row.exit_reason || row.entry_signal && row.entry_reason)) {
                        var parts = [];
                        if (row.entry_signal && row.entry_reason)
                            parts.push('<span style="color:#00e676">&#9650; ENTRY</span> ' + row.entry_reason);
                        if (row.exit_signal && row.exit_reason)
                            parts.push('<span style="color:#FFD700">&#9660; EXIT</span> ' + row.exit_reason);
                        tooltipEl.innerHTML = parts.join('<br>');
                        tooltipEl.style.display = "block";
                        tooltipEl.style.left = (param.sourceEvent.clientX + 14) + "px";
                        tooltipEl.style.top = (param.sourceEvent.clientY - 36) + "px";
                    } else {
                        tooltipEl.style.display = "none";
                    }
                }
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
                activePanels.forEach(function (pane, i) {
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

    function jumpToDate(dateStr) {
        if (!dateStr || !chartInstances[0] || globalTimeToIndex[dateStr] === undefined) return;

        const idx = globalTimeToIndex[dateStr];
        const bars = globalLedger;

        // 1. Center the view on the date (+/- 65 bars)
        const fromIdx = Math.max(0, idx - 65);
        const toIdx = Math.min(bars.length - 1, idx + 65);
        
        const fromTime = bars[fromIdx].date.split(" ")[0];
        const toTime = bars[toIdx].date.split(" ")[0];

        chartInstances[0].timeScale().setVisibleRange({
            from: fromTime,
            to: toTime
        });

        // 2. TRIGGER RELEVANT DATA: Update all legends and sidebar for this date
        if (window.refreshUI) {
            window.refreshUI({ time: dateStr });
        }

        // 3. Visual Pointer: Set crosshair and add a focus marker
        chartInstances.forEach(c => {
            c.setCrosshairPosition(0, dateStr, c.series ? c.series[0] : null);
        });
        
        // Add a focus marker to main series
        const baseMarkers = []; 
        for (var i = 0; i < globalLedger.length; i++) {
            var r = globalLedger[i];
            var t = r.date ? r.date.split(" ")[0] : null;
            if (r.entry_signal) {
                baseMarkers.push({ time: t, position: 'belowBar', color: '#00e676', shape: 'arrowUp', text: String(r.entry_signal) });
            }
            if (r.cooldown) {
                baseMarkers.push({ time: t, position: 'aboveBar', color: '#00bcd4', shape: 'circle' });
            }
            if (r.exit_signal) {
                baseMarkers.push({ time: t, position: 'aboveBar', color: '#FFD700', shape: 'arrowDown', text: '' });
            }
            if (r.oracle_trough) {
                baseMarkers.push({ time: t, position: 'belowBar', color: '#3d5afe', shape: 'arrowUp', text: 'ORC' });
            }
            if (r.oracle_peak) {
                baseMarkers.push({ time: t, position: 'aboveBar', color: '#f50057', shape: 'arrowDown', text: 'ORC' });
            }
        }
        
        if (mainSeries) {
            mainSeries.setMarkers([
                ...baseMarkers,
                { time: dateStr, position: 'aboveBar', color: '#f1c40f', shape: 'arrowDown', text: 'FOCUS' }
            ]);
        }
        
        // Update the date picker value to match
        const picker = document.getElementById("jump-to-date");
        if (picker) picker.value = dateStr;
    }

    // Add listener for the new date input
    const jumpPicker = document.getElementById("jump-to-date");
    if (jumpPicker) {
        jumpPicker.addEventListener("change", function(e) {
            jumpToDate(e.target.value);
        });
        // UX Fix: Allow re-triggering jump on Enter even if date hasn't changed
        jumpPicker.addEventListener("keydown", function(e) {
            if (e.key === "Enter") {
                jumpToDate(this.value);
            }
        });
    }

    function buildSidebarAnnotations(l) {
        if (!l) return;
        function fmt(v, d) { return (v != null && typeof v === "number" && !isNaN(v)) ? v.toFixed(d || 2) : (v || "\u2014"); }

        var regime = l.regime || "\u2014";
        var regimeColor = regime === "uptrend" ? "#3fb950" : (regime === "downtrend" ? "#ef5350" : (regime === "transition" ? "#d29922" : "#8b949e"));

        var statusHtml = "";
        if (l.in_trade_pnl != null) {
            var pnl = parseFloat(l.in_trade_pnl);
            var pnlColor = pnl >= 0 ? "#3fb950" : "#ef5350";
            statusHtml = "<tr><td>Current PnL</td><td class='val' style='color:" + pnlColor + "'>" + pnl.toFixed(2) + "%</td></tr>";
        } else {
            statusHtml = "<tr><td>Regime</td><td class='val' style='color:" + regimeColor + "'>" + regime + "</td></tr>";
        }

        var entryPathHtml = "";
        if (l.entry_signal && l.entry_tag) {
            var pathName = l.entry_tag.replace("SavgolCTS ", "");
            entryPathHtml = "<tr><td>Entry Path</td><td class='val' style='color:#00e676; font-weight:600;'>" + pathName + "</td></tr>";
        }

        document.getElementById("state-table").innerHTML =
            "<tr><td>Date</td><td class='val'>" + (l.date ? l.date.split("T")[0] : "\u2014") + "</td></tr>" +
            statusHtml +
            entryPathHtml +
            (function () {
                if (!l.entry_reason || l.entry_reason === "Neutral/No Entry" || l.entry_reason === "Hold") return "";
                
                if (l.entry_signal) {
                    var html = "";
                    if (l.entry_reason.includes(" | ")) {
                        var parts = l.entry_reason.split(" | ");
                        var mainReason = parts[0];
                        html += "<tr><td>Signal Reason</td><td class='val' style='color:#00e676; font-size:11px; vertical-align:top'>" + mainReason + "</td></tr>";
                        
                        parts.slice(1).forEach(function(p) {
                            var kv = p.split(": ");
                            if (kv.length > 1) {
                                var label = kv[0].trim();
                                if (label === "RawScore") label = "Original ML Score";
                                html += "<tr><td>" + label + "</td><td class='val'>" + kv[1].trim() + "</td></tr>";
                            }
                        });
                    } else {
                        html = "<tr><td>Signal Reason</td><td class='val' style='color:#00e676; font-size:11px; vertical-align:top'>" + l.entry_reason + "</td></tr>";
                    }
                    return html;
                } else {
                    var reasons = l.entry_reason.split(" | ");
                    var rowsHtml = reasons.map(function(r) {
                        var parts = r.split(": ");
                        var type = parts.length > 1 ? parts[0] : "";
                        var text = parts.length > 1 ? parts.slice(1).join(": ") : r;
                        return "<tr><td style='color:#b0bec5; width:50px; vertical-align:top; padding:4px; font-weight:500; border: 1px solid #30363d;'>" + type + "</td><td style='color:#ff7043; vertical-align:top; padding:4px; border: 1px solid #30363d;'>" + text + "</td></tr>";
                    }).join("");
                    var tableHtml = "<table style='width:100%; border-collapse:collapse; margin-top:6px; font-size:11px; border: 1px solid #30363d;'>" + rowsHtml + "</table>";
                    return "<tr><td colspan='2' style='padding-top:12px; border-top:1px solid #21262d; margin-top:8px;'><div style='color:#b0bec5; font-weight:600; text-transform:uppercase; font-size:10px; letter-spacing:0.5px; margin-bottom:4px;'>Rejected Reasons</div>" + tableHtml + "</td></tr>";
                }
            })() +
            (function () {
                if (!l.exit_reason) return "";
                return "<tr><td>Exit Signal</td><td class='val' style='color:#ff1744; font-size:11px; vertical-align:top'>" + l.exit_reason + "</td></tr>";
            })();



        // Clear any scoring breakdown area
        var diagEl = document.getElementById("gate-diagnostics");
        if (diagEl) diagEl.innerHTML = "";
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
                try { this.defaultWlId = parseInt(defStr) || null; } catch (e) { }
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
            var wlAdd = document.getElementById("wl-add");
            if (wlAdd) wlAdd.onclick = () => { var name = prompt("Enter Watchlist Name:"); if (name) this.api("/de/api/watchlists", "POST", { name }).then(() => this.fetchLists()).catch(err => alert("Error: " + err.message)); };

            var wlRename = document.getElementById("wl-rename");
            if (wlRename) wlRename.onclick = () => { if (!this.currentWlId) return; var name = prompt("Enter New Name:"); if (name) this.api("/de/api/watchlists/" + this.currentWlId, "PATCH", { name }).then(() => { if (this.currentWlId == this.defaultWlId) localStorage.removeItem("de_default_watchlist"); this.fetchLists(true); }).catch(err => alert("Error: " + err.message)); };

            var wlDelete = document.getElementById("wl-delete");
            if (wlDelete) wlDelete.onclick = () => { if (!this.currentWlId || !confirm("Delete this watchlist?")) return; this.api("/de/api/watchlists/" + this.currentWlId, "DELETE").then(() => { if (this.currentWlId == this.defaultWlId) { this.defaultWlId = null; localStorage.removeItem("de_default_watchlist"); } this.currentWlId = null; this.fetchLists(); }).catch(err => alert("Error deleting watchlist: " + err.message)); };

            var btnDefault = document.getElementById("wl-set-default");
            if (btnDefault) {
                btnDefault.onclick = () => {
                    if (!this.currentWlId) return;
                    if (this.currentWlId == this.defaultWlId) {
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
                    }).catch(err => alert("Error adding item: " + err.message));
                });
            }

            document.getElementById("wl-items").onclick = (e) => { var item = e.target.closest(".wl-item"); if (!item) return; var sym = item.dataset.sym; if (e.target.classList.contains("remove-btn")) { this.api(`/de/api/watchlists/${this.currentWlId}/items/${sym}`, "DELETE").then(() => this.fetchItems()).catch(err => alert("Error removing item: " + err.message)); } else { loadSymbol(sym); } };
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
                if (keepCurrentSelection && this.currentWlId) {
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
                    var isDef = (l.id == this.defaultWlId) ? " \u2605" : "";
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
        updateUIState: function () {
            var btnDefault = document.getElementById("wl-set-default");
            if (btnDefault) {
                if (this.currentWlId && this.currentWlId == this.defaultWlId) {
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
            this.api("/de/api/watchlists/import-index", "POST", { watchlist_id: 0, index_name: indexName }).then(res => {
                this.currentWlId = res.watchlist_id;
                this.fetchLists(true);
                document.getElementById("wl-import-dropdown").style.display = "none";
            }).catch(err => {
                alert("Import failed: " + err.message);
                console.error("Import error:", err);
            });
        },
        fetchItems: function () { if (!this.currentWlId) return; fetch(`/de/api/watchlists/${this.currentWlId}/items`).then(r => r.json()).then(items => { this.currentItems = items; this.renderItems(); }); },
        renderItems: function () {
            var sortMode = this.activeSortMode, sorted = [...this.currentItems];
            if (sortMode === "name-asc") sorted.sort((a, b) => a.symbol.localeCompare(b.symbol)); else if (sortMode === "name-desc") sorted.sort((a, b) => b.symbol.localeCompare(a.symbol)); else if (sortMode === "date-asc") sorted.sort((a, b) => new Date(a.added_at) - new Date(b.added_at)); else if (sortMode === "date-desc") sorted.sort((a, b) => new Date(b.added_at) - new Date(a.added_at));
            document.getElementById("wl-items").innerHTML = sorted.map(i => `<div class="wl-item ${i.symbol === symbol ? 'active' : ''}" data-sym="${i.symbol}"><div class="wl-item-info"><span class="sym">${i.symbol}</span><span class="date">${formatDate(i.added_at)}</span></div><span class="remove-btn">\u00d7</span></div>`).join("");
            this.updateActiveState();
        },
        updateActiveState: function () { document.querySelectorAll(".wl-item").forEach(item => { if (item.dataset.sym === symbol) item.classList.add("active"); else item.classList.remove("active"); }); }
    };
    fetchAllStocks();
    WatchlistManager.init();

    // --- Settings Panel Logic (panel configuration only) ---
    var settingsOverlay = document.getElementById("settings-overlay");
    var currentPanelsConfig = [];

    var btnOpenSettings = document.getElementById("open-settings");
    if (btnOpenSettings) {
        btnOpenSettings.onclick = function () {
            renderPanelsSettings();
            settingsOverlay.classList.remove("hidden");
        };
    }

    document.querySelectorAll(".settings-tab").forEach(function (btn) {
        btn.addEventListener("click", function () {
            document.querySelectorAll(".settings-tab").forEach(b => b.classList.remove("active"));
            this.classList.add("active");
            document.querySelectorAll(".settings-body").forEach(b => b.classList.add("hidden"));
            document.getElementById(this.dataset.target).classList.remove("hidden");
        });
    });

    function closeSettings() { settingsOverlay.classList.add("hidden"); }
    var btnCloseSettings = document.getElementById("settings-close");
    if (btnCloseSettings) btnCloseSettings.onclick = closeSettings;

    var btnCancelSettings = document.getElementById("settings-cancel");
    if (btnCancelSettings) btnCancelSettings.onclick = closeSettings;
    settingsOverlay.addEventListener("click", function (e) { if (e.target === settingsOverlay) closeSettings(); });

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
                + '<button class="mini-btn move-up" data-idx="' + i + '">\u25B2</button>'
                + '<button class="mini-btn move-down" data-idx="' + i + '">\u25BC</button>'
                + '</div>'
                + '<span class="panel-setting-label">' + label + '</span>'
                + '<button class="mini-button delete-panel" data-idx="' + i + '">\u00d7</button>'
                + '</div>';
        });
        listContainer.innerHTML = html;

        var select = document.getElementById("add-panel-select");
        var selHtml = '<option value="">-- Select Panel --</option>';
        Object.keys(PANEL_DEFINITIONS).forEach(function (key) {
            if (currentPanelsConfig.indexOf(key) === -1) {
                selHtml += '<option value="' + key + '">' + PANEL_DEFINITIONS[key].label + '</option>';
            }
        });
        select.innerHTML = selHtml;
        document.getElementById("add-panel-btn").disabled = currentPanelsConfig.length >= Object.keys(PANEL_DEFINITIONS).length;

        listContainer.querySelectorAll(".move-up").forEach(function (btn) {
            btn.onclick = function () {
                var idx = parseInt(this.dataset.idx);
                if (idx > 0) {
                    var tmp = currentPanelsConfig[idx];
                    currentPanelsConfig[idx] = currentPanelsConfig[idx - 1];
                    currentPanelsConfig[idx - 1] = tmp;
                    updatePanelsUI();
                }
            };
        });
        listContainer.querySelectorAll(".move-down").forEach(function (btn) {
            btn.onclick = function () {
                var idx = parseInt(this.dataset.idx);
                if (idx < currentPanelsConfig.length - 1) {
                    var tmp = currentPanelsConfig[idx];
                    currentPanelsConfig[idx] = currentPanelsConfig[idx + 1];
                    currentPanelsConfig[idx + 1] = tmp;
                    updatePanelsUI();
                }
            };
        });
        listContainer.querySelectorAll(".delete-panel").forEach(function (btn) {
            btn.onclick = function () {
                var idx = parseInt(this.dataset.idx);
                currentPanelsConfig.splice(idx, 1);
                updatePanelsUI();
            };
        });
    }

    var btnAddPanel = document.getElementById("add-panel-btn");
    if (btnAddPanel) {
        btnAddPanel.onclick = function () {
            var valIdx = document.getElementById("add-panel-select").value;
            if (valIdx) {
                currentPanelsConfig.push(valIdx);
                updatePanelsUI();
            }
        };
    }

    var btnSaveSettings = document.getElementById("settings-save");
    if (btnSaveSettings) {
        btnSaveSettings.onclick = function () {
            // Save UI Panels
            localStorage.setItem("de_panel_config", JSON.stringify(currentPanelsConfig));
            closeSettings();
            loadSymbol(symbol);
        };
    }

    var btnResetSettings = document.getElementById("settings-reset");
    if (btnResetSettings) {
        btnResetSettings.onclick = function () {
            if (!confirm("Reset panels to defaults?")) return;
            localStorage.removeItem("de_panel_config");
            closeSettings();
            loadSymbol(symbol);
        };
    }

    window.WatchlistManager = WatchlistManager;
})();
