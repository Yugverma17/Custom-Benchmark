import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Dict

import duckdb


@dataclass
class ColumnStats:
    data_type: str
    quantiles: list


@dataclass
class TableStats:
    total_rows: int
    columns: Dict[str, ColumnStats]


def list_tables(con):
    query = "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE';"
    return [row[0] for row in con.execute(query).fetchall()]


def get_table_columns(con, table_name: str):
    query = f"DESCRIBE {table_name};"
    return [(row[0], row[1]) for row in con.execute(query).fetchall()]


def get_table_row_count(con, table_name: str):
    query = f"SELECT COUNT(*) FROM {table_name};"
    return con.execute(query).fetchone()[0]


SAMPLE_ROW_THRESHOLD = 2_000_000
SAMPLE_SIZE          = 1_000_000

def _source_expr(table_name: str, row_count: int) -> str:
    """For large tables, quantile computation reads from a bounded random
    sample instead of the full table. Sorting/ordering 10s of millions of
    rows (especially for string-column quantiles) can consume tens of GB
    of memory since DuckDB's ORDER BY / window-function spill isn't capped
    tightly by SET memory_limit. A 1M-row sample gives statistically
    equivalent quantiles for predicate-generation purposes at a bounded,
    safe memory cost."""
    if row_count > SAMPLE_ROW_THRESHOLD:
        return f"(SELECT * FROM {table_name} USING SAMPLE {SAMPLE_SIZE} ROWS)"
    return table_name


def get_column_statistics(con, table_name: str, column_name: str, data_type: str, row_count: int = 0):
    quantiles = [i / 100 for i in range(1, 101)]
    labels = ["q_0"] + [f"q_{i}" for i in range(100)] + ["q_100"]
    source = _source_expr(table_name, row_count)

    if any(t in data_type for t in ("INTEGER", "BIGINT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC")):
        quantile_queries = [
            f"PERCENTILE_CONT({p}) WITHIN GROUP (ORDER BY {column_name}) AS q_{i}"
            for i, p in enumerate(quantiles)
        ]
        query = f"""
            SELECT
                MIN({column_name}) AS min_value,
                {", ".join(quantile_queries)},
                MAX({column_name}) AS max_value
            FROM {source};
        """
        result = con.execute(query).fetchone()
        assert result is not None
        stats = dict(zip(labels, result))

    elif any(t in data_type for t in ("VARCHAR", "TEXT", "STRING")):
        query = f"""
            WITH ordered_strings AS (
                SELECT {column_name}, ROW_NUMBER() OVER (ORDER BY {column_name}) AS row_num, COUNT(*) OVER () AS total_rows
                FROM {source}
            )
            SELECT
                MIN({column_name}) AS min_value,
                {", ".join([f"MAX(CASE WHEN row_num = CAST(total_rows * {p} AS INTEGER) THEN {column_name} END) AS q_{i}" for i, p in enumerate(quantiles)])},
                MAX({column_name}) AS max_value
            FROM ordered_strings;
        """
        result = con.execute(query).fetchone()
        result_list = list(result)
        for i in range(len(result_list)):
            if result_list[i] is None and i + 1 < len(result_list):
                for j in range(i + 1, len(result_list)):
                    if result_list[j] is not None:
                        result_list[i] = result_list[j]
                        break
        stats = dict(zip(labels, result_list))

    elif any(t in data_type for t in ("DATE", "TIMESTAMP", "TIME")):
        quantile_queries = [
            f"PERCENTILE_CONT({p}) WITHIN GROUP (ORDER BY {column_name}) AS q_{i}"
            for i, p in enumerate(quantiles)
        ]
        query = f"""
            SELECT
                MIN({column_name}) AS min_date,
                {", ".join(quantile_queries)},
                MAX({column_name}) AS max_date
            FROM {source};
        """
        result = con.execute(query).fetchone()
        stats = dict(zip(labels, result))

    else:
        return None

    return convert_to_serializable(stats)


def convert_to_serializable(stats) -> Dict[str, any]:
    quantiles = [value for key, value in stats.items() if key.startswith("q_")]
    stats_without_quantiles = {key: value for key, value in stats.items() if not key.startswith("q_")}

    serializable_quantiles = [
        (float(value) if isinstance(value, Decimal) else
         value.isoformat() if isinstance(value, (date, datetime)) else
         value if value is not None else None)
        for value in quantiles
    ]

    return {
        **{
            key: (float(value) if isinstance(value, Decimal) else
                  value.isoformat() if isinstance(value, (date, datetime)) else
                  value if value is not None else None)
            for key, value in stats_without_quantiles.items()
        },
        "quantiles": serializable_quantiles,
    }


def create_quantiles(db_path: str, output_file: str, force: bool = False):
    if force and os.path.exists(output_file):
        os.remove(output_file)

    all_stats = {}
    if os.path.exists(output_file):
        with open(output_file) as f:
            all_stats = json.load(f)

    con = duckdb.connect(db_path, read_only=True)
    tables = list_tables(con)

    for table in tables:
        if table in all_stats:
            print(f"  Skipping {table} (already computed).")
            continue

        print(f"  Computing stats for {table} ...", flush=True)
        row_count = get_table_row_count(con, table)
        columns = get_table_columns(con, table)
        table_stats = TableStats(total_rows=row_count, columns={})

        import concurrent.futures

        def process_column(args):
            col_name, col_type = args
            dd_con = duckdb.connect(db_path, read_only=True)
            dd_con.execute("SET threads=2; SET memory_limit='4GB'")
            stats = get_column_statistics(dd_con, table, col_name, col_type, row_count)
            dd_con.close()
            if stats:
                return col_name, ColumnStats(data_type=col_type, **stats)
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(process_column, columns))

        for result in results:
            if result:
                col_name, col_stats = result
                table_stats.columns[col_name] = col_stats

        if table_stats.columns:
            all_stats[table] = asdict(table_stats)
            with open(output_file, "w") as f:
                json.dump(all_stats, f, indent=2)
            print(f"  Saved progress after {table} -> {output_file}", flush=True)

    con.close()
    print(f"Stats written to: {output_file}")
