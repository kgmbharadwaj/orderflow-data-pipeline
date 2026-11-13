"""Source-to-target reconciliation and label-based correctness validation."""
import csv
import json

from .readers import count_records, sha256_file


def _check(name, expected, actual, detail=""):
    return {"check": name, "expected": expected, "actual": actual,
            "passed": expected == actual, "detail": detail}


def reconcile(conn, orders_path=None, expected_summary_path=None, labels_path=None):
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    checks = []

    if orders_path:
        sha = sha256_file(orders_path)
        in_file = count_records(orders_path, "orders")
        loaded = q("SELECT COUNT(*) FROM order_history WHERE source_sha256=?", sha)
        rejected = q("SELECT COUNT(*) FROM rejects WHERE source_sha256=? AND entity='orders'", sha)
        checks.append(_check("file records == history rows + rejects", in_file, loaded + rejected,
                             "in_file=%d loaded=%d rejected=%d" % (in_file, loaded, rejected)))

    checks.append(_check("fact_orders rows == distinct orders in history",
                         q("SELECT COUNT(DISTINCT order_id) FROM order_history"),
                         q("SELECT COUNT(*) FROM fact_orders")))
    latest_sum = q("SELECT COALESCE(SUM(line_total_cents),0) FROM order_history h WHERE updated_at = "
                   "(SELECT MAX(updated_at) FROM order_history WHERE order_id=h.order_id)")
    checks.append(_check("sum(line_total) of latest events == sum(fact_orders.line_total)", latest_sum,
                         q("SELECT COALESCE(SUM(line_total_cents),0) FROM fact_orders")))
    checks.append(_check("fact_orders.last_updated_at == max(history.updated_at) for every order", 0,
                         q("SELECT COUNT(*) FROM fact_orders f WHERE last_updated_at <> "
                           "(SELECT MAX(updated_at) FROM order_history WHERE order_id=f.order_id)")))
    checks.append(_check("orphan customer keys in fact_orders", 0,
                         q("SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_customers c USING(customer_id) "
                           "WHERE c.customer_id IS NULL")))
    checks.append(_check("orphan product keys in fact_orders", 0,
                         q("SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_products p USING(product_id) "
                           "WHERE p.product_id IS NULL")))
    checks.append(_check("duplicate (order_id, updated_at) in history", 0,
                         q("SELECT COUNT(*) FROM (SELECT 1 FROM order_history GROUP BY order_id, updated_at "
                           "HAVING COUNT(*)>1)")))
    checks.append(_check("rejects without source traceability", 0,
                         q("SELECT COUNT(*) FROM rejects WHERE source_file='' OR source_line IS NULL "
                           "OR source_sha256=''")))

    if expected_summary_path:
        exp = json.load(open(expected_summary_path))
        checks.append(_check("distinct valid orders vs generator expectation",
                             exp["expected_distinct_orders"], q("SELECT COUNT(*) FROM fact_orders")))
        checks.append(_check("sum of final line totals (cents) vs generator expectation",
                             exp["expected_final_total_cents"],
                             q("SELECT COALESCE(SUM(line_total_cents),0) FROM fact_orders")))

    if labels_path and orders_path:
        checks.append(_check("per-line outcome mismatches vs labels file", 0,
                             _label_mismatches(conn, sha, labels_path),
                             "labels kept separate from input data"))

    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def _label_mismatches(conn, sha, labels_path):
    conn.execute("DROP TABLE IF EXISTS temp.labels")
    conn.execute("CREATE TEMP TABLE labels (source_line INTEGER PRIMARY KEY, label TEXT)")
    with open(labels_path, newline="") as f:
        r = csv.reader(f)
        next(r)
        batch = []
        for line, label in r:
            batch.append((int(line), label))
            if len(batch) >= 10000:
                conn.executemany("INSERT INTO temp.labels VALUES (?,?)", batch)
                batch = []
        if batch:
            conn.executemany("INSERT INTO temp.labels VALUES (?,?)", batch)
    sql = """
    WITH actual AS (
      SELECT source_line, 'VALID' AS label FROM order_history WHERE source_sha256=?1
      UNION ALL
      SELECT source_line, 'REJECT:' || reason_code FROM rejects WHERE source_sha256=?1 AND entity='orders')
    SELECT COUNT(*) FROM (
      SELECT l.source_line FROM temp.labels l LEFT JOIN actual a USING(source_line)
        WHERE a.label IS NOT l.label
      UNION ALL
      SELECT a.source_line FROM actual a LEFT JOIN temp.labels l USING(source_line)
        WHERE l.source_line IS NULL)"""
    n = conn.execute(sql, (sha,)).fetchone()[0]
    conn.execute("DROP TABLE temp.labels")
    return n


def render_markdown(result):
    lines = ["# Reconciliation report", "", "Overall: **%s**" % ("PASS" if result["passed"] else "FAIL"), "",
             "| Check | Expected | Actual | Result |", "|---|---|---|---|"]
    for c in result["checks"]:
        lines.append("| %s | %s | %s | %s |" % (c["check"], c["expected"], c["actual"],
                                                 "PASS" if c["passed"] else "FAIL"))
    return "\n".join(lines) + "\n"
