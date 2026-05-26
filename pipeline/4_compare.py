import argparse, csv, os, sys
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

parser = argparse.ArgumentParser()
parser.add_argument('--exp', required=True, help='experiment name')
args = parser.parse_args()

out_dir   = os.path.join(BASE, 'experiments', args.exp, 'output')
t_csv     = os.path.join(out_dir, 'T.csv')
tp_csv    = os.path.join(out_dir, 'T_prime.csv')
score_csv = os.path.join(out_dir, 'scorecard.csv')

for p in [t_csv, tp_csv]:
    if not os.path.exists(p):
        print(f"ERROR: {p} not found. Run steps 1-3 first.")
        sys.exit(1)

def load(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))

T  = load(t_csv)
Tp = load(tp_csv)

def nums(rows, col, only_ok=True):
    vals = []
    for r in rows:
        if only_ok and r.get('status','') != 'ok':
            continue
        try:
            vals.append(float(r[col]))
        except (ValueError, KeyError):
            pass
    return np.array(vals) if vals else np.array([0.0])

def pct_ok(rows):
    if not rows:
        return 0.0
    return 100.0 * sum(1 for r in rows if r.get('status','') == 'ok') / len(rows)

def describe(arr):
    if len(arr) == 0:
        return {'mean': 0, 'median': 0, 'p90': 0, 'p99': 0, 'std': 0}
    return {
        'mean'  : float(np.mean(arr)),
        'median': float(np.median(arr)),
        'p90'   : float(np.percentile(arr, 90)),
        'p99'   : float(np.percentile(arr, 99)),
        'std'   : float(np.std(arr)),
    }

def ratio(a, b):
    return a / b if b != 0 else float('nan')

def pct_diff(a, b):
    return 100.0 * (a - b) / b if b != 0 else float('nan')

rt_T   = nums(T,  'runtime_ms')
rt_Tp  = nums(Tp, 'runtime_ms')
jc_T   = nums(T,  'join_count', only_ok=False)
jc_Tp  = nums(Tp, 'join_count', only_ok=False)
br_T   = nums(T,  'bytes_read', only_ok=False)
br_Tp  = nums(Tp, 'bytes_read', only_ok=False)
nt_T   = nums(T,  'n_tables',   only_ok=False)
nt_Tp  = nums(Tp, 'n_tables',   only_ok=False)

def table_freq(rows):
    freq = {}
    for r in rows:
        for t in r.get('read_tables','').split('|'):
            t = t.strip()
            if t:
                freq[t] = freq.get(t, 0) + 1
    return freq

def jc_hist(arr, bins=11):
    counts, _ = np.histogram(arr, bins=np.arange(0, bins + 1))
    s = counts.sum()
    return counts / s if s > 0 else counts.astype(float)

def emd_1d(h1, h2):
    return float(np.sum(np.abs(np.cumsum(h1) - np.cumsum(h2))))

jc_T_hist  = jc_hist(jc_T)
jc_Tp_hist = jc_hist(jc_Tp)
jc_emd     = emd_1d(jc_T_hist, jc_Tp_hist)

dT  = describe(rt_T)
dTp = describe(rt_Tp)
djT  = describe(jc_T)
djTp = describe(jc_Tp)
dnT  = describe(nt_T)
dnTp = describe(nt_Tp)
dbT  = describe(br_T)
dbTp = describe(br_Tp)

score_rows = [
    {'metric': 'T_success_rate_percent',
     'T': f"{pct_ok(T):.2f}", 'T_prime': f"{pct_ok(Tp):.2f}"},
    {'metric': 'runtime_mean_milliseconds',
     'T': f"{dT['mean']:.2f}", 'T_prime': f"{dTp['mean']:.2f}"},
    {'metric': 'runtime_median_milliseconds',
     'T': f"{dT['median']:.2f}", 'T_prime': f"{dTp['median']:.2f}"},
    {'metric': 'runtime_90th_percentile_milliseconds',
     'T': f"{dT['p90']:.2f}", 'T_prime': f"{dTp['p90']:.2f}"},
    {'metric': 'runtime_99th_percentile_milliseconds',
     'T': f"{dT['p99']:.2f}", 'T_prime': f"{dTp['p99']:.2f}"},
    {'metric': 'join_count_mean',
     'T': f"{djT['mean']:.2f}", 'T_prime': f"{djTp['mean']:.2f}"},
    {'metric': 'join_count_median',
     'T': f"{djT['median']:.2f}", 'T_prime': f"{djTp['median']:.2f}"},
    {'metric': 'join_count_earth_movers_distance',
     'T': '0.0000', 'T_prime': f"{jc_emd:.4f}"},
    {'metric': 'number_of_tables_mean',
     'T': f"{dnT['mean']:.2f}", 'T_prime': f"{dnTp['mean']:.2f}"},
    {'metric': 'bytes_read_mean',
     'T': f"{dbT['mean']:.0f}", 'T_prime': f"{dbTp['mean']:.0f}"},
    {'metric': 'bytes_read_median',
     'T': f"{dbT['median']:.0f}", 'T_prime': f"{dbTp['median']:.0f}"},
]

with open(score_csv, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['metric', 'T', 'T_prime'])
    writer.writeheader()
    writer.writerows(score_rows)

print(f"Scorecard saved -> {score_csv}")
