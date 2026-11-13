"""Chunked, idempotent, restartable loader.

Memory is O(chunk_size): records are streamed, validated and written one chunk
(= one transaction) at a time. A checkpoint row commits atomically with each chunk.
"""
import hashlib
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from itertools import islice

from . import schema
from .config import Config
from .readers import RecordSource, SchemaError, sha256_file

log = logging.getLogger("orderflow")

try:
    import resource
except ImportError:  # Windows
    resource = None


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")




def peak_rss_mb():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)

        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)

        process = kernel32.GetCurrentProcess()

        success = psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )

        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

        return round(
            counters.PeakWorkingSetSize / (1024 * 1024), 1
        )

    if resource is None:
        return None

    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    return round(
        kb / (1024 * 1024 if sys.platform == "darwin" else 1024),
        1
    )

    if sys.platform == "win32":
        import ctypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)

        process = ctypes.windll.kernel32.GetCurrentProcess()

        success = ctypes.windll.psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb
        )

        if not success:
            return None

        return round(
            counters.PeakWorkingSetSize / (1024 * 1024), 1
        )

    if resource is None:
        return None

    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    return round(
        kb / (1024 * 1024 if sys.platform == "darwin" else 1024),
        1
    )


def _hid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


class RunResult(dict):
    """dict with attribute access: status, run_id, rows_read, ..."""
    __getattr__ = dict.get


def _chunks(it, n):
    while True:
        c = list(islice(it, n))
        if not c:
            return
        yield c


def _known_ids(conn, table, col, ids):
    ids = sorted(ids)
    found = set()
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        q = "SELECT %s FROM %s WHERE %s IN (%s)" % (col, table, col, ",".join("?" * len(part)))
        found.update(r[0] for r in conn.execute(q, part))
    return found


UPSERT_FACT = """
INSERT INTO fact_orders (order_id, customer_id, product_id, order_ts, order_date, quantity,
  unit_price_cents, currency, status, line_total_cents, last_updated_at, source_file, run_id)
VALUES (:order_id,:customer_id,:product_id,:order_ts,:order_date,:quantity,
  :unit_price_cents,:currency,:status,:line_total_cents,:updated_at,:source_file,:run_id)
ON CONFLICT(order_id) DO UPDATE SET
  customer_id=excluded.customer_id, product_id=excluded.product_id, order_ts=excluded.order_ts,
  order_date=excluded.order_date, quantity=excluded.quantity,
  unit_price_cents=excluded.unit_price_cents, currency=excluded.currency, status=excluded.status,
  line_total_cents=excluded.line_total_cents, last_updated_at=excluded.last_updated_at,
  source_file=excluded.source_file, run_id=excluded.run_id
WHERE excluded.last_updated_at > fact_orders.last_updated_at
"""

INSERT_HISTORY = """
INSERT OR IGNORE INTO order_history (order_id, updated_at, event_id, customer_id, product_id,
  order_ts, quantity, unit_price_cents, currency, status, line_total_cents, applied_to_fact,
  source_file, source_sha256, source_line, run_id, loaded_at)
VALUES (:order_id,:updated_at,:event_id,:customer_id,:product_id,:order_ts,:quantity,
  :unit_price_cents,:currency,:status,:line_total_cents,1,:source_file,:source_sha256,
  :source_line,:run_id,:loaded_at)
"""


class _Ctx:
    def __init__(self, entity, path, sha, run_id):
        self.entity, self.path, self.sha, self.run_id = entity, path, sha, run_id
        self.stats = Counter()
        self.rejects = []

    def reject(self, item, reason, detail):
        self.stats["rejected"] += 1
        self.rejects.append((_hid(self.entity, self.sha, item.line), self.run_id, self.entity,
                             self.path, self.sha, item.line, reason, detail, item.raw, now_iso()))


def _validate_chunk(ctx, items):
    ok = []
    fn = schema.VALIDATORS[ctx.entity]
    for it in items:
        if it.error:
            ctx.reject(it, *it.error)
            continue
        clean, err = fn(it.record)
        if err:
            ctx.reject(it, *err)
        else:
            ok.append((it, clean))
    return ok


def _apply_orders(conn, ctx, ok):
    cust = _known_ids(conn, "dim_customers", "customer_id", {c["customer_id"] for _, c in ok})
    prod = _known_ids(conn, "dim_products", "product_id", {c["product_id"] for _, c in ok})
    loaded_at = now_iso()
    for it, c in ok:
        if c["customer_id"] not in cust:
            ctx.reject(it, schema.UNKNOWN_CUSTOMER, "customer_id=%s not in dim_customers" % c["customer_id"])
            continue
        if c["product_id"] not in prod:
            ctx.reject(it, schema.UNKNOWN_PRODUCT, "product_id=%s not in dim_products" % c["product_id"])
            continue
        row = dict(c, event_id=_hid(c["order_id"], c["updated_at"]), source_file=ctx.path,
                   source_sha256=ctx.sha, source_line=it.line, run_id=ctx.run_id, loaded_at=loaded_at)
        if conn.execute(INSERT_HISTORY, row).rowcount == 1:
            ctx.stats["loaded"] += 1
            if conn.execute(UPSERT_FACT, row).rowcount == 0:  # older than current state
                conn.execute("UPDATE order_history SET applied_to_fact=0 WHERE order_id=? AND updated_at=?",
                             (c["order_id"], c["updated_at"]))
                ctx.stats["late"] += 1
            continue
        prev = conn.execute("SELECT source_sha256, source_line, source_file FROM order_history "
                            "WHERE order_id=? AND updated_at=?", (c["order_id"], c["updated_at"])).fetchone()
        if prev[0] == ctx.sha and prev[1] == it.line:
            ctx.stats["replayed"] += 1  # same file+line already loaded: idempotent no-op
        else:
            ctx.reject(it, schema.DUPLICATE_EVENT,
                       "(order_id, updated_at) already loaded from %s:%s" % (prev[2], prev[1]))


def _apply_customers(conn, ctx, ok):
    conn.executemany(
        "INSERT INTO dim_customers (customer_id,name,country,signup_date,source_file,run_id) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(customer_id) DO UPDATE SET name=excluded.name, "
        "country=excluded.country, signup_date=excluded.signup_date, source_file=excluded.source_file, "
        "run_id=excluded.run_id",
        [(c["customer_id"], c["name"], c["country"], c["signup_date"], ctx.path, ctx.run_id) for _, c in ok])
    ctx.stats["loaded"] += len(ok)


def _apply_products(conn, ctx, ok):
    conn.executemany(
        "INSERT INTO dim_products (product_id,name,category,list_price_cents,source_file,run_id) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET name=excluded.name, "
        "category=excluded.category, list_price_cents=excluded.list_price_cents, "
        "source_file=excluded.source_file, run_id=excluded.run_id",
        [(c["product_id"], c["name"], c["category"], c["list_price_cents"], ctx.path, ctx.run_id) for _, c in ok])
    ctx.stats["loaded"] += len(ok)


APPLY = {"orders": _apply_orders, "customers": _apply_customers, "products": _apply_products}


def _finish(conn, run_id, started, status, ctx, warnings, failure, cfg, resumed_skipped):
    s = ctx.stats
    dur = round(time.time() - started, 3)
    conn.execute(
        "UPDATE run_log SET status=?, ended_at=?, duration_seconds=?, rows_read=?, rows_loaded=?, "
        "rows_rejected=?, rows_replayed=?, rows_late=?, rows_resumed_skipped=?, peak_rss_mb=?, "
        "warnings=?, failure_reason=? WHERE run_id=?",
        (status, now_iso(), dur, s["read"], s["loaded"], s["rejected"], s["replayed"], s["late"],
         resumed_skipped, peak_rss_mb(), "; ".join(warnings) or None, failure, run_id))
    res = RunResult(run_id=run_id, entity=ctx.entity, status=status, source_file=ctx.path,
                    source_sha256=ctx.sha, rows_read=s["read"], rows_loaded=s["loaded"],
                    rows_rejected=s["rejected"], rows_replayed=s["replayed"], rows_late=s["late"],
                    rows_resumed_skipped=resumed_skipped, duration_seconds=dur,
                    failure_reason=failure, warnings=warnings)
    log.info("run_finished %s", json.dumps(res, default=str))
    return res


def run_file(conn, entity, path, cfg=None):
    """Load one file. Never raises for data problems; returns RunResult (status in
    SUCCESS | SKIPPED_ALREADY_LOADED | FAILED)."""
    cfg = cfg or Config()
    started = time.time()
    sha = sha256_file(path)
    run_id = "run_%s_%s" % (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"), sha[:8])
    ctx = _Ctx(entity, path, sha, run_id)
    prog = conn.execute("SELECT status, last_line FROM file_progress WHERE source_sha256=? AND entity=?",
                        (sha, entity)).fetchone()
    conn.execute("INSERT INTO run_log (run_id, entity, source_file, source_sha256, status, started_at, chunk_size) "
                 "VALUES (?,?,?,?,?,?,?)", (run_id, entity, path, sha, "RUNNING", now_iso(), cfg.chunk_size))
    log.info("run_started run_id=%s entity=%s file=%s sha=%s", run_id, entity, path, sha[:12])

    if prog and prog[0] == "COMPLETE" and not cfg.force:
        return _finish(conn, run_id, started, "SKIPPED_ALREADY_LOADED", ctx,
                       ["identical file already loaded (use --force to replay)"], None, cfg, 0)

    resume_from = 0 if (cfg.force or not prog) else prog[1]
    resumed_skipped = 0
    warnings = []
    chunks_done = 0
    source = RecordSource(path, entity)
    try:
        def pending():
            nonlocal resumed_skipped
            for it in source:
                if it.line <= resume_from:
                    resumed_skipped += 1
                    continue
                ctx.stats["read"] += 1
                yield it

        last_line = resume_from
        for chunk in _chunks(pending(), cfg.chunk_size):
            ctx.rejects = []
            conn.execute("BEGIN")
            try:
                APPLY[entity](conn, ctx, _validate_chunk(ctx, chunk))
                if ctx.rejects:
                    conn.executemany("INSERT OR REPLACE INTO rejects (reject_id, run_id, entity, source_file, "
                                     "source_sha256, source_line, reason_code, reason_detail, raw_record, "
                                     "rejected_at) VALUES (?,?,?,?,?,?,?,?,?,?)", ctx.rejects)
                last_line = chunk[-1].line
                conn.execute("INSERT OR REPLACE INTO file_progress VALUES (?,?,?,?,?,?,?)",
                             (sha, entity, path, last_line, "IN_PROGRESS", run_id, now_iso()))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            chunks_done += 1
            if cfg.fail_after_chunks is not None and chunks_done >= cfg.fail_after_chunks:
                raise RuntimeError("injected failure after %d chunk(s)" % chunks_done)
        warnings = list(source.warnings)
        conn.execute("INSERT OR REPLACE INTO file_progress VALUES (?,?,?,?,?,?,?)",
                     (sha, entity, path, last_line, "COMPLETE", run_id, now_iso()))
        return _finish(conn, run_id, started, "SUCCESS", ctx, warnings, None, cfg, resumed_skipped)
    except SchemaError as e:
        log.error("schema_error run_id=%s %s", run_id, e)
        return _finish(conn, run_id, started, "FAILED", ctx, list(source.warnings),
                       "SCHEMA_ERROR: %s" % e, cfg, resumed_skipped)
    except Exception as e:  # noqa: BLE001 - operator-facing failure record
        log.exception("run_failed run_id=%s", run_id)
        return _finish(conn, run_id, started, "FAILED", ctx, list(source.warnings),
                       "%s: %s" % (type(e).__name__, e), cfg, resumed_skipped)
