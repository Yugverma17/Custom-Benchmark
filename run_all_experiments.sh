#!/usr/bin/env bash
set -e
BASE="G:/Yug/Benchmark/Custom-Benchmark"
cd "$BASE"

EXPERIMENTS=(
  "imdb   imdb   imdb_to_imdb"
  "imdb   tpch   imdb_to_tpch"
  "imdb   tpcds  imdb_to_tpcds"
  "tpch   tpch   tpch_to_tpch"
  "tpch   imdb   tpch_to_imdb"
  "tpch   tpcds  tpch_to_tpcds"
  "tpcds  tpcds  tpcds_to_tpcds"
  "tpcds  imdb   tpcds_to_imdb"
  "tpcds  tpch   tpcds_to_tpch"
)

for entry in "${EXPERIMENTS[@]}"; do
  read -r src tgt exp <<< "$entry"

  echo ""
  echo "========================================"
  echo " Experiment: $exp  ($src -> $tgt)"
  echo "========================================"

  OUT="$BASE/experiments/$exp/output"

  if [ ! -f "$OUT/T.csv" ]; then
    echo "  SKIP: T.csv missing for $exp -- run Step 1 first"
    continue
  fi

  rm -f "$OUT/W_prime_queries.csv" "$OUT/T_prime.csv"

  echo "  [Step 2] Generating W'..."
  python pipeline/2_generate_workload.py --source-db "$src" --target-db "$tgt" --exp "$exp"

  echo "  [Step 3] Executing W' on $tgt..."
  python pipeline/3_execute_workload_prime.py --db "$tgt" --exp "$exp"

  echo "  Done: $exp"
done

echo ""
echo "========================================"
echo " Evaluating all experiments..."
echo "========================================"
rm -f "$BASE/experiments/_evaluation/summary_scorecard.csv"
python pipeline/5_evaluate.py

echo ""
echo "ALL DONE. Summary -> experiments/_evaluation/summary_scorecard.csv"
