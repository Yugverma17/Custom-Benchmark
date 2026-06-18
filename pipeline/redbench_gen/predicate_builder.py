from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from pipeline.redbench_gen.retrieve_statistics import ColumnStats
from pipeline.redbench_gen.workload_statistics_retriever import DatabaseStatisticsRetriever, extract_scale_columns
from pipeline.redbench_gen.column_type_retriever import ColumnType, retrieve_column_type


def sanitize_value(value, column_type):
    if column_type == ColumnType.VARCHAR:
        sanitized_value = str(value).replace("'", "''").strip()
        return f"'{sanitized_value}'"
    elif column_type == ColumnType.INT:
        return int(value)
    elif column_type == ColumnType.FLOAT:
        return float(value)
    elif column_type == ColumnType.DATE:
        return f"'{str(value)}'"
    else:
        if isinstance(value, str):
            sanitized_value = value.replace("'", "''").strip()
            return f"'{sanitized_value}'"
        else:
            return value


def rescale_sigma(sigma: float, min_filter_selectivity: float) -> float:
    assert 0 <= sigma <= 1, f"Sigma must be between 0 and 1, but got {sigma}"
    assert 0 <= min_filter_selectivity <= 1
    return min_filter_selectivity + (1 - min_filter_selectivity) * sigma


def build_predicate(
    query: pd.Series,
    database_knowledge: DatabaseStatisticsRetriever,
    rand_state: np.random.RandomState,
    min_filter_selectivity: float,
) -> Tuple[List[str], List[Tuple[str, str, float]]]:
    predicates = []
    approximated_selectivities = []

    start_t = query.get("start_t")
    if not start_t:
        raise ValueError("start_t (main table) must be specified!")

    for table, sigma in query["join_tables_with_selectivity"].items():
        if min_filter_selectivity is not None:
            sigma_scaled = rescale_sigma(sigma, min_filter_selectivity)
        else:
            sigma_scaled = sigma

        column_stats: Dict[str, ColumnStats] = (
            database_knowledge.retrieve_column_statistics(table).columns
        )

        table_info = database_knowledge.retrieve_table_info(table)
        if not table_info:
            raise ValueError(f"Table {table} not found in schema!")

        items = list(column_stats.items())
        rand_state.shuffle(items)

        valid_columns = extract_scale_columns(
            {
                "relationships": database_knowledge.retrieve_relationships(),
                "table_col_info": database_knowledge.retrieve_table_info(),
            }
        )[table]
        filtered_items = [
            (column, values) for column, values in items if column not in valid_columns
        ]

        for column, values in filtered_items:
            quantiles = values.quantiles
            if None in quantiles:
                continue

            assert column in table_info, (
                f"Column {column} not found in table {table} schema!"
            )
            column_type = retrieve_column_type(table_info[column]["type"])

            range_size = max(0, min(100, int(sigma_scaled * 100)))
            start_index = rand_state.randint(0, 100 - range_size + 1)

            lower_bound = quantiles[start_index]
            upper_bound = quantiles[start_index + range_size]

            start_index = quantiles.index(lower_bound)
            end_index = len(quantiles) - 1 - quantiles[::-1].index(upper_bound)
            selectivity = (end_index - start_index + 1) / len(quantiles)
            approximated_selectivities.append((table, column, selectivity))

            lower_bound = sanitize_value(lower_bound, column_type)
            upper_bound = sanitize_value(upper_bound, column_type)

            predicate = f'"{table}"."{column}" BETWEEN {lower_bound} AND {upper_bound}'
            predicates.append(predicate)
            break

    return predicates, approximated_selectivities
