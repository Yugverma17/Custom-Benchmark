import duckdb, os, sys, time

BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR   = os.path.join(BASE, 'databases')
WL_TPCH  = os.path.join(BASE, 'workloads', 'tpch')
WL_TPCDS = os.path.join(BASE, 'workloads', 'tpcds')

os.makedirs(DB_DIR,   exist_ok=True)
os.makedirs(WL_TPCH,  exist_ok=True)
os.makedirs(WL_TPCDS, exist_ok=True)

print("\nSetting up TPC-H (SF=1)")
tpch_path = os.path.join(DB_DIR, 'tpch.duckdb')

if os.path.exists(tpch_path):
    print(f"  tpch.duckdb already exists, skipping.")
else:
    print("  Generating TPC-H data...")
    t0 = time.time()
    con = duckdb.connect(tpch_path)
    con.execute("INSTALL tpch; LOAD tpch")
    con.execute("CALL dbgen(sf=1)")
    tables = ['lineitem','orders','partsupp','customer','part','supplier','nation','region']
    for t in tables:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"    {t:<20} {n:>12,}")
    con.close()
    print(f"  Done in {time.time()-t0:.1f}s")

con = duckdb.connect(tpch_path)
con.execute("LOAD tpch")
rows = con.execute("SELECT query_nr, query FROM tpch_queries()").fetchall()
con.close()
saved = 0
for qnr, qsql in rows:
    path = os.path.join(WL_TPCH, f"q{qnr:02d}.sql")
    if not os.path.exists(path):
        with open(path, 'w') as f:
            f.write(qsql.strip() + '\n')
        saved += 1
print(f"  TPC-H queries: {saved} new files saved ({len(rows)} total)")

print("\nSetting up TPC-DS (SF=1)")
tpcds_path = os.path.join(DB_DIR, 'tpcds.duckdb')

if os.path.exists(tpcds_path):
    print(f"  tpcds.duckdb already exists, skipping.")
else:
    print("  Generating TPC-DS data (takes 3-5 min)...")
    t0 = time.time()
    con = duckdb.connect(tpcds_path)
    con.execute("INSTALL tpcds; LOAD tpcds")
    con.execute("CALL dsdgen(sf=1)")
    tables = ['store_sales','web_sales','catalog_sales','store_returns',
              'web_returns','catalog_returns','inventory','customer',
              'date_dim','item','store','customer_demographics',
              'household_demographics','customer_address']
    for t in tables:
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"    {t:<30} {n:>12,}")
        except Exception as e:
            print(f"    {t:<30}  ERROR: {e}")
    con.close()
    print(f"  Done in {time.time()-t0:.1f}s")

con = duckdb.connect(tpcds_path)
con.execute("LOAD tpcds")
try:
    rows = con.execute("SELECT query_nr, query FROM tpcds_queries()").fetchall()
    con.close()
    saved = 0
    for qnr, qsql in rows:
        path = os.path.join(WL_TPCDS, f"q{qnr:02d}.sql")
        if not os.path.exists(path):
            with open(path, 'w') as f:
                f.write(qsql.strip() + '\n')
            saved += 1
    print(f"  TPC-DS queries: {saved} new files saved ({len(rows)} total)")
except Exception as e:
    con.close()
    print(f"  WARNING: Could not extract TPC-DS queries: {e}")

print(f"\nDone.")
print(f"  TPC-H  -> {tpch_path}")
print(f"  TPC-DS -> {tpcds_path}")
print(f"  IMDB   -> {os.path.join(DB_DIR, 'imdb.duckdb')}  (symlink to existing DB)")
