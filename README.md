# Custom Benchmark Synthesis

This project synthesises a workload W' for a target database D' given a source database D, its workload W, and the execution trace T from running W on D. The synthesised workload is then evaluated for structural and execution fidelity against the original.

The experiments use three databases IMDB (JOB workload), TPC-H, and TPC-DS to run all 9 source→target combinations as a proof of concept.

---

## Project Structure

```
.
├── config.py                        # paths, timeouts, DB-workload mapping
├── requirements.txt
├── run_all_experiments.sh           # runs steps 2-3 for all 9 experiments, then evaluates
├── databases/                       # .duckdb files (not on GitHub, see below)
├── workloads/
│   ├── job/                         
│   ├── tpch/                        
│   └── tpcds/                       
├── schemas/
│   ├── imdb_schema.py
│   ├── tpch_schema.py
│   └── tpcds_schema.py
├── pipeline/
│   ├── utils.py                     # shared helpers
│   ├── 1_execute_workload.py        # runs W on D, produces T.csv
│   ├── 2_generate_workload.py       # synthesises W', produces W_prime_queries.csv
│   ├── 3_execute_workload_prime.py  # runs W' on D', produces T_prime.csv
│   └── 5_evaluate.py               # evaluates all experiments, produces summary_scorecard.csv
├── setup/
│   ├── setup_databases.py           # generates TPC-H and TPC-DS databases
│   ├── setup_imdb.py                # loads IMDB CSVs into DuckDB
│   └── setup_workloads.py
└── experiments/
    ├── imdb_to_imdb/output/         # T.csv, W_prime_queries.csv, T_prime.csv
    ├── imdb_to_tpch/output/
    ├── ...
    └── _evaluation/
        └── summary_scorecard.csv    # final results across all experiments
```

---

## What is Not on GitHub

The following are excluded from version control because of size:

 Path 
| `databases/*.duckdb` 
| `workloads/job/*.sql` 
| `databases/imdb_csvs/` 

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Requires Python 3.11+ and DuckDB 1.0+.

### 2. TPC-H and TPC-DS databases

Both databases are generated automatically using DuckDB's built-in extensions at scale factor 1. This also writes the SQL workload files into `workloads/tpch/` and `workloads/tpcds/`.

```bash
python setup/setup_databases.py
```

### 3. IMDB database (Join Order Benchmark)

The IMDB database is built from the CSV dump released by the Institut de Montefiore. Download the archive and extract the CSVs, then run:

```bash
python setup/setup_imdb.py
```

**Download the IMDB CSV dump:**
- Source: [https://github.com/gregrahn/join-order-benchmark](https://github.com/gregrahn/join-order-benchmark)  
  (follow the `get_imdb_data.sh` script or download `imdb.tgz` directly from the linked source)
- Extract to `databases/imdb_csvs/`
- The setup script reads from that directory and writes `databases/imdb.duckdb`

## Running the Pipeline

All steps are run from the project root. The experiment name convention is `{source-db}_to_{target-db}`.

### Step 1 — Execute W on D (produces T.csv)

Run the original workload on the source database. Only needed once per source DB.

```bash
python pipeline/1_execute_workload.py --db imdb --workload job --exp imdb_to_imdb
python pipeline/1_execute_workload.py --db tpch  --workload tpch --exp tpch_to_tpch
python pipeline/1_execute_workload.py --db tpcds --workload tpcds --exp tpcds_to_tpcds
```

For cross-database experiments the T.csv is shared. Copy or symlink as needed:

```bash
cp experiments/imdb_to_imdb/output/T.csv experiments/imdb_to_tpch/output/T.csv
cp experiments/imdb_to_imdb/output/T.csv experiments/imdb_to_tpcds/output/T.csv
```

### Step 2 — Generate W' (produces W_prime_queries.csv)

Synthesises a workload for the target database using the RedBench generation strategy: normalised selectivity, size-rank table mapping, random walk on the FK graph, and actual column quantile statistics from D'.

```bash
python pipeline/2_generate_workload.py --source-db imdb --target-db tpch --exp imdb_to_tpch
```


### Step 3 — Execute W' on D' (produces T_prime.csv)

```bash
python pipeline/3_execute_workload_prime.py --db tpch --exp imdb_to_tpch
```

### Step 4 (optional) — Evaluate a single experiment

`5_evaluate.py` supports a `--exp` flag to run just one experiment without regenerating the full summary.

```bash
python pipeline/5_evaluate.py --exp imdb_to_tpch
```

### Step 5 — Evaluate all experiments

Reads T.csv, W_prime_queries.csv, and T_prime.csv for every experiment and writes a single summary.

```bash
python pipeline/5_evaluate.py
```

Output: `experiments/_evaluation/summary_scorecard.csv`

---

## Running Everything at Once

After Step 1 is done for all three source databases, re-run Steps 2–3 for all 9 experiments and evaluate in one go:

```bash
bash run_all_experiments.sh
```

---

## Output — summary_scorecard.csv

One row per experiment, 29 columns:

| Column group | Columns |
|---|---|
| Query counts | `query_count_W`, `query_count_Wprime` |
| Query diversity | `query_diversity_W/Wprime` — fraction of queries with a unique table-set |
| Scan-Set Repetition Rate | `srr_W/Wprime` — fraction of queries whose table-set appears in more than one query |
| Operator counts (EXPLAIN) | `avg_scans/joins/aggregations/sorts/total_ops_W/Wprime` |
| Plan diversity | `plan_diversity_W/Wprime`, `unique_plan_signatures_W/Wprime` |
| Execution — success | `success_rate_percent_T/Tprime` |
| Execution — runtime | `runtime_mean_ms_T/Tprime`, `runtime_90th_pct_ms_T/Tprime` |
| Execution — data volume | `bytes_read_mean_T/Tprime` |

---

## Experiments

Nine experiments cover all source→target combinations:

| Experiment | Source DB | Target DB |
|---|---|---|
| imdb_to_imdb | IMDB (JOB) | IMDB |
| imdb_to_tpch | IMDB (JOB) | TPC-H |
| imdb_to_tpcds | IMDB (JOB) | TPC-DS |
| tpch_to_imdb | TPC-H | IMDB |
| tpch_to_tpch | TPC-H | TPC-H |
| tpch_to_tpcds | TPC-H | TPC-DS |
| tpcds_to_imdb | TPC-DS | IMDB |
| tpcds_to_tpch | TPC-DS | TPC-H |
| tpcds_to_tpcds | TPC-DS | TPC-DS |

