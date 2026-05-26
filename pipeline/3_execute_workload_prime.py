import argparse, csv, os, sys, time
import duckdb

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from config import DB_PATHS, QUERY_TIMEOUT_S
from pipeline.utils import (
    load_schema, count_joins, extract_tables,
    estimate_bytes_read, query_hash, execute_with_timeout
)

parser = argparse.ArgumentParser()
parser.add_argument('--db',  required=True, help='target DB: imdb | tpch | tpcds')
parser.add_argument('--exp', required=True, help='experiment name, e.g. imdb_to_tpch')
args = parser.parse_args()

db_path    = DB_PATHS[args.db]
schema_dir = os.path.join(BASE, 'schemas')
out_dir    = os.path.join(BASE, 'experiments', args.exp, 'output')
wp_csv     = os.path.join(out_dir, 'W_prime_queries.csv')
tp_csv     = os.path.join(out_dir, 'T_prime.csv')

if not os.path.exists(wp_csv):
    print(f"ERROR: W_prime_queries.csv not found at {wp_csv}")
    print("Run 2_generate_workload.py first.")
    sys.exit(1)

schema = load_schema(args.db, schema_dir)

print(f"\nExecuting W' on {args.db}")
print(f"DB: {db_path}  |  W': {wp_csv}  |  Output: {tp_csv}\n")

with open(wp_csv, newline='') as f:
    wp_rows = list(csv.DictReader(f))

print(f"Loaded {len(wp_rows)} synthetic queries")

tp_rows = []
t_start  = time.time()

for i, wp in enumerate(wp_rows, 1):
    sql        = wp['sql']
    query_file = wp['query_file']

    tables    = extract_tables(sql)
    joins     = count_joins(sql)
    bytes_est = estimate_bytes_read(tables, schema)
    qhash     = query_hash(sql)

    con = duckdb.connect(db_path, read_only=True)
    con.execute("SET memory_limit='6GB'")
    con.execute("SET threads=2")
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

    result = execute_with_timeout(con, sql, QUERY_TIMEOUT_S)
    con.close()

    row = {
        'query_file'      : query_file,
        'query_hash'      : qhash,
        'join_count'      : joins,
        'read_tables'     : '|'.join(tables),
        'n_tables'        : len(tables),
        'bytes_read'      : bytes_est,
        'runtime_ms'      : result['runtime_ms'],
        'rows_returned'   : result['rows_returned'],
        'status'          : result['status'],
        'error_msg'       : result['error_msg'],
        'src_join_count'  : wp['source_join_count'],
        'src_bytes_read'  : wp['source_bytes_read'],
    }
    tp_rows.append(row)

    status_sym = 'ok' if result['status'] == 'ok' else ('timeout' if result['status'] == 'timeout' else 'error')
    print(f"  [{i:>3}/{len(wp_rows)}] {query_file}  {status_sym}  {result['runtime_ms']:>8.1f} ms  joins={joins}  tables={len(tables)}")

fieldnames = ['query_file','query_hash','join_count','read_tables','n_tables',
              'bytes_read','runtime_ms','rows_returned','status','error_msg',
              'src_join_count','src_bytes_read']
with open(tp_csv, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(tp_rows)

elapsed   = time.time() - t_start
ok_count  = sum(1 for r in tp_rows if r['status'] == 'ok')
err_count = sum(1 for r in tp_rows if r['status'] == 'error')
to_count  = sum(1 for r in tp_rows if r['status'] == 'timeout')

print(f"\nDone in {elapsed:.1f}s  |  ok={ok_count}  error={err_count}  timeout={to_count}")
print(f"Saved -> {tp_csv}")
