#!/usr/bin/env python3

import sys
import time
import json
import requests
import pymysql
import parsing   # your parsing.py

# ------------------------
# CONFIG
# ------------------------

NETWORK = "testnet"

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

BLOCKBOOK_TYPE = "address_indexer"

RETRY_INTERVAL = 5

# ------------------------
# Derived Config
# ------------------------

if NETWORK == "mainnet":
    API_URL = BLOCKBOOK_MAINNET_URL_LIST.rstrip("/") + "/api"
    ADDRESS_PREFIXES = ["F", "e"]
else:
    API_URL = BLOCKBOOK_TESTNET_URL_LIST.rstrip("/") + "/api"
    ADDRESS_PREFIXES = ["o", "e"]

# ------------------------
# DB Functions
# ------------------------

def block_exists(blocknumber, db_name):
    """
    Checks if blockNumber exists in latestBlocks table of the selected db.
    """
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

def insert_block_into_latestBlocks(blockinfo, db_name):
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
            json_data = json.dumps(blockinfo)

            try:
                cursor.execute(sql, (
                    blockinfo["height"],
                    blockinfo["hash"],
                    json_data
                ))
                connection.commit()
                print(f"✅ Inserted block {blockinfo['height']} into {db_name}.")
            except pymysql.err.DataError as e:
                if "Data too long for column" in str(e):
                    size_kb = len(json_data.encode('utf-8')) / 1024
                    print(f"⚠️ Skipping block {blockinfo['height']} because JSON data is too large ({size_kb:.1f} KB).")
                    connection.rollback()
                else:
                    raise

    finally:
        connection.close()

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

        # Process txs through parser pipeline
        parsed_txs = []
        for tx in blockinfo.get("txs", []):
            flodata = tx.get("floData", "")
            flodata_clean = flodata.replace("\n", " \n ")
            parsed = parsing.parse_flodata(flodata_clean, blockinfo, NETWORK)

            if parsed and parsed.get("type") not in [None, "", "noise"]:
                parsed_txs.append({
                    "txid": tx["txid"],
                    "parsedData": parsed
                })

        if parsed_txs:
            blockinfo_to_store = dict(blockinfo)
            blockinfo_to_store["parsedResult"] = parsed_txs

            insert_block_into_latestBlocks(blockinfo_to_store, db_name)
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

    if len(args) == 1 and not args[0].isdigit():
        # address mode
        address = args[0]

        # ✅ Check address prefix
        if not any(address.startswith(prefix) for prefix in ADDRESS_PREFIXES):
            print(f"⚠️ Address {address} does not look valid for {NETWORK}. Skipping.")
            sys.exit(1)

        print(f"🔍 Scanning blocks for FLO address: {address} in {db_name}...")

        blocks = get_blocks_for_address(address)
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
        # single block
        blocknumber = int(args[0])
        scan_and_fix_blocks([blocknumber], db_name)

    elif len(args) == 2:
        start = int(args[0])
        end = int(args[1])
        scan_and_fix_blocks(range(start, end+1), db_name)

    else:
        print(f"Usage:")
        print(f"  {sys.argv[0]} <block_height> [--backup]")
        print(f"  {sys.argv[0]} <start_height> <end_height> [--backup]")
        print(f"  {sys.argv[0]} <flo_address> [--backup]")
        sys.exit(1)
