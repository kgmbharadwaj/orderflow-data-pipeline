# Data dictionary

Naming: `snake_case` tables/columns; timestamps are ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`);
money is stored as **integer cents** (no float drift); every loaded/rejected row carries
`run_id`, `source_file`, and `source_line`.

## Source files (inputs)

### orders (CSV or JSON Lines) — grain: one **order event** (a status change of an order)
| field | type | rules |
|---|---|---|
| order_id | string | required, `ORD-` + 8 digits |
| customer_id | string | required, `C` + 6 digits, must exist in customers (referential integrity) |
| product_id | string | required, `P` + 5 digits, must exist in products |
| order_ts | timestamp | required, ISO-8601; offset converted to UTC; naive = UTC |
| quantity | integer | required, 1..10000 |
| unit_price | decimal | required, >= 0, at most 2 decimal places, <= 1,000,000 |
| currency | string | required, one of USD, EUR, INR (case-insensitive) |
| status | string | required, one of placed, paid, shipped, delivered, cancelled, refunded |
| updated_at | timestamp | required, event time, must be >= order_ts |

Event key: `(order_id, updated_at)` (normalized to UTC). Extra columns are ignored with a warning;
a missing required column fails the file.

### customers — key `customer_id`: name, country (ISO alpha-2), signup_date (YYYY-MM-DD)
### products — key `product_id`: name, category, list_price (decimal, 2 dp)
Dimensions are snapshot upserts (last row for a key wins).

## Target tables (SQLite)

| table | grain / key | purpose |
|---|---|---|
| `dim_customers`, `dim_products` | `customer_id` / `product_id` | reference data |
| `order_history` | PK `(order_id, updated_at)` | **append-only event history**; never overwritten. Derived: `event_id` (sha1 of key, 16 hex), `line_total_cents = quantity * unit_price_cents`, `applied_to_fact` (0 = late event that did not change current state), lineage `source_file / source_sha256 / source_line / run_id / loaded_at` |
| `fact_orders` | PK `order_id` | current state = event with greatest `updated_at`. Derived: `order_date` (UTC date of order_ts), `line_total_cents`, `last_updated_at` |
| `rejects` | PK `reject_id` = sha1(entity, file sha256, line) | quarantine: `reason_code`, `reason_detail`, `raw_record`, source lineage |
| `run_log` | PK `run_id` | one row per run: status, start/end, duration, rows_read/loaded/rejected/replayed/late/resumed_skipped, peak_rss_mb, warnings, failure_reason |
| `file_progress` | PK `(source_sha256, entity)` | restart checkpoint (`last_line`, `IN_PROGRESS`/`COMPLETE`) |

Views: `v_daily_sales`, `v_order_status`, `v_reject_summary`.

## Reject reason codes
`MALFORMED_ROW`, `MISSING_REQUIRED_FIELD`, `INVALID_ID_FORMAT`, `INVALID_QUANTITY`, `INVALID_PRICE`,
`INVALID_CURRENCY`, `INVALID_STATUS`, `INVALID_TIMESTAMP`, `UPDATED_BEFORE_ORDER`,
`UNKNOWN_CUSTOMER`, `UNKNOWN_PRODUCT`, `DUPLICATE_EVENT`.
Checks run in the order listed in `pipeline/schema.py`, so the first failing rule is the reason.

## IDs
`run_id = run_<UTC timestamp with microseconds>_<first 8 of file sha256>`; `event_id` and `reject_id`
are deterministic hashes, so the same input always yields the same ids.
