import re, hashlib, random, importlib.util, os, threading, time, signal
import numpy as np
from collections import defaultdict


def load_schema(db_name: str, schema_dir: str):
    path = os.path.join(schema_dir, f"{db_name}_schema.py")
    spec = importlib.util.spec_from_file_location("schema", path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def normalise_sql(sql: str) -> str:
    sql = re.sub(r'--[^\n]*', '', sql)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    return re.sub(r'\s+', ' ', sql).strip().lower()

def query_hash(sql: str) -> str:
    return hashlib.md5(normalise_sql(sql).encode()).hexdigest()[:8]

def count_joins(sql: str) -> int:
    explicit = len(re.findall(r'\bJOIN\b', sql, re.IGNORECASE))
    from_m = re.search(
        r'\bFROM\b(.*?)(?:\bWHERE\b|\bGROUP\b|\bORDER\b|\bHAVING\b|\bLIMIT\b|\bJOIN\b|$)',
        sql, re.IGNORECASE | re.DOTALL)
    implicit = 0
    if from_m:
        items = [i.strip() for i in from_m.group(1).split(',') if i.strip()]
        implicit = max(0, len(items) - 1)
    return explicit + implicit

def extract_tables(sql: str) -> list[str]:
    clean = re.sub(r'--[^\n]*', '', sql)
    clean = re.sub(r'/\*.*?\*/', '', clean, flags=re.DOTALL)
    seen, result = set(), []

    for t in re.findall(r'\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)', clean, re.IGNORECASE):
        tl = t.lower()
        if tl not in seen:
            seen.add(tl); result.append(tl)

    from_m = re.search(
        r'\bFROM\b(.*?)(?:\bWHERE\b|\bGROUP\b|\bORDER\b|\bHAVING\b|\bLIMIT\b|\bJOIN\b|$)',
        clean, re.IGNORECASE | re.DOTALL)
    if from_m:
        for item in from_m.group(1).split(','):
            item = item.strip()
            m = re.match(r'([a-zA-Z_][a-zA-Z0-9_]*)', item)
            if m:
                tl = m.group(1).lower()
                if tl not in seen:
                    seen.add(tl); result.append(tl)
    return result

def estimate_bytes_read(tables: list[str], schema, selectivity: float = 0.10) -> int:
    total = sum(
        schema.TABLE_SIZES.get(t, 1000) * schema.AVG_ROW_SIZE.get(t, 80)
        for t in tables
    )
    return int(total * selectivity)


def build_adjacency(fk_edges):
    adj = defaultdict(list)
    for child, child_col, parent, parent_col in fk_edges:
        adj[child].append((parent, child_col, parent_col))
        adj[parent].append((child, parent_col, child_col))
    return adj

def bfs_join_tree(tables: list, adj: dict):
    tables_set = set(tables)
    start = tables[0]
    visited   = {start: None}
    queue     = [start]
    bfs_order = [start]

    while queue and len(visited) < len(tables_set):
        curr = queue.pop(0)
        for neighbor, curr_col, nbr_col in adj.get(curr, []):
            if neighbor in tables_set and neighbor not in visited:
                visited[neighbor] = (curr, curr_col, nbr_col)
                queue.append(neighbor)
                bfs_order.append(neighbor)

    return visited, start, bfs_order

def select_tables_bfs(n_tables: int, schema, rng: random.Random) -> list[str]:
    adj = build_adjacency(schema.FK_EDGES)
    all_tables = sorted(schema.TABLE_SIZES, key=lambda t: -schema.TABLE_SIZES[t])
    start = all_tables[0]

    visited_set = {start}
    queue = [start]
    order = [start]
    while queue and len(order) < max(n_tables, 1):
        curr = queue.pop(0)
        neighbors = [nb for nb, _, _ in adj.get(curr, [])
                     if nb in schema.TABLE_SIZES and nb not in visited_set]
        rng.shuffle(neighbors)
        for nb in neighbors:
            if len(order) >= n_tables:
                break
            visited_set.add(nb)
            queue.append(nb)
            order.append(nb)

    selected = order[:n_tables]
    return sorted(selected, key=lambda t: -schema.TABLE_SIZES.get(t, 0))


def build_predicate(alias: str, col_def: tuple, sel: float, rng: random.Random) -> str | None:
    col   = col_def[0]
    dtype = col_def[1]

    if dtype == 'int':
        lo, hi = col_def[2], col_def[3]
        width  = max(1, int((hi - lo) * sel * 2.5))
        start  = rng.randint(lo, max(lo, hi - width))
        end    = min(hi, start + width)
        return f"{alias}.{col} >= {start} AND {alias}.{col} <= {end}"

    elif dtype == 'float':
        lo, hi = col_def[2], col_def[3]
        span   = (hi - lo) * sel * 2.5
        start  = rng.uniform(lo, max(lo, hi - span))
        end    = min(hi, start + span)
        return f"{alias}.{col} >= {start:.2f} AND {alias}.{col} <= {end:.2f}"

    elif dtype == 'enum':
        vals = col_def[2]
        k = max(1, int(len(vals) * sel * 3))
        chosen = rng.sample(vals, min(k, len(vals)))
        if len(chosen) == 1:
            return f"{alias}.{col} = '{chosen[0]}'"
        in_list = ', '.join(f"'{v}'" for v in chosen)
        return f"{alias}.{col} IN ({in_list})"

    elif dtype == 'like':
        letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        prefix  = rng.choice(letters)
        return f"{alias}.{col} LIKE '{prefix}%'"

    return None

def build_select_sql(tables: list[str], target_bytes: float, schema, rng: random.Random) -> str:
    if not tables:
        return "SELECT 1"

    if len(tables) == 1:
        t = tables[0]
        alias = f"{t[0]}0"
        agg_col = schema.AGG_COL.get(t, schema.DEFAULT_AGG_COL)
        sql = f"SELECT MIN({alias}.{agg_col}) AS result\nFROM {t} AS {alias}"
        if t in schema.PRED_COLS:
            total_bytes = schema.TABLE_SIZES.get(t, 1) * schema.AVG_ROW_SIZE.get(t, 80)
            sel = max(0.001, min(0.99, target_bytes / total_bytes)) if total_bytes > 0 else 0.1
            pred = build_predicate(alias, schema.PRED_COLS[t][0], sel, rng)
            if pred:
                sql += f"\nWHERE {pred}"
        return sql

    adj = build_adjacency(schema.FK_EDGES)
    aliases  = {t: f"{t[0]}{i}" for i, t in enumerate(tables)}
    visited, root, bfs_order = bfs_join_tree(tables, adj)

    agg_col = schema.AGG_COL.get(root, schema.DEFAULT_AGG_COL)
    select_clause = f"SELECT MIN({aliases[root]}.{agg_col}) AS result"

    join_parts = []
    for t in bfs_order[1:]:
        if t not in visited or visited[t] is None:
            continue
        parent, parent_col, my_col = visited[t]
        if parent in aliases:
            join_parts.append(
                f"JOIN {t} AS {aliases[t]}"
                f" ON {aliases[t]}.{my_col} = {aliases[parent]}.{parent_col}"
            )

    total_bytes = sum(
        schema.TABLE_SIZES.get(t, 1) * schema.AVG_ROW_SIZE.get(t, 80)
        for t in tables
    )
    sel = max(0.001, min(0.99, target_bytes / total_bytes)) if total_bytes > 0 else 0.1

    where_parts = []
    for t in bfs_order:
        if t not in schema.PRED_COLS:
            continue
        pred = build_predicate(aliases[t], schema.PRED_COLS[t][0], sel, rng)
        if pred:
            where_parts.append(pred)
        if len(where_parts) >= 3:
            break

    sql = select_clause + "\n"
    sql += f"FROM {root} AS {aliases[root]}"
    for jp in join_parts:
        sql += "\n" + jp
    if where_parts:
        sql += "\nWHERE " + "\nAND ".join(where_parts)
    return sql


class _QueryTimeout(Exception):
    pass

def execute_with_timeout(con, sql: str, timeout_s: int) -> dict:
    t0 = time.perf_counter()

    if hasattr(signal, 'SIGALRM'):
        def _alarm_handler(signum, frame):
            try:
                con.interrupt()
            except Exception:
                pass
            raise _QueryTimeout()

        old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(timeout_s)
        try:
            rows = con.execute(sql).fetchall()
            rt   = (time.perf_counter() - t0) * 1000
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            return {'runtime_ms': round(rt, 2), 'rows_returned': len(rows),
                    'status': 'ok', 'error_msg': ''}
        except _QueryTimeout:
            rt = (time.perf_counter() - t0) * 1000
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            return {'runtime_ms': round(rt, 2), 'rows_returned': None,
                    'status': 'timeout', 'error_msg': 'Query exceeded timeout'}
        except Exception as e:
            rt = (time.perf_counter() - t0) * 1000
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            return {'runtime_ms': round(rt, 2), 'rows_returned': None,
                    'status': 'error', 'error_msg': str(e)[:200]}
    else:
        interrupted = [False]

        def do_interrupt():
            interrupted[0] = True
            try:
                con.interrupt()
            except Exception:
                pass

        timer = threading.Timer(timeout_s, do_interrupt)
        timer.start()
        try:
            rows = con.execute(sql).fetchall()
            rt   = (time.perf_counter() - t0) * 1000
            timer.cancel()
            return {'runtime_ms': round(rt, 2), 'rows_returned': len(rows),
                    'status': 'ok', 'error_msg': ''}
        except Exception as e:
            rt = (time.perf_counter() - t0) * 1000
            timer.cancel()
            status = 'timeout' if interrupted[0] else 'error'
            return {'runtime_ms': round(rt, 2), 'rows_returned': None,
                    'status': status, 'error_msg': str(e)[:200]}
