#!/usr/bin/env python3

import sys
import time
import json
import requests
import pymysql
import parsing

# ------------------------
# CONFIG
# ------------------------

NETWORK = "testnet"
NETWORK = NETWORK.lower()
if NETWORK == "mainnet":
    ADDRESS_PREFIXES = ["F", "e"]
else:
    ADDRESS_PREFIXES = ["o", "e"]



BLOCKBOOK_MAINNET_URL_LIST = "http://127.0.0.1/"
BLOCKBOOK_TESTNET_URL_LIST = "http://127.0.0.1/"

MYSQL_USER = "new_user"
MYSQL_PASSWORD = "user_password"
MYSQL_HOST = "localhost"

# Default values
DEFAULT_DB = "rm_latestCache_db"
BACKUP_DB = "rm_latestCache_db_backup"
BLOCKBOOK_URL = BLOCKBOOK_MAINNET_URL_LIST

# Adjust for testnet
if NETWORK.lower() == "testnet":
    DEFAULT_DB = "rmtest_latestCache_db"
    BACKUP_DB = "rmtest_latestCache_db_backup"
    BLOCKBOOK_URL = BLOCKBOOK_TESTNET_URL_LIST

print(f"Using DB: {DEFAULT_DB}")
print(f"Using Backup DB: {BACKUP_DB}")
print(f"Blockbook URL: {BLOCKBOOK_URL}")

BLOCKBOOK_TYPE = "address_indexer"

RETRY_INTERVAL = 5

# ------------------------
# Derived Config
# ------------------------

if NETWORK == "mainnet":
    API_URL = BLOCKBOOK_MAINNET_URL_LIST.rstrip("/") + "/api"
else:
    API_URL = BLOCKBOOK_TESTNET_URL_LIST.rstrip("/") + "/api"

# ------------------------
# DB Functions
# ------------------------

def block_exists(blocknumber, db_name):
    connection = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=db_name,
        charset="utf8mb4"
    )
    try:
        with connection.cursor() as cursor:
            sql = "SELECT COUNT(*) FROM latestBlocks WHERE blockNumber = %s"
            cursor.execute(sql, (blocknumber,))
            count = cursor.fetchone()[0]
            return count > 0
    finally:
        connection.close()


def insert_block_into_latestBlocks(blockinfo, db_name, parsed_txs):
    connection = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=db_name,
        charset="utf8mb4"
    )
    try:
        with connection.cursor() as cursor:
            sql = """
                REPLACE INTO latestBlocks (blockNumber, blockHash, jsonData)
                VALUES (%s, %s, %s)
            """

            # Attempt to store the entire block first
            json_data_full = json.dumps(blockinfo)

            try:
                cursor.execute(sql, (
                    blockinfo["height"],
                    blockinfo["hash"],
                    json_data_full
                ))
                connection.commit()
                print(f"✅ Inserted full block {blockinfo['height']} into {db_name}.")
                return
            except pymysql.err.DataError as e:
                if "Data too long for column" not in str(e):
                    raise
                size_kb = len(json_data_full.encode('utf-8')) / 1024
                print(f"⚠️ Full block {blockinfo['height']} too large ({size_kb:.1f} KB). Trying only parsed transactions...")
                connection.rollback()

            # Try inserting minimal block with parsed transactions only
            if parsed_txs:
                minimal_block = {
                    key: blockinfo[key]
                    for key in blockinfo
                    if key not in ["txs", "parsedResult"]
                }
                minimal_block["parsedResult"] = parsed_txs

                json_data_minimal = json.dumps(minimal_block)

                try:
                    cursor.execute(sql, (
                        blockinfo["height"],
                        blockinfo["hash"],
                        json_data_minimal
                    ))
                    connection.commit()
                    print(f"✅ Inserted minimal block {blockinfo['height']} with {len(parsed_txs)} parsed txs into {db_name}.")
                except pymysql.err.DataError as e:
                    size_kb = len(json_data_minimal.encode('utf-8')) / 1024
                    print(f"⚠️ Even minimal block {blockinfo['height']} too large ({size_kb:.1f} KB). Skipping block.")
                    connection.rollback()
            else:
                print(f"⚠️ No parsed transactions for block {blockinfo['height']}. Skipping block entirely.")
                connection.rollback()
    finally:
        connection.close()


def get_databases():
    """
    Return all network-specific *_db databases except known system/cache databases.
    For mainnet: rm_*_db
    For testnet: rmtest_*_db
    """
    connection = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        charset="utf8mb4"
    )
    dbs = []
    
    # Determine prefix
    if NETWORK.lower() == "testnet":
        prefix = "rmtest_"
    else:
        prefix = "rm_"
    
    # Databases we do NOT want to scan
    skip_dbs = {
        "rm_latestCache_db",
        "rm_latestCache_db_backup",
        "rm_system_db",
        "rm_system_db_backup",
        "rmtest_latestCache_db",
        "rmtest_latestCache_db_backup",
        "rmtest_system_db",
        "rmtest_system_db_backup",
    }
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW DATABASES;")
            all_dbs = [row[0] for row in cursor.fetchall()]
            for db in all_dbs:
                if (
                    db.startswith(prefix)
                    and db.endswith("_db")
                    and db not in skip_dbs
                ):
                    dbs.append(db)
    finally:
        connection.close()
    
    print(f"→ get_databases(): Found {len(dbs)} DBs matching prefix '{prefix}': {dbs}")
    return dbs



def get_unique_addresses_from_transactionHistory(db_name):
    """
    Returns set of unique addresses from both transactionHistory
    and contractTransactionHistory tables in db_name.
    Skips nulls, empty strings, and obviously invalid addresses.
    """
    addresses = set()
    connection = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=db_name,
        charset="utf8mb4"
    )
    try:
        with connection.cursor() as cursor:
            for table in ["transactionHistory", "contractTransactionHistory"]:
                cursor.execute(f"SHOW TABLES LIKE '{table}'")
                if cursor.fetchone() is None:
                    continue

                cursor.execute(f"""
                    SELECT DISTINCT sourceFloAddress
                    FROM {table}
                    WHERE sourceFloAddress IS NOT NULL AND sourceFloAddress != ''
                """)
                rows = cursor.fetchall()
                for r in rows:
                    addr = r[0]
                    if addr and any(addr.startswith(prefix) for prefix in ADDRESS_PREFIXES):
                        addresses.add(addr)
                    elif addr not in (None, ""):
                        print(f"⚠️ Skipping invalid sourceFloAddress in {db_name}.{table}: {addr}")

                cursor.execute(f"""
                    SELECT DISTINCT destFloAddress
                    FROM {table}
                    WHERE destFloAddress IS NOT NULL AND destFloAddress != ''
                """)
                rows = cursor.fetchall()
                for r in rows:
                    addr = r[0]
                    if addr and any(addr.startswith(prefix) for prefix in ADDRESS_PREFIXES):
                        addresses.add(addr)
                    elif addr not in (None, ""):
                        print(f"⚠️ Skipping invalid destFloAddress in {db_name}.{table}: {addr}")
    except Exception as e:
        print(f"⚠️ Error reading from {db_name}: {e}")
    finally:
        connection.close()
    return addresses



# ------------------------
# Blockbook Functions
# ------------------------

def get_blockhash_by_height(blockindex):
    if BLOCKBOOK_TYPE == "blockbook_legacy":
        url = f"{API_URL}/v2/block-index/{blockindex}"
        expected_key = "blockHash"
    elif BLOCKBOOK_TYPE == "address_indexer":
        url = f"{API_URL}/blockheight/{blockindex}"
        expected_key = "hash"
    else:
        raise Exception("Unsupported blockbook_type")

    while True:
        try:
            r = requests.get(url, timeout=10, verify=False)
            r.raise_for_status()
            data = r.json()
            if expected_key in data:
                return data[expected_key]
            else:
                print(f"⚠️ Missing {expected_key} in response. Retrying...")
        except Exception as e:
            print(f"⚠️ Error fetching blockhash for {blockindex}: {e}")
        time.sleep(RETRY_INTERVAL)


def get_block_by_hash(blockhash):
    url = f"{API_URL}/block/{blockhash}"
    while True:
        try:
            r = requests.get(url, timeout=10, verify=False)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"⚠️ Error fetching block {blockhash}: {e}")
            time.sleep(RETRY_INTERVAL)


def get_blocks_for_address(address):
    url = f"{API_URL}/address/{address}?page=1&pageSize=10000"
    all_blocks = set()

    while True:
        try:
            r = requests.get(url, timeout=10, verify=False)
            if r.status_code == 400:
                print(f"⚠️ Blockbook rejected address {address}. Skipping it.")
                return []
            r.raise_for_status()
            data = r.json()

            txs = data.get("txs", [])
            for tx in txs:
                height = tx.get("blockheight")
                if height is not None:
                    all_blocks.add(height)

            return sorted(all_blocks)
        except Exception as e:
            print(f"⚠️ Error fetching address data for {address}: {e}")
            time.sleep(RETRY_INTERVAL)

# ------------------------
# Main Logic
# ------------------------

def scan_and_fix_blocks(block_numbers, db_name):
    for height in block_numbers:
        if block_exists(height, db_name):
            print(f"✅ Block {height} already exists in {db_name}.")
            continue

        print(f"🚀 Scanning missing block {height}...")

        blockhash = get_blockhash_by_height(height)
        blockinfo = get_block_by_hash(blockhash)

        parsed_txs = []
        for tx in blockinfo.get("txs", []):
            floData = tx.get("floData", "")
            floData_clean = floData.replace("\n", " \n ")
            parsed = parsing.parse_floData(floData_clean, blockinfo, NETWORK)

            if parsed and parsed.get("type") not in [None, "", "noise"]:
                parsed_txs.append({
                    "txid": tx["txid"],
                    "parsedData": parsed
                })

        if parsed_txs:
            blockinfo_to_store = dict(blockinfo)
            blockinfo_to_store["parsedResult"] = parsed_txs
            insert_block_into_latestBlocks(blockinfo_to_store, db_name, parsed_txs)
        else:
            print(f"ℹ️ Block {height} contains no meaningful transactions, skipping insert.")

# ------------------------
# Entry Point
# ------------------------

if __name__ == "__main__":
    args = sys.argv[1:]
    db_name = DEFAULT_DB

    if "--backup" in args:
        db_name = BACKUP_DB
        args.remove("--backup")

    if "--all-dbs" in args:
        args.remove("--all-dbs")
        all_dbs = get_databases()
        for db in all_dbs:
            print(f"\n🔍 Scanning database: {db}")
            addresses = get_unique_addresses_from_transactionHistory(db)
            print(f"Found {len(addresses)} unique addresses in {db}.")

            for address in addresses:
                print(f"\n🔎 Scanning blocks for address {address} from source DB {db} into target DB {db_name}...")

                blocks = get_blocks_for_address(address)
                if not blocks:
                    continue
                missing = [b for b in blocks if not block_exists(b, db_name)]

                if missing:
                    print(f"⚠️ Missing blocks for address {address}: {missing}")
                    scan_and_fix_blocks(missing, db_name)
                else:
                    print(f"✅ All blocks for {address} already exist in {db_name}.")
    elif len(args) == 1 and not args[0].isdigit():
        address = args[0]
        print(f"🔍 Scanning blocks for FLO address: {address} in {db_name}...")

        blocks = get_blocks_for_address(address)
        if not blocks:
            print(f"⚠️ No valid blocks found for {address}. Skipping.")
            sys.exit(0)

        print(f"Address participated in {len(blocks)} blocks.")

        missing = []
        for b in blocks:
            if not block_exists(b, db_name):
                missing.append(b)

        if missing:
            print(f"⚠️ Missing blocks for address {address}: {missing}")
            scan_and_fix_blocks(missing, db_name)
        else:
            print(f"✅ All blocks for {address} already exist in {db_name}.")
    elif len(args) == 1:
        blocknumber = int(args[0])
        scan_and_fix_blocks([blocknumber], db_name)
    elif len(args) == 2:
        start = int(args[0])
        end = int(args[1])
        scan_and_fix_blocks(range(start, end + 1), db_name)
    else:
        print(f"Usage:")
        print(f"  {sys.argv[0]} <block_height> [--backup]")
        print(f"  {sys.argv[0]} <start_height> <end_height> [--backup]")
        print(f"  {sys.argv[0]} <flo_address> [--backup]")
        print(f"  {sys.argv[0]} --all-dbs [--backup]")
        sys.exit(1)
