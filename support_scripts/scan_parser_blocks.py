#!/usr/bin/env python3
import sys
import time
import json
import requests
import pymysql

# Import your parsing module
import parsing

# ------------------------
# CONFIG
# ------------------------

# NETWORK can be either "mainnet" or "testnet"
NETWORK = "mainnet"

# Blockbook URLs
BLOCKBOOK_MAINNET_URL_LIST = "http://127.0.0.1/"
BLOCKBOOK_TESTNET_URL_LIST = "https://blockbook-testnet.ranchimall.net/"

# MySQL Config
MYSQL_USER = "new_user"
MYSQL_PASSWORD = "user_password"
MYSQL_HOST = "localhost"
MYSQL_DATABASE = "rm_parser_db"

# Blockbook API type
BLOCKBOOK_TYPE = "address_indexer"

# Retry interval in seconds
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

def ensure_table_exists():
    """
    Create parsedBlocks table if it doesn't exist.
    """
    connection = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        charset="utf8mb4"
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DATABASE}")
            cursor.execute(f"USE {MYSQL_DATABASE}")
            sql = """
            CREATE TABLE IF NOT EXISTS parsedBlocks (
                blockNumber BIGINT PRIMARY KEY,
                blockHash TEXT,
                jsonData LONGTEXT,
                parsedResult LONGTEXT
            )
            """
            cursor.execute(sql)
        connection.commit()
        print("✅ ensured parsedBlocks table exists.")
    finally:
        connection.close()

def store_parsed_data_mysql(block_number, block_hash, block_json, parsed_data):
    """
    REPLACE INTO parsedBlocks in rm_parser_db
    """
    connection = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4"
    )
    try:
        with connection.cursor() as cursor:
            sql = """
                REPLACE INTO parsedBlocks (blockNumber, blockHash, jsonData, parsedResult)
                VALUES (%s, %s, %s, %s)
            """
            cursor.execute(sql, (
                block_number,
                block_hash,
                json.dumps(block_json),
                json.dumps(parsed_data)
            ))
        connection.commit()
        print(f"✅ Block {block_number} stored/replaced in parsedBlocks table.")
    finally:
        connection.close()

# ------------------------
# Blockbook Functions
# ------------------------

def get_blockhash_by_height(blockindex):
    """
    Fetch block hash for a given block height.
    """
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
    """
    Fetch full block JSON by block hash.
    """
    url = f"{API_URL}/block/{blockhash}"
    while True:
        try:
            r = requests.get(url, timeout=10, verify=False)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"⚠️ Error fetching block {blockhash}: {e}")
            time.sleep(RETRY_INTERVAL)

# ------------------------
# Main Scanning Logic
# ------------------------

def scan_blocks(start_height, end_height):
    ensure_table_exists()

    for height in range(start_height, end_height + 1):
        blockhash = get_blockhash_by_height(height)
        print(f"\n=== Block {height} | hash {blockhash} ===")

        blockinfo = get_block_by_hash(blockhash)

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
            store_parsed_data_mysql(
                height,
                blockhash,
                blockinfo,
                parsed_txs
            )

            # Print parsed transactions to console
            print(f"\nParsed transactions for block {height}:\n")
            for tx in parsed_txs:
                print(f"TXID: {tx['txid']}")
                print("Parsed Data:")
                print(json.dumps(tx["parsedData"], indent=4))
                print("-" * 40)

        else:
            print(f"ℹ️ No meaningful floData found in block {height}.")

# ------------------------
# Entry Point
# ------------------------

if __name__ == "__main__":
    if len(sys.argv) == 2:
        # Single block mode
        block_height = int(sys.argv[1])
        scan_blocks(block_height, block_height)
    elif len(sys.argv) == 3:
        # Range mode
        start_height = int(sys.argv[1])
        end_height = int(sys.argv[2])
        scan_blocks(start_height, end_height)
    else:
        print(f"Usage:")
        print(f"    {sys.argv[0]} <block_height>")
        print(f"    {sys.argv[0]} <start_height> <end_height>")
        sys.exit(1)
