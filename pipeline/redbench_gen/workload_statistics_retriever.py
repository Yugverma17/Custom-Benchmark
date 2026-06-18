import collections
import json
import os
import re
from typing import Dict, Optional

from pipeline.redbench_gen.retrieve_statistics import ColumnStats, TableStats


def load_database_stats(json_path: str) -> Dict[str, TableStats]:
    with open(json_path, "r") as f:
        data = json.load(f)
    return {
        table_name: TableStats(
            total_rows=table_data["total_rows"],
            columns={
                col_name: ColumnStats(**col_data)
                for col_name, col_data in table_data["columns"].items()
            },
        )
        for table_name, table_data in data.items()
    }


def modify_json(json_data, words_to_change, addition_str):
    if isinstance(json_data, dict):
        return {key: modify_json(value, words_to_change, addition_str) for key, value in json_data.items()}
    elif isinstance(json_data, list):
        return [modify_json(item, words_to_change, addition_str) for item in json_data]
    elif isinstance(json_data, str) and json_data in words_to_change:
        return json_data + addition_str
    else:
        return json_data


def modify_dict_keys(original_dict, words_to_change, addition_str, root_only: bool = False):
    if not isinstance(original_dict, dict):
        return original_dict
    modified_dict = {}
    for key, value in original_dict.items():
        new_key = f"{key}{addition_str}" if key in words_to_change else key
        if root_only:
            modified_dict[new_key] = value
        else:
            modified_dict[new_key] = modify_dict_keys(value, words_to_change, addition_str)
    return modified_dict


def get_json_schema(path: str):
    assert os.path.exists(path), f"Schema JSON not found: {path}"
    with open(path, "r") as f:
        return json.load(f)


def get_sql_schema(path: str, keep_newline: bool = False):
    with open(path, "r") as f:
        data = f.read()
    if not keep_newline:
        data = data.replace("\n", "")
    return data


def extract_scale_columns(schema: Dict):
    scale_columns = collections.defaultdict(set)
    for rel in schema["relationships"]:
        table_l, col_l, table_r, col_r = rel
        if not isinstance(col_l, list):
            col_l = [col_l]
            col_r = [col_r]
        for table, columns in [(table_l, col_l), (table_r, col_r)]:
            for c in columns:
                scale_columns[table].add(c)

    for table, table_stats in schema["table_col_info"].items():
        for column, column_stats in table_stats.items():
            if column_stats["pk"]:
                scale_columns[table].add(column)
    return scale_columns


class DatabaseStatisticsRetriever:
    def __init__(self, num_universes, column_statistics_path, json_schema_path, sql_schema_path=None):
        self.num_universes = num_universes
        self.column_statistics_path = column_statistics_path
        self.json_schema_path = json_schema_path
        self.sql_schema_path = sql_schema_path

        self._table_names = None
        self._column_statistic = None
        self._relationships = None
        self._table_column_info = None
        self._varchar_lengths = self._load_varchar_lengths()

    def _load_varchar_lengths(self):
        if not self.sql_schema_path or not os.path.exists(self.sql_schema_path):
            return {}
        sql_schema = get_sql_schema(self.sql_schema_path, keep_newline=True)
        table_pattern = re.compile(
            r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(?P<name>(?:\"[^\"]+\"|\w+)(?:\.(?:\"[^\"]+\"|\w+))?)[\s\r\n]*\((?P<body>.*?)\);",
            re.IGNORECASE | re.DOTALL,
        )
        column_pattern = re.compile(
            r'(?P<column>"[^"]+"|\b\w+\b)\s+(?:character varying|varchar|char)\s*(?:\(\s*(?P<length>\d+)\s*\))?',
            re.IGNORECASE,
        )
        raw: Dict[str, Dict[str, Optional[int]]] = {}
        for m in table_pattern.finditer(sql_schema):
            raw_name = m.group("name")
            body = m.group("body")
            if "." in raw_name:
                raw_name = raw_name.split(".")[-1]
            table_name = raw_name.strip('"')
            cols: Dict[str, Optional[int]] = {}
            for cm in column_pattern.finditer(body):
                col_id = cm.group("column")
                col_name = col_id[1:-1] if col_id.startswith('"') and col_id.endswith('"') else col_id
                length_str = cm.group("length")
                cols[col_name] = int(length_str) if length_str else None
            raw[table_name] = cols

        result: Dict[str, Dict[str, Optional[int]]] = {}
        for table_name, columns in raw.items():
            target = result.setdefault(table_name, {})
            for col_name, length in columns.items():
                if col_name not in target:
                    target[col_name] = length
                    continue
                existing = target[col_name]
                if existing is None or length is None:
                    target[col_name] = None
                elif existing != length:
                    target[col_name] = max(existing, length)
        return result

    def get_default_table_names(self):
        if self._table_names:
            return self._table_names
        data = load_database_stats(self.column_statistics_path)
        self._table_names = list(data.keys())
        return self._table_names

    def get_original_table_names(self):
        table_names = self.get_default_table_names()
        result = []
        for multiverse_id in range(self.num_universes):
            add_str = f"_{multiverse_id}"
            result.extend([t + add_str for t in table_names])
        return result

    def retrieve_column_statistics(self, table_name=None):
        if self._column_statistic:
            return (
                self._column_statistic[table_name.split("_ctasc2b89z8c2z9_")[0]]
                if table_name
                else self._column_statistic
            )
        data = load_database_stats(self.column_statistics_path)
        table_names = list(data.keys())
        result_dict = {}
        for multiverse_id in range(self.num_universes):
            add_str = f"_{multiverse_id}"
            universe_dict = modify_dict_keys(data, table_names, add_str, root_only=True)
            result_dict = result_dict | universe_dict
        self._column_statistic = result_dict
        return (
            self._column_statistic[table_name.split("_ctasc2b89z8c2z9_")[0]]
            if table_name
            else self._column_statistic
        )

    def retrieve_relationships(self):
        if self._relationships:
            return self._relationships
        schema = get_json_schema(self.json_schema_path)
        result_list = []
        for multiverse_id in range(self.num_universes):
            add_str = f"_{multiverse_id}"
            universe_list = modify_json(
                schema["relationships"], self.get_default_table_names(), add_str
            )
            result_list.extend(universe_list)
        self._relationships = result_list
        return self._relationships

    def retrieve_table_info(self, table_name=None):
        if self._table_column_info:
            return (
                self._table_column_info[table_name.split("_ctasc2b89z8c2z9_")[0]]
                if table_name
                else self._table_column_info
            )
        schema = get_json_schema(self.json_schema_path)
        result_dict = {}
        for multiverse_id in range(self.num_universes):
            add_str = f"_{multiverse_id}"
            universe_dict = modify_dict_keys(
                schema["table_col_info"],
                self.get_default_table_names(),
                add_str,
                root_only=True,
            )
            result_dict = result_dict | universe_dict
        self._table_column_info = result_dict
        return (
            self._table_column_info[table_name.split("_ctasc2b89z8c2z9_")[0]]
            if table_name
            else self._table_column_info
        )

    def retrieve_varchar_lengths(self):
        return self._varchar_lengths
