"""
setup_imdb.py
Downloads the JOB (Join Order Benchmark) IMDB CSV dataset and imports
it into a DuckDB file at databases/imdb.duckdb.

Download size  : ~1.8 GB compressed
Uncompressed   : ~7 GB
Import time    : ~5-10 minutes
"""

import duckdb, os, sys, time, urllib.request, tarfile, shutil, ssl

BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR   = os.path.join(BASE, "databases")
CSV_DIR  = os.path.join(DB_DIR, "imdb_csvs")
IMDB_DB  = os.path.join(DB_DIR, "imdb.duckdb")
TGZ_PATH = os.path.join(DB_DIR, "imdb.tgz")

DOWNLOAD_URLS = [
    "http://homepages.cwi.nl/~boncz/job/imdb.tgz",
    "https://event.cwi.nl/da/job/imdb.tgz",
]

# ── Schema definitions ────────────────────────────────────────────────────────
DDL = {
"aka_name": """
    id              INTEGER NOT NULL,
    person_id       INTEGER NOT NULL,
    name            TEXT,
    imdb_index      VARCHAR(3),
    name_pcode_cf   VARCHAR(5),
    name_pcode_nf   VARCHAR(5),
    surname_pcode   VARCHAR(5),
    md5sum          VARCHAR(32)
""",
"aka_title": """
    id              INTEGER NOT NULL,
    movie_id        INTEGER NOT NULL,
    title           TEXT,
    imdb_index      VARCHAR(4),
    kind_id         INTEGER NOT NULL,
    production_year INTEGER,
    phonetic_code   VARCHAR(5),
    episode_of_id   INTEGER,
    season_nr       INTEGER,
    episode_nr      INTEGER,
    note            VARCHAR(72),
    md5sum          VARCHAR(32)
""",
"cast_info": """
    id              INTEGER NOT NULL,
    person_id       INTEGER NOT NULL,
    movie_id        INTEGER NOT NULL,
    person_role_id  INTEGER,
    note            TEXT,
    nr_order        INTEGER,
    role_id         INTEGER NOT NULL
""",
"char_name": """
    id              INTEGER NOT NULL,
    name            TEXT NOT NULL,
    imdb_index      VARCHAR(2),
    imdb_id         INTEGER,
    name_pcode_nf   VARCHAR(5),
    surname_pcode   VARCHAR(5),
    md5sum          VARCHAR(32)
""",
"comp_cast_type": """
    id              INTEGER NOT NULL,
    kind            VARCHAR(32) NOT NULL
""",
"company_name": """
    id              INTEGER NOT NULL,
    name            TEXT NOT NULL,
    country_code    VARCHAR(6),
    imdb_id         INTEGER,
    name_pcode_nf   VARCHAR(5),
    name_pcode_sf   VARCHAR(5),
    md5sum          VARCHAR(32)
""",
"company_type": """
    id              INTEGER NOT NULL,
    kind            VARCHAR(32) NOT NULL
""",
"complete_cast": """
    id              INTEGER NOT NULL,
    movie_id        INTEGER,
    subject_id      INTEGER NOT NULL,
    status_id       INTEGER NOT NULL
""",
"info_type": """
    id              INTEGER NOT NULL,
    info            VARCHAR(32) NOT NULL
""",
"keyword": """
    id              INTEGER NOT NULL,
    keyword         TEXT NOT NULL,
    phonetic_code   VARCHAR(5)
""",
"kind_type": """
    id              INTEGER NOT NULL,
    kind            VARCHAR(15) NOT NULL
""",
"link_type": """
    id              INTEGER NOT NULL,
    link            VARCHAR(32) NOT NULL
""",
"movie_companies": """
    id              INTEGER NOT NULL,
    movie_id        INTEGER NOT NULL,
    company_id      INTEGER NOT NULL,
    company_type_id INTEGER NOT NULL,
    note            TEXT
""",
"movie_info": """
    id              INTEGER NOT NULL,
    movie_id        INTEGER NOT NULL,
    info_type_id    INTEGER NOT NULL,
    info            TEXT NOT NULL,
    note            TEXT
""",
"movie_info_idx": """
    id              INTEGER NOT NULL,
    movie_id        INTEGER NOT NULL,
    info_type_id    INTEGER NOT NULL,
    info            VARCHAR(10) NOT NULL,
    note            TEXT
""",
"movie_keyword": """
    id              INTEGER NOT NULL,
    movie_id        INTEGER NOT NULL,
    keyword_id      INTEGER NOT NULL
""",
"movie_link": """
    id              INTEGER NOT NULL,
    movie_id        INTEGER NOT NULL,
    linked_movie_id INTEGER NOT NULL,
    link_type_id    INTEGER NOT NULL
""",
"name": """
    id              INTEGER NOT NULL,
    name            TEXT NOT NULL,
    imdb_index      VARCHAR(9),
    imdb_id         INTEGER,
    gender          VARCHAR(1),
    name_pcode_cf   VARCHAR(5),
    name_pcode_nf   VARCHAR(5),
    surname_pcode   VARCHAR(5),
    md5sum          VARCHAR(32)
""",
"person_info": """
    id              INTEGER NOT NULL,
    person_id       INTEGER NOT NULL,
    info_type_id    INTEGER NOT NULL,
    info            TEXT NOT NULL,
    note            TEXT
""",
"role_type": """
    id              INTEGER NOT NULL,
    role            VARCHAR(32) NOT NULL
""",
"title": """
    id              INTEGER NOT NULL,
    title           TEXT NOT NULL,
    imdb_index      VARCHAR(5),
    kind_id         INTEGER NOT NULL,
    production_year INTEGER,
    imdb_id         INTEGER,
    phonetic_code   VARCHAR(5),
    episode_of_id   INTEGER,
    season_nr       INTEGER,
    episode_nr      INTEGER,
    series_years    VARCHAR(49),
    md5sum          VARCHAR(32)
""",
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def download_with_progress(url, dest):
    print(f"  Downloading from:\n    {url}")
    # Bypass SSL verification (needed on some Windows systems for academic URLs)
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_ctx))
    urllib.request.install_opener(opener)
    def reporthook(block, block_size, total):
        downloaded = block * block_size
        if total > 0:
            pct = min(downloaded / total * 100, 100)
            mb  = downloaded / 1e6
            sys.stdout.write(f"\r    {pct:5.1f}%  {mb:7.1f} MB")
            sys.stdout.flush()
    urllib.request.urlretrieve(url, dest, reporthook)
    print()

def download_tgz():
    if os.path.exists(TGZ_PATH):
        print(f"  imdb.tgz already downloaded, skipping.")
        return True
    for url in DOWNLOAD_URLS:
        try:
            download_with_progress(url, TGZ_PATH)
            print(f"  Download complete -> {TGZ_PATH}")
            return True
        except Exception as e:
            print(f"  Failed ({e}), trying next URL...")
    print("  ERROR: Could not download imdb.tgz from any URL.")
    return False

def extract_tgz():
    if os.path.exists(CSV_DIR) and len(os.listdir(CSV_DIR)) > 10:
        print(f"  CSVs already extracted, skipping.")
        return
    os.makedirs(CSV_DIR, exist_ok=True)
    print(f"  Extracting to {CSV_DIR} ...")
    t0 = time.time()
    with tarfile.open(TGZ_PATH, "r:gz") as tar:
        tar.extractall(CSV_DIR)
    print(f"  Extracted in {time.time()-t0:.1f}s")

def find_csv(table):
    """Find the CSV file for a table anywhere under CSV_DIR."""
    for root, _, files in os.walk(CSV_DIR):
        for f in files:
            if f.lower() == f"{table}.csv":
                return os.path.join(root, f)
    return None

def import_tables(con):
    total_rows = 0
    for table, cols in DDL.items():
        csv_path = find_csv(table)
        if csv_path is None:
            print(f"  WARNING: {table}.csv not found, skipping.")
            continue

        print(f"  Importing {table:<25}", end="", flush=True)
        t0 = time.time()

        con.execute(f"DROP TABLE IF EXISTS {table}")
        con.execute(f"CREATE TABLE {table} ({cols})")
        safe_path = csv_path.replace('\\', '/')
        con.execute(f"""
            INSERT INTO {table}
            SELECT * FROM read_csv(
                '{safe_path}',
                delim=',',
                quote='"',
                escape='\\',
                header=false,
                ignore_errors=true,
                null_padding=true
            )
        """)

        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        total_rows += n
        print(f"  {n:>12,} rows  ({time.time()-t0:.1f}s)")

    print(f"\n  Total rows imported: {total_rows:,}")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.makedirs(DB_DIR, exist_ok=True)

    if os.path.exists(IMDB_DB):
        print(f"imdb.duckdb already exists at {IMDB_DB}")
        print("Delete it first if you want to rebuild.")
        sys.exit(0)

    print("=" * 60)
    print("Setting up IMDB database for JOB benchmark")
    print("=" * 60)

    # Step 1: Download
    print("\n[1/3] Downloading IMDB CSV archive (~1.8 GB) ...")
    if not download_tgz():
        sys.exit(1)

    # Step 2: Extract
    print("\n[2/3] Extracting CSV files ...")
    extract_tgz()

    # Step 3: Import into DuckDB
    print("\n[3/3] Importing into DuckDB ...")
    t0 = time.time()
    con = duckdb.connect(IMDB_DB)
    import_tables(con)
    con.close()
    elapsed = time.time() - t0

    print(f"\nDone in {elapsed:.1f}s")
    print(f"  IMDB DB -> {IMDB_DB}")
    print(f"\nOptional: delete CSV cache to free space:")
    print(f"  rm -rf \"{CSV_DIR}\"")
