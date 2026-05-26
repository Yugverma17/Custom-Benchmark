import argparse, csv, os, sys
import numpy as np
from collections import Counter
from scipy import stats as sp

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def load_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, newline='') as f:
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

def table_freq(rows, col):
    freq = Counter()
    for r in rows:
        for t in r.get(col, '').split('|'):
            t = t.strip()
            if t:
                freq[t] += 1
    return freq

def describe(arr):
    if len(arr) == 0:
        return dict(mean=0.0, median=0.0, p90=0.0, p99=0.0, std=0.0)
    return dict(
        mean   = float(np.mean(arr)),
        median = float(np.median(arr)),
        p90    = float(np.percentile(arr, 90)),
        p99    = float(np.percentile(arr, 99)),
        std    = float(np.std(arr)),
    )

def pct_ok(rows):
    if not rows:
        return 0.0
    return 100.0 * sum(1 for r in rows if r.get('status', '') == 'ok') / len(rows)

def jc_emd(a, b, bins=12):
    edges = np.arange(0, bins + 1)
    h1, _ = np.histogram(a, bins=edges)
    h2, _ = np.histogram(b, bins=edges)
    s1, s2 = h1.sum(), h2.sum()
    h1n = h1 / s1 if s1 > 0 else h1.astype(float)
    h2n = h2 / s2 if s2 > 0 else h2.astype(float)
    return float(np.sum(np.abs(np.cumsum(h1n) - np.cumsum(h2n))))

def entropy(freq):
    counts = np.array(list(freq.values()), dtype=float)
    if counts.sum() == 0:
        return 0.0
    p = counts / counts.sum()
    return float(-np.sum(p * np.log2(p + 1e-12)))

def ratio(a, b):
    return a / b if b else float('nan')

def spearman(x, y):
    if len(x) < 3:
        return float('nan'), float('nan')
    return sp.spearmanr(x, y)

def fmt(v, d=3):
    if isinstance(v, float) and np.isnan(v):
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
        print(f"SKIP {exp_name}: missing {missing}")
        return None

    w_jc  = floats(T, 'join_count')
    wp_jc = np.array([float(r['source_join_count']) for r in WP
                      if r.get('source_join_count', '').strip()])
    w_nt  = floats(T, 'n_tables')
    wp_nt = np.array([float(r['n_tables']) for r in WP
                      if r.get('n_tables', '').strip()])

    jc_emd_ww = jc_emd(w_jc, wp_jc)
    dw_jc = describe(w_jc);  dwp_jc = describe(wp_jc)
    dw_nt = describe(w_nt);  dwp_nt = describe(wp_nt)

    w_br  = floats(T, 'bytes_read')
    wp_br = np.array([float(r['source_bytes_read']) for r in WP
                      if r.get('source_bytes_read', '').strip()])
    dw_br  = describe(w_br)
    dwp_br = describe(wp_br)
    bytes_ratio_ww = ratio(dwp_br['mean'], dw_br['mean'])

    freq_w  = table_freq(T,  'read_tables')
    freq_wp = table_freq(WP, 'target_tables')
    uniq_w  = set(freq_w)
    uniq_wp = set(freq_wp)
    ent_w   = entropy(freq_w)
    ent_wp  = entropy(freq_wp)
    jaccard_ww = len(uniq_w & uniq_wp) / len(uniq_w | uniq_wp) if (uniq_w | uniq_wp) else 0.0

    div_w  = len({frozenset(r.get('read_tables',  '').split('|')) for r in T})  / len(T)
    div_wp = len({frozenset(r.get('target_tables','').split('|')) for r in WP}) / len(WP)

    common_ww = sorted(uniq_w & uniq_wp)
    spear_ww = float('nan')
    if len(common_ww) >= 3:
        spear_ww, _ = spearman([freq_w[t]  for t in common_ww],
                               [freq_wp[t] for t in common_ww])

    t_ok_pct  = pct_ok(T)
    tp_ok_pct = pct_ok(TP)

    rt_T  = floats(T,  'runtime_ms', only_ok=True)
    rt_Tp = floats(TP, 'runtime_ms', only_ok=True)
    dT  = describe(rt_T)
    dTp = describe(rt_Tp)

    ks_stat = float('nan')
    if len(rt_T) > 1 and len(rt_Tp) > 1:
        ks_stat, _ = sp.ks_2samp(rt_T, rt_Tp)

    t_map  = {r['query_file']: float(r['runtime_ms'])
              for r in T  if r.get('status') == 'ok'}
    tp_map = {r['query_file']: float(r['runtime_ms'])
              for r in TP if r.get('status') == 'ok'}
    common_q = sorted(set(t_map) & set(tp_map))
    pearson_r = spear_tt_r = float('nan')
    if len(common_q) >= 3:
        rv_T  = np.array([t_map[q]  for q in common_q])
        rv_Tp = np.array([tp_map[q] for q in common_q])
        pearson_r,  _ = sp.pearsonr(rv_T, rv_Tp)
        spear_tt_r, _ = spearman(rv_T, rv_Tp)

    jc_T  = floats(T,  'join_count')
    jc_Tp = floats(TP, 'join_count')
    jc_emd_tt = jc_emd(jc_T, jc_Tp)

    freq_T  = table_freq(T,  'read_tables')
    freq_Tp = table_freq(TP, 'read_tables')
    common_tt = sorted(set(freq_T) & set(freq_Tp))
    spear_tt_tbl = float('nan')
    if len(common_tt) >= 3:
        spear_tt_tbl, _ = spearman([freq_T[t]  for t in common_tt],
                                   [freq_Tp[t] for t in common_tt])

    sc = {
        'experiment'                                    : exp_name,
        'workload_query_count'                          : len(T),
        'workload_prime_query_count'                    : len(WP),
        'T_success_rate_percent'                        : fmt(t_ok_pct,  1),
        'T_prime_success_rate_percent'                  : fmt(tp_ok_pct, 1),
        'join_count_emd_W_Wprime'                       : fmt(jc_emd_ww),
        'table_jaccard_similarity_W_Wprime'             : fmt(jaccard_ww),
        'table_frequency_spearman_r_W_Wprime'           : fmt(spear_ww),
        'table_access_entropy_bits_W'                   : fmt(ent_w),
        'table_access_entropy_bits_Wprime'              : fmt(ent_wp),
        'bytes_read_ratio_Wprime_over_W'                : fmt(bytes_ratio_ww, 2),
        'runtime_median_ratio_Tprime_over_T'            : fmt(ratio(dTp['median'], dT['median']), 2),
        'runtime_90th_percentile_ratio_Tprime_over_T'   : fmt(ratio(dTp['p90'],    dT['p90']),    2),
        'runtime_ks_statistic'                          : fmt(ks_stat),
        'runtime_pearson_r'                             : fmt(pearson_r),
        'runtime_spearman_r'                            : fmt(spear_tt_r),
        'join_count_emd_T_Tprime'                       : fmt(jc_emd_tt),
        'table_frequency_spearman_r_T_Tprime'           : fmt(spear_tt_tbl),
        'query_diversity_W'                             : fmt(div_w),
        'query_diversity_Wprime'                        : fmt(div_wp),
        'unique_tables_accessed_W'                      : len(uniq_w),
        'unique_tables_accessed_Wprime'                 : len(uniq_wp),
    }

    return sc


parser = argparse.ArgumentParser()
parser.add_argument('--exp', default=None, help='single experiment (default: all)')
args = parser.parse_args()

exp_root = os.path.join(BASE, 'experiments')
if args.exp:
    experiments = [args.exp]
else:
    experiments = sorted(
        d for d in os.listdir(exp_root)
        if os.path.isdir(os.path.join(exp_root, d)) and not d.startswith('_')
    )

eval_dir = os.path.join(exp_root, '_evaluation')
os.makedirs(eval_dir, exist_ok=True)

all_sc = []
for exp in experiments:
    out_dir = os.path.join(exp_root, exp, 'output')
    sc = evaluate(exp, out_dir)
    if sc is None:
        continue
    all_sc.append(sc)
    print(f"Evaluated: {exp}")

if all_sc:
    summary_csv = os.path.join(eval_dir, 'summary_scorecard.csv')
    with open(summary_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(all_sc[0].keys()))
        writer.writeheader()
        writer.writerows(all_sc)
    print(f"\nSummary saved -> {summary_csv}")
