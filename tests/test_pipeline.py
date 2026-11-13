import json
import os
import unittest

from pipeline import loader, reconcile
from pipeline.config import Config
from pipeline.schema import ORDER_FIELDS
from tests.helpers import PipelineCase, order, write_csv


class HappyAndBasics(PipelineCase):
    def test_happy_path(self):
        f = self.orders_file([order("ORD-00000001"), order("ORD-00000002", cust="C000002", prod="P00002", qty="1", price="5.50")])
        r = self.load(f)
        self.assertEqual((r["status"], r["rows_read"], r["rows_loaded"], r["rows_rejected"]), ("SUCCESS", 2, 2, 0))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_orders"), 2)
        self.assertEqual(self.scalar("SELECT line_total_cents FROM fact_orders WHERE order_id='ORD-00000001'"), 2000)
        self.assertTrue(reconcile.reconcile(self.conn, f)["passed"])

    def test_empty_inputs_succeed_with_zero_rows(self):
        for name, content in (("zero.csv", ""), ("header.csv", ",".join(ORDER_FIELDS) + "\n")):
            p = self.p(name)
            with open(p, "w") as fh:
                fh.write(content)
            r = self.load(p)
            self.assertEqual((r["status"], r["rows_read"], r["rows_loaded"]), ("SUCCESS", 0, 0), name)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_orders"), 0)

    def test_run_log_records_metrics(self):
        r = self.load(self.orders_file([order(), order("ORD-00000002", qty="0")]))
        row = self.q("SELECT status, rows_read, rows_loaded, rows_rejected, started_at, ended_at, "
                     "failure_reason FROM run_log WHERE run_id=?", r["run_id"])[0]
        self.assertEqual(row[:4], ("SUCCESS", 2, 1, 1))
        self.assertTrue(row[4].endswith("Z") and row[5].endswith("Z"))
        self.assertIsNone(row[6])


class MalformedAndRejects(PipelineCase):
    def test_rejects_are_traceable_to_source_line(self):
        f = self.orders_file([order("ORD-00000001"), order("ORD-00000002", qty="abc"), order("ORD-00000003", cur="XXX")])
        r = self.load(f)
        self.assertEqual(r["rows_rejected"], 2)
        rows = self.q("SELECT source_line, reason_code, source_file, source_sha256, raw_record FROM rejects ORDER BY source_line")
        self.assertEqual([(x[0], x[1]) for x in rows], [(3, "INVALID_QUANTITY"), (4, "INVALID_CURRENCY")])
        self.assertTrue(all(x[2] == f and x[3] == r["source_sha256"] and "ORD-0000000" in x[4] for x in rows))

    def test_malformed_row_and_missing_field_do_not_stop_the_run(self):
        f = self.p("o.csv")
        with open(f, "w") as fh:
            fh.write(",".join(ORDER_FIELDS) + "\n" + ",".join(order()) + "\nBROKEN,ROW\n"
                     + ",".join(order("ORD-00000003", cust="")) + "\n" + ",".join(order("ORD-00000004")) + "\n")
        r = self.load(f)
        self.assertEqual((r["status"], r["rows_loaded"], r["rows_rejected"]), ("SUCCESS", 2, 2))
        self.assertEqual(dict(self.q("SELECT reason_code, COUNT(*) FROM rejects GROUP BY 1")),
                         {"MALFORMED_ROW": 1, "MISSING_REQUIRED_FIELD": 1})

    def test_invalid_reference_keys(self):
        r = self.load(self.orders_file([order("ORD-00000001", cust="C999999"), order("ORD-00000002", prod="P99999"), order("ORD-00000003")]))
        self.assertEqual(r["rows_loaded"], 1)
        self.assertEqual(dict(self.q("SELECT reason_code, COUNT(*) FROM rejects GROUP BY 1")),
                         {"UNKNOWN_CUSTOMER": 1, "UNKNOWN_PRODUCT": 1})
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_history WHERE order_id IN ('ORD-00000001','ORD-00000002')"), 0)

    def test_schema_change_fails_run_and_loads_nothing(self):
        cols = [c for c in ORDER_FIELDS if c != "unit_price"] + ["price"]  # column renamed upstream
        f = write_csv(self.p("o.csv"), cols, [])
        r = self.load(f)
        self.assertEqual(r["status"], "FAILED")
        self.assertIn("SCHEMA_ERROR", r["failure_reason"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_history"), 0)
        self.assertEqual(self.q("SELECT 1 FROM file_progress WHERE entity='orders'"), [])

    def test_unexpected_extra_column_is_a_warning_not_a_failure(self):
        f = write_csv(self.p("o.csv"), ORDER_FIELDS + ["channel"], [order() + ["web"]])
        r = self.load(f)
        self.assertEqual((r["status"], r["rows_loaded"]), ("SUCCESS", 1))
        self.assertTrue(any("extra columns" in w for w in r["warnings"]))

    def test_jsonl_input(self):
        f = self.p("o.jsonl")
        good = dict(zip(ORDER_FIELDS, order(qty="2")))
        good["quantity"] = 2  # JSON number
        with open(f, "w") as fh:
            fh.write(json.dumps(good) + "\n{bad json\n" + json.dumps({"order_id": "ORD-00000009"}) + "\n")
        r = self.load(f)
        self.assertEqual((r["rows_loaded"], r["rows_rejected"]), (1, 2))
        self.assertEqual(dict(self.q("SELECT reason_code, COUNT(*) FROM rejects GROUP BY 1")),
                         {"MALFORMED_ROW": 1, "MISSING_REQUIRED_FIELD": 1})


class Duplicates(PipelineCase):
    def test_duplicate_event_in_same_file_first_wins(self):
        a = order("ORD-00000001", qty="2")
        b = order("ORD-00000001", qty="9")  # same (order_id, updated_at), different payload
        r = self.load(self.orders_file([a, b]))
        self.assertEqual((r["rows_loaded"], r["rows_rejected"]), (1, 1))
        self.assertEqual(self.scalar("SELECT quantity FROM fact_orders"), 2)
        self.assertEqual(self.q("SELECT reason_code, source_line FROM rejects"), [("DUPLICATE_EVENT", 3)])

    def test_overlapping_batch_from_another_file_is_rejected_as_duplicate(self):
        self.load(self.orders_file([order("ORD-00000001"), order("ORD-00000002")], "a.csv"))
        r = self.load(self.orders_file([order("ORD-00000002"), order("ORD-00000003")], "b.csv"))
        self.assertEqual((r["rows_loaded"], r["rows_rejected"]), (1, 1))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_history"), 3)


class LateAndOutOfOrder(PipelineCase):
    def test_late_arriving_event_is_kept_in_history_but_does_not_overwrite_state(self):
        t0, t1, t2 = "2026-03-01T10:00:00Z", "2026-03-01T11:00:00Z", "2026-03-01T12:00:00Z"
        rows = [order(status="shipped", upd=t2), order(status="paid", upd=t1), order(status="placed", upd=t0)]
        r = self.load(self.orders_file(rows))
        self.assertEqual((r["rows_loaded"], r["rows_late"]), (3, 2))
        self.assertEqual(self.q("SELECT status, last_updated_at FROM fact_orders"), [("shipped", t2)])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_history WHERE applied_to_fact=0"), 2)

    def test_in_order_events_update_state_and_keep_history(self):
        rows = [order(status="placed"), order(status="paid", upd="2026-03-02T10:00:00Z")]
        self.load(self.orders_file(rows))
        self.assertEqual(self.q("SELECT status FROM fact_orders"), [("paid",)])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_history"), 2)

    def test_late_event_in_a_later_file(self):
        self.load(self.orders_file([order(status="paid", upd="2026-03-02T10:00:00Z")], "a.csv"))
        r = self.load(self.orders_file([order(status="placed")], "b.csv"))
        self.assertEqual(r["rows_late"], 1)
        self.assertEqual(self.q("SELECT status FROM fact_orders"), [("paid",)])

    def test_timezone_variants_of_same_instant_are_duplicates(self):
        a = order(upd="2026-03-01T10:00:00Z")
        b = order(upd="2026-03-01T15:30:00+05:30")
        r = self.load(self.orders_file([a, b]))
        self.assertEqual((r["rows_loaded"], r["rows_rejected"]), (1, 1))


class IdempotencyAndRecovery(PipelineCase):
    def rows(self, n):
        return [order("ORD-%08d" % i) for i in range(1, n + 1)]

    def snapshot(self):
        return [self.q("SELECT * FROM fact_orders ORDER BY order_id"),
                self.q("SELECT order_id, updated_at, applied_to_fact, source_line FROM order_history ORDER BY 1,2"),
                self.q("SELECT reject_id, source_line, reason_code FROM rejects ORDER BY 1")]

    def test_rerun_of_same_file_is_skipped(self):
        f = self.orders_file(self.rows(5) + [order("ORD-00000099", qty="0")])
        self.load(f)
        before = self.snapshot()
        r = self.load(f)
        self.assertEqual(r["status"], "SKIPPED_ALREADY_LOADED")
        self.assertEqual(self.snapshot(), before)

    def test_forced_replay_creates_no_duplicates_and_no_new_rejects(self):
        f = self.orders_file(self.rows(5) + [order("ORD-00000099", qty="0"), order("ORD-00000001")])  # last = duplicate
        self.load(f)
        before = self.snapshot()
        r = self.load(f, force=True)
        self.assertEqual((r["status"], r["rows_loaded"], r["rows_replayed"], r["rows_rejected"]), ("SUCCESS", 0, 5, 2))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM rejects"), 2)

    def test_partial_failure_then_restart_resumes_from_checkpoint(self):
        f = self.orders_file(self.rows(25) + [order("ORD-00000100", qty="x")])
        r1 = self.load(f, chunk_size=10, fail_after_chunks=1)
        self.assertEqual(r1["status"], "FAILED")
        self.assertIn("injected failure", r1["failure_reason"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_history"), 10)  # only the committed chunk
        self.assertEqual(self.scalar("SELECT last_line FROM file_progress WHERE entity='orders'"), 11)
        r2 = self.load(f, chunk_size=10)
        self.assertEqual((r2["status"], r2["rows_resumed_skipped"], r2["rows_loaded"], r2["rows_rejected"]), ("SUCCESS", 10, 15, 1))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM fact_orders"), 25)
        self.assertTrue(reconcile.reconcile(self.conn, f)["passed"])
        # result identical to a clean single run
        cols = "order_id, status, last_updated_at, line_total_cents, quantity"
        clean = self.q("SELECT %s FROM fact_orders ORDER BY order_id" % cols)
        self.conn.close()
        from pipeline import db
        conn2 = db.connect(self.p("clean.db"))
        for e, name in (("customers", "customers.csv"), ("products", "products.csv")):
            loader.run_file(conn2, e, self.p(name), self.cfg)
        loader.run_file(conn2, "orders", f, Config(chunk_size=10))
        self.assertEqual(clean, conn2.execute("SELECT %s FROM fact_orders ORDER BY order_id" % cols).fetchall())
        self.conn = conn2

    def test_failed_chunk_is_rolled_back_atomically(self):
        f = self.orders_file(self.rows(6))
        orig = loader._apply_orders
        calls = {"n": 0}

        def boom(conn, ctx, ok):
            calls["n"] += 1
            orig(conn, ctx, ok)
            if calls["n"] == 2:
                raise ValueError("db exploded mid-chunk")
        loader.APPLY["orders"] = boom
        try:
            r = self.load(f, chunk_size=3)
        finally:
            loader.APPLY["orders"] = orig
        self.assertEqual(r["status"], "FAILED")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_history"), 3)  # chunk 2 rolled back
        r = self.load(f, chunk_size=3)
        self.assertEqual((r["status"], self.scalar("SELECT COUNT(*) FROM order_history")), ("SUCCESS", 6))

    def test_backfill_older_file_after_newer_one_keeps_latest_state(self):
        self.load(self.orders_file([order(status="shipped", upd="2026-03-05T00:00:00Z")], "new.csv"))
        self.load(self.orders_file([order(status="placed")], "old.csv"))
        self.assertEqual(self.q("SELECT status FROM fact_orders"), [("shipped",)])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM order_history"), 2)

    def test_chunk_size_does_not_change_results(self):
        rows = self.rows(40) + [order("ORD-00000005", status="paid", upd="2026-03-02T00:00:00Z"), order("ORD-00000001")]
        f = self.orders_file(rows)
        self.load(f, chunk_size=7)
        a = [self.q("SELECT order_id, status, last_updated_at, line_total_cents FROM fact_orders ORDER BY 1"),
             self.q("SELECT order_id, updated_at, applied_to_fact FROM order_history ORDER BY 1,2"),
             self.q("SELECT source_line, reason_code FROM rejects ORDER BY 1")]
        for t in ("order_history", "fact_orders", "rejects"):
            self.conn.execute("DELETE FROM " + t)
        self.conn.execute("DELETE FROM file_progress WHERE entity='orders'")
        self.load(f, chunk_size=1000)
        b = [self.q("SELECT order_id, status, last_updated_at, line_total_cents FROM fact_orders ORDER BY 1"),
             self.q("SELECT order_id, updated_at, applied_to_fact FROM order_history ORDER BY 1,2"),
             self.q("SELECT source_line, reason_code FROM rejects ORDER BY 1")]
        self.assertEqual(a, b)


class DimensionLoads(PipelineCase):
    def test_dimension_rejects_and_upsert(self):
        f = write_csv(self.p("c2.csv"), ["customer_id", "name", "country", "signup_date"],
                      [["C000003", "Cy", "DE", "2025-05-05"], ["C000004", "Di", "Germany", "2025-05-05"],
                       ["C000001", "Asha R", "IN", "2025-01-10"]])
        r = loader.run_file(self.conn, "customers", f, self.cfg)
        self.assertEqual((r["rows_loaded"], r["rows_rejected"]), (2, 1))
        self.assertEqual(self.scalar("SELECT name FROM dim_customers WHERE customer_id='C000001'"), "Asha R")


if __name__ == "__main__":
    unittest.main()
