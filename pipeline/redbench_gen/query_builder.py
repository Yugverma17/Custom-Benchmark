import re

import numpy as np
import pandas as pd

from pipeline.redbench_gen.workload_statistics_retriever import DatabaseStatisticsRetriever
from pipeline.redbench_gen.join_clause_builder import build_join_clauses
from pipeline.redbench_gen.predicate_builder import build_predicate


def remove_duplicated_white_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def build_select_query(
    query: pd.Series,
    database_knowledge: DatabaseStatisticsRetriever,
    rand_state: np.random.RandomState,
    min_filter_selectivity: float,
    aggregation="*",
    add_distinct=True,
    override_predicate=None,
    simple_agg=False,
):
    start_t = query.get("start_t")
    join_tables = query.get("join_tables", set())

    if not start_t:
        raise ValueError("start_t (main table) must be specified!")
    if not join_tables:
        raise ValueError("join_tables cannot be empty!")
    if start_t not in join_tables:
        raise ValueError("start_t must be included in join_tables!")

    table_info = database_knowledge.retrieve_table_info(start_t)
    if not table_info:
        raise ValueError(f"Table {start_t} not found in schema!")

    pk_columns = [col for col, props in table_info.items() if props.get("pk", False)]
    columns = [col for col, props in table_info.items()]

    if simple_agg:
        chosen_columns = rand_state.choice(
            columns, size=max(1, min(query["num_aggregations"], len(columns)))
        )
        aggregation = ", ".join([f'"{start_t}"."{c}"' for c in chosen_columns])

    pk_columns_str = ", ".join([f'"{start_t}"."{c}"' for c in pk_columns])
    distinct_query = f"DISTINCT ON({pk_columns_str})"
    select_clause = f"SELECT {distinct_query} {aggregation}"

    from_clause = f'FROM "{start_t}"'

    join_condition_clauses = build_join_clauses(query)
    join_clause = " ".join(join_condition_clauses)

    if override_predicate:
        predicate_clauses, approximated_selectivities = override_predicate
    else:
        predicate_clauses, approximated_selectivities = build_predicate(
            query,
            database_knowledge,
            rand_state,
            min_filter_selectivity=min_filter_selectivity,
        )

    where_clause = (
        f"WHERE {' AND '.join(predicate_clauses)}" if predicate_clauses else ""
    )

    sql_query = f"{select_clause} {from_clause} {join_clause} {where_clause};"

    return (
        remove_duplicated_white_spaces(sql_query.strip()),
        remove_duplicated_white_spaces(sql_query.strip()),
        approximated_selectivities,
    )
