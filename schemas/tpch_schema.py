FK_EDGES = [
    ('lineitem',  'l_orderkey',   'orders',   'o_orderkey'),
    ('lineitem',  'l_partkey',    'part',     'p_partkey'),
    ('lineitem',  'l_suppkey',    'supplier', 's_suppkey'),
    ('orders',    'o_custkey',    'customer', 'c_custkey'),
    ('partsupp',  'ps_partkey',   'part',     'p_partkey'),
    ('partsupp',  'ps_suppkey',   'supplier', 's_suppkey'),
    ('customer',  'c_nationkey',  'nation',   'n_nationkey'),
    ('supplier',  's_nationkey',  'nation',   'n_nationkey'),
    ('nation',    'n_regionkey',  'region',   'r_regionkey'),
]

TABLE_SIZES = {
    'lineitem' : 6001215,
    'orders'   : 1500000,
    'partsupp' :  800000,
    'customer' :  150000,
    'part'     :  200000,
    'supplier' :   10000,
    'nation'   :      25,
    'region'   :       5,
}

AVG_ROW_SIZE = {
    'lineitem' : 180,
    'orders'   : 110,
    'partsupp' : 120,
    'customer' : 180,
    'part'     : 130,
    'supplier' : 150,
    'nation'   :  50,
    'region'   :  40,
}

PRED_COLS = {
    'lineitem' : [('l_quantity',    'int',   1,  50),
                  ('l_returnflag',  'enum',  ['N', 'R', 'A']),
                  ('l_linestatus',  'enum',  ['O', 'F'])],
    'orders'   : [('o_orderstatus', 'enum',  ['F', 'O', 'P']),
                  ('o_shippriority','int',   0,   1)],
    'customer' : [('c_mktsegment',  'enum',  ['BUILDING', 'AUTOMOBILE', 'MACHINERY',
                                              'HOUSEHOLD', 'FURNITURE']),
                  ('c_acctbal',     'float', -999.0, 9999.99)],
    'part'     : [('p_size',        'int',   1,  50),
                  ('p_type',        'like',  None)],
    'supplier' : [('s_nationkey',   'int',   0,  24),
                  ('s_acctbal',     'float', -999.0, 9999.99)],
    'partsupp' : [('ps_availqty',   'int',   1,  9999)],
    'nation'   : [('n_regionkey',   'int',   0,  4)],
    'region'   : [('r_regionkey',   'int',   0,  4)],
}

AGG_COL = {
    'part'    : 'p_name',
    'customer': 'c_name',
    'supplier': 's_name',
    'nation'  : 'n_name',
    'region'  : 'r_name',
    'orders'  : 'o_orderstatus',
}
DEFAULT_AGG_COL = 'l_quantity'
