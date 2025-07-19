import asyncio
import json
import aiomysql
import os

BASE_DB_CONFIG = {
    "host": "localhost",
    "user": "new_user",
    "password": "user_password",
    "db": "rm_addressindexer_data2",
    "autocommit": True,
    "charset": "utf8mb4"
}

PORTS_TO_TRY = [3306, 3307]
BATCH_SIZE = 100
PROGRESS_FILE = "last_processed_id.txt"

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
            addresses = v.get("scriptPubKey", {}).get("addresses", [])
            return {
                "value": v.get("value"),
                "addresses": addresses,
                "scriptPubKey": {"addresses": addresses}
            }

        slim_tx = {
            "version": tx.get("version"),
            "locktime": tx.get("locktime"),
            "blockheight": tx.get("blockheight"),
            "confirmations": tx.get("confirmations"),
            "valueIn": tx.get("valueIn"),
            "valueOut": tx.get("valueOut"),
            "fees": tx.get("fees"),
            "vin": [slim_vin(v) for v in tx.get("vin", [])],
            "vout": [slim_vout(v) for v in tx.get("vout", [])]
        }

        return json.dumps(slim_tx, separators=(',', ':'))

    except Exception as e:
        print(f"Error slimming tx: {e}")
        return raw_json_str

def read_last_id():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return int(f.read().strip())
    return 0

def write_last_id(last_id):
    with open(PROGRESS_FILE, "w") as f:
        f.write(str(last_id))

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
        last_id = read_last_id()
        total_original = 0
        total_slimmed = 0
        total_saved = 0
        updated_count = 0
        batch_num = 1

        while True:
            await cursor.execute("""
                SELECT id, txid, raw_transaction_json
                FROM transactions
                WHERE id > %s
                ORDER BY id ASC
                LIMIT %s;
            """, (last_id, BATCH_SIZE))
            rows = await cursor.fetchall()

            if not rows:
                break

            for row in rows:
                last_id = row["id"]
                original_json = row["raw_transaction_json"]
                if not original_json:
                    continue

                slimmed_json = slim_raw_transaction_json(original_json)
                original_len = len(original_json)
                slimmed_len = len(slimmed_json)

                total_original += original_len
                total_slimmed += slimmed_len

                if slimmed_len < original_len:
                    await cursor.execute("""
                        UPDATE transactions
                        SET raw_transaction_json = %s
                        WHERE id = %s;
                    """, (slimmed_json, row["id"]))

                    updated_count += 1
                    total_saved += (original_len - slimmed_len)

            await conn.commit()
            write_last_id(last_id)

            print(f"📦 Batch {batch_num}: up to ID {last_id} | Updated: {updated_count} | Saved: {total_saved / 1024:.2f} KB")
            batch_num += 1

        print("\n✅ All batches processed.")
        print(f"Total original size: {total_original / 1024:.2f} KB")
        print(f"Total slimmed size:  {total_slimmed / 1024:.2f} KB")
        print(f"Total saved:         {total_saved / 1024:.2f} KB")
        print(f"Reduction:           {100 * (1 - total_slimmed / total_original):.2f}%")
        print(f"Total rows updated:  {updated_count}")

    conn.close()

asyncio.run(main())
