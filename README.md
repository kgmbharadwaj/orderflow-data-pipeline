# orderflow: bounded-memory, idempotent CSV/JSONL pipeline

Ingests large order-event files (CSV or JSON Lines) plus customer/product reference files into
SQLite with streaming, chunked transactions, validation, a quarantine table, restartable loads,
run metrics, and reconciliation. **Standard library only (Python 3.9+), nothing to install.**

## Quick start (clean environment)
```bash
git clone <this-repo> && cd orderflow
make test        # 45 tests, ~1 s   (or: python3 -m unittest discover -s tests -t . -v)
make sample      # load committed 300-row sample, reconcile against labels, print reports
make large       # generate + load 100,000 rows (ROWS=1000000 for more), reconcile
make bench       # throughput + peak memory
```
Without make:
```bash
python3 -m pipeline generate --out data/generated --rows 100000 --seed 42
python3 -m pipeline load --db wh.db --customers data/generated/customers.csv \
    --products data/generated/products.csv --orders data/generated/orders.csv [--chunk-size 5000] [--force]
python3 -m pipeline reconcile --db wh.db --orders data/generated/orders.csv \
    --expected data/generated/expected_summary.json --labels data/generated/orders_labels.csv
python3 -m pipeline report --db wh.db
```
Exit codes: `load` returns 1 if any run FAILED; `reconcile` returns 2 if any check fails.

## Architecture
```
 customers.csv ─┐                    ┌────────────── per chunk = 1 transaction ──────────────┐
 products.csv  ─┼─► RecordSource ──► │ validate (schema.py) ► ref check (batched IN) ►        │
 orders.csv/.jsonl  (streaming,      │ history INSERT OR IGNORE ► fact UPSERT (if newer) ►    │
                     bad rows are    │ rejects INSERT OR REPLACE ► checkpoint(last_line)      │
                     yielded, not    └─────────────────────────┬──────────────────────────────┘
                     raised)                                   ▼
                                  SQLite: dim_*, order_history, fact_orders, rejects, run_log, file_progress
                                                               ▼
                                          reconcile.py / reports.py (views + sample queries)
```
Modules: `readers.py` (streaming, schema check), `schema.py` (pure validation), `loader.py`
(chunking, idempotency, checkpoints, metrics), `db.py` (DDL/views), `generator.py` (seeded data +
labels), `reconcile.py`, `reports.py`, `cli.py`. Details: `docs/DATA_DICTIONARY.md`, `docs/DESIGN.md`.

## Data model and rules (summary)
- **Grain:** one row per order *event* `(order_id, updated_at)`. `order_history` keeps every accepted
  event; `fact_orders` holds current state (latest `updated_at` wins).
- **Referential integrity:** `customer_id`/`product_id` must exist in the dimensions (else reject).
- **Derived:** `line_total_cents = quantity × unit_price_cents`, `order_date`, `event_id`, `applied_to_fact`.
- **Naming:** snake_case, ISO-8601 UTC timestamps, deterministic ids, `run_id`/`source_file`/`source_line` lineage.
- **Assumptions:** naive timestamps are UTC; currencies USD/EUR/INR (no FX, so revenue is reported per currency);
  prices have at most 2 decimals; first event wins on a duplicate key; a missing required column fails the file.

## Operational behaviour
| Scenario | Behaviour | Test |
|---|---|---|
| Rerun of same file | skipped (`SKIPPED_ALREADY_LOADED`), zero changes | `test_rerun_of_same_file_is_skipped` |
| Forced replay (`--force`) | rows counted as `replayed`, no duplicates, same rejects | `test_forced_replay_creates_no_duplicates...` |
| Crash mid-file | committed chunks stay; restart resumes after `last_line` | `test_partial_failure_then_restart_resumes...` |
| Failure inside a chunk | whole chunk rolled back | `test_failed_chunk_is_rolled_back_atomically` |
| Backfill / overlapping files | older events go to history only; overlaps rejected as duplicates | `test_backfill_...`, `test_overlapping_batch...` |
| Retry | just run the same command again (see restart) | — |

Observability: structured log lines (`run_started`, `run_finished`) on stderr and one `run_log` row
per run: run_id, start/end, duration, rows read/loaded/rejected/replayed/late, peak RSS, warnings, failure_reason.

## Edge-case coverage
Malformed input, missing required field, duplicate key/event, unexpected schema change (missing
column fails; extra column warns), empty batch (zero bytes and header-only), partial failure + retry,
late/out-of-order events, invalid reference keys, rerun of same input, nulls/blank values, JSONL
bad lines, timezone variants of one instant, money precision, update-before-order, boundary
quantities. See `tests/` (45 tests: validation, readers, pipeline, generated end-to-end).

## Evidence in `output/`
`test_results.txt` (45 pass), `reconciliation_sample.md`, `reports_sample.txt`, `rejects_sample.csv`,
`sample_run_summary.json`, `benchmark.txt`, `optimization.txt`.

Reconciliation checks: file records = history + rejects; fact rows = distinct orders; sum of latest-event
totals = fact total; no orphan keys; no duplicate event keys; every reject traceable; and, using the
**separate** labels file, 0 per-line outcome mismatches plus expected order count/total.

## Performance (this sandbox, 1 core, `make bench`)
| rows | load time | rows/s | peak RSS |
|---|---|---|---|
| 100,000 | 4.6 s | ~21,700 | 41 MB |
| 500,000 | 23.0 s | ~21,700 | 47 MB |
| 1,000,000 | 50.3 s | ~19,900 | 50 MB |

10× more rows costs ~9 MB more, so memory is bounded by chunk size, not input size (chunk 50,000 → 126 MB).
**Optimization shipped:** batched `IN (...)` reference lookups per chunk, ~11% faster than per-key queries.
Next: `executemany` for conflict-free rows.

## Known limitations / next steps
SQLite is single-writer; JSON support is JSON Lines (not one huge array); no status-transition
state machine; timestamps truncated to seconds; counters on resumed runs cover the resumed part only.
Next: Parquet output, partitioned parallel loading, quality-gate thresholds, Postgres/warehouse target.
