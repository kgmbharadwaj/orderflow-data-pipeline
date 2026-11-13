import filecmp
import os
import tempfile
import unittest

from pipeline import db, generator, loader, reconcile
from pipeline.config import Config


class GeneratedDataset(unittest.TestCase):
    """End-to-end: synthetic data with labels kept OUTSIDE the input files."""

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.d = cls._t.name
        cls.summary = generator.generate(cls.d, rows=6000, seed=11)
        cls.conn = db.connect(os.path.join(cls.d, "w.db"))
        cfg = Config(chunk_size=800)
        for e in ("customers", "products", "orders"):
            cls.res = loader.run_file(cls.conn, e, os.path.join(cls.d, e + ".csv"), cfg)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls._t.cleanup()

    def paths(self):
        d = self.d
        return (os.path.join(d, "orders.csv"), os.path.join(d, "expected_summary.json"),
                os.path.join(d, "orders_labels.csv"))

    def test_every_line_outcome_matches_its_label(self):
        o, e, l = self.paths()
        res = reconcile.reconcile(self.conn, o, e, l)
        failed = [c for c in res["checks"] if not c["passed"]]
        self.assertEqual(failed, [])

    def test_all_reject_reasons_are_exercised(self):
        reasons = {r[0] for r in self.conn.execute("SELECT DISTINCT reason_code FROM rejects")}
        self.assertGreaterEqual(len(reasons), 11)

    def test_rerun_is_a_noop(self):
        before = self.conn.execute("SELECT COUNT(*) FROM order_history").fetchone()[0]
        r = loader.run_file(self.conn, "orders", self.paths()[0], Config())
        self.assertEqual(r["status"], "SKIPPED_ALREADY_LOADED")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM order_history").fetchone()[0], before)

    def test_generator_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d2:
            generator.generate(d2, rows=6000, seed=11)
            for name in ("orders.csv", "orders_labels.csv", "customers.csv", "expected_summary.json"):
                self.assertTrue(filecmp.cmp(os.path.join(self.d, name), os.path.join(d2, name), shallow=False), name)

    def test_input_file_contains_no_labels(self):
        with open(self.paths()[0]) as f:
            self.assertNotIn("VALID", f.read())


if __name__ == "__main__":
    unittest.main()
