"""Deterministic synthetic data generator (fixed seed). Streams to disk: O(block) memory.

Outputs in out_dir:
  customers.csv, products.csv, orders.csv          <- pipeline inputs
  orders_labels.csv                                <- expected outcome per orders.csv line (NOT input)
  expected_summary.json                            <- expected aggregates (NOT input)
"""
import csv
import json
import os
import random
from collections import Counter, deque
from datetime import datetime, timedelta, timezone

from . import schema

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
COUNTRIES = ["IN", "US", "DE", "FR", "GB", "AE", "SG"]
CATEGORIES = ["electronics", "home", "books", "toys", "grocery", "fashion"]
INVALID_KINDS = [
    ("missing_field", schema.MISSING_REQUIRED_FIELD), ("bad_id", schema.INVALID_ID_FORMAT),
    ("bad_quantity", schema.INVALID_QUANTITY), ("bad_price", schema.INVALID_PRICE),
    ("bad_currency", schema.INVALID_CURRENCY), ("bad_status", schema.INVALID_STATUS),
    ("bad_timestamp", schema.INVALID_TIMESTAMP), ("updated_before_order", schema.UPDATED_BEFORE_ORDER),
    ("unknown_customer", schema.UNKNOWN_CUSTOMER), ("unknown_product", schema.UNKNOWN_PRODUCT),
    ("malformed_row", schema.MALFORMED_ROW),
]
FLOW = ["placed", "paid", "shipped", "delivered"]


def _fmt_ts(rng, dt):
    if rng.random() < 0.5:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    tz = timezone(timedelta(hours=5, minutes=30))
    return dt.astimezone(tz).isoformat(timespec="seconds")


def generate(out_dir, rows=1000, seed=42, invalid_rate=0.05, dup_rate=0.01, block_orders=1500):
    rng = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)
    n_cust, n_prod = max(20, rows // 50), max(10, min(5000, rows // 500))

    with open(os.path.join(out_dir, "customers.csv"), "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(schema.CUSTOMER_FIELDS)
        for i in range(1, n_cust + 1):
            d = BASE - timedelta(days=rng.randint(1, 900))
            w.writerow(["C%06d" % i, "Customer %d" % i, rng.choice(COUNTRIES), d.strftime("%Y-%m-%d")])
    with open(os.path.join(out_dir, "products.csv"), "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(schema.PRODUCT_FIELDS)
        for i in range(1, n_prod + 1):
            w.writerow(["P%05d" % i, "Product %d" % i, rng.choice(CATEGORIES), "%.2f" % rng.uniform(1, 999)])

    labels = Counter()
    exp_orders = exp_total = 0
    order_seq = 0
    line_no = 1  # header
    recent = deque(maxlen=300)  # recently written VALID lines, candidates for duplicates

    with open(os.path.join(out_dir, "orders.csv"), "w", newline="") as fo, \
            open(os.path.join(out_dir, "orders_labels.csv"), "w", newline="") as fl:
        wo, wl = csv.writer(fo, lineterminator="\n"), csv.writer(fl, lineterminator="\n")
        wo.writerow(schema.ORDER_FIELDS)
        wl.writerow(["source_line", "label"])

        def emit(fields, label, raw_line=None):
            nonlocal line_no
            line_no += 1
            if raw_line is not None:
                fo.write(raw_line + "\n")
            else:
                wo.writerow(fields)
            wl.writerow([line_no, label])
            labels[label] += 1

        def make_record(oid, cust, prod, ts, qty, price, cur, status, upd):
            return [oid, "C%06d" % cust, "P%05d" % prod, _fmt_ts(rng, ts), str(qty), price, cur, status,
                    _fmt_ts(rng, upd)]

        while line_no - 1 < rows:
            events, final = [], {}
            for _ in range(block_orders):
                order_seq += 1
                oid = "ORD-%08d" % order_seq
                cust, prod = rng.randint(1, n_cust), rng.randint(1, n_prod)
                ts = BASE + timedelta(seconds=rng.randint(0, 300 * 86400))
                qty, price = rng.randint(1, 20), rng.choice(["%.2f", "%.1f", "%.0f"]) % rng.uniform(1, 999)
                cur = rng.choice(sorted(schema.CURRENCIES))
                n_ev = rng.randint(1, 4)
                upd = ts
                for k in range(n_ev):
                    if k:
                        upd = upd + timedelta(minutes=rng.randint(1, 3000))
                    status = "cancelled" if (k == n_ev - 1 and k > 0 and rng.random() < 0.1) else FLOW[k]
                    events.append(("valid", make_record(oid, cust, prod, ts, qty, price, cur, status, upd),
                                   oid, upd, qty * schema.parse_money_cents(price)))
                if rng.random() < invalid_rate * 2:  # invalid rows are standalone orders
                    order_seq += 1
                    kind, reason = rng.choice(INVALID_KINDS)
                    events.append(("invalid:" + kind, make_record("ORD-%08d" % order_seq, cust, prod, ts, 1,
                                                                   "10.00", "USD", "placed", ts), None, None, 0))
            rng.shuffle(events)  # windowed disorder => out-of-order / late-arriving events
            written = {}
            for kind, rec, oid, upd, total in events:
                if line_no - 1 >= rows:
                    break
                if kind == "valid":
                    emit(rec, "VALID")
                    recent.append(list(rec))
                    prev = written.get(oid)
                    iso = schema.parse_ts(rec[8])
                    if prev is None or iso > prev[0]:
                        written[oid] = (iso, total)
                else:
                    _emit_invalid(emit, rng, kind.split(":")[1], rec, n_cust, n_prod)
                if recent and line_no - 1 < rows and rng.random() < dup_rate:
                    emit(list(rng.choice(recent)), "REJECT:" + schema.DUPLICATE_EVENT)
            exp_orders += len(written)
            exp_total += sum(t for _, t in written.values())

    summary = {"seed": seed, "rows": rows, "label_counts": dict(sorted(labels.items())),
               "expected_distinct_orders": exp_orders, "expected_final_total_cents": exp_total}
    with open(os.path.join(out_dir, "expected_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    return summary


def _emit_invalid(emit, rng, kind, rec, n_cust, n_prod):
    reason = dict(INVALID_KINDS)[kind]
    rec = list(rec)
    F = {n: i for i, n in enumerate(schema.ORDER_FIELDS)}
    if kind == "missing_field":
        rec[F[rng.choice(["order_id", "customer_id", "quantity", "currency", "status", "unit_price"])]] = ""
    elif kind == "bad_id":
        rec[F["order_id"]] = rng.choice(["ORD-12", "order_1", "XYZ"])
    elif kind == "bad_quantity":
        rec[F["quantity"]] = rng.choice(["0", "-3", "2.5", "abc"])
    elif kind == "bad_price":
        rec[F["unit_price"]] = rng.choice(["-5.00", "12.345", "abc", "NaN"])
    elif kind == "bad_currency":
        rec[F["currency"]] = rng.choice(["XXX", "usd$", "RUPEE"])
    elif kind == "bad_status":
        rec[F["status"]] = rng.choice(["teleported", "PAID??"])
    elif kind == "bad_timestamp":
        rec[F["updated_at"]] = rng.choice(["2026-13-45T00:00:00Z", "yesterday", "2026-02-30T10:00:00Z"])
    elif kind == "updated_before_order":
        rec[F["updated_at"]] = "2025-01-01T00:00:00Z"
    elif kind == "unknown_customer":
        rec[F["customer_id"]] = "C9%05d" % rng.randint(0, 99999)
    elif kind == "unknown_product":
        rec[F["product_id"]] = "P9%04d" % rng.randint(0, 9999)
    elif kind == "malformed_row":
        emit(None, "REJECT:" + reason, raw_line=",".join(rec[:rng.randint(2, 6)]))
        return
    emit(rec, "REJECT:" + reason)
