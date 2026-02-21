// Efficient, robust toggle handling for all button groups in the header
window.switchTbtn = function (btn) {
    const group = btn.closest('.btn-group');
    if (!group) return;

    // Toggle active state within group
    group.querySelectorAll('.tbtn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // Trigger reload
    if (typeof loadSymbol === 'function') loadSymbol();
};


// ─── Chart Theme ──────────────────────────────────────────────
const theme = {
    layout: {
        textColor: '#6e7399',
        background: { type: 'solid', color: '#0b0d14' },
        fontFamily: "'Inter', sans-serif",
        fontSize: 11,
    },
    grid: {
        vertLines: { color: '#141728' },
        horzLines: { color: '#141728' },
    },
    leftPriceScale: { visible: true, borderColor: '#262a42', minimumWidth: 60, width: 60 },
    rightPriceScale: { borderColor: '#262a42', minimumWidth: 60, width: 60 },
    timeScale: {
        borderColor: '#262a42',
        timeVisible: true,
        rightOffset: 5,
        barSpacing: 6,
        minBarSpacing: 2,
        visible: true,
    },
    localization: {
        priceFormatter: (p) => p.toFixed(2),
    },
    crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: {
            color: '#b0b5d0',
            width: 1,
            style: 0, // Solid
            labelVisible: true,
        },
        horzLine: {
            color: '#b0b5d0',
            width: 1,
            style: 0, // Solid
            labelVisible: true,
        },
    },
};

const candleOpts = {
    upColor: '#00e396', downColor: '#ff4976',
    borderVisible: false,
    wickUpColor: '#00e396', wickDownColor: '#ff4976',
};

// ─── Create Charts ────────────────────────────────────────────
function createChart(containerId, extraOpts = {}) {
    const el = document.getElementById(containerId);
    const chart = LightweightCharts.createChart(el, { ...theme, ...extraOpts });
    new ResizeObserver(entries => {
        const { width, height } = entries[0].contentRect;
        if (width > 0 && height > 0) chart.resize(width, height);
    }).observe(el);
    return chart;
}

const priceChart = createChart('price-chart', {
    timeScale: { visible: false, height: 0 },
    handleScroll: { mouseWheel: true, pressedMouseMove: true },
    handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
});
priceChart.priceScale('left').applyOptions({
    visible: false
});
priceChart.priceScale('right').applyOptions({
    visible: true,
    borderColor: '#262a42',
    minimumWidth: 100
});

// Number Abbreviation Helper
function abbrev(val) {
    const abs = Math.abs(val);
    const sign = val < 0 ? '-' : '';
    if (abs >= 10000000) return sign + (abs / 10000000).toFixed(1) + 'Cr';
    if (abs >= 100000) return sign + (abs / 100000).toFixed(1) + 'L';
    if (abs >= 1000) return sign + (abs / 1000).toFixed(1) + 'K';
    return val.toString();
}

const ledgerChart = createChart('ledger-chart', {
    timeScale: { visible: false, height: 0 },
    localization: { priceFormatter: abbrev }
});
ledgerChart.priceScale('left').applyOptions({
    visible: false
});
ledgerChart.priceScale('right').applyOptions({
    visible: true,
    borderColor: '#262a42',
    minimumWidth: 100
});
// Ghost scale for Cumulative DVL (Independent, Invisible)
ledgerChart.priceScale('ghost').applyOptions({
    visible: false,
    scaleMargins: { top: 0.1, bottom: 0.1 } // Give it some breathing room
});

// Series - Pane 0
const candleSeries = priceChart.addCandlestickSeries(candleOpts);
const davwapSeries = priceChart.addLineSeries({
    color: '#2962FF',
    lineWidth: 2,
    lastValueVisible: true,
    priceLineVisible: false,
});
const volumeSeries = priceChart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: '', // Attach to overlay
});
priceChart.priceScale('').applyOptions({
    scaleMargins: {
        top: 0.8,    // Push volume down (leaves top 80% for price)
        bottom: 0,
    },
});

// Series - Pane 1
const cumulativeLedgerSeries = ledgerChart.addLineSeries({
    color: 'rgba(255, 255, 255, 0.4)',
    lineWidth: 1,
    lineStyle: 3, // Dotted
    priceScaleId: 'ghost', // Separate scale
    crosshairMarkerVisible: false,
    lastValueVisible: false,
    priceLineVisible: false
});

const ledgerSeries = ledgerChart.addLineSeries({
    color: '#FFD700',
    lineWidth: 2,
    priceScaleId: 'right',
});

// Pane 2: MCS Chart Setup
const mcsChart = createChart('mcs-chart', {
    timeScale: { visible: true },
});
mcsChart.priceScale('left').applyOptions({
    visible: false
});
mcsChart.priceScale('right').applyOptions({
    visible: true,
    borderColor: '#262a42',
    minimumWidth: 100
});
const mcsSeries = mcsChart.addHistogramSeries({
    priceScaleId: 'right',
    base: 0,
    autoscaleInfoProvider: () => ({
        priceRange: {
            minValue: -1,
            maxValue: 1,
        }
    })
});
mcsSeries.createPriceLine({
    price: 0,
    color: '#6e7399',
    lineStyle: 2, // Dashed
});

// Synchronize Charts
let isSyncing = false;
function syncCharts() {
    priceChart.timeScale().subscribeVisibleTimeRangeChange(range => {
        if (!range || isSyncing) return;
        isSyncing = true;
        ledgerChart.timeScale().setVisibleRange(range);
        mcsChart.timeScale().setVisibleRange(range);
        isSyncing = false;
    });
    ledgerChart.timeScale().subscribeVisibleTimeRangeChange(range => {
        if (!range || isSyncing) return;
        isSyncing = true;
        priceChart.timeScale().setVisibleRange(range);
        mcsChart.timeScale().setVisibleRange(range);
        isSyncing = false;
    });
    mcsChart.timeScale().subscribeVisibleTimeRangeChange(range => {
        if (!range || isSyncing) return;
        isSyncing = true;
        priceChart.timeScale().setVisibleRange(range);
        ledgerChart.timeScale().setVisibleRange(range);
        isSyncing = false;
    });

    // Crosshair Sync
    let isMovingCrosshair = false;
    priceChart.subscribeCrosshairMove(param => {
        if (isMovingCrosshair) return;
        isMovingCrosshair = true;
        if (param.time) {
            ledgerChart.setCrosshairPosition(null, param.time, ledgerSeries);
            mcsChart.setCrosshairPosition(null, param.time, mcsSeries);
        } else {
            ledgerChart.clearCrosshairPosition();
            mcsChart.clearCrosshairPosition();
        }
        isMovingCrosshair = false;
    });
    ledgerChart.subscribeCrosshairMove(param => {
        if (isMovingCrosshair) return;
        isMovingCrosshair = true;
        if (param.time) {
            priceChart.setCrosshairPosition(null, param.time, candleSeries);
            mcsChart.setCrosshairPosition(null, param.time, mcsSeries);
        } else {
            priceChart.clearCrosshairPosition();
            mcsChart.clearCrosshairPosition();
        }
        isMovingCrosshair = false;
    });
    mcsChart.subscribeCrosshairMove(param => {
        if (isMovingCrosshair) return;
        isMovingCrosshair = true;
        if (param.time) {
            priceChart.setCrosshairPosition(null, param.time, candleSeries);
            ledgerChart.setCrosshairPosition(null, param.time, ledgerSeries);
        } else {
            priceChart.clearCrosshairPosition();
            ledgerChart.clearCrosshairPosition();
        }
        isMovingCrosshair = false;
    });
}
syncCharts();

// ─── Watchlist Manager ────────────────────────────────────────
// ... (rest of WatchlistManager stays same)
class WatchlistManager {
    constructor() {
        this.lists = [];
        this.activeListId = null;
        this.init();
    }

    async init() {
        await this.fetchLists();
        if (this.lists.length > 0) {
            // Try to restore last active list
            const savedId = localStorage.getItem('lfm_last_wl_id');
            const target = this.lists.find(l => l.id == savedId) || this.lists[0];
            this.switchList(target.id);
        } else {
            // Create default if none
            await this.createList("Default");
        }
    }

    async fetchLists() {
        try {
            const res = await fetch('/lfm/api/watchlists');
            this.lists = await res.json();
            this.renderSelector();
        } catch (e) { console.error("Failed to fetch watchlists", e); }
    }

    renderSelector() {
        const sel = document.getElementById('wl-selector');
        sel.innerHTML = this.lists.map(l => `<option value="${l.id}">${l.name}</option>`).join('');
        if (this.activeListId) sel.value = this.activeListId;
        this.renderWlMenu();
    }

    async renderWlMenu() {
        const container = document.getElementById('wl-menu-indices');
        if (!container) return;

        try {
            const listRes = await fetch('/lfm/api/watchlists/supported-indices');
            const supported = await listRes.json();
            const existingNames = this.lists.map(l => l.name.toUpperCase());

            container.innerHTML = supported.map(idx => {
                const isSetup = existingNames.includes(idx.toUpperCase());
                return `<button onclick="watchlistManager.quickImportIndex('${idx}')">
                    <span style="display:flex; justify-content:space-between; width:100%; align-items:center;">
                        <span>${idx}</span>
                        <span style="font-size:10px; color:${isSetup ? 'var(--green)' : 'var(--text-2)'}">
                            ${isSetup ? '● Active' : '+ Setup'}
                        </span>
                    </span>
                </button>`;
            }).join('');
        } catch (e) {
            container.innerHTML = '<div style="padding:10px; font-size:11px; color:var(--red);">Error loading indices</div>';
        }
    }

    async quickImportIndex(indexName) {
        const existing = this.lists.find(l => l.name.toUpperCase() === indexName.toUpperCase());
        if (existing) {
            this.switchList(existing.id);
            toggleWlMenu(); // Close menu
            return;
        }

        this.showToast(`Setting up ${indexName}...`);
        try {
            const res = await fetch(`/lfm/api/watchlists/import-index`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ index_name: indexName })
            });
            const data = await res.json();
            if (res.ok) {
                this.showToast(`✅ ${indexName} ready (${data.imported} stocks)`);
                await this.fetchLists();
                if (data.watchlist_id) this.switchList(data.watchlist_id);
            } else {
                alert(data.detail || "Import failed");
            }
        } catch (e) {
            alert("Import failed: " + e.message);
        }
        toggleWlMenu(); // Close menu
    }

    async switchList(id) {
        if (!id) return;
        this.activeListId = id;
        localStorage.setItem('lfm_last_wl_id', id);
        document.getElementById('wl-selector').value = id;
        await this.fetchItems(id);
    }

    async fetchItems(id) {
        const container = document.getElementById('wl-items');
        container.innerHTML = '<div class="wl-empty">Loading...</div>';

        try {
            const res = await fetch(`/lfm/api/watchlists/${id}/items`);
            const items = await res.json();

            if (items.length === 0) {
                container.innerHTML = '<div class="wl-empty">List is empty</div>';
                return;
            }

            container.innerHTML = items.map(item => `
                <div class="wl-item ${item.symbol === document.getElementById('symbol-input').value ? 'active' : ''}" 
                     onclick="loadSymbol('${item.symbol}')">
                    <span>${item.symbol}</span>
                    <span class="del-btn" onclick="event.stopPropagation(); watchlistManager.removeItem('${item.symbol}')">×</span>
                </div>
            `).join('');
        } catch (e) {
            container.innerHTML = '<div class="wl-empty">Error loading items</div>';
        }
    }

    async createList(name = null) {
        if (!name) name = prompt("Enter Watchlist Name:");
        if (!name) return;

        try {
            const res = await fetch('/lfm/api/watchlists', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, description: '' })
            });
            if (!res.ok) throw new Error("Failed to create list");
            await this.fetchLists();
            const newList = this.lists.find(l => l.name === name);
            if (newList) this.switchList(newList.id);
        } catch (e) { alert(e.message); }
    }

    async renameCurrent() {
        if (!this.activeListId) return;
        const current = this.lists.find(l => l.id == this.activeListId);
        const newName = prompt("Rename Watchlist:", current.name);
        if (!newName || newName === current.name) return;

        try {
            const res = await fetch(`/lfm/api/watchlists/${this.activeListId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName })
            });
            if (!res.ok) throw new Error("Rename failed");
            await this.fetchLists();
            this.showToast("Watchlist renamed");
        } catch (e) { alert(e.message); }
    }

    async deleteCurrent() {
        if (!this.activeListId) return;
        const current = this.lists.find(l => l.id == this.activeListId);
        if (!current) return;
        if (!confirm(`Delete watchlist "${current.name}" and all its items?`)) return;

        try {
            const res = await fetch(`/lfm/api/watchlists/${this.activeListId}`, { method: 'DELETE' });
            if (!res.ok) throw new Error("Delete failed");
            await this.fetchLists();
            this.activeListId = this.lists[0]?.id || null;
            if (this.activeListId) this.switchList(this.activeListId);
            else document.getElementById('wl-items').innerHTML = '<div class="wl-empty">No watchlists</div>';
            this.showToast("Watchlist deleted");
        } catch (e) { alert(e.message); }
    }

    async addToCurrent(symbol) {
        if (!this.activeListId) return alert("Please select or create a watchlist first");
        if (!symbol) return;

        try {
            const res = await fetch(`/lfm/api/watchlists/${this.activeListId}/items`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol })
            });
            if (res.ok) {
                this.fetchItems(this.activeListId);
                this.showToast(`Added ${symbol}`);
            }
        } catch (e) { console.error(e); }
    }

    async removeItem(symbol) {
        if (!this.activeListId) return;
        if (!confirm(`Remove ${symbol}?`)) return;

        try {
            const res = await fetch(`/lfm/api/watchlists/${this.activeListId}/items/${symbol}`, { method: 'DELETE' });
            if (res.ok) {
                await this.fetchItems(this.activeListId);
                this.showToast(`Removed ${symbol}`);
            }
        } catch (e) {
            console.error("Remove failed", e);
            this.showToast("Remove failed");
        }
    }

    showToast(msg) {
        toast(msg);
    }
}

const watchlistManager = new WatchlistManager();
window.watchlistManager = watchlistManager;

// ─── UI Helpers ───────────────────────────────────────────────
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('collapsed');
}

function toggleWlMenu(event) {
    if (event) event.stopPropagation();
    const menu = document.getElementById('wl-menu');
    menu.classList.toggle('show');
}

// Close dropdowns when clicking outside
window.onclick = function (event) {
    if (!event.target.closest('.icon-btn') && !event.target.closest('.dropdown-content')) {
        const dropdowns = document.getElementsByClassName("dropdown-content");
        for (let i = 0; i < dropdowns.length; i++) {
            const openDropdown = dropdowns[i];
            if (openDropdown.classList.contains('show')) {
                openDropdown.classList.remove('show');
            }
        }
    }
}

// Fixed loadSymbol
window.loadSymbol = function (sym) {
    const input = document.getElementById('symbol-input');
    const symbol = sym || input.value.trim().toUpperCase();
    if (!symbol) return;

    input.value = symbol;

    document.querySelectorAll('.wl-item').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.wl-item').forEach(item => {
        if (item.querySelector('span')?.innerText === symbol) {
            item.classList.add('active');
        }
    });

    if (typeof fetchStockData === 'function') fetchStockData(symbol);
    else console.warn("fetchStockData not found");

    // Update Anchor Button Visibility based on current aggregation
    const agg = document.querySelector('#ctrl-agg .tbtn.active')?.dataset.val || 'daily';
    const btn = document.getElementById('btn-set-anchor');
    if (btn) {
        if (agg === 'monthly') {
            btn.style.display = 'block';
        } else {
            btn.style.display = 'none';
            if (typeof isSelectingAnchor !== 'undefined' && isSelectingAnchor) toggleAnchorMode();
        }
    }
};


// ─── Metrics Update ───────────────────────────────────────────
function updateMetrics(meta) {
    const sym = meta.symbol || '—';
    document.getElementById('symbol-display').textContent = sym;

    document.getElementById('m-price-val').textContent = `₹${meta.last_price?.toLocaleString() ?? '—'}`;
    const chg = meta.price_change_pct ?? 0;
    const chgEl = document.getElementById('m-price-sub');
    chgEl.textContent = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`;
    chgEl.style.color = chg >= 0 ? '#00e396' : '#ff4976';

    const mPrice = document.getElementById('m-price');
    mPrice.className = 'metric ' + (chg >= 0 ? 'green' : 'red');

    document.getElementById('m-anchor-val').textContent = meta.anchor_date || '—';
}

// ─── Toast ────────────────────────────────────────────────────
function toast(msg) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.classList.add('show');
    setTimeout(() => el.classList.remove('show'), 2500);
}


// The main data fetcher
let fetchController = null;
let currentPocLine = null;

async function fetchStockData(sym) {
    if (sym) document.getElementById('symbol-input').value = sym;
    const symbol = document.getElementById('symbol-input').value.trim().toUpperCase();
    if (!symbol) return;

    // Abort previous fetch
    if (fetchController) fetchController.abort();
    fetchController = new AbortController();

    // Cleanly remove POC line immediately on switch
    if (currentPocLine) {
        candleSeries.removePriceLine(currentPocLine);
        currentPocLine = null;
    }

    const urlParams = new URLSearchParams(window.location.search);
    urlParams.set('symbol', symbol);
    window.history.replaceState({}, '', `${window.location.pathname}?${urlParams}`);

    const agg = document.querySelector('#ctrl-agg .tbtn.active')?.dataset.val || 'daily';

    // Show/Hide Anchor Button
    const btn = document.getElementById('btn-set-anchor');
    if (btn) {
        if (agg === 'monthly') {
            btn.style.display = 'block';
        } else {
            btn.style.display = 'none';
            if (typeof isSelectingAnchor !== 'undefined' && isSelectingAnchor) toggleAnchorMode();
        }
    }

    let lookback = 365;
    if (agg === 'weekly') lookback = 1000;
    if (agg === 'monthly') lookback = 4000;

    document.getElementById('loading').style.display = 'flex';

    try {
        const url = `/lfm/api/analysis/stock/${symbol}?lookback=${lookback}&agg=${agg}`;
        const res = await fetch(url, { signal: fetchController.signal });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Failed to load');
        }
        const data = await res.json();

        isSyncing = true; // Lock sync globally

        updateMetrics(data.meta);
        document.title = `${symbol} — Liquidity Flow`;

        // Update all series
        candleSeries.setData(data.candles);

        // Build and Set Chart Markers
        let markers = [];

        data.candles.forEach(d => {
            if (d.is_ignition) {
                markers.push({
                    time: d.time,
                    position: 'aboveBar',
                    color: '#b197fc', // Purple
                    shape: 'arrowUp',
                    text: agg === 'daily' ? `${d.ignition_score}` : ''
                });
            } else if (d.is_poc_breakout) {
                markers.push({
                    time: d.time,
                    position: 'aboveBar',
                    color: '#ffd43b', // Gold
                    shape: 'arrowUp'
                });
            } else if (d.is_poc_bounce) {
                markers.push({
                    time: d.time,
                    position: 'belowBar',
                    color: '#ffd43b', // Gold
                    shape: 'star'
                });
            } else if (d.is_coil) {
                markers.push({
                    time: d.time,
                    position: 'belowBar',
                    color: '#4dabf7', // Blue
                    shape: 'circle',
                    text: agg === 'daily' ? `${d.coil_score}` : ''
                });
            }
        });

        candleSeries.setMarkers(markers);
        davwapSeries.setData(data.davwap || []);
        cumulativeLedgerSeries.setData(data.ledger_cumulative || []);
        ledgerSeries.setData(data.ledger || []);
        volumeSeries.setData(data.volumes || []);
        mcsSeries.setData(data.mcs || []);

        // Add Volume Profile POC
        if (data.volume_profile && data.volume_profile.length > 0) {
            const pocBin = data.volume_profile.find(b => b.is_poc);
            if (pocBin) {
                const pocPrice = (pocBin.price_start + pocBin.price_end) / 2;
                currentPocLine = candleSeries.createPriceLine({
                    price: pocPrice,
                    color: '#ffd43b',
                    lineStyle: 3, // Dotted
                    lineWidth: 2,
                    axisLabelVisible: true
                });
            }
        }

        const len = data.candles.length;
        if (len > 0) {
            const lastIndex = len - 1;
            let showCount = (agg === 'daily') ? 130 : 60;
            const fromIndex = Math.max(0, lastIndex - showCount);
            const fromTime = data.candles[fromIndex].time;
            const toTime = data.candles[lastIndex].time;

            if (fromTime && toTime) {
                const range = { from: fromTime, to: toTime };
                priceChart.timeScale().setVisibleRange(range);
                ledgerChart.timeScale().setVisibleRange(range);
            }
        }

        // Finalize
        toast(`✅ ${symbol} loaded (${data.meta.anchor_status === 'CONFIRMED' ? '⚓ ' + data.meta.anchor_date : 'No Anchor'})`);

    } catch (err) {
        if (err.name === 'AbortError') return;

        // Clear data on error
        candleSeries.setData([]);
        davwapSeries.setData([]);
        cumulativeLedgerSeries.setData([]);
        ledgerSeries.setData([]);
        volumeSeries.setData([]);
        mcsSeries.setData([]);
        document.getElementById('symbol-display').textContent = '';

        toast(`❌ ${err.message}`);
        console.error(err);
    } finally {
        isSyncing = false; // Release lock
        document.getElementById('loading').style.display = 'none';
    }
}

document.getElementById('symbol-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') {
        loadSymbol();
    }
});

document.querySelector('.symbol-search button').addEventListener('click', () => {
    loadSymbol();
});

// ─── Manual Anchor Logic ──────────────────────────────────────
let isSelectingAnchor = false;

window.toggleAnchorMode = function () {
    isSelectingAnchor = !isSelectingAnchor;
    const btn = document.getElementById('btn-set-anchor');
    const chartDiv = document.getElementById('price-chart'); // Cursor on Price Chart

    if (isSelectingAnchor) {
        btn.classList.add('active');
        btn.style.color = '#00e396';
        chartDiv.style.cursor = 'crosshair';
        toast("Select a MONTH to anchor...");
    } else {
        btn.classList.remove('active');
        btn.style.color = '';
        chartDiv.style.cursor = 'default';
    }
};

async function setManualAnchor(dateInput) {
    const symbol = document.getElementById('symbol-input').value.trim().toUpperCase();

    // Handle LightweightCharts date object {year, month, day}
    let dateStr = dateInput;
    if (typeof dateInput === 'object' && dateInput !== null) {
        if (dateInput.year && dateInput.month && dateInput.day) {
            const y = dateInput.year;
            const m = String(dateInput.month).padStart(2, '0');
            const d = String(dateInput.day).padStart(2, '0');
            dateStr = `${y}-${m}-${d}`;
        }
    }

    if (!confirm(`Set Day Zero anchor to month of ${dateStr}?`)) {
        toggleAnchorMode(); // Cancel
        return;
    }

    try {
        toast(`⚓ Setting anchor for ${symbol}...`);

        const res = await fetch(`/lfm/api/analysis/stock/${symbol}/anchor/manual`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: String(dateStr) })
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: "Update failed" }));
            throw new Error(err.detail || "Update failed");
        }

        const newAnchor = await res.json();
        toast(`✅ Anchor updated: ${newAnchor.anchor_date}`);
        toggleAnchorMode(); // Reset UI
        loadSymbol(symbol); // Reload
    } catch (e) {
        toast(`❌ ${e.message}`);
        toggleAnchorMode();
    }
}

// Click Listener for Anchor Selection
priceChart.subscribeClick(param => {
    if (!isSelectingAnchor || !param.time) return;
    // param.time is YYYY-MM-DD string
    setManualAnchor(param.time);
});

// Update visibility based on Aggregation


// ─── Initial Load ─────────────────────────────────────────────
(function initLoad() {
    const urlParams = new URLSearchParams(window.location.search);
    const startSym = urlParams.get('symbol');

    // Only call loadSymbol if we actually have a symbol to avoid double fetch with the 100ms timeout
    if (startSym) {
        loadSymbol(startSym);
    } else {
        // Delay slightly for WatchlistManager
        setTimeout(() => {
            const currentSym = document.getElementById('symbol-input').value.trim();
            if (currentSym) loadSymbol(currentSym);
        }, 300);
    }
})();
