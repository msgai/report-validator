import os
import sys
import csv
import json
import math
import re
import yaml
import pandas as pd
from dateutil import parser as date_parser
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)

# Regex to detect date/timestamp-like strings
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?$')

# Regex to detect truncated/unclosed empty JSON keys like "tone":", or "tone":""
JSON_EMPTY_KEY_PATTERN = re.compile(r'"([a-zA-Z0-9_]+)"\s*:\s*"\s*(?=[,}])')

# String representations of NULL / Empty
NULL_VALUES = {"null", "none", "nan", "\\n", ""}

def is_null(val: str) -> bool:
    """Check if a string represents NULL / Empty."""
    if val is None:
        return True
    return str(val).strip().lower() in NULL_VALUES

def normalize_key_value(val: str) -> str:
    """Normalize primary key parts so NULL and \\N produce the identical string."""
    if is_null(val):
        return "<NULL>"
    return str(val).strip()

def normalize_json_empty_keys(s: str) -> str:
    """
    Normalizes serialization differences in JSON:
    Transforms SQL's unclosed empty quote:    "tone":",
    Into standard closed empty string:       "tone":"",
    """
    if not s or not ('{"' in s or '["' in s):
        return s
    return JSON_EMPTY_KEY_PATTERN.sub(r'"\1":""', s)

class DataReadError(Exception):
    """Raised when a JSON dump cannot be read without ambiguity."""


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_json_dataframe(filepath: str) -> pd.DataFrame:
    try:
        with open(filepath, "r", encoding="utf-8") as source:
            payload = json.load(source, object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise DataReadError(f"Unable to read JSON dump '{filepath}': {exc}") from exc

    metadata = None
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        records = payload["data"]
        metadata = payload.get("meta")
        declared_rows = payload.get("rows")
        if declared_rows is not None and declared_rows != len(records):
            raise DataReadError(
                f"JSON dump '{filepath}' declares {declared_rows} rows but contains {len(records)}."
            )
    else:
        raise DataReadError(
            f"JSON dump '{filepath}' must be an array of row objects or an object with a data array."
        )

    if any(not isinstance(record, dict) for record in records):
        raise DataReadError(f"JSON dump '{filepath}' contains a non-object data record.")

    if records:
        columns = list(records[0])
        expected_columns = set(columns)
        for record_number, record in enumerate(records, start=1):
            if set(record) != expected_columns:
                raise DataReadError(
                    f"JSON record {record_number} in '{filepath}' has a different set of columns."
                )
    elif isinstance(metadata, list):
        columns = [item.get("name") for item in metadata if isinstance(item, dict)]
    else:
        raise DataReadError(f"JSON dump '{filepath}' contains no records or column metadata.")

    if metadata is not None:
        metadata_columns = [item.get("name") for item in metadata if isinstance(item, dict)]
        if metadata_columns and metadata_columns != columns:
            raise DataReadError(f"JSON metadata columns do not match row columns in '{filepath}'.")

    def stringify(value):
        if value is None:
            return "null"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(value, bool):
            return json.dumps(value)
        return str(value)

    return pd.DataFrame(
        [{column: stringify(record[column]) for column in columns} for record in records],
        columns=columns,
        dtype=str
    )


def load_csv_dataframe(filepath: str) -> pd.DataFrame:
    try:
        return pd.read_csv(
            filepath,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig"
        )
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError) as exc:
        raise DataReadError(f"Unable to read CSV dump '{filepath}': {exc}") from exc


def load_excel_dataframe(filepath: str) -> pd.DataFrame:
    try:
        return pd.read_excel(
            filepath,
            dtype=str,
            keep_default_na=False,
        )
    except ImportError as exc:
        raise DataReadError(
            f"Unable to read Excel dump '{filepath}': openpyxl is required. "
            "Install it with 'pip install openpyxl'."
        ) from exc
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise DataReadError(f"Unable to read Excel dump '{filepath}': {exc}") from exc


def are_values_equivalent(val_sql: str, val_ch: str, float_tol: float = 1e-5) -> bool:
    # 1. NULL check
    sql_is_null = is_null(val_sql)
    ch_is_null = is_null(val_ch)
    if sql_is_null and ch_is_null:
        return True
    if sql_is_null != ch_is_null:
        return False

    s_sql = str(val_sql).strip()
    s_ch = str(val_ch).strip()

    # Fast path: exact string match
    if s_sql == s_ch:
        return True

    # 2. Float / Numeric check
    try:
        f_sql = float(s_sql)
        f_ch = float(s_ch)
        if math.isclose(f_sql, f_ch, rel_tol=float_tol, abs_tol=float_tol):
            return True
    except (ValueError, TypeError):
        pass

    # 3. Timestamp check
    if DATE_PATTERN.match(s_sql) and DATE_PATTERN.match(s_ch):
        try:
            dt_sql = date_parser.parse(s_sql)
            dt_ch = date_parser.parse(s_ch)
            if dt_sql == dt_ch:
                return True
        except (ValueError, TypeError, OverflowError):
            pass

    # 4. JSON Empty Key Escaping Normalization ("tone":", vs "tone":"")
    if ('":' in s_sql and '":' in s_ch):
        norm_sql = normalize_json_empty_keys(s_sql)
        norm_ch = normalize_json_empty_keys(s_ch)
        if norm_sql == norm_ch:
            return True

    # 5. Strict raw text fallback
    return False


def write_duplicate_key_log(
    table_name: str,
    primary_keys: list,
    df_sql: pd.DataFrame,
    df_ch: pd.DataFrame,
    output_dir: str
) -> str:
    duplicate_groups = {"SQL": [], "ClickHouse": []}
    for source_name, dataframe in (("SQL", df_sql), ("ClickHouse", df_ch)):
        duplicate_rows = dataframe[dataframe.duplicated(subset=["__pk__"], keep=False)]
        for composite_key, group in duplicate_rows.groupby("__pk__", sort=True):
            records = group.drop(columns=["__pk__"]).to_dict(orient="records")
            duplicate_groups[source_name].append({
                "composite_key": composite_key,
                "row_count": len(records),
                "records": records
            })

    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, f"{table_name}_duplicate_key_debug.json")
    with open(log_path, "w", encoding="utf-8") as log_file:
        json.dump({
            "table_name": table_name,
            "primary_keys": primary_keys,
            "duplicate_groups": duplicate_groups
        }, log_file, ensure_ascii=False, indent=2)
    return log_path


def write_null_primary_key_log(
    table_name: str,
    primary_keys: list,
    df_sql: pd.DataFrame,
    df_ch: pd.DataFrame,
    output_dir: str
) -> str:
    null_key_rows = {"SQL": [], "ClickHouse": []}
    for source_name, dataframe in (("SQL", df_sql), ("ClickHouse", df_ch)):
        null_mask = dataframe[primary_keys].apply(lambda column: column.map(is_null)).any(axis=1)
        for row_index, row in dataframe.loc[null_mask].iterrows():
            null_columns = [column for column in primary_keys if is_null(row[column])]
            null_key_rows[source_name].append({
                "row_index": int(row_index),
                "null_primary_key_columns": null_columns,
                "record": row.to_dict()
            })

    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, f"{table_name}_null_primary_key_debug.json")
    with open(log_path, "w", encoding="utf-8") as log_file:
        json.dump({
            "table_name": table_name,
            "primary_keys": primary_keys,
            "null_key_row_counts": {
                source_name: len(rows) for source_name, rows in null_key_rows.items()
            },
            "rows": null_key_rows
        }, log_file, ensure_ascii=False, indent=2)
    return log_path


def duplicate_group_matches(sql_rows, ch_rows, columns: list, float_tol: float) -> bool:
    if len(sql_rows) != len(ch_rows):
        return False

    unmatched_ch_rows = list(ch_rows)
    for sql_row in sql_rows:
        matching_index = next(
            (
                index for index, ch_row in enumerate(unmatched_ch_rows)
                if all(
                    are_values_equivalent(sql_row[column], ch_row[column], float_tol)
                    for column in columns
                )
            ),
            None
        )
        if matching_index is None:
            return False
        unmatched_ch_rows.pop(matching_index)

    return not unmatched_ch_rows


def compare_table(
    table_cfg: dict,
    downloads_dir: str,
    output_dir: str,
    float_tol: float,
    count_duplicate_records: bool = False
) -> dict:
    table_name = table_cfg["table_name"]
    sql_file_path = os.path.join(downloads_dir, table_cfg["sql_file"])
    ch_file_path = os.path.join(downloads_dir, table_cfg["ch_file"])
    pk_cols = table_cfg["primary_keys"]
    file_format = str(table_cfg.get("file_format", "json")).lower()
    file_format_aliases = {
        "json": "json",
        "csv": "csv",
        "xlsx": "xlsx",
        "xls": "xls",
        "excel": "xlsx",
    }
    file_format = file_format_aliases.get(file_format, file_format)

    summary = {
        "table_name": table_name,
        "status": "PASSED",
        "sql_rows": 0,
        "ch_rows": 0,
        "warnings": [],
        "sql_null_key_rows": 0,
        "ch_null_key_rows": 0,
        "missing_in_ch": 0,
        "extra_in_ch": 0,
        "row_order_checked": bool(table_cfg.get("check_row_order", False)),
        "row_order_mismatches": 0,
        "cell_mismatches": 0,
        "duplicate_group_mismatches": 0,
        "report_file": None,
        "debug_log_file": None,
        "null_key_log_file": None
    }

    if not os.path.exists(sql_file_path):
        summary["status"] = "ERROR"
        summary["warnings"].append(f"SQL file not found: {sql_file_path}")
        return summary
    if not os.path.exists(ch_file_path):
        summary["status"] = "ERROR"
        summary["warnings"].append(f"ClickHouse file not found: {ch_file_path}")
        return summary
    if file_format not in {"json", "csv", "xlsx", "xls"}:
        summary["status"] = "ERROR"
        summary["warnings"].append(
            f"Unsupported file_format '{file_format}'. Use 'json', 'csv', 'xlsx', or 'xls'."
        )
        return summary

    expected_extensions = {
        "json": ".json",
        "csv": ".csv",
        "xlsx": ".xlsx",
        "xls": ".xls",
    }
    expected_extension = expected_extensions[file_format]
    if not sql_file_path.lower().endswith(expected_extension) or not ch_file_path.lower().endswith(expected_extension):
        summary["status"] = "ERROR"
        summary["warnings"].append(
            f"Both SQL and ClickHouse dump files must be {file_format.upper()} files."
        )
        return summary

    try:
        if file_format == "json":
            dataframe_loader = load_json_dataframe
        elif file_format == "csv":
            dataframe_loader = load_csv_dataframe
        else:
            dataframe_loader = load_excel_dataframe
        df_sql = dataframe_loader(sql_file_path)
        sql_headers = list(df_sql.columns)
        df_ch = dataframe_loader(ch_file_path)
    except DataReadError as exc:
        summary["status"] = "ERROR"
        summary["warnings"].append(str(exc))
        return summary

    summary["sql_rows"] = len(df_sql)
    summary["ch_rows"] = len(df_ch)

    discrepancies = []

    ch_headers = list(df_ch.columns)
    missing_in_target_cols = set(sql_headers) - set(ch_headers)
    extra_in_target_cols = set(ch_headers) - set(sql_headers)
    if missing_in_target_cols:
        summary["warnings"].append(
            f"Columns missing in ClickHouse: {sorted(missing_in_target_cols)}"
        )
        for column in sorted(missing_in_target_cols):
            discrepancies.append({
                "table_name": table_name,
                "composite_key": "",
                "column_name": column,
                "sql_value": "COLUMN PRESENT",
                "ch_value": "COLUMN MISSING",
                "diff_type": "COLUMN_MISSING_IN_TARGET"
            })
    if extra_in_target_cols:
        summary["warnings"].append(
            f"Ignoring extra columns in ClickHouse: {sorted(extra_in_target_cols)}"
        )

    for pk in pk_cols:
        if pk not in sql_headers:
            summary["status"] = "FAILED"
            summary["warnings"].append(f"Key '{pk}' missing from SQL table!")
            return summary
        if pk not in ch_headers:
            summary["status"] = "FAILED"
            summary["warnings"].append(f"Key '{pk}' missing from ClickHouse table!")
            return summary

    # Normalize Key Columns BEFORE building composite key
    sql_null_key_mask = df_sql[pk_cols].apply(lambda column: column.map(is_null)).any(axis=1)
    ch_null_key_mask = df_ch[pk_cols].apply(lambda column: column.map(is_null)).any(axis=1)
    sql_null_key_rows = int(sql_null_key_mask.sum())
    ch_null_key_rows = int(ch_null_key_mask.sum())
    summary["sql_null_key_rows"] = sql_null_key_rows
    summary["ch_null_key_rows"] = ch_null_key_rows
    if sql_null_key_rows or ch_null_key_rows:
        try:
            summary["null_key_log_file"] = write_null_primary_key_log(
                table_name, pk_cols, df_sql, df_ch, output_dir
            )
        except OSError as exc:
            summary["warnings"].append(f"Unable to write null-primary-key debug log: {exc}")

        if not table_cfg.get("exclude_null_primary_key_rows", False):
            summary["status"] = "ERROR"
            summary["warnings"].append(
                f"Rows with null primary-key values: SQL={sql_null_key_rows}, "
                f"ClickHouse={ch_null_key_rows}. Comparison aborted."
            )
            return summary

        summary["warnings"].append(
            f"Excluded rows with null primary-key values: SQL={sql_null_key_rows}, "
            f"ClickHouse={ch_null_key_rows}. Comparing remaining rows only."
        )
        df_sql = df_sql.loc[~sql_null_key_mask].copy()
        df_ch = df_ch.loc[~ch_null_key_mask].copy()
        if df_sql.empty and df_ch.empty:
            summary["status"] = "ERROR"
            summary["warnings"].append(
                "No rows with valid primary keys remain; comparison was not performed."
            )
            return summary

    df_sql_keys = pd.DataFrame()
    df_ch_keys = pd.DataFrame()
    for pk in pk_cols:
        df_sql_keys[pk] = df_sql[pk].apply(normalize_key_value)
        df_ch_keys[pk] = df_ch[pk].apply(normalize_key_value)

    df_sql["__pk__"] = [
        " || ".join(values)
        for values in df_sql_keys[pk_cols].itertuples(index=False, name=None)
    ]
    df_ch["__pk__"] = [
        " || ".join(values)
        for values in df_ch_keys[pk_cols].itertuples(index=False, name=None)
    ]
    display_keys = {}
    for dataframe, key_values in ((df_sql, df_sql_keys), (df_ch, df_ch_keys)):
        for composite_key, values in zip(dataframe["__pk__"], key_values[pk_cols].itertuples(index=False, name=None)):
            display_keys[composite_key] = " || ".join(
                f"{column}={value}" for column, value in zip(pk_cols, values)
            )

    if summary["row_order_checked"]:
        sql_order = list(df_sql["__pk__"])
        ch_order = list(df_ch["__pk__"])
        for row_index in range(max(len(sql_order), len(ch_order))):
            sql_key = sql_order[row_index] if row_index < len(sql_order) else None
            ch_key = ch_order[row_index] if row_index < len(ch_order) else None
            if sql_key != ch_key:
                summary["row_order_mismatches"] += 1
                discrepancies.append({
                    "table_name": table_name,
                    "composite_key": f"row_position={row_index + 1}",
                    "column_name": "ROW_ORDER",
                    "sql_value": display_keys.get(sql_key, "<NO ROW>"),
                    "ch_value": display_keys.get(ch_key, "<NO ROW>"),
                    "diff_type": "ROW_ORDER_MISMATCH"
                })
        if summary["row_order_mismatches"]:
            summary["warnings"].append(
                f"Row order mismatches: {summary['row_order_mismatches']} positions differ. "
                "See the diff report for SQL and ClickHouse primary-key order."
            )

    common_cols = [c for c in sql_headers if c in ch_headers and c not in pk_cols and c != "__pk__"]
    sql_duplicate_mask = df_sql.duplicated(subset=["__pk__"], keep=False)
    ch_duplicate_mask = df_ch.duplicated(subset=["__pk__"], keep=False)
    sql_duplicate_keys = set(df_sql.loc[sql_duplicate_mask, "__pk__"])
    ch_duplicate_keys = set(df_ch.loc[ch_duplicate_mask, "__pk__"])
    duplicate_keys = sql_duplicate_keys | ch_duplicate_keys
    has_duplicate_keys = bool(duplicate_keys)

    if has_duplicate_keys and not count_duplicate_records:
        summary["status"] = "ERROR"
        summary["warnings"].append(
            "Duplicate composite primary keys detected. Comparison aborted. "
            "Set count_duplicate_records: true to compare duplicate records."
        )
        return summary

    if has_duplicate_keys:
        sql_dupe_rows = int(sql_duplicate_mask.sum())
        ch_dupe_rows = int(ch_duplicate_mask.sum())
        summary["warnings"].append(
            f"Duplicate composite primary keys detected: SQL={sql_dupe_rows} rows, "
            f"ClickHouse={ch_dupe_rows} rows. Comparing duplicate groups record-by-record."
        )
        try:
            summary["debug_log_file"] = write_duplicate_key_log(
                table_name, pk_cols, df_sql, df_ch, output_dir
            )
        except OSError as exc:
            summary["warnings"].append(f"Unable to write duplicate-key debug log: {exc}")

        for composite_key in sorted(duplicate_keys):
            sql_group = df_sql.loc[df_sql["__pk__"] == composite_key]
            ch_group = df_ch.loc[df_ch["__pk__"] == composite_key]
            sql_records = sql_group.drop(columns=["__pk__"]).to_dict(orient="records")
            ch_records = ch_group.drop(columns=["__pk__"]).to_dict(orient="records")
            if not duplicate_group_matches(sql_records, ch_records, common_cols, float_tol):
                summary["duplicate_group_mismatches"] += 1
                discrepancies.append({
                    "table_name": table_name,
                    "composite_key": display_keys[composite_key],
                    "column_name": "DUPLICATE_GROUP",
                    "sql_value": {"row_count": len(sql_records), "records": sql_records},
                    "ch_value": {"row_count": len(ch_records), "records": ch_records},
                    "diff_type": "DUPLICATE_GROUP_MISMATCH"
                })

        if summary["duplicate_group_mismatches"]:
            summary["warnings"].append(
                f"Duplicate groups with count or value differences: "
                f"{summary['duplicate_group_mismatches']}"
            )
        else:
            summary["warnings"].append(
                f"All {len(duplicate_keys)} duplicate-key groups have matching row counts "
                "and values on both sides."
            )

        df_sql = df_sql.loc[~df_sql["__pk__"].isin(duplicate_keys)].copy()
        df_ch = df_ch.loc[~df_ch["__pk__"].isin(duplicate_keys)].copy()

    df_sql = df_sql.set_index("__pk__")
    df_ch = df_ch.set_index("__pk__")

    sql_keys = set(df_sql.index)
    ch_keys = set(df_ch.index)

    missing_keys = sql_keys - ch_keys
    extra_keys = ch_keys - sql_keys
    common_keys = sql_keys & ch_keys

    summary["missing_in_ch"] = len(missing_keys)
    summary["extra_in_ch"] = len(extra_keys)

    for k in missing_keys:
        discrepancies.append({
            "table_name": table_name,
            "composite_key": display_keys[k],
            "column_name": "ALL",
            "sql_value": "EXISTS",
            "ch_value": "MISSING",
            "diff_type": "MISSING_IN_TARGET"
        })

    for k in extra_keys:
        discrepancies.append({
            "table_name": table_name,
            "composite_key": display_keys[k],
            "column_name": "ALL",
            "sql_value": "MISSING",
            "ch_value": "EXISTS",
            "diff_type": "EXTRA_IN_TARGET"
        })

    # Row & Column Value Comparison
    if common_keys:
        aligned_sql = df_sql.loc[list(common_keys)]
        aligned_ch = df_ch.loc[list(common_keys)]

        for col in common_cols:
            s_sql = aligned_sql[col]
            s_ch = aligned_ch[col]

            candidate_mask = s_sql != s_ch
            candidates = candidate_mask[candidate_mask].index

            for pk_val in candidates:
                val_s = s_sql.at[pk_val]
                val_c = s_ch.at[pk_val]

                if not are_values_equivalent(val_s, val_c, float_tol):
                    summary["cell_mismatches"] += 1
                    discrepancies.append({
                        "table_name": table_name,
                        "composite_key": display_keys[pk_val],
                        "column_name": col,
                        "sql_value": val_s,
                        "ch_value": val_c,
                        "diff_type": "VALUE_MISMATCH"
                    })

    if (
        summary["missing_in_ch"] > 0
        or summary["extra_in_ch"] > 0
        or summary["row_order_mismatches"] > 0
        or summary["cell_mismatches"] > 0
        or summary["duplicate_group_mismatches"] > 0
    ):
        summary["status"] = "FAILED"
    elif missing_in_target_cols:
        summary["status"] = "FAILED"

    if discrepancies:
        os.makedirs(output_dir, exist_ok=True)
        report_file = os.path.join(output_dir, f"{table_name}_diff_report.csv")
        with open(report_file, "w", encoding="utf-8", newline="") as report:
            writer = csv.DictWriter(report, fieldnames=discrepancies[0].keys())
            writer.writeheader()
            writer.writerows(discrepancies)
        summary["report_file"] = report_file

    return summary

def print_header():
    print(Fore.CYAN + Style.BRIGHT + "=" * 80)
    print(Fore.CYAN + Style.BRIGHT + "          SQL vs CLICKHOUSE DATA MIGRATION VALIDATION RUNNER          ")
    print(Fore.CYAN + Style.BRIGHT + "=" * 80)

def print_table_summary(s: dict):
    print(f"\n{Fore.MAGENTA + Style.BRIGHT}▶ Table: {s['table_name']}")
    print("-" * 50)

    status_color = Fore.GREEN if s["status"] == "PASSED" else Fore.RED
    print(f"  Overall Status       : {status_color + Style.BRIGHT}{s['status']}")
    print(f"  SQL Row Count        : {Fore.WHITE}{s['sql_rows']:,}")
    print(f"  ClickHouse Row Count : {Fore.WHITE}{s['ch_rows']:,}")
    if s.get("sql_null_key_rows") or s.get("ch_null_key_rows"):
        print(
            f"  Excluded Null-Key Rows: SQL={s['sql_null_key_rows']:,}, "
            f"ClickHouse={s['ch_null_key_rows']:,}"
        )

    missing_color = Fore.RED if s["missing_in_ch"] > 0 else Fore.GREEN
    extra_color = Fore.YELLOW if s["extra_in_ch"] > 0 else Fore.GREEN
    cell_color = Fore.RED if s["cell_mismatches"] > 0 else Fore.GREEN

    print(f"  Missing in ClickHouse: {missing_color}{s['missing_in_ch']:,}")
    print(f"  Extra in ClickHouse  : {extra_color}{s['extra_in_ch']:,}")
    if s.get("row_order_checked"):
        order_color = Fore.RED if s["row_order_mismatches"] else Fore.GREEN
        print(f"  Row Order Mismatches : {order_color}{s['row_order_mismatches']:,}")
    print(f"  Value Mismatches     : {cell_color}{s['cell_mismatches']:,}")
    print(f"  Duplicate Group Mismatches: {Fore.RED if s['duplicate_group_mismatches'] else Fore.GREEN}{s['duplicate_group_mismatches']:,}")

    if s["warnings"]:
        print(f"  {Fore.YELLOW}Notes / Schema Warnings:")
        for w in s["warnings"]:
            print(f"    - {Fore.YELLOW}{w}")

    if s["report_file"]:
        print(f"  {Fore.CYAN}Diff Report Generated: {Fore.LIGHTBLUE_EX}{s['report_file']}")
    elif s["status"] == "PASSED":
        print(f"  {Fore.GREEN}No diff report needed (100% matched)")
    else:
        print(f"  {Fore.YELLOW}No diff report generated")

    if s.get("debug_log_file"):
        print(f"  {Fore.CYAN}Duplicate-key Debug Log: {Fore.LIGHTBLUE_EX}{s['debug_log_file']}")
    if s.get("null_key_log_file"):
        print(f"  {Fore.CYAN}Null-primary-key Debug Log: {Fore.LIGHTBLUE_EX}{s['null_key_log_file']}")

def main():
    config_path = "config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    if not os.path.exists(config_path):
        print(Fore.RED + f"Config file '{config_path}' not found!")
        if config_path == "config.yaml" and os.path.exists("config.example.yaml"):
            print(Fore.YELLOW + "Tip: Copy 'config.example.yaml' to 'config.yaml' to get started:\n  cp config.example.yaml config.yaml")
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    downloads_dir = os.path.expanduser(config.get("downloads_dir", "~/Downloads"))
    output_dir = os.path.expanduser(config.get("output_dir", "~/Downloads/comparison_reports"))
    float_tol = float(config.get("float_tolerance", 1e-5))

    print_header()
    print(f"{Fore.BLUE}Reading files from : {Fore.WHITE}{downloads_dir}")
    print(f"{Fore.BLUE}Saving reports to  : {Fore.WHITE}{output_dir}\n")

    all_summaries = []
    tables = config.get("tables", [])

    for table_cfg in tables:
        summary = compare_table(
            table_cfg,
            downloads_dir,
            output_dir,
            float_tol,
            count_duplicate_records=bool(table_cfg.get("count_duplicate_records", False))
        )
        all_summaries.append(summary)
        print_table_summary(summary)

    print("\n" + Fore.CYAN + Style.BRIGHT + "=" * 80)
    print(Fore.CYAN + Style.BRIGHT + "                          FINAL RUN SUMMARY                                  ")
    print(Fore.CYAN + Style.BRIGHT + "=" * 80)

    passed_tables = sum(1 for s in all_summaries if s["status"] == "PASSED")
    total_tables = len(all_summaries)

    summary_color = Fore.GREEN if passed_tables == total_tables else Fore.RED
    print(f"Tables Evaluated : {total_tables}")
    print(f"Tables Passed    : {Fore.GREEN}{passed_tables}")
    print(f"Tables Failed    : {Fore.RED}{total_tables - passed_tables}")
    null_key_rows_excluded = any(
        s.get("sql_null_key_rows", 0) or s.get("ch_null_key_rows", 0)
        for s in all_summaries
    )
    if passed_tables != total_tables:
        verdict = "MIGRATION INCONSISTENCIES DETECTED"
    elif null_key_rows_excluded:
        verdict = "VALID-KEY ROWS MATCH (NULL-KEY ROWS EXCLUDED)"
    else:
        verdict = "MIGRATION VERIFIED (100% MATCH)"
    print(f"Verdict          : {summary_color + Style.BRIGHT}{verdict}\n")

if __name__ == "__main__":
    main()