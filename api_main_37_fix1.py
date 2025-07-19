from collections import defaultdict
import json
import os
import requests
import sys
import time
from datetime import datetime
from quart import jsonify, make_response, Quart, render_template, request, flash, redirect, url_for, send_file
from quart_cors import cors
import asyncio
from typing import Optional
from config import *
import parsing
import subprocess
import pyflo
from operator import itemgetter
import pdb
import ast
from quart import Response
import traceback






#MYSQL ENHANCEMENTS START
import configparser
import aiohttp
import aiomysql
import asyncio
import atexit
import pymysql
from dbutils.pooled_db import PooledDB
from apscheduler.schedulers.background import BackgroundScheduler
import logging
import signal
import threading
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from hypercorn.config import Config as HypercornConfig
from hypercorn.asyncio import serve
from asyncio import PriorityQueue
from aiomysql.cursors import DictCursor
import psutil
from aiomysql import DictCursor


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
net = config['DEFAULT'].get('NET', 'mainnet')  # Default to 'mainnet' if not set

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


from contextlib import asynccontextmanager

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
    raw_api_url = config.get("API", "LOCALADDRESSINDEXERURL", fallback="https://blockbook.ranchimall.net/api/")
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
    logger.info("ABC: Entering get_mysql_connection function")
    conn = None  # Initialize conn to None to avoid "referenced before assignment"
    logger.info("ABC: Initialized conn to None")

    db_name = standardize_db_name(db_name) if not no_standardize else db_name
    logger.info(f"ABC: Database name standardized to: {db_name}")

    try:
        logger.info("ABC: Checking USE_ASYNC flag")
        if USE_ASYNC:
            logger.info("ABC: Async mode enabled")
            if not async_connection_pool:
                logger.error("ABC: Async connection pool not initialized")
                raise RuntimeError("Async connection pool not initialized")

            logger.info("ABC: Acquiring async connection")
            conn = await async_connection_pool.acquire()  # Acquire connection
            if conn is None:
                logger.error("ABC: Failed to acquire a valid connection")
                raise RuntimeError("Failed to acquire a valid async connection")
            logger.info("ABC: Async connection acquired successfully")

            async with conn.cursor() as cursor:
                logger.info("ABC: Creating async cursor and selecting database")
                await cursor.execute(f"USE `{db_name}`")  # Use escaped database name
                logger.info(f"ABC: Database `{db_name}` selected successfully in async mode")
            return conn  # Return connection for further use
        else:
            logger.info("ABC: Sync mode enabled")
            if not sync_connection_pool:
                logger.error("ABC: Sync connection pool not initialized")
                raise RuntimeError("Sync connection pool not initialized")

            logger.info("ABC: Acquiring sync connection")
            conn = sync_connection_pool.connection()  # Acquire sync connection
            if conn is None:
                logger.error("ABC: Failed to acquire a valid connection")
                raise RuntimeError("Failed to acquire a valid sync connection")
            logger.info("ABC: Sync connection acquired successfully")

            conn.select_db(db_name)  # Select database
            logger.info(f"ABC: Database `{db_name}` selected successfully in sync mode")
            return conn  # Return connection for further use
    except Exception as e:
        logger.error(f"ABC: Error in database connection: {e}")
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
internalTransactionTypes = [ 'tokenswapDepositSettlement', 'tokenswapParticipationSettlement', 'smartContractDepositReturn']

if net == 'mainnet':
    is_testnet = False
elif net == 'testnet':
    is_testnet = True

# Validation functionss
def check_flo_address(floaddress, is_testnet=False):
    return pyflo.is_address_valid(floaddress, testnet=is_testnet)

def check_integer(value):
    return str.isdigit(value)

""" ??? NOT USED???
# Helper functions
def retryRequest(tempserverlist, apicall):
    if len(tempserverlist) != 0:
        try:
            response = requests.get('{}api/{}'.format(tempserverlist[0], apicall))
        except:
            tempserverlist.pop(0)
            return retryRequest(tempserverlist, apicall)
        else:
            if response.status_code == 200:
                return json.loads(response.content)
            else:
                tempserverlist.pop(0)
                return retryRequest(tempserverlist, apicall)
    else:
        print("None of the APIs are responding for the call {}".format(apicall))
        sys.exit(0)


def multiRequest(apicall, net):
    testserverlist = ['http://0.0.0.0:9000/', 'https://testnet.flocha.in/', 'https://testnet-flosight.duckdns.org/']
    mainserverlist = ['http://0.0.0.0:9001/', 'https://livenet.flocha.in/', 'https://testnet-flosight.duckdns.org/']
    if net == 'mainnet':
        return retryRequest(mainserverlist, apicall)
    elif net == 'testnet':
        return retryRequest(testserverlist, apicall)
"""


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


#ATTEMPT 1
# async def update_transaction_confirmations(transactionJson):
#     url = f"{apiUrl}/api/v1/tx/{transactionJson['txid']}"
#     try:
#         async with aiohttp.ClientSession() as session:
#             async with session.get(url) as response:
#                 if response.status == 200:
#                     response_data = await response.json()
#                     transactionJson['confirmations'] = response_data['confirmations']
#     except Exception as e:
#         print(f"Error fetching transaction confirmation: {e}")
#     return transactionJson

# ATTEMPT 2
# async def update_transaction_confirmations(transactionJson):
#     try:
#         # Simulate the response without making an actual API call as it is slowing down
#         transactionJson['confirmations'] = transactionJson['confirmations']
#         logger.info(f"Mock confirmation set for transaction {transactionJson['txid']}: {transactionJson['confirmations']} confirmations")
#     except Exception as e:
#         print(f"Error updating transaction confirmation: {e}")
#     return transactionJson

#ATTEMPT 3
# async def update_transaction_confirmations(transactionJson):
#     url = f"{apiUrl}/api/v1/tx/{transactionJson['txid']}"
#     try:
#         timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
#         async with aiohttp.ClientSession(timeout=timeout) as session:
#             async with session.get(url) as response:
#                 if response.status == 200:
#                     response_data = await response.json()
#                     transactionJson['confirmations'] = response_data['confirmations']
#                 else:
#                     print(f"API error: {response.status}")
#     except asyncio.TimeoutError:
#         print(f"Request timed out after {API_TIMEOUT} seconds")
#     except Exception as e:
#         print(f"Error fetching transaction confirmation: {e}")
#     return transactionJson


import asyncio
import aiohttp

transaction_confirmation_cache = {}

async def update_transaction_confirmations(transactionJson):
    txid = transactionJson['txid']
    if txid in transaction_confirmation_cache:
        transactionJson['confirmations'] = transaction_confirmation_cache[txid]
        return transactionJson

    if transactionJson.get('confirmations',0) >= 1:
        return transactionJson

    url = f"{apiUrl}/api/v1/tx/{txid}"
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)  # Increased timeout
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for retry in range(1):  # Retry logic
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            response_data = await response.json()
                            transactionJson['confirmations'] = response_data['confirmations']
                            transaction_confirmation_cache[txid] = response_data['confirmations']  # Cache result
                            return transactionJson
                        print(f"API error: {response.status}")
                except asyncio.TimeoutError:
                    await asyncio.sleep(2 ** retry)  # Exponential backoff
                    continue
            print(f"Skipping transaction {txid} due to repeated timeouts. CASE 3")
    except Exception as e:
        print(f"Error fetching transaction confirmation: {e}")
    return transactionJson




async def smartcontract_morph_helper(smart_contracts):
    contractList = []
    for idx, contract in enumerate(smart_contracts):
        contractDict = {}
        contractDict['contractName'] = contract[1]
        contractDict['contractAddress'] = contract[2]
        contractDict['status'] = contract[3]
        contractDict['contractType'] = contract[5]

        if contractDict['contractType'] in ['continuous-event', 'continuos-event']:
            contractDict['contractSubType'] = 'tokenswap'
            accepting_selling_tokens = ast.literal_eval(contract[4])
            contractDict['acceptingToken'] = accepting_selling_tokens[0]
            contractDict['sellingToken'] = accepting_selling_tokens[1]
            
            # Awaiting async calls
            contractStructure = await fetchContractStructure(contractDict['contractName'], contractDict['contractAddress'])
            if contractStructure['pricetype'] == 'dynamic':
                # temp fix
                if 'oracle_address' in contractStructure.keys():
                    contractDict['oracle_address'] = contractStructure['oracle_address']
                    contractDict['price'] = await fetch_dynamic_swap_price(contractStructure, {'time': datetime.now().timestamp()})
            else:
                contractDict['price'] = contractStructure['price']
        
        elif contractDict['contractType'] == 'one-time-event':
            contractDict['tokenIdentification'] = contract[4]
            # Awaiting async call
            contractStructure = await fetchContractStructure(contractDict['contractName'], contractDict['contractAddress'])
            
            if 'payeeAddress' in contractStructure.keys():
                contractDict['contractSubType'] = 'time-trigger'
            else:
                choice_list = []
                for obj_key in contractStructure['exitconditions'].keys():
                    choice_list.append(contractStructure['exitconditions'][obj_key])
                contractDict['userChoices'] = choice_list
                contractDict['contractSubType'] = 'external-trigger'
                contractDict['expiryDate'] = contract[9]
            
            contractDict['closeDate'] = contract[10]

        contractDict['transactionHash'] = contract[6]
        contractDict['blockNumber'] = contract[7]
        contractDict['incorporationDate'] = contract[8]
        contractList.append(contractDict)

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
        async with get_mysql_conn_ctx("system") as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params)
                smart_contracts = await cursor.fetchall()
                return smart_contracts

    except aiomysql.MySQLError as e:
        print(f"Database error in return_smart_contracts: {e}")
        return []

    except Exception as e:
        print(f"Unexpected error in return_smart_contracts: {e}")
        return []



async def fetchContractStructure(contractName, contractAddress):
    """
    Fetches the structure of a smart contract from the MySQL database.
    """
    db_name = f"{contractName.strip()}_{contractAddress.strip()}"

    try:
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT attribute, value FROM contractstructure')
                result = await cursor.fetchall()

                contractStructure = {}
                conditionDict = {}
                counter = 0

                for attribute, value in result:
                    if attribute == 'exitconditions':
                        conditionDict[counter] = value
                        counter += 1
                    else:
                        contractStructure[attribute] = value

                if conditionDict:
                    contractStructure['exitconditions'] = conditionDict

        # Post-process after connection is released
        if 'contractAmount' in contractStructure:
            contractStructure['contractAmount'] = float(contractStructure['contractAmount'])
        if 'payeeAddress' in contractStructure:
            contractStructure['payeeAddress'] = json.loads(contractStructure['payeeAddress'])
        if 'maximumsubscriptionamount' in contractStructure:
            contractStructure['maximumsubscriptionamount'] = float(contractStructure['maximumsubscriptionamount'])
        if 'minimumsubscriptionamount' in contractStructure:
            contractStructure['minimumsubscriptionamount'] = float(contractStructure['minimumsubscriptionamount'])
        if 'price' in contractStructure:
            contractStructure['price'] = float(contractStructure['price'])

        return contractStructure

    except aiomysql.MySQLError as e:
        print(f"Database error while fetching contract structure: {e}")
        return 0
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 0


async def fetchContractStatus(contractName, contractAddress):
    try:
        async with get_mysql_conn_ctx('system') as conn:
            async with conn.cursor() as cursor:
                query = """
                    SELECT status 
                    FROM activecontracts 
                    WHERE contractName = %s AND contractAddress = %s 
                    ORDER BY id DESC 
                    LIMIT 1
                """
                await cursor.execute(query, (contractName, contractAddress))
                status = await cursor.fetchone()

        return status[0] if status else None

    except aiomysql.MySQLError as e:
        print(f"Database error while fetching contract status: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error: {e}")
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

    # USD -> INR
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.exchangerate-api.com/v4/latest/usd", timeout=10) as response:
                if response.status == 200:
                    price = await response.json()
                    prices['USDINR'] = price['rates']['INR']
    except Exception as e:
        print(f"Error fetching USD to INR exchange rate: {e}")

    # Blockchain stuff: BTC, FLO -> USD, INR
    # BTC -> USD | BTC -> INR
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,flo&vs_currencies=usd,inr", timeout=10) as response:
                if response.status == 200:
                    price = await response.json()
                    prices['BTCUSD'] = price['bitcoin']['usd']
                    prices['BTCINR'] = price['bitcoin']['inr']
    except Exception as e:
        print(f"Error fetching BTC prices: {e}")

    # FLO -> USD | FLO -> INR
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.coinlore.net/api/ticker/?id=67", timeout=10) as response:
                if response.status == 200:
                    price = await response.json()
                    prices["FLOUSD"] = float(price[0]['price_usd'])
                    if 'USDINR' in prices:
                        prices["FLOINR"] = float(prices["FLOUSD"]) * float(prices['USDINR'])
    except Exception as e:
        print(f"Error fetching FLO prices: {e}")

    # Log the updated prices
    print('Prices updated at time: %s' % datetime.now())
    print(prices)

    # Update prices in the database asynchronously
    try:
        async with get_mysql_conn_ctx('system') as conn:
            async with conn.cursor() as cursor:
                for pair, price in prices.items():
                    await cursor.execute(
                        "UPDATE ratepairs SET price = %s WHERE ratepair = %s",
                        (price, pair)
                    )
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
                contract_status_time_info = await cursor.fetchone()

        return contract_status_time_info if contract_status_time_info else []

    except aiomysql.MySQLError as e:
        print(f"Database error while fetching contract status and time info: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error: {e}")
        return []




def checkIF_commitee_trigger_tranasaction(transactionDetails):
    if transactionDetails[3] == 'trigger':
        pass


async def transaction_post_processing(transactionJsonData):
    rowarray_list = []

    for i, row in enumerate(transactionJsonData):
        transactions_object = {}

        try:
            parsedFloData = json.loads(row['parsedFloData'])
            transactionDetails = json.loads(row['jsonData'])

            if row['transactionType'] in internalTransactionTypes or (
                row['transactionType'] == 'trigger' and row.get('transactionSubType') != 'committee'
            ):
                internal_info = {
                    'senderAddress': row['sourceFloAddress'],
                    'receiverAddress': row['destFloAddress'],
                    'tokenAmount': row['transferAmount'],
                    'tokenIdentification': row['token'],
                    'contractName': parsedFloData['contractName'],
                    'transactionTrigger': transactionDetails['txid'],
                    'time': transactionDetails['time'],
                    'type': row['transactionType'],
                    'onChain': False
                }
                transactions_object = internal_info
            else:
                transactions_object = {**parsedFloData, **transactionDetails}
                transactions_object = await update_transaction_confirmations(transactions_object)
                transactions_object['onChain'] = True

            rowarray_list.append(transactions_object)

        except Exception as e:
            logger.error(f"Error processing row {i}: {e}")

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
        # Use context manager to auto-release both connections
        async with get_mysql_conn_ctx(token_db_name) as conn_token, \
                   get_mysql_conn_ctx(sc_db_name) as conn_sc:

            async with conn_sc.cursor(DictCursor) as cursor_sc, \
                       conn_token.cursor(DictCursor) as cursor_token:

                # Fetch data from contractTransactionHistory (sc_db_name)
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

                # Build placeholder string for IN clause
                transaction_hashes_placeholder = f"({','.join(['%s'] * len(transaction_hashes))})"
                query_token = f"""
                    SELECT jsonData, parsedFloData, time, transactionType, sourceFloAddress, destFloAddress, 
                           transferAmount, '{token_name}' AS token, transactionHash
                    FROM transactionHistory
                    WHERE transactionHash IN {transaction_hashes_placeholder}
                """
                await cursor_token.execute(query_token, transaction_hashes)
                token_transactions = list(await cursor_token.fetchall())

                # Combine results
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
    
    Args:
        contractName (str): Name of the smart contract.
        contractAddress (str): Address of the smart contract.
        _from (int, optional): Starting index for transactions. Defaults to 0.
        to (int, optional): Ending index for transactions. Defaults to 100.
        USE_ASYNC (bool, optional): Flag to use async connection.

    Returns:
        list or Response: Processed transactions or error response
    """
    try:
        sc_db_name = standardize_db_name(f"{contractName}_{contractAddress}")

        # Fetch contract structure
        contractStructure = await fetchContractStructure(contractName, contractAddress)
        if not contractStructure:
            return jsonify(description="Invalid contract structure"), 404

        transactionJsonData = []

        creation_tx_query = """
            SELECT jsonData, parsedFloData, time, transactionType, sourceFloAddress, destFloAddress, 
                   transferAmount, '' AS token, transactionSubType 
            FROM contractTransactionHistory
            ORDER BY id
            LIMIT 1;
        """

        # Use connection context manager
        async with get_mysql_conn_ctx(sc_db_name) as conn_sc:
            async with conn_sc.cursor(DictCursor) as cursor_sc:
                await cursor_sc.execute(creation_tx_query)
                creation_tx = list(await cursor_sc.fetchall())
                transactionJsonData.extend(creation_tx)

        # Fetch token transactions concurrently
        if contractStructure['contractType'] == 'continuos-event':
            token1 = contractStructure['accepting_token']
            token2 = contractStructure['selling_token']

            token_results_1, token_results_2 = await asyncio.gather(
                fetch_token_transactions_for_contract(token1, sc_db_name, _from, to, USE_ASYNC),
                fetch_token_transactions_for_contract(token2, sc_db_name, _from, to, USE_ASYNC)
            )

            transactionJsonData.extend(list(token_results_1))
            transactionJsonData.extend(list(token_results_2))

        elif contractStructure['contractType'] == 'one-time-event':
            token1 = contractStructure['tokenIdentification']
            result = await fetch_token_transactions_for_contract(token1, sc_db_name, _from, to, USE_ASYNC)
            transactionJsonData.extend(list(result))

        return await transaction_post_processing(transactionJsonData)

    except aiomysql.MySQLError as e:
        print(f"Database error while fetching contract transactions: {e}")
        return jsonify(description="Database error"), 500

    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify(description="Unexpected error occurred"), 500








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
        if 'isCoinBase' in transaction_info or transaction_info['vin'][0]['addresses'][0] != admin_flo_id or transaction_info['blocktime'] > blocktime:
            return
        try:
            tx_flodata = json.loads(transaction_info['floData'])
            committee_list.extend(process_committee_flodata(tx_flodata))
        except Exception as e:
            print(f"Error processing transaction: {e}")

    async def send_api_request(url):
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, ssl=API_VERIFY) as response:
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

    url = f'{api_url}api/v1/address/{admin_flo_id}?details=txs'
    response = await send_api_request(url)
    if response == ["timeout"]:
        return ["timeout"]

    if response is None:
        return []

    for transaction_info in response.get('txs', []):
        await process_transaction(transaction_info)

    while 'incomplete' in response:
        url = f'{api_url}api/v1/address/{admin_flo_id}/txs?latest={latest_param}&mempool={mempool_param}&before={init_id}'
        response = await send_api_request(url)
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
    return jsonify('Welcome to RanchiMall FLO Api v2')


@app.route('/api/v1.0/getSystemData', methods=['GET'])
async def systemData():
    try:
        system_db_name = standardize_db_name("system")
        latest_cache_db_name = standardize_db_name("latestCache")

        async with get_mysql_conn_ctx(system_db_name) as conn_system:
            async with conn_system.cursor() as cursor_system:
                await cursor_system.execute('SELECT COUNT(DISTINCT tokenAddress) FROM tokenAddressMapping')
                tokenAddressCount = (await cursor_system.fetchone())[0]

                await cursor_system.execute('SELECT COUNT(DISTINCT token) FROM tokenAddressMapping')
                tokenCount = (await cursor_system.fetchone())[0]

                await cursor_system.execute('SELECT COUNT(DISTINCT contractName) FROM contractAddressMapping')
                contractCount = (await cursor_system.fetchone())[0]

                await cursor_system.execute("SELECT value FROM systemData WHERE attribute='lastblockscanned'")
                lastscannedblock = int((await cursor_system.fetchone())[0])

        async with get_mysql_conn_ctx(latest_cache_db_name) as conn_cache:
            async with conn_cache.cursor() as cursor_cache:
                await cursor_cache.execute('SELECT COUNT(DISTINCT blockNumber) FROM latestBlocks')
                validatedBlockCount = (await cursor_cache.fetchone())[0]

                await cursor_cache.execute('SELECT COUNT(DISTINCT transactionHash) FROM latestTransactions')
                validatedTransactionCount = (await cursor_cache.fetchone())[0]

        return jsonify(
            systemAddressCount=tokenAddressCount,
            systemBlockCount=validatedBlockCount,
            systemTransactionCount=validatedTransactionCount,
            systemSmartContractCount=contractCount,
            systemTokenCount=tokenCount,
            lastscannedblock=lastscannedblock,
            result='ok'
        )

    except Exception as e:
        logger.error(f"Error in systemData function: {e}", exc_info=True)
        return jsonify(result='error', description=INTERNAL_ERROR)







@app.route('/api/v1.0/broadcastTx/<raw_transaction_hash>')
async def broadcastTx(raw_transaction_hash):
    try:
        p1 = subprocess.run(['flo-cli',f"-datadir={FLO_DATA_DIR}",'sendrawtransaction',raw_transaction_hash], capture_output=True)
        return jsonify(args=p1.args,returncode=p1.returncode,stdout=p1.stdout.decode(),stderr=p1.stderr.decode())
    except Exception as e:
        print("broadcastTx:", e)
        return jsonify(result='error', description=INTERNAL_ERROR)
    



@app.route('/api/v1.0/getTokenList', methods=['GET'])
async def getTokenList():
    if not is_backend_ready():
        return jsonify(result='error', description=BACKEND_NOT_READY_ERROR)

    try:
        # Prefix and suffix pattern for identifying token databases
        database_prefix = f"{mysql_config.database_prefix}_"
        database_suffix = "_db"

        token_list = []

        async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn:
            async with conn.cursor(DictCursor) as cursor:
                query = f"""
                    SELECT SCHEMA_NAME FROM SCHEMATA
                    WHERE SCHEMA_NAME LIKE '{database_prefix}%'
                    AND SCHEMA_NAME LIKE '%{database_suffix}'
                """
                await cursor.execute(query)
                all_databases = await cursor.fetchall()

                for row in all_databases:
                    db_name = row['SCHEMA_NAME']
                    stripped_name = db_name[len(database_prefix):-len(database_suffix)]

                    # Skip non-token databases
                    if stripped_name in ["latestCache", "system"]:
                        continue

                    parts = stripped_name.split('_')
                    if len(parts) == 1:
                        token_list.append(stripped_name)
                    elif len(parts) == 2 and len(parts[1]) == 34 and (parts[1].startswith('F') or parts[1].startswith('o')):
                        continue  # Skip smart contract DBs

        return jsonify(tokens=token_list, result='ok')

    except Exception as e:
        logger.error(f"getTokenList: {e}", exc_info=True)
        return jsonify(result='error', description=INTERNAL_ERROR), 500



@app.route('/api/v1.0/getTokenInfo', methods=['GET'])
async def getTokenInfo():
    try:
        token = request.args.get('token')
        if token is None:
            return jsonify(result='error', description="token name hasn't been passed"), 400

        db_name = standardize_db_name(token)

        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor(DictCursor) as cursor:
                # Fetch incorporation details
                await cursor.execute("SELECT * FROM transactionHistory WHERE id = 1")
                incorporationRow = await cursor.fetchone()
                if not incorporationRow:
                    return jsonify(result='error', description='Incorporation details not found'), 404

                # Fetch the number of distinct addresses
                await cursor.execute("SELECT COUNT(DISTINCT address) AS activeAddressCount FROM activeTable")
                distinct_addr_result = await cursor.fetchone()
                numberOf_distinctAddresses = distinct_addr_result['activeAddressCount'] if distinct_addr_result else 0

                # Fetch the total number of transactions
                await cursor.execute("SELECT MAX(id) AS totalTransactions FROM transactionHistory")
                tx_count_result = await cursor.fetchone()
                numberOf_transactions = tx_count_result['totalTransactions'] if tx_count_result else 0

                # Fetch associated contracts
                await cursor.execute("""
                    SELECT contractName, contractAddress, blockNumber, blockHash, transactionHash
                    FROM tokenContractAssociation
                """)
                associatedContracts = await cursor.fetchall()

        associatedContractList = [
            {
                'contractName': item['contractName'],
                'contractAddress': item['contractAddress'],
                'blockNumber': item['blockNumber'],
                'blockHash': item['blockHash'],
                'transactionHash': item['transactionHash'],
            }
            for item in associatedContracts
        ]

        response = {
            'result': 'ok',
            'token': token,
            'incorporationAddress': incorporationRow.get('sourceFloAddress'),
            'tokenSupply': incorporationRow.get('transferAmount'),
            'time': incorporationRow.get('time'),
            'blockchainReference': incorporationRow.get('blockchainReference'),
            'activeAddress_no': numberOf_distinctAddresses,
            'totalTransactions': numberOf_transactions,
            'associatedSmartContracts': associatedContractList,
        }

        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except Exception as e:
        logger.error(f"getTokenInfo: {e}", exc_info=True)
        return jsonify(result='error', description=INTERNAL_ERROR), 500





@app.route('/api/v1.0/getTokenTransactions', methods=['GET'])
async def getTokenTransactions():
    try:
        token = request.args.get('token')
        senderFloAddress = request.args.get('senderFloAddress')
        destFloAddress = request.args.get('destFloAddress')
        limit = request.args.get('limit')

        if token is None:
            return jsonify(result='error', description="token name hasn't been passed"), 400

        db_name = standardize_db_name(token)

        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor(DictCursor) as cursor:
                query = "SELECT jsonData, parsedFloData FROM transactionHistory"
                conditions = []
                params = []

                if senderFloAddress and not destFloAddress:
                    conditions.append("sourceFloAddress = %s")
                    params.append(senderFloAddress)
                elif not senderFloAddress and destFloAddress:
                    conditions.append("destFloAddress = %s")
                    params.append(destFloAddress)
                elif senderFloAddress and destFloAddress:
                    conditions.append("sourceFloAddress = %s AND destFloAddress = %s")
                    params.extend([senderFloAddress, destFloAddress])

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)

                query += " ORDER BY id DESC"
                if limit is not None:
                    if not limit.isdigit():
                        return jsonify(result='error', description='limit validation failed'), 400
                    query += " LIMIT %s"
                    params.append(int(limit))

                await cursor.execute(query, params)
                transactionJsonData = await cursor.fetchall()

        # Parse results
        rowarray_list = {}
        for row in transactionJsonData:
            transactions_object = {
                'transactionDetails': json.loads(row['jsonData']),
                'parsedFloData': json.loads(row['parsedFloData'])


            }
            rowarray_list[transactions_object['transactionDetails']['txid']] = transactions_object

        # Respond
        if not is_backend_ready():
            return jsonify(result='ok', token=token, transactions=rowarray_list, warning=BACKEND_NOT_READY_WARNING), 206
        else:
            return jsonify(result='ok', token=token, transactions=rowarray_list), 200

    except Exception as e:
        logger.error(f"getTokenTransactions: {e}", exc_info=True)
        return jsonify(result='error', description=INTERNAL_ERROR), 500



@app.route('/api/v1.0/getTokenBalances', methods=['GET'])
async def getTokenBalances():
    try:
        token = request.args.get('token')
        if token is None:
            return jsonify(result='error', description="token name hasn't been passed"), 400

        db_name = standardize_db_name(token)

        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                query = "SELECT address, SUM(transferBalance) FROM activeTable GROUP BY address"
                await cursor.execute(query)
                addressBalances = await cursor.fetchall()

        # Build response dict
        returnList = {address: balance for address, balance in addressBalances}

        response = {
            "result": "ok",
            "token": token,
            "balances": returnList
        }

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except Exception as e:
        logger.error(f"getTokenBalances: {e}", exc_info=True)
        return jsonify(result='error', description=INTERNAL_ERROR), 500



# FLO Address APIs
@app.route('/api/v1.0/getFloAddressInfo', methods=['GET'])
async def getFloAddressInfo():
    try:
        floAddress = request.args.get('floAddress')
        if floAddress is None:
            return jsonify(description='floAddress hasn\'t been passed'), 400

        # Connect to the system database asynchronously
        db_name = standardize_db_name("system")
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                # Fetch associated tokens asynchronously
                query = "SELECT token FROM tokenAddressMapping WHERE tokenAddress = %s"
                await cursor.execute(query, (floAddress,))
                tokenNames = await cursor.fetchall()

                # Fetch incorporated contracts asynchronously
                query = """
                    SELECT contractName, status, tokenIdentification, contractType, transactionHash, blockNumber, blockHash 
                    FROM activecontracts 
                    WHERE contractAddress = %s
                """
                await cursor.execute(query, (floAddress,))
                incorporatedContracts = await cursor.fetchall()

        # Prepare token details by querying each token database separately asynchronously
        detailList = {}
        if tokenNames:
            tasks = []
            for token in tokenNames:
                token_name = token[0]
                token_db_name = standardize_db_name(token_name)

                tasks.append(fetch_token_balance(token_name, floAddress))

            # Run all tasks concurrently
            token_balances = await asyncio.gather(*tasks)

            # Add token balances to the details list
            for balance in token_balances:
                detailList.update(balance)

        else:
            # Address is not associated with any token
            if not is_backend_ready():
                return jsonify(result='error', description=BACKEND_NOT_READY_ERROR), 503
            else:
                return jsonify(result='error', description='FLO address is not associated with any tokens'), 404

        # Prepare contract details asynchronously
        incorporatedSmartContracts = []
        if incorporatedContracts:
            for contract in incorporatedContracts:
                tempdict = {
                    'contractName': contract[0],
                    'contractAddress': floAddress,
                    'status': contract[1],
                    'tokenIdentification': contract[2],
                    'contractType': contract[3],
                    'transactionHash': contract[4],
                    'blockNumber': contract[5],
                    'blockHash': contract[6],
                }
                incorporatedSmartContracts.append(tempdict)
        else:
            incorporatedSmartContracts = None

        # Return the response
        response = {
            'result': 'ok',
            'floAddress': floAddress,
            'floAddressBalances': detailList,
            'incorporatedSmartContracts': incorporatedSmartContracts,
        }

        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except Exception as e:
        logger.error(f"getFloAddressInfo: {e}", exc_info=True)
        return jsonify(result='error', description=INTERNAL_ERROR), 500



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
async def getAddressBalance():
    try:
        floAddress = request.args.get('floAddress')
        token = request.args.get('token')

        if floAddress is None:
            return jsonify(result='error', description='floAddress hasn\'t been passed'), 400

        if token is None:
            # Fetch balances for all associated tokens
            db_name = standardize_db_name("system")
            async with get_mysql_conn_ctx(db_name) as conn:
                async with conn.cursor(DictCursor) as cursor:
                    query = "SELECT token FROM tokenAddressMapping WHERE tokenAddress = %s"
                    await cursor.execute(query, (floAddress,))
                    tokenNames = [row['token'] for row in await cursor.fetchall()]

            if tokenNames:
                tasks = [fetch_token_balance(token_name, floAddress) for token_name in tokenNames]
                results = await asyncio.gather(*tasks)

                # Combine results
                detailList = {k: v for result in results for k, v in result.items()}

                response = {
                    'result': 'ok',
                    'floAddress': floAddress,
                    'floAddressBalances': detailList
                }

                if not is_backend_ready():
                    response['warning'] = BACKEND_NOT_READY_WARNING
                    return jsonify(response), 206
                else:
                    return jsonify(response), 200
            else:
                if not is_backend_ready():
                    return jsonify(result='error', description=BACKEND_NOT_READY_ERROR), 503
                else:
                    return jsonify(result='error', description='FLO address is not associated with any tokens'), 404

        else:
            # Fetch balance for the specific token
            token_db_name = standardize_db_name(token)
            async with get_mysql_conn_ctx(token_db_name) as conn:
                async with conn.cursor(DictCursor) as cursor:
                    query = "SELECT SUM(transferBalance) AS balance FROM activeTable WHERE address = %s"
                    await cursor.execute(query, (floAddress,))
                    result = await cursor.fetchone()
                    balance = result['balance'] or 0 if result else 0

            response = {
                'result': 'ok',
                'token': token,
                'floAddress': floAddress,
                'balance': balance
            }

            if not is_backend_ready():
                response['warning'] = BACKEND_NOT_READY_WARNING
                return jsonify(response), 206
            else:
                return jsonify(response), 200

    except Exception as e:
        print("getAddressBalance:", e)
        return jsonify(result='error', description=INTERNAL_ERROR), 500





@app.route('/api/v1.0/getFloAddressTransactions', methods=['GET'])
async def getFloAddressTransactions():
    try:
        floAddress = request.args.get('floAddress')
        token = request.args.get('token')
        limit = request.args.get('limit')

        if floAddress is None:
            return jsonify(result='error', description='floAddress has not been passed'), 400

        # Handle token-specific or all-token scenarios
        if token is None:
            # Fetch all tokens associated with the floAddress
            db_name = standardize_db_name("system")
            async with get_mysql_conn_ctx(db_name) as conn:
                async with conn.cursor(DictCursor) as cursor:
                    query = "SELECT token FROM tokenAddressMapping WHERE tokenAddress = %s"
                    await cursor.execute(query, (floAddress,))
                    tokenNames = await cursor.fetchall()
        else:
            # Check if the token database exists
            token_db_name = standardize_db_name(token)
            try:
                async with get_mysql_conn_ctx(token_db_name):
                    tokenNames = [{'token': token}]
            except Exception:
                if not is_backend_ready():
                    return jsonify(result='error', description=BACKEND_NOT_READY_ERROR), 503
                else:
                    return jsonify(result='error', description='Token does not exist'), 404

        # Process transactions for the tokens
        if tokenNames:
            allTransactionList = {}
            tasks = []

            for token_row in tokenNames:
                tokenname = token_row['token']
                token_db_name = standardize_db_name(tokenname)
                tasks.append(fetch_transactions_for_token(token_db_name, floAddress, limit))

            # Run all tasks concurrently
            transaction_data = await asyncio.gather(*tasks)

            # Process fetched transactions
            for data in transaction_data:
                for row in data:
                    transactions_object = {
                        'transactionDetails': json.loads(row['jsonData']),
                        'parsedFloData': json.loads(row['parsedFloData'])
                    }
                    allTransactionList[transactions_object['transactionDetails']['txid']] = transactions_object

            response = {
                'result': 'ok',
                'floAddress': floAddress,
                'transactions': allTransactionList
            }
            if token is not None:
                response['token'] = token
            if not is_backend_ready():
                response['warning'] = BACKEND_NOT_READY_WARNING
                return jsonify(response), 206
            else:
                return jsonify(response), 200

        else:
            if not is_backend_ready():
                return jsonify(result='error', description=BACKEND_NOT_READY_ERROR), 503
            else:
                return jsonify(result='error', description='No token transactions present on this address'), 404

    except Exception as e:
        print("getFloAddressTransactions:", e)
        return jsonify(result='error', description=INTERNAL_ERROR), 500



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
async def getContractList():
    try:
        # Get query parameters
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        # Standardize system database name
        system_db_name = standardize_db_name("system")

        # Initialize contract list
        contractList = []

        # Connect to the system database asynchronously and manage it with async context
        async with get_mysql_conn_ctx(system_db_name) as conn:
            async with conn.cursor() as cursor:

                # Build the query dynamically based on input parameters
                query = "SELECT * FROM activecontracts"
                conditions = []
                params = []

                if contractName:
                    conditions.append("contractName=%s")
                    params.append(contractName)

                if contractAddress:
                    conditions.append("contractAddress=%s")
                    params.append(contractAddress)

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)

                # Execute the query asynchronously
                await cursor.execute(query, tuple(params))
                allcontractsDetailList = await cursor.fetchall()

                # Process the results asynchronously
                for contract in allcontractsDetailList:
                    contractDict = {
                        'contractName': contract[1],
                        'contractAddress': contract[2],
                        'status': contract[3],
                        'tokenIdentification': contract[4],
                        'contractType': contract[5],
                        'transactionHash': contract[6],
                        'blockNumber': contract[7],
                        'incorporationDate': contract[8],
                    }
                    if contract[9]:
                        contractDict['expiryDate'] = contract[9]
                    if contract[10]:
                        contractDict['closeDate'] = contract[10]

                    contractList.append(contractDict)

        # Check backend readiness and return response
        if not is_backend_ready():
            return jsonify(smartContracts=contractList, result='ok', warning=BACKEND_NOT_READY_WARNING), 206
        else:
            return jsonify(smartContracts=contractList, result='ok'), 200

    except Exception as e:
        print("getContractList:", e)
        return jsonify(result='error', description=INTERNAL_ERROR), 500


    
@app.route('/api/v1.0/getSmartContractInfo', methods=['GET'], endpoint='getSmartContractInfoV1')
async def getContractInfo():
    logger.info("Entering getContractInfo function")
    try:
        # Get query parameters
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not contractName:
            logger.error("Contract name is missing")
            return jsonify(result='error', description="Smart Contract's name hasn't been passed"), 400
        if not contractAddress:
            logger.error("Contract address is missing")
            return jsonify(result='error', description="Smart Contract's address hasn't been passed"), 400

        # Standardize the smart contract database name
        contract_db_name = standardize_db_name(f"{contractName}_{contractAddress}")
        logger.info(f"Standardized contract database name: {contract_db_name}")

        # Initialize response dictionary
        returnval = {}

        # Fetch contract structure
        try:
            async with get_mysql_conn_ctx(contract_db_name) as contract_conn:
                async with contract_conn.cursor() as cursor:
                    await cursor.execute("SELECT attribute, value FROM contractstructure")
                    result = await cursor.fetchall()
                    if not result:
                        logger.error("No contract structure found")
                        return jsonify(result='error', description="No contract structure found for the specified smart contract"), 404

                    # Process contract structure data
                    contractStructure = {}
                    conditionDict = {}
                    for item in result:
                        if item[0] == 'exitconditions':
                            conditionDict[len(conditionDict)] = item[1]
                        else:
                            contractStructure[item[0]] = item[1]

                    if conditionDict:
                        contractStructure['exitconditions'] = conditionDict

                    # Update the response dictionary
                    returnval.update(contractStructure)
                    if 'exitconditions' in returnval:
                        returnval['userChoice'] = returnval.pop('exitconditions')

        except Exception as e:
            logger.error(f"Error fetching contract structure: {e}", exc_info=True)
            return jsonify(result='error', description="Failed to fetch contract structure"), 500

        # Return the final response
        logger.info("Returning final response with contract information")
        return jsonify(result='ok', contractName=contractName, contractAddress=contractAddress, contractInfo=returnval), 200

    except Exception as e:
        logger.error(f"Unhandled error in getContractInfo: {e}", exc_info=True)
        return jsonify(result='error', description="Internal Server Error"), 500





@app.route('/api/v1.0/getSmartContractParticipants', methods=['GET'])
async def getcontractparticipants():
    try:
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not contractName:
            return jsonify(result='error', description="Smart Contract's name hasn't been passed"), 400

        if not contractAddress:
            return jsonify(result='error', description="Smart Contract's address hasn't been passed"), 400

        contractName = contractName.strip().lower()
        contractAddress = contractAddress.strip()

        # Standardize smart contract database name
        contract_db_name = standardize_db_name(f"{contractName}_{contractAddress}")

        # Fetch contract structure asynchronously
        contractStructure = await fetchContractStructure(contractName, contractAddress)

        returnval = {}

        async with get_mysql_conn_ctx(contract_db_name) as conn:
            async with conn.cursor(DictCursor) as cursor:

                if 'exitconditions' in contractStructure:
                    await cursor.execute('SELECT * FROM contractTransactionHistory WHERE transactionType="trigger"')
                    triggers = await cursor.fetchall()

                    if len(triggers) == 1:
                        await cursor.execute('SELECT value FROM contractstructure WHERE attribute="tokenIdentification"')
                        token_row = await cursor.fetchone()
                        token = token_row['value'] if token_row else None

                        await cursor.execute('SELECT id, participantAddress, tokenAmount, userChoice, transactionHash, winningAmount FROM contractparticipants')
                        result = await cursor.fetchall()
                        for row in result:
                            returnval[row['participantAddress']] = {
                                'participantFloAddress': row['participantAddress'],
                                'tokenAmount': row['tokenAmount'],
                                'userChoice': row['userChoice'],
                                'transactionHash': row['transactionHash'],
                                'winningAmount': row['winningAmount'],
                                'tokenIdentification': token
                            }

                    elif len(triggers) == 0:
                        await cursor.execute('SELECT id, participantAddress, tokenAmount, userChoice, transactionHash FROM contractparticipants')
                        result = await cursor.fetchall()
                        for row in result:
                            returnval[row['participantAddress']] = {
                                'participantFloAddress': row['participantAddress'],
                                'tokenAmount': row['tokenAmount'],
                                'userChoice': row['userChoice'],
                                'transactionHash': row['transactionHash']
                            }

                    else:
                        return jsonify(result='error', description='More than 1 trigger present. This is unusual, please check your code'), 500

                elif 'payeeAddress' in contractStructure:
                    await cursor.execute('SELECT id, participantAddress, tokenAmount, userChoice, transactionHash FROM contractparticipants')
                    result = await cursor.fetchall()
                    for row in result:
                        returnval[row['participantAddress']] = {
                            'participantFloAddress': row['participantAddress'],
                            'tokenAmount': row['tokenAmount'],
                            'userChoice': row['userChoice'],
                            'transactionHash': row['transactionHash']
                        }

                elif contractStructure['contractType'] == 'continuos-event' and contractStructure['subtype'] == 'tokenswap':
                    await cursor.execute('SELECT * FROM contractparticipants')
                    contract_participants = await cursor.fetchall()
                    for row in contract_participants:
                        returnval[row['participantAddress']] = {
                            'participantFloAddress': row['participantAddress'],
                            'participationAmount': row['participationAmount'],
                            'swapPrice': float(row['userChoice']),
                            'transactionHash': row['transactionHash'],
                            'blockNumber': row['blockNumber'],
                            'blockHash': row['blockHash'],
                            'swapAmount': row['winningAmount']
                        }

        if not is_backend_ready():
            return jsonify(
                result='ok',
                warning=BACKEND_NOT_READY_WARNING,
                contractName=contractName,
                contractAddress=contractAddress,
                participantInfo=returnval
            ), 206
        else:
            return jsonify(
                result='ok',
                contractName=contractName,
                contractAddress=contractAddress,
                participantInfo=returnval
            ), 200

    except Exception as e:
        print("getcontractparticipants:", e)
        return jsonify(result='error', description=INTERNAL_ERROR), 500




    

@app.route('/api/v1.0/getParticipantDetails', methods=['GET'], endpoint='getParticipantDetailsV1')
async def getParticipantDetails():
    try:
        floAddress = request.args.get('floAddress')
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not floAddress:
            return jsonify(result='error', description="FLO address hasn't been passed"), 400

        if (contractName and not contractAddress) or (not contractName and contractAddress):
            return jsonify(result='error', description='Pass both contractName and contractAddress as URL parameters'), 400

        floAddress = floAddress.strip()
        if contractName:
            contractName = contractName.strip().lower()
        if contractAddress:
            contractAddress = contractAddress.strip()

        # Standardize the contract database name
        system_db_name = standardize_db_name("system")
        contract_db_name = standardize_db_name(f"{contractName}_{contractAddress}") if contractName and contractAddress else None

        # Connect to the system database asynchronously
        async with get_mysql_conn_ctx(system_db_name) as conn_system:
            async with conn_system.cursor() as cursor_system:
                # Query contract participation details for the FLO address
                query = """
                    SELECT * 
                    FROM contractAddressMapping 
                    WHERE address = %s AND addressType = 'participant'
                """
                params = [floAddress]

                if contractName and contractAddress:
                    query += " AND contractName = %s AND contractAddress = %s"
                    params.extend([contractName, contractAddress])

                await cursor_system.execute(query, tuple(params))
                participant_address_contracts = await cursor_system.fetchall()

                if not participant_address_contracts:
                    if not is_backend_ready():
                        return jsonify(result='error', description=BACKEND_NOT_READY_ERROR), 503
                    else:
                        return jsonify(result='error', description="Address hasn't participated in any contract"), 404

                participationDetailsList = []
                for contract in participant_address_contracts:
                    detailsDict = {}
                    contract_name = contract[3]
                    contract_address = contract[4]
                    db_name = standardize_db_name(f"{contract_name}_{contract_address}")

                    # Fetch participation details from the contract database
                    async with get_mysql_conn_ctx(db_name) as conn_contract:
                        async with conn_contract.cursor() as cursor_contract:
                            # Fetch contract structure asynchronously
                            contractStructure = await fetchContractStructure(contract_name, contract_address)

                            if contractStructure['contractType'] == 'continuos-event' and contractStructure['subtype'] == 'tokenswap':
                                await cursor_contract.execute("SELECT * FROM contractparticipants WHERE participantAddress = %s", (floAddress,))
                                participant_details = await cursor_contract.fetchall()
                                participationList = []
                                for participation in participant_details:
                                    detailsDict = {
                                        'participationAddress': floAddress,
                                        'participationAmount': participation[2],
                                        'receivedAmount': float(participation[3]),
                                        'participationToken': contractStructure['accepting_token'],
                                        'receivedToken': contractStructure['selling_token'],
                                        'swapPrice_received_to_participation': float(participation[7]),
                                        'transactionHash': participation[4],
                                        'blockNumber': participation[5],
                                        'blockHash': participation[6],
                                    }
                                    participationList.append(detailsDict)
                                participationDetailsList.append(participationList)

                            elif contractStructure['contractType'] == 'one-time-event' and 'payeeAddress' in contractStructure:
                                detailsDict['contractName'] = contract_name
                                detailsDict['contractAddress'] = contract_address

                                await cursor_system.execute('''
                                    SELECT status, tokenIdentification, contractType, blockNumber, blockHash, incorporationDate, expiryDate, closeDate 
                                    FROM activecontracts 
                                    WHERE contractName = %s AND contractAddress = %s
                                ''', (contract_name, contract_address))
                                temp = await cursor_system.fetchone()
                                detailsDict.update({
                                    'status': temp[0],
                                    'tokenIdentification': temp[1],
                                    'contractType': temp[2],
                                    'blockNumber': temp[3],
                                    'blockHash': temp[4],
                                    'incorporationDate': temp[5],
                                    'expiryDate': temp[6],
                                    'closeDate': temp[7]
                                })

                                await cursor_contract.execute("SELECT tokenAmount FROM contractparticipants WHERE participantAddress = %s", (floAddress,))
                                result = await cursor_contract.fetchone()
                                detailsDict['tokenAmount'] = result[0]
                                participationDetailsList.append(detailsDict)

                            elif contractStructure['contractType'] == 'one-time-event' and 'exitconditions' in contractStructure:
                                detailsDict['contractName'] = contract_name
                                detailsDict['contractAddress'] = contract_address

                                await cursor_system.execute('''
                                    SELECT status, tokenIdentification, contractType, blockNumber, blockHash, incorporationDate, expiryDate, closeDate 
                                    FROM activecontracts 
                                    WHERE contractName = %s AND contractAddress = %s
                                ''', (contract_name, contract_address))
                                temp = await cursor_system.fetchone()
                                detailsDict.update({
                                    'status': temp[0],
                                    'tokenIdentification': temp[1],
                                    'contractType': temp[2],
                                    'blockNumber': temp[3],
                                    'blockHash': temp[4],
                                    'incorporationDate': temp[5],
                                    'expiryDate': temp[6],
                                    'closeDate': temp[7]
                                })

                                await cursor_contract.execute("""
                                    SELECT userChoice, winningAmount 
                                    FROM contractparticipants 
                                    WHERE participantAddress = %s
                                """, (floAddress,))
                                result = await cursor_contract.fetchone()
                                detailsDict['userChoice'] = result[0]
                                detailsDict['winningAmount'] = result[1]
                                participationDetailsList.append(detailsDict)

                # Final response based on backend readiness
                if not is_backend_ready():
                    return jsonify(
                        warning=BACKEND_NOT_READY_WARNING,
                        floAddress=floAddress,
                        type='participant',
                        participatedContracts=participationDetailsList
                    ), 206
                else:
                    return jsonify(
                        floAddress=floAddress,
                        type='participant',
                        participatedContracts=participationDetailsList
                    ), 200

    except Exception as e:
        print("getParticipantDetails:", e)
        return jsonify(description="Unexpected error occurred"), 500



    

@app.route('/api/v1.0/getSmartContractTransactions', methods=['GET'])
async def getsmartcontracttransactions():
    try:
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not contractName:
            return jsonify(result='error', description="Smart Contract's name hasn't been passed"), 400

        if not contractAddress:
            return jsonify(result='error', description="Smart Contract's address hasn't been passed"), 400

        # Standardize the contract database name
        contract_db_name = standardize_db_name(f"{contractName.strip()}_{contractAddress.strip()}")

        # Establish asynchronous connection to the contract database
        async with get_mysql_conn_ctx(contract_db_name) as conn:
            async with conn.cursor() as cursor:
                # Fetch contract transaction data asynchronously
                query = '''
                    SELECT jsonData, parsedFloData 
                    FROM contractTransactionHistory
                '''
                await cursor.execute(query)
                result = await cursor.fetchall()

                # Process the fetched transaction data
                returnval = {}
                for item in result:
                    transactions_object = {}
                    transactions_object['transactionDetails'] = json.loads(item[0])
                    #transactions_object['transactionDetails'] = await update_transaction_confirmations(transactions_object['transactionDetails'])
                    transactions_object['parsedFloData'] = json.loads(item[1])
                    returnval[transactions_object['transactionDetails']['txid']] = transactions_object

        # Check backend readiness
        if not is_backend_ready():
            return jsonify(
                result='ok',
                warning=BACKEND_NOT_READY_WARNING,
                contractName=contractName,
                contractAddress=contractAddress,
                contractTransactions=returnval
            ), 206
        else:
            return jsonify(
                result='ok',
                contractName=contractName,
                contractAddress=contractAddress,
                contractTransactions=returnval
            ), 200

    except aiomysql.MySQLError as e:
        print(f"MySQL error while fetching transactions: {e}")
        return jsonify(result='error', description='Database error occurred'), 500

    except Exception as e:
        print("getSmartContractTransactions:", e)
        return jsonify(result='error', description=INTERNAL_ERROR), 500



    

@app.route('/api/v1.0/getBlockDetails/<blockdetail>', methods=['GET'])
async def getblockdetails(blockdetail):
    try:
        blockJson = await blockdetailhelper(blockdetail)
        if len(blockJson) != 0:
            blockJson = json.loads(blockJson[0][0])
            return jsonify(result='ok', blockDetails=blockJson)
        else:
            if not is_backend_ready():
                return jsonify(result='error', description=BACKEND_NOT_READY_ERROR)
            else:
                return jsonify(result='error', description='Block doesn\'t exist in database')
    except Exception as e:
        print("getblockdetails:", e)
        return jsonify(result='error', description=INTERNAL_ERROR)


@app.route('/api/v1.0/getTransactionDetails/<transactionHash>', methods=['GET'])
async def gettransactiondetails(transactionHash):
    try:
        transactionJsonData = await transactiondetailhelper(transactionHash)
        if len(transactionJsonData) != 0:
            transactionJson = json.loads(transactionJsonData[0][0])
            #transactionJson = await update_transaction_confirmations(transactionJson)
            parseResult = json.loads(transactionJsonData[0][1])

            return jsonify(parsedFloData=parseResult, transactionDetails=transactionJson, transactionHash=transactionHash, result='ok')
        else:
            if not is_backend_ready():
                return jsonify(result='error', description=BACKEND_NOT_READY_ERROR)
            else:
                return jsonify(result='error', description='Transaction doesn\'t exist in database')
    except Exception as e:
        print("gettransactiondetails:", e)
        return jsonify(result='error', description=INTERNAL_ERROR)


@app.route('/api/v1.0/getLatestTransactionDetails', methods=['GET'])
async def getLatestTransactionDetails():
    try:
        # Validate and set the `numberOfLatestBlocks` parameter
        numberOfLatestBlocks = request.args.get('numberOfLatestBlocks')
        numberOfLatestBlocks = int(numberOfLatestBlocks) if numberOfLatestBlocks and numberOfLatestBlocks.isdigit() else None

        # Default to fetching transactions from the latest 4 blocks if no parameter is provided
        if numberOfLatestBlocks is None:
            numberOfLatestBlocks = 4

        # Standardize the database name
        db_name = standardize_db_name('latestCache')

        # Connect to the database asynchronously
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                # Optimized query using JOIN instead of `IN`
                query = """
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
                """

                # Execute the query with the specified limit
                await cursor.execute(query, (numberOfLatestBlocks,))
                latestTransactions = await cursor.fetchall()

                # Process each transaction and store in a dictionary
                tempdict = {}
                for item in latestTransactions:
                    try:
                        item = list(item)
                        tx_parsed_details = {}

                        # Parse transaction details
                        tx_parsed_details['transactionDetails'] = json.loads(item[3])
                        tx_parsed_details['parsedFloData'] = json.loads(item[5])
                        tx_parsed_details['parsedFloData']['transactionType'] = item[4]
                        tx_parsed_details['transactionDetails']['blockheight'] = int(item[2])

                        # Merge transaction and parsed details
                        tx_parsed_details = {
                            **tx_parsed_details['transactionDetails'],
                            **tx_parsed_details['parsedFloData']
                        }

                        # Add on-chain flag
                        tx_parsed_details['onChain'] = True

                        # Add transaction to the dictionary using txid as the key
                        txid = tx_parsed_details['txid']
                        tempdict[txid] = tx_parsed_details
                    except json.JSONDecodeError as json_error:
                        print(f"JSON decode error for transaction {item[0]}: {json_error}")
                    except Exception as process_error:
                        print(f"Error processing transaction {item[0]}: {process_error}")

        # Prepare the response
        response = {
            'result': 'ok',
            'latestTransactions': tempdict,
        }

        # Include a readiness warning if the backend isn't ready
        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except ValueError:
        # Handle invalid `numberOfLatestBlocks` value
        return jsonify(result='error', description='Invalid numberOfLatestBlocks value provided'), 400
    except Exception as e:
        # Print error traceback for debugging
        import traceback
        traceback.print_exc()
        print("getLatestTransactionDetails Error:", e)
        return jsonify(result='error', description=INTERNAL_ERROR), 500



    


@app.route('/api/v1.0/getLatestBlockDetails', methods=['GET'])
async def getLatestBlockDetails():
    try:
        # Validate and set the limit
        limit = request.args.get('limit')
        limit = int(limit) if limit and limit.isdigit() else 4

        # Standardize the database name
        db_name = standardize_db_name('latestCache')

        # Connect to the database asynchronously
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                # Optimized query using JOIN to avoid subquery issues
                query = """
                    SELECT lb.* 
                    FROM latestBlocks lb
                    JOIN (
                        SELECT blockNumber 
                        FROM latestBlocks 
                        ORDER BY blockNumber DESC 
                        LIMIT %s
                    ) AS recent_blocks
                    ON lb.blockNumber = recent_blocks.blockNumber
                    ORDER BY lb.id ASC;
                """

                # Execute the query with the specified limit
                await cursor.execute(query, (limit,))
                latestBlocks = await cursor.fetchall()

                # Parse the block details
                block_details = {
                    json.loads(item[3])['hash']: json.loads(item[3])
                    for item in latestBlocks
                }

        # Prepare the response
        response = {
            'result': 'ok',
            'latestBlocks': block_details,
        }

        # Include a readiness warning if the backend isn't ready
        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except ValueError:
        # Handle invalid limit value
        return jsonify(result='error', description='Invalid limit value provided'), 400
    except Exception as e:
        # Print error traceback for debugging
        import traceback
        traceback.print_exc()
        print("getLatestBlockDetails Error:", e)
        return jsonify(result='error', description=INTERNAL_ERROR), 500




    

@app.route('/api/v1.0/getBlockTransactions/<blockdetail>', methods=['GET'])
async def getblocktransactions(blockdetail):
    try:
        blockJson = await blockdetailhelper(blockdetail)
        if len(blockJson) != 0:
            blockJson = json.loads(blockJson[0][0])
            blocktxlist = blockJson['txs']
            blocktxs = {}
            for tx in blocktxlist:
                temptx = await transactiondetailhelper(tx['txid'])
                transactionJson = json.loads(temptx[0][0])
                parseResult = json.loads(temptx[0][1])
                blocktxs[tx['txid']] = {
                    "parsedFloData": parseResult,
                    "transactionDetails": transactionJson
                }

            return jsonify(result='ok', transactions=blocktxs, blockKeyword=blockdetail)
        else:
            if not is_backend_ready():
                return jsonify(result='error', description=BACKEND_NOT_READY_ERROR)
            else:
                return jsonify(result='error', description='Block doesn\'t exist in database')


    except Exception as e:
        print("getblocktransactions:", e)
        return jsonify(result='error', description=INTERNAL_ERROR)
    

@app.route('/api/v1.0/categoriseString/<urlstring>', methods=['GET'])
async def categoriseString(urlstring):
    try:
        # Check if the string is a transaction hash
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{apiUrl}/api/v1/tx/{urlstring}") as response:
                if response.status == 200:
                    return jsonify(type="transaction"), 200

            # Check if it's a block hash
            async with session.get(f"{apiUrl}/api/v1/block/{urlstring}") as response:
                if response.status == 200:
                    return jsonify(type="block"), 200

        database_prefix = f"{mysql_config.database_prefix}_"
        database_suffix = "_db"
        token_names = set()
        contract_list = []

        # Connect to information_schema to list databases
        async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn_info:
            async with conn_info.cursor(DictCursor) as cursor_info:
                await cursor_info.execute(
                    f"""
                    SELECT SCHEMA_NAME FROM SCHEMATA
                    WHERE SCHEMA_NAME LIKE '{database_prefix}%' AND SCHEMA_NAME LIKE '%{database_suffix}'
                    """
                )
                databases = await cursor_info.fetchall()

                for db in databases:
                    db_name = db['SCHEMA_NAME']
                    stripped_name = db_name[len(database_prefix):-len(database_suffix)]

                    if stripped_name in ["latestCache", "system"]:
                        continue

                    parts = stripped_name.split('_')
                    if len(parts) == 1:
                        token_names.add(stripped_name.lower())
                    elif len(parts) == 2 and len(parts[1]) == 34 and (parts[1].startswith('F') or parts[1].startswith('o')):
                        contract_list.append({
                            "contractName": parts[0],
                            "contractAddress": parts[1]
                        })

        # Check if string matches a token
        if urlstring.lower() in token_names:
            return jsonify(type="token"), 200

        # Check for smart contract name match
        async with get_mysql_conn_ctx("system") as conn_system:
            async with conn_system.cursor(DictCursor) as cursor_system:
                await cursor_system.execute("SELECT DISTINCT contractName FROM activecontracts")
                smart_contract_names = {row['contractName'].lower() for row in await cursor_system.fetchall()}

                if urlstring.lower() in smart_contract_names:
                    return jsonify(type="smartContract"), 200

        # NEW: Check for full contractName_contractAddress format match
        for contract in contract_list:
            if urlstring == f"{contract['contractName']}_{contract['contractAddress']}":
                return jsonify(type="smartContract"), 200

        # Otherwise, treat as noise
        return jsonify(type="noise"), 200

    except Exception as e:
        print("categoriseString:", e)
        return jsonify(result="error", description=INTERNAL_ERROR), 500







@app.route('/api/v1.0/getTokenSmartContractList', methods=['GET'])
async def getTokenSmartContractList():
    try:
        database_prefix = f"{mysql_config.database_prefix}_"
        database_suffix = "_db"

        token_list = []
        contract_list = []

        # Query information_schema to fetch all schema names
        async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn_info:
            async with conn_info.cursor(DictCursor) as cursor_info:
                await cursor_info.execute(
                    f"""
                    SELECT SCHEMA_NAME FROM SCHEMATA
                    WHERE SCHEMA_NAME LIKE '{database_prefix}%' AND SCHEMA_NAME LIKE '%{database_suffix}'
                    """
                )
                all_databases = await cursor_info.fetchall()

                for db in all_databases:
                    db_name = db["SCHEMA_NAME"]
                    stripped_name = db_name[len(database_prefix):-len(database_suffix)]

                    if stripped_name in ["latestCache", "system"]:
                        continue

                    parts = stripped_name.split('_')

                    if len(parts) == 1:
                        token_list.append(stripped_name)
                    elif len(parts) == 2 and len(parts[1]) == 34 and (parts[1].startswith('F') or parts[1].startswith('o')):
                        contract_list.append({
                            "contractName": parts[0],
                            "contractAddress": parts[1]
                        })

        # Add smart contract details from activecontracts table
        async with get_mysql_conn_ctx("system") as conn_system:
            async with conn_system.cursor(DictCursor) as cursor_system:
                await cursor_system.execute("SELECT * FROM activecontracts")
                all_contracts_detail_list = await cursor_system.fetchall()

                for contract in all_contracts_detail_list:
                    for item in contract_list:
                        if item["contractName"] == contract["contractName"] and item["contractAddress"] == contract["contractAddress"]:
                            item.update({
                                "status": contract["status"],
                                "tokenIdentification": contract["tokenIdentification"],
                                "contractType": contract["contractType"],
                                "transactionHash": contract["transactionHash"],
                                "blockNumber": contract["blockNumber"],
                                "blockHash": contract["blockHash"],
                                "incorporationDate": contract["incorporationDate"],
                                "expiryDate": contract["expiryDate"] if contract["expiryDate"] else None,
                                "closeDate": contract["closeDate"] if contract["closeDate"] else None,
                            })

        response = {
            "tokens": token_list,
            "smartContracts": contract_list,
            "result": "ok"
        }

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except aiomysql.MySQLError as e:
        print("Database error in getTokenSmartContractList:", e)
        return jsonify(result="error", description="Database error occurred"), 500
    except Exception as e:
        print("getTokenSmartContractList:", e)
        return jsonify(result="error", description=INTERNAL_ERROR), 500


    


###################
###  VERSION 2  ###
###################

@app.route('/api/v2/info', methods=['GET'])
async def info():
    try:
        # Standardize database names
        system_db_name = standardize_db_name("system")
        latest_cache_db_name = standardize_db_name("latestCache")

        # Initialize data variables
        tokenAddressCount = tokenCount = contractCount = lastscannedblock = 0
        validatedBlockCount = validatedTransactionCount = 0

        # Use async with for resource management
        async with get_mysql_conn_ctx(system_db_name) as conn_system:
            async with conn_system.cursor() as cursor_system:
                # Query for the number of FLO addresses in tokenAddress mapping
                await cursor_system.execute("SELECT COUNT(DISTINCT tokenAddress) FROM tokenAddressMapping")
                tokenAddressCount = (await cursor_system.fetchone())[0]

                await cursor_system.execute("SELECT COUNT(DISTINCT token) FROM tokenAddressMapping")
                tokenCount = (await cursor_system.fetchone())[0]

                await cursor_system.execute("SELECT COUNT(DISTINCT contractName) FROM contractAddressMapping")
                contractCount = (await cursor_system.fetchone())[0]

                await cursor_system.execute("SELECT value FROM systemData WHERE attribute='lastblockscanned'")
                lastscannedblock = int((await cursor_system.fetchone())[0])

        async with get_mysql_conn_ctx(latest_cache_db_name) as conn_latest_cache:
            async with conn_latest_cache.cursor() as cursor_latest_cache:
                # Query for total number of validated blocks
                await cursor_latest_cache.execute("SELECT COUNT(DISTINCT blockNumber) FROM latestBlocks")
                validatedBlockCount = (await cursor_latest_cache.fetchone())[0]

                await cursor_latest_cache.execute("SELECT COUNT(DISTINCT transactionHash) FROM latestTransactions")
                validatedTransactionCount = (await cursor_latest_cache.fetchone())[0]

        # Construct response based on backend readiness
        response_data = {
            "systemAddressCount": tokenAddressCount,
            "systemBlockCount": validatedBlockCount,
            "systemTransactionCount": validatedTransactionCount,
            "systemSmartContractCount": contractCount,
            "systemTokenCount": tokenCount,
            "lastscannedblock": lastscannedblock
        }

        if not is_backend_ready():
            response_data["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response_data), 206
        else:
            return jsonify(response_data), 200

    except Exception as e:
        print("info:", e)
        return jsonify(description=INTERNAL_ERROR), 500



    

@app.route('/api/v2/broadcastTx/<raw_transaction_hash>')
async def broadcastTx_v2(raw_transaction_hash):
    try:
        p1 = subprocess.run(['flo-cli',f"-datadir={FLO_DATA_DIR}",'sendrawtransaction',raw_transaction_hash], capture_output=True)
        return jsonify(args=p1.args,returncode=p1.returncode,stdout=p1.stdout.decode(),stderr=p1.stderr.decode()), 200


    except Exception as e:
        print("broadcastTx_v2:", e)
        return jsonify(description=INTERNAL_ERROR), 500
    


@app.route('/api/v2/tokenList', methods=['GET'])
async def tokenList():
    try:
        token_list = []

        # Prefix and suffix for token databases
        database_prefix = f"{mysql_config.database_prefix}_"
        database_suffix = "_db"

        # Connect to MySQL information_schema using DictCursor
        async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn:
            async with conn.cursor(DictCursor) as cursor:
                try:
                    await cursor.execute(
                        f"""
                        SELECT SCHEMA_NAME FROM SCHEMATA
                        WHERE SCHEMA_NAME LIKE '{database_prefix}%' 
                        AND SCHEMA_NAME LIKE '%{database_suffix}'
                        """
                    )
                    all_databases = [row["SCHEMA_NAME"] for row in await cursor.fetchall()]

                    for db_name in all_databases:
                        stripped_name = db_name[len(database_prefix):-len(database_suffix)]

                        if stripped_name in ["latestCache", "system"]:
                            continue

                        parts = stripped_name.split('_')
                        if len(parts) == 1:
                            token_list.append(stripped_name)
                        elif len(parts) == 2 and len(parts[1]) == 34 and (parts[1].startswith('F') or parts[1].startswith('o')):
                            continue  # Skip smart contract databases

                except Exception as e:
                    print(f"Error querying databases: {e}")
                    return jsonify(description="Error querying databases"), 500

        # Prepare response
        if not is_backend_ready():
            return jsonify(warning=BACKEND_NOT_READY_WARNING, tokens=token_list), 206
        else:
            return jsonify(tokens=token_list), 200

    except Exception as e:
        print(f"tokenList: {e}")
        return jsonify(description=INTERNAL_ERROR), 500








@app.route('/api/v2/tokenInfo/<token>', methods=['GET'])
async def tokenInfo(token):
    try:
        if token is None:
            return jsonify(description='Token name has not been passed'), 400

        token_db_name = standardize_db_name(token)

        try:
            async with get_mysql_conn_ctx(token_db_name) as conn:
                async with conn.cursor(DictCursor) as cursor:
                    # Fetch incorporation data
                    await cursor.execute('SELECT * FROM transactionHistory WHERE id=1')
                    incorporationRow = await cursor.fetchone()

                    if not incorporationRow:
                        return jsonify(description='Incorporation record not found'), 404

                    # Fetch number of distinct active addresses
                    await cursor.execute('SELECT COUNT(DISTINCT address) AS count FROM activeTable')
                    address_count_row = await cursor.fetchone()
                    numberOf_distinctAddresses = address_count_row['count']

                    # Fetch total number of transactions
                    await cursor.execute('SELECT MAX(id) AS max_id FROM transactionHistory')
                    transaction_count_row = await cursor.fetchone()
                    numberOf_transactions = transaction_count_row['max_id']

                    # Fetch associated contracts
                    await cursor.execute('''
                        SELECT contractName, contractAddress, blockNumber, blockHash, transactionHash
                        FROM tokenContractAssociation
                    ''')
                    associatedContracts = await cursor.fetchall()

            associatedContractList = [
                {
                    'contractName': item['contractName'],
                    'contractAddress': item['contractAddress'],
                    'blockNumber': item['blockNumber'],
                    'blockHash': item['blockHash'],
                    'transactionHash': item['transactionHash'],
                }
                for item in associatedContracts
            ]

            response = {
                'token': token,
                'incorporationAddress': incorporationRow['sourceFloAddress'],
                'tokenSupply': incorporationRow['transferAmount'],
                'time': incorporationRow['time'],
                'blockchainReference': incorporationRow['blockchainReference'],
                'activeAddress_no': numberOf_distinctAddresses,
                'totalTransactions': numberOf_transactions,
                'associatedSmartContracts': associatedContractList,
            }

            if not is_backend_ready():
                return jsonify(warning=BACKEND_NOT_READY_WARNING, **response), 206
            else:
                return jsonify(**response), 200

        except aiomysql.MySQLError:
            if not is_backend_ready():
                return jsonify(description=BACKEND_NOT_READY_ERROR), 503
            else:
                return jsonify(description="Token database doesn't exist"), 404

    except Exception as e:
        print("tokenInfo:", e)
        return jsonify(description=INTERNAL_ERROR), 500





@app.route('/api/v2/tokenTransactions/<token>', methods=['GET'])
async def tokenTransactions(token):
    try:
        if token is None:
            return jsonify(description='Token name has not been passed'), 400

        # Input validations
        senderFloAddress = request.args.get('senderFloAddress')
        if senderFloAddress and not check_flo_address(senderFloAddress, is_testnet):
            return jsonify(description='senderFloAddress validation failed'), 400

        destFloAddress = request.args.get('destFloAddress')
        if destFloAddress and not check_flo_address(destFloAddress, is_testnet):
            return jsonify(description='destFloAddress validation failed'), 400

        limit = request.args.get('limit')
        if limit is not None and not check_integer(limit):
            return jsonify(description='limit validation failed'), 400

        use_AND = request.args.get('use_AND')
        if use_AND is not None and use_AND not in ['True', 'False']:
            return jsonify(description='use_AND validation failed'), 400

        _from = int(request.args.get('_from', 1))
        to = int(request.args.get('to', 100))

        if _from < 1 or to < 1:
            return jsonify(description='_from or to validation failed'), 400

        token_db_name = standardize_db_name(token)

        async with get_mysql_conn_ctx(token_db_name) as conn:
            async with conn.cursor(DictCursor) as cursor:
                query = "SELECT jsonData, parsedFloData FROM transactionHistory WHERE 1=1"
                params = []

                # Handle dynamic conditions
                if use_AND == 'True' and senderFloAddress and destFloAddress:
                    query += " AND sourceFloAddress = %s AND destFloAddress = %s"
                    params.extend([senderFloAddress, destFloAddress])
                else:
                    if senderFloAddress:
                        query += " AND sourceFloAddress = %s"
                        params.append(senderFloAddress)
                    if destFloAddress:
                        query += " AND destFloAddress = %s"
                        params.append(destFloAddress)

                query += " ORDER BY id DESC LIMIT %s OFFSET %s"
                params.extend([to, (_from - 1) * to])

                await cursor.execute(query, tuple(params))
                transactionJsonData = await cursor.fetchall()

        sortedFormattedTransactions = []
        for row in transactionJsonData:
            transactions_object = {
                'transactionDetails': json.loads(row['jsonData']),
                'parsedFloData': json.loads(row['parsedFloData'])
            }
            sortedFormattedTransactions.append(transactions_object)

        response = {
            'token': token,
            'transactions': sortedFormattedTransactions
        }

        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except aiomysql.MySQLError as e:
        print(f"Database error in tokenTransactions: {e}")
        return jsonify(description="Database error occurred"), 500
    except Exception as e:
        print("tokenTransactions:", e)
        return jsonify(description=INTERNAL_ERROR), 500

    

@app.route('/api/v2/tokenBalances/<token>', methods=['GET'])
async def tokenBalances(token):
    try:
        # Validate the token parameter
        if not token:
            return jsonify(description="Token name has not been passed"), 400

        # Standardize the token database name
        token_db_name = standardize_db_name(token)

        try:
            # Establish async connection to the token database
            async with get_mysql_conn_ctx(token_db_name) as conn:
                async with conn.cursor() as cursor:
                    # Fetch balances grouped by address
                    query = "SELECT address, SUM(transferBalance) FROM activeTable GROUP BY address"
                    await cursor.execute(query)
                    addressBalances = await cursor.fetchall()

                    # Create the return dictionary
                    returnList = {address: balance for address, balance in addressBalances}

        except aiomysql.MySQLError as e:
            print(f"Database error while fetching balances for token {token}: {e}")
            if not is_backend_ready():
                return jsonify(description=BACKEND_NOT_READY_ERROR), 503
            else:
                return jsonify(description="Token database doesn't exist"), 404

        # Prepare and return the response
        if not is_backend_ready():
            return jsonify(
                warning=BACKEND_NOT_READY_WARNING,
                token=token,
                balances=returnList
            ), 206
        else:
            return jsonify(
                token=token,
                balances=returnList
            ), 200

    except Exception as e:
        print("tokenBalances:", e)
        return jsonify(description=INTERNAL_ERROR), 500




@app.route('/api/v2/floAddressInfo/<floAddress>', methods=['GET'])
async def floAddressInfo(floAddress):
    try:
        if not floAddress:
            return jsonify(description="floAddress hasn't been passed"), 400

        if not check_flo_address(floAddress, is_testnet):
            return jsonify(description="floAddress validation failed"), 400

        detail_list = {}
        incorporated_smart_contracts = []

        # Use DictCursor for readable access
        async with get_mysql_conn_ctx(standardize_db_name("system")) as conn:
            async with conn.cursor(DictCursor) as cursor:
                # Fetch associated tokens
                await cursor.execute("SELECT DISTINCT token FROM tokenAddressMapping WHERE tokenAddress = %s", (floAddress,))
                token_names = [row["token"] for row in await cursor.fetchall()]

                # Fetch smart contracts incorporated by the address
                await cursor.execute("""
                    SELECT contractName, status, tokenIdentification, contractType, transactionHash, blockNumber, blockHash
                    FROM activecontracts
                    WHERE contractAddress = %s
                """, (floAddress,))
                incorporated_contracts = await cursor.fetchall()

        # Fetch balances in parallel
        if token_names:
            tasks = [fetch_token_balance(token, floAddress) for token in token_names]
            balances = await asyncio.gather(*tasks, return_exceptions=True)

            for token, balance_result in zip(token_names, balances):
                if isinstance(balance_result, Exception):
                    print(f"Error fetching balance for token {token}: {balance_result}")
                else:
                    detail_list.update(balance_result)

        # Format smart contract info
        for contract in incorporated_contracts:
            incorporated_smart_contracts.append({
                'contractName': contract['contractName'],
                'contractAddress': floAddress,
                'status': contract['status'],
                'tokenIdentification': contract['tokenIdentification'],
                'contractType': contract['contractType'],
                'transactionHash': contract['transactionHash'],
                'blockNumber': contract['blockNumber'],
                'blockHash': contract['blockHash'],
            })

        # Build response
        response = {
            'floAddress': floAddress,
            'floAddressBalances': detail_list or None,
            'incorporatedSmartContracts': incorporated_smart_contracts or None
        }

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except Exception as e:
        print(f"floAddressInfo: {e}")
        return jsonify(description="Unexpected error occurred"), 500


@app.route('/api/v2/floAddressBalance/<floAddress>', methods=['GET'])
async def floAddressBalance(floAddress):
    try:
        if not floAddress:
            return jsonify(description="floAddress hasn't been passed"), 400

        if not check_flo_address(floAddress, is_testnet):
            return jsonify(description="floAddress validation failed"), 400

        token = request.args.get('token')

        # Case 1: Fetch balances for all tokens associated with this address
        if not token:
            async with get_mysql_conn_ctx(standardize_db_name("system")) as system_conn:
                async with system_conn.cursor(DictCursor) as cursor:
                    await cursor.execute(
                        "SELECT DISTINCT token FROM tokenAddressMapping WHERE tokenAddress = %s",
                        (floAddress,)
                    )
                    token_names = [row["token"] for row in await cursor.fetchall()]

            if not token_names:
                return jsonify(description="No tokens associated with the FLO address"), 404

            # Fetch balances concurrently
            tasks = [fetch_token_balance(token_name, floAddress) for token_name in token_names]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            detail_list = {}
            for token_name, result in zip(token_names, results):
                if isinstance(result, Exception):
                    print(f"Error fetching balance for token {token_name}: {result}")
                else:
                    detail_list.update(result)

            response = {
                'floAddress': floAddress,
                'floAddressBalances': detail_list
            }
            if not is_backend_ready():
                response['warning'] = BACKEND_NOT_READY_WARNING
                return jsonify(response), 206
            return jsonify(response), 200

        # Case 2: Fetch balance for a specific token
        else:
            result = await fetch_token_balance(token, floAddress)
            balance = result.get(token, {}).get('balance', 0)

            response = {
                'floAddress': floAddress,
                'token': token,
                'balance': balance
            }
            if not is_backend_ready():
                response['warning'] = BACKEND_NOT_READY_WARNING
                return jsonify(response), 206
            return jsonify(response), 200

    except aiomysql.MySQLError as e:
        print(f"Database error in floAddressBalance: {e}")
        return jsonify(description="Database error occurred"), 500

    except Exception as e:
        print(f"floAddressBalance: {e}")
        return jsonify(description="Unexpected error occurred"), 500





@app.route('/api/v2/floAddressTransactions/<floAddress>', methods=['GET'])
async def floAddressTransactions(floAddress):
    try:
        # Validate input
        if floAddress is None:
            return jsonify(description='floAddress has not been passed'), 400
        if not check_flo_address(floAddress, is_testnet):
            return jsonify(description='floAddress validation failed'), 400

        # Validate limit
        limit = request.args.get('limit')
        if limit is not None and not check_integer(limit):
            return jsonify(description='limit validation failed'), 400

        # Get optional token filter
        token = request.args.get('token')
        all_transaction_list = []

        if token is None:
            try:
                # Fetch associated tokens for the FLO address
                async with get_mysql_conn_ctx(standardize_db_name("system")) as system_conn:
                    async with system_conn.cursor(DictCursor) as cursor:
                        query = "SELECT DISTINCT token FROM tokenAddressMapping WHERE tokenAddress = %s"
                        await cursor.execute(query, (floAddress,))
                        token_names = [row['token'] for row in await cursor.fetchall()]

                # If no tokens found, return 404
                if not token_names:
                    return jsonify(description="No tokens associated with the FLO address"), 404

                # Fetch transactions for all associated tokens concurrently
                tasks = [
                    fetch_token_transactions(token_name, floAddress, floAddress, limit)
                    for token_name in token_names
                ]
                transaction_data = await asyncio.gather(*tasks)

                # Aggregate transactions from all tokens
                for data in transaction_data:
                    if isinstance(data, list):  # Ensure data is a list
                        all_transaction_list.extend(data)

            except aiomysql.MySQLError as e:
                print(f"Database error while fetching tokens: {e}")
                return jsonify(description="Database error occurred"), 500

        else:
            try:
                # Fetch transactions for the specified token
                all_transaction_list = await fetch_token_transactions(
                    token, floAddress, floAddress, limit
                )

            except aiomysql.MySQLError as e:
                print(f"Error accessing token database {token}: {e}")
                return jsonify(description="Token database error occurred"), 500

        # Sort and format transactions
        sorted_formatted_transactions = sort_transactions(all_transaction_list)

        # Prepare response
        response = {
            "floAddress": floAddress,
            "transactions": sorted_formatted_transactions
        }
        if token:
            response["token"] = token
        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        return jsonify(response), 200

    except Exception as e:
        print("floAddressTransactions:", e)
        return jsonify(description="Unexpected error occurred"), 500






    
# SMART CONTRACT APIs
@app.route('/api/v2/smartContractList', methods=['GET'])
async def getContractList_v2():
    try:
        # Retrieve and validate query parameters
        contractName = request.args.get('contractName')
        if contractName:
            contractName = contractName.strip().lower()

        contractAddress = request.args.get('contractAddress')
        if contractAddress:
            contractAddress = contractAddress.strip()
            if not check_flo_address(contractAddress, is_testnet):
                return jsonify(description="contractAddress validation failed"), 400

        # Standardize the system database name
        system_db_name = standardize_db_name("system")

        # Initialize contract list
        smart_contracts_morphed = []

        # Fetch contracts from the database asynchronously
        async with get_mysql_conn_ctx(system_db_name) as conn:
            async with conn.cursor() as cursor:
                # Build query dynamically
                query = "SELECT * FROM activecontracts"
                conditions = []
                params = []

                if contractName:
                    conditions.append("contractName=%s")
                    params.append(contractName)

                if contractAddress:
                    conditions.append("contractAddress=%s")
                    params.append(contractAddress)

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)

                # Execute query and fetch results
                await cursor.execute(query, tuple(params))
                smart_contracts = await cursor.fetchall()

                # Morph the smart contract data
                smart_contracts_morphed = await smartcontract_morph_helper(smart_contracts)

        # Fetch the committee address list asynchronously
        committeeAddressList = await refresh_committee_list(APP_ADMIN, apiUrl, int(time.time()))

        # Prepare the response
        response = {
            "smartContracts": smart_contracts_morphed,
            "smartContractCommittee": committeeAddressList,
        }

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206

        return jsonify(response), 200

    except Exception as e:
        print("getContractList_v2:", e)
        return jsonify(description="Unexpected error occurred"), 500


    
@app.route('/api/v2/getSmartContractInfo', methods=['GET'], endpoint='getSmartContractInfoV2')
async def getContractInfo():
    try:
        # Validate query parameters
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not contractName:
            return jsonify(result='error', description="Smart Contract's name hasn't been passed"), 400
        if not contractAddress:
            return jsonify(result='error', description="Smart Contract's address hasn't been passed"), 400

        # Standardize the database names
        contract_db_name = standardize_db_name(f"{contractName}_{contractAddress}")
        system_db_name = standardize_db_name("system")

        # Initialize the response structure
        contract_info = {}

        # Fetch contract structure and participants/token details
        async with get_mysql_conn_ctx(contract_db_name) as conn_contract:
            async with conn_contract.cursor() as cursor:
                # Fetch contract structure
                await cursor.execute("SELECT attribute, value FROM contractstructure")
                results = await cursor.fetchall()

                exit_conditions = {}
                for idx, (attribute, value) in enumerate(results):
                    if attribute == 'exitconditions':
                        exit_conditions[idx] = value
                    else:
                        contract_info[attribute] = value

                if exit_conditions:
                    contract_info['userChoice'] = exit_conditions

                # Fetch participant details
                await cursor.execute("SELECT COUNT(participantAddress) FROM contractparticipants")
                contract_info['numberOfParticipants'] = (await cursor.fetchone())[0]

                await cursor.execute("SELECT SUM(tokenAmount) FROM contractparticipants")
                contract_info['tokenAmountDeposited'] = (await cursor.fetchone())[0]

        # Fetch system-level contract details
        async with get_mysql_conn_ctx(system_db_name) as conn_system:
            async with conn_system.cursor() as cursor:
                query = """
                    SELECT status, incorporationDate, expiryDate, closeDate
                    FROM activecontracts
                    WHERE contractName = %s AND contractAddress = %s
                """
                await cursor.execute(query, (contractName, contractAddress))
                system_results = await cursor.fetchone()

                if system_results:
                    contract_info.update({
                        'status': system_results[0],
                        'incorporationDate': system_results[1],
                        'expiryDate': system_results[2],
                        'closeDate': system_results[3]
                    })

        # Handle additional logic for closed contracts
        if contract_info.get('status') == 'closed' and contract_info.get('contractType') == 'one-time-event':
            async with get_mysql_conn_ctx(contract_db_name) as conn_contract:
                async with conn_contract.cursor() as cursor:
                    await cursor.execute("""
                        SELECT transactionType, transactionSubType
                        FROM contractTransactionHistory
                        WHERE transactionType = 'trigger'
                    """)
                    triggers = await cursor.fetchall()

                    if len(triggers) == 1:
                        trigger_type = triggers[0][1]
                        contract_info['triggerType'] = trigger_type

                        # Fetch winning details if user choices exist
                        if 'userChoice' in contract_info:
                            if trigger_type is None:
                                await cursor.execute("""
                                    SELECT userChoice 
                                    FROM contractparticipants
                                    WHERE winningAmount IS NOT NULL
                                    LIMIT 1
                                """)
                                contract_info['winningChoice'] = (await cursor.fetchone())[0]

                                await cursor.execute("""
                                    SELECT participantAddress, winningAmount
                                    FROM contractparticipants
                                    WHERE winningAmount IS NOT NULL
                                """)
                                winners = await cursor.fetchall()
                                contract_info['numberOfWinners'] = len(winners)
                    else:
                        return jsonify(result='error', description="Data integrity issue: multiple triggers found"), 500

        # Final response
        response = {
            'result': 'ok',
            'contractName': contractName,
            'contractAddress': contractAddress,
            'contractInfo': contract_info
        }

        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206

        return jsonify(response), 200

    except Exception as e:
        print("getContractInfo:", e)
        return jsonify(result='error', description="Unexpected error occurred"), 500
    



@app.route('/api/v2/smartContractParticipants', methods=['GET'])
async def getcontractparticipants_v2():
    try:
        # Validate query parameters
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not contractName:
            return jsonify(description="Smart Contract's name hasn't been passed"), 400
        if not contractAddress:
            return jsonify(description="Smart Contract's address hasn't been passed"), 400
        if not check_flo_address(contractAddress, is_testnet):
            return jsonify(description="contractAddress validation failed"), 400

        # Standardize database name
        contractName = contractName.strip().lower()
        contractAddress = contractAddress.strip()
        db_name = standardize_db_name(f"{contractName}_{contractAddress}")

        # Fetch contract structure and status
        contractStructure = await fetchContractStructure(contractName, contractAddress)
        contractStatus = await fetchContractStatus(contractName, contractAddress)

        participantInfo = []

        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor(DictCursor) as cursor:
                # External-trigger contracts
                if 'exitconditions' in contractStructure:
                    token = contractStructure.get('tokenIdentification', None)
                    query = '''
                        SELECT id, participantAddress, tokenAmount, userChoice, transactionHash
                        FROM contractparticipants
                    '''
                    await cursor.execute(query)
                    participants = await cursor.fetchall()

                    if contractStatus == 'closed':
                        for row in participants:
                            await cursor.execute(
                                'SELECT winningAmount FROM contractwinners WHERE referenceTxHash = %s',
                                (row['transactionHash'],)
                            )
                            result = await cursor.fetchone()
                            winningAmount = result['winningAmount'] if result else 0
                            participantInfo.append({
                                'participantFloAddress': row['participantAddress'],
                                'tokenAmount': row['tokenAmount'],
                                'userChoice': row['userChoice'],
                                'transactionHash': row['transactionHash'],
                                'winningAmount': winningAmount,
                                'tokenIdentification': token
                            })
                    else:
                        for row in participants:
                            participantInfo.append({
                                'participantFloAddress': row['participantAddress'],
                                'tokenAmount': row['tokenAmount'],
                                'userChoice': row['userChoice'],
                                'transactionHash': row['transactionHash']
                            })

                    contractSubtype = 'external-trigger'

                # Time-trigger contracts
                elif 'payeeAddress' in contractStructure:
                    query = '''
                        SELECT id, participantAddress, tokenAmount, transactionHash
                        FROM contractparticipants
                    '''
                    await cursor.execute(query)
                    participants = await cursor.fetchall()

                    for row in participants:
                        participantInfo.append({
                            'participantFloAddress': row['participantAddress'],
                            'tokenAmount': row['tokenAmount'],
                            'transactionHash': row['transactionHash']
                        })

                    contractSubtype = 'time-trigger'

                # Token-swap contracts
                elif contractStructure['contractType'] == 'continuos-event' and contractStructure['subtype'] == 'tokenswap':
                    query = '''
                        SELECT id, participantAddress, participationAmount, swapPrice, transactionHash, blockNumber, blockHash, swapAmount
                        FROM contractparticipants
                    '''
                    await cursor.execute(query)
                    participants = await cursor.fetchall()

                    for row in participants:
                        participantInfo.append({
                            'participantFloAddress': row['participantAddress'],
                            'participationAmount': row['participationAmount'],
                            'swapPrice': float(row['swapPrice']),
                            'transactionHash': row['transactionHash'],
                            'blockNumber': row['blockNumber'],
                            'blockHash': row['blockHash'],
                            'swapAmount': row['swapAmount']
                        })

                    contractSubtype = 'tokenswap'

                else:
                    return jsonify(description="Unsupported contract type"), 400

        response = {
            'contractName': contractName,
            'contractAddress': contractAddress,
            'contractType': contractStructure['contractType'],
            'contractSubtype': contractSubtype,
            'participantInfo': participantInfo
        }

        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except Exception as e:
        print("getcontractparticipants_v2:", e)
        return jsonify(description="Unexpected error occurred"), 500



    
@app.route('/api/v2/getParticipantDetails', methods=['GET'], endpoint='getParticipantDetailsV2')
async def getParticipantDetails():
    try:
        floAddress = request.args.get('floAddress')
        contractName = request.args.get('contractName')
        contractAddress = request.args.get('contractAddress')

        if not floAddress:
            return jsonify(result='error', description='FLO address hasn\'t been passed'), 400

        if (contractName and not contractAddress) or (contractAddress and not contractName):
            return jsonify(result='error', description='Pass both, contractName and contractAddress as URL parameters'), 400

        floAddress = floAddress.strip()
        if contractName:
            contractName = contractName.strip().lower()
        if contractAddress:
            contractAddress = contractAddress.strip()

        system_db_name = standardize_db_name("system")
        contract_db_name = standardize_db_name(f"{contractName}_{contractAddress}") if contractName and contractAddress else None

        participationDetailsList = []

        async with get_mysql_conn_ctx(system_db_name) as conn_system:
            async with conn_system.cursor(DictCursor) as cursor_system:
                if contractName and contractAddress:
                    query = '''
                        SELECT * 
                        FROM contractAddressMapping 
                        WHERE address = %s AND addressType = "participant" AND contractName = %s AND contractAddress = %s
                    '''
                    params = (floAddress, contractName, contractAddress)
                else:
                    query = '''
                        SELECT * 
                        FROM contractAddressMapping 
                        WHERE address = %s AND addressType = "participant"
                    '''
                    params = (floAddress,)

                await cursor_system.execute(query, params)
                participant_address_contracts = await cursor_system.fetchall()

                if not participant_address_contracts:
                    if not is_backend_ready():
                        return jsonify(result='error', description=BACKEND_NOT_READY_ERROR), 503
                    else:
                        return jsonify(result='error', description='Address hasn\'t participated in any contract'), 404

                for contract in participant_address_contracts:
                    detailsDict = {}
                    contract_name = contract['contractName']
                    contract_address = contract['contractAddress']
                    contract_db_name = standardize_db_name(f"{contract_name}_{contract_address}")

                    contractStructure = await fetchContractStructure(contract_name, contract_address)

                    async with get_mysql_conn_ctx(contract_db_name) as conn_contract:
                        async with conn_contract.cursor(DictCursor) as cursor_contract:
                            if contractStructure['contractType'] == 'tokenswap':
                                query = '''
                                    SELECT participantAddress, participationAmount, receivedAmount, transactionHash, blockNumber, blockHash 
                                    FROM contractparticipants 
                                    WHERE participantAddress = %s
                                '''
                                await cursor_contract.execute(query, (floAddress,))
                                participation_details = await cursor_contract.fetchall()

                                participationList = []
                                for row in participation_details:
                                    participationList.append({
                                        'participationAddress': row['participantAddress'],
                                        'participationAmount': row['participationAmount'],
                                        'receivedAmount': row['receivedAmount'],
                                        'transactionHash': row['transactionHash'],
                                        'blockNumber': row['blockNumber'],
                                        'blockHash': row['blockHash']
                                    })

                                detailsDict['contractName'] = contract_name
                                detailsDict['contractAddress'] = contract_address
                                detailsDict['participationDetails'] = participationList

                            elif contractStructure['contractType'] == 'one-time-event' and 'payeeAddress' in contractStructure:
                                query = '''
                                    SELECT tokenAmount, transactionHash 
                                    FROM contractparticipants 
                                    WHERE participantAddress = %s
                                '''
                                await cursor_contract.execute(query, (floAddress,))
                                result = await cursor_contract.fetchone()

                                if result:
                                    detailsDict['contractName'] = contract_name
                                    detailsDict['contractAddress'] = contract_address
                                    detailsDict['tokenAmount'] = result['tokenAmount']
                                    detailsDict['transactionHash'] = result['transactionHash']

                        participationDetailsList.append(detailsDict)

        response = {
            'result': 'ok',
            'floAddress': floAddress,
            'type': 'participant',
            'participatedContracts': participationDetailsList
        }

        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except aiomysql.MySQLError as db_error:
        print(f"Database error: {db_error}")
        return jsonify(result='error', description="Database error occurred"), 500
    except Exception as e:
        print(f"getParticipantDetails: {e}")
        return jsonify(result='error', description="Unexpected error occurred"), 500




    

@app.route('/api/v2/smartContractTransactions', methods=['GET'])
async def smartcontracttransactions():
    try:
        # Get and validate query parameters
        contractName = request.args.get('contractName')
        if not contractName:
            return jsonify(description="Smart Contract's name hasn't been passed"), 400
        contractName = contractName.strip().lower()

        contractAddress = request.args.get('contractAddress')
        if not contractAddress:
            return jsonify(description="Smart Contract's address hasn't been passed"), 400
        contractAddress = contractAddress.strip()
        if not check_flo_address(contractAddress, is_testnet):
            return jsonify(description="contractAddress validation failed"), 400

        _from = int(request.args.get('_from', 1))
        to = int(request.args.get('to', 100))

        if _from < 1:
            return jsonify(description="_from validation failed"), 400
        if to < 1:
            return jsonify(description="to validation failed"), 400

        # Standardize the smart contract database name
        contractDbName = standardize_db_name(f"{contractName}_{contractAddress}")

        # Check if the smart contract database exists
        async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn:
            async with conn.cursor() as cursor:
                query = "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s"
                await cursor.execute(query, (contractDbName,))
                db_exists = await cursor.fetchone()

        if not db_exists:
            if not is_backend_ready():
                return jsonify(description=BACKEND_NOT_READY_ERROR), 503
            else:
                return jsonify(description="Smart Contract with the given name doesn't exist"), 404

        # Fetch transaction data
        transaction_result = await fetch_contract_transactions(contractName, contractAddress, _from, to)

        # Handle if the result is already a Response (jsonify + status)
        if isinstance(transaction_result, tuple) and isinstance(transaction_result[0], Response):
            return transaction_result

        if isinstance(transaction_result, Response):
            return transaction_result

        # Fallback check: if result is not a list
        if not isinstance(transaction_result, list):
            print(f"Unexpected result type from fetch_contract_transactions: {type(transaction_result)}")
            return jsonify(description="Unexpected error occurred while fetching transactions"), 500

        transactionJsonData = sort_transactions(transaction_result)

        # Build final response
        response_data = {
            "contractName": contractName,
            "contractAddress": contractAddress,
            "contractTransactions": transactionJsonData
        }

        if not is_backend_ready():
            response_data["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response_data), 206
        else:
            return jsonify(response_data), 200

    except ValueError as ve:
        print(f"Value error in smartcontracttransactions: {ve}")
        return jsonify(description="Invalid input for _from or to"), 400

    except Exception as e:
        print(f"smartcontracttransactions: {e}")
        return jsonify(description=INTERNAL_ERROR), 500




    

# todo - add options to only ask for active/consumed/returned deposits
@app.route('/api/v2/smartContractDeposits', methods=['GET'])
async def smartcontractdeposits():
    try:
        # Validate input parameters
        contractName = request.args.get('contractName')
        if not contractName:
            return jsonify(description="Smart Contract's name hasn't been passed"), 400
        contractName = contractName.strip().lower()

        contractAddress = request.args.get('contractAddress')
        if not contractAddress:
            return jsonify(description="Smart Contract's address hasn't been passed"), 400
        contractAddress = contractAddress.strip()
        if not check_flo_address(contractAddress, is_testnet):
            return jsonify(description="contractAddress validation failed"), 400

        # Standardize the database name
        db_name = standardize_db_name(f"{contractName}_{contractAddress}")

        # Connect to the smart contract database asynchronously
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor(DictCursor) as cursor:
                # Fetch distinct deposits with latest balances and original deposit details in one query
                distinct_deposits_query = """
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
                    ) AS latest_deposits
                    ON cd.transactionHash = latest_deposits.transactionHash AND cd.id = latest_deposits.max_id
                    GROUP BY cd.transactionHash, depositorAddress, cd.status, currentBalance
                    ORDER BY cd.id DESC;
                """
                await cursor.execute(distinct_deposits_query)
                distinct_deposits = await cursor.fetchall()

                # Transform deposit information
                deposit_info = [
                    {
                        'depositorAddress': deposit['depositorAddress'],
                        'transactionHash': deposit['transactionHash'],
                        'status': deposit['status'],
                        'originalBalance': deposit['originalBalance'],
                        'currentBalance': deposit['currentBalance'],
                        'time': deposit['expiryTime'],
                    }
                    for deposit in distinct_deposits
                ]

                # Fetch the current total deposit balance
                total_deposit_balance_query = """
                    SELECT SUM(depositBalance) AS totalDepositBalance 
                    FROM (
                        SELECT transactionHash, MAX(id) AS max_id 
                        FROM contractdeposits 
                        GROUP BY transactionHash
                    ) AS latest_deposits
                    INNER JOIN contractdeposits cd
                    ON cd.transactionHash = latest_deposits.transactionHash AND cd.id = latest_deposits.max_id;
                """
                await cursor.execute(total_deposit_balance_query)
                current_deposit_balance = await cursor.fetchone()

        # Prepare the response
        response = {
            'currentDepositBalance': current_deposit_balance['totalDepositBalance'] if current_deposit_balance else 0,
            'depositInfo': deposit_info,
        }

        # Return response based on backend readiness
        if not is_backend_ready():
            response['warning'] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except aiomysql.MySQLError as db_error:
        print(f"Database error in smartcontractdeposits: {db_error}")
        return jsonify(description="Database error occurred"), 500
    except Exception as e:
        print(f"smartcontractdeposits: {e}")
        return jsonify(description="An internal error occurred"), 500




    
@app.route('/api/v2/blockDetails/<blockHash>', methods=['GET'])
async def blockdetails(blockHash):
    try:
        # todo - validate blockHash
        blockJson = await blockdetailhelper(blockHash)
        if len(blockJson) != 0:
            blockJson = json.loads(blockJson[0][0])
            return jsonify(blockDetails=blockJson), 200
        else:
            if not is_backend_ready():
                return jsonify(description=BACKEND_NOT_READY_ERROR), 503
            else:
                return jsonify(description='Block doesn\'t exist in database'), 404    
    except Exception as e:
        print("blockdetails:", e)
        return jsonify(description=INTERNAL_ERROR), 500



@app.route('/api/v2/transactionDetails/<transactionHash>', methods=['GET'])
async def transactiondetails1(transactionHash):
    try:
        # Fetch transaction details using the helper
        transactionJsonData = await transactiondetailhelper(transactionHash)

        if transactionJsonData:
            transactionJson = json.loads(transactionJsonData[0][0])
            transactionJson = await update_transaction_confirmations(transactionJson)

            try:
                parseResult = json.loads(transactionJsonData[0][1])
            except (IndexError, json.JSONDecodeError, TypeError) as e:
                print(f"[ERROR] Failed to parse parseResult: {e}")
                return jsonify(description="Invalid parseResult in transaction data"), 500

            operation = transactionJsonData[0][2]
            db_reference = transactionJsonData[0][3]

            sender_address, receiver_address = extract_ip_op_addresses(transactionJson)
            mergeTx = {**parseResult, **transactionJson}
            mergeTx['onChain'] = True
            operationDetails = {}

            # Standardize database name
            db_name = standardize_db_name(db_reference)

            async with get_mysql_conn_ctx(db_name) as conn:
                async with conn.cursor() as cursor:
                    if operation == 'smartContractDeposit':
                        await cursor.execute("""
                            SELECT depositAmount, blockNumber 
                            FROM contractdeposits 
                            WHERE status = 'deposit-return' AND transactionHash = %s
                        """, (transactionJson['txid'],))
                        returned_deposit_tx = await cursor.fetchall()
                        if returned_deposit_tx:
                            operationDetails['returned_depositAmount'] = returned_deposit_tx[0][0]
                            operationDetails['returned_blockNumber'] = returned_deposit_tx[0][1]

                        await cursor.execute("""
                            SELECT depositAmount, blockNumber 
                            FROM contractdeposits 
                            WHERE status = 'deposit-honor' AND transactionHash = %s
                        """, (transactionJson['txid'],))
                        deposit_honors = await cursor.fetchall()
                        operationDetails['depositHonors'] = {
                            'list': [{'honor_amount': honor[0], 'blockNumber': honor[1]} for honor in deposit_honors],
                            'count': len(deposit_honors)
                        }

                        await cursor.execute("""
                            SELECT depositBalance 
                            FROM contractdeposits 
                            WHERE id = (SELECT MAX(id) FROM contractdeposits WHERE transactionHash = %s)
                        """, (transactionJson['txid'],))
                        depositBalance = await cursor.fetchone()
                        operationDetails['depositBalance'] = depositBalance[0]
                        operationDetails['consumedAmount'] = parseResult['depositAmount'] - depositBalance[0]

                    elif operation == 'tokenswap-participation':
                        await cursor.execute("""
                            SELECT tokenAmount, winningAmount, userChoice 
                            FROM contractparticipants 
                            WHERE transactionHash = %s
                        """, (transactionJson['txid'],))
                        swap_amounts = await cursor.fetchone()

                        await cursor.execute("""
                            SELECT value FROM contractstructure 
                            WHERE attribute = 'selling_token'
                        """)
                        structure = await cursor.fetchone()

                        operationDetails = {
                            'participationAmount': swap_amounts[0],
                            'receivedAmount': swap_amounts[1],
                            'participationToken': parseResult['tokenIdentification'],
                            'receivedToken': structure[0],
                            'swapPrice_received_to_participation': float(swap_amounts[2])
                        }

                    elif operation == 'smartContractPays':
                        await cursor.execute("""
                            SELECT participantAddress, tokenAmount, userChoice, winningAmount 
                            FROM contractparticipants 
                            WHERE winningAmount IS NOT NULL
                        """)
                        winner_participants = await cursor.fetchall()
                        operationDetails = {
                            'total_winners': len(winner_participants),
                            'winning_choice': winner_participants[0][2] if winner_participants else None,
                            'winner_list': [
                                {
                                    'participantAddress': participant[0],
                                    'participationAmount': participant[1],
                                    'winningAmount': participant[3]
                                } for participant in winner_participants
                            ]
                        }

                    elif operation == 'ote-externaltrigger-participation':
                        await cursor.execute("""
                            SELECT winningAmount 
                            FROM contractparticipants 
                            WHERE transactionHash = %s
                        """, (transactionHash,))
                        winningAmount = await cursor.fetchone()
                        if winningAmount and winningAmount[0] is not None:
                            operationDetails['winningAmount'] = winningAmount[0]

                    elif operation == 'tokenswapParticipation':
                        contractName, contractAddress = db_reference.rsplit('_', 1)
                        txhash_txs = await fetch_swap_contract_transactions(contractName, contractAddress, transactionHash)
                        mergeTx['subTransactions'] = [
                            tx for tx in txhash_txs if not tx.get('onChain', True)
                        ]

            mergeTx['operation'] = operation
            mergeTx['operationDetails'] = operationDetails
            return jsonify(mergeTx), 200

        else:
            if not is_backend_ready():
                return jsonify(description=BACKEND_NOT_READY_ERROR), 503
            else:
                return jsonify(description="Transaction doesn't exist in database"), 404

    except Exception as e:
        print(f"transactiondetails1: {e}")
        return jsonify(description=INTERNAL_ERROR), 500



    
@app.route('/api/v2/latestTransactionDetails', methods=['GET'])
async def latestTransactionDetails():
    try:
        # Validate the 'limit' parameter
        limit = request.args.get('limit')
        if limit is not None and not check_integer(limit):
            return jsonify(description='limit validation failed'), 400

        # Default limit if none provided
        limit = int(limit) if limit else 4

        # Standardize the database name
        db_name = standardize_db_name("latestCache")

        # Connect to the database asynchronously
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                # Updated query with JOIN to replace IN and LIMIT
                query = """
                    SELECT lt.* 
                    FROM latestTransactions lt
                    JOIN (
                        SELECT DISTINCT blockNumber 
                        FROM latestTransactions 
                        ORDER BY blockNumber DESC 
                        LIMIT %s
                    ) AS recent_blocks
                    ON lt.blockNumber = recent_blocks.blockNumber
                    ORDER BY lt.id DESC;
                """
                # Execute the query
                await cursor.execute(query, (limit,))

                # Fetch and process transactions
                latestTransactions = await cursor.fetchall()
                tx_list = []
                for item in latestTransactions:
                    try:
                        item = list(item)
                        tx_parsed_details = {}

                        # Parse transaction details
                        tx_parsed_details['transactionDetails'] = json.loads(item[3])
                        tx_parsed_details['parsedFloData'] = json.loads(item[5])
                        tx_parsed_details['parsedFloData']['transactionType'] = item[4]
                        tx_parsed_details['transactionDetails']['blockheight'] = int(item[2])

                        # Merge parsed details
                        merged_tx = {
                            **tx_parsed_details['transactionDetails'], 
                            **tx_parsed_details['parsedFloData']
                        }
                        merged_tx['onChain'] = True

                        # Append to the transaction list
                        tx_list.append(merged_tx)
                    except json.JSONDecodeError as e:
                        print(f"Error decoding JSON for transaction ID {item[0]}: {e}")
                    except Exception as e:
                        print(f"Unexpected error processing transaction ID {item[0]}: {e}")

        # Return response based on backend readiness
        if not is_backend_ready():
            return jsonify(warning=BACKEND_NOT_READY_WARNING, latestTransactions=tx_list), 206
        else:
            return jsonify(latestTransactions=tx_list), 200

    except Exception as e:
        # Print the exception and return an error response
        import traceback
        traceback.print_exc()
        print("latestTransactionDetails Error:", e)
        return jsonify(description="An internal error occurred while processing the request"), 500




    


@app.route('/api/v2/latestBlockDetails', methods=['GET'])
async def latestBlockDetails():
    try:
        # Validate the 'limit' parameter
        limit = request.args.get('limit')
        if limit is not None and not check_integer(limit):
            return jsonify(description='limit validation failed'), 400

        # Default limit if none provided
        limit = int(limit) if limit else 4

        # Standardize the database name
        db_name = standardize_db_name("latestCache")

        # Connect to the database asynchronously
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                # Updated query with JOIN to avoid subquery issues
                query = """
                    SELECT lb.jsonData 
                    FROM latestBlocks lb
                    JOIN (
                        SELECT blockNumber 
                        FROM latestBlocks 
                        ORDER BY blockNumber DESC 
                        LIMIT %s
                    ) AS recent_blocks
                    ON lb.blockNumber = recent_blocks.blockNumber
                    ORDER BY lb.id DESC;
                """
                # Execute the query with the limit parameter
                await cursor.execute(query, (limit,))

                # Fetch and parse the blocks
                latestBlocks = await cursor.fetchall()
                templst = [json.loads(item[0]) for item in latestBlocks]

        # Return response based on backend readiness
        if not is_backend_ready():
            return jsonify(warning=BACKEND_NOT_READY_WARNING, latestBlocks=templst), 206
        else:
            return jsonify(latestBlocks=templst), 200

    except Exception as e:
        # Print the exception and return an error response
        import traceback
        traceback.print_exc()
        print("latestBlockDetails Error:", e)
        return jsonify(description="An internal error occurred while processing the request"), 500

    

@app.route('/api/v2/blockTransactions/<blockHash>', methods=['GET'])
async def blocktransactions(blockHash):
    try:
        blockJson = await blockdetailhelper(blockHash)
        if len(blockJson) != 0:
            blockJson = json.loads(blockJson[0][0])
            blocktxlist = blockJson['txs']
            blocktxs = []
            for i in range(len(blocktxlist)):
                temptx = await transactiondetailhelper(blocktxlist[i]['txid'])                        
                transactionJson = json.loads(temptx[0][0])
                parseResult = json.loads(temptx[0][1])
                blocktxs.append({**parseResult , **transactionJson})

                # TODO (CRITICAL): Write conditions to include and filter on chain and offchain transactions
                #blocktxs['onChain'] = True
            return jsonify(transactions=blocktxs, blockKeyword=blockHash), 200
        else:
            if not is_backend_ready():
                return jsonify(description=BACKEND_NOT_READY_ERROR), 503
            else:
                return jsonify(description='Block doesn\'t exist in database'), 404


    except Exception as e:
        print("blocktransactions:", e)
        return jsonify(description=INTERNAL_ERROR), 500
    

@app.route('/api/v2/categoriseString/<urlstring>', methods=['GET'])
async def categoriseString_v2(urlstring):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{apiUrl}/api/v1/tx/{urlstring}") as response:
                if response.status == 200:
                    return jsonify(type='transaction'), 200
            async with session.get(f"{apiUrl}/api/v1/block/{urlstring}") as response:
                if response.status == 200:
                    return jsonify(type='block'), 200

        db_name = standardize_db_name("system")
        async with get_mysql_conn_ctx(db_name) as conn:
            async with conn.cursor() as cursor:
                query = """
                    SELECT db_type 
                    FROM databaseTypeMapping 
                    WHERE LOWER(db_name) = %s
                    LIMIT 1;
                """
                await cursor.execute(query, (urlstring.lower(),))
                result = await cursor.fetchone()
                if result:
                    db_type = result[0]
                    if db_type in ("token", "infinite-token"):
                        return jsonify(type='token'), 200
                    elif db_type == "smartcontract":
                        return jsonify(type='smartContract'), 200

        return jsonify(type='noise'), 200

    except Exception as e:
        print("categoriseString_v2:", e)
        return jsonify(description="Internal Error"), 500





# Assuming `get_mysql_connection` has been updated to work with aiomysql and async

@app.route('/api/v2/tokenSmartContractList', methods=['GET'])
async def tokenSmartContractList():
    try:
        # Prefix for databases
        database_prefix = f"{mysql_config.database_prefix}_"
        database_suffix = "_db"

        # Initialize lists for tokens and contracts
        token_list = []

        # Step 1: Enumerate all databases asynchronously
        async with get_mysql_conn_ctx("information_schema", no_standardize=True) as conn_info:
            async with conn_info.cursor() as cursor:
                query = f"""
                    SELECT SCHEMA_NAME 
                    FROM SCHEMATA 
                    WHERE SCHEMA_NAME LIKE '{database_prefix}%' 
                      AND SCHEMA_NAME LIKE '%{database_suffix}'
                """
                await cursor.execute(query)
                all_databases = await cursor.fetchall()

                # Filter token databases from smart contract databases
                for db_name in all_databases:
                    stripped_name = db_name[0][len(database_prefix):-len(database_suffix)]

                    # Exclude "latestCache" and "system" databases
                    if stripped_name in ["latestCache", "system"]:
                        continue

                    parts = stripped_name.split('_')
                    if len(parts) == 1:  # Token database format (e.g., usd, inr)
                        token_list.append(stripped_name)

        # Step 2: Fetch smart contracts from the `system` database
        async with get_mysql_conn_ctx("system") as conn_system:
            async with conn_system.cursor() as cursor:
                # Validate and process query parameters
                contractName = request.args.get('contractName')
                contractAddress = request.args.get('contractAddress')

                if contractName:
                    contractName = contractName.strip().lower()
                if contractAddress:
                    contractAddress = contractAddress.strip()
                    if not check_flo_address(contractAddress, is_testnet):
                        return jsonify(description='contractAddress validation failed'), 400

                # Fetch smart contracts matching the filters
                query = """
                    SELECT * 
                    FROM activecontracts 
                    WHERE (%s IS NULL OR LOWER(contractName) = %s) 
                      AND (%s IS NULL OR contractAddress = %s)
                """
                await cursor.execute(query, (contractName, contractName, contractAddress, contractAddress))
                smart_contracts = await cursor.fetchall()

                # Morph the smart contract data for response formatting
                smart_contracts_morphed = await smartcontract_morph_helper(smart_contracts)

        # Step 3: Fetch committee address list
        committeeAddressList = await refresh_committee_list(APP_ADMIN, apiUrl, int(time.time()))

        # Step 4: Prepare and send the response
        response = {
            "tokens": token_list,
            "smartContracts": smart_contracts_morphed,
            "smartContractCommittee": committeeAddressList
        }

        if not is_backend_ready():
            response["warning"] = BACKEND_NOT_READY_WARNING
            return jsonify(response), 206
        else:
            return jsonify(response), 200

    except Exception as e:
        print("tokenSmartContractList:", e)
        return jsonify(description=INTERNAL_ERROR), 500





    
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




