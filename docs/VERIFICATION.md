# OrderFlow – Verification Guide

## 1. Purpose

This document explains how to install, execute, test, and verify the OrderFlow Data Pipeline.

The verification process covers environment setup, sample data generation, SQLite loading, data validation, reconciliation, reporting, and performance testing.

## 2. Environment Requirements

| Requirement | Specification |
|---|---|
| Programming Language | Python 3.9+ |
| Database | SQLite |
| Operating System | Windows / Linux |
| Version Control | Git |
| Testing Framework | Python unittest |
| Input Formats | CSV and JSON Lines |

SQLite is included with Python's standard library.

## 3. Clone the Repository

Execute the following commands:

```bash
git clone https://github.com/kgmbharadwaj/orderflow-data-pipeline.git

cd orderflow-data-pipeline
```

## 4. Environment Setup

Create a virtual environment:

```bash
python -m venv venv
```

Activate it on Windows:

```powershell
venv\Scripts\activate
```

Install project dependencies:

```bash
pip install -r requirements.txt
```

## 5. Run Unit Tests

Execute:

```bash
python -m unittest discover -s tests -t . -v
```

**Expected Result:**

- 45 unit tests should execute.
- All tests should pass.
- No unexpected errors should occur.

**What is verified?**

- Data validation rules.
- Customer and product references.
- Currency and order status validation.
- Timestamp normalization.
- Duplicate event detection.
- Quantity and price boundaries.
- Order update validation.

Latest documented test result: 45 tests passed.

## 6. Generate Sample Data

Generate 100,000 sample order records:

```bash
python -m pipeline generate --out data/generated --rows 100000 --seed 42
```

**Expected Result:**

The generator creates sample input datasets, including:

- customers.csv
- products.csv
- orders.csv

It also generates reconciliation support files:

- orders_labels.csv
- expected_summary.json

The generated files are stored in `data/generated/`.

## 7. Load Data into SQLite

Execute the following command:

```bash
python -m pipeline load --db wh.db --customers data/generated/customers.csv --products data/generated/products.csv --orders data/generated/orders.csv
```

**Expected Result:**

- Customer reference records are loaded.
- Product reference records are loaded.
- Order events are processed.
- Valid records are stored in SQLite.
- Invalid records are moved to the rejects table.
- Execution metrics are recorded.

The pipeline uses chunk-based transactions to control memory consumption.

## 8. Run Reconciliation

Execute:

```bash
python -m pipeline reconcile --db wh.db --orders data/generated/orders.csv --expected data/generated/expected_summary.json --labels data/generated/orders_labels.csv
```

**Expected Result:**

The reconciliation process compares actual database results with generated expectations.

It checks:

- Total source records.
- Valid and rejected records.
- Distinct valid orders.
- Order line totals.
- Duplicate records.
- Orphan customer and product references.
- Per-line outcome mismatches.

**Expected status:** PASS when all reconciliation checks match.

## 9. Generate Reports

Execute:

```bash
python -m pipeline report --db wh.db
```

**Expected Result:**

The reporting module generates reports using SQLite views.

The available reporting views include:

- `v_daily_sales`
- `v_order_status`
- `v_reject_summary`

## 10. Run Performance Benchmark

Execute:

```bash
python -m pipeline benchmark --rows 100000
```

The benchmark measures processing performance and peak memory consumption.

### Previously Observed Benchmark Results

| Metric | 100K rows | 500K rows |
|---|---:|---:|
| Rows processed | 100,000 | 500,000 |
| Rows loaded | 95,148 | 475,733 |
| Rows rejected | 4,852 | 24,267 |
| Rows per second | 10,831 | 10,620 |
| Peak RSS | 47 MB | 56.7 MB |
| Reconciliation | PASS | PASS |

Note: These are previously documented local benchmark results. Actual measurements may vary depending on hardware and environment.

## 11. Data Integrity Verification

The following checks are used to verify data integrity:

| Check | Expected Behaviour |
|---|---|
| Duplicate events | Duplicate events are identified |
| Invalid records | Records are quarantined |
| Unknown customers | Referential integrity is enforced |
| Unknown products | Referential integrity is enforced |
| Late-arriving events | Historical events are retained |
| Current order state | Latest valid event determines current state |
| Money calculations | Monetary values are stored in integer cents |
| Resumable loading | Completed chunks are checkpointed |

## 12. Error Handling and Recovery

The pipeline supports:

- Row-level validation and quarantine.
- File-level schema validation.
- Deterministic identifiers for events and rejects.
- SHA-256-based file identification.
- Resumable chunk-based loading.
- Duplicate prevention during reruns.

Malformed records are quarantined rather than stopping the entire load.

Files missing required columns fail before their records are written.

### 13. Known Limitations

1. SQLite supports a single writer at a time, which limits concurrent write operations.
2. JSON input support is focused on JSON Lines rather than large JSON arrays.
3. Processing is sequential and single-process; distributed processing is not currently implemented.
4. Order status transition rules are not enforced.
5. Timestamps are normalized to seconds, so sub-second precision is not retained.
6. CSV source-line tracking assumes one physical line per record. Quoted fields containing embedded newlines may affect physical line-number tracking.
7. Naive timestamps are interpreted as UTC.
8. During resumed processing, execution counters describe the resumed portion rather than necessarily representing the entire original run.
9. The pipeline does not currently enforce all possible business-specific order lifecycle rules.

### 14. Conclusion

The verification process demonstrates that OrderFlow can generate, ingest, validate, and reconcile large datasets using a memory-conscious processing approach.

The project provides:

- Incremental CSV and JSON Lines ingestion.
- Chunk-based database transactions.
- Schema and business-rule validation.
- Rejection handling and source traceability.
- File-level idempotency and checkpoint-based recovery.
- Order history and current-state management.
- Data reconciliation and analytical reporting.
- Automated unit testing and performance benchmarking.

The documented tests and benchmark results represent local execution measurements. Actual performance may vary depending on hardware and environment.

OrderFlow is designed to be reproducible locally, testable, and understandable through its source code and technical documentation.