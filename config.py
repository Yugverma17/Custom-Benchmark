import os

BASE = os.path.dirname(os.path.abspath(__file__))

DB_PATHS = {
    'imdb'     : os.path.join(BASE, 'databases', 'imdb.duckdb'),
    'tpch'     : os.path.join(BASE, 'databases', 'tpch.duckdb'),
    'tpcds'    : os.path.join(BASE, 'databases', 'tpcds.duckdb'),
    'tpch_sf10': os.path.join(BASE, 'databases', 'tpch_sf10.duckdb'),
}

WORKLOAD_DIRS = {
    'job'  : os.path.join(BASE, 'workloads', 'job'),
    'tpch' : os.path.join(BASE, 'workloads', 'tpch'),
    'tpcds': os.path.join(BASE, 'workloads', 'tpcds'),
}

DB_WORKLOAD = {
    'imdb' : 'job',
    'tpch' : 'tpch',
    'tpcds': 'tpcds',
}

SCHEMA_DIR = os.path.join(BASE, 'schemas')
EXP_DIR    = os.path.join(BASE, 'experiments')

QUERY_TIMEOUT_S = 30
