import argparse
import importlib
import json
import os
import re
import sys

import duckdb

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'schemas'))

from config import DB_PATHS

KNOWN_PKS = {
    'tpch': {
        'customer': ['c_custkey'],
        'lineitem': ['l_orderkey', 'l_linenumber'],
        'nation':   ['n_nationkey'],
        'orders':   ['o_orderkey'],
        'part':     ['p_partkey'],
        'partsupp': ['ps_partkey', 'ps_suppkey'],
        'region':   ['r_regionkey'],
        'supplier': ['s_suppkey'],
    },
    'tpcds': {},
}

parser = argparse.ArgumentParser()
parser.add_argument('--db', required=True, choices=['imdb', 'tpch', 'tpcds'])
args = parser.parse_args()

out_dir = os.path.join(BASE, 'schemas', 'redbench')
os.makedirs(out_dir, exist_ok=True)

schema_json_path = os.path.join(out_dir, f'{args.db}_schema.json')
schema_sql_path  = os.path.join(out_dir, f'{args.db}_schema.sql')
stats_json_path  = os.path.join(out_dir, f'{args.db}_column_stats.json')

db_path = DB_PATHS[args.db]
print(f"Connecting to {db_path} ...")
con = duckdb.connect(db_path, read_only=True)

if args.db in ('tpch', 'tpcds'):
    try:
        con.close()
        con = duckdb.connect(db_path, read_only=False)
        con.execute(f"LOAD {args.db}")
    except Exception:
        pass

tables = [r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' ORDER BY table_name"
).fetchall()]
print(f"Found {len(tables)} tables")


def parse_pks_from_sql(sql_text):
    pks = {}
    table_pattern = re.compile(
        r'CREATE TABLE\s+(?:"([^"]+)"|(\w+))\s*\((.*?)\);',
        re.IGNORECASE | re.DOTALL
    )
    for m in table_pattern.finditer(sql_text):
        table = m.group(1) or m.group(2)
        body = m.group(3)
        pk_cols = []
        inline_pk = re.compile(r'"?(\w+)"?\s+\w[\w\s()]*PRIMARY\s+KEY', re.IGNORECASE)
        for cm in inline_pk.finditer(body):
            pk_cols.append(cm.group(1))
        table_pk = re.search(r'PRIMARY\s+KEY\s*\(([^)]+)\)', body, re.IGNORECASE)
        if table_pk and not pk_cols:
            pk_cols = [c.strip().strip('"') for c in table_pk.group(1).split(',')]
        if pk_cols:
            pks[table] = pk_cols
    return pks


src_sql_for_imdb = os.path.join(BASE, 'databases', 'imdb_csvs', 'schematext.sql')
pk_lookup = {}

if args.db == 'imdb' and os.path.exists(src_sql_for_imdb):
    with open(src_sql_for_imdb, 'r') as f:
        sql_text = f.read()
    pk_lookup = parse_pks_from_sql(sql_text)
    print(f"  PKs parsed from SQL: {list(pk_lookup.keys())[:5]} ...")
elif args.db in KNOWN_PKS:
    pk_lookup = KNOWN_PKS[args.db]
    print(f"  Using hardcoded PKs for {args.db}")

for table in tables:
    if table not in pk_lookup:
        cols = [r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()]
        if 'id' in cols:
            pk_lookup[table] = ['id']
        elif table in KNOWN_PKS.get(args.db, {}):
            pass
        else:
            sk_cols = [c for c in cols if c.endswith('_sk')]
            if sk_cols:
                pk_lookup[table] = [sk_cols[0]]


table_col_info = {}
for t in tables:
    rows = con.execute(f'DESCRIBE "{t}"').fetchall()
    cols = {}
    pks_for_table = set(pk_lookup.get(t, []))
    for row in rows:
        col_name = row[0]
        col_type = row[1]
        cols[col_name] = {"type": col_type, "pk": col_name in pks_for_table}
    table_col_info[t] = cols

schema_mod = importlib.import_module(f'{args.db}_schema')
fk_edges_py = getattr(schema_mod, 'FK_EDGES', [])
relationships = [[tl, cl, tr, cr] for tl, cl, tr, cr in fk_edges_py]

schema_json = {
    "table_col_info": table_col_info,
    "relationships": relationships,
}

with open(schema_json_path, 'w') as f:
    json.dump(schema_json, f, indent=2)
print(f"Schema JSON written to {schema_json_path}")

sql_lines = []
for t in tables:
    cols = table_col_info[t]
    col_defs = []
    for col_name, col_info in cols.items():
        pk_str = " PRIMARY KEY" if col_info["pk"] else ""
        col_defs.append(f'  "{col_name}" {col_info["type"]}{pk_str}')
    table_sql = f'CREATE TABLE "{t}" (\n' + ",\n".join(col_defs) + "\n);"
    sql_lines.append(table_sql)

with open(schema_sql_path, 'w') as f:
    f.write("\n\n".join(sql_lines) + "\n")
print(f"Schema SQL written to {schema_sql_path}")

con.close()

print(f"\nGenerating column statistics (may take several minutes) ...")
from pipeline.redbench_gen.retrieve_statistics import create_quantiles
create_quantiles(db_path, stats_json_path, force=False)

print(f"\nAll artifacts for '{args.db}' ready in {out_dir}/")
