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


def get_column_statistics(con, table_name: str, column_name: str, data_type: str):
    quantiles = [i / 100 for i in range(1, 101)]
    labels = ["q_0"] + [f"q_{i}" for i in range(100)] + ["q_100"]

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
            FROM {table_name};
        """
        result = con.execute(query).fetchone()
        assert result is not None
        stats = dict(zip(labels, result))

    elif any(t in data_type for t in ("VARCHAR", "TEXT", "STRING")):
        query = f"""
            WITH ordered_strings AS (
                SELECT {column_name}, ROW_NUMBER() OVER (ORDER BY {column_name}) AS row_num, COUNT(*) OVER () AS total_rows
                FROM {table_name}
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
            FROM {table_name};
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
    if not force and os.path.exists(output_file):
        print(f"Stats file already exists: {output_file}. Skipping.")
        return

    con = duckdb.connect(db_path, read_only=True)
    tables = list_tables(con)
    all_stats = {}

    for table in tables:
        print(f"  Computing stats for {table} ...")
        row_count = get_table_row_count(con, table)
        columns = get_table_columns(con, table)
        table_stats = TableStats(total_rows=row_count, columns={})

        import concurrent.futures

        def process_column(args):
            col_name, col_type = args
            dd_con = duckdb.connect(db_path, read_only=True)
            stats = get_column_statistics(dd_con, table, col_name, col_type)
            dd_con.close()
            if stats:
                return col_name, ColumnStats(data_type=col_type, **stats)
            return None

        with concurrent.futures.ThreadPoolExecutor() as executor:
            results = list(executor.map(process_column, columns))

        for result in results:
            if result:
                col_name, col_stats = result
                table_stats.columns[col_name] = col_stats

        if table_stats.columns:
            all_stats[table] = asdict(table_stats)

    con.close()

    with open(output_file, "w") as f:
        json.dump(all_stats, f, indent=2)

    print(f"Stats written to: {output_file}")
