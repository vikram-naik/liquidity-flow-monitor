# Treasury Data Dictionary

> [!WARNING]
> **Data Integrity Warning**
> - The table `treasury_interest_delta` is currently **UNUSED** and not populated by any active data ingestion process.
> - The column `maturing_5yr` in `treasury_debt_profile` is currently **HARD-WIRED to 0.0** in the ingestion agent (`src/agents/treasury_agent.py`) and does not reflect actual data.

This document details the schema, usage, and data sources for the `treasury_*` tables in `liquidity_monitor.db`.

## 1. `treasury_liquidity`
**Description**: Tracks key liquidity metrics including the Treasury General Account (TGA) balance, Reverse Repo (RRP) balance, and US Sovereign CDS spread.

**Schema**:
- `record_date` (DATE): The date of the record.
- `tga_balance` (REAL): TGA Closing Balance (in Billions).
- `rrp_balance` (REAL): Overnight RRP volume (in Billions).
- `cds_spread` (REAL): US 5Y CDS Spread.

**Population Source**:
- **TGA**: `treasury_agent.py` -> `poll_liquidity`. Fetched from [FiscalData API](https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/operating-cash-balance) (`v1/accounting/dts/operating_cash_balance`).
- **RRP**: `treasury_agent.py` -> `poll_liquidity`. Sourced primarily from `yield_logs` table (populated by `flow_agent.py` via NY Fed/St. Louis Fed) as a fallback/merge.
- **CDS**: `treasury_agent.py` -> `scrape_cds_spread`. Scraped from [WorldGovernmentBonds](https://www.worldgovernmentbonds.com/sovereign-cds/).

**Usage**:
- Used in `analytics.py` for liquidity analysis.
- Dashboard visuals (implied by usage in `analytics.py`).
- API endpoint: `/upload/treasury-liquidity` (`api/main.py`).

---

## 2. `treasury_debt_profile`
**Description**: Tracks the maturity profile of US debt, specifically focusing on short-term rolling debt.

**Schema**:
- `record_date` (DATE): The date of the record.
- `maturing_1yr` (REAL): Debt maturing within 1 year (calculated as Bills + Floating Rate Notes).
- `maturing_5yr` (REAL): Debt maturing within 5 years. **NOTE: Currently always populated as 0.0.**
- `total_debt` (REAL): Total Public Debt Outstanding.

**Population Source**:
- `treasury_agent.py` -> `poll_debt_profile`. Fetched from [FiscalData API](https://fiscaldata.treasury.gov/datasets/monthly-statement-public-debt/summary-of-treasury-securities-outstanding) (`v1/debt/mspd/mspd_table_1`).

**Usage**:
- `analytics.py` queries this table.
- API endpoint: `/upload/treasury-debt-profile` (`api/main.py`).

---

## 3. `treasury_buybacks`
**Description**: Records Treasury buyback operations.

**Schema**:
- `record_date` (DATE): Date of the operation.
- `total_offered` (REAL): Total par amount offered.
- `total_accepted` (REAL): Total par amount accepted.
- `security_type` (TEXT): Type of security (e.g., Nominal Coupon).
- `maturity_bucket` (TEXT): Maturity bucket (e.g., 20Y-30Y).

**Population Source**:
- `treasury_agent.py` -> `poll_buybacks`. Fetched from [FiscalData API](https://fiscaldata.treasury.gov/datasets/treasury-buybacks-historical/treasury-buyback-historical-operations) (`v1/accounting/od/buybacks_operations`).

**Usage**:
- `analytics.py` queries this table.
- API endpoint: `/upload/treasury-buybacks` (`api/main.py`).

---

## 4. `treasury_daily_debt_flows`
**Description**: Tracks daily issuance and redemption of specific securities (primarily Bills) to estimate real-time debt walls.

**Schema**:
- `record_date` (DATE): Date of the transaction.
- `security_type` (TEXT): Type of security (e.g., Bills).
- `transaction_type` (TEXT): 'Issues' or 'Redemptions'.
- `amount_mil` (REAL): Amount in Millions.

**Population Source**:
- `treasury_agent.py` -> `poll_daily_debt_flows`. Fetched from [FiscalData API](https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/public-debt-transactions) (`v1/accounting/dts/public_debt_transactions`).

**Usage**:
- `analytics.py` queries this table.
- API endpoint: `/upload/treasury-daily-debt-flows` (`api/main.py`).

---

## 5. `treasury_auctions`
**Description**: Stores detailed results from Treasury auctions.

**Schema**:
- `record_date` (DATE): Record date of the auction data.
- `auction_date` (DATE): Actual date of the auction.
- `security_type` (TEXT): Term (e.g., 10-Year).
- `maturity` (TEXT): Security Type (e.g., Note, Bond, Bill).
- `bid_to_cover` (REAL): Ratio of bids received to bids accepted.
- `tail_bps` (REAL): Difference between High Yield and Median Yield (calculated).
- `high_yield` (REAL): Highest accepted yield.
- `offering_amount` (REAL): Amount offered.
- `total_accepted` (REAL): Amount accepted.
- `primary_dealer_accepted` (REAL), `direct_bidder_accepted` (REAL), `indirect_bidder_accepted` (REAL), `soma_accepted` (REAL), `noncomp_accepted` (REAL): Breakdown of acceptances.
- `is_new_issuance` (BOOLEAN): Flag if it's a new issuance (calculated based on date).

**Population Source**:
- `treasury_agent.py` -> `poll_auctions`. Fetched from [FiscalData API](https://fiscaldata.treasury.gov/datasets/treasury-auctions/treasury-auctions-query) (`v1/accounting/od/auctions_query`).

**Usage**:
- `analytics.py` queries this table.
- API endpoint: `/upload/treasury-auctions` (`api/main.py`).

---

## 6. `treasury_maturity_schedule`
**Description**: A forward-looking schedule of debt maturities.

**Schema**:
- `record_date` (DATE): The baseline date when this schedule was captured.
- `maturity_date` (DATE): When the debt matures.
- `security_class` (TEXT): Description of the security.
- `amount_mil` (REAL): Outstanding amount in Millions.
- `issue_date` (DATE): Original issue date.

**Population Source**:
- `treasury_agent.py` -> `poll_maturity_schedule`. Fetched from [FiscalData API](https://fiscaldata.treasury.gov/datasets/monthly-statement-public-debt/detailed-solvency-and-trust-fund-data) (`v1/debt/mspd/mspd_table_3`). Uses the latest available record date to fetch all future maturities.

**Usage**:
- `analytics.py` queries this table.
- API endpoint: `/upload/treasury-maturity-schedule` (`api/main.py`).

---

## 7. `treasury_avg_interest_rates`
**Description**: Historical average interest rates on public debt.

**Schema**:
- `record_date` (DATE): Date of the record.
- `security_desc` (TEXT): Description (e.g., Marketable).
- `avg_interest_rate_amt` (REAL): Average interest rate.

**Population Source**:
- `treasury_agent.py` -> `poll_avg_interest_rates`. Fetched from [FiscalData API](https://fiscaldata.treasury.gov/datasets/average-interest-rates-treasury-securities/average-interest-rates-on-u-s-treasury-securities) (`v2/accounting/od/avg_interest_rates`).

**Usage**:
- `analytics.py` queries this table.
- API endpoint: `/upload/treasury-avg-interest-rates` (`api/main.py`).

---

## 8. `treasury_issuance_plan`
**Description**: Tracks upcoming issuance plans (derived from auction announcements).

**Schema**:
- `auction_date` (DATE): Date of the auction.
- `security_term` (TEXT): Term of the security.
- `offering_amount` (REAL): Offering amount.
- `is_new_issuance` (BOOLEAN): Flag for new issuance.

**Population Source**:
- `treasury_agent.py` -> `save_issuance_plan`. Populated using data fetched in `poll_auctions` (reusing the auctions data).

**Usage**:
- `analytics.py` queries this table.
- API endpoint: `/upload/treasury-issuance-plan` (`api/main.py`).

---

## 9. `treasury_interest_delta`
**Description**: Intended to track changes in interest rates/costs?

**Schema**:
- `record_date` (DATE)
- `security_class` (TEXT)
- `historical_rate` (REAL)
- `new_rate` (REAL)
- `delta_bps` (REAL)

**Population Source**:
- **None**. This table is defined in `database.py` but **not populated** by `treasury_agent.py`. It appears to be unused or a placeholder for future functionality.

**Usage**:
- No active usage found in `treasury_agent.py` or `analytics.py`.
