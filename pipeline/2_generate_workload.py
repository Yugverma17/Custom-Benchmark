import argparse, csv, os, random, sys
import duckdb

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from config import DB_PATHS
from pipeline.utils import (
    load_schema,
    build_size_rank_mapping,
    compute_relative_selectivity,
    compute_column_stats,
    random_walk_tables,
    build_select_sql_redbench,
    execute_with_timeout,
    query_hash,
    select_tables_bfs,
)

VALIDATION_TIMEOUT_S = 10
MAX_RETRIES          = 3

parser = argparse.ArgumentParser()
parser.add_argument('--source-db', required=True, help='imdb | tpch | tpcds')
parser.add_argument('--target-db', required=True, help='imdb | tpch | tpcds')
parser.add_argument('--exp',       required=True, help='experiment name')
parser.add_argument('--seed',      type=int, default=42)
args = parser.parse_args()

same_db    = (args.source_db == args.target_db)
schema_dir = os.path.join(BASE, 'schemas')
out_dir    = os.path.join(BASE, 'experiments', args.exp, 'output')
t_csv      = os.path.join(out_dir, 'T.csv')
wp_csv     = os.path.join(out_dir, 'W_prime_queries.csv')

if not os.path.exists(t_csv):
    print(f"ERROR: T.csv not found at {t_csv}")
    print("Run 1_execute_workload.py first.")
    sys.exit(1)

source_schema = load_schema(args.source_db, schema_dir)
target_schema = load_schema(args.target_db, schema_dir)
rng           = random.Random(args.seed)

print(f"\nRedBench Generation: {args.source_db} -> {args.target_db}  (same_db={same_db})")
print(f"T.csv  : {t_csv}")
print(f"Output : {wp_csv}\n")

print("[1/4] Computing column statistics from target DB ...")
target_db_path = DB_PATHS[args.target_db]
con_target = duckdb.connect(target_db_path, read_only=True)

if args.target_db == 'tpch':
    try:
        con_target.close()
        con_target = duckdb.connect(target_db_path, read_only=False)
        con_target.execute("LOAD tpch")
    except Exception:
        pass
elif args.target_db == 'tpcds':
    try:
        con_target.close()
        con_target = duckdb.connect(target_db_path, read_only=False)
        con_target.execute("LOAD tpcds")
    except Exception:
        pass

col_stats = compute_column_stats(con_target, target_schema)
n_stat_cols = sum(len(v) for v in col_stats.values())
print(f"  Stats collected for {n_stat_cols} predicate columns across {len(col_stats)} tables")

print(f"\n[2/4] Building size-rank table mapping ({args.source_db} -> {args.target_db}) ...")

if same_db:
    rank_map = {t: t for t in source_schema.TABLE_SIZES}
    print(f"  Same DB: identity mapping ({len(rank_map)} tables)")
else:
    rank_map = build_size_rank_mapping(source_schema, target_schema)
    src_ranked = sorted(source_schema.TABLE_SIZES, key=lambda t: -source_schema.TABLE_SIZES[t])
    print(f"  Top mappings (source rank -> target rank):")
    for i, src in enumerate(src_ranked[:6]):
        tgt = rank_map[src]
        print(f"    [{i+1:2d}] {src:<25} ({source_schema.TABLE_SIZES[src]:>10,} rows)  ->  "
              f"{tgt:<25} ({target_schema.TABLE_SIZES[tgt]:>10,} rows)")
    if len(src_ranked) > 6:
        print(f"    ... and {len(src_ranked)-6} more")

print(f"\n[3/4] Loading trace T ...")
with open(t_csv, newline='') as f:
    t_rows = list(csv.DictReader(f))
print(f"  {len(t_rows)} queries loaded")

print(f"\n[4/4] Generating W' ({len(t_rows)} queries) ...\n")

wp_rows     = []
ok_count    = 0
relax_count = 0
error_count = 0

target_tables_set = set(target_schema.TABLE_SIZES.keys())

for i, tr in enumerate(t_rows, 1):
    join_count = int(tr['join_count'])
    bytes_read = int(tr['bytes_read'])
    n_tables   = max(1, join_count + 1)
    query_file = tr['query_file']
    src_tables = [t for t in tr.get('read_tables', '').split('|') if t]

    rel_sel = compute_relative_selectivity(src_tables, bytes_read, source_schema)

    if same_db:
        seed_tables = [t for t in src_tables if t in target_tables_set]
    else:
        seen, seed_tables = set(), []
        for t in src_tables:
            mapped = rank_map.get(t)
            if mapped and mapped not in seen:
                seen.add(mapped)
                seed_tables.append(mapped)

    if not seed_tables:
        seed_tables = select_tables_bfs(n_tables, target_schema, rng)

    tables = random_walk_tables(seed_tables, n_tables, target_schema, rng)

    sql        = "SELECT 1"
    gen_status = 'error'

    for attempt in range(MAX_RETRIES):
        relaxed_sel = min(0.99, rel_sel * (2 ** attempt))
        candidate   = build_select_sql_redbench(tables, relaxed_sel, target_schema, col_stats, rng)
        result      = execute_with_timeout(con_target, candidate, VALIDATION_TIMEOUT_S)
        sql        = candidate
        gen_status = result['status']
        if result['status'] == 'ok':
            ok_count += 1
            break
        elif result['status'] == 'error':
            error_count += 1
            break
        relax_count += 1

    qh = query_hash(sql)
    wp_rows.append({
        'query_file'          : query_file,
        'source_join_count'   : join_count,
        'source_bytes_read'   : bytes_read,
        'relative_selectivity': round(rel_sel, 4),
        'target_tables'       : '|'.join(tables),
        'n_tables'            : len(tables),
        'query_hash'          : qh,
        'gen_status'          : gen_status,
        'sql'                 : sql,
    })

    if i <= 5 or i % 20 == 0:
        print(f"  [{i:>3}/{len(t_rows)}] {query_file:<16}  "
              f"rel_sel={rel_sel:.3f}  tables={len(tables):2d}  status={gen_status}")

con_target.close()

os.makedirs(out_dir, exist_ok=True)
fieldnames = [
    'query_file', 'source_join_count', 'source_bytes_read',
    'relative_selectivity', 'target_tables', 'n_tables',
    'query_hash', 'gen_status', 'sql',
]
with open(wp_csv, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(wp_rows)

print(f"\n{'='*60}")
print(f"Generated {len(wp_rows)} synthetic queries")
print(f"  Validated ok   : {ok_count}")
print(f"  Relaxed/timeout: {relax_count}")
print(f"  Error          : {error_count}")
print(f"Saved -> {wp_csv}")
