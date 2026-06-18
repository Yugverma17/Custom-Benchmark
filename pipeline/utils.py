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


def augment_schema(schema, factor: int = 2):
    class AugmentedSchema:
        pass
    aug = AugmentedSchema()
    suffixes = [f"_{i}" for i in range(factor)]
    aug.TABLE_SIZES  = {f"{t}{s}": v for t, v in schema.TABLE_SIZES.items()  for s in suffixes}
    aug.AVG_ROW_SIZE = {f"{t}{s}": v for t, v in schema.AVG_ROW_SIZE.items() for s in suffixes}
    aug.PRED_COLS    = {f"{t}{s}": v for t, v in schema.PRED_COLS.items()    for s in suffixes}
    aug.FK_EDGES     = [
        (f"{tl}{s}", cl, f"{tr}{s}", cr)
        for tl, cl, tr, cr in schema.FK_EDGES
        for s in suffixes
    ]
    aug._base        = schema
    aug._factor      = factor
    aug._suffixes    = suffixes
    return aug


def base_table(augmented_name: str) -> str:
    for sep in ('_0', '_1', '_2', '_3'):
        if augmented_name.endswith(sep):
            return augmented_name[:-len(sep)]
    return augmented_name


def build_size_rank_mapping(source_schema, target_schema) -> dict:
    src_ranked = sorted(source_schema.TABLE_SIZES, key=lambda t: -source_schema.TABLE_SIZES[t])
    tgt_ranked = sorted(target_schema.TABLE_SIZES, key=lambda t: -target_schema.TABLE_SIZES[t])
    return {src: tgt_ranked[min(i, len(tgt_ranked) - 1)] for i, src in enumerate(src_ranked)}


def compute_relative_selectivity(tables: list, bytes_read: int, source_schema) -> float:
    total = sum(
        source_schema.TABLE_SIZES.get(t, 1000) * source_schema.AVG_ROW_SIZE.get(t, 80)
        for t in tables
    )
    if total <= 0:
        return 0.1
    return min(0.99, max(0.001, bytes_read / total))


_N_QUANTILES   = 101
_QUANTILE_PTS  = [i / 100.0 for i in range(_N_QUANTILES)]
_MIN_SEL       = 0.05


def compute_column_stats(con, schema) -> dict:
    stats: dict = {}
    computed: dict = {}

    for table, col_defs in schema.PRED_COLS.items():
        real_table = base_table(table)
        if real_table in computed:
            stats[table] = computed[real_table]
            continue
        stats[table] = {}
        computed[real_table] = stats[table]
        for col_def in col_defs:
            col   = col_def[0]
            dtype = col_def[1]
            try:
                if dtype in ('int', 'float'):
                    try:
                        row = con.execute(
                            f"SELECT quantile_cont(CAST({col} AS DOUBLE), "
                            f"{_QUANTILE_PTS}) "
                            f"FROM {real_table} WHERE {col} IS NOT NULL"
                        ).fetchone()
                        vals = row[0] if row else None
                        if vals and len(vals) == _N_QUANTILES:
                            stats[table][col] = {
                                'type': dtype,
                                'quantiles': list(vals)
                            }
                            continue
                    except Exception:
                        pass
                    row = con.execute(
                        f"SELECT MIN(CAST({col} AS DOUBLE)), MAX(CAST({col} AS DOUBLE)) "
                        f"FROM {real_table} WHERE {col} IS NOT NULL"
                    ).fetchone()
                    lo, hi = (row[0] or 0), (row[1] or 0)
                    q_vals = [lo + (hi - lo) * i / 100 for i in range(_N_QUANTILES)]
                    stats[table][col] = {
                        'type': dtype,
                        'quantiles': q_vals
                    }

                elif dtype == 'varchar':
                    try:
                        row = con.execute(
                            f"SELECT quantile_disc(CAST({col} AS VARCHAR), "
                            f"{_QUANTILE_PTS}) "
                            f"FROM {real_table} WHERE {col} IS NOT NULL"
                        ).fetchone()
                        vals = row[0] if row else None
                        if vals and len(vals) == _N_QUANTILES:
                            stats[table][col] = {
                                'type': 'varchar',
                                'quantiles': list(vals)
                            }
                            continue
                    except Exception:
                        pass
                    rows = con.execute(
                        f"SELECT DISTINCT CAST({col} AS VARCHAR) "
                        f"FROM {real_table} WHERE {col} IS NOT NULL LIMIT 200"
                    ).fetchall()
                    vals_list = sorted(r[0] for r in rows if r[0] is not None)
                    n = len(vals_list)
                    q_vals = [vals_list[min(int(i/100*(n-1)), n-1)] for i in range(_N_QUANTILES)]
                    stats[table][col] = {
                        'type': 'varchar',
                        'quantiles': q_vals
                    }

                elif dtype == 'enum':
                    rows = con.execute(
                        f"SELECT DISTINCT CAST({col} AS VARCHAR) "
                        f"FROM {real_table} WHERE {col} IS NOT NULL LIMIT 100"
                    ).fetchall()
                    stats[table][col] = {
                        'type': 'enum',
                        'values': [r[0] for r in rows if r[0] is not None]
                    }

                elif dtype == 'like':
                    rows = con.execute(
                        f"SELECT DISTINCT SUBSTR(CAST({col} AS VARCHAR), 1, 1) "
                        f"FROM {real_table} WHERE {col} IS NOT NULL LIMIT 26"
                    ).fetchall()
                    stats[table][col] = {
                        'type': 'like',
                        'prefixes': [r[0] for r in rows if r[0]]
                    }

            except Exception as exc:
                stats[table][col] = {'type': dtype, 'error': str(exc)[:120]}

    return stats


def random_walk_tables(seed_tables: list, n_tables: int, schema, rng: random.Random) -> list:
    adj      = build_adjacency(schema.FK_EDGES)
    all_tbls = set(schema.TABLE_SIZES.keys())

    valid_seeds = [t for t in seed_tables if t in all_tbls]
    if not valid_seeds:
        return select_tables_bfs(n_tables, schema, rng)

    valid_seeds = sorted(valid_seeds, key=lambda t: -schema.TABLE_SIZES.get(t, 0))
    start   = valid_seeds[0]
    visited = {start}
    path    = [start]
    remaining_seeds = set(valid_seeds[1:])

    for _ in range(n_tables * 8):
        if len(path) >= n_tables:
            break
        current = path[-1] if rng.random() > 0.25 else rng.choice(path)
        neighbors = [nb for nb, _, _ in adj.get(current, [])
                     if nb in all_tbls and nb not in visited]
        if not neighbors:
            for t in rng.sample(path, len(path)):
                neighbors = [nb for nb, _, _ in adj.get(t, [])
                             if nb in all_tbls and nb not in visited]
                if neighbors:
                    break
        if not neighbors:
            break
        seed_nbrs = [nb for nb in neighbors if nb in remaining_seeds]
        if seed_nbrs and rng.random() < 0.7:
            next_t = rng.choice(seed_nbrs)
        else:
            next_t = rng.choice(neighbors)
        visited.add(next_t)
        path.append(next_t)
        remaining_seeds.discard(next_t)

    if len(path) < n_tables:
        for t in select_tables_bfs(n_tables * 2, schema, rng):
            if t not in visited:
                path.append(t)
                visited.add(t)
            if len(path) >= n_tables:
                break

    return path[:n_tables]


def build_predicate_redbench(alias: str, col_def: tuple, col_stat: dict,
                              sigma: float, rng: random.Random) -> 'str | None':
    if not col_stat or 'error' in col_stat:
        return build_predicate(alias, col_def, sigma, rng)

    col   = col_def[0]
    dtype = col_stat.get('type', col_def[1])

    if dtype in ('int', 'float', 'varchar') and 'quantiles' in col_stat:
        quantiles    = col_stat['quantiles']
        sigma_scaled = _MIN_SEL + (1 - _MIN_SEL) * min(1.0, sigma)
        range_size   = max(0, min(100, int(sigma_scaled * 100)))
        max_start    = max(0, 100 - range_size)
        start_idx    = rng.randint(0, max_start)
        lo           = quantiles[start_idx]
        hi           = quantiles[start_idx + range_size]
        if lo is None or hi is None:
            return None
        if dtype == 'int':
            return f"{alias}.{col} BETWEEN {int(lo)} AND {int(hi)}"
        if dtype == 'float':
            return f"{alias}.{col} BETWEEN {lo:.4f} AND {hi:.4f}"
        lo_s = str(lo).replace("'", "''")
        hi_s = str(hi).replace("'", "''")
        return f"{alias}.{col} BETWEEN '{lo_s}' AND '{hi_s}'"

    elif dtype == 'enum' and 'values' in col_stat:
        vals = col_stat['values']
        if not vals:
            return None
        k      = max(1, int(len(vals) * sigma * 3))
        chosen = rng.sample(vals, min(k, len(vals)))
        if len(chosen) == 1:
            return f"{alias}.{col} = '{chosen[0]}'"
        in_list = ', '.join(f"'{v}'" for v in chosen)
        return f"{alias}.{col} IN ({in_list})"

    elif dtype == 'like' and 'prefixes' in col_stat:
        prefixes = col_stat['prefixes'] or list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
        return f"{alias}.{col} LIKE '{rng.choice(prefixes)}%'"

    return build_predicate(alias, col_def, sigma, rng)


def build_select_sql_redbench(tables: list, selectivity: float,
                               schema, col_stats: dict, rng: random.Random) -> str:
    if not tables:
        return "SELECT 1"

    adj = build_adjacency(schema.FK_EDGES)

    if len(tables) == 1:
        t     = tables[0]
        alias = f"{t[0]}0"
        agg   = getattr(schema, 'AGG_COL', {}).get(t, getattr(schema, 'DEFAULT_AGG_COL', 'id'))
        sql   = f"SELECT MIN({alias}.{agg}) AS result\nFROM {t} AS {alias}"
        if t in schema.PRED_COLS:
            col_def  = schema.PRED_COLS[t][0]
            col_stat = col_stats.get(t, {}).get(col_def[0], {})
            pred     = build_predicate_redbench(alias, col_def, col_stat, selectivity, rng)
            if pred:
                sql += f"\nWHERE {pred}"
        return sql

    aliases              = {t: f"{t[0]}{i}" for i, t in enumerate(tables)}
    visited, root, order = bfs_join_tree(tables, adj)
    agg                  = getattr(schema, 'AGG_COL', {}).get(root,
                           getattr(schema, 'DEFAULT_AGG_COL', 'id'))

    join_parts = []
    for t in order[1:]:
        if visited.get(t) is None:
            continue
        parent, parent_col, my_col = visited[t]
        if parent in aliases:
            join_parts.append(
                f"JOIN {t} AS {aliases[t]}"
                f" ON {aliases[t]}.{my_col} = {aliases[parent]}.{parent_col}"
            )

    where_parts = []
    for t in order:
        if t not in schema.PRED_COLS:
            continue
        col_def  = schema.PRED_COLS[t][0]
        col_stat = col_stats.get(t, {}).get(col_def[0], {})
        pred     = build_predicate_redbench(aliases[t], col_def, col_stat, selectivity, rng)
        if pred:
            where_parts.append(pred)

    sql  = f"SELECT MIN({aliases[root]}.{agg}) AS result\n"
    sql += f"FROM {root} AS {aliases[root]}"
    for jp in join_parts:
        sql += "\n" + jp
    if where_parts:
        sql += "\nWHERE " + "\nAND ".join(where_parts)
    return sql


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
