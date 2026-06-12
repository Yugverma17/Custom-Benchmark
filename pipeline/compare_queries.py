import csv, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def load_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, newline='', encoding='utf-8', errors='replace') as f:
        return list(csv.DictReader(f))


def compare_experiment(exp_name, out_dir):
    T  = load_csv(os.path.join(out_dir, 'T.csv'))
    WP = load_csv(os.path.join(out_dir, 'W_prime_queries.csv'))
    if T is None or WP is None:
        return None, None

    wp_by_file = {r['query_file']: r for r in WP}

    rows = []
    for t in T:
        qf = t['query_file']
        wp = wp_by_file.get(qf)
        if wp is None:
            continue

        w_tables  = set(x for x in t.get('read_tables', '').split('|') if x)
        wp_tables = set(x for x in wp.get('target_tables', '').split('|') if x)
        common    = w_tables & wp_tables
        only_w    = w_tables - wp_tables
        only_wp   = wp_tables - w_tables
        union     = w_tables | wp_tables
        exact     = (w_tables == wp_tables)
        jacc      = (len(common) / len(union)) if union else 1.0

        rows.append({
            'query_file'         : qf,
            'tables_match_exact' : exact,
            'jaccard_similarity' : round(jacc, 3),
            'W_tables'           : '|'.join(sorted(w_tables)),
            'Wprime_tables'      : '|'.join(sorted(wp_tables)),
            'only_in_W'          : '|'.join(sorted(only_w)),
            'only_in_Wprime'     : '|'.join(sorted(only_wp)),
            'W_n_tables'         : len(w_tables),
            'Wprime_n_tables'    : len(wp_tables),
            'W_join_count'       : t.get('join_count', ''),
            'gen_status'         : wp.get('gen_status', ''),
        })

    n = len(rows)
    exact_count = sum(1 for r in rows if r['tables_match_exact'])
    avg_jacc    = sum(r['jaccard_similarity'] for r in rows) / n if n else 0.0

    summary = {
        'experiment'             : exp_name,
        'n_queries'              : n,
        'exact_table_match_count': exact_count,
        'exact_table_match_pct'  : round(100 * exact_count / n, 1) if n else 0.0,
        'avg_jaccard_similarity' : round(avg_jacc, 3),
    }
    return rows, summary


exp_root = os.path.join(BASE, 'experiments')
experiments = sorted(
    d for d in os.listdir(exp_root)
    if os.path.isdir(os.path.join(exp_root, d)) and not d.startswith('_')
)

print(f"\nComparing W vs W' table sets for {len(experiments)} experiment(s)...\n")

all_summaries = []
for exp in experiments:
    out_dir = os.path.join(exp_root, exp, 'output')
    rows, summary = compare_experiment(exp, out_dir)
    if rows is None:
        print(f"  SKIP {exp}: T.csv or W_prime_queries.csv missing")
        continue

    report_csv = os.path.join(out_dir, 'query_comparison.csv')
    with open(report_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    all_summaries.append(summary)
    print(f"  {exp:<28} exact_match={summary['exact_table_match_pct']:>5.1f}%  "
          f"avg_jaccard={summary['avg_jaccard_similarity']:.3f}  "
          f"({summary['exact_table_match_count']}/{summary['n_queries']})")

if all_summaries:
    eval_dir = os.path.join(exp_root, '_evaluation')
    os.makedirs(eval_dir, exist_ok=True)
    summary_csv = os.path.join(eval_dir, 'query_comparison_summary.csv')
    with open(summary_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(all_summaries[0].keys()))
        writer.writeheader()
        writer.writerows(all_summaries)
    print(f"\nPer-query reports -> experiments/<exp>/output/query_comparison.csv")
    print(f"Summary saved     -> {summary_csv}")
