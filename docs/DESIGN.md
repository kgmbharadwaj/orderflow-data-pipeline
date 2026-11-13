# Design and trade-off note

**Goal:** load arbitrarily large CSV/JSONL with bounded memory, correct and explainable results,
safe reruns. Chosen over breadth: one complete vertical slice, standard library only.

## Key decisions
1. **Streaming + chunked transactions.** A generator reads one record at a time; every `chunk_size`
   records are validated and written in one transaction. Memory is O(chunk_size), not O(file).
   Measured: peak RSS 41 MB at 100k rows and 50 MB at 1M rows (see README).
2. **Idempotency at two levels.** (a) File level: SHA-256 of content; an identical file that is
   `COMPLETE` is skipped. (b) Row level: natural keys + `INSERT OR IGNORE` on the history PK, upsert on
   `fact_orders`, `INSERT OR REPLACE` on rejects with deterministic ids. So even `--force` replays,
   restarts after a crash, and overlapping files cannot create duplicates.
3. **History vs current state.** `order_history` is append-only (events are facts); `fact_orders`
   is derived and only moves forward in `updated_at`. Late/out-of-order events are *kept* (with
   `applied_to_fact=0`) instead of silently dropped or allowed to overwrite newer state.
4. **Duplicate semantics.** Same `(order_id, updated_at)` from a different position/file is a
   `DUPLICATE_EVENT` reject (first one wins). Same file+line seen again is a *replay* (no-op, counted
   separately), which is what makes reruns distinguishable from true duplicates.
5. **Checkpoint is atomic with data.** `file_progress.last_line` commits in the same transaction as the
   chunk, so a crash can never leave "progress without data" or "data without progress".
6. **Fail the file vs reject the row.** Bad *records* go to quarantine and the run continues;
   a bad *schema* (missing required column) fails the whole file before anything is written.
7. **Money as integer cents** parsed with `Decimal`; >2 decimals rejected rather than rounded.
8. **Labels separate from inputs.** The generator writes `orders_labels.csv` and
   `expected_summary.json` outside the input files; reconciliation compares per-line outcomes to them.

## Trade-offs / limitations
- SQLite is single-writer and local; the SQL is portable but a warehouse would need bulk-load paths.
- JSON support is **JSON Lines** (streamable). A single giant JSON array would need an incremental
  parser (e.g. `ijson`), intentionally not added to keep zero dependencies.
- CSV `source_line` is the physical line; records with embedded newlines would shift numbering.
- Timestamps truncate to seconds; naive timestamps are assumed UTC.
- `rows_*` counters on a resumed run cover only the resumed portion; DB-based reconciliation covers all.
- Status transition rules (e.g. `delivered` before `shipped`) are not enforced; latest event wins.
- Sequential, single process. Parallelism would need partitioning by `order_id` hash.

## Optimization (measured)
Reference-key checks use one batched `IN (...)` query per chunk instead of one query per key:
100k rows 4.57 s -> 4.06 s (~11% faster, `output/optimization.txt`). The remaining cost is the
per-row history insert + upsert; the next step is `executemany` for rows with no key conflict.
