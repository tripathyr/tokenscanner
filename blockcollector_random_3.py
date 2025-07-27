#!/usr/bin/env python3

import asyncio
import aiomysql
import aiohttp
import json
import parsing
import random
import pymysql.err
import configparser
import os

# -----------------------
# CONFIG LOAD
# -----------------------

config_path = os.path.join(os.path.dirname(__file__), "config.ini")

config = configparser.ConfigParser()
config.read(config_path)

cfg_default = config["DEFAULT"]
cfg_mysql = config["MYSQL"]
cfg_api = config["API"]

# -----------------------
# CONFIG
# -----------------------

NETWORK = cfg_default.get("NET", "testnet").strip()

BLOCKBOOK_MAINNET_URL_LIST = cfg_default.get("BLOCKBOOK_MAINNET_URL_LIST", "").strip()
BLOCKBOOK_TESTNET_URL_LIST = cfg_default.get("BLOCKBOOK_TESTNET_URL_LIST", "").strip()

MYSQL_USER = cfg_mysql.get("USERNAME", "").strip()
MYSQL_PASSWORD = cfg_mysql.get("PASSWORD", "").strip()
MYSQL_HOST = cfg_mysql.get("HOST", "").strip()
MYSQL_DB_PREFIX = cfg_mysql.get("DATABASE_PREFIX", "").strip()

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
PROCESSED_CHUNKS_TABLE = "ProcessedChunks"

RETRY_LIMIT = int(cfg_default.get("RETRY_LIMIT", 5))
MAX_CONCURRENT_TASKS = 25
RANGE_CHUNK_SIZE = int(cfg_default.get("RANGE_CHUNK_SIZE", 50000))

# -----------------------
# RANGE CONFIGURATION
# -----------------------

ranges_to_process = [
    "2210001-2220000",
    "3000000-3500000"
]

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
                    blockNumber BIGINT UNIQUE,
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

            await cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {PROCESSED_CHUNKS_TABLE} (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    range_start BIGINT,
                    range_end BIGINT,
                    PRIMARY KEY (id),
                    UNIQUE KEY unique_range (range_start, range_end)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            await conn.commit()

    print("✅ Tables checked/created.")

# -----------------------
# Chunk tracking logic
# -----------------------

async def chunk_already_processed(pool, start_height, end_height):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            sql = f"""
                SELECT id FROM {PROCESSED_CHUNKS_TABLE}
                WHERE range_start = %s AND range_end = %s
            """
            await cur.execute(sql, (start_height, end_height))
            row = await cur.fetchone()
            return row is not None

async def save_processed_chunk(pool, start_height, end_height):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            sql = f"""
                INSERT IGNORE INTO {PROCESSED_CHUNKS_TABLE}
                    (range_start, range_end)
                VALUES (%s, %s)
            """
            await cur.execute(sql, (start_height, end_height))
            await conn.commit()
    print(f"✅ Saved completed chunk {start_height}-{end_height}")

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
                    ON DUPLICATE KEY UPDATE jsonData = VALUES(jsonData)
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
            floData = tx.get("floData", "")
            floData_clean = floData.replace("\n", " \n ")
            parsed = parsing.parse_floData(floData_clean, blockinfo, NETWORK)

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

    # Mark this chunk complete
    await save_processed_chunk(pool, start_height, end_height)

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
            current_start = start_height
            while current_start <= end_height:
                current_end = min(current_start + RANGE_CHUNK_SIZE - 1, end_height)

                already_done = await chunk_already_processed(pool, current_start, current_end)
                if already_done:
                    print(f"✅ Skipping chunk {current_start}-{current_end} (already processed).")
                else:
                    print(f"\n🚀 Processing chunk {current_start} → {current_end}")
                    await process_range(pool, session, current_start, current_end)

                current_start = current_end + 1

    pool.close()
    await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
