import asyncio
import json
from decimal import Decimal
import aiomysql

# Static config, no port yet
BASE_DB_CONFIG = {
    "host": "localhost",
    "user": "new_user",
    "password": "user_password",
    "db": "rm_addressindexer_data2",
    "autocommit": True,
    "charset": "utf8mb4"
}

PORTS_TO_TRY = [3306, 3307]

def slim_raw_transaction_json(raw_json_str):
    try:
        tx = json.loads(raw_json_str)

        def slim_vin(v):
            return {
                "txid": v.get("txid"),
                "vout": v.get("vout"),
                "value": v.get("value"),
                "addresses": v.get("addresses", [])
            }

        def slim_vout(v):
            return {
                "value": v.get("value"),
                "scriptPubKey": {
                    "addresses": v.get("scriptPubKey", {}).get("addresses", [])
                }
            }

        slim_tx = {
            "vin": [slim_vin(v) for v in tx.get("vin", [])],
            "vout": [slim_vout(v) for v in tx.get("vout", [])]
        }

        return json.dumps(slim_tx, separators=(',', ':'))
    except Exception as e:
        print(f"Error slimming tx: {e}")
        return raw_json_str

async def try_connect():
    for port in PORTS_TO_TRY:
        try:
            config = BASE_DB_CONFIG.copy()
            config["port"] = port
            conn = await aiomysql.connect(**config)
            print(f"✅ Connected to MySQL on port {port}")
            return conn
        except Exception as e:
            print(f"⚠️  Failed to connect on port {port}: {e}")
    raise RuntimeError("❌ Could not connect to MySQL on any of the specified ports.")

async def main():
    conn = await try_connect()
    async with conn.cursor(aiomysql.DictCursor) as cursor:
        await cursor.execute("""
            SELECT id, txid, raw_transaction_json
            FROM transactions
            WHERE raw_transaction_json IS NOT NULL
            LIMIT 100;
        """)
        rows = await cursor.fetchall()

        total_original = 0
        total_slimmed = 0

        print("\n📦 Showing first 3 before/after:\n")

        for i, row in enumerate(rows):
            original_json = row['raw_transaction_json']
            slimmed_json = slim_raw_transaction_json(original_json)

            original_len = len(original_json)
            slimmed_len = len(slimmed_json)

            total_original += original_len
            total_slimmed += slimmed_len

            if i < 3:
                print(f"--- TX {i+1}: {row['txid']} ---")
                print(f"Original size: {original_len} bytes")
                print(f"Slimmed size:  {slimmed_len} bytes\n")
                print("Original JSON:")
                print(original_json[:500] + "...")  # Truncate for readability
                print("\nSlimmed JSON:")
                print(slimmed_json)
                print("\n" + "-"*60 + "\n")

        print("📊 Summary over 100 transactions:")
        print(f"Total original size: {total_original / 1024:.2f} KB")
        print(f"Total slimmed size:  {total_slimmed / 1024:.2f} KB")
        print(f"Estimated saved:     {(total_original - total_slimmed) / 1024:.2f} KB")
        print(f"Reduction:           {100 * (1 - total_slimmed / total_original):.2f}%")

    conn.close()

asyncio.run(main())
