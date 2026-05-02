---
name: feature-implementation-pipeline
description: End-to-end workflow for implementing a new indicator or feature from the DivergenceEngine backend to the web UI. Use when asked to add a new metric, oscillator, or visualization to the trading dashboard.
---

# Feature Implementation Pipeline

This skill defines the multi-layer integration process for adding new indicators or features to the Liquidity Flow Monitor (LFM) system.

## 1. Backend: Indicator Computation
Add the mathematical logic to the `DivergenceEngine` pipeline.

1. **Locate Module:** Identify which module in `src/divergence_engine/` should compute the metric (e.g., `price_range.py` for range-based, `dvl_ledger.py` for delivery-based).
2. **Implement Logic:** Ensure the computation is vectorized (using `numpy` or `pandas`) and added as a column to the `df`.
3. **Orchestrate:** Ensure the module is instantiated and called within `DivergenceEngine.run()` in `src/divergence_engine/engine.py`.
4. **Column Preservation:** If the column is an intermediate value that should be visible in the final ledger, ensure it is NOT listed in the `_DROP_COLS` list in `engine.py`.

## 2. API: Data Exposure
Prepare the backend data for serialization to the frontend.

1. **Update UI_COLUMNS:** Add the exact name of the new column to the `UI_COLUMNS` list in `src/divergence_engine/chart.py`.
2. **Serialization:** `chart.py` will automatically include this column in the JSON payload sent to the UI.

## 2.5 Backend: Multi-Reason Metadata
If the feature represents a collection of reasons (e.g., accumulated rejections), use a delimited string.
1. **Accumulate:** In `check_entry` or `check_exit`, maintain a `rejections` list.
2. **Join:** Return `False, 0, {"reason": " | ".join(rejections)}`.

## 3. UI: Visualization Rendering
Register and draw the feature in the web dashboard.

1. **Register Panel:** Update `PANEL_DEFINITIONS` in `src/web/js/divergence_engine.js` with a label.
   ```javascript
   "my_feature": { label: "My New Feature Label" }
   ```
2. **Data Parsing:** Add a new array variable and parsing logic in the `buildCharts` function to extract the data from the API response.
3. **Drawing Logic:** Add a conditional block in the panel loop to define the chart series (e.g., `LineSeries`, `HistogramSeries`) and set its data.
   ```javascript
   } else if (panelKey === "my_feature") {
       var sNew = c.addSeries(LC.LineSeries, { color: "#color", lineWidth: 2 });
       sNew.setData(myFeatureArr);
       legConfig.push({ api: sNew, label: "Label", col: "my_feature", color: "#color" });
   }
   ```
4. **Sidebar Table (Annotations):** If the data should appear in the Engine State table, update `buildSidebarAnnotations`. For multi-reason strings, use a split-join formatter:
   ```javascript
   var formatted = l.entry_reason.split(" | ").join("<br>");
   ```

## 4. Verification & Cache
1. **Flush Cache:** ALWAYS flush the Redis cache after backend changes to see the new data:
   ```bash
   ./venv/bin/python scripts/flush_cache.py --all
   ```
2. **Test Logic:** Run a quick python snippet to verify the `DivergenceEngine` output:
   ```bash
   ./venv/bin/python -c "from src.divergence_engine.engine import DivergenceEngine; print(DivergenceEngine('RELIANCE').run().ledger.columns)"
   ```
3. **UI Check:** Open the browser, go to Settings -> Add Panel, and verify the new visualization appears.
