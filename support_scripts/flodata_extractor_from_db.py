#!/usr/bin/env python3

import sys
import json
import pymysql
import argparse
import parsing

# ------------------------
# All config in-code
# ------------------------

CONFIGS = {
    "mainnet": {
        "NETWORK": "mainnet",
        "DB_NAME": "rm_latestCache_db",
        "API_URL": "http://127.0.0.1/api",
        "MYSQL_USER": "new_user",
        "MYSQL_PASSWORD": "user_password",
        "MYSQL_HOST": "localhost"
    },
    "testnet": {
        "NETWORK": "testnet",
        "DB_NAME": "rmtest_latestCache_db",
        "API_URL": "https://blockbook-testnet.ranchimall.net/api",
        "MYSQL_USER": "new_user",
        "MYSQL_PASSWORD": "user_password",
        "MYSQL_HOST": "localhost"
    }
}

# ------------------------
# CLI arguments
# ------------------------

parser = argparse.ArgumentParser()
parser.add_argument(
    "--net",
    choices=["mainnet", "testnet"],
    default="testnet",
    help="Network to extract from"
)
args = parser.parse_args()
NET = args.net

# ------------------------
# Load chosen config
# ------------------------

config = CONFIGS[NET]

NETWORK = config["NETWORK"]
DB_NAME = config["DB_NAME"]
API_URL = config["API_URL"]
MYSQL_USER = config["MYSQL_USER"]
MYSQL_PASSWORD = config["MYSQL_PASSWORD"]
MYSQL_HOST = config["MYSQL_HOST"]

print(f"🔧 Running for network: {NETWORK}")
print(f"🔧 Database: {DB_NAME}")
print(f"🔧 API URL: {API_URL}")

# ------------------------
# Connect to DB
# ------------------------

connection = pymysql.connect(
    host=MYSQL_HOST,
    user=MYSQL_USER,
    password=MYSQL_PASSWORD,
    database=DB_NAME,
    charset="utf8mb4"
)

# ------------------------
# Process all blocks
# ------------------------

try:
    with connection.cursor() as cursor:
        sql = "SELECT blockNumber, jsonData FROM latestBlocks ORDER BY blockNumber"
        cursor.execute(sql)
        rows = cursor.fetchall()

        for blockNumber, jsonData in rows:
            blockinfo = json.loads(jsonData)

            txs = blockinfo.get("txs", [])
            if not txs:
                continue

            print(f"\n======= Block {blockNumber} =======")

            for tx in txs:
                txid = tx.get("txid")
                flodata = tx.get("floData", "")

                if not flodata:
                    continue

                # Clean up floData to replace newlines
                flodata_clean = flodata.replace("\n", " \n ")

                # Call your parse logic
                parsed = parsing.parse_flodata(flodata_clean, blockinfo, NETWORK)

                if parsed and parsed.get("type") not in [None, "", "noise"]:
                    print(f"TxID: {txid}")
                    print("Parsed floData:")
                    print(json.dumps(parsed, indent=2))
                    print("-----------")

finally:
    connection.close()
