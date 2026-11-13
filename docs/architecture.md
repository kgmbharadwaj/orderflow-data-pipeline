# OrderFlow – System Architecture

## 1. Overview
OrderFlow is a Python-based, SQLite-powered data pipeline designed to process large-scale order event datasets.

## 2. Architecture Components

1. Data Generator – Generates sample customer, product and order data.
2. Data Readers – Reads CSV and JSONL files.
3. Data Validator – Validates incoming records.
4. Data Loader – Loads valid records into SQLite.
5. Database – Stores processed order data.
6. Reconciliation – Compares actual results with expected results.
7. Reporting – Generates pipeline execution reports.

## 3. Data Flow

Input Files → Readers → Validation → SQLite Loader → Reconciliation → Reports

## 4. Performance
- Supports chunk-based processing.
- Supports large datasets.
- Handles late-arriving events.
- Supports resumable loading.

## 5. Technology Stack
- Python
- SQLite
- CSV / JSONL
- Git and GitHub