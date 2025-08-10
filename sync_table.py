import os
import sys
import time
import json
import pymysql
from pymysql.cursors import DictCursor

# Configuration
BATCH_SIZE = 500
TOTAL_ROWS_ESTIMATE = 18516561  # Update this per table
PROGRESS_FILE = "{}_progress.json"

def retry_query(cursor, sql, params=(), retries=5, delay=3):
    for attempt in range(retries):
        try:
            cursor.execute(sql, params)
            return
        except pymysql.err.OperationalError as e:
            if attempt == retries - 1:
                raise
            print(f"⚠️ Retry {attempt+1}/{retries} after error: {e}")
            time.sleep(delay)
            delay *= 2

def get_primary_key_columns(connection, table):
    with connection.cursor() as cursor:
        retry_query(cursor, f"SHOW KEYS FROM {table} WHERE Key_name = 'PRIMARY'")
        return [row['Column_name'] for row in cursor.fetchall()]

def fetch_local_batch(cursor, table, pk_cols, last_key):
    where_clause = ''
    params = ()
    if last_key:
        where_clause = 'WHERE ' + ' AND '.join([f"{col} <= %s" for col in pk_cols])
        params = last_key
    order_clause = ', '.join(f"{col} DESC" for col in pk_cols)
    sql = f"SELECT * FROM {table} {where_clause} ORDER BY {order_clause} LIMIT {BATCH_SIZE}"
    retry_query(cursor, sql, params)
    return cursor.fetchall()

def fetch_existing_keys(cursor, table, pk_cols, keys):
    if not keys:
        return set()
    placeholders = ','.join(['(' + ','.join(['%s'] * len(pk_cols)) + ')'] * len(keys))
    flat = [item for key in keys for item in key]
    sql = f"SELECT {', '.join(pk_cols)} FROM {table} WHERE ({', '.join(pk_cols)}) IN ({placeholders})"
    retry_query(cursor, sql, flat)
    return set(tuple(row[col] for col in pk_cols) for row in cursor.fetchall())

def insert_rows(cursor, table, rows):
    if not rows:
        return
    keys = rows[0].keys()
    cols = ", ".join(keys)
    vals = "(" + ", ".join(["%s"] * len(keys)) + ")"
    sql = f"REPLACE INTO {table} ({cols}) VALUES {vals}"
    cursor.executemany(sql, [tuple(row[k] for k in keys) for row in rows])

def load_progress(table):
    file = PROGRESS_FILE.format(table)
    if os.path.exists(file):
        with open(file) as f:
            return json.load(f)
    return {'last_key': None, 'inserted': 0, 'scanned': 0}

def save_progress(table, last_key, inserted, scanned):
    file = PROGRESS_FILE.format(table)
    with open(file, 'w') as f:
        json.dump({
            'last_key': last_key,
            'inserted': inserted,
            'scanned': scanned
        }, f)

def sync_table(table):
    # 🔧 Adjust your credentials as needed
    local = pymysql.connect(host='localhost', user='new_user', password='user_password', db='rm_addressindexer_data2', charset='utf8mb4', cursorclass=DictCursor, autocommit=True)
    remote = pymysql.connect(host='127.0.0.1', port=3308, user='syncuser', password='Sync@1234', db='rm_addressindexer_data2', charset='utf8mb4', cursorclass=DictCursor, autocommit=True)

    pk_cols = get_primary_key_columns(local, table)
    print(f"🔁 Resumable sync: {table} (PK: {pk_cols})")

    progress = load_progress(table)
    last_key = tuple(progress['last_key']) if progress['last_key'] else None
    total_inserted = progress['inserted']
    total_scanned = progress['scanned']

    batch = 0
    start_time = time.time()

    while True:
        with local.cursor() as lc, remote.cursor() as rc:
            local_batch = fetch_local_batch(lc, table, pk_cols, last_key)
            if not local_batch:
                print(f"✅ Sync complete. Total inserted: {total_inserted}")
                break

            keys = [tuple(row[col] for col in pk_cols) for row in local_batch]
            existing_keys = fetch_existing_keys(rc, table, pk_cols, keys)
            to_insert = [row for row in local_batch if tuple(row[col] for col in pk_cols) not in existing_keys]

            insert_rows(rc, table, to_insert)
            total_inserted += len(to_insert)
            total_scanned += len(local_batch)
            last_key = [local_batch[-1][col] for col in pk_cols]

            save_progress(table, last_key, total_inserted, total_scanned)

            elapsed = time.time() - start_time
            rate = total_scanned / elapsed if elapsed else 1
            eta = (TOTAL_ROWS_ESTIMATE - total_scanned) / rate
            eta_str = time.strftime("%H:%M:%S", time.gmtime(eta))

            pct = (total_scanned / TOTAL_ROWS_ESTIMATE) * 100
            print(f"📦 Batch {batch} | Inserted: {len(to_insert)} | Last key: {tuple(last_key)}")
            print(f"📊 Progress: {total_scanned}/{TOTAL_ROWS_ESTIMATE} scanned ({pct:.2f}%), {total_inserted} inserted ({(total_inserted / TOTAL_ROWS_ESTIMATE) * 100:.4f}%) | ETA: {eta_str}")
            batch += 1

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sync_table.py <table>")
    else:
        sync_table(sys.argv[1])
