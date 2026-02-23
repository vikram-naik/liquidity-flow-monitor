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
    // Localization move to individual charts to allow mixed formats
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
    if (val === undefined || val === null) return '';
    const abs = Math.abs(val);
    const sign = val < 0 ? '-' : '';
    if (abs >= 10000000) return sign + (abs / 10000000).toFixed(1) + 'Cr';
    if (abs >= 100000) return sign + (abs / 100000).toFixed(1) + 'L';
    if (abs >= 1000) return sign + (abs / 1000).toFixed(1) + 'K';

    // For small values (like Ledger/MCS), limit to 2 decimals and strip trailing zeros
    let formatted = val.toFixed(2);
    if (formatted.includes('.')) {
        formatted = formatted.replace(/\.?0+$/, '');
    }
    return formatted;
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
const candleSeries = priceChart.addCandlestickSeries({
    ...candleOpts,
    priceFormat: { type: 'price', precision: 2, minMove: 0.05 },
});
const davwapSeries = priceChart.addLineSeries({
    color: '#2962FF',
    lineWidth: 2,
    lastValueVisible: true,
    priceLineVisible: false,
    priceFormat: { type: 'price', precision: 2, minMove: 0.05 },
});
const volumeSeries = priceChart.addHistogramSeries({
    priceFormat: {
        type: 'custom',
        minMove: 1,
        formatter: (val) => abbrev(val)
    },
    priceScaleId: 'volume-overlay',
});
priceChart.priceScale('volume-overlay').applyOptions({
    scaleMargins: {
        top: 0.8,
        bottom: 0,
    },
    visible: false,
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
    localization: { priceFormatter: abbrev }
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

    async addToList(symbol, listId) {
        if (!listId) return;
        if (!symbol) return;

        try {
            const res = await fetch(`/lfm/api/watchlists/${listId}/items`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol })
            });
            if (res.ok) {
                if (listId == this.activeListId) {
                    this.fetchItems(this.activeListId);
                }
                const targetList = this.lists.find(l => l.id == listId);
                this.showToast(`Copied ${symbol} to ${targetList ? targetList.name : 'Watchlist'}`);
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

window.toggleCopyToMenu = function (event) {
    if (event) event.stopPropagation();
    const menu = document.getElementById('copy-to-menu');
    if (!menu) return;

    const symbol = document.getElementById('symbol-input').value;
    if (!symbol) {
        menu.innerHTML = '<div style="padding: 10px; font-size: 12px; color: var(--text-2);">No symbol selected</div>';
    } else {
        const lists = watchlistManager.lists;
        if (lists.length === 0) {
            menu.innerHTML = '<div style="padding: 10px; font-size: 12px; color: var(--text-2);">No watchlists</div>';
        } else {
            let html = `<div class="menu-label" style="padding: 8px 14px; font-size: 11px; color: var(--text-2); text-transform: uppercase;">Copy ${symbol} to:</div><div class="menu-divider"></div>`;
            lists.forEach(l => {
                const isActive = l.id == watchlistManager.activeListId;
                html += `<button onclick="watchlistManager.addToList('${symbol}', ${l.id}); document.getElementById('copy-to-menu').classList.remove('show');"
                    style="${isActive ? 'color: var(--text-2);' : ''}">
                    ${l.name} ${isActive ? '(Current)' : ''}
                </button>`;
            });
            menu.innerHTML = html;
        }
    }

    // close other menus
    const wlMenu = document.getElementById('wl-menu');
    if (wlMenu && wlMenu.classList.contains('show')) wlMenu.classList.remove('show');

    menu.classList.toggle('show');
};

// Close dropdowns when clicking outside
window.onclick = function (event) {
    if (!event.target.closest('.icon-btn') && !event.target.closest('.text-btn') && !event.target.closest('.dropdown-content')) {
        const dropdowns = document.getElementsByClassName("dropdown-content");
        for (let i = 0; i < dropdowns.length; i++) {
            const openDropdown = dropdowns[i];
            if (openDropdown.classList.contains('show')) {
                openDropdown.classList.remove('show');
            }
        }
    }
}

window.showMethodology = function () {
    document.getElementById('charts-view').style.display = 'none';
    document.getElementById('methodology-view').style.display = 'block';

    // Clear url symbol so it feels like a standalone page
    const urlParams = new URLSearchParams(window.location.search);
    urlParams.delete('symbol');
    window.history.replaceState({}, '', `${window.location.pathname}`);
    document.title = "LFM Methodology";
};

window.showCharts = function () {
    const chartsView = document.getElementById('charts-view');
    const methodologyView = document.getElementById('methodology-view');
    if (chartsView) chartsView.style.display = 'flex';
    if (methodologyView) methodologyView.style.display = 'none';
};

// Fixed loadSymbol
window.loadSymbol = function (sym) {
    showCharts();
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
    const btnSet = document.getElementById('btn-set-anchor');
    const btnAuto = document.getElementById('btn-auto-anchor');
    if (agg === 'daily') {
        if (btnSet) btnSet.style.display = 'block';
        if (btnAuto) btnAuto.style.display = 'block';
    } else {
        if (btnSet) btnSet.style.display = 'none';
        if (btnAuto) btnAuto.style.display = 'none';
        if (typeof isSelectingAnchor !== 'undefined' && isSelectingAnchor) toggleAnchorMode();
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

    // Anchor Warning Logic
    const warningEl = document.getElementById('m-anchor-warning');
    if (warningEl) {
        if (meta.anchor_type === 'FALLBACK_MIN') {
            warningEl.style.display = 'inline-block';
            warningEl.title = 'Warning: Low confidence anchor. System fell back to 2-year absolute low.';
        } else if (meta.anchor_type === 'MANUAL') {
            warningEl.style.display = 'inline-block';
            warningEl.title = 'Manual Anchor Override';
            warningEl.textContent = '⚙️'; // Gear icon for manual
            warningEl.style.color = '#00e396';
        } else {
            warningEl.style.display = 'none';
        }
    }

    // Trend Intensity Tilt (0-90)
    const lAngle = meta.ledger_angle;
    const mAngle = meta.mcs_angle;
    const lVelocity = meta.ledger_velocity;

    const lEl = document.getElementById('m-ledger-tilt-val');
    const mEl = document.getElementById('m-mcs-tilt-val');
    const lVEl = document.getElementById('m-ledger-velocity-val');
    const lBadge = document.getElementById('badge-ledger');
    const mBadge = document.getElementById('badge-mcs');

    // Threshold-based badges (High >= 45)
    if (lAngle !== null && lAngle !== undefined) {
        lEl.innerHTML = `${lAngle}&deg; <span style="font-size:10px; opacity:0.8;">↗</span>`;
        if (lVEl) {
            lVEl.textContent = lVelocity || '';
            // Color code velocity
            if (lVelocity === 'Accelerating') lVEl.style.color = '#00e396';
            else if (lVelocity === 'Weakening') lVEl.style.color = '#ffb01f';
            else if (lVelocity === 'Reversing') lVEl.style.color = '#ff4976';
            else lVEl.style.color = '#6e7399';
        }
        if (lAngle >= 45) {
            lBadge.textContent = 'H';
            lBadge.className = 'i-badge high';
        } else {
            lBadge.textContent = 'L';
            lBadge.className = 'i-badge low';
        }
    } else {
        lEl.textContent = '—';
        if (lVEl) lVEl.textContent = '';
        lBadge.textContent = '—';
        lBadge.className = 'i-badge';
    }

    if (mAngle !== null && mAngle !== undefined) {
        mEl.innerHTML = `${mAngle}&deg; <span style="font-size:10px; opacity:0.8;">↗</span>`;
        if (mAngle >= 45) {
            mBadge.textContent = 'H';
            mBadge.className = 'i-badge high';
        } else {
            mBadge.textContent = 'L';
            mBadge.className = 'i-badge low';
        }
    } else {
        mEl.textContent = '—';
        mBadge.textContent = '—';
        mBadge.className = 'i-badge';
    }
}

// ─── Intensity Guide Modal ────────────────────────────
function openIntensityGuide() {
    const modal = document.getElementById('intensity-modal');
    if (modal) modal.style.display = 'flex';
}

// ─── Help Guide Modal ────────────────────────────
const helpContent = {
    davwap: {
        title: "DAVWAP (Delivery Anchored VWAP)",
        body: `
            <div class="guide-section">
                <h4>What it is</h4>
                <p>The Delivery Anchored VWAP is the "Heart Line" of a cycle. It's the volume-weighted average price of all <em>delivery</em> shares traded since the Day Zero anchor.</p>
            </div>
            <div class="guide-section">
                <h4>Interpretation</h4>
                <p>Price trading above a rising DAVWAP indicates healthy institutional support and cost-basis defense. Price breaking decisively below a rising DAVWAP signals distribution and cycle failure.</p>
            </div>
        `
    },
    ledger: {
        title: "Momentum Ledger (DVL)",
        body: `
            <div class="guide-section">
                <h4>What it is</h4>
                <p>Measures the net intent behind institutional delivery volume. It filters out speculative volume to focus on "Quality" volume.</p>
            </div>
            <div class="guide-section">
                <h4>Calculation</h4>
                <p><code>DVL = Cumulative Sum of (MFM * Delivery Volume)</code></p>
                <p><strong>MFM (Money Flow Multiplier):</strong> Determined by the close's position within the high-low range. Ranges from -1 (selling at lows) to +1 (buying into highs).</p>
            </div>
            <div class="guide-section">
                <h4>Interpretation</h4>
                <p>A rising ledger signifies active <strong>Accumulation</strong>. A falling ledger indicates <strong>Distribution</strong> (selling pressure). "Divergence" occurs when price rises while the ledger falls, indicating fragile momentum.</p>
            </div>
        `
    },
    mcs: {
        title: "MCS (Money Capacity Score)",
        body: `
            <div class="guide-section">
                <h4>What it is</h4>
                <p>Measures "Absorption" capacity—how efficiently the price is absorbing institutional volume spikes.</p>
            </div>
            <div class="guide-section">
                <h4>Calculation</h4>
                <p>Measured as a 30-day Pearson correlation between Typical Price and Relative Delivery Volume (RDV).</p>
            </div>
            <div class="guide-section">
                <h4>Interpretation</h4>
                <p><strong>Positive (0 to 1):</strong> High Capacity. Price and volume are moving together, indicating strong trend participation.</p>
                <p><strong>Negative (-1 to 0):</strong> Low Capacity/Absorption. Volume is spiking on price drops or stalls, indicating high overhead supply.</p>
            </div>
        `
    },
    anchor: {
        title: "Day Zero Anchor",
        body: `
            <div class="guide-section">
                <h4>What it is</h4>
                <p>The "Start Point" for all cumulative flow calculations, including the <strong>Momentum Ledger</strong> and <strong>DAVWAP</strong>.</p>
            </div>
            <div class="guide-section">
                <h4>Calculation (Automatic)</h4>
                <p>The system uses a <strong>Volume-Confirmed Volatility Pivot</strong> algorithm over a 2-year (104 week) lookback:</p>
                <ul>
                    <li><strong>Volatility Check:</strong> Scans for a weekly breakout above <code>Recent Low + (3 * 10-week ATR)</code>.</li>
                    <li><strong>Liquidity Check:</strong> Requires weekly delivery volume to exceed its 10-week moving average by at least 1.5x.</li>
                    <li><strong>Anchor Score:</strong> Evaluates all valid pivots based on volume expansion and base tightness, selecting the strongest structural pivot.</li>
                    <li><strong>Precision:</strong> Pinpoints the exact daily date of the structural base low preceding the breakout.</li>
                </ul>
            </div>
            <div class="guide-section">
                <h4>Anchor Warnings ⚠️</h4>
                <p>If you see a warning icon next to the anchor date, it means the system could not find a confirmed structural pivot and has fallen back to the absolute 2-year low. This is a low-confidence anchor and you may want to set it manually.</p>
            </div>
            <div class="guide-section">
                <h4>How to set it</h4>
                <p><strong>Automatic:</strong> The system identifies the major structural low within the last few years using the logic above.</p>
                <p><strong>Manual:</strong> Switch to <strong>Daily (D)</strong> view, click the ⚓ icon, and then click on a specific candle in the chart. This allows you to set the anchor to an exact timestamp.</p>
            </div>
        `
    },
    ignition: {
        title: "Ignition Marker (Breakout)",
        body: `
            <div class="guide-section">
                <h4>What it is</h4>
                <p>The Ignition marker isolates a single day of explosive, structurally significant markup initiated near value. It is represented by a <strong>Purple Up-Arrow</strong> over the candle.</p>
            </div>
            <div class="guide-section">
                <h4>Triggers & Scoring (0-100)</h4>
                <ul>
                    <li><strong>Expansion (30 pts):</strong> The candle's net expansion must inherently be huge (<code>>= 0.8 * Average True Range</code>).</li>
                    <li><strong>Volume (30 pts):</strong> Delivery volume must significantly exceed the 10-day moving average.</li>
                    <li><strong>Proximity (20 pts):</strong> The move must have <strong>originated</strong> strictly within <code>1.0 ATR</code> of the DAVWAP.</li>
                    <li><strong>Ledger (20 pts):</strong> The 5-day Momentum Ledger slope must be strongly accelerating.</li>
                </ul>
                <p>A score >= 50 triggers the marker.</p>
            </div>
        `
    },
    coil: {
        title: "Coil Marker (Compression)",
        body: `
            <div class="guide-section">
                <h4>What it is</h4>
                <p>The Coil marker highlights extreme volatility compression near value, often preceding an explosive move. It is represented by a <strong>Blue Circle</strong> beneath the candle.</p>
            </div>
            <div class="guide-section">
                <h4>Triggers & Scoring (0-100)</h4>
                <ul>
                    <li><strong>Geometry (30 pts):</strong> The candle's Total Range and Body size must be drastically smaller than the 50-day ATR (<code>Range < 0.8 ATR</code> and <code>Body < 0.4 ATR</code>).</li>
                    <li><strong>Dryness (30 pts):</strong> Institutional volume is drying up (well below average), indicating supply exhaustion.</li>
                    <li><strong>Proximity (20 pts):</strong> The candle is occurring very close to the DAVWAP (within <code>1.0 ATR</code>).</li>
                    <li><strong>Ledger (20 pts):</strong> Despite the dryness, the underlying Momentum Ledger remains positive or stable.</li>
                </ul>
                <p>A score >= 50 triggers the marker.</p>
            </div>
        `
    },
    grind: {
        title: "Grind Marker (Hidden Accumulation)",
        body: `
            <div class="guide-section">
                <h4>What it is</h4>
                <p>The Grind marker, or "Composite Ignition", isolates a 3-day sequence of "slow grind" upward breakouts that fail standard single-day Ignition criteria, but mathematically achieve a commanding structural breakaway collectively.</p>
            </div>
            <div class="guide-section">
                <h4>Progressive Triggers (G1 &rarr; G2 &rarr; G3)</h4>
                <ul>
                    <li><strong>G1 (Initiation):</strong> Day 1 begins a move from within <code>1.0 ATR</code> of DAVWAP, expanding initially at least <code>0.5 ATR</code>.</li>
                    <li><strong>G2 (Continuation):</strong> Day 2 closes higher, and cumulative expansion from G1 origin exceeds <code>0.8 ATR</code>.</li>
                    <li><strong>G3 (Completion):</strong> Day 3 closes higher still, pushing the cumulative expansion from G1 origin beyond <code>1.2 ATR</code>. The 5-Day Momentum Ledger slope must be actively rising. Additionally, the Money Capacity Score (MCS) must be tracking strictly positively, or greater than Day 0 (the day prior to G1 initiation).</li>
                </ul>
            </div>
            <div class="guide-section">
                <h4>Ghosting Mechanic</h4>
                <p>The system gives you a live edge. If today acts like a strong Day 1, you will see a <strong>G1</strong> badge immediately. However, if this sequence fails to mature into a full 3-day Grind over the next few days, the isolated <strong>G1</strong> marker is retroactively "ghosted" (erased) to keep the historical chart pristine.</p>
            </div>
        `
    },
    price: {
        title: "Price & Delivery Volume",
        body: `
            <div class="guide-section">
                <h4>Candlestick Chart</h4>
                <p>Standard OHLC (Open, High, Low, Close) candles. White candles represent positive closes, while darker candles represent negative closes.</p>
            </div>
            <div class="guide-section">
                <h4>Delivery Quantity (Vertical Bars)</h4>
                <p>Unlike standard volume charts that show all trades, these bars represent the <strong>Delivery Quantity</strong>—stocks actually transferred between accounts. This is the "Conviction" volume that institutional players use for long-term positions.</p>
            </div>
            <div class="guide-section">
                <h4>Color Coding</h4>
                <ul>
                    <li><strong>Candles:</strong> Colored based on the Open-to-Close relationship. Red if Close < Open, Green if Close >= Open.</li>
                    <li><strong>Volume Bars:</strong> Colored based on the <strong>Money Flow Multiplier (MFM)</strong>, not the daily price change.</li>
                </ul>
            </div>
            <div class="guide-section">
                <h4>Understanding Volume Colors (Hidden Accumulation)</h4>
                <p>It is entirely possible (and significant) to see a <strong><span style="color: #ef5350;">Red Candle</span></strong> paired with a <strong><span style="color: #26a69a;">Green Volume Bar</span></strong>.</p>
                <p>The MFM measures intraday control. If a stock gaps down and drops hard, but buyers step in aggressively to push the price back up so it closes in the upper half of its daily range, the MFM is positive. This prints a green volume bar, indicating active <strong>Absorption/Accumulation</strong> despite the red price candle.</p>
            </div>
        `
    }
};

function openHelp(metricId) {
    const modal = document.getElementById('help-modal');
    const content = helpContent[metricId];
    if (!modal || !content) return;

    document.getElementById('help-title').textContent = content.title;
    document.getElementById('help-body').innerHTML = content.body;
    modal.style.display = 'flex';
}

function closeHelp(event) {
    const modal = document.getElementById('help-modal');
    if (modal) modal.style.display = 'none';
}

function closeIntensityGuide(event) {
    const modal = document.getElementById('intensity-modal');
    if (modal) modal.style.display = 'none';
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
    const btnSet = document.getElementById('btn-set-anchor');
    const btnAuto = document.getElementById('btn-auto-anchor');
    if (agg === 'daily') {
        if (btnSet) btnSet.style.display = 'block';
        if (btnAuto) btnAuto.style.display = 'block';
    } else {
        if (btnSet) btnSet.style.display = 'none';
        if (btnAuto) btnAuto.style.display = 'none';
        if (typeof isSelectingAnchor !== 'undefined' && isSelectingAnchor) toggleAnchorMode();
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
            }
            if (d.is_coil) {
                markers.push({
                    time: d.time,
                    position: 'belowBar',
                    color: '#4dabf7', // Blue
                    shape: 'circle',
                    text: agg === 'daily' ? `${d.coil_score}` : ''
                });
            }
            if (d.grind_level) {
                markers.push({
                    time: d.time,
                    position: 'belowBar',
                    color: '#fcc419', // Gold
                    shape: 'arrowUp',
                    text: agg === 'daily' ? `G${d.grind_level}` : ''
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
        toast("Select a DATE/CANDLE to anchor...");
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

    if (!confirm(`Set Day Zero anchor to ${dateStr}?`)) {
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

async function setAutoAnchor() {
    const symbol = document.getElementById('symbol-input').value.trim().toUpperCase();

    if (!confirm(`Force a recalculation of the Auto-Anchor for ${symbol}?\nThis will clear any manual overrides.`)) {
        return;
    }

    try {
        toast(`🔄 Calculating optimal anchor for ${symbol}...`);

        const res = await fetch(`/lfm/api/analysis/stock/${symbol}/anchor/auto`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: "Auto-anchor calculation failed" }));
            throw new Error(err.detail || "Auto-anchor calculation failed");
        }

        const newAnchor = await res.json();
        toast(`✅ Auto-Anchor found: ${newAnchor.anchor_date}`);
        loadSymbol(symbol); // Reload
    } catch (e) {
        toast(`❌ ${e.message}`);
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
