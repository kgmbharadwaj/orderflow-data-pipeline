import csv
import os
import tempfile
import unittest

from pipeline import db, loader
from pipeline.config import Config
from pipeline.schema import ORDER_FIELDS

CUSTOMERS = [["C000001", "Asha", "IN", "2025-01-10"], ["C000002", "Ben", "US", "2025-02-11"]]
PRODUCTS = [["P00001", "Widget", "home", "9.99"], ["P00002", "Gadget", "electronics", "19.50"]]


def order(oid="ORD-00000001", cust="C000001", prod="P00001", ts="2026-03-01T10:00:00Z", qty="2",
          price="10.00", cur="USD", status="placed", upd=None):
    return [oid, cust, prod, ts, qty, price, cur, status, upd or ts]


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return path


class PipelineCase(unittest.TestCase):
    chunk_size = 5000

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.conn = db.connect(os.path.join(self.dir, "t.db"))
        self.cfg = Config(db_path=os.path.join(self.dir, "t.db"), chunk_size=self.chunk_size)
        r1 = loader.run_file(self.conn, "customers", write_csv(self.p("customers.csv"),
                             ["customer_id", "name", "country", "signup_date"], CUSTOMERS), self.cfg)
        r2 = loader.run_file(self.conn, "products", write_csv(self.p("products.csv"),
                             ["product_id", "name", "category", "list_price"], PRODUCTS), self.cfg)
        assert r1["status"] == r2["status"] == "SUCCESS"

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def p(self, name):
        return os.path.join(self.dir, name)

    def orders_file(self, rows, name="orders.csv", header=None):
        return write_csv(self.p(name), header or ORDER_FIELDS, rows)

    def load(self, path, **kw):
        cfg = Config(**dict(self.cfg.__dict__, **kw))
        return loader.run_file(self.conn, "orders", path, cfg)

    def q(self, sql, *a):
        return self.conn.execute(sql, a).fetchall()

    def scalar(self, sql, *a):
        return self.conn.execute(sql, a).fetchone()[0]
