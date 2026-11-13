"""Sample analytical queries for the target consumer."""

QUERIES = {
    "daily_sales (first 10 rows)": "SELECT * FROM v_daily_sales ORDER BY order_date, currency LIMIT 10",
    "orders_by_status": "SELECT * FROM v_order_status ORDER BY status, currency",
    "revenue_by_currency (excl. cancelled/refunded)":
        "SELECT currency, COUNT(*) AS orders, ROUND(SUM(line_total_cents)/100.0,2) AS revenue "
        "FROM fact_orders WHERE status NOT IN ('cancelled','refunded') GROUP BY currency ORDER BY currency",
    "top_5_customers_by_spend (per currency)":
        "SELECT customer_id, currency, ROUND(SUM(line_total_cents)/100.0,2) AS spend FROM fact_orders "
        "WHERE status NOT IN ('cancelled','refunded') GROUP BY customer_id, currency ORDER BY spend DESC LIMIT 5",
    "rejects_by_reason": "SELECT * FROM v_reject_summary ORDER BY entity, rejects DESC",
    "late_arriving_events": "SELECT COUNT(*) AS late_events FROM order_history WHERE applied_to_fact=0",
    "recent_runs": "SELECT run_id, entity, status, rows_read, rows_loaded, rows_rejected, rows_replayed, "
                   "rows_late, duration_seconds FROM run_log ORDER BY started_at DESC LIMIT 10",
}


def run_reports(conn):
    out = {}
    for name, sql in QUERIES.items():
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        out[name] = {"columns": cols, "rows": cur.fetchall()}
    return out


def render_text(results):
    lines = []
    for name, r in results.items():
        lines += ["## " + name, " | ".join(r["columns"])]
        lines += [" | ".join(str(v) for v in row) for row in r["rows"]] or ["(no rows)"]
        lines.append("")
    return "\n".join(lines)
