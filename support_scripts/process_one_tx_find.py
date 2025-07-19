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

from tracktokens32_fix1 import (
    processTransaction,
    normalize_transaction_data,
    set_configs
)

# ----------------------------
# LOGGER SETUP
# ----------------------------

logger = logging.getLogger("process_one_tx_find")
logger.setLevel(logging.INFO)

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)

# ----------------------------
# CONFIG INIT (OPTIONAL)
# ----------------------------

# If your tracktokens logic depends on configs:
#
# import configparser
# config = configparser.ConfigParser()
# config.read("/path/to/config.ini")
# set_configs(config)

# ----------------------------
# PROCESS A SINGLE TRANSACTION
# ----------------------------

def process_transaction_from_block(blockinfo, txid):
    txs_by_txid = {
        tx["txid"]: tx
        for tx in blockinfo.get("txs", [])
    }

    tx_data = txs_by_txid.get(txid)
    if not tx_data:
        return False

    # Find parsedData
    parsed_data = None
    for pr in blockinfo.get("parsedResult", []):
        if pr.get("txid") == txid:
            parsed_data = pr.get("parsedData")
            break

    if not parsed_data:
        logger.warning(f"⚠️ No parsedData found for transaction {txid}. Skipping.")
        return True

    normalize_transaction_data(tx_data)

    try:
        result = processTransaction(tx_data, parsed_data, blockinfo)
        if result == 1:
            logger.info(f"✅ Transaction {txid} processed successfully.")
        else:
            logger.warning(f"⚠️ Transaction {txid} returned result code {result}.")
    except Exception as e:
        logger.error(f"❌ Error processing transaction {txid}: {e}")

    return True

# ----------------------------
# MAIN
# ----------------------------

def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <transaction_id>")
        sys.exit(1)

    txid = sys.argv[1]

    connection = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset="utf8mb4"
    )

    found = False

    try:
        with connection.cursor() as cursor:
            sql = f"""
                SELECT blockNumber, jsonData
                FROM {TABLE_NAME}
                ORDER BY blockNumber
            """
            cursor.execute(sql)
            rows = cursor.fetchall()

            for blockNumber, jsonData in rows:
                blockinfo = json.loads(jsonData)

                success = process_transaction_from_block(blockinfo, txid)
                if success:
                    logger.info(f"✅ Done processing txid {txid} from block {blockNumber}")
                    found = True
                    break

        if not found:
            logger.error(f"❌ Transaction {txid} not found in any block!")

    finally:
        connection.close()

if __name__ == "__main__":
    main()
