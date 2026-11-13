import unittest

from pipeline import schema as s
from tests.helpers import order
from pipeline.schema import ORDER_FIELDS


def rec(**over):
    r = dict(zip(ORDER_FIELDS, order()))
    r.update(over)
    return r


class ValidateOrder(unittest.TestCase):
    def reason(self, **over):
        clean, err = s.validate_order(rec(**over))
        self.assertIsNone(clean)
        return err[0]

    def test_happy_path_derives_fields(self):
        c, err = s.validate_order(rec(quantity="3", unit_price="12.5"))
        self.assertIsNone(err)
        self.assertEqual((c["unit_price_cents"], c["line_total_cents"], c["order_date"]), (1250, 3750, "2026-03-01"))

    def test_missing_and_null_and_blank_fields(self):
        self.assertEqual(self.reason(order_id=""), s.MISSING_REQUIRED_FIELD)
        self.assertEqual(self.reason(currency=None), s.MISSING_REQUIRED_FIELD)
        self.assertEqual(self.reason(status="   "), s.MISSING_REQUIRED_FIELD)

    def test_bad_ids(self):
        self.assertEqual(self.reason(order_id="ORD-12"), s.INVALID_ID_FORMAT)
        self.assertEqual(self.reason(customer_id="X1"), s.INVALID_ID_FORMAT)

    def test_quantity_boundaries(self):
        for bad in ["0", "-1", "2.5", "abc", "10001"]:
            self.assertEqual(self.reason(quantity=bad), s.INVALID_QUANTITY, bad)
        for good in ["1", "10000"]:
            self.assertIsNone(s.validate_order(rec(quantity=good))[1])

    def test_price_edge_cases(self):  # domain: exact money, no float drift
        for bad in ["-0.01", "12.345", "NaN", "inf", "abc", "1000001"]:
            self.assertEqual(self.reason(unit_price=bad), s.INVALID_PRICE, bad)
        self.assertEqual(s.validate_order(rec(unit_price="0.10", quantity="3"))[0]["line_total_cents"], 30)
        self.assertEqual(s.validate_order(rec(unit_price="0"))[0]["line_total_cents"], 0)

    def test_currency_and_status(self):
        self.assertEqual(self.reason(currency="XXX"), s.INVALID_CURRENCY)
        self.assertEqual(self.reason(status="teleported"), s.INVALID_STATUS)
        self.assertEqual(s.validate_order(rec(currency="usd", status="PAID"))[0]["status"], "paid")

    def test_timestamps_normalised_to_utc(self):
        c, _ = s.validate_order(rec(order_ts="2026-03-01T15:30:00+05:30", updated_at="2026-03-01T10:00:00Z"))
        self.assertEqual((c["order_ts"], c["updated_at"]), ("2026-03-01T10:00:00Z",) * 2)
        c, _ = s.validate_order(rec(order_ts="2026-03-01 10:00:00", updated_at="2026-03-01 10:00:00"))
        self.assertEqual(c["order_ts"], "2026-03-01T10:00:00Z")  # naive assumed UTC

    def test_invalid_timestamps(self):
        for bad in ["yesterday", "2026-13-45T00:00:00Z", "2026-02-30T10:00:00Z", "2026-03-01"]:
            self.assertEqual(self.reason(updated_at=bad), s.INVALID_TIMESTAMP, bad)

    def test_update_before_order_is_rejected(self):
        self.assertEqual(self.reason(updated_at="2026-02-28T00:00:00Z"), s.UPDATED_BEFORE_ORDER)

    def test_timezone_shift_does_not_cause_false_ordering_error(self):
        # 10:00Z order, update expressed in +05:30 as 15:31 local == 10:01Z -> valid
        self.assertIsNone(s.validate_order(rec(updated_at="2026-03-01T15:31:00+05:30"))[1])


class ValidateDims(unittest.TestCase):
    def test_customer_and_product(self):
        self.assertIsNone(s.validate_customer(dict(customer_id="C000001", name="A", country="IN",
                                                   signup_date="2025-01-01"))[1])
        self.assertEqual(s.validate_customer(dict(customer_id="C000001", name="A", country="India",
                                                  signup_date="2025-01-01"))[1][0], s.INVALID_ID_FORMAT)
        self.assertEqual(s.validate_product(dict(product_id="P00001", name="x", category="c",
                                                 list_price="-1"))[1][0], s.INVALID_PRICE)


if __name__ == "__main__":
    unittest.main()
