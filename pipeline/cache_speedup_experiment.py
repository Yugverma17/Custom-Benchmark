"""
Cache speedup experiment for TPC-H SF=10.

For both the original 22 TPC-H queries (W) and the RedBench synthetic queries (W'):
  1. Run every query once  — pass 1 (cold: OS page cache not yet hot for this DB)
  2. Run every query again — pass 2 (warm: pages loaded by pass 1 are now in OS cache)
  speedup = pass1_time / pass2_time

High variance in speedup across queries means unpredictable data-access patterns.
If W' has significantly higher speedup variance than W, the synthetic workload is
not representative of the original — supporting dropping RedBench.

Progress is saved after every query so the script can be safely interrupted
and re-run — it will resume from where it left off.

Output:
  experiments/cache_speedup_sf10/results_W.csv
  experiments/cache_speedup_sf10/results_Wprime.csv
  experiments/cache_speedup_sf10/summary.csv
  experiments/cache_speedup_sf10/run.log
"""

import csv, os, sys, time, threading
import numpy as np
import duckdb

BASE    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, 'experiments', 'cache_speedup_sf10')
DB_PATH = os.path.join(BASE, 'databases', 'tpch_sf10.duckdb')
WL_DIR  = os.path.join(BASE, 'workloads', 'tpch')
WP_CSV  = os.path.join(BASE, 'experiments', 'tpch_to_tpch', 'output', 'W_prime_queries.csv')
TIMEOUT = 600                                          

os.makedirs(OUT_DIR, exist_ok=True)

LOG_PATH = os.path.join(OUT_DIR, 'run.log')
_log = open(LOG_PATH, 'a', buffering=1)

def log(msg):
    ts = time.strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    _log.write(line + '\n')


                                                                                

def build_sf10():
    if os.path.exists(DB_PATH):
        log(f"tpch_sf10.duckdb already exists, skipping build.")
        return
    log("Building TPC-H SF=10 (this takes ~5-10 min) ...")
    t0 = time.time()
    con = duckdb.connect(DB_PATH)
    con.execute("INSTALL tpch; LOAD tpch")
    con.execute("CALL dbgen(sf=10)")
    for tbl in ['lineitem','orders','partsupp','customer','part','supplier','nation','region']:
        n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        log(f"  {tbl:<20} {n:>14,}")
    con.close()
    size_gb = os.path.getsize(DB_PATH) / 1e9
    log(f"Done in {time.time()-t0:.0f}s  ({size_gb:.1f} GB)")


                                                                               

def run_query(sql):
    con = duckdb.connect(DB_PATH, read_only=True)
    con.execute("SET memory_limit='16GB'; SET threads=4")
    con.execute("LOAD tpch")

    interrupted = [False]

    def do_interrupt():
        interrupted[0] = True
        try:
            con.interrupt()
        except Exception:
            pass

    timer = threading.Timer(TIMEOUT, do_interrupt)
    t0 = time.perf_counter()
    timer.start()
    try:
        con.execute(sql).fetchall()
        timer.cancel()
        rt = (time.perf_counter() - t0) * 1000
        status, error = 'ok', ''
    except Exception as e:
        timer.cancel()
        rt = (time.perf_counter() - t0) * 1000
        status = 'timeout' if interrupted[0] else 'error'
        error  = str(e)[:120]
    finally:
        try:
            con.close()
        except Exception:
            pass

    return {'runtime_ms': round(rt, 2), 'status': status, 'error': error}


                                                                               

RESULT_FIELDS = ['query', 'pass1_status', 'pass1_ms', 'pass2_status', 'pass2_ms', 'cache_speedup']

def load_existing(path):
    if not os.path.exists(path):
        return {}
    with open(path, newline='') as f:
        return {r['query']: r for r in csv.DictReader(f)}

def append_row(path, row):
    write_header = not os.path.exists(path)
    with open(path, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow(row)

def two_pass(queries, label, out_csv):
    log(f"\n{'='*60}")
    log(f"  {label}  ({len(queries)} queries)")
    log(f"{'='*60}")

    existing = load_existing(out_csv)
    speedups = []
    for r in existing.values():
        if r['cache_speedup'] not in ('n/a', ''):
            speedups.append(float(r['cache_speedup']))

    todo = [q for q in queries if q['name'] not in existing]
    if existing:
        log(f"  Resuming: {len(existing)} already done, {len(todo)} remaining")

                                                                         
    for i, q in enumerate(todo, 1):
        log(f"\n  [{i:>2}/{len(todo)}] {q['name']} ...")

        r1 = run_query(q['sql'])
        log(f"    cold: {r1['status']:<8}  {r1['runtime_ms']:>9.1f} ms")

        r2 = run_query(q['sql'])
        log(f"    warm: {r2['status']:<8}  {r2['runtime_ms']:>9.1f} ms")

        if r1['status'] == 'ok' and r2['status'] == 'ok' and r2['runtime_ms'] > 0:
            sp = round(r1['runtime_ms'] / r2['runtime_ms'], 4)
        else:
            sp = None
        if sp is not None:
            speedups.append(sp)

        row = {
            'query'        : q['name'],
            'pass1_status' : r1['status'],
            'pass1_ms'     : r1['runtime_ms'],
            'pass2_status' : r2['status'],
            'pass2_ms'     : r2['runtime_ms'],
            'cache_speedup': sp if sp is not None else 'n/a',
        }
        append_row(out_csv, row)
        log(f"    speedup={sp}")

    return speedups


                                                                                

def stats(values, name):
    if not values:
        return {'workload': name, 'n_pairs': 0}
    a = np.array(values, dtype=float)
    mean = float(np.mean(a))
    std  = float(np.std(a))
    return {
        'workload'       : name,
        'n_pairs'        : len(values),
        'mean_speedup'   : round(mean, 3),
        'median_speedup' : round(float(np.median(a)), 3),
        'std_speedup'    : round(std, 3),
        'cv_speedup'     : round(std / mean, 3) if mean > 0 else 'n/a',
        'min_speedup'    : round(float(np.min(a)), 3),
        'max_speedup'    : round(float(np.max(a)), 3),
        'p10_speedup'    : round(float(np.percentile(a, 10)), 3),
        'p90_speedup'    : round(float(np.percentile(a, 90)), 3),
    }


                                                                                

log("=" * 60)
log("  Cache Speedup Experiment  —  TPC-H SF=10")
log("=" * 60)

log("\n[1/4] Checking TPC-H SF=10 database ...")
build_sf10()

log("\n[2/4] Loading query sets ...")
W_queries = []
for fname in sorted(os.listdir(WL_DIR)):
    if fname.endswith('.sql'):
        W_queries.append({'name': fname, 'sql': open(os.path.join(WL_DIR, fname)).read()})
log(f"  W  (original TPC-H): {len(W_queries)} queries")

with open(WP_CSV, newline='') as f:
    wp_rows = list(csv.DictReader(f))
WP_queries = [{'name': r['query_file'], 'sql': r['sql']} for r in wp_rows]
log(f"  W' (RedBench synth): {len(WP_queries)} queries")

log("\n[3/4] Running W (original TPC-H) ...")
speedups_W  = two_pass(W_queries,  "W  — original TPC-H",    os.path.join(OUT_DIR, 'results_W.csv'))

log("\n[4/4] Running W' (RedBench synthetic) ...")
speedups_WP = two_pass(WP_queries, "W' — RedBench synthetic", os.path.join(OUT_DIR, 'results_Wprime.csv'))

         
s_W  = stats(speedups_W,  'W_original')
s_WP = stats(speedups_WP, "W'_synthetic")
with open(os.path.join(OUT_DIR, 'summary.csv'), 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(s_W.keys()))
    w.writeheader()
    w.writerows([s_W, s_WP])

log("\n" + "="*60)
log("  RESULTS")
log("="*60)
for s in [s_W, s_WP]:
    log(f"\n  {s['workload']}")
    for k, v in s.items():
        if k != 'workload':
            log(f"    {k:<20} {v}")

log("\n  VERDICT")
if speedups_W and speedups_WP:
    cv_W  = float(np.std(speedups_W))  / max(float(np.mean(speedups_W)),  1e-9)
    cv_WP = float(np.std(speedups_WP)) / max(float(np.mean(speedups_WP)), 1e-9)
    log(f"  CV (W  original) : {cv_W:.3f}")
    log(f"  CV (W' synthetic): {cv_WP:.3f}")
    ratio = cv_WP / cv_W if cv_W > 0 else float('inf')
    if ratio > 1.5:
        log(f"  W' has {ratio:.1f}x higher speedup variance than W.")
        log("  -> Synthetic queries have inconsistent data-access patterns.")
        log("  -> RedBench workload fidelity is questionable.")
    else:
        log(f"  Speedup variance is similar (ratio {ratio:.2f}x).")
        log("  -> Synthetic queries show comparable cache behaviour to originals.")

log(f"\nDone. Outputs -> {OUT_DIR}")
_log.close()
