from typing import List

import pandas as pd


def build_join_conditions(query: pd.Series) -> List[str]:
    clauses = []
    for left_table, left_keys, right_table, right_keys, is_inner in query["joins_t"]:
        conditions = " AND ".join(
            f'"{left_table}"."{lk}" = "{right_table}"."{rk}"'
            for lk, rk in zip(left_keys, right_keys)
        )
        clauses.append(conditions)
    return clauses


def build_join_clauses(query: pd.Series) -> List[str]:
    clauses = []
    for left_table, left_keys, right_table, right_keys, is_inner in query["joins_t"]:
        join_type = "JOIN" if is_inner else "LEFT JOIN"
        conditions = " AND ".join(
            f'"{left_table}"."{lk}" = "{right_table}"."{rk}"'
            for lk, rk in zip(left_keys, right_keys)
        )
        clauses.append(f'{join_type} "{right_table}" ON {conditions}')
    return clauses
