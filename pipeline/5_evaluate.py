import argparse, csv, os, re, sys
import duckdb
import numpy as np
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from config import DB_PATHS, WORKLOAD_DIRS, DB_WORKLOAD

SCAN_OPS = {'TABLE_SCAN', 'SEQ_SCAN', 'COLUMN_DATA_SCAN',
            'EXPRESSION_SCAN', 'POSITIONAL_SCAN', 'CHUNK_SCAN'}
JOIN_OPS  = {'HASH_JOIN', 'NESTED_LOOP_JOIN', 'MERGE_JOIN',
             'CROSS_PRODUCT', 'IE_JOIN', 'DELIM_JOIN', 'PIECEWISE_MERGE_JOIN'}
AGG_OPS   = {'HASH_GROUP_BY', 'UNGROUPED_AGG', 'PERFECT_HASH_GROUP_BY',
             'STREAMING_WINDOW', 'WINDOW', 'GROUPING_SETS'}
SORT_OPS  = {'ORDER_BY', 'TOP_N'}
ALL_OPS   = SCAN_OPS | JOIN_OPS | AGG_OPS | SORT_OPS | {
             'FILTER', 'PROJECTION', 'LIMIT', 'DISTINCT',
             'UNION', 'RECURSIVE_CTE', 'CTE_SCAN', 'EMPTY_RESULT'}

_db_cons = {}

def get_con(db_name):
    if db_name not in _db_cons:
        path = DB_PATHS[db_name]
        try:
            con = duckdb.connect(path, read_only=False)
        except Exception:
            con = duckdb.connect(path, read_only=True)
        for ext in (['tpch'] if db_name == 'tpch' else
                    ['tpcds'] if db_name == 'tpcds' else []):
            try:
                con.execute(f"LOAD {ext}")
            except Exception:
                pass
        _db_cons[db_name] = con
        print(f"    [db] opened {db_name}")
    return _db_cons[db_name]

_explain_cache = {}

def explain_ops(db_name, sql):
    try:
        rows = get_con(db_name).execute(f"EXPLAIN {sql}").fetchall()
        text = '\n'.join(str(r[-1]) for r in rows if r[-1])
    except Exception:
        text = ''
    counts = Counter()
    for op in ALL_OPS:
        n = len(re.findall(rf'\b{re.escape(op)}\b', text))
        if n:
            counts[op] = n
    return counts, frozenset(counts.keys())

def explain_W(source_db, wl_dir, query_file):
    key = (source_db, query_file)
    if key not in _explain_cache:
        path = os.path.join(wl_dir, query_file)
        if os.path.exists(path):
            with open(path, encoding='utf-8', errors='replace') as f:
                sql = f.read().strip()
            _explain_cache[key] = explain_ops(source_db, sql)
        else:
            _explain_cache[key] = (Counter(), frozenset())
    return _explain_cache[key]

def load_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, newline='', encoding='utf-8', errors='replace') as f:
        return list(csv.DictReader(f))

def floats(rows, col, only_ok=False):
    out = []
    for r in rows:
        if only_ok and r.get('status', '') != 'ok':
            continue
        try:
            out.append(float(r[col]))
        except (ValueError, KeyError):
            pass
    return np.array(out) if out else np.array([0.0])

def pct_ok(rows):
    if not rows:
        return 0.0
    return 100.0 * sum(1 for r in rows if r.get('status', '') == 'ok') / len(rows)

def query_diversity(rows, table_col):
    if not rows:
        return 0.0
    unique_sets = {frozenset(r.get(table_col, '').split('|')) for r in rows}
    return len(unique_sets) / len(rows)

def srr(rows, table_col):
    if not rows:
        return 0.0
    sets  = [frozenset(r.get(table_col, '').split('|')) for r in rows]
    freq  = Counter(sets)
    return sum(1 for s in sets if freq[s] > 1) / len(sets)

def avg_ops(counter_list):
    if not counter_list:
        return {k: 0.0 for k in
                ('avg_scans','avg_joins','avg_aggregations','avg_sorts','avg_total_ops')}
    def mean(fn): return float(np.mean([fn(c) for c in counter_list]))
    return {
        'avg_scans'        : round(mean(lambda c: sum(c[o] for o in SCAN_OPS)), 2),
        'avg_joins'        : round(mean(lambda c: sum(c[o] for o in JOIN_OPS)),  2),
        'avg_aggregations' : round(mean(lambda c: sum(c[o] for o in AGG_OPS)),   2),
        'avg_sorts'        : round(mean(lambda c: sum(c[o] for o in SORT_OPS)),  2),
        'avg_total_ops'    : round(mean(lambda c: sum(c.values())),               2),
    }

def plan_div(sig_list):
    if not sig_list:
        return 0.0, 0
    return round(len(set(sig_list)) / len(sig_list), 3), len(set(sig_list))

def fmt(v, d=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 'n/a'
    if isinstance(v, float):
        return f'{v:.{d}f}'
    return str(v)

def evaluate(exp_name, out_dir):
    T  = load_csv(os.path.join(out_dir, 'T.csv'))
    WP = load_csv(os.path.join(out_dir, 'W_prime_queries.csv'))
    TP = load_csv(os.path.join(out_dir, 'T_prime.csv'))

    missing = [n for n, d in [('T', T), ("W'", WP), ("T'", TP)] if d is None]
    if missing:
        print(f"  SKIP {exp_name}: missing {missing}")
        return None

    src, tgt = exp_name.split('_to_') if '_to_' in exp_name else (exp_name, exp_name)
    src = src.replace('_sizerank', '')
    tgt = tgt.replace('_sizerank', '')
    wl_dir   = WORKLOAD_DIRS[DB_WORKLOAD[src]]

    div_W  = query_diversity(T,  'read_tables')
    div_Wp = query_diversity(WP, 'target_tables')
    srr_W  = srr(T,  'read_tables')
    srr_Wp = srr(WP, 'target_tables')

    ops_W, sigs_W = [], []
    for row in T:
        cnts, sig = explain_W(src, wl_dir, row.get('query_file', ''))
        ops_W.append(cnts); sigs_W.append(sig)

    ops_Wp, sigs_Wp = [], []
    for row in WP:
        sql = row.get('sql', '').strip()
        if not sql or sql == 'SELECT 1':
            ops_Wp.append(Counter()); sigs_Wp.append(frozenset())
            continue
        cnts, sig = explain_ops(tgt, sql)
        ops_Wp.append(cnts); sigs_Wp.append(sig)

    avg_W  = avg_ops(ops_W)
    avg_Wp = avg_ops(ops_Wp)
    pdiv_W,  uniq_W  = plan_div(sigs_W)
    pdiv_Wp, uniq_Wp = plan_div(sigs_Wp)

    rt_T  = floats(T,  'runtime_ms', only_ok=True)
    rt_Tp = floats(TP, 'runtime_ms', only_ok=True)
    br_T  = floats(T,  'bytes_read', only_ok=False)
    br_Tp = floats(TP, 'bytes_read', only_ok=False)

    op_keys = ['avg_scans','avg_joins','avg_aggregations','avg_sorts','avg_total_ops']

    return {
        'experiment'                    : exp_name,
        'query_count_W'                 : len(T),
        'query_count_Wprime'            : len(WP),
        'query_diversity_W'             : fmt(div_W,  3),
        'query_diversity_Wprime'        : fmt(div_Wp, 3),
        'srr_W'                         : fmt(srr_W,  3),
        'srr_Wprime'                    : fmt(srr_Wp, 3),
        **{f'{k}_W' : fmt(avg_W[k])  for k in op_keys},
        **{f'{k}_Wprime': fmt(avg_Wp[k]) for k in op_keys},
        'plan_diversity_W'              : fmt(pdiv_W,  3),
        'plan_diversity_Wprime'         : fmt(pdiv_Wp, 3),
        'unique_plan_signatures_W'      : uniq_W,
        'unique_plan_signatures_Wprime' : uniq_Wp,
        'success_rate_percent_T'        : fmt(pct_ok(T),  1),
        'success_rate_percent_Tprime'   : fmt(pct_ok(TP), 1),
        'runtime_mean_ms_T'             : fmt(float(np.mean(rt_T))  if len(rt_T)  else 0.0, 1),
        'runtime_mean_ms_Tprime'        : fmt(float(np.mean(rt_Tp)) if len(rt_Tp) else 0.0, 1),
        'runtime_90th_pct_ms_T'         : fmt(float(np.percentile(rt_T,  90)) if len(rt_T)  else 0.0, 1),
        'runtime_90th_pct_ms_Tprime'    : fmt(float(np.percentile(rt_Tp, 90)) if len(rt_Tp) else 0.0, 1),
        'bytes_read_mean_T'             : fmt(float(np.mean(br_T))  if len(br_T)  else 0.0, 0),
        'bytes_read_mean_Tprime'        : fmt(float(np.mean(br_Tp)) if len(br_Tp) else 0.0, 0),
    }

parser = argparse.ArgumentParser()
parser.add_argument('--exp', default=None)
parser.add_argument('--exps', default=None)
parser.add_argument('--out', default='summary_scorecard.csv')
args = parser.parse_args()

exp_root = os.path.join(BASE, 'experiments')
if args.exps:
    experiments = [e.strip() for e in args.exps.split(',') if e.strip()]
elif args.exp:
    experiments = [args.exp]
else:
    experiments = sorted(d for d in os.listdir(exp_root)
           if os.path.isdir(os.path.join(exp_root, d)) and not d.startswith('_')
           and not d.endswith('_sizerank'))

print(f"\nEvaluating {len(experiments)} experiment(s) ...\n")

all_sc = []
for exp in experiments:
    print(f"  {exp}")
    sc = evaluate(exp, os.path.join(exp_root, exp, 'output'))
    if sc:
        all_sc.append(sc)

for con in _db_cons.values():
    try: con.close()
    except Exception: pass

if all_sc:
    eval_dir    = os.path.join(exp_root, '_evaluation')
    os.makedirs(eval_dir, exist_ok=True)
    summary_csv = os.path.join(eval_dir, args.out)
    with open(summary_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(all_sc[0].keys()))
        writer.writeheader()
        writer.writerows(all_sc)
    print(f"\nSummary saved  ->  {summary_csv}")
    print(f"Experiments    :  {len(all_sc)}")
    print(f"Columns        :  {len(all_sc[0])}")
else:
    print("No experiments evaluated.")
