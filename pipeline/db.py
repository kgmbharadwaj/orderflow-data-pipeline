"""SQLite target: connection + DDL. SQLite stands in for any warehouse; SQL is portable."""
import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS dim_customers (
  customer_id TEXT PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL,
  signup_date TEXT NOT NULL, source_file TEXT NOT NULL, run_id TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS dim_products (
  product_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
  list_price_cents INTEGER NOT NULL, source_file TEXT NOT NULL, run_id TEXT NOT NULL);

-- append-only event history: one row per accepted (order_id, updated_at)
CREATE TABLE IF NOT EXISTS order_history (
  order_id TEXT NOT NULL, updated_at TEXT NOT NULL, event_id TEXT NOT NULL,
  customer_id TEXT NOT NULL, product_id TEXT NOT NULL, order_ts TEXT NOT NULL,
  quantity INTEGER NOT NULL, unit_price_cents INTEGER NOT NULL, currency TEXT NOT NULL,
  status TEXT NOT NULL, line_total_cents INTEGER NOT NULL,
  applied_to_fact INTEGER NOT NULL DEFAULT 1,
  source_file TEXT NOT NULL, source_sha256 TEXT NOT NULL, source_line INTEGER NOT NULL,
  run_id TEXT NOT NULL, loaded_at TEXT NOT NULL,
  PRIMARY KEY (order_id, updated_at));
CREATE INDEX IF NOT EXISTS ix_history_source ON order_history(source_sha256, source_line);

-- current state per order (latest event by updated_at wins)
CREATE TABLE IF NOT EXISTS fact_orders (
  order_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES dim_customers(customer_id),
  product_id TEXT NOT NULL REFERENCES dim_products(product_id),
  order_ts TEXT NOT NULL, order_date TEXT NOT NULL, quantity INTEGER NOT NULL,
  unit_price_cents INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
  line_total_cents INTEGER NOT NULL, last_updated_at TEXT NOT NULL,
  source_file TEXT NOT NULL, run_id TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS rejects (
  reject_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, entity TEXT NOT NULL,
  source_file TEXT NOT NULL, source_sha256 TEXT NOT NULL, source_line INTEGER NOT NULL,
  reason_code TEXT NOT NULL, reason_detail TEXT, raw_record TEXT, rejected_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_rejects_source ON rejects(source_sha256);

CREATE TABLE IF NOT EXISTS run_log (
  run_id TEXT PRIMARY KEY, entity TEXT NOT NULL, source_file TEXT NOT NULL,
  source_sha256 TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
  ended_at TEXT, duration_seconds REAL, rows_read INTEGER DEFAULT 0,
  rows_loaded INTEGER DEFAULT 0, rows_rejected INTEGER DEFAULT 0,
  rows_replayed INTEGER DEFAULT 0, rows_late INTEGER DEFAULT 0,
  rows_resumed_skipped INTEGER DEFAULT 0, chunk_size INTEGER,
  peak_rss_mb REAL, warnings TEXT, failure_reason TEXT);

-- restart checkpoint: committed atomically with each chunk
CREATE TABLE IF NOT EXISTS file_progress (
  source_sha256 TEXT NOT NULL, entity TEXT NOT NULL, source_file TEXT NOT NULL,
  last_line INTEGER NOT NULL, status TEXT NOT NULL, run_id TEXT NOT NULL,
  updated_at TEXT NOT NULL, PRIMARY KEY (source_sha256, entity));

CREATE VIEW IF NOT EXISTS v_daily_sales AS
  SELECT order_date, currency, COUNT(*) AS orders, SUM(quantity) AS units,
         SUM(line_total_cents) / 100.0 AS revenue
  FROM fact_orders WHERE status NOT IN ('cancelled','refunded')
  GROUP BY order_date, currency;

CREATE VIEW IF NOT EXISTS v_order_status AS
  SELECT status, currency, COUNT(*) AS orders, SUM(line_total_cents) / 100.0 AS value
  FROM fact_orders GROUP BY status, currency;

CREATE VIEW IF NOT EXISTS v_reject_summary AS
  SELECT entity, reason_code, COUNT(*) AS rejects FROM rejects GROUP BY entity, reason_code;
"""


def connect(path):
    conn = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DDL)
    return conn
