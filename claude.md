# Project: Liquidity Flow Monitor

## Core Directives for Claude CLI
1. **Token Efficiency First:** Assume strict context limits. Provide concise, direct answers without unnecessary pleasantries or filler text.
2. **Context Resets:** Expect frequent `/context clear` commands. Always rely on `handoff.md` (if it exists) to establish the current state before generating new code.
3. **Data Handling:** NEVER attempt to read or ingest full `.csv`, `.json`, or `.parquet` files. If data structure context is needed, ask the user to provide a schema snippet or use bash commands to output only the first 3-5 rows (`head -n 5 data.csv`).

## Architecture & Style Guidelines
* **Strict Modularity:** Code must be highly modular. Isolate ingestion, transformation, feature engineering, and validation steps into separate files.
* **Factory Patterns:** Utilize factory patterns for data connectors and transformers to keep the main execution logic clean and extendable.
* **Type Hinting & Docstrings:** All Python functions must include strict type hints and concise PEP 257 docstrings to ensure clear handoffs between modules.
* **Error Handling:** Fail early and loudly. Implement robust logging rather than silent `try/except` passes, especially during data transformation steps.

## Workflow Commands
* **Handoff Generation:** When the user types "Generate handoff", create a concise summary of the current working state, unresolved bugs, and the exact next step, and output it to a file named `handoff.md`.
* **venv:** `source venv/bin/activate`
* **web-app start:** `uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload`
* **web-app URL:** `http://localhost:8000/de/dashboard/{symbol}`