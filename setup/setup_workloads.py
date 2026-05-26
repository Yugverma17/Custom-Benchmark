import os, shutil

BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB_SRC  = '/home/nvidia/Yug/Benchmark/imdb_job_trace/queries'
JOB_DST  = os.path.join(BASE, 'workloads', 'job')

os.makedirs(JOB_DST, exist_ok=True)

if not os.path.isdir(JOB_SRC):
    print(f"ERROR: JOB source not found: {JOB_SRC}")
    raise SystemExit(1)

sql_files = sorted(f for f in os.listdir(JOB_SRC) if f.endswith('.sql'))
if not sql_files:
    print(f"ERROR: no .sql files in {JOB_SRC}")
    raise SystemExit(1)

copied = 0
skipped = 0
for fname in sql_files:
    src = os.path.join(JOB_SRC, fname)
    dst = os.path.join(JOB_DST, fname)
    if not os.path.exists(dst):
        shutil.copy2(src, dst)
        copied += 1
    else:
        skipped += 1

print(f"JOB workload: {copied} copied, {skipped} already present -> {JOB_DST}/")
print(f"Total .sql files: {len(os.listdir(JOB_DST))}")
