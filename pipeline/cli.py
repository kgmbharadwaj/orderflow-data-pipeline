import argparse
import json
import logging
import os
import sys
import time

from . import db, generator, loader, reconcile, reports
from .config import Config


def _setup_logging(verbose):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    logging.Formatter.converter = time.gmtime


def cmd_generate(a):
    s = generator.generate(a.out, rows=a.rows, seed=a.seed, invalid_rate=a.invalid_rate, dup_rate=a.dup_rate)
    print(json.dumps(s, indent=2))
    return 0


def cmd_load(a):
    cfg = Config(db_path=a.db, chunk_size=a.chunk_size, force=a.force)
    conn = db.connect(a.db)
    results = []
    for entity, path in (("customers", a.customers), ("products", a.products), ("orders", a.orders)):
        if path:
            results.append(loader.run_file(conn, entity, path, cfg))
            if results[-1]["status"] == "FAILED":
                break  # do not load orders against a failed dimension load
    print(json.dumps(results, indent=2, default=str))
    return 1 if any(r["status"] == "FAILED" for r in results) else 0


def cmd_reconcile(a):
    conn = db.connect(a.db)
    res = reconcile.reconcile(conn, a.orders, a.expected, a.labels)
    md = reconcile.render_markdown(res)
    print(md)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
    return 0 if res["passed"] else 2


def cmd_report(a):
    print(reports.render_text(reports.run_reports(db.connect(a.db))))
    return 0


def cmd_benchmark(a):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        t0 = time.time()
        generator.generate(d, rows=a.rows, seed=a.seed)
        gen_s = time.time() - t0
        conn = db.connect(os.path.join(d, "bench.db"))
        cfg = Config(db_path=os.path.join(d, "bench.db"), chunk_size=a.chunk_size)
        for e in ("customers", "products"):
            loader.run_file(conn, e, os.path.join(d, e + ".csv"), cfg)
        r = loader.run_file(conn, "orders", os.path.join(d, "orders.csv"), cfg)
        rec = reconcile.reconcile(conn, os.path.join(d, "orders.csv"),
                                  os.path.join(d, "expected_summary.json"), os.path.join(d, "orders_labels.csv"))
        out = {"rows": a.rows, "chunk_size": a.chunk_size, "generate_seconds": round(gen_s, 2),
               "load_seconds": r["duration_seconds"], "rows_per_second": round(a.rows / max(r["duration_seconds"], 1e-9)),
               "peak_rss_mb": loader.peak_rss_mb(), "reconciliation_passed": rec["passed"],
               "rows_loaded": r["rows_loaded"], "rows_rejected": r["rows_rejected"]}
        print(json.dumps(out))
        conn.close()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="orderflow", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="create synthetic dataset + labels")
    g.add_argument("--out", default="data/generated"); g.add_argument("--rows", type=int, default=100_000)
    g.add_argument("--seed", type=int, default=42); g.add_argument("--invalid-rate", type=float, default=0.05)
    g.add_argument("--dup-rate", type=float, default=0.01); g.set_defaults(fn=cmd_generate)

    l = sub.add_parser("load", help="load customers/products/orders (CSV or JSONL)")
    l.add_argument("--db", default="warehouse.db"); l.add_argument("--customers"); l.add_argument("--products")
    l.add_argument("--orders"); l.add_argument("--chunk-size", type=int, default=5000)
    l.add_argument("--force", action="store_true", help="replay a file that was already loaded")
    l.set_defaults(fn=cmd_load)

    r = sub.add_parser("reconcile", help="source-to-target reconciliation")
    r.add_argument("--db", default="warehouse.db"); r.add_argument("--orders")
    r.add_argument("--expected"); r.add_argument("--labels"); r.add_argument("--out")
    r.set_defaults(fn=cmd_reconcile)

    q = sub.add_parser("report", help="sample analytical queries"); q.add_argument("--db", default="warehouse.db")
    q.set_defaults(fn=cmd_report)

    b = sub.add_parser("benchmark", help="generate + load + measure")
    b.add_argument("--rows", type=int, default=100_000); b.add_argument("--chunk-size", type=int, default=5000)
    b.add_argument("--seed", type=int, default=42); b.set_defaults(fn=cmd_benchmark)

    a = p.parse_args(argv)
    _setup_logging(a.verbose)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
