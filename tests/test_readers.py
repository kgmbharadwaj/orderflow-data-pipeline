import os
import tempfile
import unittest

from pipeline import schema as s
from pipeline.readers import RecordSource, SchemaError, count_records, sha256_file
from tests.helpers import order, write_csv
from pipeline.schema import ORDER_FIELDS


class Readers(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name

    def tearDown(self):
        self._t.cleanup()

    def path(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_malformed_rows_are_yielded_as_errors_not_raised(self):
        p = self.path("o.csv", ",".join(ORDER_FIELDS) + "\n" + ",".join(order()) + "\nONLY,TWO\n" + ",".join(order("ORD-00000002")) + "\n")
        items = list(RecordSource(p, "orders"))
        self.assertEqual([i.line for i in items], [2, 3, 4])
        self.assertEqual(items[1].error[0], s.MALFORMED_ROW)
        self.assertIsNone(items[2].error)

    def test_empty_file_and_header_only(self):
        self.assertEqual(count_records(self.path("zero.csv", ""), "orders"), 0)
        self.assertEqual(count_records(self.path("h.csv", ",".join(ORDER_FIELDS) + "\n"), "orders"), 0)

    def test_missing_required_column_raises_schema_error(self):
        cols = [c for c in ORDER_FIELDS if c != "currency"]
        p = write_csv(os.path.join(self.d, "x.csv"), cols, [])
        with self.assertRaises(SchemaError):
            list(RecordSource(p, "orders"))

    def test_extra_and_reordered_columns_are_tolerated(self):
        cols = list(reversed(ORDER_FIELDS)) + ["new_col"]
        p = write_csv(os.path.join(self.d, "x.csv"), cols, [list(reversed(order())) + ["z"]])
        src = RecordSource(p, "orders")
        items = list(src)
        self.assertEqual(items[0].record["order_id"], "ORD-00000001")
        self.assertTrue(any("extra columns" in w for w in src.warnings))

    def test_blank_lines_skipped_and_bom_header_ok(self):
        p = self.path("b.csv", "\ufeff" + ",".join(ORDER_FIELDS) + "\n\n" + ",".join(order()) + "\n\n")
        self.assertEqual(count_records(p, "orders"), 1)

    def test_jsonl_bad_lines(self):
        p = self.path("o.jsonl", '{"order_id": "ORD-00000001"}\nnot json\n[1,2]\n\n{"order_id": "x"}\n')
        items = list(RecordSource(p, "orders"))
        self.assertEqual([i.line for i in items], [1, 2, 3, 5])
        self.assertEqual([bool(i.error) for i in items], [False, True, True, False])

    def test_sha256_is_content_based(self):
        a, b = self.path("a.txt", "same"), self.path("b.txt", "same")
        self.assertEqual(sha256_file(a), sha256_file(b))


if __name__ == "__main__":
    unittest.main()
