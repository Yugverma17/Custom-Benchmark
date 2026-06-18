import argparse, collections, csv, os, sys
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import re

from config import DB_PATHS
from pipeline.utils import load_schema, build_size_rank_mapping, query_hash
from pipeline.redbench_gen.workload_statistics_retriever import DatabaseStatisticsRetriever
from pipeline.redbench_gen.create_join import sample_acyclic_join
from pipeline.redbench_gen.query_builder import build_select_query


def strip_aug_suffix(sql: str, base_tables: list) -> str:
    for t in sorted(base_tables, key=len, reverse=True):
        for sfx in ('_0', '_1', '_2', '_3'):
            aug = t + sfx
            sql = re.sub(rf'(?<!["\w]){re.escape(aug)}(?!["\w])', t, sql)
            sql = sql.replace(f'"{aug}"', f'"{t}"')
    return sql

MIN_FILTER_SELECTIVITY = 0.05
NUM_UNIVERSES          = 2

parser = argparse.ArgumentParser()
parser.add_argument('--source-db',   required=True)
parser.add_argument('--target-db',   required=True)
parser.add_argument('--exp',         required=True)
parser.add_argument('--seed',        type=int, default=3123)
parser.add_argument('--mapping',     default='sizerank', choices=['identity', 'sizerank'])
args = parser.parse_args()

schema_dir   = os.path.join(BASE, 'schemas')
redbench_dir = os.path.join(schema_dir, 'redbench')
out_dir      = os.path.join(BASE, 'experiments', args.exp, 'output')
t_csv        = os.path.join(out_dir, 'T.csv')
wp_csv       = os.path.join(out_dir, 'W_prime_queries.csv')

if not os.path.exists(t_csv):
    print(f"ERROR: T.csv not found at {t_csv}")
    sys.exit(1)

stats_path  = os.path.join(redbench_dir, f'{args.target_db}_column_stats.json')
json_schema = os.path.join(redbench_dir, f'{args.target_db}_schema.json')
sql_schema  = os.path.join(redbench_dir, f'{args.target_db}_schema.sql')

for p in [stats_path, json_schema, sql_schema]:
    if not os.path.exists(p):
        print(f"ERROR: Missing artifact: {p}")
        print(f"Run: python pipeline/gen_db_artifacts.py --db {args.target_db}")
        sys.exit(1)

print(f"\nRedbench Generation: {args.source_db} -> {args.target_db}")
print(f"T.csv  : {t_csv}")
print(f"Output : {wp_csv}")
print(f"Seed   : {args.seed}")

print(f"\n[1/4] Loading DatabaseStatisticsRetriever for '{args.target_db}' ...")
db_knowledge = DatabaseStatisticsRetriever(
    num_universes=NUM_UNIVERSES,
    column_statistics_path=stats_path,
    json_schema_path=json_schema,
    sql_schema_path=sql_schema,
)

table_names_base = db_knowledge.get_default_table_names()
table_names_aug  = db_knowledge.get_original_table_names()
print(f"  Base tables: {len(table_names_base)}, Augmented: {len(table_names_aug)}")

relationships_list = db_knowledge.retrieve_relationships()
relationships_table = collections.defaultdict(list)
for rel in relationships_list:
    tl, cl, tr, cr = rel
    if not isinstance(cl, list):
        cl, cr = [cl], [cr]
    relationships_table[tl].append([cl, tr, cr])
    relationships_table[tr].append([cr, tl, cl])

table_info_aug = db_knowledge.retrieve_table_info()

col_stats_all = db_knowledge.retrieve_column_statistics()
aug_table_sizes = {
    t_aug: col_stats_all[t_aug.rsplit('_', 1)[0]].total_rows
    for t_aug in table_names_aug
    if t_aug.rsplit('_', 1)[0] in col_stats_all
}

print(f"\n[2/4] Building table mapping ({args.source_db} -> {args.target_db}) ...")

source_schema = None
rank_map = {}

try:
    source_schema = load_schema(args.source_db, schema_dir)
    target_schema = load_schema(args.target_db, schema_dir)
    rank_map = build_size_rank_mapping(source_schema, target_schema)
    print(f"  Rank-map built: {len(rank_map)} source tables")
except Exception:
    print(f"  No Python schema for source '{args.source_db}' - will use read_tables directly")

aug_suffixes = set(f'_{i}' for i in range(NUM_UNIVERSES))

def is_already_augmented(table_name):
    for sfx in aug_suffixes:
        if table_name.endswith(sfx) and table_name[:-len(sfx)] in table_names_base:
            return True
    return False

print(f"\n[3/4] Loading trace T ...")
with open(t_csv, newline='') as f:
    t_rows = list(csv.DictReader(f))
total_reps = sum(int(tr.get('repetitions', 1)) for tr in t_rows)
print(f"  {len(t_rows)} unique groups -> {total_reps} output rows")

print(f"\n[4/4] Generating W' ...")

np_rng = np.random.RandomState(args.seed)
filter_tables_set = set(table_names_aug)

query_id = 0
wp_rows  = []
ok_count = 0
err_count = 0

for i, tr in enumerate(t_rows, 1):
    join_count  = int(tr['join_count'])
    bytes_read  = float(tr['bytes_read'])
    n_joins     = join_count
    repetitions = int(tr.get('repetitions', 1))
    src_tables  = [t for t in tr.get('read_tables', '').split('|') if t]
    query_file  = tr.get('query_file', f'q{i}')
    num_aggs    = int(tr.get('num_aggregations', 1)) if 'num_aggregations' in tr else 1

    already_aug = src_tables and is_already_augmented(src_tables[0])

    seen_aug, seed_tables_aug = set(), []

    if already_aug:
        for t in src_tables:
            if t in filter_tables_set and t not in seen_aug:
                seen_aug.add(t)
                seed_tables_aug.append(t)
    elif args.mapping == 'identity':
        universe_id = query_id % NUM_UNIVERSES
        suffix = f"_{universe_id}"
        for t in src_tables:
            t_aug = t + suffix
            if t_aug in filter_tables_set and t_aug not in seen_aug:
                seen_aug.add(t_aug)
                seed_tables_aug.append(t_aug)
    else:
        universe_id = query_id % NUM_UNIVERSES
        suffix = f"_{universe_id}"
        for t in src_tables:
            mapped = rank_map.get(t, t)
            t_aug = mapped + suffix
            if t_aug in filter_tables_set and t_aug not in seen_aug:
                seen_aug.add(t_aug)
                seed_tables_aug.append(t_aug)

    if not seed_tables_aug:
        seed_tables_aug = [table_names_aug[0]]

    start_t = max(seed_tables_aug, key=lambda t: aug_table_sizes.get(t, 0))

    joins, join_tables = sample_acyclic_join(
        start_t=start_t,
        filter_tables=filter_tables_set,
        no_joins=n_joins,
        relationships_table=relationships_table,
        table_column_information=table_info_aug,
        randstate=np_rng,
        left_outer_join_ratio=1,
    )

    total_table_bytes = sum(aug_table_sizes.get(t, 1) for t in join_tables)
    sigma = min(1.0, bytes_read / total_table_bytes) if total_table_bytes > 0 else 0.5

    join_tables_with_sel = {t: sigma for t in join_tables}

    joins_t = []
    for jl, cl, jr, cr, is_outer in joins:
        if not isinstance(cl, list):
            cl, cr = [cl], [cr]
        joins_t.append((jl, cl, jr, cr, not is_outer))

    row_series = pd.Series({
        "start_t": start_t,
        "join_tables": join_tables,
        "joins_t": joins_t,
        "join_tables_with_selectivity": join_tables_with_sel,
        "num_aggregations": max(1, num_aggs),
        "query_type": "select",
    })

    try:
        sql, _, _ = build_select_query(
            query=row_series,
            database_knowledge=db_knowledge,
            rand_state=np_rng,
            min_filter_selectivity=MIN_FILTER_SELECTIVITY,
            simple_agg=True,
        )
        sql = strip_aug_suffix(sql, table_names_base)
        gen_status = 'ok'
        ok_count += 1
    except Exception as e:
        sql = "SELECT 1"
        gen_status = 'error'
        err_count += 1
        if err_count <= 5:
            print(f"  [ERROR q{i}] {e}")

    base_tables_in_join = sorted(set(t.rsplit('_', 1)[0] for t in join_tables))
    qh = query_hash(sql)
    base_row = {
        'query_file'   : query_file,
        'join_count'   : join_count,
        'bytes_read'   : int(bytes_read),
        'target_tables': '|'.join(base_tables_in_join),
        'n_tables'     : len(join_tables),
        'query_hash'   : qh,
        'gen_status'   : gen_status,
        'sql'          : sql,
    }
    for _ in range(repetitions):
        wp_rows.append(base_row)

    query_id += 1

    if i <= 5 or i % 500 == 0:
        print(f"  [{i:>4}/{len(t_rows)}] start={start_t:<30} tables={len(join_tables):2d}  reps={repetitions:3d}  {gen_status}")

os.makedirs(out_dir, exist_ok=True)
fieldnames = ['query_file', 'join_count', 'bytes_read', 'target_tables', 'n_tables', 'query_hash', 'gen_status', 'sql']
with open(wp_csv, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(wp_rows)

print(f"\n{'='*60}")
print(f"Generated {len(wp_rows)} rows ({len(t_rows)} unique groups)")
print(f"  ok    : {ok_count}")
print(f"  error : {err_count}")
print(f"Saved -> {wp_csv}")
