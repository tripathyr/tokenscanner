#!/usr/bin/env python3

import asyncio
import aiomysql
import aiohttp
import json
import parsing
import random
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

COLLECTOR_TABLE = "latestBlocksCollector"
FINAL_TABLE = "latestBlocksCollectorSorted"
FAILURE_TABLE = "latestBlocksCollectorFailures"

RETRY_LIMIT = 5
MAX_CONCURRENT_TASKS = 25

# -----------------------
# RANGE CONFIGURATION
# -----------------------

# This is your internal list of ranges to scan:
ranges_to_process = [
    "2210001-2220000",
    "3000000-3500000"

    
    
]

# Convert those strings into numeric ranges
ranges = []
for r in ranges_to_process:
    start_str, end_str = r.split("-")
    start_height = int(start_str.replace(",", "").strip())
    end_height = int(end_str.replace(",", "").strip())
    ranges.append((start_height, end_height))

# -----------------------
# Table Setup
# -----------------------

async def ensure_tables(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {COLLECTOR_TABLE} (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    blockNumber BIGINT,
                    blockHash TEXT,
                    jsonData LONGTEXT,
                    PRIMARY KEY (id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
            """)
            await cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {FAILURE_TABLE} (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    blockNumber BIGINT,
                    reason TEXT,
                    PRIMARY KEY (id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
            """)
            await conn.commit()
    print("✅ Tables checked/created.")

# -----------------------
# Async HTTP Functions
# -----------------------

async def fetch_json(session, url, retries=RETRY_LIMIT):
    for attempt in range(1, retries + 1):
        if session.closed:
            print(f"❌ Session is closed, skipping fetch for {url}")
            return None
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
    if result is None:
        return None
    return result.get(expected_key)

async def get_block_by_hash(session, blockhash):
    url = f"{API_URL}/block/{blockhash}"
    result = await fetch_json(session, url)
    return result

# -----------------------
# Async DB Insert
# -----------------------

async def insert_block(pool, blockinfo):
    json_data = json.dumps(blockinfo)
    try:
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
                print(f"✅ Inserted block {blockinfo['height']} into {COLLECTOR_TABLE}.")
    except pymysql.err.ProgrammingError as e:
        print(f"❌ MySQL error during insert: {e}")
    except pymysql.err.DataError as e:
        size_kb = len(json_data.encode('utf-8')) / 1024
        print(f"⚠️ Block {blockinfo['height']} too large ({size_kb:.1f} KB). Skipping.")
    except Exception as e:
        print(f"❌ Unexpected error inserting block {blockinfo.get('height')}: {e}")

# -----------------------
# Collector Logic
# -----------------------

async def collect_block(pool, session, blockheight, failures):
    try:
        blockhash = await get_blockhash_by_height(session, blockheight)
        if not blockhash:
            reason = "Could not fetch blockhash after retries"
            print(f"⚠️ Block {blockheight} permanently failed. Reason: {reason}")
            failures.append((blockheight, reason))
            return "failed"

        blockinfo = await get_block_by_hash(session, blockhash)
        if not blockinfo:
            reason = "Could not fetch block data after retries"
            print(f"⚠️ Block {blockheight} permanently failed. Reason: {reason}")
            failures.append((blockheight, reason))
            return "failed"

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
            return "collected"
        else:
            print(f"ℹ️ Block {blockheight} skipped. No meaningful parsed transactions.")
            return "skipped"

    except Exception as e:
        reason = f"Unexpected error: {e}"
        print(f"❌ Block {blockheight} failed with unexpected error: {e}")
        failures.append((blockheight, reason))
        return "failed"

# -----------------------
# Rearranging Logic
# -----------------------

async def rearrange_collected_data(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"""
                DROP TABLE IF EXISTS {FINAL_TABLE};
            """)
            await cur.execute(f"""
                CREATE TABLE {FINAL_TABLE} LIKE {COLLECTOR_TABLE};
            """)
            await cur.execute(f"""
                INSERT INTO {FINAL_TABLE} (blockNumber, blockHash, jsonData)
                SELECT blockNumber, blockHash, jsonData
                FROM {COLLECTOR_TABLE}
                ORDER BY blockNumber;
            """)
            await conn.commit()
    print(f"✅ Rearranged data into {FINAL_TABLE}.")

# -----------------------
# Save Permanently Failed Blocks to DB
# -----------------------

async def save_failed_blocks(pool, failures):
    if not failures:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"TRUNCATE TABLE {FAILURE_TABLE}")
                await conn.commit()
        print("✅ No permanently failed blocks. Failure table cleaned up.")
        return

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"TRUNCATE TABLE {FAILURE_TABLE}")
            sql = f"""
                INSERT INTO {FAILURE_TABLE} (blockNumber, reason)
                VALUES (%s, %s)
            """
            await cur.executemany(sql, failures)
            await conn.commit()
    print(f"⚠️ Saved {len(failures)} permanently failed blocks to {FAILURE_TABLE}")

# -----------------------
# Process a single range
# -----------------------

async def process_range(pool, session, start_height, end_height):
    block_heights = list(range(start_height, end_height + 1))
    random.shuffle(block_heights)

    collected_blocks = set()
    failures = []
    collected_count = 0
    skipped_count = 0
    total_blocks = len(block_heights)

    tasks = []
    for blockheight in block_heights:
        if blockheight not in collected_blocks:
            collected_blocks.add(blockheight)
            tasks.append(
                collect_block(pool, session, blockheight, failures)
            )
        if len(tasks) >= MAX_CONCURRENT_TASKS:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if result == "collected":
                    collected_count += 1
                elif result == "skipped":
                    skipped_count += 1
            completed = collected_count + skipped_count + len(failures)
            print(f"Progress: {completed}/{total_blocks} blocks done "
                  f"(collected: {collected_count}, skipped: {skipped_count}, failed: {len(failures)})")
            tasks = []

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if result == "collected":
                collected_count += 1
            elif result == "skipped":
                skipped_count += 1
        completed = collected_count + skipped_count + len(failures)
        print(f"Progress: {completed}/{total_blocks} blocks done "
              f"(collected: {collected_count}, skipped: {skipped_count}, failed: {len(failures)})")

    await rearrange_collected_data(pool)
    await save_failed_blocks(pool, failures)

    print("\n✅ RANGE SUMMARY:")
    print(f"Range {start_height}–{end_height}")
    print(f"Total blocks attempted: {total_blocks}")
    print(f"Successfully collected: {collected_count}")
    print(f"Skipped (empty/noise): {skipped_count}")
    print(f"Permanently failed: {len(failures)}")

# -----------------------
# Main Entry
# -----------------------

async def main(custom_ranges=None):
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

    await ensure_tables(pool)

    ranges_to_use = custom_ranges if custom_ranges is not None else ranges

    async with aiohttp.ClientSession() as session:
        for (start_height, end_height) in ranges_to_use:
            print(f"\n🚀 Processing range {start_height} → {end_height}")
            await process_range(pool, session, start_height, end_height)

    pool.close()
    await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())

