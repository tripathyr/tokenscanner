# Standard Library Imports
import asyncio
import json
import logging
import math
import re
import signal
import sys
import threading
import time
from collections import OrderedDict
from configparser import ConfigParser
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from uuid import uuid4

# Third-party Libraries
import aiohttp
import aiomysql
import psutil
import zmq
import zmq.asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from hypercorn.asyncio import serve
from hypercorn.config import Config as HypercornConfig
from quart import Quart, jsonify, request, Response, websocket
from quart_cors import cors


# --- Configuration Setup ---
config = ConfigParser()
config.read('config.ini')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

aiohttp_session = None
RUN_UTXO_CLEANUP_ON_STARTUP = True

# --- RPC Configurations ---
RPC_ADDRESS = config['ADDRESS_INDEXER'].get('rpc_address', 'http://127.0.0.1:8066/')
RPC_USER = config['ADDRESS_INDEXER'].get('rpc_user', 'rpc')
RPC_PASSWORD = config['ADDRESS_INDEXER'].get('rpc_password', 'rpc')


RPC_TIMEOUT = 30
API_TIMEOUT = 60
DB_RETRY_TIMEOUT = int(config['API'].get('DB_RETRY_TIMEOUT', 60))
CLEANUP_INTERVAL_SECONDS = 5
IDLE_TIME_THRESHOLD_TIMEOUT= 5
CLEANER_THREAD_EXIT_TIMEOUT = 5




@contextmanager
def override_globals(**kwargs):
    """
    Temporarily override global variables.

    Usage:
        with override_globals(VARIABLE=value, ...):
            ...
    """
    originals = {}
    try:
        for var, new_value in kwargs.items():
            # Save original
            originals[var] = globals().get(var)
            # Override
            globals()[var] = new_value
        yield
    finally:
        # Restore originals
        for var, original_value in originals.items():
            globals()[var] = original_value



# --- ZMQ Setup ---
try:
    ZMQ_BLOCK_HASH_ADDRESS = config['ADDRESS_INDEXER'].get('zmqpubhashblock', '').strip()
    if not ZMQ_BLOCK_HASH_ADDRESS:
        raise ValueError("ZMQ address for zmqpubhashblock not found in config.ini")
    logger.info(f"ZMQ_BLOCK_HASH_ADDRESS set to: {ZMQ_BLOCK_HASH_ADDRESS}")
except Exception as e:
    logger.error(f"Failed to set ZMQ_BLOCK_HASH_ADDRESS: {e}")
    ZMQ_BLOCK_HASH_ADDRESS = None


# --- Async RPC Request ---
async def rpc_request(method, params=None):
    global aiohttp_session

    payload = {
        "jsonrpc": "1.0",
        "id": "query",
        "method": method,
        "params": params or []
    }
    auth = aiohttp.BasicAuth(RPC_USER, RPC_PASSWORD)

    try:
        if aiohttp_session is None:
            aiohttp_session = aiohttp.ClientSession()

        async with aiohttp_session.post(RPC_ADDRESS, json=payload, auth=auth, timeout=RPC_TIMEOUT) as response:
            if response.status != 200:
                raw_error = await response.read()
                error_text = raw_error.decode("utf-8", errors="replace")
                logger.error(f"RPC request failed: {response.status} {error_text}")
                return {"error": "RPC request failed", "status": response.status}

            raw = await response.read()
            text = raw.decode("utf-8", errors="replace")
            return json.loads(text)

    except Exception as e:
        logger.error(f"Error making RPC request: {e}")
        return {"error": str(e)}



# --- Helper: Detect FLOD Network via RPC ---
async def detect_network_via_rpc():
    response = await rpc_request("getblockchaininfo")
    if "result" in response:
        chain = response["result"].get("chain")
        logger.info(f"Network detected from flod RPC: {chain}")
        return chain
    else:
        logger.warning("Unable to detect network from flod RPC. Defaulting to config.ini setting.")
        return config['DEFAULT'].get('NET', 'mainnet')


# --- Static Configurations ---
MYSQL_HOST = config['MYSQL'].get('HOST', 'localhost')
MYSQL_USER = config['MYSQL'].get('USERNAME', 'default_user')
MYSQL_PASSWORD = config['MYSQL'].get('PASSWORD', 'default_password')

ADDRESS_INDEXER_HOST = config['ADDRESS_INDEXER'].get('HOST', 'localhost')
ADDRESS_INDEXER_PORT = config['ADDRESS_INDEXER'].getint('PORT', 6000)
ADDRESS_INDEXER_SUFFIX = config['ADDRESS_INDEXER'].get('SUFFIX', '_data')
ADDRESS_INDEXER_URLS = [url.strip().rstrip('/') for url in config['ADDRESS_INDEXER'].get('URL', '').split(',')]

SELF_URL = f"http://{ADDRESS_INDEXER_HOST}:{ADDRESS_INDEXER_PORT}"

if not ADDRESS_INDEXER_URLS:
    raise ValueError("No URLs configured in the [ADDRESS_INDEXER] section under 'URL'.")


# Global dictionary to track task statuses
task_status = {}

def validate_config():
    required_sections = ['API', 'MYSQL', 'ADDRESS_INDEXER']
    for section in required_sections:
        if section not in config:
            raise ValueError(f"Missing required section: {section}")
    if 'rpc_address' not in config['ADDRESS_INDEXER']:
        raise ValueError("Missing RPC address in [ADDRESS_INDEXER]")


def get_indexer_url(index=0, url=ADDRESS_INDEXER_URLS):
    """
    Return the base URL for the indexer API.
    Defaults to the first URL in the provided list or ADDRESS_INDEXER_URLS.
    """
    try:
        # Check if `url` is a list; if not, treat it as a single URL
        if isinstance(url, list):
            base_url = url[index]
        else:
            # Treat `url` as a single string and return it
            base_url = url

        # Ensure the URL ends with a trailing slash
        if not base_url.endswith('/'):
            base_url += '/'

        return base_url
    except IndexError:
        raise ValueError(f"No valid URL available for index {index} in the provided URL list.")





validate_config()

def convert_unix_to_readable(unix_timestamp):
    return datetime.utcfromtimestamp(unix_timestamp).strftime('%Y-%m-%d %H:%M:%S')



# App Initialization
address_indexer_app = Quart(__name__)
# Enable CORS
address_indexer_app = cors(address_indexer_app, allow_origin="*")
async_connection_pool = None
scheduler = AsyncIOScheduler()
max_concurrent_tasks = None
last_stable_pool_size = 5

#DATABASE SECTION STARTS


async def get_dynamic_pool_size():
    """Dynamically determine the optimal pool size while preventing CPU overload."""
    global last_stable_pool_size

    cpu_usage = psutil.cpu_percent(interval=1)

    # Define min and max pool size
    MIN_POOL_SIZE = 3    # Start conservatively 
    MAX_POOL_SIZE = 20  # Maximum allowed pool size
    SAFE_CPU_LIMIT = 80  # CPU threshold to prevent overload

    # Adjust pool size dynamically based on CPU usage
    if cpu_usage < 30:
        new_pool_size = min(last_stable_pool_size + 5, MAX_POOL_SIZE)  # Increase cautiously
    elif 30 <= cpu_usage <= SAFE_CPU_LIMIT:
        new_pool_size = last_stable_pool_size  # Maintain stable size
    else:
        new_pool_size = max(last_stable_pool_size - 5, MIN_POOL_SIZE)  # Reduce to prevent overload

    # Update stable size if it's within safe CPU limits
    if cpu_usage < SAFE_CPU_LIMIT:
        last_stable_pool_size = new_pool_size

    return new_pool_size


async def initialize_connection_pool():
    """Initialize the connection pool with dynamic port selection."""
    global async_connection_pool, max_concurrent_tasks

    maxsize = 20  # Default maxsize for the pool
    ports_to_try = [3306, 3307]  # List of ports to attempt
    successful_port = None  # To store the port that works

    for port in ports_to_try:
        try:
            maxsize = await get_dynamic_pool_size()
            async_connection_pool = await aiomysql.create_pool(
                host=MYSQL_HOST,
                user=MYSQL_USER,
                port=port,
                password=MYSQL_PASSWORD,
                db=None,
                charset="utf8mb4",
                maxsize=maxsize,
                minsize=1,
                loop=asyncio.get_event_loop(),
            )
            successful_port = port
            logger.info(f"Connection pool initialized on port {port} with maxsize={maxsize}.")
            break  # Exit the loop as soon as a connection is successful
        except Exception as e:
            logger.warning(f"Failed to connect to MySQL on port {port}: {e}")

    if successful_port is None:
        logger.error("Failed to initialize connection pool on all tried ports.")
        raise RuntimeError("Could not connect to MySQL on any of the specified ports.")

    max_concurrent_tasks = max(1, maxsize - 5)  # Ensure at least 1 task can run
    logger.info(f"Max concurrent tasks set to {max_concurrent_tasks}.")


async def get_mysql_connection():
    if async_connection_pool is None:
        raise RuntimeError("Connection pool not initialized")
    return await async_connection_pool.acquire()


async def adjust_pool_size():
    """Periodically adjust the pool size based on CPU utilization."""
    global async_connection_pool

    if async_connection_pool is not None:
        new_maxsize = await get_dynamic_pool_size()
        async_connection_pool.maxsize = new_maxsize
        logger.info(f"Adjusted connection pool size to {new_maxsize} based on CPU utilization.")

# Schedule periodic adjustments every 10 seconds
scheduler.add_job(adjust_pool_size, "interval", seconds=200)
scheduler.start()    


async def initialize_database():
    create_transactions_table_query = f"""
        CREATE TABLE IF NOT EXISTS transactions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            txid VARCHAR(255) UNIQUE,  -- Add UNIQUE to automatically index txid
            blockhash TEXT,
            blockheight INT,
            time TEXT,
            valueOut TEXT,
            vin_addresses TEXT,
            vout_addresses TEXT,
            raw_transaction_json LONGTEXT,
            floData TEXT,
            UNIQUE KEY idx_txid (txid)  
        );
        """

    create_processed_blocks_table_query = """
        CREATE TABLE IF NOT EXISTS processed_blocks (
            block_height INT PRIMARY KEY,
            block_hash VARCHAR(255),
            processed_at DATETIME NOT NULL,
            UNIQUE KEY idx_block_hash (block_hash)  
        );
        """

    create_addresses_table_query = """
        CREATE TABLE IF NOT EXISTS addresses (
            id INT AUTO_INCREMENT PRIMARY KEY,
            flo_address VARCHAR(255) UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY idx_flo_address (flo_address)  
        );
        """

    create_address_to_tx_table_query = """
        CREATE TABLE IF NOT EXISTS address_to_tx (
            address VARCHAR(255),
            txid VARCHAR(255),
            PRIMARY KEY (address, txid),
            INDEX idx_address (address),  
            INDEX idx_txid (txid)  
        );
        """

    create_balance_data_table_query = """
        CREATE TABLE IF NOT EXISTS balance_data (
            id INT AUTO_INCREMENT PRIMARY KEY,
            flo_address VARCHAR(255) UNIQUE,
            balance DOUBLE,
            balance_sat BIGINT,
            total_received DOUBLE,
            total_received_sat BIGINT,
            total_sent DOUBLE,
            total_sent_sat BIGINT,
            last_computed_blockheight BIGINT,
            last_computed_blockhash VARCHAR(255),
            last_computed_blocktime DATETIME,
            appeared_in_coinbase BOOLEAN DEFAULT FALSE, -- New field to indicate coinbase appearance
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        );
        """

    create_utxos_table_query = """
        CREATE TABLE IF NOT EXISTS utxos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            txid VARCHAR(255) NOT NULL,
            vout INT NOT NULL,
            address VARCHAR(255) NOT NULL,
            amount DOUBLE NOT NULL,
            satoshis BIGINT NOT NULL,
            block_height INT NOT NULL,
            spent BOOLEAN DEFAULT FALSE,
            spent_at_block INT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY (txid, vout)
        );

        """



    conn = await get_mysql_connection()
    try:
        async with conn.cursor() as cursor:
            logger.info(f"Initializing database: {ADDRESS_INDEXER_DB_NAME}")
            
            # Create the database if it doesn't exist
            await cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{ADDRESS_INDEXER_DB_NAME}`")
            
            # Select the database context
            await cursor.execute(f"USE `{ADDRESS_INDEXER_DB_NAME}`")
            
            # Create the tables
            await cursor.execute(create_transactions_table_query)
            await cursor.execute(create_processed_blocks_table_query)
            await cursor.execute(create_addresses_table_query)
            await cursor.execute(create_address_to_tx_table_query)
            await cursor.execute(create_balance_data_table_query)
            await cursor.execute(create_utxos_table_query)

            # Index for transactions.blockheight
            await cursor.execute("""
                SELECT COUNT(1)
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'transactions' AND INDEX_NAME = 'idx_blockheight';
            """, (ADDRESS_INDEXER_DB_NAME,))
            index_exists = await cursor.fetchone()
            if index_exists[0] == 0:
                await cursor.execute("CREATE INDEX idx_blockheight ON transactions (blockheight);")
                logger.info("Index idx_blockheight created.")
            else:
                logger.info("Index idx_blockheight already exists.")

            # Add index for address_to_tx.address if not exists
            await cursor.execute("""
            SELECT COUNT(1)
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'address_to_tx' AND INDEX_NAME = 'idx_address';
            """, (ADDRESS_INDEXER_DB_NAME,))
            index_exists = await cursor.fetchone()
            if index_exists[0] == 0:
                await cursor.execute("CREATE INDEX idx_address ON address_to_tx (address);")

            # Add index for address_to_tx.txid if not exists
            await cursor.execute("""
            SELECT COUNT(1)
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'address_to_tx' AND INDEX_NAME = 'idx_txid';
            """, (ADDRESS_INDEXER_DB_NAME,))
            index_exists = await cursor.fetchone()
            if index_exists[0] == 0:
                await cursor.execute("CREATE INDEX idx_txid ON address_to_tx (txid);")

            await conn.commit()
            logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")
        raise
    finally:
        try:
            async_connection_pool.release(conn)
        except Exception as release_error:
            logger.error(f"Failed to release connection during database initialization: {release_error}")


#DATABASE SECTION ENDS


# BLOCKBOOK BLOCK ADJUSTMENT FUNCTIONS START 

# Function to fetch block data directly from flod RPC
async def b_fetch_block_data_flod(blockhash):
    auth = aiohttp.BasicAuth(RPC_USER, RPC_PASSWORD)
    payload = {
        "jsonrpc": "1.0",
        "id": "block_query",
        "method": "getblock",
        "params": [blockhash, 2]
    }

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(RPC_ADDRESS, json=payload) as response:
            raw = await response.read()
            text = raw.decode("utf-8", errors="replace")  # Replace invalid UTF-8 bytes with �
            return json.loads(text)


# Function to fetch transaction data by txid
async def b_fetch_transaction(txid):
    auth = aiohttp.BasicAuth(RPC_USER, RPC_PASSWORD)
    payload = {
        "jsonrpc": "1.0",
        "id": "tx_query",
        "method": "getrawtransaction",
        "params": [txid, 1]
    }

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(RPC_ADDRESS, json=payload) as response:
            raw = await response.read()
            text = raw.decode("utf-8", errors="replace")
            return json.loads(text)


# Function to compute VIN values
async def b_compute_vin_values(vin):
    for vin_entry in vin:
        if "txid" in vin_entry:
            tx_data = await b_fetch_transaction(vin_entry["txid"])
            if tx_data.get("result") and len(tx_data["result"]["vout"]) > vin_entry["vout"]:
                vout = tx_data["result"]["vout"][vin_entry["vout"]]
                vin_entry["value"] = vout["value"]
                vin_entry["valueSat"] = math.floor(vout["value"] * 1e8)

                # Extract addresses and isAddress from scriptPubKey
                script_pub_key = vout.get("scriptPubKey", {})
                vin_entry["addresses"] = script_pub_key.get("addresses", [])
                vin_entry["isAddress"] = bool(script_pub_key.get("addresses"))
            else:
                vin_entry["value"] = 0
                vin_entry["valueSat"] = 0
                vin_entry["addresses"] = []
                vin_entry["isAddress"] = False
    return vin

# Asynchronous function to compute transaction-level fields
async def b_compute_transaction_values(tx, blockhash, blockheight):
    tx["blockhash"] = blockhash  # Assign blockhash for the transaction
    tx["blockheight"] = blockheight  # Assign blockheight for the transaction

    # Check if the transaction is coinbase
    is_coinbase = all('txid' not in vin for vin in tx["vin"])

    # Compute valueIn and valueOut
    value_in = sum(vin.get("value", 0) for vin in tx["vin"])
    value_out = sum(vout["value"] for vout in tx.get("vout", []))

    tx["valueIn"] = value_in
    tx["valueInSat"] = math.floor(value_in * 1e8)
    tx["valueOut"] = value_out
    tx["valueOutSat"] = math.floor(value_out * 1e8)

    # Compute fees
    if is_coinbase:
        tx["fees"] = 0
        tx["feesSat"] = 0
    else:
        fees = value_in - value_out
        tx["fees"] = max(0, round(fees, 8))
        tx["feesSat"] = max(0, tx["valueInSat"] - tx["valueOutSat"])

# Asynchronous function to add valueSat and isAddress to each vout entry
async def b_compute_vout_values(tx):
    for vout in tx.get("vout", []):
        vout["valueSat"] = math.floor(vout["value"] * 1e8)
        script_pub_key = vout.get("scriptPubKey", {})
        vout["addresses"] = script_pub_key.get("addresses", [])
        vout["isAddress"] = bool(script_pub_key.get("addresses"))


# Function to process transactions
async def b_process_transactions(transactions, blockhash, blockheight):
    processed_txs = []
    for tx in transactions:
        # Compute VIN values
        tx["vin"] = await b_compute_vin_values(tx["vin"])
        await b_compute_vout_values(tx)
        await b_compute_transaction_values(tx, blockhash, blockheight)
        processed_txs.append(tx)
    return processed_txs


async def b_process_block(blockhash):
    try:
        # Fetch block data directly from flod RPC
        block_data = await b_fetch_block_data_flod(blockhash)

        if block_data.get("error") or not block_data.get("result"):
            return {"error": f"Error fetching block data: {block_data.get('error')}"}

        block_result = block_data["result"]
        blockheight = block_result["height"]

        # Process transactions
        processed_transactions = await b_process_transactions(
            block_result["tx"],
            block_result["hash"],
            blockheight
        )

        # Calculate pagination-related fields
        tx_count = len(processed_transactions)
        items_on_page = 1000
        total_pages = math.ceil(tx_count / items_on_page)
        current_page = 1

        # Prepare the formatted block data with ordered fields
        output = OrderedDict([
            ("page", current_page),
            ("totalPages", total_pages),
            ("itemsOnPage", items_on_page),
            ("hash", block_result["hash"]),
            ("previousBlockHash", block_result.get("previousblockhash")),
            ("nextBlockHash", block_result.get("nextblockhash")),
            ("height", blockheight),
            ("confirmations", block_result.get("confirmations", 0)),
            ("size", block_result["size"]),
            ("time", block_result["time"]),
            ("version", block_result.get("version", 0)),
            ("merkleRoot", block_result["merkleroot"]),
            ("nonce", block_result["nonce"]),
            ("bits", block_result.get("bits", "")),
            ("difficulty", block_result["difficulty"]),
            ("txCount", tx_count),
            ("txs", processed_transactions),
        ])

        return output

    except Exception as e:
        logger.error(f"Error in b_process_block for block {blockhash}: {e}", exc_info=True)
        return {"error": f"Exception while processing block {blockhash}: {str(e)}"}


# BLOCKBOOK BLOCK ADJUSTMENT FUNCTIONS END


# VALIDATION FUNCTIONS
def is_valid_flo_address(address):
    """Validate FLO address, including multisig formats."""
    return re.match(r"^[Fe][a-zA-Z0-9]{33}$", address) is not None

def is_valid_hash(hash_value):
    """Validate transaction or block hash."""
    return re.match(r"^[a-fA-F0-9]{64}$", hash_value) is not None

def is_valid_block_height(block_height):
    # Check if block_height is a valid non-negative integer
    return isinstance(block_height, int) and block_height >= 0



#ADDRESS PROCESING SECTION STARTS

async def get_blockhash(block_height):
    global aiohttp_session

    payload = {
        "method": "getblockhash",
        "params": [block_height],
        "id": 1
    }

    if aiohttp_session is None:
        aiohttp_session = aiohttp.ClientSession()

    try:
        async with aiohttp_session.post(
            RPC_ADDRESS,
            json=payload,
            auth=aiohttp.BasicAuth(RPC_USER, RPC_PASSWORD),
            timeout=RPC_TIMEOUT
        ) as response:
            if response.status == 200:
                result = await response.json()
                return result.get("result")
            else:
                error_text = await response.text()
                raise Exception(f"Error {response.status}: {error_text}")
    except Exception as e:
        logger.error(f"Error in get_blockhash({block_height}): {e}")
        raise


async def process_address_externalsource(flo_address):
    """
    Process and store transactions for a specific FLO address using external sources with support for dynamic pagination.
    """
    global aiohttp_session
    current_index = 0
    logger.info(f"Starting transaction processing for FLO address: {flo_address}")

    if aiohttp_session is None:
        aiohttp_session = aiohttp.ClientSession()

    while current_index < len(ADDRESS_INDEXER_URLS):
        address_api_url = f"{get_indexer_url(current_index)}api/addr/{flo_address}"

        try:
            # Step 1: Fetch the metadata (e.g., txApperances) for the address
            logger.info(f"Fetching metadata for address: {flo_address}")
            async with aiohttp_session.get(address_api_url, timeout=API_TIMEOUT) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch metadata for {flo_address} (Status: {response.status}). Moving to next URL.")
                    current_index += 1
                    continue
                metadata = await response.json()

            total_tx_apperances = metadata.get("txApperances", 0)
            if total_tx_apperances == 0:
                logger.info(f"No transactions found for address: {flo_address}.")
                return {"message": f"No transactions to process for {flo_address}."}

            logger.info(f"Total transactions for {flo_address}: {total_tx_apperances}")

            # Step 2: Fetch stored transaction IDs
            conn = await get_mysql_connection()
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(f"USE `{ADDRESS_INDEXER_DB_NAME}`")
                    stored_txids_query = """
                    SELECT txid
                    FROM address_to_tx
                    WHERE address = %s;
                    """
                    await cursor.execute(stored_txids_query, (flo_address,))
                    stored_txids = {row[0] for row in await cursor.fetchall()}
                logger.info(f"Fetched {len(stored_txids)} stored transactions for address: {flo_address}")
            finally:
                async_connection_pool.release(conn)

            # Step 3: Fetch transactions in batches
            from_index = 0
            batch_size = 100
            total_transactions_fetched = 0
            all_new_txids = []

            while from_index < total_tx_apperances:
                paginated_url = f"{address_api_url}?from={from_index}&to={from_index + batch_size}"
                try:
                    logger.info(f"Fetching address data from {paginated_url}")
                    async with aiohttp_session.get(paginated_url, timeout=API_TIMEOUT) as response:
                        if response.status != 200:
                            logger.error(f"Failed to fetch paginated data for {flo_address} (Status: {response.status}). Breaking pagination.")
                            break
                        address_data = await response.json()

                    transactions = address_data.get("transactions", [])
                    if not transactions:
                        logger.info(f"No more transactions to fetch for address: {flo_address}.")
                        break

                    new_txids = [txid for txid in transactions if txid not in stored_txids]
                    all_new_txids.extend(new_txids)
                    logger.info(f"Fetched {len(transactions)} transactions (new: {len(new_txids)}) from {paginated_url}.")

                    total_transactions_fetched += len(transactions)
                    from_index += batch_size
                except asyncio.TimeoutError:
                    logger.error(f"Timeout while fetching {paginated_url}. Skipping to the next batch.")
                    from_index += batch_size
                    continue
                except Exception as e:
                    logger.error(f"Error during paginated fetch for {paginated_url}: {e}")
                    break

            # Step 4: Process new transactions
            for txid in all_new_txids:
                try:
                    transaction_data_url = f"{SELF_URL}/api/tx/{txid}"
                    async with aiohttp_session.get(transaction_data_url, timeout=API_TIMEOUT) as response:
                        if response.status != 200:
                            logger.error(f"Failed to fetch transaction data for {txid}. Skipping.")
                            continue
                        transaction_data = await response.json()

                    logger.info(f"Transaction data fetched successfully for {txid}")
                    await store_transaction_data(transaction_data)
                    logger.info(f"Transaction {txid} stored successfully.")
                except Exception as e:
                    logger.error(f"Error processing transaction {txid}: {e}")

            logger.info(f"Completed processing for FLO address: {flo_address}")
            return {"message": f"Transactions for {flo_address} processed successfully."}

        except Exception as e:
            logger.error(f"Error fetching address data for {flo_address} using {get_indexer_url(current_index)}: {e}")
            current_index += 1
            logger.info(f"Switching to next URL (Index: {current_index})")

    logger.error(f"All URLs failed for address: {flo_address}")
    return {"error": "All URLs failed. Could not process address transactions."}



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
                "scriptPubKey": {
                    "addresses": addresses
                }
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
        return raw_json_str  # fallback


async def store_transaction_data(transaction_data):
    """
    Store transaction data in the database, update the address-to-transaction mapping,
    and manage UTXOs for the transaction.
    """
    conn = await get_mysql_connection()
    transaction_query = f"""
    INSERT INTO `{ADDRESS_INDEXER_DB_NAME}`.transactions (
        txid, blockhash, blockheight, time, valueOut, vin_addresses, vout_addresses, raw_transaction_json, floData
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    mapping_query = f"""
    INSERT IGNORE INTO `{ADDRESS_INDEXER_DB_NAME}`.address_to_tx (address, txid)
    VALUES (%s, %s)
    """
    utxo_insert_query = f"""
    INSERT INTO `{ADDRESS_INDEXER_DB_NAME}`.utxos (
        txid, vout, address, amount, satoshis, block_height, spent, created_at, updated_at
    ) VALUES (%s, %s, %s, %s, %s, %s, FALSE, NOW(), NOW())
    """
    utxo_delete_query = f"""
    DELETE FROM `{ADDRESS_INDEXER_DB_NAME}`.utxos
    WHERE txid = %s AND vout = %s
    """

    try:
        async with conn.cursor() as cursor:
            # Select the correct database context
            await cursor.execute(f"USE `{ADDRESS_INDEXER_DB_NAME}`")

            # Extract transaction details
            vin_addresses = []
            spent_utxos = []  # Track inputs (spent UTXOs)
            for vin in transaction_data.get("vin", []):
                if "addresses" in vin:
                    vin_addresses.extend(vin["addresses"])  # Collect all addresses from vin
                if "txid" in vin and "vout" in vin:
                    spent_utxos.append((vin["txid"], vin["vout"]))

            vout_addresses = []
            new_utxos = []  # Track outputs (new UTXOs)
            for index, vout in enumerate(transaction_data.get("vout", [])):
                if "scriptPubKey" in vout and "addresses" in vout["scriptPubKey"]:
                    addresses = vout["scriptPubKey"]["addresses"]
                    vout_addresses.extend(addresses)
                    value = Decimal(vout["value"]) * Decimal(1e8)
                    for address in addresses:
                        new_utxos.append((transaction_data["txid"], index, address, vout["value"], int(value), None))  # Blockheight is None initially

            raw_transaction_json = slim_raw_transaction_json(json.dumps(transaction_data))
            flo_data = transaction_data.get("floData", None)
            blockhash = transaction_data.get("blockhash")
            blockheight = transaction_data.get("blockheight")

            # Fetch blockheight if missing
            if blockheight is None and blockhash:
                try:
                    rpc_response = await rpc_request("getblock", [blockhash])
                    blockheight = rpc_response.get("result", {}).get("height")
                    if blockheight is None:
                        raise ValueError(f"Could not fetch blockheight for blockhash {blockhash}")
                except Exception as e:
                    logger.error(f"Error fetching block height for blockhash {blockhash}: {e}")
                    raise

            # Validate critical fields
            txid = transaction_data.get("txid")
            if not txid or not blockhash or blockheight is None:
                logger.error(f"Invalid transaction data: {transaction_data}")
                raise ValueError("Transaction data is missing required fields")

            # Insert the transaction into the `transactions` table
            await cursor.execute(transaction_query, (
                txid,
                blockhash,
                blockheight,
                transaction_data.get("time"),
                transaction_data.get("valueOut"),
                ", ".join(vin_addresses),
                ", ".join(vout_addresses),
                raw_transaction_json,
                flo_data,
            ))
            logger.info(f"Transaction {txid} stored in the database.")

            # Insert address-to-tx mappings
            mappings = [(address, txid) for address in set(vin_addresses + vout_addresses)]
            for address, txid in mappings:
                await cursor.execute(mapping_query, (address, txid))
                logger.info(f"Mapped address {address} to transaction {txid}.")

            # Update UTXOs
            # Delete spent UTXOs
            for spent_txid, spent_vout in spent_utxos:
                await cursor.execute(utxo_delete_query, (spent_txid, spent_vout))
                logger.info(f"Deleted spent UTXO: txid={spent_txid}, vout={spent_vout}.")

            # Insert new UTXOs
            for utxo in new_utxos:
                utxo_with_blockheight = (*utxo[:-1], blockheight)  # Add blockheight to the tuple
                await cursor.execute(utxo_insert_query, utxo_with_blockheight)
                logger.info(f"Inserted new UTXO: {utxo_with_blockheight}")

            # Commit changes
            await conn.commit()

    except Exception as e:
        logger.error(f"Error storing transaction {transaction_data.get('txid', 'unknown')}: {e}")
    finally:
        try:
            async_connection_pool.release(conn)
        except Exception as release_error:
            logger.error(f"Failed to release connection: {release_error}")



async def get_last_processed_block():
    conn = await get_mysql_connection()
    try:
        async with conn.cursor() as cursor:
            # Select the correct database context
            await cursor.execute(f"USE `{ADDRESS_INDEXER_DB_NAME}`")
            
            # Query to get the last processed block
            await cursor.execute("SELECT MAX(block_height) FROM processed_blocks")
            result = await cursor.fetchone()
            return result[0] if result and result[0] else 0
    except Exception as e:
        logger.error(f"Error fetching last processed block: {e}")
        return 0  # Return a default value in case of error
    finally:
        try:
            async_connection_pool.release(conn)
        except Exception as e:
            logger.error(f"Failed to release connection: {e}")


async def process_blocks(start_block, end_block, batch_size=200, max_concurrent_batches=5, retries=5):
    """Process blocks from start_block to end_block with batching, deduplication, retries, and concurrency."""
    # Constants for dynamic concurrency control
    BASE_CONCURRENT_BATCHES = 5  # Default concurrency level
    MAX_CONCURRENT_BATCHES = 20   # Upper limit
    MIN_CONCURRENT_BATCHES = 2    # Lower limit
    HIGH_CPU_THRESHOLD = 80       # Reduce concurrency if CPU usage is above this
    LOW_CPU_THRESHOLD = 40  

    semaphore = asyncio.Semaphore(max_concurrent_batches)

    def adjust_concurrency():
        """Dynamically adjust concurrency based on CPU usage."""
        global semaphore
        cpu_usage = psutil.cpu_percent(interval=1)

        if cpu_usage > HIGH_CPU_THRESHOLD:
            new_limit = max(MIN_CONCURRENT_BATCHES, semaphore._value - 2)
        elif cpu_usage < LOW_CPU_THRESHOLD:
            new_limit = min(MAX_CONCURRENT_BATCHES, semaphore._value + 2)
        else:
            new_limit = semaphore._value  # Keep it unchanged

        # Update semaphore value dynamically
        semaphore = asyncio.Semaphore(new_limit)
        logger.info(f"Adjusted concurrency to {new_limit} batches (CPU Usage: {cpu_usage}%)")


    async def add_addresses_if_new(cursor, addresses):
        """Add multiple addresses to the `addresses` table in a batch with retries."""
        query = "INSERT IGNORE INTO addresses (flo_address) VALUES (%s)"
        for attempt in range(retries):
            try:
                await cursor.executemany(query, [(address,) for address in addresses])
                logger.info(f"Successfully added addresses: {list(addresses)[:5]}..." if len(addresses) > 5 else f"Successfully added addresses: {list(addresses)}")
                return
            except Exception as e:
                if "Deadlock" in str(e):
                    logger.warning(f"Deadlock detected while adding addresses. Retrying ({attempt + 1}/{retries})...")
                else:
                    logger.error(f"Error adding addresses {list(addresses)[:5]}: {e}")
                    raise
                await asyncio.sleep(0.5)

    async def add_address_tx_mapping(cursor, address, txid):
        """Add a mapping between address and txid with retry logic."""
        logger.info(f"Mapping address {address} to transaction {txid}")
        for attempt in range(retries):
            try:
                await cursor.execute(
                    "INSERT IGNORE INTO address_to_tx (address, txid) VALUES (%s, %s)", (address, txid)
                )
                logger.info(f"Successfully mapped address {address} to transaction {txid}")
                return
            except Exception as e:
                if "Deadlock" in str(e):
                    logger.warning(f"Deadlock detected for {address} and {txid}. Retrying ({attempt + 1}/{retries})...")
                else:
                    logger.error(f"Error adding address-to-tx mapping for {address} and {txid}: {e}")
                    raise
                await asyncio.sleep(0.5)
        logger.error(f"Failed to add address-to-tx mapping for {address} and {txid} after {retries} retries.")

    async def process_transaction(cursor, tx, blockhash, block_height, block_time):
        """Process a single transaction, adding it to the database, mappings, and UTXOs."""
        txid = tx["txid"]
        vin_addresses = []

        # Process `vin` to fetch input addresses and mark UTXOs as spent
        for vin in tx.get("vin", []):
            try:
                if "txid" in vin and "vout" in vin:
                    prev_txid = vin["txid"]
                    vout_index = vin["vout"]

                    # Fetch the previous transaction
                    prev_tx_response = await rpc_request("getrawtransaction", [prev_txid, True])
                    if "result" in prev_tx_response:
                        prev_tx = prev_tx_response["result"]
                        prev_vout = prev_tx["vout"][vout_index]

                        # Get input addresses from the previous transaction
                        vin_addresses.extend(prev_vout["scriptPubKey"].get("addresses", []))

                        # DELETE the UTXO instead of marking it as spent
                        await cursor.execute(
                            """
                            DELETE FROM utxos
                            WHERE txid = %s AND vout = %s
                            """,
                            (prev_txid, vout_index),
                        )
                        logger.info(f"Deleted UTXO: txid={prev_txid}, vout={vout_index}")
            except Exception as e:
                logger.error(f"Error processing vin: {vin}, error: {e}")

        # Process `vout` to fetch output addresses and insert new UTXOs
        vout_addresses = []
        for vout_index, vout in enumerate(tx.get("vout", [])):
            try:
                if "scriptPubKey" in vout and "addresses" in vout["scriptPubKey"]:
                    address = vout["scriptPubKey"]["addresses"][0]
                    vout_addresses.append(address)

                    # Insert the new UTXO
                    await cursor.execute(
                        f"""
                        INSERT INTO `{ADDRESS_INDEXER_DB_NAME}`.utxos (
                            txid, vout, address, amount, satoshis, block_height, spent
                        ) VALUES (%s, %s, %s, %s, %s, %s, FALSE)
                        """,
                        (
                            txid,
                            vout_index,
                            address,
                            vout.get("value", 0),
                            int(vout.get("value", 0) * 1e8),  # Convert BTC to satoshis
                            block_height,
                        )
                    )
                    logger.info(f"Inserted new UTXO: address={address}, txid={txid}, vout={vout_index}")
            except Exception as e:
                logger.error(f"Error processing vout: {vout}, error: {e}")

        # Prepare vin and vout address strings for the transaction table
        vin_addresses_str = ", ".join(vin_addresses)
        vout_addresses_str = ", ".join(vout_addresses)

        # Add addresses and mappings
        logger.debug(f"Transaction {txid}: vin_addresses={vin_addresses}, vout_addresses={vout_addresses}")
        await add_addresses_if_new(cursor, set(vin_addresses + vout_addresses))
        for address in set(vin_addresses + vout_addresses):
            await add_address_tx_mapping(cursor, address, txid)

        # Insert the transaction into the `transactions` table
        transaction_time = block_time  # Always use block time as the transaction time
        query = f"""
        INSERT INTO `{ADDRESS_INDEXER_DB_NAME}`.transactions (
            txid, blockhash, blockheight, time, valueOut, vin_addresses, vout_addresses, raw_transaction_json, floData
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        flo_data = tx.get("floData", None)
        raw_transaction_json = slim_raw_transaction_json(json.dumps(tx))


        try:
            await cursor.execute(
                query,
                (
                    txid,
                    blockhash,
                    block_height,
                    transaction_time,
                    tx.get("valueOut", None),
                    vin_addresses_str,
                    vout_addresses_str,
                    raw_transaction_json,
                    flo_data,
                ),
            )
            logger.info(f"Transaction {txid} stored in the database.")
        except Exception as e:
            logger.error(f"Error inserting transaction {txid}: {e}")



    async def process_single_block(block_height):
        """Process a single block while respecting the semaphore limit."""
        global aiohttp_session
        async with semaphore:
            conn = await get_mysql_connection()
            try:
                async with conn.cursor() as cursor:
                    # Select the correct database context
                    await cursor.execute(f"USE `{ADDRESS_INDEXER_DB_NAME}`")
                    logger.info(f"Processing block {block_height}")

                    # Get block hash
                    blockhash_response = await rpc_request("getblockhash", [block_height])
                    if "result" not in blockhash_response:
                        logger.error(f"Block number {block_height} not found.")
                        return
                    blockhash = blockhash_response["result"]

                    # Reuse global aiohttp session if available
                    if aiohttp_session is None:
                        aiohttp_session = aiohttp.ClientSession()

                    # Get block data
                    block_data_url = f"{SELF_URL}/api/block/{blockhash}"
                    async with aiohttp_session.get(block_data_url, timeout=API_TIMEOUT) as response:
                        if response.status != 200:
                            logger.error(f"Failed to fetch block data for block number {block_height}.")
                            return
                        block_data = await response.json()

                    # Extract block time
                    block_time = block_data.get("time")
                    if not block_time:
                        logger.error(f"Block {block_height} does not contain a valid time.")
                        return

                    # Fetch existing transactions for the block
                    await cursor.execute(
                        "SELECT txid FROM transactions WHERE blockheight = %s",
                        (block_height,),
                    )
                    existing_txids = {row[0] for row in await cursor.fetchall()}
                    logger.info(f"Existing transactions for block {block_height}: {list(existing_txids)}")

                    # Process transactions in the block
                    for tx in block_data.get("txs", []):
                        txid = tx["txid"]
                        if txid in existing_txids:
                            logger.info(f"Transaction {txid} already exists. Rechecking mappings.")
                        else:
                            logger.info(f"Transaction {txid} missing. Adding to the database.")
                            await process_transaction(cursor, tx, blockhash, block_height, block_time)

                    # Mark block as processed with block_time
                    await cursor.execute(
                        """
                        INSERT INTO processed_blocks (block_height, block_hash, processed_at)
                        VALUES (%s, %s, FROM_UNIXTIME(%s)) AS new
                        ON DUPLICATE KEY UPDATE 
                            block_hash = new.block_hash,
                            processed_at = new.processed_at
                        """,
                        (block_height, blockhash, block_time),
                    )

                    await conn.commit()
                    logger.info(f"Block {block_height} processed successfully.")
            except Exception as e:
                logger.error(f"Error processing block {block_height}: {e}")
            finally:
                try:
                    async_connection_pool.release(conn)
                except Exception as release_error:
                    logger.error(f"Failed to release connection for block {block_height}: {release_error}")



    # Process blocks in batches
    try:
        for batch_start in range(start_block, end_block + 1, batch_size):
            batch_end = min(batch_start + batch_size - 1, end_block)
            batch_tasks = [
                process_single_block(block_height) for block_height in range(batch_start, batch_end + 1)
            ]
            logger.info(f"Processing batch {batch_start} to {batch_end} ({batch_start - start_block + 1}/{end_block - start_block + 1} blocks processed so far)")
            
            start_time = time.time()  # Track batch processing time
            
            # Process blocks concurrently, with exception handling
            results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            failed_blocks = []  # To track blocks that failed in this batch

            for block_height, result in zip(range(batch_start, batch_end + 1), results):
                if isinstance(result, Exception):
                    logger.error(f"Error processing block {block_height}: {result}")
                    failed_blocks.append(block_height)  # Add to retry list
                else:
                    logger.info(f"Successfully processed block {block_height}")
            
            # Retry failed blocks
            for retry_attempt in range(1, retries + 1):
                if not failed_blocks:
                    break  # No blocks to retry
                
                logger.info(f"Retrying failed blocks: {failed_blocks} (attempt {retry_attempt}/{retries})")
                retry_tasks = [process_single_block(block_height) for block_height in failed_blocks]
                retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)

                # Update failed_blocks with any blocks that still fail
                failed_blocks = [
                    block_height for block_height, result in zip(failed_blocks, retry_results)
                    if isinstance(result, Exception)
                ]

            if failed_blocks:
                logger.error(f"Failed to process blocks after {retries} retries: {failed_blocks}")

            logger.info(f"Successfully processed batch {batch_start} to {batch_end} in {time.time() - start_time:.2f} seconds")
        logger.info(f"Processed blocks {start_block} to {end_block}.")
    except Exception as e:
        logger.error(f"Error processing blocks {start_block} to {end_block}: {e}")


async def find_and_process_missing_blocks(batch_size=1, max_concurrent_batches=1):
    try:
        # Temporarily override globals to expand cleaner timeouts so that large missing blocks can be processed
        with override_globals(
            CLEANUP_INTERVAL_SECONDS=5000,
            IDLE_TIME_THRESHOLD_TIMEOUT=5000,
            CLEANER_THREAD_EXIT_TIMEOUT=5000,
        ):
            logger.info("Global values temporarily overridden to 1000.")

            logger.info("Fetching latest block height from chain...")
            chain_info = await rpc_request("getblockchaininfo")
            if "result" not in chain_info:
                raise Exception(f"RPC error: {chain_info.get('error')}")
            latest_block = chain_info["result"]["blocks"]

            logger.info(f"Latest block on chain: {latest_block}")

            logger.info("Fetching processed blocks from database...")
            conn = await get_mysql_connection()
            async with conn.cursor() as cursor:
                await cursor.execute(f"USE `{ADDRESS_INDEXER_DB_NAME}`")
                await cursor.execute("SELECT block_height FROM processed_blocks")
                rows = await cursor.fetchall()
                processed_set = set(row[0] for row in rows)

            await async_connection_pool.release(conn)

            all_blocks = set(range(0, latest_block + 1))
            missing_blocks = sorted(all_blocks - processed_set)
            logger.info(f"Found {len(missing_blocks)} missing blocks.")

            if not missing_blocks:
                logger.info("No missing blocks found.")
                return

            from itertools import groupby
            from operator import itemgetter

            ranges = []
            for _, group in groupby(enumerate(missing_blocks), lambda x: x[1] - x[0]):
                block_range = list(map(itemgetter(1), group))
                ranges.append((block_range[0], block_range[-1]))

            logger.info(f"Preparing to process {len(ranges)} missing block ranges.")

            for start, end in ranges:
                logger.info(f"Processing missing range: {start} to {end}")
                await process_blocks(
                    start_block=start,
                    end_block=end,
                    batch_size=batch_size,
                    max_concurrent_batches=max_concurrent_batches
                )

            logger.info("Missing block processing complete.")

    except Exception as e:
        logger.error(f"Error during missing block recovery: {e}")





# API ROUTES START

    # Helper function to truncate satoshis to BTC
def truncate_satoshi_to_btc(satoshi):
    """Convert satoshi to BTC with truncation to 8 decimal places."""
    btc = Decimal(satoshi) / Decimal(1e8)
    return btc.quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)

async def process_all_transactions_for_coinbase(conn, flo_address, results, page=1, page_size=1000, from_scratch=False):
    """
    Process all transactions for the given FLO address when it appears in a coinbase transaction.
    Update UTXOs, store balance data, and return paginated transactions.
    Supports full recalculation (`from_scratch=True`) or incremental updates.
    """
    total_received = Decimal(0)
    total_sent = Decimal(0)
    balance = Decimal(0)
    transactions = []
    processed_blocks = set()

    # Define block API URL using SELF_URL
    block_api_url = f"{SELF_URL}/api/block"

    # Query to store updated balance data
    store_balance_query = """
    REPLACE INTO balance_data (
        flo_address, balance_sat, total_received_sat, total_sent_sat, last_computed_blockheight
    ) VALUES (%s, %s, %s, %s, %s);
    """

    # Queries for UTXO management
    insert_utxo_query = """
    INSERT INTO utxos (
        txid, vout, address, amount, satoshis, block_height, spent, created_at, updated_at
    ) VALUES (%s, %s, %s, %s, %s, %s, FALSE, NOW(), NOW())
    """
    delete_utxo_query = """
    DELETE FROM utxos
    WHERE txid = %s AND vout = %s
    """

    # Fetch last computed block height for incremental updates
    last_computed_blockheight = 0
    if not from_scratch:
        fetch_last_block_query = """
        SELECT last_computed_blockheight
        FROM balance_data
        WHERE flo_address = %s;
        """
        async with conn.cursor() as cursor:
            await cursor.execute(fetch_last_block_query, (flo_address,))
            row = await cursor.fetchone()
            if row and row["last_computed_blockheight"] is not None:
                last_computed_blockheight = row["last_computed_blockheight"]

    # Filter transactions based on block height (incremental or full processing)
    filtered_transactions = [
        tx for tx in results if from_scratch or tx["blockheight"] > last_computed_blockheight
    ]

    for transaction in filtered_transactions:
        try:
            raw_transaction = json.loads(transaction['raw_transaction_json'])

            if transaction.get('floData'):
                transaction['floData'] = transaction['floData'].replace('\\"', '"')

            # Check if the transaction is a coinbase transaction
            if not raw_transaction.get("vin"):
                blockhash = transaction["blockhash"]

                # Fetch full block data for accurate calculations
                if blockhash not in processed_blocks:
                    global aiohttp_session
                    if aiohttp_session is None:
                        aiohttp_session = aiohttp.ClientSession()

                    async with aiohttp_session.get(f"{block_api_url}/{blockhash}") as response:
                        if response.status != 200:
                            logger.error(f"Failed to fetch block data for {blockhash}")
                            continue

                        block_data = await response.json()


                    # Step 1: Calculate coinbase reward
                    coinbase_tx = block_data['txs'][0]  # First transaction is the coinbase transaction
                    for index, vout in enumerate(coinbase_tx.get("vout", [])):
                        addresses = vout.get("scriptPubKey", {}).get("addresses", [])
                        value = Decimal(vout.get("value", 0))

                        # Update UTXOs for each address
                        async with conn.cursor() as cursor:
                            for address in addresses:
                                if address == flo_address:
                                    await cursor.execute(
                                        insert_utxo_query,
                                        (
                                            coinbase_tx["txid"],
                                            index,
                                            address,
                                            value,
                                            int(value * Decimal(1e8)),  # Convert to satoshis
                                            transaction["blockheight"]
                                        )
                                    )
                                    total_received += value
                                    balance += value

                    # Step 2: Calculate total transaction fees
                    total_transaction_fees = Decimal(0)
                    for tx in block_data['txs']:
                        if 'vin' in tx and not tx['vin'][0].get("coinbase"):
                            tx_value_in = sum(Decimal(vin.get("value", 0)) for vin in tx.get("vin", []))
                            tx_value_out = sum(Decimal(vout.get("value", 0)) for vout in tx.get("vout", []))
                            total_transaction_fees += tx_value_in - tx_value_out

                    # Add transaction fees proportionally
                    for index, vout in enumerate(coinbase_tx.get("vout", [])):
                        addresses = vout.get("scriptPubKey", {}).get("addresses", [])
                        value = Decimal(vout.get("value", 0))
                        fee_share = total_transaction_fees * (value / sum(
                            Decimal(v["value"]) for v in coinbase_tx.get("vout", [])
                        ))
                        async with conn.cursor() as cursor:
                            for address in addresses:
                                if address == flo_address:
                                    await cursor.execute(
                                        insert_utxo_query,
                                        (
                                            coinbase_tx["txid"],
                                            index,
                                            address,
                                            fee_share,
                                            int(fee_share * Decimal(1e8)),
                                            transaction["blockheight"]
                                        )
                                    )
                                    total_received += fee_share
                                    balance += fee_share

                    # Mark block as processed
                    processed_blocks.add(blockhash)

            else:
                # Regular transaction: Compute received and sent amounts
                received = sum(
                    Decimal(vout.get("value", 0))
                    for vout in raw_transaction.get("vout", [])
                    if flo_address in vout.get("addresses", [])
                )
                sent = sum(
                    Decimal(vin.get("value", 0))
                    for vin in raw_transaction.get("vin", [])
                    if flo_address in vin.get("addresses", [])
                )

                # Remove spent UTXOs
                async with conn.cursor() as cursor:
                    for vin in raw_transaction.get("vin", []):
                        if "txid" in vin and "vout" in vin:
                            await cursor.execute(delete_utxo_query, (vin["txid"], vin["vout"]))
                            logger.info(f"Deleted spent UTXO: {vin['txid']}:{vin['vout']}")

                total_received += received
                total_sent += sent
                balance += received - sent

            # Add transaction to the list
            transactions.append(OrderedDict([
                ("txid", transaction["txid"]),
                ("version", raw_transaction.get("version")),
                ("locktime", raw_transaction.get("locktime")),
                ("vin", raw_transaction.get("vin", [])),
                ("vout", raw_transaction.get("vout", [])),
                ("blockhash", transaction["blockhash"]),
                ("blockheight", raw_transaction.get("blockheight")),
                ("confirmations", raw_transaction.get("confirmations", 0)),
                ("time", transaction.get("time")),
                ("valueIn", raw_transaction.get("valueIn", 0)),
                ("valueOut", raw_transaction.get("valueOut", 0)),
                ("fees", raw_transaction.get("fees", 0)),
                ("floData", transaction.get("floData", ""))
            ]))
        except Exception as e:
            logger.error(f"Error processing transaction {transaction.get('txid')}: {e}")
            continue

    # Implement pagination
    total_items = len(transactions)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    start_index = (page - 1) * page_size
    end_index = start_index + page_size

    paginated_transactions = transactions[start_index:end_index]

    # Store the updated balances
    async with conn.cursor() as cursor:
        await cursor.execute(store_balance_query, (
            flo_address,
            int(balance * Decimal(1e8)),  # Convert to satoshis
            int(total_received * Decimal(1e8)),  # Convert to satoshis
            int(total_sent * Decimal(1e8)),  # Convert to satoshis
            max(tx["blockheight"] for tx in transactions) if transactions else 0
        ))
        await conn.commit()

    # Prepare response data
    response_data = OrderedDict([
        ("page", page),
        ("totalPages", total_pages),
        ("itemsOnPage", len(paginated_transactions)),
        ("addrStr", flo_address),
        ("balance", float(truncate_satoshi_to_btc(balance))),
        ("balanceSat", int(balance * Decimal(1e8))),
        ("totalReceived", float(truncate_satoshi_to_btc(total_received))),
        ("totalReceivedSat", int(total_received * Decimal(1e8))),
        ("totalSent", float(truncate_satoshi_to_btc(total_sent))),
        ("totalSentSat", int(total_sent * Decimal(1e8))),
        ("unconfirmedBalance", 0),
        ("unconfirmedBalanceSat", 0),
        ("unconfirmedTxApperances", 0),
        ("txApperances", total_items),
        ("txs", paginated_transactions)
    ])
    return results, response_data



async def calculate_balance_and_update(conn, flo_address, from_scratch=False):
    """
    Calculate balances for the given FLO address and update stored data.
    If `from_scratch` is True, recalculate the balances from all transactions.
    """
    fetch_balance_query = """
    SELECT balance_sat, total_received_sat, total_sent_sat, last_computed_blockheight
    FROM balance_data
    WHERE flo_address = %s;
    """

    fetch_all_transactions_query = f"""
    SELECT t.*
    FROM `{ADDRESS_INDEXER_DB_NAME}`.transactions t
    JOIN `{ADDRESS_INDEXER_DB_NAME}`.address_to_tx at ON t.txid = at.txid
    WHERE at.address = %s;
    """

    new_transactions_query = f"""
    SELECT t.*
    FROM `{ADDRESS_INDEXER_DB_NAME}`.transactions t
    JOIN `{ADDRESS_INDEXER_DB_NAME}`.address_to_tx at ON t.txid = at.txid
    WHERE at.address = %s AND t.blockheight > %s;
    """

    store_balance_query = """
    REPLACE INTO balance_data (
        flo_address, balance_sat, total_received_sat, total_sent_sat, last_computed_blockheight
    ) VALUES (%s, %s, %s, %s, %s);
    """

    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Initialize balances
            balance_sat = Decimal(0)
            total_received_sat = Decimal(0)
            total_sent_sat = Decimal(0)
            last_computed_blockheight = 0

            if not from_scratch:
                # Fetch stored balances
                await cursor.execute(fetch_balance_query, (flo_address,))
                stored_data = await cursor.fetchone()

                if stored_data:
                    balance_sat = Decimal(stored_data['balance_sat'])
                    total_received_sat = Decimal(stored_data['total_received_sat'])
                    total_sent_sat = Decimal(stored_data['total_sent_sat'])
                    last_computed_blockheight = stored_data['last_computed_blockheight']

            # Fetch transactions
            if from_scratch:
                await cursor.execute(fetch_all_transactions_query, (flo_address,))
            else:
                await cursor.execute(new_transactions_query, (flo_address, last_computed_blockheight))

            transactions = await cursor.fetchall()

            # Process transactions
            for transaction in transactions:
                try:
                    raw_transaction = json.loads(transaction['raw_transaction_json'])

                    # Calculate received and sent amounts
                    received_sat = sum(
                        Decimal(vout.get("value", 0)) * Decimal(1e8)
                        for vout in raw_transaction.get("vout", [])
                        if flo_address in vout.get("addresses", [])
                    )
                    sent_sat = sum(
                        Decimal(vin.get("value", 0)) * Decimal(1e8)
                        for vin in raw_transaction.get("vin", [])
                        if flo_address in vin.get("addresses", [])
                    )

                    # Update totals
                    total_received_sat += received_sat
                    total_sent_sat += sent_sat
                    balance_sat += received_sat - sent_sat

                except Exception as e:
                    logger.error(f"Error processing transaction {transaction.get('txid')}: {e}")
                    continue

            # Determine the latest block height
            latest_block = max(tx["blockheight"] for tx in transactions) if transactions else last_computed_blockheight

            # Update stored balances
            await cursor.execute(store_balance_query, (
                flo_address,
                int(balance_sat),
                int(total_received_sat),
                int(total_sent_sat),
                latest_block
            ))
            await conn.commit()

            logger.info(f"Balance data for {flo_address} updated successfully.")
            return balance_sat, total_received_sat, total_sent_sat

    except Exception as e:
        logger.error(f"Error calculating and updating balance for FLO address {flo_address}: {e}")
        raise



async def fetch_and_calculate_address_data(conn, flo_address, page, page_size, details="txs"):
    """
    Fetch paginated transactions and calculate balances for the given FLO address.
    If details="basic", the `txs` key will not be included in the response.
    """
    # Queries
    count_query = """
    SELECT COUNT(*) as total
    FROM `transactions` t
    JOIN `address_to_tx` at ON t.txid = at.txid
    WHERE at.address = %s;
    """

    paginated_query = """
    SELECT t.*
    FROM `transactions` t
    JOIN `address_to_tx` at ON t.txid = at.txid
    WHERE at.address = %s
    ORDER BY t.blockheight DESC
    LIMIT %s OFFSET %s;
    """

    try:
        # Validate page and page_size
        if page < 1 or page_size < 1:
            raise ValueError("page and page_size must be positive integers.")

        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Calculate offset
            offset = (page - 1) * page_size
            
            # Use the database
            await cursor.execute(f"USE `{ADDRESS_INDEXER_DB_NAME}`")

            # Fetch total transaction count
            await cursor.execute(count_query, (flo_address,))
            total_count = (await cursor.fetchone())["total"]
            total_pages = max(1, (total_count + page_size - 1) // page_size)

            # Fetch paginated transactions
            await cursor.execute(paginated_query, (flo_address, page_size, offset))
            paginated_transactions = await cursor.fetchall()

            # Update balance
            balance_sat, total_received_sat, total_sent_sat = await calculate_balance_and_update(conn, flo_address)

            # Format transactions if details != "basic"
            transactions = []
            if details != "basic":
                for transaction in paginated_transactions:
                    try:
                        raw_transaction = json.loads(transaction['raw_transaction_json'])
                        transactions.append(OrderedDict([
                            ("txid", transaction["txid"]),
                            ("version", raw_transaction.get("version")),
                            ("locktime", raw_transaction.get("locktime")),
                            ("vin", raw_transaction.get("vin", [])),
                            ("vout", raw_transaction.get("vout", [])),
                            ("blockhash", transaction["blockhash"]),
                            ("blockheight", raw_transaction.get("blockheight")),
                            ("confirmations", raw_transaction.get("confirmations", 0)),
                            ("time", transaction.get("time")),
                            ("valueIn", raw_transaction.get("valueIn", 0)),
                            ("valueOut", raw_transaction.get("valueOut", 0)),
                            ("fees", raw_transaction.get("fees", 0)),
                            ("floData", transaction.get("floData", ""))
                        ]))
                    except Exception as e:
                        logger.error(f"Error processing transaction {transaction.get('txid')}: {e}")
                        continue

            # Prepare the response data
            response_data = OrderedDict([
                ("page", page),
                ("totalPages", total_pages),
                ("itemsOnPage", len(transactions) if details != "basic" else 0),
                ("addrStr", flo_address),
                ("balance", float(truncate_satoshi_to_btc(balance_sat))),
                ("balanceSat", int(balance_sat)),
                ("totalReceived", float(truncate_satoshi_to_btc(total_received_sat))),
                ("totalReceivedSat", int(total_received_sat)),
                ("totalSent", float(truncate_satoshi_to_btc(total_sent_sat))),
                ("totalSentSat", int(total_sent_sat)),
                ("unconfirmedBalance", float(0)),
                ("unconfirmedBalanceSat", 0),
                ("unconfirmedTxApperances", 0),
                ("txApperances", total_count),
            ])

            # Add the `txs` key only if details != "basic"
            if details != "basic":
                response_data["txs"] = transactions

            return paginated_transactions, response_data

    except ValueError as ve:
        logger.error(f"Invalid input for pagination: {ve}")
        raise
    except Exception as e:
        logger.error(f"Error during address data fetch/calculation: {e}")
        raise


#Fetches from internal address index
@address_indexer_app.route('/api/v2/address/<flo_address>', methods=['GET'])
@address_indexer_app.route('/api/v1/address/<flo_address>', methods=['GET'])
@address_indexer_app.route('/api/address/<flo_address>', methods=['GET'])
async def address_fetch(flo_address):
    """
    Fetch transactions for the given FLO address using the mapping table.
    """
    if not is_valid_flo_address(flo_address):
            return jsonify({"error": "Invalid FLO address"}), 400


    try:
        query_params = {key.lower(): value for key, value in request.args.items()}
        page = int(query_params.get("page", 1))  
        pagesize = int(query_params.get("pagesize", 1000))
        details = query_params.get("details", "txs")   

        conn = await get_mysql_connection()

        try:
            # Fetch results and calculate response data
            results, response_data = await fetch_and_calculate_address_data(conn, flo_address, page, pagesize, details)
            return jsonify(response_data)

        finally:
            # Release connection back to the pool
            await async_connection_pool.release(conn)

    except ValueError as ve:
        logger.error(f"Invalid pagination parameters: {ve}")
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        logger.error(f"Error fetching transactions for FLO address {flo_address}: {e}")
        return jsonify({"error": "Error fetching transactions", "details": str(e)}), 500


@address_indexer_app.route('/api/v2/block/<blockhash>', methods=['GET'])
@address_indexer_app.route('/api/v1/block/<blockhash>', methods=['GET'])
@address_indexer_app.route('/api/block/<blockhash>', methods=['GET'])
async def get_block_by_hash(blockhash):
    """API to fetch block details by blockhash."""

    if not is_valid_hash(blockhash):
        return jsonify({"error": "Invalid block hash"}), 400

    try:
        block_output = await b_process_block(blockhash)

        # Handle returned error strings
        if isinstance(block_output, str):
            logger.error(f"Block processing returned string error: {block_output}")
            return jsonify({"error": block_output}), 500

        # Handle returned error dicts
        if isinstance(block_output, dict) and "error" in block_output:
            logger.error(f"Block processing returned error: {block_output['error']}")
            return jsonify(block_output), 500

        # Return cleanly serialized block data
        return Response(
            json.dumps(block_output),
            status=200,
            mimetype='application/json'
        )

    except Exception as e:
        logger.error(f"Unhandled error in get_block_by_hash: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error while processing block {blockhash}"}), 500



@address_indexer_app.route('/api/v2/tx/<txhash>', methods=['GET'])
@address_indexer_app.route('/api/v1/tx/<txhash>', methods=['GET'])
@address_indexer_app.route('/api/tx/<txhash>', methods=['GET'])
async def get_tx_by_hash(txhash):
    """API to fetch transaction details by txhash and format as per Blockbook output."""

    if not is_valid_hash(txhash):
        return jsonify({"error": "Invalid transaction hash"}), 400

    # Fetch raw transaction data
    rpc_response = await rpc_request('getrawtransaction', [txhash, True])

    # Check for actual errors in the RPC response
    if rpc_response.get("error") is not None:
        logger.error(f"Error in RPC response: {rpc_response['error']}")
        return Response(
            json.dumps(rpc_response),
            status=500,
            mimetype='application/json'
        )

    # Ensure the transaction result exists
    tx_data = rpc_response.get("result", {})
    if not tx_data:
        logger.error(f"No transaction data found for hash: {txhash}")
        return Response(
            json.dumps({"error": "Transaction not found"}),
            status=400,
            mimetype='application/json'
        )

    # Extract required transaction details
    blockhash = tx_data.get("blockhash")
    confirmations = tx_data.get("confirmations", 0)
    time = tx_data.get("time")
    blocktime = tx_data.get("blocktime")
    size = tx_data.get("size")
    version = tx_data.get("version")
    locktime = tx_data.get("locktime")
    txid = tx_data.get("txid")
    hex_data = tx_data.get("hex")
    floData = tx_data.get("floData", "")

    # Fetch blockheight using blockhash if available
    blockheight = None
    if blockhash:
        try:
            block_response = await rpc_request('getblock', [blockhash])
            blockheight = block_response.get("result", {}).get("height")
        except Exception as e:
            logger.error(f"Error fetching block height for blockhash {blockhash}: {e}")

    # Compute VIN values
    tx_data["vin"] = await b_compute_vin_values(tx_data.get("vin", []))

    # Compute VOUT values
    await b_compute_vout_values(tx_data)

    # Compute transaction-level fields such as valueIn, valueOut, fees
    await b_compute_transaction_values(tx_data, blockhash, blockheight)

    # Format the transaction data as per Blockbook output
    formatted_tx = OrderedDict([
        ("txid", txid),
        ("version", version),
        ("locktime", locktime),
        ("vin", tx_data["vin"]),  # Includes "addresses", "isAddress", "value", "valueSat"
        ("vout", tx_data["vout"]),  # Includes "value", "valueSat", "addresses", "isAddress"
        ("blockhash", blockhash),
        ("blockheight", blockheight),
        ("confirmations", confirmations),
        ("time", time),
        ("blocktime", blocktime),
        ("valueOut", tx_data.get("valueOut")),
        ("valueOutSat", tx_data.get("valueOutSat")),
        ("valueIn", tx_data.get("valueIn")),
        ("valueInSat", tx_data.get("valueInSat")),
        ("fees", tx_data.get("fees")),
        ("feesSat", tx_data.get("feesSat")),
        ("size", size),
        ("hex", hex_data),
        ("floData", floData),
    ])

    # Return the response using custom serialization to ensure order
    return Response(
        json.dumps(formatted_tx),
        status=200,
        mimetype='application/json'
    )




# Fetches all txids directly from external flosight or blockbook, and then use normal address API to return results
@address_indexer_app.route('/api/address/priority/<flo_address>', methods=['GET'])
async def fetch_priority_transactions(flo_address):
    """
    Fetch and process transactions for a specific FLO address with priority, then invoke the general address API to return results.
    """
    if not is_valid_flo_address(flo_address):
            return jsonify({"error": "Invalid FLO address"}), 400
    try:
        # Step 1: Trigger external processing for the address
        result = await process_address_externalsource(flo_address)

        # Log the result of external processing
        if "error" in result:
            logger.error(f"External processing failed for {flo_address}: {result['error']}")
            return jsonify({
                "error": "Failed to process priority transactions.",
                "details": result["error"]
            }), 500

        logger.info(f"External processing completed for {flo_address}: {result['message']}")

        # Step 2: Construct the URL for the underlying address API
        base_url = request.host_url.rstrip('/')  # Get the base URL of the server
        address_api_url = f"{base_url}/api/address/{flo_address}"

        # Forward the query parameters to the address API
        query_params = {key: value for key, value in request.args.items()}

        global aiohttp_session
        if aiohttp_session is None:
            aiohttp_session = aiohttp.ClientSession()

        async with aiohttp_session.get(address_api_url, params=query_params) as response:
            if response.status != 200:
                error_message = await response.json()
                logger.error(f"Error fetching transactions from {address_api_url}: {error_message}")
                return jsonify({
                    "error": f"Error fetching transactions from {address_api_url}",
                    "details": error_message
                }), response.status

            # Return the response from the address API
            address_api_response = await response.json()
            return jsonify(address_api_response)

    except Exception as e:
        logger.error(f"Error in priority transaction fetch for {flo_address}: {e}")
        return jsonify({"error": "Error processing priority transactions", "details": str(e)}), 500



@address_indexer_app.route('/api/hash/<hash_value>', methods=['GET'])
async def get_block_or_tx(hash_value):
    """
    API to check if the hash is a blockhash or txhash by querying both APIs in parallel.
    Returns the response with the longer content.
    """
    if not is_valid_hash(hash_value):
        return jsonify({"error": "Invalid hash"}), 400

    try:
        # Construct URLs
        block_url = f"{SELF_URL}/api/block/{hash_value}"
        transaction_url = f"{SELF_URL}/api/tx/{hash_value}"

        global aiohttp_session
        if aiohttp_session is None:
            aiohttp_session = aiohttp.ClientSession()

        # Fetch block and transaction data in parallel
        block_task = aiohttp_session.get(block_url, timeout=API_TIMEOUT)
        tx_task = aiohttp_session.get(transaction_url, timeout=API_TIMEOUT)

        # Wait for both tasks to complete
        block_response, tx_response = await asyncio.gather(block_task, tx_task)

        # Process block response
        block_data = None
        if block_response.status == 200:
            block_text = await block_response.text()
            if "Block not found" not in block_text:
                block_data = block_text

        # Process transaction response
        tx_data = None
        if tx_response.status == 200:
            tx_text = await tx_response.text()
            tx_data = tx_text

        # Compare and return the longer response
        if block_data and tx_data:
            if len(block_data) >= len(tx_data):
                return Response(block_data, status=200, mimetype="application/json")
            else:
                return Response(tx_data, status=200, mimetype="application/json")
        elif block_data:
            return Response(block_data, status=200, mimetype="application/json")
        elif tx_data:
            return Response(tx_data, status=200, mimetype="application/json")
        else:
            return Response(
                json.dumps({"error": "Hash not found"}),
                status=400,
                mimetype="application/json"
            )

    except Exception as e:
        logger.error(f"Error processing hash {hash_value}: {e}")
        return Response(
            json.dumps({"error": f"Error processing hash {hash_value}: {str(e)}"}),
            status=500,
            mimetype="application/json"
        )


@address_indexer_app.route('/api/blockheight/<int:block_height>', methods=['GET'])
async def get_block_by_number(block_height):
    """
    API to fetch block data by block number.
    Step 1: Fetch block hash using RPC request.
    Step 2: Fetch block data using SELF_URL.
    """
    # Validate block height
    if not is_valid_block_height(block_height):
        return jsonify({"error": "Invalid block height"}), 400

    try:
        # Step 1: Fetch the block hash using the block number
        blockhash_response = await rpc_request('getblockhash', [block_height])
        if "result" not in blockhash_response:
            return jsonify({"error": f"Block number {block_height} not found"}), 400

        blockhash = blockhash_response["result"]

        # Step 2: Fetch the block data using SELF_URL
        block_data_url = f"{SELF_URL}/api/block/{blockhash}"

        global aiohttp_session
        if aiohttp_session is None:
            aiohttp_session = aiohttp.ClientSession()

        async with aiohttp_session.get(block_data_url, timeout=API_TIMEOUT) as block_data_response:
            if block_data_response.status != 200:
                return jsonify({"error": f"Failed to fetch block data for block number {block_height}"}), block_data_response.status

            raw_bytes = await block_data_response.read()
            text = raw_bytes.decode("utf-8", errors="replace")
            block_data = json.loads(text)

        return jsonify(block_data), 200

    except Exception as e:
        return jsonify({"error": f"Error processing block number {block_height}: {str(e)}"}), 500



@address_indexer_app.route('/api/block-index/<int:block_height>', methods=['GET'])
@address_indexer_app.route('/api/v1/block-index/<int:block_height>', methods=['GET'])
@address_indexer_app.route('/api/v2/block-index/<int:block_height>', methods=['GET'])
async def get_blockhash_by_height(block_height):
    """
    API to fetch block hash by block number.
    Blockbook-compatible response format:
    {
        "blockHash": "<hash>"
    }
    """
    # Validate block height
    if not is_valid_block_height(block_height):
        return jsonify({"error": "Invalid block height"}), 400

    try:
        # Fetch the block hash using the block number
        blockhash_response = await rpc_request('getblockhash', [block_height])
        if "result" not in blockhash_response:
            return jsonify({"error": f"Block number {block_height} not found"}), 400

        blockhash = blockhash_response["result"]

        # Return in Blockbook-compatible format
        return jsonify({"blockHash": blockhash}), 200

    except Exception as e:
        return jsonify({"error": f"Error processing block number {block_height}: {str(e)}"}), 500



@address_indexer_app.route('/api/sendtx/<signedTxHash>', methods=['GET'])
async def broadcast_transaction(signedTxHash):
    """
    API to broadcast a raw transaction through flod RPC.
    """
    try:
        # Validate the signed transaction hash
        signedTxHash = signedTxHash.strip()
        if not signedTxHash:
            return jsonify({"error": "Empty Transaction Data"}), 400

        # Send the raw transaction using flod's RPC method
        rpc_response = await rpc_request('sendrawtransaction', [signedTxHash])

        # Handle RPC errors
        if rpc_response.get("error") is not None:
            logger.error(f"RPC Error while broadcasting transaction: {rpc_response['error']}")
            return jsonify({
                "error": "Failed to broadcast transaction",
                "details": rpc_response["error"]
            }), 500

        # Extract transaction ID from the response
        txid = rpc_response.get("result")
        if not txid:
            logger.error("No transaction ID returned by the RPC response")
            return jsonify({"error": "Transaction broadcast failed"}), 500

        # Return success response
        return jsonify({"result": txid}), 200

    except Exception as e:
        logger.error(f"Error broadcasting transaction: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@address_indexer_app.route('/api/latest-block', methods=['GET'])
async def get_latest_block():
    """
    API to fetch the latest block in the processed_blocks table.
    """
    try:
        conn = await get_mysql_connection()
        try:
            query = """
                SELECT block_height, block_hash, UNIX_TIMESTAMP(processed_at) AS block_time
                FROM processed_blocks
                ORDER BY block_height DESC
                LIMIT 1;
            """
            async with conn.cursor() as cursor:
                await cursor.execute(query)
                result = await cursor.fetchone()

                if not result:
                    return jsonify({"error": "No blocks found in the processed_blocks table"}), 400

                block_height, block_hash, block_time = result

                # Handle case where block_time is None
                if block_time is None:
                    logger.warning(f"Invalid block_time for block {block_height}.")
                    block_time = "Invalid Time"

                return jsonify({
                    "blockheight": block_height,
                    "blockhash": block_hash,
                    "latest_time": block_time  # Already in UNIX timestamp format
                }), 200
        finally:
            await async_connection_pool.release(conn)
    except Exception as e:
        logger.error(f"Error fetching latest block: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500



@address_indexer_app.route('/api/v2/utxo/<address>', methods=['GET'])
@address_indexer_app.route('/api/v1/utxo/<address>', methods=['GET'])
@address_indexer_app.route('/api/utxo/<address>', methods=['GET'])
async def get_utxos_for_address(address):
    """
    API to fetch UTXOs for a given address. Includes an option to filter by confirmed UTXOs.
    Query Parameters:
        - confirmed (boolean): If true, only return UTXOs with confirmations > 0.
    """
    try:
        # Extract query parameter
        confirmed_only = request.args.get('confirmed', 'false').lower() == 'true'

        conn = await get_mysql_connection()
        async with conn.cursor() as cursor:
            try:
                # Ensure the correct database context
                await cursor.execute(f"USE `{ADDRESS_INDEXER_DB_NAME}`")
            except Exception as e:
                logger.error(f"Error selecting database: {e}")
                return jsonify({"error": "Database selection failed"}), 500

            # Fetch current block height
            await cursor.execute("SELECT MAX(block_height) FROM processed_blocks")
            result = await cursor.fetchone()
            if result is None or result[0] is None:
                logger.error("processed_blocks table is empty or block height is not available")
                return jsonify({"error": "No blocks have been processed yet"}), 500
            current_block_height = result[0]

            # Fetch UTXOs for the given address
            query = f"""
            SELECT txid, vout, amount, satoshis, block_height, spent
            FROM utxos
            WHERE address = %s AND spent = FALSE
            """
            await cursor.execute(query, (address,))
            utxos = await cursor.fetchall()

            # Prepare UTXO list with dynamic confirmations
            utxo_list = []
            for utxo in utxos:
                confirmations = current_block_height - utxo[4] + 1
                if confirmed_only and confirmations <= 0:
                    continue  # Skip unconfirmed UTXOs if confirmed_only is True
                utxo_list.append({
                    "txid": utxo[0],
                    "vout": utxo[1],
                    "amount": utxo[2],
                    "satoshis": utxo[3],
                    "block_height": utxo[4],
                    "confirmations": confirmations
                })

            return jsonify(utxo_list)
    except Exception as e:
        logger.error(f"Error fetching UTXOs for address {address}: {e}")
        return jsonify({"error": str(e)}), 500


@address_indexer_app.route('/api/balance/<flo_address>', methods=['GET'])
@address_indexer_app.route('/api/v2/balance/<flo_address>', methods=['GET'])
@address_indexer_app.route('/api/v1/balance/<flo_address>', methods=['GET'])
@address_indexer_app.route('/api/balance', methods=['GET'])
@address_indexer_app.route('/api/v1/balance', methods=['GET'])
@address_indexer_app.route('/api/v2/balance', methods=['GET'])
async def get_balance(flo_address=None):
    try:
        flo_address = flo_address or request.args.get('floAddress')
        if not flo_address:
            return jsonify(description="floAddress hasn't been passed"), 400

        if not is_valid_flo_address(flo_address):  
            return jsonify(description="Invalid FLO address"), 400

        conn = await get_mysql_connection()
        async with conn.cursor() as cursor:
            await cursor.execute(f"USE `{ADDRESS_INDEXER_DB_NAME}`")
            balance_sat, total_received_sat, total_sent_sat = await calculate_balance_and_update(conn, flo_address)

        response = {
            "floAddress": flo_address,
            "balance": float(truncate_satoshi_to_btc(balance_sat)),
            "balanceSat": int(balance_sat),
            "totalReceived": float(truncate_satoshi_to_btc(total_received_sat)),
            "totalReceivedSat": int(total_received_sat),
            "totalSent": float(truncate_satoshi_to_btc(total_sent_sat)),
            "totalSentSat": int(total_sent_sat),
        }


        return jsonify(response), 200

    except Exception as e:
        logger.error(f"get_balance: {e}")
        return jsonify(description="Internal error occurred"), 500





@address_indexer_app.route('/api/process_blocks', methods=['POST'])
async def api_process_blocks():
    """
    API to trigger block processing for a given range.
    """
    try:
        # Parse JSON request body
        data = await request.json
        start_block = data.get("start_block")
        end_block = data.get("end_block")

        # Validate input
        if start_block is None or end_block is None:
            return jsonify({"error": "Both 'start_block' and 'end_block' are required."}), 400
        if not isinstance(start_block, int) or not isinstance(end_block, int) or start_block > end_block:
            return jsonify({"error": "'start_block' and 'end_block' must be valid integers with start_block <= end_block."}), 400

        # Generate a unique task ID
        task_id = str(uuid4())
        task_status[task_id] = {"status": "in_progress", "details": None}

        # Trigger block processing
        async def process_task():
            try:
                await process_blocks(start_block, end_block)
                task_status[task_id]["status"] = "completed"
                logger.info(f"Block processing completed for range {start_block} to {end_block}.")
            except Exception as e:
                task_status[task_id]["status"] = "failed"
                task_status[task_id]["details"] = str(e)
                logger.error(f"Error processing blocks {start_block} to {end_block}: {e}")

        asyncio.create_task(process_task())
        logger.info(f"Block processing initiated for range {start_block} to {end_block} (Task ID: {task_id}).")

        # Return the task ID to the client
        return jsonify({"message": f"Block processing started for range {start_block} to {end_block}.", "task_id": task_id}), 202

    except Exception as e:
        logger.error(f"Error in API /api/process_blocks: {e}")
        return jsonify({"error": "Failed to initiate block processing.", "details": str(e)}), 500


@address_indexer_app.route('/api/task_status/<task_id>', methods=['GET'])
async def get_task_status(task_id):
    """
    API to check the status of a block processing task.
    """
    try:
        status = task_status.get(task_id)
        if not status:
            return jsonify({"error": "Invalid task ID or task not found."}), 400
        return jsonify({"task_id": task_id, "status": status["status"], "details": status.get("details")})
    except Exception as e:
        logger.error(f"Error in API /api/task_status/{task_id}: {e}")
        return jsonify({"error": "Failed to retrieve task status.", "details": str(e)}), 500


#FRAMEWORK FUNCTIONS


@scheduler.scheduled_job("interval", minutes=10)
async def scheduled_block_processor():
    try:
        last_processed_block = await get_last_processed_block()
        blockchain_info_response = await rpc_request('getblockchaininfo')
        if "result" not in blockchain_info_response:
            logger.error("Failed to fetch blockchain info.")
            return

        current_block = blockchain_info_response["result"]["blocks"]
        await process_blocks(last_processed_block + 1, current_block)
    except Exception as e:
        logger.error(f"Error in scheduled block processor: {e}")




async def rollback_all_blocks(conn, start_block, end_block):
    """
    Rollback all blocks between `start_block` and `end_block` from all relevant tables,
    adjust the balances of associated indexed addresses, and reinsert UTXOs.

    :param conn: Database connection object
    :param start_block: Starting block height for rollback (inclusive).
    :param end_block: Ending block height for rollback (inclusive).
    """
    # Query to reinsert spent UTXOs
    reinsert_spent_utxos_query = """
    INSERT INTO utxos (txid, vout, address, amount, satoshis, block_height, spent, created_at, updated_at)
    SELECT
        vin.txid,
        vin.vout,
        prev_vout.scriptPubKey->'$.addresses[0]' AS address,
        prev_vout.value AS amount,
        CAST(prev_vout.value * 1e8 AS UNSIGNED) AS satoshis,
        transactions.blockheight AS block_height,
        FALSE AS spent,
        NOW() AS created_at,
        NOW() AS updated_at
    FROM (
        SELECT JSON_TABLE(
            raw_transaction_json, '$.vin[*]'
            COLUMNS(txid VARCHAR(255) PATH '$.txid', vout INT PATH '$.vout')
        ) AS vin
        FROM transactions
        WHERE blockheight BETWEEN %s AND %s
    ) AS vin_data
    INNER JOIN transactions ON transactions.txid = vin_data.txid
    INNER JOIN JSON_TABLE(
        transactions.raw_transaction_json, '$.vout[*]'
        COLUMNS(value DECIMAL(16,8) PATH '$.value', scriptPubKey JSON PATH '$.scriptPubKey')
    ) AS prev_vout ON vin_data.vout = prev_vout.scriptPubKey->'$.vout';
    """

    # Query to delete UTXOs created in the rollback range
    delete_created_utxos_query = """
    DELETE FROM utxos
    WHERE txid IN (
        SELECT txid FROM transactions
        WHERE blockheight BETWEEN %s AND %s
    );
    """

    # Other queries (e.g., balance updates) remain unchanged

    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Fetch all indexed addresses
            await cursor.execute(fetch_indexed_addresses_query)
            indexed_addresses = {row['flo_address'] for row in await cursor.fetchall()}

            logger.info(f"Found {len(indexed_addresses)} indexed addresses to check during rollback.")

            # Dictionary to track balance changes for indexed addresses
            address_balance_changes = {address: {"received": Decimal(0), "sent": Decimal(0)} for address in indexed_addresses}

            # Fetch all transactions in the range of blocks to be rolled back
            await cursor.execute(fetch_block_transactions_query, (start_block, end_block))
            transactions_to_rollback = await cursor.fetchall()

            logger.info(f"Rolling back {len(transactions_to_rollback)} transactions from blocks {start_block} to {end_block}.")

            processed_blocks = set()

            # Process each transaction to rollback balance effects and UTXOs
            for transaction in transactions_to_rollback:
                try:
                    raw_transaction = json.loads(transaction['raw_transaction_json'])
                    txid = transaction['txid']

                    # Balance adjustments (unchanged from previous logic)
                    for vout in raw_transaction.get("vout", []):
                        addresses = vout.get("scriptPubKey", {}).get("addresses", [])
                        value_sat = Decimal(vout.get("value", 0)) * Decimal(1e8)
                        for address in addresses:
                            if address in indexed_addresses:
                                address_balance_changes[address]["received"] -= value_sat

                    for vin in raw_transaction.get("vin", []):
                        if "addresses" in vin:
                            addresses = vin.get("addresses", [])
                            value_sat = Decimal(vin.get("value", 0)) * Decimal(1e8)
                            for address in addresses:
                                if address in indexed_addresses:
                                    address_balance_changes[address]["sent"] -= value_sat

                except Exception as e:
                    logger.error(f"Error processing transaction {transaction.get('txid')}: {e}")
                    continue

            # Reinsert spent UTXOs
            await cursor.execute(reinsert_spent_utxos_query, (start_block, end_block))

            # Delete created UTXOs
            await cursor.execute(delete_created_utxos_query, (start_block, end_block))

            # Update balances for indexed addresses
            for address, changes in address_balance_changes.items():
                try:
                    # Fetch current stored balance
                    await cursor.execute("""
                    SELECT balance_sat, total_received_sat, total_sent_sat, last_computed_blockheight
                    FROM balance_data
                    WHERE flo_address = %s;
                    """, (address,))
                    stored_balance = await cursor.fetchone()

                    balance_sat = Decimal(stored_balance['balance_sat']) if stored_balance else Decimal(0)
                    total_received_sat = Decimal(stored_balance['total_received_sat']) if stored_balance else Decimal(0)
                    total_sent_sat = Decimal(stored_balance['total_sent_sat']) if stored_balance else Decimal(0)

                    # Adjust balances
                    balance_sat += changes["received"] - changes["sent"]
                    total_received_sat += changes["received"]
                    total_sent_sat += changes["sent"]

                    # Update stored balance
                    await cursor.execute(update_balance_query, (
                        address,
                        int(balance_sat),
                        int(total_received_sat),
                        int(total_sent_sat),
                        start_block - 1  # Adjust last computed block height
                    ))

                except Exception as e:
                    logger.error(f"Error updating balance for address {address}: {e}")
                    continue

            # Commit changes
            await conn.commit()
            logger.info(f"Rollback and UTXO reinsertion completed for blocks {start_block} to {end_block}.")

    except Exception as e:
        logger.error(f"Error rolling back blocks {start_block} to {end_block}: {e}")
        raise





@address_indexer_app.route('/api/rollback', methods=['POST'])
async def rollback_blocks():
    """
    API Endpoint to roll back blocks between `start_block` and `end_block`.
    """
    try:
        # Parse request JSON
        data = await request.json
        start_block = data.get("start_block")
        end_block = data.get("end_block")

        # Validate input
        if start_block is None or end_block is None:
            return jsonify({"error": "start_block and end_block are required"}), 400

        if not isinstance(start_block, int) or not isinstance(end_block, int):
            return jsonify({"error": "start_block and end_block must be integers"}), 400

        if start_block > end_block:
            return jsonify({"error": "start_block must be less than or equal to end_block"}), 400

        # Get a database connection
        conn = await get_mysql_connection()

        # Rollback blocks and adjust balances
        await rollback_all_blocks(conn, start_block, end_block)

        # Return success response
        return jsonify({"message": f"Rollback completed for blocks {start_block} to {end_block}."})

    except Exception as e:
        logger.error(f"Error during rollback: {e}")
        return jsonify({"error": "Rollback failed", "details": str(e)}), 500

    finally:
        # Ensure the connection is released
        try:
            if conn:
                async_connection_pool.release(conn)
        except Exception as release_error:
            logger.error(f"Failed to release connection: {release_error}")


#UI Section 

# HTML Template as a Multi-line String
html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Address Indexer API UI</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .flo-data {
            font-size: 1.1rem;
            font-weight: 500;
            color: #444;
        }
        .flo-data-label {
            font-size: 1rem;
            font-weight: bold;
            color: #333;
        }
        .tx-time {
            font-size: 1rem;
            font-weight: bold;
            color: #555;
        }
        .secondary-field {
            font-size: 0.9rem;
            color: #777;
        }
        a {
            text-decoration: none;
            color: #007bff;
        }
        a:hover {
            text-decoration: underline;
            color: #0056b3;
        }
        .loading-spinner {
            text-align: center;
            margin-top: 20px;
        }
        .latest-block {
            font-size: 0.9rem;
            color: #555;
            margin-top: 10px;
            text-align: center;
        }
    </style>
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
</head>
<body class="bg-light">
    <div class="container py-5">
        <h1 class="text-center mb-4">Address Indexer</h1>
        <form id="api-form" class="card p-4">
            <div class="mb-3">
                <label for="input_value" class="form-label">Enter FLO Address, Block Hash, Transaction Hash, or Block Height</label>
                <input type="text" id="input_value" name="input_value" class="form-control" placeholder="Enter Value" required>
            </div>
        </form>
        
        <!-- Latest Block Information -->
        <div id="latestBlock" class="latest-block">
            Loading latest block...
        </div>

        <div id="loading-spinner" class="loading-spinner d-none">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
        </div>
        <div id="api-result" class="mt-4"></div>
    </div>
    <script>
        async function fetchLatestBlock() {
            try {
                const response = await fetch('/api/latest-block');
                console.log('Response status:', response.status);

                if (!response.ok) {
                    document.getElementById('latestBlock').textContent = "Error fetching latest block.";
                    return;
                }
                const data = await response.json();

                // Ensure latest_time is parsed properly
                if (!data.latest_time || isNaN(data.latest_time)) {
                    document.getElementById('latestBlock').textContent = "Latest Block: " + data.blockheight + " | Invalid Date";
                    return;
                }

                // Format the timestamp to local date and time
                const localDate = new Date(data.latest_time * 1000).toLocaleString(undefined, {
                    day: 'numeric',
                    month: 'long',
                    year: 'numeric',
                    hour: 'numeric',
                    minute: 'numeric',
                    second: 'numeric',
                    hour12: true,
                });

                document.getElementById('latestBlock').textContent = `Latest Block: ${data.blockheight} | ${localDate}`;
            } catch (error) {
                console.error('Error fetching latest block:', error);
                document.getElementById('latestBlock').textContent = "Error fetching latest block.";
            }
        }


        // Call fetchLatestBlock on page load
        fetchLatestBlock();

        // Auto-submit the form when the input value changes
        $('#input_value').on('input', function() {
            $('#api-form').submit();
        });

        // Handle form submission
        $('#api-form').on('submit', async function(event) {
            event.preventDefault();
            const inputValue = $('#input_value').val();
            let apiUrl;

            // Show loading spinner
            $('#loading-spinner').removeClass('d-none');
            $('#api-result').html('');

            // Determine API type
            if (/^[a-zA-Z0-9]{34}$/.test(inputValue)) {
                apiUrl = `/api/address/${inputValue}?page=1&pageSize=1000`;
            } else if (/^[a-fA-F0-9]{64}$/.test(inputValue)) {
                apiUrl = `/api/hash/${inputValue}`;
            } else if (/^\d+$/.test(inputValue)) {
                apiUrl = `/api/blockheight/${inputValue}`;
            } else {
                $('#loading-spinner').addClass('d-none');
                $('#api-result').html('<div class="alert alert-danger">Invalid input format.</div>');
                return;
            }

            try {
                const response = await fetch(apiUrl);
                $('#loading-spinner').addClass('d-none'); // Hide spinner when request completes
                if (!response.ok) {
                    const errorData = await response.json();
                    $('#api-result').html('<div class="alert alert-danger">' + errorData.error + '</div>');
                    return;
                }

                const data = await response.json();

                // Render results based on response type
                if (apiUrl.includes('/api/address/')) {
                    $('#api-result').html(renderAddress(data));
                } else if (apiUrl.includes('/api/hash/')) {
                    if (data.txid) {
                        $('#api-result').html(renderTransaction(data));
                    } else if (data.hash) {
                        $('#api-result').html(renderBlock(data));
                    } else {
                        $('#api-result').html('<div class="alert alert-danger">Invalid hash format.</div>');
                    }
                } else {
                    $('#api-result').html(renderBlock(data));
                }
            } catch (error) {
                $('#loading-spinner').addClass('d-none');
                $('#api-result').html('<div class="alert alert-danger">Error fetching data.</div>');
            }
        });

        function renderAddress(data) {
            return `
                <div class="card shadow-sm mb-3">
                    <div class="card-body">
                        <h5 class="card-title">FLO Address: 
                            <a href="/address/${data.addrStr}" onclick="fetchData('/api/address/${data.addrStr}?page=1&pageSize=1000'); return false;">${data.addrStr}</a>
                        </h5>
                        <p class="secondary-field">
                            <strong>Balance:</strong> ${data.balance} <br>
                            <strong>Total Received:</strong> ${data.totalReceived} <br>
                            <strong>Total Sent:</strong> ${data.totalSent} <br>
                            <strong>Transactions:</strong> ${data.txApperances}
                        </p>
                    </div>
                </div>
                ${data.txs.map(tx => renderTransactionCard(tx, tx.time ? formatDate(tx.time) : 'N/A')).join('')}
            `;
        }

        function renderBlock(data) {
            const blockTime = data.time ? formatDate(data.time) : 'N/A';

            return `
                <div class="card shadow-sm">
                    <div class="card-body">
                        <h5 class="card-title">Block Hash:
                            <a href="/block/${data.hash}" onclick="fetchData('/api/block/${data.hash}'); return false;">${data.hash}</a>
                        </h5>
                        <p class="secondary-field">
                            <strong>Height:</strong> ${data.height} <br>
                            <strong>Transactions:</strong> ${data.txCount} <br>
                            <strong>Confirmations:</strong> ${data.confirmations} <br>
                            <strong>Previous Block Hash:</strong> 
                            <a href="/block/${data.previousBlockHash}" onclick="fetchData('/api/block/${data.previousBlockHash}'); return false;">${data.previousBlockHash}</a> <br>
                            <strong>Next Block Hash:</strong> 
                            <a href="/block/${data.nextBlockHash}" onclick="fetchData('/api/block/${data.nextBlockHash}'); return false;">${data.nextBlockHash}</a>
                        </p>
                        ${data.txs ? data.txs.map(tx => renderTransactionCard(tx, blockTime)).join('') : ''}
                    </div>
                </div>
            `;
        }

        function renderTransaction(data) {
            return renderTransactionCard(data, data.time ? formatDate(data.time) : 'N/A');
        }

        function renderTransactionCard(tx, displayTime) {
            const uniqueSenders = new Set(tx.vin.flatMap(input => input.addresses || []));
            const uniqueReceivers = new Set(tx.vout.flatMap(output => output.addresses || []));

            const transactionDetailsId = `details-${tx.txid}`;

            return `
                <div class="card shadow-sm mb-3">
                    <div class="card-body">
                        <p class="flo-data-label">FLO Data:</p>
                        <p class="flo-data">${tx.floData || 'N/A'}</p>
                        <p class="tx-time">${displayTime}</p>
                        <button class="btn btn-link p-0" style="text-decoration: none;" onclick="toggleDetails('${transactionDetailsId}', this)">Show Details</button>
                        <div id="${transactionDetailsId}" class="d-none mt-3">
                            <p class="secondary-field">
                                <strong>Sender:</strong> 
                                ${[...uniqueSenders]
                                    .map(addr => `<a href="/address/${addr}" onclick="fetchData('/api/address/${addr}'); return false;">${addr}</a>`)
                                    .join(', ') || 'N/A'} <br>
                                <strong>Receiver:</strong> 
                                ${[...uniqueReceivers]
                                    .map(addr => `<a href="/address/${addr}" onclick="fetchData('/api/address/${addr}'); return false;">${addr}</a>`)
                                    .join(', ') || 'N/A'} <br>
                                <strong>Value In:</strong> ${tx.valueIn || 'N/A'} <br>
                                <strong>Value Out:</strong> ${tx.valueOut || 'N/A'} <br>
                                <strong>Confirmations:</strong> ${tx.confirmations || 'N/A'} <br>
                                <strong>Transaction ID:</strong> 
                                <a href="/tx/${tx.txid}" onclick="fetchData('/api/tx/${tx.txid}'); return false;">${tx.txid}</a> <br>
                                <strong>Block Hash:</strong> 
                                <a href="/block/${tx.blockhash}" onclick="fetchData('/api/block/${tx.blockhash}'); return false;">${tx.blockhash}</a>
                            </p>
                        </div>
                    </div>
                </div>
            `;
        }


        function toggleDetails(id, button) {
            const detailsDiv = document.getElementById(id);
            const isHidden = detailsDiv.classList.contains('d-none');
            
            // Toggle visibility
            detailsDiv.classList.toggle('d-none', !isHidden);
            
            // Update button text
            button.textContent = isHidden ? 'Hide Details' : 'Show Details';
        }


        // Format time as "7 August 2024 at 1:47:27 pm"
        function formatDate(timestamp) {
            const date = new Date(timestamp * 1000);
            const options = { day: 'numeric', month: 'long', year: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric', hour12: true };
            return date.toLocaleString(undefined, options);
        }

        // Fetch data for links
        async function fetchData(apiUrl) {
            try {
                $('#loading-spinner').removeClass('d-none');
                const response = await fetch(apiUrl);
                $('#loading-spinner').addClass('d-none');
                if (!response.ok) {
                    $('#api-result').html('<div class="alert alert-danger">Error fetching data.</div>');
                    return;
                }

                const data = await response.json();

                if (apiUrl.includes('/api/address/')) {
                    $('#api-result').html(renderAddress(data));
                } else if (apiUrl.includes('/api/tx/')) {
                    $('#api-result').html(renderTransaction(data));
                } else if (apiUrl.includes('/api/block/')) {
                    $('#api-result').html(renderBlock(data));
                }
            } catch (error) {
                $('#loading-spinner').addClass('d-none');
                $('#api-result').html('<div class="alert alert-danger">Error fetching data.</div>');
            }
        }
    </script>
</body>
</html>



"""


# Serve the HTML Content
@address_indexer_app.route('/')
async def index():
    """Serve the main UI."""
    return Response(html_content, mimetype='text/html')

# Handle form submissions and API calls
@address_indexer_app.route('/submit', methods=['POST'])
async def submit():
    """
    Handles form submissions and makes API requests to the appropriate endpoint.
    """
    data = await request.form
    input_value = data.get('input_value', '').strip()
    page = data.get('page', 1)
    page_size = data.get('page_size', 10)

    # Determine API type based on input value
    if len(input_value) == 34 and input_value.isalnum():  # FLO address format
        api_url = f"{SELF_URL}/api/address/{input_value}?page={page}&pageSize={page_size}"
    elif len(input_value) == 64 and all(c in '0123456789abcdefABCDEF' for c in input_value):  # Hash format
        if "block" in data.get('api_type', ''):  # Check if it's a block or tx hash
            api_url = f"{SELF_URL}/api/block/{input_value}"
        else:
            api_url = f"{SELF_URL}/api/tx/{input_value}"
    elif input_value.isdigit():  # Block height
        api_url = f"{SELF_URL}/api/blockheight/{input_value}"
    else:
        return jsonify({"error": "Invalid input format."}), 400

    # Make API call using global aiohttp session
    global aiohttp_session
    if aiohttp_session is None:
        aiohttp_session = aiohttp.ClientSession()

    try:
        async with aiohttp_session.get(api_url) as response:
            if response.status == 200:
                result = await response.json()
                return jsonify(result)
            else:
                return jsonify({"error": f"API request failed with status {response.status}."}), response.status
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@address_indexer_app.route('/address/<flo_address>', methods=['GET'])
async def address_ui_direct(flo_address):
    """
    Direct access to the Address UI with the given FLO address.
    """
    # Validate FLO address using regex
    if not re.match(r"^[Fe][a-zA-Z0-9]{33}$", flo_address):
        return jsonify({"error": "Invalid FLO address format."}), 400


    # Render the UI with prefilled FLO address and selected API type
    return Response(
        html_content.replace(
            'id="input_value" name="input_value"',
            f'id="input_value" name="input_value" value="{flo_address}"'
        ).replace(
            '<option value="address">',
            '<option value="address" selected>'
        ),
        mimetype='text/html'
    )

@address_indexer_app.route('/block/<block_hash>', methods=['GET'])
async def block_ui_direct(block_hash):
    """
    Direct access to the Block UI with the given block hash.
    """
    # Validate block hash using regex
    if not re.match(r"^[a-fA-F0-9]{64}$", block_hash):
        return jsonify({"error": "Invalid block hash format."}), 400

    # Render the UI with prefilled block hash and selected API type
    return Response(
        html_content.replace(
            'id="input_value" name="input_value"',
            f'id="input_value" name="input_value" value="{block_hash}"'
        ).replace(
            '<option value="blockhash">',
            '<option value="blockhash" selected>'
        ),
        mimetype='text/html'
    )

@address_indexer_app.route('/tx/<tx_hash>', methods=['GET'])
async def tx_ui_direct(tx_hash):
    """
    Direct access to the Transaction UI with the given transaction hash.
    """
    # Validate transaction hash using regex
    if not re.match(r"^[a-fA-F0-9]{64}$", tx_hash):
        return jsonify({"error": "Invalid transaction hash format."}), 400

    # Render the UI with prefilled transaction hash and selected API type
    return Response(
        html_content.replace(
            'id="input_value" name="input_value"',
            f'id="input_value" name="input_value" value="{tx_hash}"'
        ).replace(
            '<option value="txhash">',
            '<option value="txhash" selected>'
        ),
        mimetype='text/html'
    )


# Sample API Endpoint for Testing
@address_indexer_app.route('/api/test', methods=['GET'])
async def test_endpoint():
    """A simple test endpoint to verify API hosting on SELF_URL."""
    return jsonify({"message": "API is running correctly!"})



# A set to track all connected WebSocket clients
connected_clients = set()

@address_indexer_app.websocket('/websocket')
async def websocket_endpoint():
    """
    Handle WebSocket connections for subscription to new blocks.
    """
    # Log the client information
    client = websocket._get_current_object()  # Get the current WebSocket connection
    connected_clients.add(client)  # Add client to the set of connected clients


    try:
        while True:
            # Log incoming messages
            message = await websocket.receive()
            subscription_request = json.loads(message)

            # Handle subscription requests
            if subscription_request.get('method') == 'subscribeNewBlock':
                response = {"id": subscription_request.get("id"), "result": "Subscription successful"}
                await websocket.send(json.dumps(response))
            else:
                response = {"error": "Invalid method"}
                await websocket.send(json.dumps(response))

    except Exception as e:
        # Log WebSocket errors
        logger.error(f"WebSocket error: {e}")
    finally:
        # Remove the client from the connected set on disconnect
        connected_clients.remove(client)
        






async def zmq_block_subscriber():
    """
    Subscribes to ZMQ block notifications and triggers block processing.
    Also notifies connected WebSocket clients about new blocks.
    """
    context = zmq.asyncio.Context()
    socket = context.socket(zmq.SUB)
    socket.connect(ZMQ_BLOCK_HASH_ADDRESS)
    socket.setsockopt_string(zmq.SUBSCRIBE, '')  # Subscribe to all messages

    logger.info(f"Subscribed to ZMQ block notifications on {ZMQ_BLOCK_HASH_ADDRESS}")

    try:
        while True:
            try:
                # Receive multipart message
                message_parts = await socket.recv_multipart()
                topic = message_parts[0].decode('utf-8')  # Decode the topic as UTF-8
                payload = message_parts[1]  # Binary data (e.g., block or transaction hash)

                logger.info(f"Received topic: {topic}")

                # Handle block hash notifications
                if topic == "hashblock":
                    blockhash = payload.hex()  # Convert binary to hex string
                    logger.info(f"New block detected: {blockhash}")

                    # Fetch block data
                    block_data_response = await rpc_request("getblock", [blockhash, 2])
                    if "result" not in block_data_response:
                        logger.error(f"Failed to fetch block data for hash {blockhash}.")
                        continue

                    block_data = block_data_response["result"]
                    block_height = block_data.get("height")
                    if block_height is not None:
                        await process_blocks(block_height, block_height)  # Process the detected block

                        # Notify WebSocket clients about the new block
                        notification = {
                            "data": {
                                "height": block_height,
                                "hash": blockhash
                            }
                        }
                        await notify_clients(notification)

                # Handle transaction hash notifications
                elif topic == "hashtx":
                    txhash = payload.hex()  # Convert binary to hex string
                    logger.info(f"New transaction detected: {txhash}")
                    # Add transaction processing logic here if needed

                else:
                    logger.warning(f"Unknown topic received: {topic}")

            except asyncio.CancelledError:
                logger.info("ZMQ subscriber task was cancelled. Cleaning up...")
                break  # Exit the loop gracefully

            except Exception as e:
                logger.error(f"Error processing ZMQ notification: {e}")
                await asyncio.sleep(1)  # Avoid tight retry loops on error

    finally:
        logger.info("Closing ZMQ socket and context.")
        socket.close()
        context.term()  # Ensure ZMQ resources are freed properly


async def notify_clients(notification):
    """
    Notify all connected WebSocket clients with the given notification.
    """
    #logger.info(f"Broadcasting notification to {len(connected_clients)} WebSocket clients: {notification}")
    disconnected_clients = []
    for client in connected_clients:
        try:
            await client.send(json.dumps(notification))
        except websockets.exceptions.ConnectionClosed:
            disconnected_clients.append(client)
            #logger.info("WebSocket client disconnected during broadcast.")

    # Remove disconnected clients
    for client in disconnected_clients:
        connected_clients.remove(client)



async def check_flod_initialization():
    """
    Check if flod is correctly initialized and verify that txindex is enabled by making a transaction request.
    """
    try:
        # Get basic blockchain info
        response = await rpc_request("getblockchaininfo")
        if response and "result" in response:
            blockchain_info = response["result"]
            if "blocks" in blockchain_info and "bestblockhash" in blockchain_info:
                logger.info("Flod is initialized and responding correctly.")

                # Fetch the best block using bestblockhash
                best_block_hash = blockchain_info["bestblockhash"]
                block_response = await rpc_request("getblock", [best_block_hash, 2])  # Verbosity 2 to include tx details
                if block_response and "result" in block_response:
                    block_info = block_response["result"]
                    if "tx" in block_info and len(block_info["tx"]) > 0:
                        # Get the first transaction ID
                        txid = block_info["tx"][0]["txid"]

                        # Try fetching the transaction details
                        tx_response = await rpc_request("getrawtransaction", [txid, True])  # True for verbose
                        if tx_response and "result" in tx_response:
                            logger.info("Txindex is enabled and functioning correctly.")
                            return True
                        else:
                            logger.error(
                                "Txindex is not enabled in flod. Ensure txindex=1 is set in flod.conf, and run full indexing."
                            )
                            return False
                    else:
                        logger.error("No transactions found in the best block. Txindex cannot be verified.")
                        return False
                else:
                    logger.error(f"Failed to fetch block details for bestblockhash: {best_block_hash}. Response: {block_response}")
                    return False
            else:
                logger.error(f"Flod response to getblockchaininfo is missing required keys: {blockchain_info}")
                return False
        else:
            logger.error("RPC connection failed. Ensure flod is running and rpcuser/rpcpassword are configured in flo.conf.")
            return False
    except Exception as e:
        logger.error(
            "Failed to check flod initialization. Ensure flod is running, and rpcuser and rpcpassword are set in flo.conf: %s",
            e,
        )
        return False



async def check_mysqld_initialization():
    """
    Check if mysqld is correctly initialized by verifying the connection and user privileges.
    """
    try:
        conn = await get_mysql_connection()
        async with conn.cursor() as cursor:
            # Simple query to test connection
            await cursor.execute("SELECT 1")
            result = await cursor.fetchone()
            if result and result[0] == 1:
                logger.info("Mysqld is initialized and responding correctly.")
            else:
                logger.error("Mysqld is responding incorrectly. Query result: %s", result)
                return False

            # Check if the user has the necessary privileges
            await cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
            grants = await cursor.fetchall()

            # Ensure the user has CREATE, SELECT, INSERT, UPDATE, and DELETE privileges
            required_privileges = {"CREATE", "SELECT", "INSERT", "UPDATE", "DELETE"}
            all_grants = " ".join([row[0] for row in grants])
            missing_privileges = [priv for priv in required_privileges if priv not in all_grants.upper()]

            if missing_privileges:
                logger.error(f"MySQL user lacks necessary privileges: {missing_privileges}")
                return False

            logger.info("MySQL user has the necessary privileges.")
            return True

    except Exception as e:
        logger.error(f"Failed to check mysqld initialization: {e}")
        return False
    finally:
        try:
            if async_connection_pool:
                async_connection_pool.release(conn)
        except Exception as release_error:
            logger.error(f"Failed to release connection during mysqld initialization check: {release_error}")


async def perform_startup_checks():
    """
    Perform startup checks for flod and mysqld.
    """
    flod_initialized = await check_flod_initialization()
    mysqld_initialized = await check_mysqld_initialization()

    if not flod_initialized:
        logger.critical("Flod is not initialized. Exiting application.")
        sys.exit(1)  # Exit if flod is not initialized

    if not mysqld_initialized:
        logger.critical("Mysqld is not initialized. Exiting application.")
        sys.exit(1)  # Exit if mysqld is not initialized

    logger.info("All startup checks passed successfully.")


async def cleanup_utxos_via_rpc(batch_size=1):
    logger.info("Starting RPC-based UTXO cleanup (serial, one RPC at a time)...")

    conn = await get_mysql_connection()
    deleted_count = 0

    try:
        async with conn.cursor() as cursor:
            await cursor.execute(f"USE `{ADDRESS_INDEXER_DB_NAME}`")

            logger.info("Fetching all UTXOs...")
            await cursor.execute("SELECT txid, vout FROM utxos")
            utxos = await cursor.fetchall()
            total = len(utxos)
            logger.info(f"Checking {total} UTXOs via RPC for spend status...")

            for i, (txid, vout) in enumerate(utxos):
                try:
                    response = await rpc_request("gettxout", [txid, vout, True])
                    if response.get("result") is None:
                        await cursor.execute("DELETE FROM utxos WHERE txid = %s AND vout = %s", (txid, vout))
                        if cursor.rowcount > 0:
                            deleted_count += 1
                except Exception as e:
                    logger.warning(f"UTXO check error for {txid}:{vout}: {e}")

                # Commit every batch_size
                if (i + 1) % batch_size == 0:
                    await conn.commit()
                    logger.info(f"Processed {i + 1}/{total} UTXOs - Deleted so far: {deleted_count}")

            await conn.commit()
            logger.info(f"RPC-based UTXO cleanup complete. Total deleted: {deleted_count}")

    except Exception as e:
        logger.error(f"Error during RPC UTXO cleanup: {e}")
    finally:
        await async_connection_pool.release(conn)









#Core startup Routine            



# Thread stop event
stop_event = threading.Event()

# Global reference for the cleaner thread
cleaner_thread = None



async def create_cleaner_connection_pool(loop):
    """Create a separate MySQL connection pool for the cleaner thread with a fixed size of 2."""
    ports_to_try = [3306, 3307]  # List of MySQL ports to attempt
    successful_port = None

    for port in ports_to_try:
        try:
            pool = await aiomysql.create_pool(
                host=MYSQL_HOST,
                user=MYSQL_USER,
                port=port,
                password=MYSQL_PASSWORD,
                db=None,
                charset="utf8mb4",
                maxsize=2,  # Fixed pool size
                minsize=1,
                loop=loop,  # Attach pool to the cleaner thread's loop
            )
            successful_port = port
            logger.info(f"Cleaner connection pool initialized on port {port} with fixed size (maxsize=2).")
            return pool  # Return the pool immediately
        except Exception as e:
            logger.warning(f"Cleaner pool failed to connect to MySQL on port {port}: {e}")

    logger.error("Cleaner pool failed to initialize on all tried ports.")
    raise RuntimeError("Could not connect to MySQL for the cleaner thread.")




async def clean_idle_connections_with_async_pool(pool, idle_time_threshold=IDLE_TIME_THRESHOLD_TIMEOUT):
    """Cleans idle MySQL connections using the given async connection pool."""
    if pool is None:
        logger.error("Cleaner connection pool is not initialized.")
        return {"status": "error", "message": "Cleaner connection pool not initialized"}

    try:
        async with pool.acquire() as conn:  # Keep the same connection for both SELECT and KILL
            async with conn.cursor() as cursor:
                query = f"""
                SELECT id, time 
                FROM performance_schema.processlist 
                WHERE command = 'Sleep' AND time > {idle_time_threshold};
                """
                await cursor.execute(query)
                idle_connections = await cursor.fetchall()

                if not idle_connections:
                    #logger.info("No idle connections found exceeding the threshold.")
                    return {"status": "success", "terminated_connections": 0}

                # Run terminations within the same connection
                terminated_count = 0
                for connection_id, idle_time in idle_connections:
                    try:
                        await cursor.execute(f"KILL {connection_id};")
                        logger.info(f"Terminated connection ID {connection_id} (idle for {idle_time} seconds)")
                        terminated_count += 1
                    except Exception as e:
                        logger.error(f"Error terminating connection ID {connection_id}: {e}")

        logger.info(f"Terminated {terminated_count} idle connections.")
        return {"status": "success", "terminated_connections": terminated_count}

    except Exception as e:
        logger.error(f"Error cleaning idle connections: {e}")
        return {"status": "error", "message": str(e)}




def run_cleaner_in_thread():
    """Runs the async cleanup function in a dedicated thread with a separate connection pool."""
    global cleaner_thread, cleaner_connection_pool

    def thread_target():
        """Function that runs inside the cleaner thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def main():
            global cleaner_connection_pool
            try:
                cleaner_connection_pool = await create_cleaner_connection_pool(loop)
                if cleaner_connection_pool is None:
                    logger.error("Cleaner connection pool could not be initialized. Exiting thread.")
                    return

                while not stop_event.is_set():
                    try:
                        await clean_idle_connections_with_async_pool(cleaner_connection_pool, idle_time_threshold=IDLE_TIME_THRESHOLD_TIMEOUT)
                        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
                    except asyncio.CancelledError:
                        logger.info("Cleanup thread received exit signal, stopping.")
                        break  # Exit loop gracefully
                    except Exception as e:
                        logger.error(f"Unexpected error in cleaner thread: {e}")

            except asyncio.CancelledError:
                logger.info("Cleanup thread received cancellation request.")
            except Exception as e:
                logger.error(f"Unexpected error in cleaner thread startup: {e}")
            finally:
                if cleaner_connection_pool:
                    logger.info("Closing cleaner connection pool...")
                    cleaner_connection_pool.close()
                    await cleaner_connection_pool.wait_closed()
                    cleaner_connection_pool = None
                    logger.info("Cleanup thread exited gracefully.")

        # Schedule the main coroutine instead of blocking the loop
        loop.create_task(main())
        try:
            loop.run_forever()  # Keep the event loop running
        except KeyboardInterrupt:
            logger.info("Cleaner thread received KeyboardInterrupt, shutting down.")
        finally:
            loop.close()

    cleaner_thread = threading.Thread(target=thread_target, daemon=True)
    cleaner_thread.start()
    return cleaner_thread






def stop_cleaner_thread():
    """Gracefully stops the cleanup thread and closes its connection pool."""
    global cleaner_thread, cleaner_connection_pool

    if cleaner_thread is None:
        logger.warning("Cleanup thread is not running.")
        return

    logger.info("Stopping cleanup thread...")
    stop_event.set()  # Signal the cleanup thread to stop

    if cleaner_thread.is_alive():
        cleaner_thread.join(timeout=CLEANER_THREAD_EXIT_TIMEOUT)  # Wait up to 5 seconds for the thread to exit

        if cleaner_thread.is_alive():
            logger.error("Cleanup thread did not exit within timeout. Forcing termination.")
            # Additional safety: log error, but let OS handle cleanup

    # Close the cleaner connection pool if it exists
    if cleaner_connection_pool:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(cleaner_connection_pool.wait_closed())  # Ensure pool is fully closed
        cleaner_connection_pool = None  # Reset the pool reference
        logger.info("Cleaner connection pool closed.")

    logger.info("Cleanup thread stopped.")




def signal_handler(sig, frame):
    """Handles system signals (SIGINT, SIGTERM) for graceful shutdown."""
    logger.info(f"Received shutdown signal ({sig}).")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(shutdown(loop))
    except RuntimeError:
        logger.warning("No running event loop detected. Creating a new one for shutdown.")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(shutdown(loop))

    # Ensure proper cleanup before exiting
    if loop.is_running():
        loop.stop()  # Stop the event loop gracefully
        logger.info("Event loop stopped.")

    logger.info("Shutdown complete.")


    


# Register signal handlers for clean shutdown
signal.signal(signal.SIGINT, signal_handler)   # Handle Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)  # Handle kill command

# Start the cleanup process in a separate high-priority thread
cleaner_thread = run_cleaner_in_thread()



# MAIN EXECUTION LOOP
async def main():
    global MYSQL_DB_PREFIX, ADDRESS_INDEXER_DB_NAME, scheduler

    try:
        # --- Step 1: Detect FLOD network and configure DB prefix ---
        chain = await detect_network_via_rpc()
        if not chain:
            logger.warning("Defaulting to mainnet as network detection failed.")
            chain = 'main'

        if chain == 'test':
            MYSQL_DB_PREFIX = 'rm_test'
        else:
            MYSQL_DB_PREFIX = config['MYSQL'].get('DATABASE_PREFIX', 'rm')

        ADDRESS_INDEXER_DB_NAME = f"{MYSQL_DB_PREFIX}_addressindexer{config['ADDRESS_INDEXER'].get('SUFFIX', '_data')}"

        logger.info(f"FLOD detected network: {chain}")
        logger.info(f"Using MySQL DB prefix: {MYSQL_DB_PREFIX}")
        logger.info(f"Full DB name: {ADDRESS_INDEXER_DB_NAME}")

        # --- Step 2: Initialize MySQL connection pool ---
        await initialize_connection_pool()

        # --- Step 3: Perform startup checks ---
        await perform_startup_checks()

        # --- Step 4: Initialize DB schema if needed ---
        await initialize_database()

        # --- Step 5: Start scheduled tasks (e.g., connection cleanup) ---
        scheduler = AsyncIOScheduler()
        scheduler.start()

        # --- Step 6: Launch core async tasks ---
        zmq_task = asyncio.create_task(zmq_block_subscriber())
        missing_blocks_task = asyncio.create_task(find_and_process_missing_blocks())

        if RUN_UTXO_CLEANUP_ON_STARTUP:
            logger.info("Running UTXO cleanup via RPC on startup.")
            await cleanup_utxos_via_rpc()
        else:
            logger.info("Skipping UTXO cleanup via RPC on startup.")


        # --- Step 7: Configure and run Quart + Hypercorn server ---
        hypercorn_config = HypercornConfig()
        hypercorn_config.bind = [f"{ADDRESS_INDEXER_HOST}:{ADDRESS_INDEXER_PORT}"]

        # Allow long processing time for large blocks
        hypercorn_config.shutdown_timeout = 300
        hypercorn_config.read_timeout = 300
        hypercorn_config.write_timeout = 300
        hypercorn_config.keep_alive_timeout = 300
        hypercorn_config.max_app_queue_size = 100

        # Setup graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(loop)))

        logger.info("🚀 Starting Hypercorn server...")
        await serve(address_indexer_app, hypercorn_config)

    except Exception as e:
        logger.error(f"❌ Error in main execution loop: {e}", exc_info=True)
        sys.exit(1)


async def shutdown(loop):
    """Gracefully shutdown the application, including the cleaner thread."""
    global async_connection_pool, cleaner_connection_pool, scheduler, aiohttp_session  # Ensure scheduler is accessible

    if aiohttp_session:
        await aiohttp_session.close()
        aiohttp_session = None
        logger.info("aiohttp session closed.")


    logger.info("Shutting down gracefully...")

    # Stop the scheduler first to prevent new tasks from starting
    if 'scheduler' in globals() and scheduler is not None:
        if scheduler.running:
            scheduler.shutdown()
            logger.info("Scheduler stopped.")

    # Close the cleaner's connection pool before stopping the thread
    if cleaner_connection_pool:
        try:
            logger.info("Closing cleaner connection pool...")
            cleaner_connection_pool.close()
            await cleaner_connection_pool.wait_closed()
            cleaner_connection_pool = None
            logger.info("Cleaner connection pool closed.")
        except Exception as e:
            logger.error(f"Error closing cleaner connection pool: {e}")

    # Stop the cleaner thread only after closing its pool
    if cleaner_thread is not None and cleaner_thread.is_alive():
        logger.info("Stopping cleaner thread...")
        stop_cleaner_thread()

    # Close the main database connection pool
    if async_connection_pool:
        try:
            logger.info("Closing main connection pool...")
            async_connection_pool.close()
            await async_connection_pool.wait_closed()
            async_connection_pool = None
            logger.info("Main connection pool closed.")
        except Exception as e:
            logger.error(f"Error closing main connection pool: {e}")

    # Cancel all running tasks **after** closing the DB pools
    tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
    logger.info(f"Cancelling {len(tasks)} remaining tasks...")
    for task in tasks:
        task.cancel()

    # Wait for tasks to finish before exiting
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        logger.info("Some tasks were cancelled during shutdown.")

    # Stop the event loop if it's still running
    if loop.is_running():
        loop.stop()
        logger.info("Event loop stopped.")

    logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Received Ctrl+C, shutting down.")

