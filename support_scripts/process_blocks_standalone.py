#!/usr/bin/env python3

import sys
import pymysql
import json
import logging
import os

# ----------------------------
# CONFIG
# ----------------------------

NETWORK = "testnet"
MYSQL_USER = "new_user"
MYSQL_PASSWORD = "user_password"
MYSQL_HOST = "localhost"
MYSQL_DB_PREFIX = "rm"

if NETWORK == "testnet":
    MYSQL_DB_PREFIX += "test"

MYSQL_DB = f"{MYSQL_DB_PREFIX}_latestCache_db"
TABLE_NAME = "latestBlocksCollectorSorted"

# ----------------------------
# TRACKTOKENS IMPORTS
# ----------------------------

# This imports your logic
# Adjust the path below if necessary
from tracktokens32_fix1 import (
    processTransaction,
    normalize_transaction_data,
    set_configs,
)

# ----------------------------
# LOGGER SETUP
# ----------------------------

logger = logging.getLogger("process_stored_blocks")
logger.setLevel(logging.INFO)

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)

# ----------------------------
# INIT CONFIGS (OPTIONAL)
# ----------------------------

# If your tracktokens logic uses global configs,
# ensure they’re set properly here. E.g.:
#
# config_file_path = "/path/to/your/config.ini"
# set_configs(configparser.ConfigParser().read(config_file_path))
#
# If your tracktokens logic relies on environment variables,
# set them here too:
# os.environ["SOME_API_KEY"] = "xyz"

# ----------------------------
# PROCESS A SINGLE BLOCK
# ----------------------------

def process_block(blockinfo):
    """
    Process one block worth of transactions.
    """
    txs_by_txid = {}
    for tx in blockinfo.get("txs", []):
        txs_by_txid[tx["txid"]] = tx

    parsed_results = blockinfo.get("parsedResult", [])
    for parsed_tx in parsed_results:
        txid = parsed_tx.get("txid")
        parsed_data = parsed_tx.get("parsedData")

        tx_data = txs_by_txid.get(txid)
        if not tx_data:
            logger.warning(f"⚠️ Skipping transaction {txid} - not found in tx list.")
            continue

        # TrackTokens logic expects normalized transactions
        normalize_transaction_data(tx_data)

        try:
            result = processTransaction(tx_data, parsed_data, blockinfo)
            if result == 1:
                logger.info(f"✅ Transaction {txid} processed successfully.")
            else:
                logger.warning(f"⚠️ Transaction {txid} returned result code {result}.")
        except Exception as e:
            logger.error(f"❌ Error processing transaction {txid}: {e}")

# ----------------------------
# MAIN LOGIC
# ----------------------------

def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <start_block> <end_block>")
        sys.exit(1)

    start_block = int(sys.argv[1])
    end_block = int(sys.argv[2])

    connection = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset="utf8mb4"
    )

    try:
        with connection.cursor() as cursor:
            sql = f"""
                SELECT blockNumber, jsonData
                FROM {TABLE_NAME}
                WHERE blockNumber BETWEEN %s AND %s
                ORDER BY blockNumber
            """
            cursor.execute(sql, (start_block, end_block))
            rows = cursor.fetchall()

            for blockNumber, jsonData in rows:
                logger.info(f"\n🚀 Processing block {blockNumber}...")
                blockinfo = json.loads(jsonData)
                process_block(blockinfo)

    finally:
        connection.close()

if __name__ == "__main__":
    main()
