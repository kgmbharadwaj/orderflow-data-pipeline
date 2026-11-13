# OrderFlow – Implementation Notes

## 1. Project Overview

OrderFlow is a Python-based data engineering pipeline designed to process large CSV and JSON Lines datasets without loading the entire input into memory.

The project uses streaming readers, chunk-based database transactions, data validation, SQLite storage, rejection handling, and reconciliation to provide reliable data processing.

## 2. Implementation Responsibilities

The implementation is organized into independent modules to improve maintainability, testing, and separation of concerns.

| Module | Implementation Responsibility |
|---|---|
| `cli.py` | Provides command-line operations for generating data, benchmarking, loading, reconciliation, and reporting |
| `config.py` | Manages configurable pipeline settings and parameters |
| `db.py` | Handles SQLite database connections and database operations |
| `generator.py` | Generates reproducible synthetic datasets |
| `readers.py` | Reads CSV and JSON Lines input incrementally |
| `schema.py` | Defines schemas, validation rules, and data-quality checks |
| `loader.py` | Processes records in chunks and manages database loading |
| `reconcile.py` | Compares actual processing outcomes with expected results |
| `reports.py` | Generates operational and analytical reports |
| `__main__.py` | Provides the module execution entry point |

## 3. Data Processing Workflow

The pipeline follows these processing stages:

1. Generate or receive source datasets.
2. Read input records incrementally.
3. Validate required fields, data types, and business rules.
4. Check customer and product references.
5. Separate valid and invalid records.
6. Load valid events into SQLite using chunk-based transactions.
7. Store rejected records in a quarantine table.
8. Maintain order history and current order state.
9. Reconcile actual results against expected results.
10. Generate reports and execution metrics.

## 4. Important Technical Implementations

### 4.1 Streaming and Chunk-Based Processing

The pipeline reads records incrementally instead of loading the entire dataset into memory.

Records are processed in configurable chunks, and database transactions are committed after each chunk.

**Why was this approach selected?**

It helps maintain bounded memory usage and supports processing large input files on systems with limited resources.

### 4.2 Data Validation

The pipeline validates incoming order records using defined schema and business rules.

Validation includes:

- Required field checks.
- Identifier format validation.
- Timestamp validation and normalization.
- Quantity and price validation.
- Currency and order status validation.
- Customer and product reference validation.

Invalid records are stored in the rejects table with reason codes and source information.

### 4.3 Duplicate Handling and Idempotency

The pipeline uses SHA-256 file identification and deterministic identifiers to support safe reruns.

Database constraints and conflict-handling operations prevent unintended duplicate event insertion.

Completed files can be skipped, while replayed records are tracked separately.

### 4.4 Order History and Current State

The pipeline maintains two different representations:

- `order_history`: Stores order events as append-only history.
- `fact_orders`: Stores the latest state of each order.

Events arriving late or out of order are preserved in history without incorrectly replacing a newer current state.

### 4.5 Checkpointing and Recovery

The pipeline maintains file progress checkpoints.

Progress is committed in the same transaction as the corresponding data chunk.

This supports recovery after interrupted processing and reduces unnecessary reprocessing.

### 4.6 Data Reconciliation

The reconciliation module compares generated expectations against actual database outcomes.

It verifies source counts, valid and rejected records, duplicate handling, referential integrity, and per-record processing outcomes.

### 4.7 Performance Measurement

The benchmark functionality measures:

- Dataset generation time.
- Data loading time.
- Processing throughput.
- Peak process memory usage.
- Reconciliation results.

It helps evaluate processing efficiency as dataset size increases.

## 5. Database Design

The project uses SQLite as its local analytical storage layer.

Important tables include:

| Table | Purpose |
|---|---|
| `dim_customers` | Stores customer reference data |
| `dim_products` | Stores product reference data |
| `order_history` | Maintains order event history |
| `fact_orders` | Stores current order state |
| `rejects` | Stores invalid and rejected records |
| `run_log` | Records pipeline execution metrics |
| `file_progress` | Tracks processing checkpoints |

The design uses stable keys, source lineage, deterministic identifiers, and integer cents for monetary values.

## 6. Testing Strategy

Automated testing is performed using Python's built-in unittest framework.

The test coverage includes:

- Normal processing scenarios.
- Empty input.
- Malformed records.
- Missing required fields.
- Duplicate events.
- Invalid customer and product references.
- Invalid currencies and statuses.
- Quantity and price boundary cases.
- Timestamp validation.
- Order update handling.

**Previously recorded test result:** 45 tests passed.

## 7. Performance Results

The project was tested with different dataset sizes.

| Dataset Size | Processing Throughput | Peak RSS |
|---|---:|---:|
| 100K rows | 11,208 rows/sec | 47 MB |
| 500K rows | 10,620 rows/sec | 47 MB |
| 1M rows | 10,566 rows/sec | 56.7 MB |

These are previously observed local benchmark results. Actual performance depends on hardware and execution conditions.

## 8. Challenges and Solutions

### Challenge 1: Processing large input files

**Problem:** Loading an entire dataset into memory can increase memory consumption significantly.

**Solution:** Implemented incremental reading and chunk-based processing.

### Challenge 2: Duplicate records during reruns

**Problem:** Reprocessing the same input could create unintended duplicates.

**Solution:** Used deterministic identifiers, database constraints, and file-level SHA-256 identification.

### Challenge 3: Late-arriving order events

**Problem:** Older events should not overwrite newer order states.

**Solution:** Preserved all events in `order_history` and maintained current state using the greatest `updated_at` timestamp.

### Challenge 4: Windows memory measurement

**Problem:** The Python `resource` module is unavailable on Windows.

**Solution:** Added Windows-specific process memory measurement using the Windows process memory API.

### Challenge 5: GitHub repository synchronization

**Problem:** Local and remote repository histories became out of sync during documentation updates.

**Solution:** Synchronized repository changes using Git pull with rebase before pushing the updated documentation.

## 9. Design Trade-offs and Limitations

- SQLite is suitable for local processing but supports only one writer at a time.
- JSON support is focused on JSON Lines.
- Processing is sequential and single-process.
- Status transition rules are not enforced.
- Large JSON arrays require an incremental parsing solution.
- Distributed processing is not currently implemented.

## 10. Future Improvements

Potential improvements include:

- Apache Airflow integration for orchestration.
- PostgreSQL or cloud data warehouse integration.
- Parallel processing for larger workloads.
- Real-time pipeline monitoring.
- Improved automated performance testing.
- Support for incremental parsing of large JSON arrays.

## 11. Conclusion

OrderFlow demonstrates practical data engineering concepts through a modular and testable data processing pipeline.

The implementation focuses on memory-efficient ingestion, data quality, idempotency, event history, recovery, reconciliation, and measurable performance.

The project is designed to be reproducible locally and understandable to developers reviewing its architecture and implementation.