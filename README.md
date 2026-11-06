# Database Migration Validator (`DB_Validator`)

[![CI](https://github.com/vruturajsinhj/DB_Validator/actions/workflows/ci.yml/badge.svg)](https://github.com/vruturajsinhj/DB_Validator/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A robust, high-performance validation runner designed to compare large-scale data dumps between source databases (e.g., PostgreSQL, MySQL, SQL Server) and target analytical databases (e.g., ClickHouse). It verifies schema consistency, record presence, composite keys, value equivalence, and row ordering across tabular dumps.

---

## Key Features

- **Multi-Format Support**: Reads **JSON** (records array or `{ "data": [...], "rows": N }`), **CSV** (UTF-8 with BOM handling), and **Excel** (`.xlsx`, `.xls`).
- **Flexible Value Normalization & Equivalence**:
  - **Floating-point tolerance**: Configurable relative and absolute numeric tolerances (e.g., `1e-5`) via `math.isclose`.
  - **Timestamps**: Robust ISO 8601 and date-string parsing via `dateutil`.
  - **NULL Representation**: Handles differences across DB export engines (`null`, `none`, `nan`, `\N`, `""`).
  - **JSON Serialization Artifacts**: Normalizes dialect differences (e.g. unclosed empty string tokens `"key":",` vs `"key":""`).
- **Composite Primary Keys**: Join and evaluate rows across any number of composite primary key columns.
- **Duplicate Record Resolution**: Configurable grouping (`count_duplicate_records: true`) to match duplicate composite-key groups record-by-record.
- **Null Primary Key Audit**: Detects and logs rows with incomplete keys, with optional exclusion (`exclude_null_primary_key_rows: true`).
- **Row Order Verification**: Optional strict sequence checking (`check_row_order: true`) for ordered tables or time-series partitions.
- **Rich Reporting**:
  - Colorized CLI terminal summary with real-time pass/fail metrics.
  - Actionable CSV discrepancy reports (`*_diff_report.csv`).
  - JSON debug traces for duplicate keys and null-key rows.

---

## Repository Structure

```text
DB_Validator/
├── .github/
│   └── workflows/
│       └── ci.yml               # GitHub Actions CI workflow (Python 3.9 - 3.13)
├── migration_comparator.py      # Core validation engine and CLI runner
├── test_migration_comparator.py # Automated test suite
├── config.example.yaml          # Sample configuration template
├── pyproject.toml               # Package metadata and CLI entry point
├── requirements.txt             # Production dependencies
├── requirements-dev.txt         # Development and testing dependencies
├── .gitignore                   # Git ignore rules for caches, reports & configs
├── .gitattributes               # LF line ending normalization
├── LICENSE                      # MIT License
└── README.md                    # Project documentation
```

---

## Getting Started

### 1. Prerequisites

- Python 3.9 or higher

### 2. Clone and Install

```bash
# Clone the repository
git clone https://github.com/<your-username>/DB_Validator.git
cd DB_Validator

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install required packages
pip install -r requirements.txt
```

Alternatively, install the project in editable mode:

```bash
pip install -e .
```

---

## Configuration

Copy the sample configuration file to `config.yaml`:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` to specify your dump directories and table configurations:

```yaml
# Folder settings (supports ~ expansion)
downloads_dir: "~/Desktop/sql_ch_dumps"
output_dir: "~/Desktop/comparison_reports"

# Comparison tolerance
float_tolerance: 1e-5

# Table definitions
tables:
  # Example 1: JSON table with composite primary keys
  - table_name: "users"
    sql_file: "users_sql.json"
    ch_file: "users_ch.json"
    primary_keys: ["id", "tenant_id"]
    file_format: "json"

  # Example 2: CSV table with duplicate counting and row order validation
  - table_name: "events_log"
    sql_file: "events_sql.csv"
    ch_file: "events_ch.csv"
    primary_keys: ["event_id", "timestamp"]
    file_format: "csv"
    count_duplicate_records: true
    check_row_order: true

  # Example 3: Excel table excluding rows with null primary keys
  - table_name: "product_catalog"
    sql_file: "products_sql.xlsx"
    ch_file: "products_ch.xlsx"
    primary_keys: ["sku"]
    file_format: "xlsx"
    exclude_null_primary_key_rows: true
    check_row_order: false
```

### Table Configuration Options

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `table_name` | string | *required* | Human-readable identifier for the table. |
| `sql_file` | string | *required* | Source SQL dump filename inside `downloads_dir`. |
| `ch_file` | string | *required* | Target ClickHouse dump filename inside `downloads_dir`. |
| `primary_keys` | list[str] | *required* | List of columns that form the composite primary key. |
| `file_format` | string | `"json"` | Dump file format (`"json"`, `"csv"`, or `"xlsx"`). |
| `count_duplicate_records` | bool | `false` | Group and compare duplicate composite keys rather than aborting. |
| `check_row_order` | bool | `false` | Verifies identical row ordering between source and target. |
| `exclude_null_primary_key_rows` | bool | `false` | Excludes and logs rows containing NULL in any primary key column. |

---

## Usage

Run with the default configuration (`config.yaml`):

```bash
python migration_comparator.py
```

Or pass a specific configuration file:

```bash
python migration_comparator.py my_custom_config.yaml
```

If installed with `pip install -e .`, you can also use the CLI command:

```bash
db-validator config.yaml
```

### Sample CLI Output

```text
================================================================================
          SQL vs CLICKHOUSE DATA MIGRATION VALIDATION RUNNER          
================================================================================
Reading files from : /path/to/sql_ch_dumps
Saving reports to  : /path/to/comparison_reports

▶ Table: events_log
--------------------------------------------------
  Overall Status       : PASSED
  SQL Row Count        : 50,000
  ClickHouse Row Count : 50,000
  Missing in ClickHouse: 0
  Extra in ClickHouse  : 0
  Row Order Mismatches : 0
  Value Mismatches     : 0
  Duplicate Group Mismatches: 0
  No diff report needed (100% matched)

================================================================================
                          FINAL RUN SUMMARY                                  
================================================================================
Tables Evaluated : 1
Tables Passed    : 1
Tables Failed    : 0
Verdict          : MIGRATION VERIFIED (100% MATCH)
```

---

## Running Tests

Run the test suite using `pytest`:

```bash
pytest -v
```

Or execute the test module directly with Python:

```bash
python test_migration_comparator.py
```

---

## Discrepancy Diff Reports

When mismatches or missing records occur, a detailed CSV diff report is generated in `output_dir` (e.g. `events_log_diff_report.csv`):

| Column | Description |
| :--- | :--- |
| `table_name` | Name of the table being validated |
| `composite_key` | Primary key identifier for the discrepancy row |
| `column_name` | Column that differed (`ALL` for missing/extra rows) |
| `sql_value` | Value present in source SQL dump |
| `ch_value` | Value present in ClickHouse dump |
| `diff_type` | `VALUE_MISMATCH`, `MISSING_IN_TARGET`, `EXTRA_IN_TARGET`, `ROW_ORDER_MISMATCH`, etc. |

---

## License

This project is licensed under the [MIT License](LICENSE).
