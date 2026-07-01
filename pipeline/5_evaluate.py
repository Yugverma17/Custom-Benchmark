import argparse, csv, os
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, newline='', encoding='utf-8', errors='replace') as f:
        return list(csv.DictReader(f))


def pct_ok(rows):
    if not rows:
        return 0.0
    return 100.0 * sum(1 for r in rows if r.get('status', '') == 'ok') / len(rows)


def query_diversity(rows, table_col):
    if not rows:
        return 0.0
    unique_sets = {frozenset(r.get(table_col, '').split('|')) for r in rows}
    return len(unique_sets) / len(rows)


def q_error(a: float, b: float):
    if a <= 0 or b <= 0:
        return None
    return max(a / b, b / a)


def compute_qerrors(T, TP):
    """Pairs T and T' rows by query_file (both must have status == ok) and
    returns the per-query q-error = max(x/x', x'/x) lists for runtime and bytes_read.
    The two metrics are scored independently, so a missing/zero value in one
    (e.g. bytes_read) doesn't also drop a valid pairing for the other."""
    tp_by_file = {r['query_file']: r for r in TP if r.get('status') == 'ok'}
    cpu_qe, bytes_qe = [], []
    for t in T:
        if t.get('status') != 'ok':
            continue
        tp = tp_by_file.get(t.get('query_file'))
        if tp is None:
            continue
        try:
            qc = q_error(float(t['runtime_ms']), float(tp['runtime_ms']))
            if qc is not None:
                cpu_qe.append(qc)
        except (ValueError, KeyError):
            pass
        try:
            qb = q_error(float(t['bytes_read']), float(tp['bytes_read']))
            if qb is not None:
                bytes_qe.append(qb)
        except (ValueError, KeyError):
            pass
    return cpu_qe, bytes_qe


def qerror_stats(values, prefix):
    if not values:
        return {f'{prefix}_mean': 'n/a', f'{prefix}_median': 'n/a',
                f'{prefix}_p90': 'n/a', f'{prefix}_max': 'n/a'}
    arr = np.array(values)
    return {
        f'{prefix}_mean'  : fmt(float(np.mean(arr)), 2),
        f'{prefix}_median': fmt(float(np.median(arr)), 2),
        f'{prefix}_p90'   : fmt(float(np.percentile(arr, 90)), 2),
        f'{prefix}_max'   : fmt(float(np.max(arr)), 2),
    }


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

    div_W  = query_diversity(T,  'read_tables')
    div_Wp = query_diversity(WP, 'target_tables')

    cpu_qe, bytes_qe = compute_qerrors(T, TP)

    return {
        'experiment'                   : exp_name,
        'query_diversity_W'            : fmt(div_W,  3),
        'query_diversity_Wprime'       : fmt(div_Wp, 3),
        'success_rate_percent_T'       : fmt(pct_ok(T),  1),
        'success_rate_percent_Tprime'  : fmt(pct_ok(TP), 1),
        **qerror_stats(cpu_qe,   'cpu_time_qerror'),
        **qerror_stats(bytes_qe, 'bytes_qerror'),
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
