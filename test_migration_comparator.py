import json
import os
import tempfile

import pandas as pd

from migration_comparator import compare_table


def test_compare_table_accepts_xlsx_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_df = pd.DataFrame([
            {"id": 1, "name": "alpha", "score": 1.23},
            {"id": 2, "name": "beta", "score": 4.56},
        ])
        ch_df = pd.DataFrame([
            {"id": 1, "name": "alpha", "score": 1.23},
            {"id": 2, "name": "beta", "score": 4.56},
        ])

        sql_path = os.path.join(tmpdir, "sql.xlsx")
        ch_path = os.path.join(tmpdir, "ch.xlsx")
        sql_df.to_excel(sql_path, index=False)
        ch_df.to_excel(ch_path, index=False)

        result = compare_table(
            {
                "table_name": "test_table",
                "sql_file": "sql.xlsx",
                "ch_file": "ch.xlsx",
                "primary_keys": ["id"],
                "file_format": "xlsx",
            },
            downloads_dir=tmpdir,
            output_dir=tmpdir,
            float_tol=1e-5,
        )

        assert result["status"] == "PASSED"


def test_compare_table_can_exclude_null_primary_key_rows():
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_df = pd.DataFrame([
            {"id": 1, "name": "alpha"},
            {"id": None, "name": "incomplete sql row"},
        ])
        ch_df = pd.DataFrame([
            {"id": 1, "name": "alpha"},
            {"id": None, "name": "incomplete ClickHouse row"},
        ])

        sql_path = os.path.join(tmpdir, "sql.xlsx")
        ch_path = os.path.join(tmpdir, "ch.xlsx")
        sql_df.to_excel(sql_path, index=False)
        ch_df.to_excel(ch_path, index=False)

        result = compare_table(
            {
                "table_name": "test_table",
                "sql_file": "sql.xlsx",
                "ch_file": "ch.xlsx",
                "primary_keys": ["id"],
                "file_format": "xlsx",
                "exclude_null_primary_key_rows": True,
            },
            downloads_dir=tmpdir,
            output_dir=tmpdir,
            float_tol=1e-5,
        )

        assert result["status"] == "PASSED"
        assert any("Excluded rows with null primary-key values: SQL=1, ClickHouse=1" in warning
                   for warning in result["warnings"])
        assert os.path.exists(result["null_key_log_file"])


def test_compare_table_handles_all_clickhouse_rows_excluded_for_null_keys():
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_df = pd.DataFrame([
            {"id": 1, "name": "alpha"},
        ])
        ch_df = pd.DataFrame([
            {"id": None, "name": "incomplete ClickHouse row"},
        ])

        sql_path = os.path.join(tmpdir, "sql.xlsx")
        ch_path = os.path.join(tmpdir, "ch.xlsx")
        sql_df.to_excel(sql_path, index=False)
        ch_df.to_excel(ch_path, index=False)

        result = compare_table(
            {
                "table_name": "test_table",
                "sql_file": "sql.xlsx",
                "ch_file": "ch.xlsx",
                "primary_keys": ["id"],
                "file_format": "xlsx",
                "exclude_null_primary_key_rows": True,
            },
            downloads_dir=tmpdir,
            output_dir=tmpdir,
            float_tol=1e-5,
        )

        assert result["status"] == "FAILED"
        assert result["missing_in_ch"] == 1
        assert result["ch_null_key_rows"] == 1


def test_compare_table_can_check_row_order():
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_df = pd.DataFrame([
            {"id": 1, "name": "alpha"},
            {"id": 2, "name": "beta"},
        ])
        ch_df = pd.DataFrame([
            {"id": 2, "name": "beta"},
            {"id": 1, "name": "alpha"},
        ])

        sql_path = os.path.join(tmpdir, "sql.xlsx")
        ch_path = os.path.join(tmpdir, "ch.xlsx")
        sql_df.to_excel(sql_path, index=False)
        ch_df.to_excel(ch_path, index=False)

        result = compare_table(
            {
                "table_name": "test_table",
                "sql_file": "sql.xlsx",
                "ch_file": "ch.xlsx",
                "primary_keys": ["id"],
                "file_format": "xlsx",
                "check_row_order": True,
            },
            downloads_dir=tmpdir,
            output_dir=tmpdir,
            float_tol=1e-5,
        )

        assert result["status"] == "FAILED"
        assert result["row_order_mismatches"] == 2
        assert os.path.exists(result["report_file"])


def test_compare_table_accepts_json_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        records = [
            {"id": "1", "name": "alpha", "val": 10.5},
            {"id": "2", "name": "beta", "val": 20.0},
        ]
        sql_path = os.path.join(tmpdir, "sql.json")
        ch_path = os.path.join(tmpdir, "ch.json")

        with open(sql_path, "w", encoding="utf-8") as f:
            json.dump(records, f)
        with open(ch_path, "w", encoding="utf-8") as f:
            json.dump({"data": records, "rows": 2}, f)

        result = compare_table(
            {
                "table_name": "json_table",
                "sql_file": "sql.json",
                "ch_file": "ch.json",
                "primary_keys": ["id"],
                "file_format": "json",
            },
            downloads_dir=tmpdir,
            output_dir=tmpdir,
            float_tol=1e-5,
        )

        assert result["status"] == "PASSED"
        assert result["sql_rows"] == 2
        assert result["ch_rows"] == 2


def test_compare_table_accepts_csv_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_csv = "id,name,amount\n1,alpha,100.50\n2,beta,200.75\n"
        ch_csv = "id,name,amount\n1,alpha,100.50\n2,beta,200.75\n"

        sql_path = os.path.join(tmpdir, "sql.csv")
        ch_path = os.path.join(tmpdir, "ch.csv")

        with open(sql_path, "w", encoding="utf-8") as f:
            f.write(sql_csv)
        with open(ch_path, "w", encoding="utf-8") as f:
            f.write(ch_csv)

        result = compare_table(
            {
                "table_name": "csv_table",
                "sql_file": "sql.csv",
                "ch_file": "ch.csv",
                "primary_keys": ["id"],
                "file_format": "csv",
            },
            downloads_dir=tmpdir,
            output_dir=tmpdir,
            float_tol=1e-5,
        )

        assert result["status"] == "PASSED"
        assert result["cell_mismatches"] == 0


def test_compare_table_detects_value_mismatches():
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_csv = "id,name,amount\n1,alpha,100.50\n2,beta,200.75\n"
        ch_csv = "id,name,amount\n1,alpha,999.99\n2,beta,200.75\n"

        sql_path = os.path.join(tmpdir, "sql.csv")
        ch_path = os.path.join(tmpdir, "ch.csv")

        with open(sql_path, "w", encoding="utf-8") as f:
            f.write(sql_csv)
        with open(ch_path, "w", encoding="utf-8") as f:
            f.write(ch_csv)

        result = compare_table(
            {
                "table_name": "csv_table",
                "sql_file": "sql.csv",
                "ch_file": "ch.csv",
                "primary_keys": ["id"],
                "file_format": "csv",
            },
            downloads_dir=tmpdir,
            output_dir=tmpdir,
            float_tol=1e-5,
        )

        assert result["status"] == "FAILED"
        assert result["cell_mismatches"] == 1
        assert result["report_file"] is not None
        assert os.path.exists(result["report_file"])


if __name__ == "__main__":
    test_compare_table_accepts_xlsx_files()
    test_compare_table_can_exclude_null_primary_key_rows()
    test_compare_table_handles_all_clickhouse_rows_excluded_for_null_keys()
    test_compare_table_can_check_row_order()
    test_compare_table_accepts_json_files()
    test_compare_table_accepts_csv_files()
    test_compare_table_detects_value_mismatches()
    print("All 7 tests passed successfully!")
