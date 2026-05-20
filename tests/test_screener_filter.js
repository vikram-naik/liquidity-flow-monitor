
/**
 * Test script to verify SelectFilter logic in isolation.
 * Mocks Ag-Grid structures to ensure doesFilterPass works as expected.
 */

class SelectFilterMock {
    constructor(params) {
        this.params = params;
        this.filterValue = '';
    }

    setFilterValue(val) {
        this.filterValue = val;
    }

    isFilterActive() {
        return this.filterValue !== '';
    }

    doesFilterPass(node) {
        // Mocking the behavior of the real SelectFilter.doesFilterPass
        // In the real version, params.node is passed. Here we pass node directly for simplicity.
        const value = this.params.valueGetter(node);
        
        if (this.filterValue === '') return true;
        if (value == null) return false;
        
        const cellValue = value.toString().toLowerCase();
        const filterValue = this.filterValue.toLowerCase();
        
        return cellValue.includes(filterValue);
    }
}

// --- Test Cases ---

const testData = [
    { data: { symbol: 'RELIANCE', signal_type: 'entry', c_up: 1, c_down: 0, max_cts: 1, min_cts: 0 } },
    { data: { symbol: 'TCS', signal_type: 'none', c_up: 0, c_down: 1, max_cts: 0, min_cts: 1 } },
    { data: { symbol: 'INFY', signal_type: 'in-trade', c_up: 0, c_down: 0, max_cts: 0, min_cts: 0 } },
    { data: { symbol: 'HDFCBANK', signal_type: 'exit', c_up: 1, c_down: 0, max_cts: 0, min_cts: 0 } }
];

// Mock ValueGetters
const stateValueGetter = (node) => node.data.signal_type;
const techValueGetter = (node) => {
    let res = [];
    if (node.data.c_up) res.push('C-UP');
    if (node.data.c_down) res.push('C-DOWN');
    if (node.data.max_cts) res.push('MAX-CTS');
    if (node.data.min_cts) res.push('MIN-CTS');
    return res.join(', ');
};

function runTests() {
    console.log("Running SelectFilter Logic Tests...\n");

    const stateFilter = new SelectFilterMock({ valueGetter: stateValueGetter });
    const techFilter = new SelectFilterMock({ valueGetter: techValueGetter });

    let passed = 0;
    let failed = 0;

    function assert(condition, message) {
        if (condition) {
            console.log(`[PASS] ${message}`);
            passed++;
        } else {
            console.error(`[FAIL] ${message}`);
            failed++;
        }
    }

    // Test 1: State Filter - ENTRY
    stateFilter.setFilterValue('entry');
    assert(stateFilter.doesFilterPass(testData[0]) === true, "RELIANCE (entry) passes 'entry' filter");
    assert(stateFilter.doesFilterPass(testData[1]) === false, "TCS (none) fails 'entry' filter");

    // Test 2: State Filter - NONE (Tech-Only)
    stateFilter.setFilterValue('none');
    assert(stateFilter.doesFilterPass(testData[1]) === true, "TCS (none) passes 'none' filter");
    assert(stateFilter.doesFilterPass(testData[0]) === false, "RELIANCE (entry) fails 'none' filter");

    // Test 3: Tech Signals - C-UP
    techFilter.setFilterValue('C-UP');
    assert(techFilter.doesFilterPass(testData[0]) === true, "RELIANCE (C-UP, MAX-CTS) passes 'C-UP' filter");
    assert(techFilter.doesFilterPass(testData[3]) === true, "HDFCBANK (C-UP) passes 'C-UP' filter");
    assert(techFilter.doesFilterPass(testData[1]) === false, "TCS (C-DOWN, MIN-CTS) fails 'C-UP' filter");

    // Test 4: Tech Signals - MAX-CTS
    techFilter.setFilterValue('MAX-CTS');
    assert(techFilter.doesFilterPass(testData[0]) === true, "RELIANCE (C-UP, MAX-CTS) passes 'MAX-CTS' filter");
    assert(techFilter.doesFilterPass(testData[2]) === false, "INFY (no tech signals) fails 'MAX-CTS' filter");

    // Test 5: Multi-signal match
    techFilter.setFilterValue('MIN-CTS');
    assert(techFilter.doesFilterPass(testData[1]) === true, "TCS (C-DOWN, MIN-CTS) passes 'MIN-CTS' filter");

    // Test 6: Empty Filter (All)
    techFilter.setFilterValue('');
    assert(techFilter.doesFilterPass(testData[2]) === true, "Empty filter passes everything");

    console.log(`\nTests Completed: ${passed} Passed, ${failed} Failed`);
    if (failed > 0) process.exit(1);
}

runTests();
