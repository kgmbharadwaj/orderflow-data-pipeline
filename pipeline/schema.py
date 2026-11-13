"""Schemas and per-record validation. Pure functions, no I/O."""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

# ---- reject reason codes (stable, documented in docs/DATA_DICTIONARY.md) ----
MALFORMED_ROW = "MALFORMED_ROW"
MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
INVALID_ID_FORMAT = "INVALID_ID_FORMAT"
INVALID_QUANTITY = "INVALID_QUANTITY"
INVALID_PRICE = "INVALID_PRICE"
INVALID_CURRENCY = "INVALID_CURRENCY"
INVALID_STATUS = "INVALID_STATUS"
INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
UPDATED_BEFORE_ORDER = "UPDATED_BEFORE_ORDER"
UNKNOWN_CUSTOMER = "UNKNOWN_CUSTOMER"
UNKNOWN_PRODUCT = "UNKNOWN_PRODUCT"
DUPLICATE_EVENT = "DUPLICATE_EVENT"

ORDER_FIELDS = ["order_id", "customer_id", "product_id", "order_ts",
                "quantity", "unit_price", "currency", "status", "updated_at"]
CUSTOMER_FIELDS = ["customer_id", "name", "country", "signup_date"]
PRODUCT_FIELDS = ["product_id", "name", "category", "list_price"]

REQUIRED = {"orders": ORDER_FIELDS, "customers": CUSTOMER_FIELDS, "products": PRODUCT_FIELDS}

CURRENCIES = {"USD", "EUR", "INR"}
STATUSES = {"placed", "paid", "shipped", "delivered", "cancelled", "refunded"}
MAX_QUANTITY = 10_000
MAX_PRICE = Decimal("1000000")

ORDER_ID_RE = re.compile(r"^ORD-\d{8}$")
CUSTOMER_ID_RE = re.compile(r"^C\d{6}$")
PRODUCT_ID_RE = re.compile(r"^P\d{5}$")
_TS_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
_INT_RE = re.compile(r"^\d+$")


def _clean(v):
    return "" if v is None else str(v).strip()


def parse_ts(value):
    """ISO-8601 -> normalized UTC 'YYYY-MM-DDTHH:MM:SSZ'. Naive input is assumed UTC.
    Sub-second precision is truncated. Returns None if invalid."""
    s = _clean(value)
    if not _TS_PREFIX_RE.match(s):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_money_cents(value, maximum=MAX_PRICE):
    """Exact decimal -> integer cents. Rejects negatives, >2 decimals, NaN/inf."""
    s = _clean(value)
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if not d.is_finite() or d < 0 or d > maximum:
        return None
    if d.as_tuple().exponent < -2:
        return None
    return int((d * 100).to_integral_value())


def validate_order(rec):
    """Returns (clean_dict, None) or (None, (reason_code, detail)).
    Check order is fixed so the reject reason is deterministic."""
    vals = {f: _clean(rec.get(f)) for f in ORDER_FIELDS}
    missing = [f for f, v in vals.items() if v == ""]
    if missing:
        return None, (MISSING_REQUIRED_FIELD, "missing: " + ",".join(missing))
    if not ORDER_ID_RE.match(vals["order_id"]):
        return None, (INVALID_ID_FORMAT, "order_id=%r" % vals["order_id"])
    if not CUSTOMER_ID_RE.match(vals["customer_id"]):
        return None, (INVALID_ID_FORMAT, "customer_id=%r" % vals["customer_id"])
    if not PRODUCT_ID_RE.match(vals["product_id"]):
        return None, (INVALID_ID_FORMAT, "product_id=%r" % vals["product_id"])
    q = vals["quantity"]
    if not _INT_RE.match(q) or not (1 <= int(q) <= MAX_QUANTITY):
        return None, (INVALID_QUANTITY, "quantity=%r (need integer 1..%d)" % (q, MAX_QUANTITY))
    cents = parse_money_cents(vals["unit_price"])
    if cents is None:
        return None, (INVALID_PRICE, "unit_price=%r" % vals["unit_price"])
    cur = vals["currency"].upper()
    if cur not in CURRENCIES:
        return None, (INVALID_CURRENCY, "currency=%r" % vals["currency"])
    status = vals["status"].lower()
    if status not in STATUSES:
        return None, (INVALID_STATUS, "status=%r" % vals["status"])
    order_ts, updated_at = parse_ts(vals["order_ts"]), parse_ts(vals["updated_at"])
    if order_ts is None or updated_at is None:
        return None, (INVALID_TIMESTAMP, "order_ts=%r updated_at=%r" % (vals["order_ts"], vals["updated_at"]))
    if updated_at < order_ts:  # normalized strings sort chronologically
        return None, (UPDATED_BEFORE_ORDER, "updated_at %s < order_ts %s" % (updated_at, order_ts))
    quantity = int(q)
    return {
        "order_id": vals["order_id"], "customer_id": vals["customer_id"],
        "product_id": vals["product_id"], "order_ts": order_ts,
        "order_date": order_ts[:10], "quantity": quantity,
        "unit_price_cents": cents, "currency": cur, "status": status,
        "updated_at": updated_at, "line_total_cents": quantity * cents,
    }, None


def validate_customer(rec):
    v = {f: _clean(rec.get(f)) for f in CUSTOMER_FIELDS}
    missing = [f for f, x in v.items() if x == ""]
    if missing:
        return None, (MISSING_REQUIRED_FIELD, "missing: " + ",".join(missing))
    if not CUSTOMER_ID_RE.match(v["customer_id"]):
        return None, (INVALID_ID_FORMAT, "customer_id=%r" % v["customer_id"])
    if not re.match(r"^[A-Z]{2}$", v["country"]):
        return None, (INVALID_ID_FORMAT, "country=%r (need ISO-3166 alpha-2)" % v["country"])
    if len(v["signup_date"]) != 10 or parse_ts(v["signup_date"] + "T00:00:00") is None:
        return None, (INVALID_TIMESTAMP, "signup_date=%r" % v["signup_date"])
    return v, None


def validate_product(rec):
    v = {f: _clean(rec.get(f)) for f in PRODUCT_FIELDS}
    missing = [f for f, x in v.items() if x == ""]
    if missing:
        return None, (MISSING_REQUIRED_FIELD, "missing: " + ",".join(missing))
    if not PRODUCT_ID_RE.match(v["product_id"]):
        return None, (INVALID_ID_FORMAT, "product_id=%r" % v["product_id"])
    cents = parse_money_cents(v["list_price"])
    if cents is None:
        return None, (INVALID_PRICE, "list_price=%r" % v["list_price"])
    v["list_price_cents"] = cents
    return v, None


VALIDATORS = {"orders": validate_order, "customers": validate_customer, "products": validate_product}
