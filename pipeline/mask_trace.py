import csv, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from pipeline.utils import load_schema

SCHEMA_DIR = os.path.join(BASE, 'schemas')
EXP_ROOT   = os.path.join(BASE, 'experiments')
DBS        = ['imdb', 'tpch', 'tpcds']


def build_table_id_map(db_name):
    schema  = load_schema(db_name, SCHEMA_DIR)
    ordered = sorted(schema.TABLE_SIZES, key=lambda t: -schema.TABLE_SIZES[t])
    return {t: i + 1 for i, t in enumerate(ordered)}, schema


def load_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, newline='', encoding='utf-8', errors='replace') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fieldnames):
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def mask_table_ids(table_str, id_map):
    tables = [t for t in table_str.split('|') if t]
    ids = sorted({id_map.get(t, 0) for t in tables})
    return ','.join(str(i) for i in ids)


def mbytes(bytes_str):
    try:
        return round(float(bytes_str) / 1_000_000, 3)
    except (ValueError, TypeError):
        return ''


def mask_T(rows, id_map):
    out = []
    for r in rows:
        out.append({
            'query_id'                       : r.get('query_file', ''),
            'query_hash'                     : r.get('query_hash', ''),
            'num_permanent_tables_accessed'  : r.get('n_tables', ''),
            'read_table_ids'                 : mask_table_ids(r.get('read_tables', ''), id_map),
            'num_joins'                      : r.get('join_count', ''),
            'mbytes_scanned'                 : mbytes(r.get('bytes_read', '')),
            'execution_duration_ms'          : r.get('runtime_ms', ''),
            'rows_returned'                  : r.get('rows_returned', ''),
            'status'                         : r.get('status', ''),
            'error_msg'                      : r.get('error_msg', ''),
        })
    return out


def mask_Wprime(rows, id_map):
    out = []
    for r in rows:
        out.append({
            'query_id'                       : r.get('query_file', ''),
            'query_hash'                     : r.get('query_hash', ''),
            'num_permanent_tables_accessed'  : r.get('n_tables', ''),
            'target_table_ids'               : mask_table_ids(r.get('target_tables', ''), id_map),
            'source_num_joins'               : r.get('source_join_count', ''),
            'source_mbytes_scanned'          : mbytes(r.get('source_bytes_read', '')),
            'relative_selectivity'           : r.get('relative_selectivity', ''),
            'gen_status'                     : r.get('gen_status', ''),
        })
    return out


def mask_Tprime(rows, id_map):
    out = []
    for r in rows:
        out.append({
            'query_id'                       : r.get('query_file', ''),
            'query_hash'                     : r.get('query_hash', ''),
            'num_permanent_tables_accessed'  : r.get('n_tables', ''),
            'read_table_ids'                 : mask_table_ids(r.get('read_tables', ''), id_map),
            'num_joins'                      : r.get('join_count', ''),
            'mbytes_scanned'                 : mbytes(r.get('bytes_read', '')),
            'execution_duration_ms'          : r.get('runtime_ms', ''),
            'rows_returned'                  : r.get('rows_returned', ''),
            'status'                         : r.get('status', ''),
            'error_msg'                      : r.get('error_msg', ''),
            'source_num_joins'               : r.get('src_join_count', ''),
            'source_mbytes_scanned'          : mbytes(r.get('src_bytes_read', '')),
        })
    return out


eval_dir = os.path.join(EXP_ROOT, '_evaluation')
os.makedirs(eval_dir, exist_ok=True)

id_maps = {}
for db in DBS:
    id_map, schema = build_table_id_map(db)
    id_maps[db] = id_map
    ref_rows = [
        {'table_id': tid, 'table_name': t, 'row_count': schema.TABLE_SIZES[t]}
        for t, tid in sorted(id_map.items(), key=lambda kv: kv[1])
    ]
    write_csv(os.path.join(eval_dir, f'table_id_mapping_{db}.csv'),
              ref_rows, ['table_id', 'table_name', 'row_count'])
    print(f"  table_id_mapping_{db}.csv  ({len(ref_rows)} tables)")

experiments = sorted(
    d for d in os.listdir(EXP_ROOT)
    if os.path.isdir(os.path.join(EXP_ROOT, d)) and not d.startswith('_')
)

print(f"\nMasking table names -> numeric table_ids for {len(experiments)} experiment(s) ...\n")

for exp in experiments:
    out_dir = os.path.join(EXP_ROOT, exp, 'output')
    src_db, tgt_db = exp.split('_to_')[0], exp.split('_to_')[1].split('_')[0]

    T  = load_csv(os.path.join(out_dir, 'T.csv'))
    WP = load_csv(os.path.join(out_dir, 'W_prime_queries.csv'))
    TP = load_csv(os.path.join(out_dir, 'T_prime.csv'))

    n_written = 0
    if T is not None:
        rows = mask_T(T, id_maps[src_db])
        write_csv(os.path.join(out_dir, 'T_masked.csv'), rows, list(rows[0].keys()))
        n_written += 1
    if WP is not None:
        rows = mask_Wprime(WP, id_maps[tgt_db])
        write_csv(os.path.join(out_dir, 'W_prime_queries_masked.csv'), rows, list(rows[0].keys()))
        n_written += 1
    if TP is not None:
        rows = mask_Tprime(TP, id_maps[tgt_db])
        write_csv(os.path.join(out_dir, 'T_prime_masked.csv'), rows, list(rows[0].keys()))
        n_written += 1

    print(f"  {exp:<28} ({src_db} -> {tgt_db})  {n_written} file(s) masked")

print("\nDone. table_id_mapping_<db>.csv files written to experiments/_evaluation/")
