# === Standard Library Imports ===
import ast
import asyncio
import atexit
import configparser
import json
import logging
import os
import pdb
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from operator import itemgetter
from typing import Optional

# === Third-Party Library Imports ===
import aiohttp
import aiomysql
import psutil
import pymysql
import requests
from aiomysql import DictCursor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from dbutils.pooled_db import PooledDB
from hypercorn.asyncio import serve
from hypercorn.config import Config as HypercornConfig
from quart import (
    Quart, jsonify, make_response, render_template, request, flash,
    redirect, url_for, send_file, Response
)
from quart_cors import cors

# === Project-Specific Imports ===
import parsing
from config import *



# Configuration Setup
config = configparser.ConfigParser()

# Define potential config file paths
current_folder_path = os.path.join(os.getcwd(), 'config.ini')  # Current directory
fallback_path = '/home/production/deployed/ranchimallflo-api-blockbook-rescan/config.ini'  # Fallback directory

# Check and load the configuration file
if os.path.exists(current_folder_path):
    config.read(current_folder_path)
    print(f"Loaded configuration from {current_folder_path}")
elif os.path.exists(fallback_path):
    config.read(fallback_path)
    print(f"Loaded configuration from {fallback_path}")
else:
    raise FileNotFoundError("Configuration file 'config.ini' not found in either current folder or fallback folder.")


logging.basicConfig(level=logging.INFO)
# Adjust specific loggers
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)



app = Quart(__name__)
app.clients = set()

# Enable CORS with specific configurations
app = cors(
    app,
    allow_origin="*",  # Allow all origins. Replace "*" with specific origins for security in production.
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # List all allowed HTTP methods
    allow_headers=["Authorization", "Content-Type"],  # Specify headers to allow in requests
)


API_TIMEOUT = int(config['API'].get('API_TIMEOUT', 1))  # Default: 1 second
RETRY_TIMEOUT_LONG = int(config['API'].get('RETRY_TIMEOUT_LONG', 1800))  # Default: 30 minutes
RETRY_TIMEOUT_SHORT = int(config['API'].get('RETRY_TIMEOUT_SHORT', 60))  # Default: 1 minute
DB_RETRY_TIMEOUT = int(config['API'].get('DB_RETRY_TIMEOUT', 60))  # Default: 1 minute




# Global connection pools
sync_connection_pool = None
async_connection_pool = None
scheduler = AsyncIOScheduler()
shutdown_event = threading.Event()
background_async_pool = None
background_cleanup_thread = None
shutdown_triggered = False





class MySQLConfig:
    def __init__(self):
        self.username = config['MYSQL'].get('USERNAME', 'default_user')
        self.password = config['MYSQL'].get('PASSWORD', 'default_password')
        self.host = config['MYSQL'].get('HOST', 'localhost')
        self.database_prefix = config['MYSQL'].get('DATABASE_PREFIX', 'rm')

mysql_config = MySQLConfig()
net = config['DEFAULT'].get('NET', 'mainnet').strip().lower()


# Adjust MySQL prefix if running on testnet
if net == 'testnet':
    mysql_config.database_prefix += "test"


async def get_optimal_pool_size():
    """
    Dynamically determines the pool size based on CPU load.
    Uses parameterized values inside the function for better control.
    Reduces pool size as CPU load increases.
    """

    # Parameterized values inside the function
    CPU_LOW_THRESHOLD = 40     # Below this, use the maximum pool size
    CPU_HIGH_THRESHOLD = 80    # Above this, use the minimum pool size
    POOL_MIN_SIZE = 5          # Minimum allowed pool size
    POOL_MAX_SIZE = 20         # Maximum allowed pool size

    # Get current CPU usage
    cpu_usage = psutil.cpu_percent(interval=1)  # Get CPU usage over 1 second
    print(f"Current CPU usage: {cpu_usage}%")

    if cpu_usage < CPU_LOW_THRESHOLD:
        return POOL_MAX_SIZE  # At low CPU usage, allow maximum connections
    elif cpu_usage > CPU_HIGH_THRESHOLD:
        return POOL_MIN_SIZE  # At high CPU usage, reduce the pool size
    else:
        # Scale proportionally between POOL_MAX_SIZE and POOL_MIN_SIZE (reverse scaling)
        scaled_size = int(POOL_MAX_SIZE - ((cpu_usage - CPU_LOW_THRESHOLD) / (CPU_HIGH_THRESHOLD - CPU_LOW_THRESHOLD)) * (POOL_MAX_SIZE - POOL_MIN_SIZE))
        return max(POOL_MIN_SIZE, min(scaled_size, POOL_MAX_SIZE))  # Ensure within limits

async def adjust_pool_size():
    """Periodically adjust the pool size based on CPU utilization."""
    global async_connection_pool

    if async_connection_pool is not None:
        new_maxsize = await get_optimal_pool_size()
        async_connection_pool.maxsize = new_maxsize
        logger.info(f"Adjusted connection pool size to {new_maxsize} based on CPU utilization.")





async def initialize_connection_pools():
    """
    Initializes sync and async connection pools, dynamically adjusting the size based on CPU load.
    """
    global sync_connection_pool, async_connection_pool

    try:
        # Determine the optimal pool size based on CPU load
        pool_size = await get_optimal_pool_size()
        print(f"Initializing connection pool with size: {pool_size}")

        # Sync connection pool
        sync_connection_pool = PooledDB(
            creator=pymysql,
            maxconnections=pool_size,
            mincached=2,
            maxcached=min(5, pool_size),
            blocking=True,
            host=mysql_config.host,
            user=mysql_config.username,
            password=mysql_config.password,
            charset="utf8mb4",
        )

        # Async connection pool
        async_connection_pool = await aiomysql.create_pool(
            host=mysql_config.host,
            user=mysql_config.username,
            password=mysql_config.password,
            db=None,
            charset="utf8mb4",
            maxsize=pool_size,
            minsize=2,
            loop=asyncio.get_running_loop(),
        )
        print(f"Connection pools initialized successfully with max size: {pool_size}")

    except Exception as e:
        print(f"Error initializing connection pools: {e}")
        raise RuntimeError("Failed to initialize database connection pools")

async def initialize_background_pool():
    global background_async_pool
    try:
        background_async_pool = await aiomysql.create_pool(
            host=mysql_config.host,
            user=mysql_config.username,
            password=mysql_config.password,
            db=None,
            charset="utf8mb4",
            maxsize=2,
            minsize=1,
            loop=asyncio.get_event_loop(),
        )
        logger.info("Background async connection pool initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize background async pool: {e}")
        raise


@asynccontextmanager
async def get_mysql_conn_ctx(db_name, no_standardize=False):
    conn = None
    try:
        db_name = standardize_db_name(db_name) if not no_standardize else db_name
        logger.info(f"[POOL-ACQUIRE] Acquiring connection for {db_name}")
        conn = await asyncio.wait_for(async_connection_pool.acquire(), timeout=5)

        async with conn.cursor() as cursor:
            await cursor.execute(f"USE `{db_name}`")

        yield conn

    except asyncio.TimeoutError:
        logger.error(f"[POOL TIMEOUT] Could not acquire connection for DB: {db_name}")
        raise
    finally:
        if conn:
            async_connection_pool.release(conn)
            logger.info(f"[POOL-RELEASE] Released connection for {db_name} | free={async_connection_pool.freesize}/{async_connection_pool.size}")




### Background Thread for Async Cleanup ###
async def clean_idle_connections_with_background_pool(idle_time_threshold=3):
    """
    Cleans idle 'Sleep' connections using the dedicated background pool.
    """
    try:
        if not background_async_pool:
            raise RuntimeError("Background async pool is not initialized")

        async with background_async_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                query = f"""
                SELECT id, time FROM performance_schema.processlist 
                WHERE command = 'Sleep' AND time > {idle_time_threshold};
                """
                await cursor.execute(query)
                idle_connections = await cursor.fetchall()

                if idle_connections:
                    for connection_id, _ in idle_connections:
                        await cursor.execute(f"KILL {connection_id};")
                    logger.info(f"Killed {len(idle_connections)} idle connections.")
                return {"status": "success", "killed": len(idle_connections)}

    except Exception as e:
        logger.error(f"Background cleanup error: {e}")
        return {"status": "error", "error": str(e)}


def start_background_cleanup_thread():
    global background_cleanup_thread

    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(initialize_background_pool())

        async def loop_cleanup():
            while not shutdown_event.is_set():
                try:
                    await clean_idle_connections_with_background_pool()
                except Exception as e:
                    logger.error(f"Error in background cleanup loop: {e}")
                await asyncio.sleep(30)

        loop.run_until_complete(loop_cleanup())

    background_cleanup_thread = threading.Thread(target=runner, daemon=True)
    background_cleanup_thread.start()




async def debug_pending_tasks():
    """
    Logs details of all pending tasks during shutdown for debugging.
    Compatible with Python 3.7 (no get_coro or get_name).
    """
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        print(f"Pending tasks during shutdown ({len(tasks)}):")
        for task in tasks:
            try:
                print(f" - Task: {task!r}")
            except Exception as e:
                print(f"   [Error printing task]: {e}")



async def run_async_update_prices():
    """Runs the async updatePrices function within the event loop."""
    await updatePrices()


def set_configs(config):
    global DATA_PATH, apiUrl, FLO_DATA_DIR, API_VERIFY, debug_status
    global APIHOST, APIPORT, APP_ADMIN, NET, is_testnet

    # Directories and data path
    DATA_PATH = config.get("API", "dbfolder", fallback="")
    FLO_DATA_DIR = config.get("API", "FLO_DATA_DIR", fallback="")

    # API URL (with trailing slash removed)
    raw_api_url = config.get("API", "LOCALADDRESSINDEXERURL", fallback="https://blockbook.flocard.app/api/")
    apiUrl = raw_api_url.rstrip('/')

    # API behavior flags
    API_VERIFY = config.getboolean("DEFAULT", "API_VERIFY", fallback=True)
    debug_status = config.getboolean("API", "debug_status", fallback=False)

    # Host/port settings
    APIHOST = config.get("API", "HOST", fallback="0.0.0.0")
    APIPORT = config.getint("API", "PORT", fallback=5017)

    # Network/admin
    APP_ADMIN = config.get("DEFAULT", "APP_ADMIN", fallback="")
    NET = config.get("DEFAULT", "NET", fallback="mainnet").strip().lower()
    is_testnet = NET == 'testnet'
    print(f"[CONFIG] is_testnet = {is_testnet}")





### Lifecycle Hooks ###
@app.before_serving
async def before_serving():
    """
    Initializes resources before the server starts serving requests.
    Ensures cleanup runs continuously.
    """
    global async_connection_pool
    print("Initializing resources before serving requests...")

    try:
        start_background_cleanup_thread()

        # Initialize connection pools
        await initialize_connection_pools()

        # Initialize database schema
        await initialize_db()

        # Start dynamic pool adjustment
        #asyncio.create_task(adjust_pool_size())

        # Schedule periodic jobs (price updates, but cleanup runs separately)
        scheduler.add_job(run_async_update_prices, trigger="interval", seconds=600)
        scheduler.start()

        print("Resources initialized.")
    
    except Exception as e:
        logger.error(f"Exception during before_serving(): {e}", exc_info=True)
        raise  # Reraise to ensure Hypercorn sees the error



@app.after_serving
async def after_serving():
    
    logger.info("after_serving() starting...")
    try:
        await shutdown_resources()
        logger.info("after_serving() completed successfully.")
    except Exception as e:
        logger.error(f"after_serving() failed: {e}", exc_info=True)

async def shutdown_resources():
    logger.info("shutdown_resources(): starting cleanup...")
    try:
        shutdown_event.set()

        if scheduler.running:
            logger.info("shutdown_resources(): stopping scheduler...")
            scheduler.shutdown(wait=False)

        if async_connection_pool:
            logger.info("shutdown_resources(): closing async connection pool...")
            async_connection_pool.close()
            try:
                await asyncio.wait_for(async_connection_pool.wait_closed(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("Timeout while waiting for async pool to close.")
            except asyncio.CancelledError:
                logger.warning("async pool shutdown cancelled by event loop")

        if background_async_pool:
            logger.info("shutdown_resources(): closing background async pool...")
            background_async_pool.close()
            try:
                await asyncio.wait_for(background_async_pool.wait_closed(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("Timeout while waiting for background pool to close.")
            except asyncio.CancelledError:
                logger.warning("background pool shutdown cancelled by event loop")

        if background_cleanup_thread and background_cleanup_thread.is_alive():
            logger.info("shutdown_resources(): background thread is alive. Waiting briefly...")
            await asyncio.sleep(1)

        logger.info("shutdown_resources(): all cleanup complete.")

    except Exception as e:
        logger.error(f"Error during shutdown cleanup: {e}", exc_info=True)






async def shutdown(loop, signal=None):
    global shutdown_triggered
    if shutdown_triggered:
        return
    shutdown_triggered = True

    if signal:
        print(f"Received exit signal {signal.name}. Shutting down...")

    await debug_pending_tasks()
    await shutdown_resources()

    tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task(loop)]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    print("Cleanup done. Ready to exit.")





async def get_mysql_connection(db_name, no_standardize=False, USE_ASYNC=True):
    db_name = standardize_db_name(db_name) if not no_standardize else db_name

    try:
        if USE_ASYNC:
            if not async_connection_pool:
                raise RuntimeError("Async connection pool not initialized")

            conn = await async_connection_pool.acquire()
            if conn is None:
                raise RuntimeError("Failed to acquire a valid async connection")

            async with conn.cursor() as cursor:
                await cursor.execute(f"USE `{db_name}`")
            return conn

        else:
            if not sync_connection_pool:
                raise RuntimeError("Sync connection pool not initialized")

            conn = sync_connection_pool.connection()
            if conn is None:
                raise RuntimeError("Failed to acquire a valid sync connection")

            conn.select_db(db_name)
            return conn

    except Exception as e:
        logger.error(f"Error in database connection: {e}")
        raise




def is_backend_ready():
    """
    Dummy function that always indicates the backend is ready.
    """
    return True


#MYSQL ENHANCEMENTS END




INTERNAL_ERROR = "Unable to process request, try again later"
BACKEND_NOT_READY_ERROR = "Server is still syncing, try again later!"
BACKEND_NOT_READY_WARNING = "Server is still syncing, data may not be final"

# Global values and configg
internalTransactionTypes = [ 'tokenswapDepositSettlement', 'tokenswapParticipationSettlement', 'smartContractDepositReturn','tokenswapRefund']


# Validation functionss
def check_flo_address(address):
    base58_chars = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    regex = r"^[Feo][" + base58_chars + r"]{33,34}$"
    return re.match(regex, address) is not None


def check_integer(value):
    return str.isdigit(value)


async def get_latest_scanned_blockheight():
    try:
        async with get_mysql_conn_ctx("system") as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT value FROM systemData WHERE attribute='lastblockscanned'")
                row = await cursor.fetchone()
                if row:
                    return int(row[0])
    except Exception as e:
        logger.error(f"Failed to fetch latest blockheight: {e}")
    return None


async def blockdetailhelper(blockdetail):
    # Determine whether the input is blockHash or blockHeight
    if blockdetail.isdigit():
        blockHash = None
        blockHeight = int(blockdetail)
    else:
        blockHash = str(blockdetail)
        blockHeight = None

    try:
        # Use async context manager for proper release
        async with get_mysql_conn_ctx("latestCache") as conn:
            async with conn.cursor() as cursor:
                # Query the database based on blockHash or blockHeight
                if blockHash:
                    query = "SELECT jsonData FROM latestBlocks WHERE blockHash = %s"
                    await cursor.execute(query, (blockHash,))
                elif blockHeight:
                    query = "SELECT jsonData FROM latestBlocks WHERE blockNumber = %s"
                    await cursor.execute(query, (blockHeight,))
                else:
                    raise ValueError("Invalid blockdetail input. Must be blockHash or blockHeight.")

                result = await cursor.fetchall()

    except aiomysql.MySQLError as e:
        print(f"Error querying database: {e}")
        result = []

    return result

async def transactiondetailhelper(transactionHash):
    try:
        async with get_mysql_conn_ctx("latestCache") as conn:
            async with conn.cursor() as cursor:
                query = """
                    SELECT jsonData, parsedFloData, transactionType, db_reference 
                    FROM latestTransactions 
                    WHERE transactionHash = %s
                """
                await cursor.execute(query, (transactionHash,))
                transactionJsonData = await cursor.fetchall()

    except aiomysql.MySQLError as e:
        print(f"Error querying database: {e}")
        transactionJsonData = []

    return transactionJsonData

transaction_confirmation_cache = {}

async def update_transaction_confirmations(transactionJson, current_tip_height=None):
    txid = transactionJson.get('txid')

    # ✅ Cached result
    if txid in transaction_confirmation_cache:
        transactionJson['confirmations'] = transaction_confirmation_cache[txid]
        return transactionJson

    # ✅ Use blockheight-based calculation if possible
    tx_blockheight = transactionJson.get("blockheight")
    if current_tip_height is not None and tx_blockheight is not None:
        confirmations = max(current_tip_height - tx_blockheight + 1, 0)
        transactionJson['confirmations'] = confirmations
        transaction_confirmation_cache[txid] = confirmations
        return transactionJson

    # 🛡️ Guard: Do NOT call API for off-chain transactions
    if not transactionJson.get("onChain", False):
        transactionJson['confirmations'] = 1100  # fallback value for off-chain without blockheight
        return transactionJson

    # 🔗 Fallback to API call only for on-chain tx without blockheight info
    url = f"{apiUrl}/api/v1/tx/{txid}"
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for retry in range(1):
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            response_data = await response.json()
                            confirmations = response_data.get('confirmations', 0)
                            transactionJson['confirmations'] = confirmations
                            transaction_confirmation_cache[txid] = confirmations
                            return transactionJson
                        print(f"API error: {response.status} for txid: {txid}")
                except asyncio.TimeoutError:
                    await asyncio.sleep(2 ** retry)
                    continue
            print(f"⚠️ Skipping transaction {txid} due to repeated timeouts. CASE 3")
    except Exception as e:
        print(f"🔥 Error fetching transaction confirmation for {txid}: {e}")

    return transactionJson

async def smartcontract_morph_helper(smart_contracts):
    contractList = []
    for contract in smart_contracts:
        cdict = {
            'contractName': contract[1],
            'contractAddress': contract[2],
            'status': contract[3],
            'contractType': contract[5],
            'transactionHash': contract[6],
            'blockNumber': contract[7],
            'incorporationDate': contract[8]
        }

        if cdict['contractType'] in ['continuous-event', 'continuos-event']:
            cdict['contractSubType'] = 'tokenswap'
            tokens = ast.literal_eval(contract[4])
            cdict['acceptingToken'], cdict['sellingToken'] = tokens[0], tokens[1]
            
            structure = await fetchContractStructure(cdict['contractName'], cdict['contractAddress'])
            if structure.get('pricetype') == 'dynamic':
                if 'oracle_address' in structure:
                    cdict['oracle_address'] = structure['oracle_address']
                    cdict['price'] = await fetch_dynamic_swap_price(structure, {'time': datetime.now().timestamp()})
            else:
                cdict['price'] = structure.get('price')

        elif cdict['contractType'] == 'one-time-event':
            cdict['tokenIdentification'] = contract[4]
            structure = await fetchContractStructure(cdict['contractName'], cdict['contractAddress'])
            
            if 'payeeAddress' in structure:
                cdict['contractSubType'] = 'time-trigger'
            else:
                cdict.update({
                    'userChoices': list(structure['exitconditions'].values()),
                    'contractSubType': 'external-trigger',
                    'expiryDate': contract[9]
                })
            cdict['closeDate'] = contract[10]

        contractList.append(cdict)
    return contractList



async def return_smart_contracts(contractName=None, contractAddress=None):
    query = """
        SELECT * FROM activecontracts 
        WHERE id IN (
            SELECT MAX(id) FROM activecontracts GROUP BY contractName, contractAddress
        )
    """
    conditions = []
    params = []

    if contractName:
        conditions.append("contractName = %s")
        params.append(contractName)
    if contractAddress:
        conditions.append("contractAddress = %s")
        params.append(contractAddress)

    if conditions:
        query += " AND " + " AND ".join(conditions)

    try:
        # Only DB work inside connection
        async with get_mysql_conn_ctx("system") as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params)
                smart_contracts = await cursor.fetchall()

        # Connection released here
        return smart_contracts

    except aiomysql.MySQLError as e:
        print(f"Database error in return_smart_contracts: {e}")
        return []

    except Exception as e:
        print(f"Unexpected error in return_smart_contracts: {e}")
        return []




async def fetchContractStructure(contractName, contractAddress):
    db_name = f"{contractName.strip()}_{contractAddress.strip()}"
    try:
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT attribute, value FROM contractstructure')
                rows = await cursor.fetchall()

        structure, exitconds = {}, {}
        for attr, val in rows:
            if attr == 'exitconditions':
                exitconds[len(exitconds)] = val
            else:
                structure[attr] = val
        if exitconds:
            structure['exitconditions'] = exitconds

        # type conversions
        for key in ['contractAmount', 'maximumsubscriptionamount', 'minimumsubscriptionamount', 'price']:
            if key in structure:
                structure[key] = float(structure[key])

        if 'payeeAddress' in structure:
            structure['payeeAddress'] = json.loads(structure['payeeAddress'])

        return structure

    except aiomysql.MySQLError as e:
        print(f"Database error while fetching contract structure: {e}")
        return 0
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 0




async def fetchContractStatus(contractName, contractAddress):
    try:
        async with get_mysql_conn_ctx('system') as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                query = """
                    SELECT status 
                    FROM activecontracts 
                    WHERE contractName = %s AND contractAddress = %s 
                    ORDER BY id DESC 
                    LIMIT 1
                """
                await cursor.execute(query, (contractName, contractAddress))
                result = await cursor.fetchone()

        return result["status"] if result else None

    except aiomysql.MySQLError as e:
        print(f"Database error fetching contract status for {contractName} / {contractAddress}: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error fetching contract status for {contractName} / {contractAddress}: {e}")
        return None





def extract_ip_op_addresses(transactionJson):
    sender_address = transactionJson['vin'][0]['addresses'][0]
    receiver_address = None
    for utxo in transactionJson['vout']:
        if utxo['scriptPubKey']['addresses'][0] == sender_address:
            continue
        receiver_address = utxo['scriptPubKey']['addresses'][0]
    return sender_address, receiver_address


async def updatePrices():
    """
    Updates the latest price data for various currency pairs in the MySQL database asynchronously.
    """
    prices = {}

    # Single session for all API calls
    try:
        async with aiohttp.ClientSession() as session:
            # USD -> INR
            try:
                async with session.get("https://api.exchangerate-api.com/v4/latest/usd", timeout=10) as response:
                    if response.status == 200:
                        price = await response.json()
                        prices['USDINR'] = price['rates']['INR']
            except Exception as e:
                print(f"Error fetching USD to INR exchange rate: {e}")

            # BTC -> USD, INR
            try:
                async with session.get(
                    "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,flo&vs_currencies=usd,inr",
                    timeout=10
                ) as response:
                    if response.status == 200:
                        price = await response.json()
                        prices['BTCUSD'] = price['bitcoin']['usd']
                        prices['BTCINR'] = price['bitcoin']['inr']
            except Exception as e:
                print(f"Error fetching BTC prices: {e}")

            # FLO -> USD, INR
            try:
                async with session.get("https://api.coinlore.net/api/ticker/?id=67", timeout=10) as response:
                    if response.status == 200:
                        price = await response.json()
                        prices["FLOUSD"] = float(price[0]['price_usd'])
                        if 'USDINR' in prices:
                            prices["FLOINR"] = prices["FLOUSD"] * prices['USDINR']
            except Exception as e:
                print(f"Error fetching FLO prices: {e}")

    except Exception as e:
        print(f"Session-level error: {e}")

    print('Prices updated at time: %s' % datetime.now())
    print(prices)

    # Update prices in bulk
    try:
        async with get_mysql_conn_ctx('system') as conn:
            async with conn.cursor() as cursor:
                if prices:
                    values = ", ".join([
                        f"('{pair}', {price})"
                        for pair, price in prices.items()
                    ])
                    query = f"""
                        INSERT INTO ratepairs (ratepair, price)
                        VALUES {values}
                        ON DUPLICATE KEY UPDATE price = VALUES(price);
                    """
                    await cursor.execute(query)
                    await conn.commit()
            print("Prices successfully updated in the database.")
    except aiomysql.MySQLError as e:
        print(f"Database error while updating prices: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")



async def fetch_dynamic_swap_price(contractStructure, blockinfo):
    oracle_address = contractStructure['oracle_address']
    print(f'Oracle address is: {oracle_address}')

    async def send_api_request(url):
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f'API error: {response.status}')
                        return None
        except asyncio.TimeoutError:
            print(f"Request timed out after {API_TIMEOUT} seconds CASE 2")
            return None
        except Exception as e:
            print(f"Error during API request: {e}")
            return None

    try:
        # Fetch transactions associated with the oracle address
        url = f'{apiUrl}/api/v1/addr/{oracle_address}'
        response_data = await send_api_request(url)
        if response_data is None:
            return None

        if 'transactions' not in response_data:
            return float(contractStructure['price'])

        transactions = response_data['transactions']
        for transaction_hash in transactions:
            transaction_url = f'{apiUrl}/api/v1/tx/{transaction_hash}'
            transaction_response = await send_api_request(transaction_url)
            if transaction_response is None:
                continue

            transaction = transaction_response
            floData = transaction.get('floData', None)

            if not floData or transaction['time'] >= blockinfo['time']:
                continue

            try:
                sender_address, receiver_address = find_sender_receiver(transaction)
                assert receiver_address == contractStructure['contractAddress']
                assert sender_address == oracle_address

                floData = json.loads(floData)
                assert floData['price-update']['contract-name'] == contractStructure['contractName']
                assert floData['price-update']['contract-address'] == contractStructure['contractAddress']

                return float(floData['price-update']['price'])
            except Exception as e:
                print(f"Error processing transaction: {e}")
                continue

        # If no matching transaction is found, return the default price
        return float(contractStructure['price'])
    except Exception as e:
        print(f"Error in fetch_dynamic_swap_price: {e}")
        return None



def find_sender_receiver(transaction_data):
    # Create vinlist and outputlist
    vinlist = []
    querylist = []

    #totalinputval = 0
    #inputadd = ''

    # todo Rule 40 - For each vin, find the feeding address and the fed value. Make an inputlist containing [inputaddress, n value]
    for vin in transaction_data["vin"]:
        vinlist.append([vin["addr"], float(vin["value"])])

    totalinputval = float(transaction_data["valueIn"])

    # todo Rule 41 - Check if all the addresses in a transaction on the input side are the same
    for idx, item in enumerate(vinlist):
        if idx == 0:
            temp = item[0]
            continue
        if item[0] != temp:
            print(f"System has found more than one address as part of vin. Transaction {transaction_data['txid']} is rejected")
            return 0

    inputlist = [vinlist[0][0], totalinputval]
    inputadd = vinlist[0][0]

    # todo Rule 42 - If the number of vout is more than 2, reject the transaction
    if len(transaction_data["vout"]) > 2:
        print(f"System has found more than 2 address as part of vout. Transaction {transaction_data['txid']} is rejected")
        return 0

    # todo Rule 43 - A transaction accepted by the system has two vouts, 1. The FLO address of the receiver
    #      2. Flo address of the sender as change address.  If the vout address is change address, then the other adddress
    #     is the recevier address

    outputlist = []
    addresscounter = 0
    inputcounter = 0
    for obj in transaction_data["vout"]:
        if obj["scriptPubKey"]["type"] == "pubkeyhash":
            addresscounter = addresscounter + 1
            if inputlist[0] == obj["scriptPubKey"]["addresses"][0]:
                inputcounter = inputcounter + 1
                continue
            outputlist.append([obj["scriptPubKey"]["addresses"][0], obj["value"]])

    if addresscounter == inputcounter:
        outputlist = [inputlist[0]]
    elif len(outputlist) != 1:
        print(f"Transaction's change is not coming back to the input address. Transaction {transaction_data['txid']} is rejected")
        return 0
    else:
        outputlist = outputlist[0]

    return inputlist[0], outputlist[0]

async def fetch_contract_status_time_info(contractName, contractAddress):
    try:
        # Fetch the raw row as quickly as possible
        async with get_mysql_conn_ctx('system') as conn:
            async with conn.cursor() as cursor:
                query = """
                    SELECT status, incorporationDate, expiryDate, closeDate 
                    FROM activecontracts 
                    WHERE contractName = %s AND contractAddress = %s 
                    ORDER BY id DESC 
                    LIMIT 1
                """
                await cursor.execute(query, (contractName, contractAddress))
                row = await cursor.fetchone()

        # Check result outside the DB context
        return row if row else []

    except aiomysql.MySQLError as e:
        print(f"Database error while fetching contract status and time info: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error: {e}")
        return []


async def transaction_post_processing(transactionJsonData):
    rowarray_list = []
    latest_blockheight = await get_latest_scanned_blockheight()

    for i, row in enumerate(transactionJsonData):
        try:
            parsedFloData = json.loads(row['parsedFloData'])
            transactionDetails = json.loads(row['jsonData'])

            is_internal = (
                row['transactionType'] in internalTransactionTypes or
                (row['transactionType'] == 'trigger' and row.get('transactionSubType') != 'committee')
            )

            if is_internal:
                transactions_object = {
                    'senderAddress': row['sourceFloAddress'],
                    'receiverAddress': row['destFloAddress'],
                    'tokenAmount': row['transferAmount'],
                    'tokenIdentification': row['token'],
                    'contractName': parsedFloData.get('contractName'),
                    'transactionTrigger': transactionDetails.get('txid'),
                    'time': transactionDetails.get('time'),
                    'type': row['transactionType'],
                    'onChain': False
                }
            else:
                # Start with merged fields
                transactions_object = {**parsedFloData, **transactionDetails}
                # Explicitly override critical fields
                transactions_object.update({
                    'senderAddress': row['sourceFloAddress'],
                    'receiverAddress': row['destFloAddress'],
                    'tokenAmount': row['transferAmount'],
                    'tokenIdentification': row['token'],
                    'contractName': parsedFloData.get('contractName'),
                    'type': row['transactionType'],
                    'onChain': True,
                    'transactionTrigger': transactionDetails.get('txid'),
                    'time': transactionDetails.get('time'),
                })

                transactions_object = await update_transaction_confirmations(
                    transactions_object, current_tip_height=latest_blockheight
                )

            rowarray_list.append(transactions_object)

        except Exception as e:
            logger.error(f"Error processing row {i}: {e}")
            continue

    return rowarray_list






def standardize_db_name(db_name):
    """
    Ensures the database name has the proper prefix and suffix.

    Args:
        db_name (str): The logical database name.

    Returns:
        str: The standardized database name.
    """
    if not (db_name.startswith(f"{mysql_config.database_prefix}_") and db_name.endswith("_db")):
        db_name = f"{mysql_config.database_prefix}_{db_name}_db"
    return db_name






async def fetch_transactions_from_token(token_name, floAddress, limit=None):
    """
    Fetch transactions for a specific token and FLO address.
    """
    token_db_name = standardize_db_name(token_name)

    try:
        async with get_mysql_conn_ctx(token_db_name) as conn:
            async with conn.cursor(DictCursor) as cursor:
                query = """
                    SELECT jsonData, parsedFloData, time, transactionType, sourceFloAddress, destFloAddress, 
                           transferAmount, %s AS token, '' AS transactionSubType 
                    FROM transactionHistory 
                    WHERE sourceFloAddress = %s OR destFloAddress = %s
                """
                parameters = [token_name, floAddress, floAddress]

                if limit is not None:
                    query += " LIMIT %s"
                    parameters.append(limit)

                await cursor.execute(query, parameters)
                transactions = await cursor.fetchall()

        # Return data after releasing connection
        return transactions

    except aiomysql.MySQLError as e:
        logger.error(f"Database error in fetch_transactions_from_token for token {token_name}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error in fetch_transactions_from_token for token {token_name}: {e}", exc_info=True)
        return []






async def fetch_token_transactions(tokens, senderFloAddress=None, destFloAddress=None, limit=None, use_and=False):
    """
    Fetch transactions for multiple tokens (or a single token).

    Args:
        tokens (list or str): List of token names or a single token name.
        senderFloAddress (str, optional): Sender FLO address.
        destFloAddress (str, optional): Destination FLO address.
        limit (int, optional): Maximum number of transactions.
        use_and (bool, optional): Use AND or OR for filtering.

    Returns:
        list: Combined list of transactions for all tokens.
    """
    # Automatically convert a single token name into a list
    if isinstance(tokens, str):
        tokens = [tokens]

    if not tokens or not isinstance(tokens, list):
        logger.error("Invalid or missing tokens")
        return []

    try:
        # Log the tokens being processed
        logger.info(f"Fetching transactions for tokens: {tokens}")

        # Fetch transactions in parallel for all tokens
        tasks = [
            fetch_transactions_from_token(
                token,
                senderFloAddress or destFloAddress,  # Use either sender or destination address
                limit
            )
            for token in tokens
        ]

        # Gather results from all tasks
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Combine and process results
        all_transactions = []
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                # Log the error for the specific token
                logger.error(f"Error fetching transactions for token {tokens[idx]}: {result}")
                continue  # Skip this token's transactions
            if isinstance(result, list):
                all_transactions.extend(result)

        # Post-process and return transactions
        return await transaction_post_processing(all_transactions)

    except Exception as e:
        logger.error(f"Unexpected error while fetching token transactions: {e}", exc_info=True)
        return []





async def fetch_token_transactions_for_contract(token_name, sc_db_name, _from, to, USE_ASYNC=True):
    """
    Fetches transactions for a specific token and smart contract database asynchronously.
    """
    token_db_name = standardize_db_name(token_name)

    try:
        # --- Step 1: Fetch contractTransactionHistory from sc_db ---
        async with get_mysql_conn_ctx(sc_db_name) as conn_sc:
            async with conn_sc.cursor(DictCursor) as cursor_sc:
                query_sc = """
                    SELECT transactionHash, transactionSubType, id
                    FROM contractTransactionHistory
                    WHERE id BETWEEN %s AND %s
                """
                await cursor_sc.execute(query_sc, (_from, to))
                sc_transactions = list(await cursor_sc.fetchall())

        if not sc_transactions:
            return []

        # Extract transaction hashes
        transaction_hashes = tuple(tx['transactionHash'] for tx in sc_transactions)
        if not transaction_hashes:
            return []

        # Prepare placeholders for IN clause
        transaction_hashes_placeholder = f"({','.join(['%s'] * len(transaction_hashes))})"

        # --- Step 2: Fetch matching transactions from token_db ---
        async with get_mysql_conn_ctx(token_db_name) as conn_token:
            async with conn_token.cursor(DictCursor) as cursor_token:
                query_token = f"""
                    SELECT jsonData, parsedFloData, time, transactionType, sourceFloAddress, destFloAddress, 
                           transferAmount, '{token_name}' AS token, transactionHash
                    FROM transactionHistory
                    WHERE transactionHash IN {transaction_hashes_placeholder}
                """
                await cursor_token.execute(query_token, transaction_hashes)
                token_transactions = list(await cursor_token.fetchall())

        # --- Step 3: Merge results (outside DB connections) ---
        combined_transactions = []
        for sc_tx in sc_transactions:
            matching_tx = next(
                (tx for tx in token_transactions if tx['transactionHash'] == sc_tx['transactionHash']),
                None
            )
            if matching_tx:
                combined_tx = {**matching_tx, 'transactionSubType': sc_tx['transactionSubType']}
                combined_transactions.append(combined_tx)

        return combined_transactions

    except aiomysql.MySQLError as e:
        logger.error(f"Database error fetching transactions for token {token_name}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error in fetch_token_transactions_for_contract: {e}", exc_info=True)
        return []




async def fetch_contract_transactions(contractName, contractAddress, _from=0, to=100, USE_ASYNC=True):
    """
    Fetches transactions related to a smart contract and associated tokens asynchronously.
    """
    try:
        sc_db_name = standardize_db_name(f"{contractName}_{contractAddress}")

        # Fetch contract structure
        contractStructure = await fetchContractStructure(contractName, contractAddress)
        if not contractStructure:
            return jsonify(description="Invalid contract structure"), 404

        transactionJsonData = []

        # Fetch contract creation transaction
        creation_tx_query = """
            SELECT jsonData, parsedFloData, time, transactionType, sourceFloAddress, destFloAddress, 
                   transferAmount, '' AS token, transactionSubType 
            FROM contractTransactionHistory
            ORDER BY id
            LIMIT 1;
        """

        # Perform DB work and fetch rows as quickly as possible
        async with get_mysql_conn_ctx(sc_db_name) as conn_sc:
            async with conn_sc.cursor(DictCursor) as cursor_sc:
                await cursor_sc.execute(creation_tx_query)
                creation_tx = await cursor_sc.fetchall()

        # Only extend the list after connection is released
        transactionJsonData.extend(list(creation_tx))

        # Fetch token transactions concurrently
        if contractStructure.get('contractType') == 'continuous-event':
            token1 = contractStructure.get('accepting_token')
            token2 = contractStructure.get('selling_token')

            token_results_1, token_results_2 = await asyncio.gather(
                fetch_token_transactions_for_contract(token1, sc_db_name, _from, to, USE_ASYNC),
                fetch_token_transactions_for_contract(token2, sc_db_name, _from, to, USE_ASYNC)
            )

            transactionJsonData.extend(list(token_results_1))
            transactionJsonData.extend(list(token_results_2))

        elif contractStructure.get('contractType') == 'one-time-event':
            token1 = contractStructure.get('tokenIdentification')
            result = await fetch_token_transactions_for_contract(token1, sc_db_name, _from, to, USE_ASYNC)
            transactionJsonData.extend(list(result))

        return await transaction_post_processing(transactionJsonData)

    except aiomysql.MySQLError as e:
        print(f"Database error while fetching contract transactions: {e}")
        return jsonify(description="Database error"), 500

    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify(description="Unexpected error occurred"), 500


async def fetch_swap_contract_transactions(contractName, contractAddress, transactionHash=None):
    try:
        sc_db_name = standardize_db_name(f"{contractName}_{contractAddress}")
        contractStructure = await fetchContractStructure(contractName, contractAddress)
        if not contractStructure:
            return []

        token1 = contractStructure.get('accepting_token')
        token2 = contractStructure.get('selling_token')

        if not token1 or not token2:
            return []

        token1_db = standardize_db_name(token1)
        token2_db = standardize_db_name(token2)

        query = f"""
            SELECT t1.jsonData, t1.parsedFloData, t1.time, t1.transactionType,
                   t1.sourceFloAddress, t1.destFloAddress, t1.transferAmount,
                   '{token1}' AS token, t1.transactionSubType
            FROM contractTransactionHistory AS s
            INNER JOIN {token1_db}.transactionHistory AS t1
                ON t1.transactionHash = s.transactionHash
            WHERE s.transactionHash = %s
            UNION
            SELECT t2.jsonData, t2.parsedFloData, t2.time, t2.transactionType,
                   t2.sourceFloAddress, t2.destFloAddress, t2.transferAmount,
                   '{token2}' AS token, t2.transactionSubType
            FROM contractTransactionHistory AS s
            INNER JOIN {token2_db}.transactionHistory AS t2
                ON t2.transactionHash = s.transactionHash
            WHERE s.transactionHash = %s
        """

        async with get_mysql_conn_ctx(sc_db_name) as conn_sc:
            async with conn_sc.cursor(DictCursor) as cursor:
                await cursor.execute(query, (transactionHash, transactionHash))
                rows = await cursor.fetchall()

        return await transaction_post_processing(rows)

    except aiomysql.MySQLError as e:
        print(f"MySQL error in fetch_swap_contract_transactions: {e}")
        return []

    except Exception as e:
        print(f"Unexpected error in fetch_swap_contract_transactions: {e}")
        return []




def sort_transactions(transactionJsonData):
    transactionJsonData = sorted(transactionJsonData, key=lambda x: x['time'], reverse=True)
    return transactionJsonData

def process_committee_flodata(flodata):
    flo_address_list = []
    try:
        contract_committee_actions = flodata['token-tracker']['contract-committee']
    except KeyError:
        print('Flodata related to contract committee')
    else:
        # Adding first and removing later to maintain consistency and not to depend on floData for order of execution
        for action in contract_committee_actions.keys():
            if action == 'add':
                for floid in contract_committee_actions[f'{action}']:
                    flo_address_list.append(floid)

        for action in contract_committee_actions.keys():
            if action == 'remove':
                for floid in contract_committee_actions[f'{action}']:
                    flo_address_list.remove(floid)
    finally:
        return flo_address_list


async def refresh_committee_list(admin_flo_id, api_url, blocktime):
    committee_list = []
    latest_param = 'true'
    mempool_param = 'false'
    init_id = None

    async def process_transaction(transaction_info):
        if (
            'isCoinBase' in transaction_info
            or transaction_info['vin'][0]['addresses'][0] != admin_flo_id
            or transaction_info['blocktime'] > blocktime
        ):
            return
        try:
            tx_flodata = json.loads(transaction_info['floData'])
            committee_list.extend(process_committee_flodata(tx_flodata))
        except Exception as e:
            print(f"Error processing transaction: {e}")

    async def send_api_request(session, url):
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        try:
            async with session.get(url, ssl=API_VERIFY, timeout=timeout) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print('Response from the Blockbook API failed')
                    raise RuntimeError(f"API request failed with status {response.status}")
        except asyncio.TimeoutError:
            print(f"Request timed out after {API_TIMEOUT} seconds CASE 1")
            return ["timeout"]
        except Exception as e:
            print(f"Error during API request: {e}")
            return None

    async with aiohttp.ClientSession() as session:
        url = f'{api_url}/api/v1/address/{admin_flo_id}?details=txs'
        response = await send_api_request(session, url)
        if response == ["timeout"]:
            return ["timeout"]

        if response is None:
            return []

        for transaction_info in response.get('txs', []):
            await process_transaction(transaction_info)

        while 'incomplete' in response:
            url = (
                f'{api_url}/api/v1/address/{admin_flo_id}/txs'
                f'?latest={latest_param}&mempool={mempool_param}&before={init_id}'
            )
            response = await send_api_request(session, url)
            if response == ["timeout"]:
                return ["timeout"]
            if response is None:
                return []
            for transaction_info in response.get('items', []):
                await process_transaction(transaction_info)
            if 'incomplete' in response:
                init_id = response['initItem']

    return committee_list





@app.route('/')
async def welcome_msg():
    return jsonify('Welcome to RanchiMall FLO API v2')


def get_api_version_from_path(default_version="v2"):
    """
    Universal helper to safely detect API version from request path.
    """
    try:
        path = request.path
        return "v1" if "/v1.0/" in path else "v2"
    except Exception:
        return default_version


@app.route('/api/v1.0/getSystemData', methods=['GET'])
@app.route('/api/v2/info', methods=['GET'])
async def unified_system_info():
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        sys_db = standardize_db_name("system")
        cache_db = standardize_db_name("latestCache")

        async with get_mysql_conn_ctx(sys_db) as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT
                        (SELECT COUNT(DISTINCT tokenAddress) FROM tokenAddressMapping),
                        (SELECT COUNT(DISTINCT token) FROM tokenAddressMapping),
                        (SELECT COUNT(DISTINCT contractName) FROM contractAddressMapping),
                        (SELECT value FROM systemData WHERE attribute='lastblockscanned')
                """)
                t_addr_count, t_count, c_count, lastblock = await cur.fetchone()
                lastblock = int(lastblock)

        async with get_mysql_conn_ctx(cache_db) as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT
                        (SELECT COUNT(DISTINCT blockNumber) FROM latestBlocks),
                        (SELECT COUNT(DISTINCT transactionHash) FROM latestTransactions)
                """)
                block_count, tx_count = await cur.fetchone()

        response = {
            "systemAddressCount": t_addr_count,
            "systemBlockCount": block_count,
            "systemTransactionCount": tx_count,
            "systemSmartContractCount": c_count,
            "systemTokenCount": t_count,
            "lastscannedblock": lastblock
        }

        if version == "v1.0":
            response["result"] = "ok"
            return jsonify(response), 200

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206

        return jsonify(response), 200

    except Exception as e:
        print("unified_system_info:", e)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": INTERNAL_ERROR}
            if version_resp == "v1.0"
            else {"description": INTERNAL_ERROR}
        )
        return jsonify(resp), 500




@app.route('/api/v1.0/broadcastTx/<raw_transaction_hash>', methods=['GET'])
@app.route('/api/v2/broadcastTx/<raw_transaction_hash>', methods=['GET'])
async def unified_broadcastTx(raw_transaction_hash):
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        # Run subprocess
        p1 = subprocess.run(
            ['flo-cli', f"-datadir={FLO_DATA_DIR}", 'sendrawtransaction', raw_transaction_hash],
            capture_output=True
        )
        result_data = {
            "args": p1.args,
            "returncode": p1.returncode,
            "stdout": p1.stdout.decode(),
            "stderr": p1.stderr.decode()
        }

        return jsonify(result_data), 200

    except Exception as e:
        print("unified_broadcastTx:", e)
        version_resp = get_api_version_from_path(default_version="v2")
        if version_resp == "v1.0":
            return jsonify(result="error", description=INTERNAL_ERROR), 500
        else:
            return jsonify(description=INTERNAL_ERROR), 500

@app.route('/api/v1.0/getTokenList', methods=['GET'])
@app.route('/api/v2/tokenList', methods=['GET'])
async def unified_token_list():
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        if not is_backend_ready():
            msg = BACKEND_NOT_READY_ERROR
            return (
                jsonify(result='error', description=msg), 200
                if version == "v1"
                else jsonify(description=msg), 503
            )

        prefix = f"{mysql_config.database_prefix}_"
        suffix = "_db"
        token_list = []

        async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn:
            async with conn.cursor(DictCursor) as cursor:
                await cursor.execute(
                    f"""
                    SELECT SCHEMA_NAME FROM SCHEMATA
                    WHERE SCHEMA_NAME LIKE '{prefix}%'
                      AND SCHEMA_NAME LIKE '%{suffix}'
                    """
                )
                rows = await cursor.fetchall()

        for db in [r["SCHEMA_NAME"] for r in rows]:
            stripped = db[len(prefix):-len(suffix)]
            if stripped in ["latestCache", "system"]:
                continue
            parts = stripped.split('_')
            if len(parts) == 1:
                token_list.append(stripped)

        response = {"tokens": token_list}
        if version == "v1":
            response["result"] = "ok"

        if version == "v2" and not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206

        return jsonify(response), 200

    except Exception as e:
        logger.error(f"unified_token_list error: {e}", exc_info=True)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": INTERNAL_ERROR}
            if version_resp == "v1"
            else {"description": INTERNAL_ERROR}
        )
        return jsonify(resp), 500




@app.route('/api/v1.0/getTokenInfo', methods=['GET'])
@app.route('/api/v2/tokenInfo/<token>', methods=['GET'])
async def unified_token_info(token=None):
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        if version == "v1":
            token = request.args.get('token')

        if not token:
            msg = "token name hasn't been passed" if version == "v1" else "Token name has not been passed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        db_name = standardize_db_name(token)

        try:
            async with get_mysql_conn_ctx(db_name) as conn:
                async with conn.cursor(DictCursor) as cursor:
                    await cursor.execute("SELECT * FROM transactionHistory WHERE id = 1")
                    incorporationRow = await cursor.fetchone()
                    if not incorporationRow:
                        msg = 'Incorporation details not found' if version == "v1" else 'Incorporation record not found'
                        resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
                        return jsonify(resp), 404

                    await cursor.execute("SELECT COUNT(DISTINCT address) AS count FROM activeTable")
                    addr_row = await cursor.fetchone()
                    num_addresses = addr_row['count'] if addr_row else 0

                    await cursor.execute("SELECT MAX(id) AS max_id FROM transactionHistory")
                    tx_row = await cursor.fetchone()
                    num_tx = tx_row['max_id'] if tx_row else 0

                    await cursor.execute("""
                        SELECT contractName, contractAddress, blockNumber, blockHash, transactionHash
                        FROM tokenContractAssociation
                    """)
                    associatedContracts = await cursor.fetchall()

            contracts_list = [
                {
                    'contractName': c['contractName'],
                    'contractAddress': c['contractAddress'],
                    'blockNumber': c['blockNumber'],
                    'blockHash': c['blockHash'],
                    'transactionHash': c['transactionHash'],
                }
                for c in associatedContracts
            ]

            payload = {
                'token': token,
                'incorporationAddress': incorporationRow['sourceFloAddress'],
                'tokenSupply': incorporationRow['transferAmount'],
                'time': incorporationRow['time'],
                'blockchainReference': incorporationRow['blockchainReference'],
                'activeAddress_no': num_addresses,
                'totalTransactions': num_tx,
                'associatedSmartContracts': contracts_list,
            }

            if version == "v1":
                payload["result"] = "ok"

            if not is_backend_ready():
                payload["warning"] = BACKEND_NOT_READY_WARNING
                return jsonify(payload), 206
            return jsonify(payload), 200

        except aiomysql.MySQLError:
            desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "Token database doesn't exist"
            code = 503 if not is_backend_ready() else 404
            resp = {"result": "error", "description": desc} if version == "v1" else {"description": desc}
            return jsonify(resp), code

    except Exception as e:
        logger.error(f"unified_token_info error: {e}", exc_info=True)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": INTERNAL_ERROR}
            if version_resp == "v1"
            else {"description": INTERNAL_ERROR}
        )
        return jsonify(resp), 500




@app.route('/api/v1.0/getTokenTransactions', methods=['GET'])
@app.route('/api/v2/tokenTransactions/<token>', methods=['GET'])
async def unified_token_transactions(token=None):
    try:
        version = get_api_version_from_path(default_version="v2")

        if version == "v1":
            token = request.args.get('token')

        if not token:
            msg = "token name hasn't been passed" if version == "v1" else "Token name has not been passed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        senderFloAddress = request.args.get('senderFloAddress')
        destFloAddress = request.args.get('destFloAddress')

        # Default pagination variables
        limit = None
        offset = 0
        page_size = None
        current_page = None

        if version == "v2":
            if senderFloAddress and not check_flo_address(senderFloAddress):
                return jsonify(description='senderFloAddress validation failed'), 400
            if destFloAddress and not check_flo_address(destFloAddress):
                return jsonify(description='destFloAddress validation failed'), 400

            use_AND = request.args.get('use_AND')
            if use_AND not in [None, 'True', 'False']:
                return jsonify(description='use_AND validation failed'), 400

            # PATCH HERE: unified param names
            page = max(int(request.args.get('page', 1)), 1)
            entrycount = request.args.get('entrycount')
            if entrycount and not entrycount.isdigit():
                return jsonify(description='entrycount validation failed'), 400
            entrycount = int(entrycount) if entrycount else 100
            limit = entrycount
            offset = (page - 1) * entrycount
            page_size = entrycount
            current_page = page

        else:
            entrycount_param = request.args.get('entrycount')
            offset_param = request.args.get('offset')
            page_param = request.args.get('page')

            if entrycount_param and not entrycount_param.isdigit():
                return jsonify(result='error', description='entrycount validation failed'), 400
            if offset_param and not offset_param.isdigit():
                return jsonify(result='error', description='offset validation failed'), 400
            if page_param and not page_param.isdigit():
                return jsonify(result='error', description='page validation failed'), 400

            limit = int(entrycount_param) if entrycount_param else None
            offset = int(offset_param) if offset_param else 0
            page = int(page_param) if page_param else None

            if page and limit:
                offset = (page - 1) * limit
            offset = max(offset, 0)

        token_db = standardize_db_name(token)

        # Build conditions
        conditions = []
        params = []

        if senderFloAddress and destFloAddress:
            if version == "v2" and use_AND == 'True':
                conditions.append("sourceFloAddress = %s AND destFloAddress = %s")
                params += [senderFloAddress, destFloAddress]
            else:
                conditions.append("sourceFloAddress = %s")
                conditions.append("destFloAddress = %s")
                params += [senderFloAddress, destFloAddress]
        elif senderFloAddress:
            conditions.append("sourceFloAddress = %s")
            params.append(senderFloAddress)
        elif destFloAddress:
            conditions.append("destFloAddress = %s")
            params.append(destFloAddress)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        if not conditions and version == "v2":
            where_clause = " WHERE 1=1"

        # Count total rows
        count_query = f"SELECT COUNT(*) as total FROM transactionHistory{where_clause}"
        async with get_mysql_conn_ctx(token_db) as conn:
            async with conn.cursor(DictCursor) as cursor:
                await cursor.execute(count_query, tuple(params))
                count_row = await cursor.fetchone()
                total_rows = count_row["total"] if count_row else 0

        # Compute paging
        if version == "v1":
            page_size = limit if limit else total_rows
            current_page = page if page else 1
        else:
            page_size = limit
            current_page = current_page

        total_pages = (total_rows // page_size) + (1 if total_rows % page_size else 0) if page_size else 1

        # Final SELECT
        query = f"""
            SELECT jsonData, parsedFloData
            FROM transactionHistory
            {where_clause}
            ORDER BY id DESC
        """

        if limit is not None:
            query += " LIMIT %s OFFSET %s"
            params += [limit, offset]

        # Run query
        async with get_mysql_conn_ctx(token_db) as conn:
            async with conn.cursor(DictCursor) as cursor:
                await cursor.execute(query, tuple(params))
                rows = await cursor.fetchall()

        # Transform
        transactions = [
            {
                'transactionDetails': json.loads(row['jsonData']),
                'parsedFloData': json.loads(row['parsedFloData'])
            }
            for row in rows
        ]

        # Build payload
        if version == "v1":
            tx_dict = {t["transactionDetails"]["txid"]: t for t in transactions}
            payload = {"result": "ok", "token": token, "transactions": tx_dict}
        else:
            payload = {"token": token, "transactions": transactions}

        payload.update({
            "total_rows": total_rows,
            "total_pages": total_pages,
            "page_size": page_size,
            "current_page": current_page,
        })

        if not is_backend_ready():
            payload["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(payload), 206

        return jsonify(payload), 200

    except aiomysql.MySQLError as e:
        print(f"Database error in unified_token_transactions: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {"result": "error", "description": "Database error occurred"} if version_resp == "v1" else {"description": "Database error occurred"}
        return jsonify(resp), 500
    except Exception as e:
        print(f"unified_token_transactions error: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {"result": "error", "description": INTERNAL_ERROR} if version_resp == "v1" else {"description": INTERNAL_ERROR}
        return jsonify(resp), 500




@app.route('/api/v1.0/getTokenBalances', methods=['GET'])
@app.route('/api/v2/tokenBalances/<token>', methods=['GET'])
async def unified_token_balances(token=None):
    try:
        version = get_api_version_from_path(default_version="v2")

        if version == "v1":
            token = request.args.get('token')

        if not token:
            msg = "token name hasn't been passed" if version == "v1" else "Token name has not been passed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        # Default pagination vars
        limit = None
        offset = 0
        page_size = None
        current_page = None

        # Unified pagination logic
        if version == "v2":
            page = max(int(request.args.get('page', 1)), 1)
            entrycount = request.args.get('entrycount')
            if entrycount and not entrycount.isdigit():
                return jsonify(description='entrycount validation failed'), 400
            entrycount = int(entrycount) if entrycount else 100
            limit = entrycount
            offset = (page - 1) * entrycount
            page_size = entrycount
            current_page = page
        else:
            entrycount_param = request.args.get('entrycount')
            offset_param = request.args.get('offset')
            page_param = request.args.get('page')

            if entrycount_param and not entrycount_param.isdigit():
                return jsonify(result='error', description='entrycount validation failed'), 400
            if offset_param and not offset_param.isdigit():
                return jsonify(result='error', description='offset validation failed'), 400
            if page_param and not page_param.isdigit():
                return jsonify(result='error', description='page validation failed'), 400

            limit = int(entrycount_param) if entrycount_param else None
            offset = int(offset_param) if offset_param else 0
            page = int(page_param) if page_param else None

            if page and limit:
                offset = (page - 1) * limit
            offset = max(offset, 0)

        token_db = standardize_db_name(token)

        # Total count of rows
        total_rows = 0
        async with get_mysql_conn_ctx(token_db) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT COUNT(DISTINCT address) FROM activeTable")
                total_rows = (await cursor.fetchone())[0]

        # Compute total pages
        if limit:
            total_pages = (total_rows // limit) + (1 if total_rows % limit else 0)
            page_size = limit
            current_page = (offset // limit) + 1 if limit else 1
        else:
            total_pages = 1
            page_size = total_rows
            current_page = 1

        # Main query with LIMIT/OFFSET
        sql = """
            SELECT address, SUM(transferBalance)
            FROM activeTable
            GROUP BY address
            ORDER BY address
        """

        params = []
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])

        try:
            async with get_mysql_conn_ctx(token_db) as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(sql, params)
                    rows = await cursor.fetchall()

            balances = {
                address: float(balance) if balance is not None else 0.0
                for address, balance in rows
            }

        except aiomysql.MySQLError as e:
            print(f"Database error while fetching balances for token {token}: {e}")
            desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "Token database doesn't exist"
            code = 503 if not is_backend_ready() else 404
            resp = {"result": "error", "description": desc} if version == "v1" else {"description": desc}
            return jsonify(resp), code

        response = {
            "token": token,
            "balances": balances,
            "total_rows": total_rows,
            "total_pages": total_pages,
            "page_size": page_size,
            "current_page": current_page,
        }

        if version == "v1":
            response["result"] = "ok"

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206

        return jsonify(response), 200

    except Exception as e:
        print(f"unified_token_balances error: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": INTERNAL_ERROR}
            if version_resp == "v1"
            else {"description": INTERNAL_ERROR}
        )
        return jsonify(resp), 500







# FLO Address APIs
@app.route('/api/v1.0/getFloAddressInfo', methods=['GET'])
@app.route('/api/v2/floAddressInfo/<floAddress>', methods=['GET'])
async def unified_flo_address_info(floAddress=None):
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        if version == "v1":
            floAddress = request.args.get('floAddress')

        if not floAddress:
            msg = "floAddress hasn't been passed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        if version == "v2" and not check_flo_address(floAddress):
            return jsonify(description="floAddress validation failed"), 400

        system_db = standardize_db_name("system")
        detail_list = {}
        incorporated_contracts = []

        async with get_mysql_conn_ctx(system_db) as conn:
            async with conn.cursor(DictCursor) as cursor:
                await cursor.execute("""
                    SELECT DISTINCT token
                    FROM tokenAddressMapping
                    WHERE tokenAddress = %s
                """, (floAddress,))
                token_names = [row["token"] for row in await cursor.fetchall()]

                await cursor.execute("""
                    SELECT contractName, status, tokenIdentification, contractType,
                           transactionHash, blockNumber, blockHash
                    FROM activecontracts
                    WHERE contractAddress = %s
                """, (floAddress,))
                contracts = await cursor.fetchall()

        if token_names:
            tasks = [fetch_token_balance(token, floAddress) for token in token_names]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for token, res in zip(token_names, results):
                if isinstance(res, Exception):
                    print(f"Error fetching balance for token {token}: {res}")
                else:
                    detail_list.update(res)
        else:
            desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "FLO address is not associated with any tokens"
            code = 503 if not is_backend_ready() else 404
            resp = {"result": "error", "description": desc} if version == "v1" else {"description": desc}
            return jsonify(resp), code

        for c in contracts:
            incorporated_contracts.append({
                "contractName": c["contractName"],
                "contractAddress": floAddress,
                "status": c["status"],
                "tokenIdentification": c["tokenIdentification"],
                "contractType": c["contractType"],
                "transactionHash": c["transactionHash"],
                "blockNumber": c["blockNumber"],
                "blockHash": c["blockHash"],
            })

        response = {
            "floAddress": floAddress,
            "floAddressBalances": detail_list or None,
            "incorporatedSmartContracts": incorporated_contracts or None
        }
        if version == "v1":
            response["result"] = "ok"

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except Exception as e:
        print(f"unified_flo_address_info: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {"result": "error", "description": INTERNAL_ERROR} if version_resp == "v1" else {"description": INTERNAL_ERROR}
        return jsonify(resp), 500



async def fetch_token_balance(token_name, floAddress):
    """
    Fetches the balance for a specific token in the provided token database.

    Args:
        token_name (str): The name of the token.
        floAddress (str): The FLO address to fetch the balance for.

    Returns:
        dict: A dictionary with the token balance.
    """
    try:
        token_db_name = standardize_db_name(token_name)
        
        # Use `async with` for resource management
        async with get_mysql_conn_ctx(token_db_name) as conn_token:
            async with conn_token.cursor() as cursor_token:

                # Fetch balance for the token asynchronously
                query = "SELECT SUM(transferBalance) FROM activeTable WHERE address = %s"
                await cursor_token.execute(query, (floAddress,))
                balance = (await cursor_token.fetchone())[0] or 0

        return {token_name: {'balance': balance, 'token': token_name}}

    except Exception as e:
        print(f"Error fetching balance for token {token_name}: {e}")
        return {token_name: {'balance': 0, 'token': token_name}}


@app.route('/api/v1.0/getFloAddressBalance', methods=['GET'])
@app.route('/api/v2/floAddressBalance/<floAddress>', methods=['GET'])
async def unified_flo_address_balance(floAddress=None):
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        if version == "v1":
            floAddress = request.args.get('floAddress')

        if not floAddress:
            msg = "floAddress hasn't been passed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        if version == "v2" and not check_flo_address(floAddress):
            return jsonify(description="floAddress validation failed"), 400

        token = request.args.get('token')

        if not token:
            async with get_mysql_conn_ctx(standardize_db_name("system")) as conn:
                async with conn.cursor(DictCursor) as cursor:
                    await cursor.execute("""
                        SELECT DISTINCT token
                        FROM tokenAddressMapping
                        WHERE tokenAddress = %s
                    """, (floAddress,))
                    token_names = [row["token"] for row in await cursor.fetchall()]

            if not token_names:
                desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "FLO address is not associated with any tokens"
                code = 503 if not is_backend_ready() else 404
                resp = {"result": "error", "description": desc} if version == "v1" else {"description": desc}
                return jsonify(resp), code

            tasks = [fetch_token_balance(tkn, floAddress) for tkn in token_names]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            detail_list = {}
            for tkn, res in zip(token_names, results):
                if isinstance(res, Exception):
                    print(f"Error fetching balance for token {tkn}: {res}")
                else:
                    detail_list.update(res)

            response = {
                "floAddress": floAddress,
                "floAddressBalances": detail_list
            }
            if version == "v1":
                response["result"] = "ok"

        else:
            token_db_name = standardize_db_name(token)
            try:
                async with get_mysql_conn_ctx(token_db_name) as conn:
                    async with conn.cursor(DictCursor) as cursor:
                        await cursor.execute("""
                            SELECT SUM(transferBalance) AS balance
                            FROM activeTable
                            WHERE address = %s
                        """, (floAddress,))
                        result = await cursor.fetchone()
                        balance = float(result["balance"]) if result and result["balance"] is not None else 0.0
            except aiomysql.MySQLError as e:
                print(f"Database error in unified_flo_address_balance: {e}")
                desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "Token database doesn't exist"
                code = 503 if not is_backend_ready() else 404
                resp = {"result": "error", "description": desc} if version == "v1" else {"description": desc}
                return jsonify(resp), code

            response = {
                "floAddress": floAddress,
                "token": token,
                "balance": balance
            }
            if version == "v1":
                response["result"] = "ok"

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except Exception as e:
        print(f"unified_flo_address_balance: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": INTERNAL_ERROR}
            if version_resp == "v1"
            else {"description": INTERNAL_ERROR}
        )
        return jsonify(resp), 500


@app.route('/api/v1.0/getFloAddressTransactions', methods=['GET'])
@app.route('/api/v2/floAddressTransactions/<floAddress>', methods=['GET'])
async def unified_flo_address_transactions(floAddress=None):
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        if version == "v1":
            floAddress = request.args.get('floAddress')

        if not floAddress:
            msg = "floAddress has not been passed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        if version == "v2" and not check_flo_address(floAddress):
            return jsonify(description="floAddress validation failed"), 400

        limit = request.args.get('limit')
        if limit is not None and not check_integer(limit):
            msg = "limit validation failed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        token = request.args.get('token')
        all_transaction_list = []

        if token is None:
            async with get_mysql_conn_ctx(standardize_db_name("system")) as conn:
                async with conn.cursor(DictCursor) as cursor:
                    await cursor.execute("""
                        SELECT DISTINCT token
                        FROM tokenAddressMapping
                        WHERE tokenAddress = %s
                    """, (floAddress,))
                    token_names = [row["token"] for row in await cursor.fetchall()]

            if not token_names:
                desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "No tokens associated with the FLO address"
                code = 503 if not is_backend_ready() else 404
                resp = {"result": "error", "description": desc} if version == "v1" else {"description": desc}
                return jsonify(resp), code

            tasks = [
                fetch_token_transactions(name, floAddress, floAddress, limit)
                for name in token_names
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, list):
                    all_transaction_list.extend(res)
                else:
                    print(f"Error fetching transactions: {res}")
        else:
            all_transaction_list = await fetch_token_transactions(token, floAddress, floAddress, limit)

        sorted_transactions = sort_transactions(all_transaction_list)

        if version == "v1":
            tx_dict = {tx["txid"]: tx for tx in sorted_transactions}
            response = {
                "result": "ok",
                "floAddress": floAddress,
                "transactions": tx_dict,
            }
        else:
            response = {
                "floAddress": floAddress,
                "transactions": sorted_transactions,
            }

        if token:
            response["token"] = token

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except aiomysql.MySQLError as e:
        print(f"Database error in unified_flo_address_transactions: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": "Database error occurred"}
            if version_resp == "v1"
            else {"description": "Database error occurred"}
        )
        return jsonify(resp), 500
    except Exception as e:
        print(f"unified_flo_address_transactions: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": INTERNAL_ERROR}
            if version_resp == "v1"
            else {"description": INTERNAL_ERROR}
        )
        return jsonify(resp), 500


async def fetch_transactions_for_token(token_db_name, floAddress, limit):
    """
    Fetches transactions for a specific token database.

    Args:
        token_db_name (str): The token database name.
        floAddress (str): The FLO address to filter transactions.
        limit (int): The limit for the number of transactions.

    Returns:
        list: List of transactions for the token.
    """
    try:
        async with get_mysql_conn_ctx(token_db_name) as conn_token:
            async with conn_token.cursor(DictCursor) as cursor_token:
                # Build query to fetch transactions
                query = """
                    SELECT jsonData, parsedFloData 
                    FROM transactionHistory 
                    WHERE sourceFloAddress = %s OR destFloAddress = %s
                    ORDER BY id DESC
                """
                params = (floAddress, floAddress)

                if limit:
                    query += " LIMIT %s"
                    params = (floAddress, floAddress, int(limit))

                await cursor_token.execute(query, params)
                return await cursor_token.fetchall()

    except Exception as e:
        print(f"Error fetching transactions for token {token_db_name}: {e}")
        return []



    

# SMART CONTRACT APIs
@app.route('/api/v1.0/getSmartContractList', methods=['GET'])
@app.route('/api/v2/smartContractList', methods=['GET'])
async def unified_smart_contract_list():
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if contractName:
            contractName = contractName.strip().lower()

        if contractAddress:
            contractAddress = contractAddress.strip()
            if version == "v2" and not check_flo_address(contractAddress):
                return jsonify(description="contractAddress validation failed"), 400

        system_db = standardize_db_name("system")
        query = "SELECT * FROM activecontracts"
        conditions, params = [], []

        if contractName:
            conditions.append("contractName = %s")
            params.append(contractName)
        if contractAddress:
            conditions.append("contractAddress = %s")
            params.append(contractAddress)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        async with get_mysql_conn_ctx(system_db) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(query, tuple(params))
                contracts = await cursor.fetchall()

        if version == "v1":
            contractList = []
            for c in contracts:
                contractDict = {
                    'contractName': c[1],
                    'contractAddress': c[2],
                    'status': c[3],
                    'tokenIdentification': c[4],
                    'contractType': c[5],
                    'transactionHash': c[6],
                    'blockNumber': c[7],
                    'incorporationDate': c[8],
                    'expiryDate': c[9] if c[9] else None,
                    'closeDate': c[10] if c[10] else None,
                }
                contractList.append({k: v for k, v in contractDict.items() if v is not None})

            response = {"smartContracts": contractList, "result": "ok"}
        else:
            smart_contracts_morphed = await smartcontract_morph_helper(contracts)
            committeeAddressList = await refresh_committee_list(APP_ADMIN, apiUrl, int(time.time()))
            response = {
                "smartContracts": smart_contracts_morphed,
                "smartContractCommittee": committeeAddressList,
            }

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except Exception as e:
        print(f"unified_smart_contract_list: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {
            "result": "error",
            "description": INTERNAL_ERROR
        } if version_resp == "v1" else {
            "description": "Unexpected error occurred"
        }
        return jsonify(resp), 500


    
@app.route('/api/v1.0/getSmartContractInfo', methods=['GET'], endpoint='getSmartContractInfoV1')
@app.route('/api/v2/smartContractInfo', methods=['GET'], endpoint='getSmartContractInfoV2')
async def unified_get_smart_contract_info():
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        for param, val in [("contractName", contractName), ("contractAddress", contractAddress)]:
            if not val:
                msg = f"Smart Contract's {param} hasn't been passed"
                return jsonify(result='error', description=msg), 400

        contract_db = standardize_db_name(f"{contractName}_{contractAddress}")
        system_db = standardize_db_name("system")

        # Fetch contract structure
        try:
            async with get_mysql_conn_ctx(contract_db) as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT attribute, value FROM contractstructure")
                    rows = await cursor.fetchall()
        except Exception as e:
            err_msg = f"Failed to fetch contract structure: {e}"
            print(err_msg)
            return jsonify(result='error', description=err_msg), 500

        if not rows:
            msg = "No contract structure found for the specified smart contract"
            return jsonify(result='error', description=msg), 404

        contract_info = {}
        exit_conditions = {}
        for idx, (attribute, value) in enumerate(rows):
            if attribute == 'exitconditions':
                exit_conditions[idx] = value
            else:
                contract_info[attribute] = value

        if exit_conditions:
            contract_info['userChoice'] = exit_conditions

        if version == "v2":
            async with get_mysql_conn_ctx(contract_db) as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT COUNT(participantAddress), SUM(tokenAmount) FROM contractparticipants")
                    participants = await cursor.fetchone()
                    contract_info['numberOfParticipants'] = participants[0]
                    contract_info['tokenAmountDeposited'] = participants[1]

            async with get_mysql_conn_ctx(system_db) as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("""
                        SELECT status, incorporationDate, expiryDate, closeDate
                        FROM activecontracts
                        WHERE contractName = %s AND contractAddress = %s
                    """, (contractName, contractAddress))
                    row = await cursor.fetchone()
                    if row:
                        contract_info.update({
                            "status": row[0],
                            "incorporationDate": row[1],
                            "expiryDate": row[2],
                            "closeDate": row[3],
                        })

            if contract_info.get('status') == 'closed' and contract_info.get('contractType') == 'one-time-event':
                async with get_mysql_conn_ctx(contract_db) as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute("""
                            SELECT transactionSubType
                            FROM contractTransactionHistory
                            WHERE transactionType = 'trigger'
                        """)
                        triggers = await cursor.fetchall()

                        if len(triggers) == 1:
                            trigger_type = triggers[0][0]
                            contract_info['triggerType'] = trigger_type

                            if trigger_type is None and 'userChoice' in contract_info:
                                await cursor.execute("""
                                    SELECT userChoice
                                    FROM contractparticipants
                                    WHERE winningAmount IS NOT NULL
                                    LIMIT 1
                                """)
                                result = await cursor.fetchone()
                                if result:
                                    contract_info['winningChoice'] = result[0]

                                await cursor.execute("""
                                    SELECT COUNT(*) 
                                    FROM contractparticipants
                                    WHERE winningAmount IS NOT NULL
                                """)
                                count = await cursor.fetchone()
                                contract_info['numberOfWinners'] = count[0]

                        elif len(triggers) > 1:
                            return jsonify(result='error', description="Data integrity issue: multiple triggers found"), 500

        response = {
            "result": "ok",
            "contractName": contractName,
            "contractAddress": contractAddress,
            "contractInfo": contract_info
        }

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except Exception as e:
        print(f"unified_get_smart_contract_info: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": "Internal Server Error"}
            if version_resp == "v1"
            else {"description": "Internal Server Error"}
        )
        return jsonify(resp), 500



@app.route('/api/v1.0/getSmartContractParticipants', methods=['GET'])
@app.route('/api/v2/smartContractParticipants', methods=['GET'])
async def unified_get_contract_participants():
    try:
        version = get_api_version_from_path(default_version="v2")

        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not contractName or not contractAddress:
            msg = "Smart Contract's name hasn't been passed" if not contractName else "Smart Contract's address hasn't been passed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        contractName = contractName.strip().lower()
        contractAddress = contractAddress.strip()

        if version == "v2" and not check_flo_address(contractAddress):
            return jsonify(description="contractAddress validation failed"), 400

        db_name = standardize_db_name(f"{contractName}_{contractAddress}")
        contractStructure = await fetchContractStructure(contractName, contractAddress)
        contractStatus = await fetchContractStatus(contractName, contractAddress) if version == "v2" else None

        participantInfo_v1, participantInfo_v2, contractSubtype = {}, [], None

        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor(DictCursor) as cursor:

                query_map = {
                    'external-trigger': """
                        SELECT id, participantAddress, tokenAmount, userChoice, transactionHash
                        FROM contractparticipants
                    """,
                    'time-trigger': """
                        SELECT id, participantAddress, tokenAmount, transactionHash
                        FROM contractparticipants
                    """,
                    'tokenswap': """
                        SELECT
                            participantAddress,
                            tokenAmount,
                            userChoice,
                            winningAmount,
                            transactionHash,
                            blockNumber,
                            blockHash
                        FROM contractparticipants
                    """
                }

                # One-time contract: external-trigger
                if 'exitconditions' in contractStructure:
                    contractSubtype = 'external-trigger'
                    await cursor.execute(query_map[contractSubtype])
                    rows = await cursor.fetchall()
                    token = contractStructure.get('tokenIdentification')

                    for row in rows:
                        data = {
                            'participantFloAddress': row['participantAddress'],
                            'tokenAmount': float(row['tokenAmount']) if row['tokenAmount'] else 0.0,
                            'userChoice': row['userChoice'],
                            'transactionHash': row['transactionHash'],
                            'tokenIdentification': token
                        }
                        if version == "v2" and contractStatus == 'closed':
                            await cursor.execute("""
                                SELECT winningAmount FROM contractwinners WHERE referenceTxHash = %s
                            """, (row['transactionHash'],))
                            win = await cursor.fetchone()
                            data['winningAmount'] = float(win['winningAmount']) if win else 0.0
                        if version == "v2":
                            participantInfo_v2.append(data)
                        else:
                            participantInfo_v1[row['participantAddress']] = data

                # One-time contract: time-trigger
                elif 'payeeAddress' in contractStructure:
                    contractSubtype = 'time-trigger'
                    await cursor.execute(query_map[contractSubtype])
                    rows = await cursor.fetchall()

                    for row in rows:
                        data = {
                            'participantFloAddress': row['participantAddress'],
                            'tokenAmount': float(row['tokenAmount']) if row['tokenAmount'] else 0.0,
                            'transactionHash': row['transactionHash']
                        }
                        if version == "v2":
                            participantInfo_v2.append(data)
                        else:
                            participantInfo_v1[row['participantAddress']] = data

                # Continuous contract: tokenswap
                elif contractStructure.get('contractType') in ['continuous-event', 'continuos-event'] and \
                     contractStructure.get('subtype') == 'tokenswap':

                    contractSubtype = 'tokenswap'
                    await cursor.execute(query_map[contractSubtype])
                    rows = await cursor.fetchall()

                    for row in rows:
                        data = {
                            'participantFloAddress': row['participantAddress'],
                            'participationAmount': float(row['tokenAmount']) if row['tokenAmount'] else 0.0,
                            'swapPrice': float(row['userChoice']) if row['userChoice'] else None,
                            'swapAmount': float(row['winningAmount']) if row['winningAmount'] else 0.0,
                            'transactionHash': row['transactionHash'],
                            'blockNumber': row['blockNumber'],
                            'blockHash': row['blockHash'],
                        }
                        if version == "v2":
                            participantInfo_v2.append(data)
                        else:
                            participantInfo_v1[row['participantAddress']] = data

                else:
                    return jsonify(description="Unsupported contract type"), 400

        response = {
            "result": "ok",
            "contractName": contractName,
            "contractAddress": contractAddress,
            "participantInfo": participantInfo_v1
        } if version == "v1" else {
            "contractName": contractName,
            "contractAddress": contractAddress,
            "contractType": contractStructure.get('contractType', ''),
            "contractSubtype": contractSubtype,
            "participantInfo": participantInfo_v2
        }

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except Exception as e:
        print("unified_get_contract_participants:", e)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": "Internal Server Error"}
            if version_resp == "v1"
            else {"description": "Unexpected error occurred"}
        )
        return jsonify(resp), 500



@app.route('/api/v1.0/getParticipantDetails', methods=['GET'], endpoint='getParticipantDetailsV1')
@app.route('/api/v2/getParticipantDetails', methods=['GET'], endpoint='getParticipantDetailsV2')
async def unified_get_participant_details():
    try:
        version = get_api_version_from_path(default_version="v2")

        floAddress = request.args.get('floAddress')
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not floAddress:
            msg = "FLO address hasn't been passed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        if (contractName and not contractAddress) or (contractAddress and not contractName):
            msg = "Pass both, contractName and contractAddress as URL parameters"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        floAddress = floAddress.strip()
        contractName = contractName.strip().lower() if contractName else None
        contractAddress = contractAddress.strip() if contractAddress else None

        system_db = standardize_db_name("system")

        # Find all contracts the participant has joined
        query = '''
            SELECT * 
            FROM contractAddressMapping 
            WHERE address = %s AND addressType = "participant"
        '''
        params = (floAddress,)
        if contractName and contractAddress:
            query += " AND contractName = %s AND contractAddress = %s"
            params += (contractName, contractAddress)

        async with get_mysql_conn_ctx(system_db) as conn:
            async with conn.cursor(DictCursor) as cursor:
                await cursor.execute(query, params)
                participant_contracts = await cursor.fetchall()

        if not participant_contracts:
            desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "Address hasn't participated in any contract"
            code = 503 if not is_backend_ready() else 404
            resp = {"result": "error", "description": desc} if version == "v1" else {"description": desc}
            return jsonify(resp), code

        participationDetailsList = []

        for contract in participant_contracts:
            contract_name = contract['contractName']
            contract_address = contract['contractAddress']
            contract_db = standardize_db_name(f"{contract_name}_{contract_address}")
            details = {
                'contractName': contract_name,
                'contractAddress': contract_address
            }

            # Fetch the contract structure
            contractStructure = await fetchContractStructure(contract_name, contract_address)

            async with get_mysql_conn_ctx(contract_db) as conn:
                async with conn.cursor(DictCursor) as cursor:

                    # Continuous-event tokenswap contracts
                    if (
                        contractStructure.get('contractType') in ['continuous-event', 'continuos-event']
                        and contractStructure.get('subtype') == 'tokenswap'
                    ):
                        await cursor.execute("""
                            SELECT
                                sourceFloAddress AS participantAddress,
                                SUM(transferAmount) AS participationAmount,
                                userChoice AS swapPrice,
                                winningAmount AS swapAmount,
                                transactionHash,
                                blockNumber,
                                blockHash
                            FROM contractTransactionHistory
                            WHERE sourceFloAddress = %s
                              AND transactionType = 'participation'
                              AND transactionSubType = 'swap'
                            GROUP BY participantAddress, transactionHash, blockNumber, blockHash
                        """, (floAddress,))
                        rows = await cursor.fetchall()

                        details['participationDetails'] = [
                            {
                                'participationAddress': row['participantAddress'],
                                'participationAmount': float(row['participationAmount']) if row['participationAmount'] else 0.0,
                                'swapPrice': float(row['swapPrice']) if row['swapPrice'] else None,
                                'swapAmount': float(row['swapAmount']) if row['swapAmount'] else None,
                                'transactionHash': row['transactionHash'],
                                'blockNumber': row['blockNumber'],
                                'blockHash': row['blockHash']
                            }
                            for row in rows
                        ]

                        # Fetch deposit details too
                        await cursor.execute("""
                            SELECT 
                                depositorAddress,
                                depositAmount,
                                depositBalance,
                                expiryTime,
                                status,
                                transactionHash,
                                blockNumber,
                                blockHash
                            FROM contractdeposits
                            WHERE depositorAddress = %s
                        """, (floAddress,))
                        deposit_rows = await cursor.fetchall()

                        details['depositDetails'] = [
                            {
                                'depositorAddress': d['depositorAddress'],
                                'depositAmount': float(d['depositAmount']) if d['depositAmount'] else 0.0,
                                'depositBalance': float(d['depositBalance']) if d['depositBalance'] else 0.0,
                                'expiryTime': d['expiryTime'],
                                'status': d['status'],
                                'transactionHash': d['transactionHash'],
                                'blockNumber': d['blockNumber'],
                                'blockHash': d['blockHash']
                            }
                            for d in deposit_rows
                        ]

                    # One-time event (time-trigger)
                    elif contractStructure.get('contractType') == 'one-time-event' and 'payeeAddress' in contractStructure:
                        await cursor.execute("""
                            SELECT tokenAmount, transactionHash 
                            FROM contractparticipants 
                            WHERE participantAddress = %s
                        """, (floAddress,))
                        rows = await cursor.fetchall()
                        details['participationDetails'] = [
                            {
                                'tokenAmount': float(row['tokenAmount']) if row['tokenAmount'] else 0.0,
                                'transactionHash': row['transactionHash']
                            }
                            for row in rows
                        ]

                    # One-time event (external-trigger)
                    elif contractStructure.get('contractType') == 'one-time-event' and 'exitconditions' in contractStructure:
                        await cursor.execute("""
                            SELECT tokenAmount, userChoice, winningAmount, transactionHash
                            FROM contractparticipants
                            WHERE participantAddress = %s
                        """, (floAddress,))
                        rows = await cursor.fetchall()
                        details['participationDetails'] = [
                            {
                                'tokenAmount': float(row['tokenAmount']) if row['tokenAmount'] else 0.0,
                                'userChoice': row['userChoice'],
                                'winningAmount': float(row['winningAmount']) if row['winningAmount'] else 0.0,
                                'transactionHash': row['transactionHash']
                            }
                            for row in rows
                        ]

            if details:
                participationDetailsList.append(details)

        response = {
            'floAddress': floAddress,
            'type': 'participant',
            'participatedContracts': participationDetailsList,
        }
        if version == "v1":
            response['result'] = 'ok'

        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206

        return jsonify(response), 200

    except aiomysql.MySQLError as e:
        print(f"Database error: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {"result": "error", "description": "Database error occurred"} if version_resp == "v1" else {"description": "Database error occurred"}
        return jsonify(resp), 500

    except Exception as e:
        print(f"unified_get_participant_details: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {"result": "error", "description": "Unexpected error occurred"} if version_resp == "v1" else {"description": "Unexpected error occurred"}
        return jsonify(resp), 500



@app.route('/api/v1.0/getSmartContractTransactions', methods=['GET'])
@app.route('/api/v2/smartContractTransactions', methods=['GET'])
async def unified_smart_contract_transactions():
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        # Required params
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not contractName or not contractAddress:
            msg = "Smart Contract's name hasn't been passed" if not contractName else "Smart Contract's address hasn't been passed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400

        contractName = contractName.strip().lower()
        contractAddress = contractAddress.strip()

        if version == "v2" and not check_flo_address(contractAddress):
            return jsonify(description="contractAddress validation failed"), 400

        contract_db_name = standardize_db_name(f"{contractName}_{contractAddress}")

        if version == "v2":
            _from = int(request.args.get('_from', 1))
            to = int(request.args.get('to', 100))
            if _from < 1 or to < 1:
                msg = "_from validation failed" if _from < 1 else "to validation failed"
                return jsonify(description=msg), 400

            async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
                        (contract_db_name,)
                    )
                    exists = await cursor.fetchone()

            if not exists:
                desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "Smart Contract with the given name doesn't exist"
                code = 503 if not is_backend_ready() else 404
                return jsonify(description=desc), code

            transaction_result = await fetch_contract_transactions(contractName, contractAddress, _from, to)
            if isinstance(transaction_result, (Response, tuple)) and isinstance(transaction_result[0] if isinstance(transaction_result, tuple) else transaction_result, Response):
                return transaction_result

            if not isinstance(transaction_result, list):
                print(f"Unexpected result type from fetch_contract_transactions: {type(transaction_result)}")
                return jsonify(description="Unexpected error occurred while fetching transactions"), 500

            response_data = {
                "contractName": contractName,
                "contractAddress": contractAddress,
                "contractTransactions": sort_transactions(transaction_result),
            }
            if not is_backend_ready():
                response_data["warning"] = BACKEND_NOT_READY_WARNING
                return jsonify(response_data), 206
            return jsonify(response_data), 200

        else:
            # v1 logic
            async with get_mysql_conn_ctx(contract_db_name) as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("""
                        SELECT jsonData, parsedFloData
                        FROM contractTransactionHistory
                    """)
                    rows = await cursor.fetchall()

            transactions = {}
            for row in rows:
                details = json.loads(row[0])
                parsed = json.loads(row[1])
                txid = details["txid"]
                transactions[txid] = {
                    "transactionDetails": details,
                    "parsedFloData": parsed
                }

            response_data = {
                "result": "ok",
                "contractName": contractName,
                "contractAddress": contractAddress,
                "contractTransactions": transactions,
            }
            if not is_backend_ready():
                response_data["warning"] = BACKEND_NOT_READY_WARNING
                return jsonify(response_data), 206
            return jsonify(response_data), 200

    except ValueError as ve:
        print(f"Value error: {ve}")
        version_resp = get_api_version_from_path(default_version="v2")
        msg = "Invalid input for _from or to"
        resp = {"result": "error", "description": msg} if version_resp == "v1" else {"description": msg}
        return jsonify(resp), 400

    except aiomysql.MySQLError as e:
        print(f"MySQL error: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        msg = "Database error occurred"
        resp = {"result": "error", "description": msg} if version_resp == "v1" else {"description": msg}
        return jsonify(resp), 500

    except Exception as e:
        print(f"unified_smart_contract_transactions: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {"result": "error", "description": INTERNAL_ERROR} if version_resp == "v1" else {"description": INTERNAL_ERROR}
        return jsonify(resp), 500



# todo - add options to only ask for active/consumed/returned deposits
@app.route('/api/v2/smartContractDeposits', methods=['GET'])
async def smartcontractdeposits():
    try:
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not contractName or not contractAddress:
            return jsonify(description="Smart Contract's name and address must be passed"), 400

        contractName = contractName.strip().lower()
        contractAddress = contractAddress.strip()

        if not check_flo_address(contractAddress):
            return jsonify(description="contractAddress validation failed"), 400

        db_name = standardize_db_name(f"{contractName}_{contractAddress}")

        # Check if the database exists
        async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
                    (db_name,)
                )
                exists = await cursor.fetchone()

        if not exists:
            desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "Smart Contract does not exist."
            code = 503 if not is_backend_ready() else 404
            return jsonify(description=desc), code

        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor(DictCursor) as cursor:
                await cursor.execute("""
                    SELECT 
                        depositorAddress,
                        cd.transactionHash,
                        cd.status,
                        cd.depositBalance AS currentBalance,
                        MIN(cd.depositBalance) AS originalBalance,
                        MIN(cd.unix_expiryTime) AS expiryTime
                    FROM contractdeposits cd
                    INNER JOIN (
                        SELECT transactionHash, MAX(id) AS max_id
                        FROM contractdeposits
                        GROUP BY transactionHash
                    ) AS latest
                    ON cd.transactionHash = latest.transactionHash AND cd.id = latest.max_id
                    GROUP BY cd.transactionHash, depositorAddress, cd.status, currentBalance
                    ORDER BY cd.id DESC;
                """)
                deposits = await cursor.fetchall()

                deposit_info = [
                    {
                        'depositorAddress': d['depositorAddress'],
                        'transactionHash': d['transactionHash'],
                        'status': d['status'],
                        'originalBalance': float(d['originalBalance']) if d['originalBalance'] else 0.0,
                        'currentBalance': float(d['currentBalance']) if d['currentBalance'] else 0.0,
                        'time': d['expiryTime'],
                    }
                    for d in deposits
                ]

                await cursor.execute("""
                    SELECT SUM(depositBalance) AS totalDepositBalance
                    FROM (
                        SELECT transactionHash, MAX(id) AS max_id
                        FROM contractdeposits
                        GROUP BY transactionHash
                    ) latest
                    INNER JOIN contractdeposits cd
                    ON cd.transactionHash = latest.transactionHash AND cd.id = latest.max_id;
                """)
                total_balance = await cursor.fetchone()

        response = {
            'currentDepositBalance': float(total_balance['totalDepositBalance']) if total_balance and total_balance['totalDepositBalance'] else 0.0,
            'depositInfo': deposit_info,
        }

        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206

        return jsonify(response), 200

    except aiomysql.MySQLError as e:
        print(f"Database error in smartcontractdeposits: {e}")
        return jsonify(description="Database error occurred"), 500
    except Exception as e:
        print(f"smartcontractdeposits: {e}")
        return jsonify(description="An internal error occurred"), 500


@app.route('/api/v1.0/getBlockDetails/<blockdetail>', methods=['GET'])
@app.route('/api/v2/blockDetails/<blockdetail>', methods=['GET'])
async def unified_block_details(blockdetail):
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")
        blockJson = await blockdetailhelper(blockdetail)

        if blockJson:
            blockDetails = json.loads(blockJson[0][0])
            response = (
                {"result": "ok", "blockDetails": blockDetails}
                if version == "v1"
                else {"blockDetails": blockDetails}
            )
            return jsonify(response), 200

        desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "Block doesn't exist in database"
        code = 503 if not is_backend_ready() else 404
        resp = (
            {"result": "error", "description": desc}
            if version == "v1"
            else {"description": desc}
        )
        return jsonify(resp), code

    except Exception as e:
        print("unified_block_details:", e)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": INTERNAL_ERROR}
            if version_resp == "v1"
            else {"description": INTERNAL_ERROR}
        )
        return jsonify(resp), 500




@app.route('/api/v1.0/getTransactionDetails/<transactionHash>', methods=['GET'])
@app.route('/api/v2/transactionDetails/<transactionHash>', methods=['GET'])
async def unified_transaction_details(transactionHash):
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")
        data = await transactiondetailhelper(transactionHash)

        if not data or len(data) == 0:
            desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "Transaction doesn't exist in database"
            code = 503 if not is_backend_ready() else 404
            resp = {"result": "error", "description": desc} if version == "v1" else {"description": desc}
            return jsonify(resp), code

        transactionJson = json.loads(data[0][0])
        try:
            parseResult = json.loads(data[0][1])
        except Exception as e:
            print(f"[ERROR] Failed to parse parseResult: {e}")
            resp = {"result": "error", "description": "Invalid parseResult in transaction data"} if version == "v1" else {"description": "Invalid parseResult in transaction data"}
            return jsonify(resp), 500

        if version == "v1":
            return jsonify(
                parsedFloData=parseResult,
                transactionDetails=transactionJson,
                transactionHash=transactionHash,
                result='ok'
            ), 200

        # v2 logic
                # 🛡️ Skip confirmation lookup for off-chain/internal transactions
        if not transactionJson.get("onChain", True):
            transactionJson["confirmations"] = 1
        else:
            transactionJson = await update_transaction_confirmations(transactionJson)

        operation, db_reference = data[0][2], data[0][3]
        mergeTx = {**parseResult, **transactionJson, "onChain": True, "operation": operation, "operationDetails": {}}

        if operation:
            db_name = standardize_db_name(db_reference)
            async with get_mysql_conn_ctx(db_name) as conn:
                async with conn.cursor() as cursor:
                    if operation == 'smartContractDeposit':
                        operationDetails = {}

                        # returned deposits
                        await cursor.execute("""
                            SELECT depositAmount, blockNumber 
                            FROM contractdeposits 
                            WHERE status = 'deposit-return' AND transactionHash = %s
                        """, (transactionJson['txid'],))
                        returned = await cursor.fetchone()
                        if returned:
                            operationDetails.update({
                                'returned_depositAmount': returned[0],
                                'returned_blockNumber': returned[1]
                            })

                        # deposit honors
                        await cursor.execute("""
                            SELECT depositAmount, blockNumber 
                            FROM contractdeposits 
                            WHERE status = 'deposit-honor' AND transactionHash = %s
                        """, (transactionJson['txid'],))
                        honors = await cursor.fetchall()
                        operationDetails['depositHonors'] = {
                            'list': [{'honor_amount': h[0], 'blockNumber': h[1]} for h in honors],
                            'count': len(honors)
                        }

                        # deposit balance
                        await cursor.execute("""
                            SELECT depositBalance 
                            FROM contractdeposits 
                            WHERE id = (SELECT MAX(id) FROM contractdeposits WHERE transactionHash = %s)
                        """, (transactionJson['txid'],))
                        balance = await cursor.fetchone()
                        if balance:
                            operationDetails['depositBalance'] = balance[0]
                            operationDetails['consumedAmount'] = parseResult['depositAmount'] - balance[0]

                        mergeTx['operationDetails'] = operationDetails

                    elif operation == 'tokenswap-participation':
                        await cursor.execute("""
                            SELECT tokenAmount, winningAmount, userChoice 
                            FROM contractparticipants 
                            WHERE transactionHash = %s
                        """, (transactionJson['txid'],))
                        swap = await cursor.fetchone()

                        await cursor.execute("""
                            SELECT value FROM contractstructure 
                            WHERE attribute = 'selling_token'
                        """)
                        structure = await cursor.fetchone()

                        if swap and structure:
                            mergeTx['operationDetails'] = {
                                'participationAmount': swap[0],
                                'receivedAmount': swap[1],
                                'participationToken': parseResult['tokenIdentification'],
                                'receivedToken': structure[0],
                                'swapPrice_received_to_participation': float(swap[2])
                            }

                    elif operation == 'smartContractPays':
                        await cursor.execute("""
                            SELECT participantAddress, tokenAmount, userChoice, winningAmount 
                            FROM contractparticipants 
                            WHERE winningAmount IS NOT NULL
                        """)
                        winners = await cursor.fetchall()
                        mergeTx['operationDetails'] = {
                            'total_winners': len(winners),
                            'winning_choice': winners[0][2] if winners else None,
                            'winner_list': [
                                {
                                    'participantAddress': p[0],
                                    'participationAmount': p[1],
                                    'winningAmount': p[3]
                                }
                                for p in winners
                            ]
                        }

                    elif operation == 'ote-externaltrigger-participation':
                        await cursor.execute("""
                            SELECT winningAmount 
                            FROM contractparticipants 
                            WHERE transactionHash = %s
                        """, (transactionHash,))
                        win = await cursor.fetchone()
                        if win and win[0] is not None:
                            mergeTx['operationDetails'] = {'winningAmount': win[0]}

                    elif operation == 'tokenswapParticipation':
                        contractName, contractAddress = db_reference.rsplit('_', 1)
                        txhash_txs = await fetch_swap_contract_transactions(contractName, contractAddress, transactionHash)
                        mergeTx['subTransactions'] = [
                            tx for tx in txhash_txs if not tx.get('onChain', True)
                        ]

        return jsonify(mergeTx), 200

    except Exception as e:
        print(f"unified_transaction_details: {e}")
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {"result": "error", "description": INTERNAL_ERROR} if version_resp == "v1" else {"description": INTERNAL_ERROR}
        return jsonify(resp), 500





@app.route('/api/v1.0/getLatestTransactionDetails', methods=['GET'])
@app.route('/api/v2/latestTransactionDetails', methods=['GET'])
async def unified_latest_transaction_details():
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        # Parse limit param
        param_name = "numberOfLatestBlocks" if version == "v1" else "limit"
        limit_value = request.args.get(param_name)
        if limit_value:
            if version == "v1" and not limit_value.isdigit():
                return jsonify(result='error', description='Invalid numberOfLatestBlocks value provided'), 400
            if version == "v2" and not check_integer(limit_value):
                return jsonify(description='limit validation failed'), 400
            limit = int(limit_value)
        else:
            limit = 4

        db_name = standardize_db_name("latestCache")

        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT lt.* 
                    FROM latestTransactions lt
                    JOIN (
                        SELECT DISTINCT blockNumber 
                        FROM latestTransactions 
                        ORDER BY blockNumber DESC 
                        LIMIT %s
                    ) AS recent_blocks
                    ON lt.blockNumber = recent_blocks.blockNumber
                    ORDER BY lt.id ASC;
                """, (limit,))
                rows = await cursor.fetchall()

        transactions = []
        for item in rows:
            try:
                item = list(item)
                transactionDetails = json.loads(item[3])
                parsedFloData = json.loads(item[5])
                parsedFloData['transactionType'] = item[4]
                transactionDetails['blockheight'] = int(item[2])

                merged_tx = {
                    **transactionDetails,
                    **parsedFloData,
                    "onChain": True
                }
                transactions.append((merged_tx['txid'], merged_tx))

            except Exception as e:
                print(f"Error processing transaction {item[0]}: {e}")

        if version == "v1":
            response = {
                "result": "ok",
                "latestTransactions": {txid: data for txid, data in transactions}
            }
        else:
            response = {
                "latestTransactions": [data for _, data in transactions]
            }

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        print("unified_latest_transaction_details Error:", e)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {"result": "error", "description": INTERNAL_ERROR} if version_resp == "v1" else {"description": INTERNAL_ERROR}
        return jsonify(resp), 500



@app.route('/api/v1.0/getLatestBlockDetails', methods=['GET'])
@app.route('/api/v2/latestBlockDetails', methods=['GET'])
async def unified_latest_block_details():
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")
        limit_value = request.args.get("limit")
        
        if limit_value and not limit_value.isdigit():
            msg = "Invalid limit value provided" if version == "v1" else "limit validation failed"
            resp = {"result": "error", "description": msg} if version == "v1" else {"description": msg}
            return jsonify(resp), 400
        
        limit = int(limit_value) if limit_value else 4
        db_name = standardize_db_name("latestCache")

        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT lb.jsonData
                    FROM latestBlocks lb
                    JOIN (
                        SELECT blockNumber 
                        FROM latestBlocks 
                        ORDER BY blockNumber DESC 
                        LIMIT %s
                    ) AS recent_blocks
                    ON lb.blockNumber = recent_blocks.blockNumber
                    ORDER BY lb.id ASC;
                """, (limit,))
                latestBlocks = await cursor.fetchall()

        blocks = []
        for item in latestBlocks:
            try:
                blocks.append(json.loads(item[0]))
            except Exception as e:
                print(f"Error decoding block data: {e}")

        if version == "v1":
            block_details = {b.get("hash"): b for b in blocks if b.get("hash")}
            response = {"result": "ok", "latestBlocks": block_details}
        else:
            response = {"latestBlocks": blocks}

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        print("unified_latest_block_details Error:", e)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": INTERNAL_ERROR}
            if version_resp == "v1"
            else {"description": INTERNAL_ERROR}
        )
        return jsonify(resp), 500

    

@app.route('/api/v1.0/getBlockTransactions/<block_keyword>', methods=['GET'])
@app.route('/api/v2/blockTransactions/<block_keyword>', methods=['GET'])
async def unified_block_transactions(block_keyword):
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        blockJsonRows = await blockdetailhelper(block_keyword)

        if not blockJsonRows:
            desc = BACKEND_NOT_READY_ERROR if not is_backend_ready() else "Block doesn't exist in database"
            code = 503 if not is_backend_ready() else 404
            resp = {"result": "error", "description": desc} if version == "v1" else {"description": desc}
            return jsonify(resp), code

        block_json_data = json.loads(blockJsonRows[0][0])
        txs_data = []
        txs_dict = {}

        for tx in block_json_data['txs']:
            temptx = await transactiondetailhelper(tx['txid'])
            if not temptx:
                continue

            transactionJson = json.loads(temptx[0][0])
            parseResult = json.loads(temptx[0][1])
            merged_tx = {**parseResult, **transactionJson, "onChain": True}

            txs_data.append(merged_tx)
            txs_dict[tx['txid']] = {
                "parsedFloData": parseResult,
                "transactionDetails": transactionJson
            }

        response = (
            {
                "result": "ok",
                "transactions": txs_dict,
                "blockKeyword": block_keyword,
            }
            if version == "v1"
            else {
                "transactions": txs_data,
                "blockKeyword": block_keyword,
            }
        )

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        print("unified_block_transactions:", e)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            {"result": "error", "description": INTERNAL_ERROR}
            if version_resp == "v1"
            else {"description": INTERNAL_ERROR}
        )
        return jsonify(resp), 500
    

@app.route('/api/v1.0/categoriseString/<urlstring>', methods=['GET'])
@app.route('/api/v2/categoriseString/<urlstring>', methods=['GET'])
async def unified_categorise_string(urlstring):
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        async with aiohttp.ClientSession() as session:
            for endpoint, result_type in [
                (f"{apiUrl}/api/v1/tx/{urlstring}", "transaction"),
                (f"{apiUrl}/api/v1/block/{urlstring}", "block"),
            ]:
                async with session.get(endpoint) as resp:
                    if resp.status == 200:
                        return jsonify(type=result_type), 200

        if version == "v2":
            async with get_mysql_conn_ctx(standardize_db_name("system")) as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("""
                        SELECT db_type 
                        FROM databaseTypeMapping 
                        WHERE LOWER(db_name) = %s
                        LIMIT 1;
                    """, (urlstring.lower(),))
                    row = await cursor.fetchone()
                    if row:
                        mapping = {
                            "token": "token",
                            "infinite-token": "token",
                            "smartcontract": "smartContract"
                        }
                        return jsonify(type=mapping.get(row[0], "noise")), 200
            return jsonify(type="noise"), 200

        # v1 logic
        prefix = f"{mysql_config.database_prefix}_"
        suffix = "_db"
        token_names, contract_list = set(), []

        async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn_info:
            async with conn_info.cursor(DictCursor) as cursor:
                await cursor.execute(f"""
                    SELECT SCHEMA_NAME 
                    FROM SCHEMATA
                    WHERE SCHEMA_NAME LIKE '{prefix}%' AND SCHEMA_NAME LIKE '%{suffix}'
                """)
                dbs = await cursor.fetchall()

                for db in dbs:
                    stripped = db['SCHEMA_NAME'][len(prefix):-len(suffix)]
                    if stripped in ["latestCache", "system"]:
                        continue
                    parts = stripped.split("_")
                    if len(parts) == 1:
                        token_names.add(stripped.lower())
                    elif len(parts) == 2 and len(parts[1]) == 34 and parts[1][0] in ("F", "o"):
                        contract_list.append(f"{parts[0]}_{parts[1]}")

        if urlstring.lower() in token_names:
            return jsonify(type="token"), 200

        async with get_mysql_conn_ctx("system") as conn:
            async with conn.cursor(DictCursor) as cursor:
                await cursor.execute("SELECT DISTINCT contractName FROM activecontracts")
                smart_names = {row['contractName'].lower() for row in await cursor.fetchall()}
                if urlstring.lower() in smart_names:
                    return jsonify(type="smartContract"), 200

        if urlstring in contract_list:
            return jsonify(type="smartContract"), 200

        return jsonify(type="noise"), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        print("unified_categorise_string:", e)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = (
            dict(result="error", description=INTERNAL_ERROR)
            if version_resp == "v1"
            else dict(description="Internal Error")
        )
        return jsonify(resp), 500



@app.route('/api/v1.0/getTokenSmartContractList', methods=['GET'])
@app.route('/api/v2/tokenSmartContractList', methods=['GET'])
async def unified_token_smart_contract_list():
    version = None
    try:
        version = get_api_version_from_path(default_version="v2")

        db_prefix = f"{mysql_config.database_prefix}_"
        db_suffix = "_db"
        token_list, contract_list = [], []

        # STEP 1 — Fetch database names
        async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn_info:
            if version == "v1":
                async with conn_info.cursor(DictCursor) as cursor:
                    await cursor.execute(f"""
                        SELECT SCHEMA_NAME FROM SCHEMATA
                        WHERE SCHEMA_NAME LIKE '{db_prefix}%' AND SCHEMA_NAME LIKE '%{db_suffix}'
                    """)
                    all_databases = await cursor.fetchall()
                    all_dbs = [row["SCHEMA_NAME"] for row in all_databases]
            else:
                async with conn_info.cursor() as cursor:
                    await cursor.execute(f"""
                        SELECT SCHEMA_NAME FROM SCHEMATA
                        WHERE SCHEMA_NAME LIKE '{db_prefix}%' AND SCHEMA_NAME LIKE '%{db_suffix}'
                    """)
                    all_databases = await cursor.fetchall()
                    all_dbs = [row[0] for row in all_databases]

            for db_name in all_dbs:
                stripped = db_name[len(db_prefix):-len(db_suffix)]
                if stripped in ["latestCache", "system"]:
                    continue
                parts = stripped.split("_")
                if len(parts) == 1:
                    token_list.append(stripped)
                elif len(parts) == 2 and len(parts[1]) == 34 and parts[1][0] in ("F", "o"):
                    contract_list.append({"contractName": parts[0], "contractAddress": parts[1]})

        # STEP 2 — Fetch smart contracts
        async with get_mysql_conn_ctx("system") as conn_system:
            if version == "v1":
                async with conn_system.cursor(DictCursor) as cursor:
                    await cursor.execute("SELECT * FROM activecontracts")
                    smart_contracts = await cursor.fetchall()

                    for contract in smart_contracts:
                        for item in contract_list:
                            if item["contractName"] == contract["contractName"] and item["contractAddress"] == contract["contractAddress"]:
                                item.update({
                                    k: contract[k] if k not in ["expiryDate", "closeDate"] else contract[k] or None
                                    for k in [
                                        "status", "tokenIdentification", "contractType", "transactionHash",
                                        "blockNumber", "blockHash", "incorporationDate", "expiryDate", "closeDate"
                                    ]
                                })
            else:
                async with conn_system.cursor() as cursor:
                    contractName = request.args.get("contractName")
                    contractAddress = request.args.get("contractAddress")

                    contractName = contractName.strip().lower() if contractName else None
                    if contractAddress:
                        contractAddress = contractAddress.strip()
                        if not check_flo_address(contractAddress):
                            return jsonify(description='contractAddress validation failed'), 400

                    await cursor.execute("""
                        SELECT * FROM activecontracts
                        WHERE (%s IS NULL OR LOWER(contractName) = %s)
                          AND (%s IS NULL OR contractAddress = %s)
                    """, (contractName, contractName, contractAddress, contractAddress))
                    smart_contracts = await cursor.fetchall()
                    smart_contracts_morphed = await smartcontract_morph_helper(smart_contracts)

        # STEP 3 — Build response
        if version == "v2":
            committeeAddressList = await refresh_committee_list(APP_ADMIN, apiUrl, int(time.time()))
            response = {
                "tokens": token_list,
                "smartContracts": smart_contracts_morphed,
                "smartContractCommittee": committeeAddressList
            }
        else:
            response = {
                "tokens": token_list,
                "smartContracts": contract_list,
                "result": "ok"
            }

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except aiomysql.MySQLError as e:
        print("Database error in unified_token_smart_contract_list:", e)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {
            "result": "error",
            "description": "Database error occurred"
        } if version_resp == "v1" else {
            "description": "Database error occurred"
        }
        return jsonify(resp), 500
    except Exception as e:
        print("unified_token_smart_contract_list:", e)
        version_resp = get_api_version_from_path(default_version="v2")
        resp = {
            "result": "error",
            "description": INTERNAL_ERROR
        } if version_resp == "v1" else {
            "description": INTERNAL_ERROR
        }
        return jsonify(resp), 500





    
class ServerSentEvent:
    def __init__(
            self,
            data: str,
            *,
            event: Optional[str] = None,
            id: Optional[int] = None,
            retry: Optional[int] = None,
    ) -> None:
        self.data = data
        self.event = event
        self.id = id
        self.retry = retry

    def encode(self) -> bytes:
        message = f"data: {self.data}"
        if self.event is not None:
            message = f"{message}\nevent: {self.event}"
        if self.id is not None:
            message = f"{message}\nid: {self.id}"
        if self.retry is not None:
            message = f"{message}\nretry: {self.retry}"
        message = f"{message}\r\n\r\n"
        return message.encode('utf-8')

@app.route('/sse')
async def sse():
    queue = asyncio.Queue()
    app.clients.add(queue)

    async def send_events():
        while True:
            try:
                data = await queue.get()
                event = ServerSentEvent(data)
                yield event.encode()
            except asyncio.CancelledError as error:
                app.clients.remove(queue)

    response = await make_response(
        send_events(),
        {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Transfer-Encoding': 'chunked',
        },
    )
    response.timeout = None
    return response


@app.route('/api/v2/prices', methods=['GET'])
async def priceData():
    try:
        # Standardize the system database name
        db_name = standardize_db_name("system")

        # Connect to the database asynchronously
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                # Query to fetch rate pairs and prices
                query = "SELECT ratepair, price FROM ratepairs;"
                await cursor.execute(query)
                ratepairs = await cursor.fetchall()

                # Prepare a dictionary of prices
                prices = {ratepair[0]: ratepair[1] for ratepair in ratepairs}

                # Return the prices
                return jsonify(prices=prices), 200

    except Exception as e:
        print("priceData:", e)
        return jsonify(description="Internal Error"), 500


@app.route("/health")
async def health():
    return {
        "status": "ok",
        "async_pool_initialized": async_connection_pool is not None
    }


#######################
#######################

async def initialize_db():
    """
    Initializes the `ratepairs` table in the `system` database if it does not exist,
    and populates it with default values.
    """
    try:
        # Standardize database name for the system database
        db_name = standardize_db_name("system")

        # Connect to the database asynchronously
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                # Create the `ratepairs` table if it does not exist
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ratepairs (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        ratepair VARCHAR(20) NOT NULL UNIQUE,
                        price FLOAT NOT NULL
                    )
                """)

                # Check if the table contains any data
                await cursor.execute("SELECT COUNT(*) FROM ratepairs")
                count = (await cursor.fetchone())[0]

                if count == 0:
                    # Insert default rate pairs if the table is empty
                    default_ratepairs = [
                        ('BTCBTC', 1),
                        ('BTCUSD', -1),
                        ('BTCINR', -1),
                        ('FLOUSD', -1),
                        ('FLOINR', -1),
                        ('USDINR', -1)
                    ]
                    await cursor.executemany(
                        "INSERT INTO ratepairs (ratepair, price) VALUES (%s, %s)",
                        default_ratepairs
                    )
                    await conn.commit()

                    # Update the prices
                    await updatePrices()

    except Exception as e:
        logger.error(f"Error initializing the database: {e}", exc_info=True)
        raise  # Important: propagate the error so the app knows startup failed




async def start_api_server(config):
    """
    Starts the API server with proper shutdown handling.
    """
    set_configs(config)  # Load configurations

    hypercorn_config = HypercornConfig()
    hypercorn_config.bind = [f"{APIHOST}:{APIPORT}"]

            # 🚀 Increase timeouts and tolerances for slow/heavy block processing
    hypercorn_config.shutdown_timeout = 2
    hypercorn_config.read_timeout = 300
    hypercorn_config.write_timeout = 300
    hypercorn_config.keep_alive_timeout = 300
    hypercorn_config.max_app_queue_size = 100


    loop = asyncio.get_event_loop()

    # Handle shutdown signals gracefully
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(loop, signal=s)))


    try:
        print("Starting Quart server...")
        await serve(app, hypercorn_config)
        print("Serve completed")  # Should never print unless shutdown
    finally:
        print("Server exiting... cleaning up")
        #await shutdown(loop)  # Ensure cleanup


if __name__ == "__main__":
    try:
        asyncio.run(start_api_server(config))
    except Exception as e:
        print(f"Error during server execution: {e}")




