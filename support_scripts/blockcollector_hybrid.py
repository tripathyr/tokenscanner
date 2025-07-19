#!/usr/bin/env python3

import asyncio
import aiomysql
import aiohttp
import json
import random
import parsing
import pymysql.err

# -----------------------
# CONFIG
# -----------------------

NETWORK = "testnet"

BLOCKBOOK_MAINNET_URL_LIST = "http://127.0.0.1/"
BLOCKBOOK_TESTNET_URL_LIST = "http://127.0.0.1/"

MYSQL_USER = "new_user"
MYSQL_PASSWORD = "user_password"
MYSQL_HOST = "localhost"
MYSQL_DB_PREFIX = "rm"

if NETWORK == "testnet":
    MYSQL_DB_PREFIX += "test"

MYSQL_DB = f"{MYSQL_DB_PREFIX}_latestCache_db"

BLOCKBOOK_TYPE = "address_indexer"

API_URL = (
    BLOCKBOOK_MAINNET_URL_LIST.rstrip("/") + "/api"
    if NETWORK == "mainnet"
    else BLOCKBOOK_TESTNET_URL_LIST.rstrip("/") + "/api"
)

PROGRESS_SAVE_INTERVAL = 5_000

COLLECTOR_TABLE = "latestBlocksCollector"

START_HEIGHT = 460_000
END_HEIGHT = 500_000

# -----------------------
# DB Utility Functions
# -----------------------

async def ensure_metadata_tables(pool):
    """
    Create tables blockScanPlan and blockScanProgress if not exist.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS blockScanPlan (
                    blockIndex BIGINT PRIMARY KEY,
                    blockNumber BIGINT NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS blockScanProgress (
                    id INT PRIMARY KEY,
                    currentIndex BIGINT
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            await conn.commit()

async def save_block_list(pool, block_list):
    """
    Save block list to DB table blockScanPlan.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM blockScanPlan")
            rows = [(i, block_list[i]) for i in range(len(block_list))]
            sql = "INSERT INTO blockScanPlan (blockIndex, blockNumber) VALUES (%s, %s)"
            await cur.executemany(sql, rows)
            await conn.commit()
    print(f"✅ Block list saved to blockScanPlan table.")

async def load_block_list(pool):
    """
    Load block list from DB table blockScanPlan.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT blockIndex, blockNumber
                FROM blockScanPlan
                ORDER BY blockIndex;
            """)
            rows = await cur.fetchall()
            block_list = [r[1] for r in rows]
            return block_list

async def save_progress(pool, currentIndex):
    """
    Save progress counter to DB.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO blockScanProgress (id, currentIndex)
                VALUES (1, %s)
                ON DUPLICATE KEY UPDATE currentIndex = VALUES(currentIndex);
            """, (currentIndex,))
            await conn.commit()
    print(f"✅ Progress saved: currentIndex = {currentIndex}")

async def load_progress(pool):
    """
    Load current progress index from DB.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT currentIndex FROM blockScanProgress WHERE id = 1")
            row = await cur.fetchone()
            return row[0] if row else 0

# -----------------------
# HTTP Functions
# -----------------------

async def fetch_json(session, url, retries=5):
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url, timeout=10, ssl=False) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            print(f"⚠️ Error fetching {url} (attempt {attempt}): {e}")
            if attempt == retries:
                print(f"❌ Giving up on {url}")
                return None
            await asyncio.sleep(5)

async def get_blockhash_by_height(session, blockindex):
    if BLOCKBOOK_TYPE == "blockbook_legacy":
        url = f"{API_URL}/v2/block-index/{blockindex}"
        expected_key = "blockHash"
    elif BLOCKBOOK_TYPE == "address_indexer":
        url = f"{API_URL}/blockheight/{blockindex}"
        expected_key = "hash"
    else:
        raise Exception("Unsupported blockbook_type")

    result = await fetch_json(session, url)
    return result.get(expected_key) if result else None

async def get_block_by_hash(session, blockhash):
    url = f"{API_URL}/block/{blockhash}"
    result = await fetch_json(session, url)
    return result

# -----------------------
# Block Scanning
# -----------------------

async def collect_block(pool, session, blockheight):
    try:
        blockhash = await get_blockhash_by_height(session, blockheight)
        if not blockhash:
            print(f"⚠️ Could not fetch blockhash for {blockheight}")
            return False

        blockinfo = await get_block_by_hash(session, blockhash)
        if not blockinfo:
            print(f"⚠️ Could not fetch block data for {blockheight}")
            return False

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
            blockinfo["parsedResult"] = parsed_txs
            await insert_block(pool, blockinfo)
            return True
        else:
            print(f"ℹ️ Block {blockheight} skipped (no meaningful transactions).")
            return False

    except Exception as e:
        print(f"❌ Error scanning block {blockheight}: {e}")
        return False

async def insert_block(pool, blockinfo):
    json_data = json.dumps(blockinfo)
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            sql = f"""
                INSERT INTO {COLLECTOR_TABLE}
                    (blockNumber, blockHash, jsonData)
                VALUES (%s, %s, %s)
            """
            await cur.execute(sql, (
                blockinfo["height"],
                blockinfo["hash"],
                json_data
            ))
            await conn.commit()
    print(f"✅ Block {blockinfo['height']} inserted into {COLLECTOR_TABLE}.")

# -----------------------
# Main
# -----------------------

async def main():
    pool = await aiomysql.create_pool(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        db=MYSQL_DB,
        charset='utf8mb4',
        autocommit=False,
        minsize=1,
        maxsize=10
    )

    await ensure_metadata_tables(pool)

    # Load block list from DB if it exists
    block_list = await load_block_list(pool)
    if not block_list:
        print("⚙️ No block list in DB. Generating new shuffled list...")
        block_list = list(range(START_HEIGHT, END_HEIGHT + 1))
        random.shuffle(block_list)
        await save_block_list(pool, block_list)
        await save_progress(pool, 0)
    else:
        print("✅ Loaded block list from DB.")

    # Load progress
    currentIndex = await load_progress(pool)
    print(f"🔧 Resuming from index: {currentIndex}")

    scanned = 0

    async with aiohttp.ClientSession() as session:
        for i in range(currentIndex, len(block_list)):
            blockheight = block_list[i]
            success = await collect_block(pool, session, blockheight)

            scanned += 1
            if scanned % PROGRESS_SAVE_INTERVAL == 0:
                await save_progress(pool, i + 1)

        # Save final progress
        await save_progress(pool, len(block_list))

    print("\n✅ All blocks completed!")

    pool.close()
    await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
