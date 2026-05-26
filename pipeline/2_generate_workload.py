import argparse, csv, os, random, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from pipeline.utils import (
    load_schema, select_tables_bfs, build_select_sql, query_hash
)

parser = argparse.ArgumentParser()
parser.add_argument('--source-db', required=True, help='imdb | tpch | tpcds')
parser.add_argument('--target-db', required=True, help='imdb | tpch | tpcds')
parser.add_argument('--exp',       required=True, help='experiment name')
parser.add_argument('--seed',      type=int, default=42)
args = parser.parse_args()

same_db = (args.source_db == args.target_db)

schema_dir = os.path.join(BASE, 'schemas')
out_dir    = os.path.join(BASE, 'experiments', args.exp, 'output')
t_csv      = os.path.join(out_dir, 'T.csv')
wp_csv     = os.path.join(out_dir, 'W_prime_queries.csv')

if not os.path.exists(t_csv):
    print(f"ERROR: T.csv not found at {t_csv}")
    print("Run 1_execute_workload.py first.")
    sys.exit(1)

target_schema = load_schema(args.target_db, schema_dir)
source_schema = load_schema(args.source_db, schema_dir) if same_db else None

print(f"\nGenerating W': {args.source_db} -> {args.target_db}  (same_db={same_db})")
print(f"T.csv: {t_csv}  |  Output: {wp_csv}\n")

with open(t_csv, newline='') as f:
    t_rows = list(csv.DictReader(f))

print(f"Loaded {len(t_rows)} queries from T")

rng = random.Random(args.seed)
wp_rows = []

target_tables_set = set(target_schema.TABLE_SIZES.keys())

for i, tr in enumerate(t_rows, 1):
    join_count  = int(tr['join_count'])
    bytes_read  = int(tr['bytes_read'])
    n_tables    = max(1, join_count + 1)
    query_file  = tr['query_file']

    if same_db:
        raw_tables = [t for t in tr['read_tables'].split('|') if t]
        tables     = [t for t in raw_tables if t in target_tables_set]

        if not tables:
            tables = select_tables_bfs(n_tables, target_schema, rng)

        if len(tables) < n_tables:
            extra = select_tables_bfs(n_tables, target_schema, rng)
            for t in extra:
                if t not in tables:
                    tables.append(t)
                if len(tables) >= n_tables:
                    break
        tables = tables[:n_tables]
    else:
        tables = select_tables_bfs(n_tables, target_schema, rng)

    sql = build_select_sql(tables, bytes_read, target_schema, rng)
    qh  = query_hash(sql)

    wp_rows.append({
        'query_file'       : query_file,
        'source_join_count': join_count,
        'source_bytes_read': bytes_read,
        'target_tables'    : '|'.join(tables),
        'n_tables'         : len(tables),
        'query_hash'       : qh,
        'sql'              : sql,
    })

    if i <= 5 or i % 20 == 0:
        print(f"  [{i:>3}/{len(t_rows)}] {query_file}  tables={len(tables)}  joins={join_count}  bytes={bytes_read:,}")

fieldnames = ['query_file','source_join_count','source_bytes_read',
              'target_tables','n_tables','query_hash','sql']
with open(wp_csv, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(wp_rows)

print(f"\nGenerated {len(wp_rows)} synthetic queries -> {wp_csv}")
