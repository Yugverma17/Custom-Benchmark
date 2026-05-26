import argparse, csv, os, sys, time
import duckdb

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from config import DB_PATHS, WORKLOAD_DIRS, QUERY_TIMEOUT_S
from pipeline.utils import (
    load_schema, count_joins, extract_tables,
    estimate_bytes_read, query_hash, execute_with_timeout
)

parser = argparse.ArgumentParser()
parser.add_argument('--db',       required=True, help='imdb | tpch | tpcds')
parser.add_argument('--workload', required=True, help='job | tpch | tpcds')
parser.add_argument('--exp',      required=True, help='experiment name, e.g. imdb_to_imdb')
args = parser.parse_args()

db_path  = DB_PATHS[args.db]
wl_dir   = WORKLOAD_DIRS[args.workload]
out_dir  = os.path.join(BASE, 'experiments', args.exp, 'output')
os.makedirs(out_dir, exist_ok=True)
out_csv  = os.path.join(out_dir, 'T.csv')

schema_dir = os.path.join(BASE, 'schemas')
schema     = load_schema(args.db, schema_dir)

sql_files = sorted(f for f in os.listdir(wl_dir) if f.endswith('.sql'))
if not sql_files:
    print(f"ERROR: no .sql files found in {wl_dir}")
    sys.exit(1)

print(f"\nExecuting workload: {args.workload} on {args.db}")
print(f"DB: {db_path}  |  Queries: {len(sql_files)}  |  Output: {out_csv}\n")

con = duckdb.connect(db_path, read_only=True)

if args.db == 'tpch':
    try:
        con.execute("LOAD tpch")
    except Exception:
        pass
elif args.db == 'tpcds':
    try:
        con.execute("LOAD tpcds")
    except Exception:
        pass

rows_out = []
t_start  = time.time()

for i, fname in enumerate(sql_files, 1):
    fpath = os.path.join(wl_dir, fname)
    with open(fpath) as f:
        sql = f.read().strip()

    if not sql:
        continue

    tables    = extract_tables(sql)
    joins     = count_joins(sql)
    bytes_est = estimate_bytes_read(tables, schema)
    qhash     = query_hash(sql)

    result = execute_with_timeout(con, sql, QUERY_TIMEOUT_S)

    row = {
        'query_file'  : fname,
        'query_hash'  : qhash,
        'join_count'  : joins,
        'read_tables' : '|'.join(tables),
        'n_tables'    : len(tables),
        'bytes_read'  : bytes_est,
        'runtime_ms'  : result['runtime_ms'],
        'rows_returned': result['rows_returned'],
        'status'      : result['status'],
        'error_msg'   : result['error_msg'],
    }
    rows_out.append(row)

    status_sym = 'ok' if result['status'] == 'ok' else ('timeout' if result['status'] == 'timeout' else 'error')
    print(f"  [{i:>3}/{len(sql_files)}] {fname}  {status_sym}  {result['runtime_ms']:>8.1f} ms  joins={joins}  tables={len(tables)}")

con.close()

fieldnames = ['query_file','query_hash','join_count','read_tables','n_tables',
              'bytes_read','runtime_ms','rows_returned','status','error_msg']
with open(out_csv, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows_out)

elapsed = time.time() - t_start
ok_count  = sum(1 for r in rows_out if r['status'] == 'ok')
err_count = sum(1 for r in rows_out if r['status'] == 'error')
to_count  = sum(1 for r in rows_out if r['status'] == 'timeout')

print(f"\nDone in {elapsed:.1f}s  |  ok={ok_count}  error={err_count}  timeout={to_count}")
print(f"Saved → {out_csv}")
