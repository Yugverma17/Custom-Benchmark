from enum import Enum


class ColumnType(Enum):
    VARCHAR = "VARCHAR"
    INT = "INT"
    FLOAT = "FLOAT"
    DATE = "DATE"
    UNKNOWN = "UNKNOWN"


def retrieve_column_type(column_type: str) -> ColumnType:
    data_type = column_type.upper()
    if any(t in data_type for t in ("INTEGER", "BIGINT", "INT")):
        return ColumnType.INT
    if any(t in data_type for t in ("DOUBLE", "REAL", "DECIMAL", "NUMERIC", "FLOAT")):
        return ColumnType.FLOAT
    if any(t in data_type for t in ("VARCHAR", "TEXT", "STRING", "CHAR")):
        return ColumnType.VARCHAR
    if any(t in data_type for t in ("DATE", "TIMESTAMP", "TIME")):
        return ColumnType.VARCHAR
    return ColumnType.UNKNOWN
