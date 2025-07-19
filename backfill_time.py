import asyncio
import logging
from configparser import ConfigParser
import aiohttp
import aiomysql
from datetime import datetime

# Configuration Setup
config = ConfigParser()
config.read('config.ini')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# MySQL Configurations
MYSQL_HOST = config['MYSQL'].get('HOST', 'localhost')
MYSQL_USER = config['MYSQL'].get('USERNAME', 'default_user')
MYSQL_PASSWORD = config['MYSQL'].get('PASSWORD', 'default_password')
MYSQL_DB_PREFIX = config['MYSQL'].get('DATABASE_PREFIX', 'rm')
ADDRESS_INDEXER_SUFFIX = config['ADDRESS_INDEXER'].get('SUFFIX', '_data')
ADDRESS_INDEXER_DB_NAME = f"{MYSQL_DB_PREFIX}_addressindexer{ADDRESS_INDEXER_SUFFIX}"

# RPC Configurations
RPC_ADDRESS = config['ADDRESS_INDEXER'].get('rpc_address', 'http://127.0.0.1:8066/')
RPC_USER = config['ADDRESS_INDEXER'].get('rpc_user', 'rpc')
RPC_PASSWORD = config['ADDRESS_INDEXER'].get('rpc_password', 'rpc')

# Connection Pool
async_connection_pool = None


async def initialize_connection_pool():
    """Initialize the MySQL connection pool and handle multiple ports."""
    global async_connection_pool
    ports_to_try = [3306, 3307]

    for port in ports_to_try:
        try:
            async_connection_pool = await aiomysql.create_pool(
                host=MYSQL_HOST,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                db=ADDRESS_INDEXER_DB_NAME,
                charset="utf8mb4",
                autocommit=True,
                maxsize=10,
                port=port
            )
            logger.info(f"Connected to MySQL on port {port}")
            return
        except Exception as e:
            logger.warning(f"Failed to connect to MySQL on port {port}: {e}")

    logger.error("Unable to connect to MySQL on any port.")
    raise RuntimeError("Could not establish a connection to MySQL.")


async def get_mysql_connection():
    """Acquire a connection from the pool."""
    if async_connection_pool is None:
        raise RuntimeError("Connection pool not initialized")
    return await async_connection_pool.acquire()


async def rpc_request(method, params=None):
    """Helper function to make RPC requests."""
    payload = {
        "jsonrpc": "1.0",
        "id": "query",
        "method": method,
        "params": params or []
    }
    auth = aiohttp.BasicAuth(RPC_USER, RPC_PASSWORD)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(RPC_ADDRESS, json=payload, auth=auth, timeout=10) as response:
                if response.status != 200:
                    logger.error(f"RPC request failed: {response.status} {await response.text()}")
                    return {"error": "RPC request failed", "status": response.status}
                return await response.json()
        except Exception as e:
            logger.error(f"Error making RPC request: {e}")
            return {"error": str(e)}


async def backfill_time_data():
    """
    Backfill `processed_blocks.processed_at` and `transactions.time` using block data.
    """
    conn = await get_mysql_connection()

    try:
        async with conn.cursor() as cursor:
            # Get all block heights
            await cursor.execute("SELECT block_height FROM processed_blocks ORDER BY block_height ASC")
            blocks_to_process = [row[0] for row in await cursor.fetchall()]

            for block_height in blocks_to_process:
                logger.info(f"Processing block {block_height}")

                # Fetch block data from the RPC API
                blockhash_response = await rpc_request("getblockhash", [block_height])
                if "result" not in blockhash_response:
                    logger.warning(f"Block hash not found for block {block_height}")
                    continue
                blockhash = blockhash_response["result"]

                block_data_response = await rpc_request("getblock", [blockhash, 2])
                if "result" not in block_data_response:
                    logger.warning(f"Block data not found for block {block_height}")
                    continue
                block_data = block_data_response["result"]

                # Extract block time
                block_time = block_data.get("time")
                if not block_time:
                    logger.warning(f"No time found for block {block_height}. Skipping.")
                    continue

                # Update `processed_blocks`
                try:
                    await cursor.execute(
                        """
                        UPDATE processed_blocks
                        SET processed_at = FROM_UNIXTIME(%s)
                        WHERE block_height = %s
                        """,
                        (block_time, block_height),
                    )
                except Exception as e:
                    logger.error(f"Error updating processed_blocks for block {block_height}: {e}")
                    continue

                # Update `transactions`
                txs = block_data.get("tx", [])
                for tx in txs:
                    txid = tx["txid"]
                    try:
                        await cursor.execute(
                            """
                            UPDATE transactions
                            SET time = FROM_UNIXTIME(%s)
                            WHERE txid = %s
                            """,
                            (block_time, txid),
                        )
                    except Exception as e:
                        logger.error(f"Error updating transaction {txid} in block {block_height}: {e}")
                        continue

                logger.info(f"Block {block_height} and its transactions updated successfully.")

            # Commit changes
            await conn.commit()

    except Exception as e:
        logger.error(f"Error in backfilling process: {e}")
    finally:
        try:
            async_connection_pool.release(conn)
        except Exception as release_error:
            logger.error(f"Failed to release connection during backfilling: {release_error}")


async def main():
    await initialize_connection_pool()
    await backfill_time_data()


if __name__ == "__main__":
    asyncio.run(main())
