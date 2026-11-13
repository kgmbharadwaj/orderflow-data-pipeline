"""Streaming readers. Yield one record at a time; never hold the file in memory."""
import csv
import hashlib
import json
import os
from collections import namedtuple

from . import schema

Item = namedtuple("Item", "line raw record error")  # error = (reason, detail) | None

csv.field_size_limit(1 << 20)


class SchemaError(Exception):
    """File-level schema problem (e.g. required column missing). Nothing is loaded."""


def sha256_file(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                return h.hexdigest()
            h.update(b)


def file_format(path):
    ext = os.path.splitext(path)[1].lower()
    return "jsonl" if ext in (".jsonl", ".ndjson", ".json") else "csv"


class RecordSource:
    """Iterable of Items. `warnings` is filled once the header has been read."""

    def __init__(self, path, entity):
        self.path, self.entity = path, entity
        self.required = schema.REQUIRED[entity]
        self.fmt = file_format(path)
        self.warnings = []

    def __iter__(self):
        return self._csv() if self.fmt == "csv" else self._jsonl()

    def _csv(self):
        with open(self.path, newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                self.warnings.append("empty file (no header)")
                return
            header = [h.strip().lower() for h in header]
            missing = [c for c in self.required if c not in header]
            if missing:
                raise SchemaError("missing required columns: %s (found: %s)" % (missing, header))
            extra = [c for c in header if c not in self.required]
            if extra:
                self.warnings.append("unexpected extra columns ignored: %s" % extra)
            while True:
                try:
                    row = next(reader)
                except StopIteration:
                    return
                except csv.Error as e:
                    yield Item(reader.line_num, "", None, (schema.MALFORMED_ROW, "csv error: %s" % e))
                    continue
                if not row:
                    continue  # blank line
                raw = ",".join(row)[:2000]
                if len(row) != len(header):
                    yield Item(reader.line_num, raw, None, (schema.MALFORMED_ROW,
                               "expected %d fields, got %d" % (len(header), len(row))))
                else:
                    yield Item(reader.line_num, raw, dict(zip(header, row)), None)

    def _jsonl(self):
        with open(self.path, encoding="utf-8-sig", errors="replace") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError as e:
                    yield Item(n, line[:2000], None, (schema.MALFORMED_ROW, "invalid JSON: %s" % e))
                    continue
                if not isinstance(obj, dict):
                    yield Item(n, line[:2000], None, (schema.MALFORMED_ROW, "JSON value is not an object"))
                    continue
                obj = {str(k).strip().lower(): v for k, v in obj.items()}
                yield Item(n, line[:2000], obj, None)


def count_records(path, entity):
    """Independent record count used for reconciliation."""
    return sum(1 for _ in RecordSource(path, entity))
