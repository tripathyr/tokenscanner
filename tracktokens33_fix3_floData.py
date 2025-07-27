# === Standard Library Imports ===
import argparse
import asyncio
import configparser
import glob
import hashlib
import json
import logging
import os
import pdb
import random
import re
import shutil
import sys
import time
from ast import literal_eval
from datetime import datetime
from decimal import Decimal
import traceback

# === Third-Party Library Imports ===
import arrow
import pymysql
import requests
import websockets
from requests.packages.urllib3.exceptions import InsecureRequestWarning
import sqlalchemy
from sqlalchemy import (
    BigInteger, Column, MetaData, create_engine, func, and_
)
from sqlalchemy.exc import (
    IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text
from urllib.parse import urljoin

# === Project-Specific Imports ===
import parsing
from parsing import perform_decimal_operation
from statef_processing import process_stateF
from util_rollback import rollback_to_block
from models_floData import (
    ActiveContracts, ActiveTable, ConsumedInfo, ConsumedTable, ContractAddressMapping,
    ContractBase, ContractDeposits, ContractParticipants,
    ContractStructure, ContractTransactionHistory,
    DatabaseTypeMapping, LatestBlocks, LatestCacheBase, LatestTransactions,
    RejectedContractTransactionHistory, RejectedTransactionHistory,
    SystemBase, SystemData, TimeActions, TokenAddressMapping, TokenBase,
    TokenContractAssociation, TransferLogs, TransactionHistory, RatePairs
)



# Disable the InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

MAX_JSON_LENGTH = 60000

#ENHANCEMENTS START


def update_dynamic_swap_price(session, price, price_timestamp, blockheight):
    """
    Updates the dynamic swap price in the ContractStructure table.

    Returns:
        True if an update was made, False if skipped.
    """
    new_metadata = {
        "price": float(price),
        "published_time": int(price_timestamp),
        "blockNumber": int(blockheight)
    }

    updated = False

    try:
        record = session.query(ContractStructure).filter_by(attribute="dynamicPriceUpdate").first()

        if record:
            try:
                current_metadata = json.loads(record.value)
                if int(current_metadata.get("blockNumber", -1)) == blockheight:
                    logger.info(
                        f"Dynamic price already recorded for blockheight {blockheight}, skipping update."
                    )
                    return False
            except (ValueError, json.JSONDecodeError):
                logger.warning("Malformed dynamicPriceUpdate JSON. Proceeding to overwrite.")

            record.value = json.dumps(new_metadata)
            record.blockNumber = blockheight
            updated = True
        else:
            session.add(ContractStructure(
                attribute="dynamicPriceUpdate",
                value=json.dumps(new_metadata),
                index=0,
                blockNumber=blockheight
            ))
            updated = True

        session.commit()
    except Exception as e:
        logger.error(f"Error updating dynamic swap price: {e}", exc_info=True)
        session.rollback()
        return False
    finally:
        session.close()

    return updated

def get_dynamic_swap_price(contract_name, contract_address):
    """
    Fetches the dynamic swap price JSON from ContractStructure.

    Returns:
        dict: Parsed JSON object if found
        None: If not found or on parse error
    """
    while True:
        session = None
        try:
            session = create_database_session_orm(
                type='smart_contract',
                parameters={'contract_name': contract_name, 'contract_address': contract_address},
                base=ContractBase
            )

            record = session.query(ContractStructure).filter_by(attribute="dynamicPriceUpdate").first()

            if record:
                try:
                    result = json.loads(record.value)
                    return result
                except json.JSONDecodeError:
                    logger.warning(f"[{contract_name}] Malformed dynamicPriceUpdate JSON.")
                    return None
            else:
                # No record found is NOT a DB error → exit instead of retrying infinitely
                return None

        except Exception as e:
            logger.warning(
                f"[{contract_name}] Failed to fetch dynamic price. Retrying in {DB_RETRY_TIMEOUT} seconds. Reason: {e}"
            )
            time.sleep(DB_RETRY_TIMEOUT)
        finally:
            if session:
                session.close()

def normalize_transaction_data(transaction_data):
    """
    Normalize transaction data to ensure compatibility with both old and new formats of Flosight and Blockbook.
    :param transaction_data: Dictionary containing transaction data.
    """
    for vin in transaction_data.get("vin", []):
        if "addr" in vin:
            vin["addresses"] = [vin.pop("addr")]

def normalize_block_transactions(blockinfo):
    """
    Normalize all transactions in a block to handle both old and new formats of flosight and blockbook.
    :param blockinfo: Block data containing transactions.
    """
    for transaction_data in blockinfo.get("txs", []):
        normalize_transaction_data(transaction_data)



def add_block_hashrecord(block_number, block_hash):
    while True:
        try:
            conn = create_database_connection('latest_cache', {'db_name': "latestCache"})
            try:
                conn.execute("START TRANSACTION;")
                existing_block = conn.execute('SELECT * FROM RecentBlocks WHERE blockNumber = %s', (block_number,)).fetchone()
                if existing_block:
                    logger.info(f"Block {block_number} already exists. No action taken."); conn.execute("COMMIT;"); return
                conn.execute('INSERT INTO RecentBlocks (blockNumber, blockHash) VALUES (%s, %s)', (block_number, block_hash))
                block_count = conn.execute('SELECT COUNT(*) FROM RecentBlocks').fetchone()[0]
                if block_count > 1000:
                    excess_count = block_count - 1000
                    oldest_blocks = conn.execute('SELECT id FROM RecentBlocks ORDER BY id ASC LIMIT %s', (excess_count,)).fetchall()
                    for block in oldest_blocks:
                        conn.execute('DELETE FROM RecentBlocks WHERE id = %s', (block[0],))
                conn.execute("COMMIT;")
            except Exception as e:
                conn.execute("ROLLBACK;"); logger.error(f"Database error: {e}"); raise
            finally:
                conn.close()
            break
        except Exception as e:
            logger.error(f"Error connecting to or working with the database: {e}")
            time.sleep(DB_RETRY_TIMEOUT)


def detect_reorg():
    API_VERIFY, ROLLBACK_BUFFER = True, 2
    try:
        conn = create_database_connection('system_dbs', {'db_name': 'system'})
        result, row = conn.execute("SELECT value FROM systemData WHERE attribute = %s", ('lastblockscanned',)), None
        row = result.fetchone(); conn.close()
        if not row: return None
        try: latest_block = int(row[0])
        except ValueError: return None
        block_number = latest_block
        while block_number > 0:
            conn = create_database_connection('latest_cache', {'db_name': 'latestCache'})
            result, local_row = conn.execute("SELECT blockHash FROM RecentBlocks WHERE blockNumber = %s", (block_number,)), None
            local_row = result.fetchone(); conn.close()
            if not local_row: return None
            local_hash = local_row[0]
            try:
                api_url, hash_key = (f"{neturl}/api/v2/block-index/{block_number}", "blockHash") if blockbook_type == "blockbook_legacy" else (f"{neturl}/api/blockheight/{block_number}", "hash") if blockbook_type == "address_indexer" else (None, None)
                if not api_url: return None
                response = requests.get(api_url, verify=API_VERIFY, timeout=RETRY_TIMEOUT_SHORT); response.raise_for_status()
                response_json, remote_hash = response.json(), response.json().get(hash_key)
                if not remote_hash: return None
                if local_hash != remote_hash:
                    logger.warning(f"Reorg detected at block {block_number}. Local hash: {local_hash}, Remote hash: {remote_hash}")
                    return block_number - ROLLBACK_BUFFER
            except requests.RequestException:
                time.sleep(10); continue
            except Exception:
                return None
            block_number -= 1
        return None
    except Exception:
        return None


#ENHANCEMENTS END




RETRY_TIMEOUT_LONG = 30 * 60 # 30 mins
RETRY_TIMEOUT_SHORT = 60 # 1 min
DB_RETRY_TIMEOUT = 60 # 60 seconds
MAX_RETRIES = 5


def process_committee_floData(floData):
    """
    Processes floData for contract-committee and returns
    a dict of actions: {"add": [...], "remove": [...]}
    """
    actions = {"add": [], "remove": []}
    try:
        contract_committee_actions = floData['token-tracker']['contract-committee']
    except KeyError:
        logger.info('No contract-committee entry found in floData.')
    else:
        for action in contract_committee_actions:
            if action in actions:
                for floid in contract_committee_actions[action]:
                    clean_addr = floid.strip()
                    actions[action].append(clean_addr)
    return actions


def refresh_committee_list(admin_flo_id, api_url, blocktime):
    """
    Retrieves the committee list by scanning transactions
    from the admin FLO ID up to the given block time.
    Applies adds/removes in transaction order.
    """
    address_actions = {}

    try:
        url_addr = urljoin(api_url, f'api/address/{admin_flo_id}')
        logger.info(f"Fetching committee list from: {url_addr}")

        response = requests.get(
            url_addr,
            verify=API_VERIFY,
            timeout=30
        )
        response.raise_for_status()
        response_json = response.json()
    except Exception as e:
        logger.error(f"Error fetching address data for {admin_flo_id}: {e}")
        sys.exit(0)


    transactions = response_json.get("txs", [])
    transactions.reverse()  # Process oldest first

    for tx in transactions:
        txid = tx.get("txid")
        blocktime_value = int(tx.get("time", float('inf')))

        # Only process txs up to the given blocktime
        if blocktime_value > blocktime:
            continue

        try:
            tx_floData_str = tx.get("floData", "")
            if not tx_floData_str:
                continue

            tx_floData = json.loads(tx_floData_str)
            actions = process_committee_floData(tx_floData)

            for addr in actions["add"]:
                address_actions[addr] = "add"
                logger.info(f"Marked {addr} as ADD")

            for addr in actions["remove"]:
                address_actions[addr] = "remove"
                logger.info(f"Marked {addr} as REMOVE")

        except Exception as e:
            logger.warning(f"Failed to parse floData in tx {txid}: {e}")
            continue

    # Build the final committee list based on last actions
    committee_list = [addr for addr, action in address_actions.items() if action == "add"]

    logger.info(f"Final committee list: {committee_list}")
    return committee_list





    def send_api_request(url):
        attempt = 0
        while attempt < MAX_RETRIES:
            try:
                response = requests.get(url, verify=API_VERIFY)
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(
                        f"Blockbook API returned {response.status_code}. "
                        f"Retrying in {RETRY_TIMEOUT_SHORT}s (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(RETRY_TIMEOUT_SHORT)
            except requests.RequestException as e:
                logger.warning(
                    f"Blockbook API request failed: {e}. "
                    f"Retrying in {RETRY_TIMEOUT_LONG}s (attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(RETRY_TIMEOUT_LONG)
            attempt += 1

        # All retries failed
        raise RuntimeError(f"Failed to fetch data from Blockbook API after {MAX_RETRIES} attempts")

    url = f'{api_url}/api/address/{admin_flo_id}?details=txs'

    try:
        response = send_api_request(url)
    except RuntimeError as e:
        logger.error(f"Committee list refresh aborted: {e}")
        return committee_list  # Or return [] if you prefer empty list on failure

    for transaction_info in response.get('txs', []):
        process_transaction(transaction_info)

    # Paging logic (uncomment if needed)
    # while 'incomplete' in response:
    #     url = f'{api_url}/api/address/{admin_flo_id}/txs?latest={latest_param}&mempool={mempool_param}&before={init_id}'
    #     response = send_api_request(url)
    #     for transaction_info in response.get('items', []):
    #         process_transaction(transaction_info)
    #     if 'incomplete' in response:
    #         init_id = response['initItem']

    return committee_list



def find_sender_receiver(transaction_data):
    normalize_transaction_data(transaction_data)

    vinlist = []

    for vin in transaction_data["vin"]:
        if "addresses" not in vin or not vin["addresses"]:
            logger.info(f"VIN missing addresses in transaction {transaction_data['txid']}. Rejecting.")
            return None, None
        vinlist.append([vin["addresses"][0], float(vin["value"])])

    totalinputval = float(transaction_data["valueIn"])

    for idx, item in enumerate(vinlist):
        if idx == 0:
            temp = item[0]
            continue
        if item[0] != temp:
            logger.info(f"System has found more than one address as part of vin. Transaction {transaction_data['txid']} is rejected")
            return None, None

    inputlist = [vinlist[0][0], totalinputval]
    inputadd = vinlist[0][0]

    if len(transaction_data["vout"]) > 2:
        logger.info(f"System has found more than 2 addresses as part of vout. Transaction {transaction_data['txid']} is rejected")
        return None, None

    outputlist = []
    addresscounter = 0
    inputcounter = 0
    for obj in transaction_data["vout"]:
        if "addresses" not in obj["scriptPubKey"] or not obj["scriptPubKey"]["addresses"]:
            continue
        addresscounter += 1
        if inputlist[0] == obj["scriptPubKey"]["addresses"][0]:
            inputcounter += 1
            continue
        outputlist.append([obj["scriptPubKey"]["addresses"][0], obj["value"]])

    if addresscounter == inputcounter:
        # All outputs are change, treat input as receiver too
        outputlist = [inputlist[0]]
    elif len(outputlist) != 1:
        logger.info(f"Transaction's change is not coming back to the input address. Transaction {transaction_data['txid']} is rejected")
        return None, None
    else:
        outputlist = outputlist[0]

    return inputlist[0], outputlist[0]

def check_database_existence(type, parameters):
    """
    Checks the existence of a MySQL database by attempting to connect to it.

    Args:
        type (str): Type of the database ('token', 'smart_contract').
        parameters (dict): Parameters for constructing database names.

    Returns:
        bool: True if the database exists, False otherwise.
    """

    # Construct database name safely
    if type == 'token':
        token_name = parameters.get('token_name')
        if token_name is None:
            raise ValueError("Missing token_name in parameters.")
        database_name = f"{mysql_config.database_prefix}_{token_name}_db"

    elif type == 'smart_contract':
        contract_name = parameters.get('contract_name')
        contract_address = parameters.get('contract_address')
        if contract_name is None or contract_address is None:
            raise ValueError("Missing contract_name or contract_address in parameters.")
        database_name = f"{mysql_config.database_prefix}_{contract_name}_{contract_address}_db"

    else:
        raise ValueError(f"Unsupported database type: {type}")

    engine_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{database_name}"

    try:
        with create_engine(engine_url, echo=False).connect():
            return True
    except OperationalError as e:
        logger.info(f"Database {database_name} does not exist or cannot connect: {e}")
        return False

def _missing_param(key):
    raise ValueError(f"Missing required parameter: {key}")

def create_database_connection(type, parameters=None):
    """
    Creates a database connection using MySQL credentials from the config file.

    Args:
        type (str): Type of the database ('token', 'smart_contract', 'system_dbs', 'latest_cache').
        parameters (dict, optional): Parameters for dynamic database names.

    Returns:
        connection: SQLAlchemy connection object.
    """

    if parameters is None:
        parameters = {}

    database_mapping = {
        'token': lambda: f"{mysql_config.database_prefix}_{parameters.get('token_name')}_db" if parameters.get('token_name') else _missing_param('token_name'),
        'smart_contract': lambda: f"{mysql_config.database_prefix}_{parameters.get('contract_name')}_{parameters.get('contract_address')}_db"
                                 if parameters.get('contract_name') and parameters.get('contract_address')
                                 else _missing_param('contract_name or contract_address'),
        'system_dbs': lambda: f"{mysql_config.database_prefix}_system_db",
        'latest_cache': lambda: f"{mysql_config.database_prefix}_latestCache_db"
    }

    if type not in database_mapping:
        raise ValueError(f"Unknown database type: {type}")

    database_name = database_mapping[type]()

    engine_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{database_name}"
    engine = create_engine(engine_url, echo=False)

    try:
        connection = engine.connect()
        return connection
    except OperationalError as e:
        logger.error(f"Failed to connect to database {database_name}: {e}")
        raise


def create_database_session_orm(type, parameters, base):
    """
    Creates a SQLAlchemy session for the specified database type, ensuring the database exists.

    Args:
        type (str): Type of the database ('token', 'smart_contract', 'system_dbs').
        parameters (dict): Parameters for constructing database names.
        base: SQLAlchemy declarative base for the ORM models.

    Returns:
        session: SQLAlchemy session object.
    """
    try:
        # Construct database name based on type
        if type == 'token':
            database_name = f"{mysql_config.database_prefix}_{parameters['token_name']}_db"
        elif type == 'smart_contract':
            database_name = f"{mysql_config.database_prefix}_{parameters['contract_name']}_{parameters['contract_address']}_db"
        elif type == 'system_dbs':
            database_name = f"{mysql_config.database_prefix}_{parameters['db_name']}_db"
        else:
            raise ValueError(f"Unknown database type: {type}")

        #logger.info(f"Database name constructed: {database_name}")

        # Check if the database exists using information_schema
        server_engine = create_engine(
            f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/",
            connect_args={"connect_timeout": DB_RETRY_TIMEOUT},  # 10 seconds timeout for connection
            echo=False
        )
        with server_engine.connect() as connection:
            #logger.info(f"Checking existence of database '{database_name}'...")
            db_exists = connection.execute(
                "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s",
                (database_name,)
            ).scalar()

            if not db_exists:
                logger.info(f"Database '{database_name}' does not exist. Creating it...")
                connection.execute(f"CREATE DATABASE `{database_name}`")
                logger.info(f"Database '{database_name}' created successfully.")
            #else:
                #logger.info(f"Database '{database_name}' already exists.")

        # Connect to the specific database and initialize tables
        #logger.info(f"Connecting to database '{database_name}'...")
        engine = create_engine(
            f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{database_name}",
            connect_args={"connect_timeout": DB_RETRY_TIMEOUT},
            echo=False
        )
        base.metadata.create_all(bind=engine)  # Create tables if they do not exist
        session = sessionmaker(bind=engine)()

        #logger.info(f"Session created for database '{database_name}' successfully.")
        return session

    except SQLAlchemyError as e:
        logger.error(f"SQLAlchemy error occurred: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error occurred: {e}")
        raise


def delete_contract_database(parameters):
    """
    Deletes a MySQL database for a smart contract if it exists.

    Args:
        parameters (dict): Parameters for constructing the database name.
            Example: {'contract_name': 'example_contract', 'contract_address': '0x123abc'}
    """
    
    # Construct the database name
    database_name = f"{mysql_config.database_prefix}_{parameters['contract_name']}_{parameters['contract_address']}_db"

    # Check if the database exists
    if check_database_existence('smart_contract', parameters):
        # Connect to MySQL server (without specifying a database)
        engine = create_engine(f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/", echo=False)
        with engine.connect() as connection:
            # Drop the database
            connection.execute(f"DROP DATABASE `{database_name}`")
            logger.info(f"Database '{database_name}' has been deleted.")
    else:
        logger.info(f"Database '{database_name}' does not exist.")


def filter_json_data(json_str):
    """
    Returns json_str if it's within size limits.
    Otherwise returns a JSON string: {"comment": "jsonData too long"}
    """
    if json_str is None:
        return None

    if len(json_str) > MAX_JSON_LENGTH:
        logger.warning(
            f"jsonData length {len(json_str)} exceeds {MAX_JSON_LENGTH}. "
            f"Storing placeholder JSON instead."
        )
        return json.dumps({
            "comment": "jsonData too long"
        })

    return json_str

def add_transaction_history(token_name, sourceFloAddress, destFloAddress, transferAmount, blockNumber, blockHash, blocktime, transactionHash, jsonData, transactionType, parsedFloData):
    if not token_name: logger.error("Missing token_name for add_transaction_history. Aborting."); return
    transferAmount = float(transferAmount) if transferAmount is not None else 0; blockNumber = int(blockNumber) if blockNumber is not None else 0; blocktime = int(blocktime) if blocktime is not None else 0
    while True:
        try:
            session = create_database_session_orm('token', {'token_name': token_name}, TokenBase)
            blockchainReference = urljoin(neturl, f"tx/{transactionHash}")
            try: json_payload = filter_json_data(jsonData)
            except Exception as e: logger.warning(f"Failed to filter/serialize jsonData: {e}"); json_payload = "{}"
            session.add(TransactionHistory(sourceFloAddress=sourceFloAddress, destFloAddress=destFloAddress, transferAmount=transferAmount, blockNumber=blockNumber, blockHash=blockHash, time=blocktime, transactionHash=transactionHash, blockchainReference=blockchainReference, jsonData=json_payload, transactionType=transactionType, parsedFloData=parsedFloData))
            session.commit(); session.close(); break
        except Exception as e:
            logger.exception(f"Database error while inserting transaction history for token({token_name}): {e}")
            time.sleep(DB_RETRY_TIMEOUT)

def add_contract_transaction_history(contract_name, contract_address, transactionType, transactionSubType, sourceFloAddress, destFloAddress, transferAmount, blockNumber, blockHash, blocktime, transactionHash, jsonData, parsedFloData,transferToken=None):
    if not contract_name or not contract_address: logger.error("Missing contract_name or contract_address. Aborting add_contract_transaction_history."); return
    transferAmount = float(transferAmount) if transferAmount is not None else 0; blockNumber = int(blockNumber) if blockNumber is not None else 0; blocktime = int(blocktime) if blocktime is not None else 0
    while True:
        try:
            session = create_database_session_orm('smart_contract', {'contract_name': contract_name, 'contract_address': contract_address}, ContractBase)
            blockchainReference = urljoin(neturl, f"tx/{transactionHash}")
            try: json_payload = filter_json_data(jsonData)
            except Exception as e: logger.warning(f"Failed to filter/serialize jsonData: {e}"); json_payload = "{}"
            session.add(ContractTransactionHistory(transactionType=transactionType, transactionSubType=transactionSubType, sourceFloAddress=sourceFloAddress, destFloAddress=destFloAddress, transferAmount=transferAmount, transferToken=transferToken, blockNumber=blockNumber, blockHash=blockHash, time=blocktime, transactionHash=transactionHash, blockchainReference=blockchainReference, jsonData=json_payload, parsedFloData=parsedFloData))
            session.commit(); session.close(); break
        except Exception as e:
            logger.exception(f"Error writing to smart_contract({contract_name}) DB: {e}")
            time.sleep(DB_RETRY_TIMEOUT)

def rejected_transaction_history(transaction_data, parsed_data, sourceFloAddress, destFloAddress, rejectComment):
    tokenIdentification = parsed_data.get('tokenIdentification'); tokenAmount = parsed_data.get('tokenAmount'); transferAmount = float(tokenAmount) if tokenAmount is not None else 0
    blockNumber = int(transaction_data.get('blockheight', 0)); blocktime = int(transaction_data.get('time', 0)); transactionHash = transaction_data.get('txid'); blockHash = transaction_data.get('blockhash')
    try: transaction_json = json.dumps(transaction_data, default=str)
    except Exception as e: logger.warning(f"Failed to serialize transaction_data: {e}"); transaction_json = "{}"
    try: parsedFloData_json = json.dumps(parsed_data, default=str)
    except Exception as e: logger.warning(f"Failed to serialize parsed_data: {e}"); parsedFloData_json = "{}"
    while True:
        try:
            session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
            blockchainReference = urljoin(neturl, f"tx/{transactionHash}")
            session.add(RejectedTransactionHistory(tokenIdentification=tokenIdentification, sourceFloAddress=sourceFloAddress, destFloAddress=destFloAddress, transferAmount=transferAmount, blockNumber=blockNumber, blockHash=blockHash, time=blocktime, transactionHash=transactionHash, blockchainReference=blockchainReference, jsonData=filter_json_data(transaction_json), rejectComment=rejectComment, transactionType=parsed_data.get('type'), parsedFloData=parsedFloData_json))
            session.commit(); session.close(); break
        except Exception as e:
            logger.error(f"Error while processing rejected transaction: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            logger.info(f"Retrying connection to 'system' database in {DB_RETRY_TIMEOUT} seconds...")
            time.sleep(DB_RETRY_TIMEOUT)


def rejected_contract_transaction_history(transaction_data, parsed_data, transactionType, contractAddress, sourceFloAddress, destFloAddress, rejectComment):
    contractName = parsed_data.get('contractName', None); blockNumber = transaction_data.get('blockheight', 0); blockHash = transaction_data.get('blockhash', ""); blocktime = transaction_data.get('time', 0); transactionHash = transaction_data.get('txid', "")
    try: jsonData = json.dumps(transaction_data, default=str)
    except Exception as e: logger.warning(f"Failed to serialize transaction_data: {e}"); jsonData = "{}"
    try: parsedFloData_json = json.dumps(parsed_data, default=str)
    except Exception as e: logger.warning(f"Failed to serialize parsed_data: {e}"); parsedFloData_json = "{}"
    blockchainReference = urljoin(neturl, f"tx/{transactionHash}")
    while True:
        try:
            session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
            session.add(RejectedContractTransactionHistory(transactionType=transactionType, contractName=contractName, contractAddress=contractAddress, sourceFloAddress=sourceFloAddress, destFloAddress=destFloAddress, transferAmount=None, blockNumber=blockNumber, blockHash=blockHash, time=blocktime, transactionHash=transactionHash, blockchainReference=blockchainReference, jsonData=filter_json_data(jsonData), rejectComment=rejectComment, parsedFloData=parsedFloData_json))
            session.commit(); session.close(); break
        except Exception as e:
            logger.error(f"Exception in rejected_contract_transaction_history: {e}")
            logger.error(traceback.format_exc())
            logger.info(f"Unable to connect to 'system' database... retrying in {DB_RETRY_TIMEOUT} seconds")
            time.sleep(DB_RETRY_TIMEOUT)



def convert_datetime_to_arrowobject(expiryTime):
    expirytime_split = expiryTime.split(' ')
    parse_string = '{}/{}/{} {}'.format(expirytime_split[3], parsing.months[expirytime_split[1]], expirytime_split[2], expirytime_split[4])
    expirytime_object = parsing.arrow.get(parse_string, 'YYYY/M/D HH:mm:ss').replace(tzinfo=expirytime_split[5][3:])
    return expirytime_object

def convert_datetime_to_arrowobject_regex(expiryTime):
    datetime_re = re.compile(r'(\w{3}\s\w{3}\s\d{1,2}\s\d{4}\s\d{2}:\d{2}:\d{2})\s(gmt[+-]\d{4})')
    match = datetime_re.search(expiryTime)
    if match:
        datetime_str = match.group(1)
        timezone_offset = match.group(2)[3:]
        dt = arrow.get(datetime_str, 'ddd MMM DD YYYY HH:mm:ss').replace(tzinfo=timezone_offset)
        return dt
    else:
        return 0


def is_a_contract_address(floAddress):
    retries = 0
    while True:
        try:
            session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
            record = session.query(ContractAddressMapping.contractAddress).filter(ContractAddressMapping.contractAddress == floAddress).first()
            session.close()
            return record is not None
        except Exception as e:
            logger.info(f"Unable to connect to 'system' database... retrying in {DB_RETRY_TIMEOUT} seconds. Reason: {e}")
            retries += 1
            if retries > 10:
                logger.error("Max retries exceeded while checking contract address.")
                raise
            time.sleep(DB_RETRY_TIMEOUT)

def fetchDynamicSwapPrice(contractStructure, blockinfo):
    oracle_address = contractStructure['oracle_address']
    base_url = api_url.rstrip("/") + "/"
    relative_path = f"api/address/{oracle_address}?details=txs"
    api_endpoint = urljoin(base_url, relative_path)
    logger.info(f"Starting dynamic price fetch for contract '{contractStructure['contractName']}' at address '{contractStructure['contractAddress']}'")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"Attempt {attempt}: querying oracle address {oracle_address} via {api_endpoint}")
            response = requests.get(api_endpoint, verify=API_VERIFY, timeout=10)
            logger.info(f"HTTP status code: {response.status_code}")
            if response.status_code == 200:
                response_json = response.json()
                txs = response_json.get('txs', [])
                logger.info(f"Found {len(txs)} transactions for oracle address.")
                if not txs:
                    logger.info(f"No transactions found for oracle address. Using default contract price {contractStructure['price']}")
                    return float(contractStructure['price'])
                for transaction in txs:
                    normalize_transaction_data(transaction)
                    floData = transaction.get('floData', '')
                    try: tx_time = int(transaction.get('time', 0)); logger.debug(f"Transaction time: {tx_time}")
                    except (ValueError, TypeError): logger.warning("Invalid time in transaction, skipping."); continue
                    if tx_time >= int(blockinfo['time']): logger.debug(f"Transaction time {tx_time} newer than block time {blockinfo['time']}. Skipping."); continue
                    try:
                        sender_address, receiver_address = find_sender_receiver(transaction)
                        logger.debug(f"Transaction sender: {sender_address}, receiver: {receiver_address}")
                        if sender_address != oracle_address: logger.debug("Sender mismatch. Skipping transaction."); continue
                        if receiver_address != contractStructure['contractAddress']: logger.debug("Receiver mismatch. Skipping transaction."); continue
                        try: floData_json = json.loads(floData)
                        except json.JSONDecodeError as e: logger.warning(f"JSON decode error in floData: {e}. Skipping transaction."); continue
                        price_update = floData_json.get('price-update')
                        if price_update:
                            contract_name_match = price_update.get('contract-name') == contractStructure['contractName']
                            contract_address_match = price_update.get('contract-address') == contractStructure['contractAddress']
                            if contract_name_match and contract_address_match:
                                price_value = float(price_update['price'])
                                logger.info(f"Fetched dynamic price {price_value} for contract {contractStructure['contractName']}")
                                return price_value
                            else:
                                logger.debug(f"Price update found but contract name/address mismatch: {price_update}")
                        else:
                            logger.debug("No price-update field in floData.")
                    except (AssertionError, KeyError, ValueError, json.JSONDecodeError) as e:
                        logger.warning(f"Error while processing transaction for dynamic price: {e}")
                        logger.debug(traceback.format_exc())
                        continue
                logger.info("No matching price-update found in transactions. Using default contract price.")
                return float(contractStructure['price'])
            else:
                logger.warning(f"API returned HTTP {response.status_code}. Retrying after {RETRY_TIMEOUT_LONG} seconds.")
                time.sleep(RETRY_TIMEOUT_LONG)
        except requests.RequestException as e:
            logger.warning(f"RequestException during fetchDynamicSwapPrice: {e}. Retrying after {RETRY_TIMEOUT_LONG} seconds.")
            logger.debug(traceback.format_exc())
            time.sleep(RETRY_TIMEOUT_LONG)
        except Exception as e:
            logger.error(f"Unhandled exception in fetchDynamicSwapPrice: {e}")
            logger.error(traceback.format_exc())
            time.sleep(RETRY_TIMEOUT_LONG)
    logger.error(f"Failed to fetch dynamic price after {MAX_RETRIES} attempts. Falling back to default price {contractStructure['price']}")
    return float(contractStructure['price'])



def get_blockhash_by_height(blockindex):
    api_path = ""; expected_key = ""
    if blockbook_type == "blockbook_legacy": api_path = f"v2/block-index/{blockindex}"; expected_key = "blockHash"
    elif blockbook_type == "address_indexer": api_path = f"blockheight/{blockindex}"; expected_key = "hash"
    else: raise Exception(f"Unsupported blockbook_type: {blockbook_type}")
    max_retries = 10; retries = 0
    while retries < max_retries:
        try:
            response = newMultiRequest(api_path)
            if not isinstance(response, dict):
                logger.warning(f"Unexpected response type for block {blockindex}: {type(response)}. Retrying...")
                retries += 1; time.sleep(DB_RETRY_TIMEOUT); continue
            if expected_key in response:
                return response[expected_key]
            else:
                logger.warning(f"Missing '{expected_key}' in response for block {blockindex}. Retrying...")
        except Exception as e:
            logger.warning(f"Error fetching block {blockindex} from API '{api_path}': {e}. Retrying...")
        retries += 1; time.sleep(DB_RETRY_TIMEOUT)
    logger.error(f"Failed to fetch block hash for block {blockindex} after {max_retries} attempts.")
    raise Exception(f"Unable to retrieve block hash for block {blockindex}. Repeated API failures.")

def store_rejected_transaction(transaction_data, parsed_data, blockinfo):
    session = None
    try:
        session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
        tx_type = parsed_data.get("type", ""); reject_comment = "Rejected in processTransaction"
        blockchain_reference = f"{config['DEFAULT']['NET']}/tx/{transaction_data['txid']}"
        block_hash = blockinfo.get("hash", ""); block_number = int(blockinfo.get("height") or 0)
        tx_time = int(transaction_data.get("time") or 0)
        sourceFloAddress = parsed_data.get("sourceFloAddress") or transaction_data.get("senderAddress", "")
        destFloAddress = parsed_data.get("destFloAddress") or transaction_data.get("receiverAddress", "")
        tokenIdentification = parsed_data.get("tokenIdentification", "")
        transferAmount = parsed_data.get("tokenAmount", 0.0)
        jsonData_filtered = filter_json_data(json.dumps(transaction_data))
        if tx_type.startswith("smartContract") or tx_type.startswith("contract"):
            rejected_tx = RejectedContractTransactionHistory(transactionType=tx_type, transactionSubType=parsed_data.get("subtype", ""), contractName=parsed_data.get("contractName", ""), contractAddress=parsed_data.get("contractAddress", ""), sourceFloAddress=sourceFloAddress, destFloAddress=destFloAddress, transferAmount=transferAmount, blockNumber=block_number, blockHash=block_hash, time=tx_time, transactionHash=transaction_data["txid"], blockchainReference=blockchain_reference, jsonData=jsonData_filtered, rejectComment=reject_comment, parsedFloData=json.dumps(parsed_data))
            session.add(rejected_tx)
        else:
            rejected_tx = RejectedTransactionHistory(tokenIdentification=tokenIdentification, sourceFloAddress=sourceFloAddress, destFloAddress=destFloAddress, transferAmount=transferAmount, blockNumber=block_number, blockHash=block_hash, time=tx_time, transactionHash=transaction_data["txid"], blockchainReference=blockchain_reference, jsonData=jsonData_filtered, rejectComment=reject_comment, transactionType=tx_type, parsedFloData=json.dumps(parsed_data))
            session.add(rejected_tx)
        session.commit()
        logger.info(f"Inserted rejected transaction {transaction_data['txid']} into rejection table.")
    except Exception as e:
        logger.error(f"Error inserting into rejection table: {e}")
    finally:
        if session:
            try: session.close()
            except: pass


def processBlock(blockindex=None, blockhash=None, blockinfo=None, keywords=None, force_store=False):
    """
    Processes a block with optional keyword filtering.

    :param blockindex: The block index (height) to process.
    :param blockhash: The block hash to process.
    :param blockinfo: The block data to process. If not provided, it will be fetched.
    :param keywords: List of keywords to filter transactions. If None, processes all transactions.
    :param force_store: If True, save the block to latestBlocks even if no meaningful transactions were parsed.
    """
    global args

    while True:
        # Retrieve block information if not already provided
        if blockinfo is None:
            if blockindex is not None and blockhash is None:
                logger.info(f'Processing block {blockindex}')
                blockhash = get_blockhash_by_height(blockindex)

            blockinfo = newMultiRequest(f"block/{blockhash}")

        # Normalize transaction data in the block
        normalize_block_transactions(blockinfo)

        # Filter based on keywords if provided
        if keywords:
            should_process = any(
                any(keyword.lower() in transaction_data.get("floData", "").lower() for keyword in keywords)
                for transaction_data in blockinfo.get('txs', [])
            )
            if not should_process:
                logger.info(f"Block {blockindex} does not contain relevant keywords. Skipping processing.")
                break

        # Add block to the database (this shouldn't prevent further processing)
        block_already_exists = False
        try:
            block_already_exists = add_block_hashrecord(blockinfo["height"], blockinfo["hash"])
        except Exception as e:
            logger.error(f"Error adding block {blockinfo['height']} to the database: {e}")

        if block_already_exists:
            logger.info(f"Block {blockindex} already exists but will continue processing its transactions.")

        # Detect reorg every 10 blocks
        if not args.rebuild and blockindex is not None and blockindex % 10 == 0:
            fork_point = detect_reorg()
            if fork_point is not None:
                logger.warning(f"Blockchain reorganization detected! Fork point at block {fork_point}.")
                rollback_to_block(fork_point)
                blockindex = fork_point + 1
                blockhash = None
                blockinfo = None
                blockhash = get_blockhash_by_height(blockindex)
                blockinfo = newMultiRequest(f"block/{blockhash}")
                normalize_block_transactions(blockinfo)
                logger.info(f"Rollback complete. Restarting processing from block {blockindex} with hash {blockhash}.")
                continue

        manage_expired_triggered_items(blockinfo)

        acceptedTxList = []
        parsedResults = []
        any_parsed = False

        for transaction_data in blockinfo["txs"]:
            if "time" not in transaction_data or transaction_data["time"] is None:
                transaction_data["time"] = blockinfo.get("time", 0)

            transaction = transaction_data["txid"]

            text = transaction_data.get("floData", "")
            text = text.replace("\n", " \n ")
            returnval = None

            parsed_data = parsing.parse_floData(text, blockinfo, config['DEFAULT']['NET'])
            if parsed_data['type'] not in ['noise', None, '']:
                any_parsed = True
                logger.info(f"Processing transaction {transaction}")
                logger.info(f"floData {text} is parsed to {parsed_data}")
                returnval = processTransaction(transaction_data, parsed_data, blockinfo)

                if returnval == 1:
                    acceptedTxList.append(transaction)
                    parsedResults.append({
                        "txid": transaction,
                        "parsedData": parsed_data
                    })
                elif returnval == 0:
                    logger.info(f"Transfer for the transaction {transaction} is illegitimate. Saving to rejected table.")
                    store_rejected_transaction(transaction_data, parsed_data, blockinfo)

        logger.info("Completed tx loop")

        # Decide whether to save block
        if any_parsed or force_store:
            if any_parsed:
                blockinfo['parsedResult'] = parsedResults
            else:
                blockinfo['parsedResult'] = []

            # Check JSON size
            json_full = json.dumps(blockinfo)
            size_bytes = len(json_full.encode('utf-8'))

            if size_bytes > 50_000:
                logger.info(f"Block {blockinfo['height']} JSON size {size_bytes} exceeds 50KB. Pruning unaccepted transactions.")

                # Prune non-accepted transactions
                if len(acceptedTxList) > 0:
                    new_txs = [tx for tx in blockinfo['txs'] if tx['txid'] in acceptedTxList]
                    blockinfo['txs'] = new_txs
                else:
                    blockinfo['txs'] = []

                json_pruned = json.dumps(blockinfo)
                size_pruned = len(json_pruned.encode('utf-8'))
                logger.info(f"Block {blockinfo['height']} JSON size after pruning: {size_pruned} bytes.")

            try:
                updateLatestBlock(blockinfo)
                logger.info(f"✅ Block {blockinfo['height']} saved in latestBlocks.")
            except Exception as e:
                logger.error(f"Error updating latest block {blockinfo['height']} in updateLatestBlock: {e}")

        else:
            logger.info(f"ℹ️ Block {blockinfo['height']} contains only noise. Not saving in latestBlocks.")

        try:
            session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
            entry = session.query(SystemData).filter(SystemData.attribute == 'lastblockscanned').first()
            entry.value = str(blockinfo['height'])
            session.commit()
            session.close()
        except Exception as e:
            logger.error(f"Error connecting to 'system' database: {e}. Retrying in {DB_RETRY_TIMEOUT} seconds")
            time.sleep(DB_RETRY_TIMEOUT)

        break



def updateLatestTransaction(transactionData, parsed_data, db_reference, transactionType=None ):
    # connect to latest transaction db
    while True:
        try:
            conn = create_database_connection('latest_cache', {'db_name':"latestCache"})
            break
        except:
            time.sleep(DB_RETRY_TIMEOUT)
    if transactionType is None:
        transactionType = parsed_data['type']
    try:
        conn.execute("INSERT INTO latestTransactions (transactionHash, blockNumber, jsonData, transactionType, parsedFloData, db_reference) VALUES (%s, %s, %s, %s, %s, %s)", (transactionData['txid'], transactionData['blockheight'], json.dumps(transactionData), transactionType, json.dumps(parsed_data), db_reference))
    except Exception as e:
        logger.error(f"Error inserting into latestTransactions: {e}")
    finally:
        conn.close()



def updateLatestBlock(blockData):
    # connect to latest block db
    while True:
        try:
            conn = create_database_connection('latest_cache', {'db_name':"latestCache"})
            break
        except:
            time.sleep(DB_RETRY_TIMEOUT)
    conn.execute('INSERT INTO latestBlocks(blockNumber, blockHash, jsonData) VALUES (%s, %s, %s)',(blockData['height'], blockData['hash'], json.dumps(blockData)))
    #conn.commit()
    conn.close()


def process_pids(entries, session, piditem):
    for entry in entries:
        '''consumedpid_dict = literal_eval(entry.consumedpid)
        total_consumedpid_amount = 0
        for key in consumedpid_dict.keys():
            total_consumedpid_amount = total_consumedpid_amount + float(consumedpid_dict[key])
        consumedpid_dict[piditem[0]] = total_consumedpid_amount
        entry.consumedpid = str(consumedpid_dict)'''
        entry.orphaned_parentid = entry.parentid
        entry.parentid = None
    #session.commit()
    return 1


def transferToken(tokenIdentification, tokenAmount, inputAddress, outputAddress, transaction_data=None, parsed_data=None, isInfiniteToken=None, blockinfo=None, transactionType=None):
    if transaction_data: normalize_transaction_data(transaction_data)
    if transactionType is None:
        try: transactionType = parsed_data['type']
        except: logger.info("This is a critical error. Please report to developers")
    tokenAmount = float(tokenAmount)
    if tokenAmount <= 0:
        logger.info(f"Skipping token transfer of zero amount from {inputAddress} to {outputAddress}.")
        if transaction_data and parsed_data:
            add_transaction_history(token_name=tokenIdentification, sourceFloAddress=inputAddress, destFloAddress=outputAddress, transferAmount=tokenAmount, blockNumber=blockinfo['height'] if blockinfo else None, blockHash=blockinfo['hash'] if blockinfo else None, blocktime=blockinfo['time'] if blockinfo else None, transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=transactionType, parsedFloData=json.dumps(parsed_data))
        return 1
    while True:
        try:
            session = create_database_session_orm('token', {'token_name': f"{tokenIdentification}"}, TokenBase)
            break
        except: time.sleep(DB_RETRY_TIMEOUT)
    if isInfiniteToken == True:
        receiverAddress_details = session.query(ActiveTable).filter(ActiveTable.address == outputAddress, ActiveTable.addressBalance != None).first()
        addressBalance = tokenAmount if receiverAddress_details is None else perform_decimal_operation('addition', receiverAddress_details.addressBalance, tokenAmount)
        if receiverAddress_details: receiverAddress_details.addressBalance = None
        session.add(ActiveTable(address=outputAddress, consumedpid='1', transferBalance=tokenAmount, addressBalance=addressBalance, blockNumber=blockinfo['height']))
        add_transaction_history(token_name=tokenIdentification, sourceFloAddress=inputAddress, destFloAddress=outputAddress, transferAmount=tokenAmount, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=transactionType, parsedFloData=json.dumps(parsed_data))
        session.commit(); session.close(); return 1
    else:
        query_data = session.query(ActiveTable.transferBalance).filter_by(address=inputAddress).all()
        availableTokens = float(sum(Decimal(f"{amount[0]}") if amount[0] is not None else Decimal(0) for amount in query_data))
        logger.info(f"The sender address {inputAddress} owns {availableTokens} {tokenIdentification.upper()} tokens")
        commentTransferAmount = float(tokenAmount)
        if availableTokens is None:
            logger.info(f"The sender address {inputAddress} doesn't own any {tokenIdentification.upper()} tokens"); session.close(); return 0
        elif availableTokens < commentTransferAmount:
            logger.info("The transfer amount is more than the user balance. This transaction will be discarded."); session.close(); return 0
        elif availableTokens >= commentTransferAmount:
            logger.info(f"System has accepted transfer of {commentTransferAmount} {tokenIdentification.upper()}# from {inputAddress} to {outputAddress}")
            table = session.query(ActiveTable).filter(ActiveTable.address == inputAddress).all()
            pidlst = []; checksum = 0
            for row in table:
                if checksum >= commentTransferAmount: break
                pidlst.append([row.id, row.transferBalance])
                checksum = perform_decimal_operation('addition', checksum, row.transferBalance)
            if checksum == commentTransferAmount:
                consumedpid_string = ''; lastid = session.query(ActiveTable)[-1].id; piddict = {}
                for piditem in pidlst:
                    entry = session.query(ActiveTable).filter(ActiveTable.id == piditem[0]).all()
                    consumedpid_string += f"{piditem[0]},"
                    piddict[piditem[0]] = piditem[1]
                    session.add(TransferLogs(sourceFloAddress=inputAddress, destFloAddress=outputAddress, transferAmount=entry[0].transferBalance, sourceId=piditem[0], destinationId=lastid + 1, blockNumber=blockinfo['height'], time=blockinfo['time'], transactionHash=transaction_data['txid']))
                    entry[0].transferBalance = 0
                if len(consumedpid_string) > 1: consumedpid_string = consumedpid_string[:-1]
                receiverAddress_details = session.query(ActiveTable).filter(ActiveTable.address == outputAddress, ActiveTable.addressBalance != None).first()
                addressBalance = commentTransferAmount if receiverAddress_details is None else perform_decimal_operation('addition', receiverAddress_details.addressBalance, commentTransferAmount)
                if receiverAddress_details: receiverAddress_details.addressBalance = None
                session.add(ActiveTable(address=outputAddress, consumedpid=str(piddict), transferBalance=commentTransferAmount, addressBalance=addressBalance, blockNumber=blockinfo['height']))
                senderAddress_details = session.query(ActiveTable).filter_by(address=inputAddress).order_by(ActiveTable.id.desc()).first()
                if senderAddress_details: senderAddress_details.addressBalance = perform_decimal_operation('subtraction', senderAddress_details.addressBalance, commentTransferAmount)
                for piditem in pidlst:
                    entries = session.query(ActiveTable).filter(ActiveTable.parentid == piditem[0]).all()
                    process_pids(entries, session, piditem)
                    entries = session.query(ConsumedTable).filter(ConsumedTable.parentid == piditem[0]).all()
                    process_pids(entries, session, piditem)
                    session.execute('INSERT INTO consumedTable (id, address, parentid, consumedpid, transferBalance, addressBalance, orphaned_parentid, blockNumber) SELECT id, address, parentid, consumedpid, transferBalance, addressBalance, orphaned_parentid, blockNumber FROM activeTable WHERE id={}'.format(piditem[0]))
                    session.execute('DELETE FROM activeTable WHERE id={}'.format(piditem[0]))
                    session.commit()
                session.commit()
            elif checksum > commentTransferAmount:
                consumedpid_string = ''; lastid = session.query(ActiveTable)[-1].id; piddict = {}
                for idx, piditem in enumerate(pidlst):
                    entry = session.query(ActiveTable).filter(ActiveTable.id == piditem[0]).all()
                    if idx != len(pidlst) - 1:
                        session.add(TransferLogs(sourceFloAddress=inputAddress, destFloAddress=outputAddress, transferAmount=entry[0].transferBalance, sourceId=piditem[0], destinationId=lastid + 1, blockNumber=blockinfo['height'], time=blockinfo['time'], transactionHash=transaction_data['txid']))
                        entry[0].transferBalance = 0
                        piddict[piditem[0]] = piditem[1]
                        consumedpid_string += f"{piditem[0]},"
                    else:
                        remaining_transfer = perform_decimal_operation('subtraction', piditem[1], perform_decimal_operation('subtraction', checksum, commentTransferAmount))
                        session.add(TransferLogs(sourceFloAddress=inputAddress, destFloAddress=outputAddress, transferAmount=remaining_transfer, sourceId=piditem[0], destinationId=lastid + 1, blockNumber=blockinfo['height'], time=blockinfo['time'], transactionHash=transaction_data['txid']))
                        entry[0].transferBalance = perform_decimal_operation('subtraction', checksum, commentTransferAmount)
                if len(consumedpid_string) > 1: consumedpid_string = consumedpid_string[:-1]
                receiverAddress_details = session.query(ActiveTable).filter(ActiveTable.address == outputAddress, ActiveTable.addressBalance != None).first()
                addressBalance = commentTransferAmount if receiverAddress_details is None else perform_decimal_operation('addition', receiverAddress_details.addressBalance, commentTransferAmount)
                if receiverAddress_details: receiverAddress_details.addressBalance = None
                session.add(ActiveTable(address=outputAddress, parentid=pidlst[-1][0], consumedpid=str(piddict), transferBalance=commentTransferAmount, addressBalance=addressBalance, blockNumber=blockinfo['height']))
                senderAddress_details = session.query(ActiveTable).filter_by(address=inputAddress).order_by(ActiveTable.id.desc()).first()
                if senderAddress_details: senderAddress_details.addressBalance = perform_decimal_operation('subtraction', senderAddress_details.addressBalance, commentTransferAmount)
                for piditem in pidlst[:-1]:
                    entries = session.query(ActiveTable).filter(ActiveTable.parentid == piditem[0]).all()
                    process_pids(entries, session, piditem)
                    entries = session.query(ConsumedTable).filter(ConsumedTable.parentid == piditem[0]).all()
                    process_pids(entries, session, piditem)
                    session.execute('INSERT INTO consumedTable (id, address, parentid, consumedpid, transferBalance, addressBalance, orphaned_parentid, blockNumber) SELECT id, address, parentid, consumedpid, transferBalance, addressBalance, orphaned_parentid, blockNumber FROM activeTable WHERE id={}'.format(piditem[0]))
                    session.execute('DELETE FROM activeTable WHERE id={}'.format(piditem[0]))
                session.commit()
            add_transaction_history(token_name=tokenIdentification, sourceFloAddress=inputAddress, destFloAddress=outputAddress, transferAmount=tokenAmount, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=transactionType, parsedFloData=json.dumps(parsed_data))
            session.commit(); session.close(); 

    txid = transaction_data["txid"] if transaction_data else None
    height = blockinfo["height"] if blockinfo else None
    bhash = blockinfo["hash"] if blockinfo else None

    try:
        systemdb_conn = create_database_connection("system_dbs", {"db_name": "system"})
        check_query = systemdb_conn.execute(
            "SELECT 1 FROM tokenAddressMapping WHERE tokenAddress = %s AND token = %s LIMIT 1",
            (outputAddress, tokenIdentification)
        ).fetchone()

        if not check_query:
            if not isInfiniteToken or (isInfiniteToken and inputAddress == db_object.get('root_address')):
                systemdb_conn.execute(
                    "INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES (%s, %s, %s, %s, %s)",
                    (
                        outputAddress,
                        tokenIdentification,
                        txid,
                        height,
                        bhash
                    )
                )

    finally:
        systemdb_conn.close()
    return 1


def trigger_internal_contract_onvalue(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name, contract_address, transaction_subType):

    tokenIdentification = contractStructure.get('tokenIdentification', 'unknown')
    parsed_data['tokenIdentification'] = tokenIdentification
    
    # Trigger the contract
    if tokenAmount_sum <= 0:
        # Add transaction to ContractTransactionHistory
        add_contract_transaction_history(contract_name=contract_name, contract_address=contract_address, transactionType='trigger', transactionSubType='zero-participation', sourceFloAddress='', destFloAddress='', transferAmount=0, transferToken=tokenIdentification, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
        # Add transaction to latestCache
        updateLatestTransaction(transaction_data, parsed_data , f"{contract_name}_{contract_address}")

    else:
        payeeAddress = json.loads(contractStructure['payeeAddress'])
        

        for floaddress in payeeAddress.keys():
            transferAmount = perform_decimal_operation('multiplication', tokenAmount_sum, perform_decimal_operation('division', payeeAddress[floaddress], 100))
            returnval = transferToken(tokenIdentification, transferAmount, contract_address, floaddress, transaction_data=transaction_data, blockinfo = blockinfo, parsed_data = parsed_data)
            if returnval == 0:
                logger.critical("Something went wrong in the token transfer method while doing local Smart Contract Trigger")
                return 0

            # Add transaction to ContractTransactionHistory
            add_contract_transaction_history(contract_name=contract_name, contract_address=contract_address, transactionType='trigger', transactionSubType=transaction_subType, sourceFloAddress=contract_address, destFloAddress=floaddress, transferAmount=transferAmount, transferToken=tokenIdentification, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
            # Add transaction to latestCache
            updateLatestTransaction(transaction_data, parsed_data , f"{contract_name}_{contract_address}")
    return 1


def process_minimum_subscriptionamount(contractStructure, connection, blockinfo, transaction_data, parsed_data):
    tokenIdentification = contractStructure.get('tokenIdentification', 'unknown')
    minimumsubscriptionamount = float(contractStructure['minimumsubscriptionamount'])

    rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
    tokenAmount_sum = float(sum(Decimal(f"{row[0]}") for row in rows))

    if tokenAmount_sum < minimumsubscriptionamount:
        # Initialize payback to contract participants
        contractParticipants = connection.execute('SELECT participantAddress, tokenAmount, transactionHash FROM contractparticipants').fetchall()

        for participant in contractParticipants:
            tokenIdentification = contractStructure['tokenIdentification']
            contractAddress = connection.execute('SELECT value FROM contractstructure WHERE attribute="contractAddress"').fetchall()[0][0]
            #transferToken(tokenIdentification, tokenAmount, inputAddress, outputAddress, transaction_data=None, parsed_data=None, isInfiniteToken=None, blockinfo=None, transactionType=None)
            returnval = transferToken(tokenIdentification, participant[1], contractAddress, participant[0], blockinfo = blockinfo, transaction_data=transaction_data,  parsed_data=parsed_data)
            if returnval == 0:
                logger.critical("Something went wrong in the token transfer method while doing local Smart Contract Trigger. THIS IS CRITICAL ERROR")
                return
            
            connection.execute('UPDATE contractparticipants SET winningAmount="{}" WHERE participantAddress="{}" AND transactionHash="{}"'.format(participant[1], participant[0], participant[2]))

            # add transaction to ContractTransactionHistory
            add_contract_transaction_history(contract_name=contractStructure['contractName'], contract_address=contractStructure['contractAddress'], transactionType=parsed_data['type'], transactionSubType='minimumsubscriptionamount-payback', sourceFloAddress=contractAddress, destFloAddress=participant[0], transferAmount=participant[1], transferToken=tokenIdentification, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
        return 1
    else:
        return 0


def check_contract_status(contractName, contractAddress):
    # Status of the contract is at 2 tables in system.db
    # activecontracts and time_actions
    # select the last entry from the column 
    while True:
        try:
            connection = create_database_connection('system_dbs')
            break
        except:
            time.sleep(DB_RETRY_TIMEOUT)
    contract_status = connection.execute(f'SELECT status FROM time_actions WHERE id=(SELECT MAX(id) FROM time_actions WHERE contractName="{contractName}" AND contractAddress="{contractAddress}")').fetchall()
    return contract_status[0][0]


def close_expire_contract(contractStructure, contractStatus, transactionHash, blockNumber, blockHash, incorporationDate, expiryDate, closeDate, trigger_time, trigger_activity, contractName, contractAddress, contractType, tokens_db, parsed_data, blockHeight):
    logger.info(f"Closing/expiring contract {contractName} at {contractAddress} with status {contractStatus}")
    while True:
        try:
            session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
            break
        except Exception as e:
            logger.warning(f"Retrying connection to system DB due to error: {e}")
            time.sleep(DB_RETRY_TIMEOUT)
    try:
        session.execute(text("INSERT INTO activecontracts (id, contractName, contractAddress, status, tokenIdentification, contractType, transactionHash, blockNumber, blockHash, incorporationDate, expiryDate, closeDate) VALUES (:id, :contractName, :contractAddress, :status, :tokenIdentification, :contractType, :transactionHash, :blockNumber, :blockHash, :incorporationDate, :expiryDate, :closeDate)"), {'id': None, 'contractName': contractStructure['contractName'], 'contractAddress': contractStructure['contractAddress'], 'status': contractStatus, 'tokenIdentification': contractStructure['tokenIdentification'], 'contractType': contractStructure['contractType'], 'transactionHash': transactionHash, 'blockNumber': blockNumber, 'blockHash': blockHash, 'incorporationDate': incorporationDate, 'expiryDate': expiryDate, 'closeDate': closeDate})
        session.execute(text("INSERT INTO time_actions (id, time, activity, status, contractName, contractAddress, contractType, tokens_db, parsed_data, transactionHash, blockNumber) VALUES (:id, :time, :activity, :status, :contractName, :contractAddress, :contractType, :tokens_db, :parsed_data, :transactionHash, :blockNumber)"), {'id': None, 'time': trigger_time, 'activity': trigger_activity, 'status': contractStatus, 'contractName': contractName, 'contractAddress': contractAddress, 'contractType': contractType, 'tokens_db': tokens_db, 'parsed_data': parsed_data, 'transactionHash': transactionHash, 'blockNumber': blockHeight})
        session.commit()
        logger.info(f"Contract {contractName} successfully marked as {contractStatus}.")
    except Exception as e:
        logger.error(f"Failed to close/expire contract {contractName}. Error: {e}")
        session.rollback()
        raise
    finally:
        session.close()


def return_time_active_contracts(session, status='active', activity='contract-time-trigger'):
    sql_query = text("SELECT t1.* FROM time_actions t1 JOIN (SELECT contractName, contractAddress, MAX(id) AS max_id FROM time_actions GROUP BY contractName, contractAddress) t2 ON t1.contractName = t2.contractName AND t1.contractAddress = t2.contractAddress AND t1.id = t2.max_id WHERE t1.status = :status AND t1.activity = :activity")
    try:
        active_contracts = session.execute(sql_query, {'status': status, 'activity': activity}).fetchall()
        return active_contracts
    except SQLAlchemyError as e:
        logger.error(f"[return_time_active_contracts] Database error while executing query: {e}")
        return []


def return_time_active_deposits(session):
    try:
        subquery_filter = session.query(TimeActions.id).group_by(TimeActions.transactionHash, TimeActions.id).having(func.count(TimeActions.transactionHash) == 1).subquery()
        active_deposits = session.query(TimeActions).filter(TimeActions.id.in_(subquery_filter.select()), TimeActions.status == 'active', TimeActions.activity == 'contract-deposit').all()
        return active_deposits
    except SQLAlchemyError as e:
        logger.error(f"[return_time_active_deposits] Database error while querying active deposits: {e}")
        return []



def process_contract_time_trigger(blockinfo, systemdb_session, active_contracts):
    for query in active_contracts:
        try:
            query_time = convert_datetime_to_arrowobject(query.time)
            blocktime = parsing.arrow.get(blockinfo['time']).to('Asia/Kolkata')
            if query.activity == 'contract-time-trigger':
                contractStructure = extract_contractStructure(query.contractName, query.contractAddress)
                if contractStructure is None:
                    logger.error(f"Contract structure missing for {query.contractName} at {query.contractAddress}")
                    continue
                contract_type = contractStructure.get('contractType')
                if not contract_type:
                    logger.error(f"Missing contractType in contractStructure for {query.contractName} at {query.contractAddress}")
                    continue
                try:
                    connection = create_database_connection('smart_contract', {'contract_name': query.contractName, 'contract_address': query.contractAddress})
                except Exception as e:
                    logger.error(f"Failed to connect to smart_contract DB for {query.contractName}_{query.contractAddress}: {e}")
                    continue
                if contract_type == 'one-time-event':
                    tx_type = 'trigger'
                    data = [blockinfo['hash'], blockinfo['height'], blockinfo['time'], blockinfo['size'], tx_type]
                    def _get_txid(data):
                        try:
                            if not isinstance(data, (bytes, bytearray)):
                                data = json.dumps(data, sort_keys=True).encode('utf-8')
                            return hashlib.sha256(data).hexdigest()
                        except Exception as e:
                            logger.error(f"Failed to generate SHA256 hash: {e}")
                            raise e
                    transaction_data = {'txid': _get_txid(data), 'blockheight': blockinfo['height'], 'time': blockinfo['time']}
                    parsed_data = {'type': tx_type, 'contractName': query.contractName, 'contractAddress': query.contractAddress}
                    try:
                        activecontracts_table_info = systemdb_session.query(ActiveContracts.blockHash, ActiveContracts.incorporationDate).filter(ActiveContracts.contractName == query.contractName, ActiveContracts.contractAddress == query.contractAddress, ActiveContracts.status == 'active').first()
                    except SQLAlchemyError as e:
                        logger.error(f"DB error while querying ActiveContracts for {query.contractName}: {e}")
                        continue
                    if activecontracts_table_info is None:
                        logger.error(f"No active contract found for {query.contractName} at {query.contractAddress}")
                        continue
                    incorporationDate = activecontracts_table_info.incorporationDate
                    if 'exitconditions' in contractStructure:
                        if 'maximumsubscriptionamount' in contractStructure:
                            try:
                                rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
                            except Exception as e:
                                logger.error(f"Error reading contractparticipants for {query.contractName}: {e}")
                                continue
                            tokenAmount_sum = float(sum(Decimal(f"{row[0]}") for row in rows if row[0] is not None))
                            maximumsubscriptionamount = float(contractStructure['maximumsubscriptionamount'])
                            if tokenAmount_sum >= maximumsubscriptionamount:
                                logger.info(f"Maximum Subscription amount {maximumsubscriptionamount} reached for {query.contractName}_{query.contractAddress}. Expiring the contract")
                                close_expire_contract(contractStructure, 'expired', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], incorporationDate, blockinfo['time'], None, query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                                continue
                        if blocktime > query_time:
                            if 'minimumsubscriptionamount' in contractStructure:
                                result = process_minimum_subscriptionamount(contractStructure, connection, blockinfo, transaction_data, parsed_data)
                                if result:
                                    logger.info(f"Contract trigger time {query_time} achieved and Minimum subscription amount reached for {query.contractName}_{query.contractAddress}. Closing the contract.")
                                    close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                                    continue
                            logger.info(f"Contract trigger time {query_time} achieved for {query.contractName}_{query.contractAddress}. Expiring the contract")
                            close_expire_contract(contractStructure, 'expired', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], incorporationDate, blockinfo['time'], None, query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                            continue
                    elif 'payeeAddress' in contractStructure:
                        if 'maximumsubscriptionamount' in contractStructure:
                            try:
                                rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
                            except Exception as e:
                                logger.error(f"Error reading contractparticipants for {query.contractName}: {e}")
                                continue
                            tokenAmount_sum = float(sum(Decimal(f"{row[0]}") for row in rows if row[0] is not None))
                            maximumsubscriptionamount = float(contractStructure['maximumsubscriptionamount'])
                            if tokenAmount_sum >= maximumsubscriptionamount:
                                logger.info(f"Triggering {query.contractName}_{query.contractAddress} as maximum subscription amount {maximumsubscriptionamount} has been reached")
                                success = trigger_internal_contract_onvalue(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name=query.contractName, contract_address=query.contractAddress, transaction_subType='maximumsubscriptionamount')
                                if not success:
                                    continue
                                logger.info(f"Closing {query.contractName}_{query.contractAddress} as maximum subscription amount {maximumsubscriptionamount} has been reached")
                                close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                                continue
                        if blocktime > query_time:
                            if 'minimumsubscriptionamount' in contractStructure:
                                result = process_minimum_subscriptionamount(contractStructure, connection, blockinfo, transaction_data, parsed_data)
                                if result:
                                    logger.info(f"Contract trigger time {query_time} achieved and Minimum subscription amount reached for {query.contractName}_{query.contractAddress}. Closing the contract.")
                                    close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                                    continue
                            try:
                                rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
                            except Exception as e:
                                logger.error(f"Error reading contractparticipants for {query.contractName}: {e}")
                                continue
                            tokenAmount_sum = float(sum(Decimal(f"{row[0]}") for row in rows if row[0] is not None))
                            logger.info(f"Triggering the contract {query.contractName}_{query.contractAddress}")
                            success = trigger_internal_contract_onvalue(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name=query.contractName, contract_address=query.contractAddress, transaction_subType='expiryTime')
                            if not success:
                                continue
                            logger.info(f"Closing the contract {query.contractName}_{query.contractAddress}")
                            close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                            continue
        except Exception as e:
            logger.error(f"Unexpected error processing contract {query.contractName}_{query.contractAddress}: {e}")
            continue

def process_contract_deposit_trigger(blockinfo, systemdb_session, active_deposits):
    for query in active_deposits:
        try:
            query_time = convert_datetime_to_arrowobject(query.time)
            blocktime = parsing.arrow.get(blockinfo['time']).to('Asia/Kolkata')
            if query.activity == 'contract-deposit' and blocktime > query_time:
                try:
                    contract_db = create_database_session_orm('smart_contract', {'contract_name': query.contractName, 'contract_address': query.contractAddress}, ContractBase)
                except Exception as e:
                    logger.error(f"Failed to connect to smart_contract DB for {query.contractName}_{query.contractAddress}: {e}")
                    continue
                try:
                    deposit_last_latest_entry = contract_db.query(ContractDeposits).filter(ContractDeposits.transactionHash == query.transactionHash).order_by(ContractDeposits.id.desc()).first()
                except SQLAlchemyError as e:
                    logger.error(f"DB error fetching deposits for {query.contractName}: {e}")
                    continue
                if deposit_last_latest_entry is None:
                    logger.error(f"No deposit entry found for transaction {query.transactionHash} in contract {query.contractName}_{query.contractAddress}")
                    continue
                returnAmount = deposit_last_latest_entry.depositBalance
                depositorAddress = deposit_last_latest_entry.depositorAddress
                try:
                    sellingToken_row = contract_db.query(ContractStructure.value).filter(ContractStructure.attribute == 'selling_token').first()
                except SQLAlchemyError as e:
                    logger.error(f"DB error fetching selling_token for {query.contractName}: {e}")
                    continue
                if sellingToken_row is None:
                    logger.error(f"Missing selling_token attribute in contract {query.contractName}_{query.contractAddress}")
                    continue
                sellingToken = sellingToken_row[0]
                tx_block_string = f"{query.transactionHash}{blockinfo['height']}".encode('utf-8').hex()
                parsed_data = {'type': 'smartContractDepositReturn', 'contractName': query.contractName, 'contractAddress': query.contractAddress}
                transaction_data = {'txid': query.transactionHash, 'blockheight': blockinfo['height'], 'time': blockinfo['time']}
                logger.info(f"Initiating smartContractDepositReturn after time expiry {query_time} for {depositorAddress} with amount {returnAmount} {sellingToken}# from {query.contractName}_{query.contractAddress} contract ")
                if float(returnAmount) <= 0:
                    logger.info(f"Skipping smartContractDepositReturn for transaction {query.transactionHash} because returnAmount is zero ({returnAmount}).")
                    try:
                        contract_db.add(ContractDeposits(depositorAddress=depositorAddress, depositAmount=-abs(returnAmount), depositBalance=0, expiryTime=deposit_last_latest_entry.expiryTime, unix_expiryTime=deposit_last_latest_entry.unix_expiryTime, status='deposit-return', transactionHash=deposit_last_latest_entry.transactionHash, blockNumber=blockinfo['height'], blockHash=blockinfo['hash']))
                        add_contract_transaction_history(contract_name=query.contractName, contract_address=query.contractAddress, transactionType='smartContractDepositReturn', transactionSubType=None, sourceFloAddress=query.contractAddress, destFloAddress=depositorAddress, transferAmount=0, transferToken=sellingToken,  blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=deposit_last_latest_entry.transactionHash, jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
                        systemdb_session.query(TimeActions).filter(TimeActions.transactionHash == query.transactionHash, TimeActions.contractName == query.contractName, TimeActions.contractAddress == query.contractAddress, TimeActions.activity == query.activity, TimeActions.status == 'active').update({TimeActions.status: 'returned', TimeActions.blockNumber: blockinfo['height']})
                        contract_db.commit()
                        systemdb_session.commit()
                        updateLatestTransaction(transaction_data, parsed_data, f"{query.contractName}_{query.contractAddress}")
                    except SQLAlchemyError as e:
                        logger.error(f"DB error saving deposit-return for {query.contractName}: {e}")
                        continue
                    continue
                try:
                    returnval = transferToken(sellingToken, returnAmount, query.contractAddress, depositorAddress, transaction_data=transaction_data, parsed_data=parsed_data, blockinfo=blockinfo)
                except Exception as e:
                    logger.error(f"Exception in transferToken for {query.contractName}: {e}")
                    continue
                if returnval == 0:
                    logger.critical(f"Something went wrong in the token transfer method while returning contract deposit. THIS IS A CRITICAL ERROR for transaction {query.transactionHash}")
                    return
                else:
                    try:
                        contract_db.add(ContractDeposits(depositorAddress=depositorAddress, depositAmount=-abs(returnAmount), depositBalance=0, expiryTime=deposit_last_latest_entry.expiryTime, unix_expiryTime=deposit_last_latest_entry.unix_expiryTime, status='deposit-return', transactionHash=deposit_last_latest_entry.transactionHash, blockNumber=blockinfo['height'], blockHash=blockinfo['hash']))
                        logger.info(f"✅ Successfully processed smartContractDepositReturn transaction ID {query.transactionHash} after time expiry {query_time} for {depositorAddress} with amount {returnAmount} {sellingToken}# from {query.contractName}_{query.contractAddress} contract ")
                        add_contract_transaction_history(contract_name=query.contractName, contract_address=query.contractAddress, transactionType='smartContractDepositReturn', transactionSubType=None, sourceFloAddress=query.contractAddress, destFloAddress=depositorAddress, transferAmount=returnAmount, transferToken=sellingToken, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=deposit_last_latest_entry.transactionHash, jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
                        systemdb_session.query(TimeActions).filter(TimeActions.transactionHash == query.transactionHash, TimeActions.contractName == query.contractName, TimeActions.contractAddress == query.contractAddress, TimeActions.activity == query.activity, TimeActions.status == 'active').update({TimeActions.status: 'returned', TimeActions.blockNumber: blockinfo['height']})
                        contract_db.commit()
                        systemdb_session.commit()
                        updateLatestTransaction(transaction_data, parsed_data, f"{query.contractName}_{query.contractAddress}")
                    except SQLAlchemyError as e:
                        logger.error(f"DB error saving post-transfer for {query.contractName}: {e}")
                        continue
        except Exception as e:
            logger.error(f"Unexpected error in process_contract_deposit_trigger for {query.contractName}_{query.contractAddress}: {e}")
            continue


def manage_expired_triggered_items(blockinfo):
    systemdb_session = None
    while True:
        try:
            systemdb_session = create_database_session_orm('system_dbs', {'db_name':'system'}, SystemBase)
            break
        except SQLAlchemyError as e:
            logger.error(f"Error connecting to system DB: {e}")
            time.sleep(DB_RETRY_TIMEOUT)
        except Exception as e:
            logger.error(f"Unexpected error during DB connection: {e}")
            time.sleep(DB_RETRY_TIMEOUT)

    try:
        active_contracts = []
        active_deposits = []

        try:
            active_contracts = return_time_active_contracts(systemdb_session)
        except SQLAlchemyError as e:
            logger.error(f"DB error fetching active contracts: {e}")
        except Exception as e:
            logger.error(f"Unexpected error fetching active contracts: {e}")

        try:
            active_deposits = return_time_active_deposits(systemdb_session)
        except SQLAlchemyError as e:
            logger.error(f"DB error fetching active deposits: {e}")
        except Exception as e:
            logger.error(f"Unexpected error fetching active deposits: {e}")

        if active_contracts:
            try:
                process_contract_time_trigger(blockinfo, systemdb_session, active_contracts)
            except Exception as e:
                logger.error(f"Error processing contract time triggers: {e}")

        if active_deposits:
            try:
                process_contract_deposit_trigger(blockinfo, systemdb_session, active_deposits)
            except Exception as e:
                logger.error(f"Error processing contract deposit triggers: {e}")

    finally:
        if systemdb_session:
            try:
                systemdb_session.close()
            except Exception as e:
                logger.warning(f"Error closing system DB session: {e}")



def check_reorg():
    connection = create_database_connection('system_dbs')
    BACK_TRACK_BLOCKS = 1000
    latest_block = connection.execute("SELECT max(blockNumber) FROM latestBlocks").fetchone()[0]
    block_number = latest_block
    if blockbook_type == "blockbook_legacy":
        api_path_template = f"{neturl}/api/v2/block-index/{{}}"
        expected_key = "blockHash"
    elif blockbook_type == "address_indexer":
        api_path_template = f"{neturl}/api/blockheight/{{}}"
        expected_key = "hash"
    else:
        logger.error(f"Unknown blockbook_type: {blockbook_type}")
        sys.exit(1)
    while block_number > 0:
        local_result = connection.execute(f"SELECT blockHash FROM latestBlocks WHERE blockNumber = {block_number}").fetchone()
        if not local_result:
            logger.error(f"No local block hash found for height {block_number}")
            break
        local_hash = local_result[0]
        try:
            api_url = api_path_template.format(block_number)
            logger.info(f"Querying Blockbook: {api_url}")
            response = requests.get(api_url, verify=API_VERIFY, timeout=RETRY_TIMEOUT_SHORT)
            response.raise_for_status()
            remote_data = response.json()
            remote_hash = remote_data.get(expected_key)
            if not remote_hash:
                raise KeyError(f"Missing expected key '{expected_key}' in API response")
        except Exception as e:
            logger.error(f"API error while checking block {block_number}: {e}")
            sys.exit(0)
        if remote_hash == local_hash:
            logger.info(f"Block {block_number} matches — no reorg at this level.")
            break
        else:
            logger.warning(f"Block hash mismatch at {block_number}! Backtracking...")
            block_number -= BACK_TRACK_BLOCKS
    connection.close()
    if block_number != latest_block:
        logger.warning(f"Reorg confirmed. Rolling back to block {block_number}")
        rollback_to_block(block_number)
    return block_number



    
def extract_contractStructure(contractName, contractAddress):
    connection = None
    while True:
        try:
            connection = create_database_connection('smart_contract', {'contract_name': f"{contractName}", 'contract_address': f"{contractAddress}"})
            break
        except Exception as e:
            logger.error(f"Failed to connect to smart_contract DB for {contractName}_{contractAddress}: {e}")
            time.sleep(DB_RETRY_TIMEOUT)
    try:
        try:
            attributevaluepair = connection.execute("SELECT attribute, value FROM contractstructure WHERE attribute != 'floData'").fetchall()
        except Exception as e:
            logger.error(f"Error executing SQL in extract_contractStructure for {contractName}_{contractAddress}: {e}")
            return {}
        contractStructure = {}
        conditionDict = {}
        counter = 0
        for item in attributevaluepair:
            attribute, value = item[0], item[1]
            if attribute == 'exitconditions':
                conditionDict[counter] = value
                counter += 1
            else:
                contractStructure[attribute] = value
        if conditionDict:
            contractStructure['exitconditions'] = conditionDict
        return contractStructure
    finally:
        if connection:
            try:
                connection.close()
            except Exception as e:
                logger.warning(f"Error closing connection for contract {contractName}_{contractAddress}: {e}")



def process_flo_checks(transaction_data):
    normalize_transaction_data(transaction_data)
    vinlist = []
    for vin in transaction_data.get("vin", []):
        addresses = vin.get("addresses", [])
        if not addresses:
            continue
        try:
            value = float(vin.get("value", 0))
        except (TypeError, ValueError):
            logger.info(f"Invalid value in vin for tx {transaction_data.get('txid')}. Transaction rejected.")
            return None, None, None
        vinlist.append([addresses[0], value])
    if not vinlist:
        logger.info(f"No valid vin addresses found in transaction {transaction_data.get('txid')}. Transaction rejected.")
        return None, None, None
    try:
        totalinputval = float(transaction_data.get("valueIn", 0))
    except (TypeError, ValueError):
        logger.info(f"Invalid valueIn in transaction {transaction_data.get('txid')}. Transaction rejected.")
        return None, None, None
    temp = vinlist[0][0]
    for item in vinlist[1:]:
        if item[0] != temp:
            logger.info(f"System has found more than one address as part of vin. Transaction {transaction_data.get('txid')} is rejected")
            return None, None, None
    inputlist = [temp, totalinputval]
    inputadd = temp

    if len(transaction_data.get("vout", [])) > 2:
        logger.info(f"System has found more than 2 addresses as part of vout. Transaction {transaction_data.get('txid')} is rejected")
        return None, None, None

    outputlist = []
    addresscounter = 0
    inputcounter = 0

    logger.info(f"vout content before processing: {transaction_data.get('vout', [])}")

    for obj in transaction_data.get("vout", []):
        logger.info(f"Processing vout object: {obj}")
        script = obj.get("scriptPubKey", {})
        addresses = script.get("addresses", [])
        logger.info(f"Extracted addresses: {addresses}")
        if not addresses:
            continue
        addresscounter += 1
        if addresses[0] == inputlist[0]:
            inputcounter += 1
            continue
        outputlist.append([addresses[0], obj.get("value", 0)])

    logger.info(f"addresscounter={addresscounter}, inputcounter={inputcounter}, outputlist={outputlist}")

    if addresscounter == inputcounter:
        outputlist = [inputlist[0]]
    elif len(outputlist) != 1:
        logger.info(f"Transaction's change is not coming back to the input address. Transaction {transaction_data.get('txid')} is rejected")
        return None, None, None
    else:
        outputlist = outputlist[0]

    return inputlist, outputlist, inputadd


def process_token_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    try:
        token_name = parsed_data.get('tokenIdentification')
        txid = transaction_data.get('txid')
        if not token_name or not txid:
            logger.error("Missing required fields in transaction or parsed data.")
            return 0
        if is_a_contract_address(inputlist[0]) or is_a_contract_address(outputlist[0]):
            rejectComment = f"Token transfer at transaction {txid} rejected because either the input or output address is a contract address."
            logger.info(rejectComment)
            rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
        if not check_database_existence('token', {'token_name': token_name}):
            rejectComment = f"Token transfer at transaction {txid} rejected because token {token_name} does not exist."
            logger.info(rejectComment)
            rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
        connection = None
        isInfiniteToken = False
        try:
            connection = create_database_connection('system_dbs', {'db_name': 'system'})
            result = connection.execute("SELECT db_name, db_type, keyword, object_format FROM databaseTypeMapping WHERE db_name = %s", (token_name,)).fetchall()
            if not result:
                logger.error(f"No database mapping found for token {token_name}.")
                return 0
            db_name, db_type, keyword, object_format = result[0]
            if db_type == 'infinite-token':
                db_object = json.loads(object_format)
                if db_object.get('root_address') == inputlist[0]:
                    isInfiniteToken = True
        except Exception as e:
            logger.error(f"Error fetching token DB details for {token_name}: {e}")
            return 0
        finally:
            if connection:
                connection.close()
        connection = None
        try:
            connection = create_database_connection('token', {'token_name': token_name})
            rows = connection.execute("SELECT blockNumber, transactionHash FROM transactionHistory").fetchall()
            blockno_txhash_T = list(zip(*rows)) if rows else ([], [])
            tx_hashes = blockno_txhash_T[1] if len(blockno_txhash_T) > 1 else []
            if txid in tx_hashes:
                logger.warning(f"Transaction {txid} already exists in the token db. This is unusual.")
                pushData_SSEapi(f"Error | Transaction {txid} already exists in the token db. Please check your code.")
                return 0
        except Exception as e:
            logger.error(f"Error checking transaction existence for {txid} in token DB: {e}")
            return 0
        finally:
            if connection:
                connection.close()
        try:
            returnval = transferToken(token_name, parsed_data.get('tokenAmount'), inputlist[0], outputlist[0], transaction_data, parsed_data, isInfiniteToken=isInfiniteToken, blockinfo=blockinfo)
            if returnval == 0:
                logger.info("Something went wrong in the token transfer method.")
                pushData_SSEapi(f"Error | Internal DB error during transfer for {txid}")
                return 0
            else:
                updateLatestTransaction(transaction_data, parsed_data, f"{token_name}", transactionType='token-transfer')
        except Exception as e:
            logger.error(f"Error performing token transfer for {txid}: {e}")
            pushData_SSEapi(f"Error | Exception during token transfer for {txid}")
            return 0
        return 1
    except Exception as e:
        logger.error(f"Unexpected error in process_token_transfer: {e}")
        return 0

def process_one_time_event_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd, connection, session, contractStructure):
    logger.info(f"Processing one-time event transfer for transaction {transaction_data.get('txid')}")
    try:
        participant_rows = connection.execute(text("SELECT participantAddress, transactionHash FROM contractparticipants")).fetchall()
        if participant_rows:
            txids = [row[1] for row in participant_rows]
            if transaction_data["txid"] in txids:
                msg = f"Transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db."
                logger.warning(msg)
                pushData_SSEapi(f"Error | {msg}")
                return 0
        if "contractAddress" in parsed_data and parsed_data["contractAddress"] != outputlist[0]:
            rejectComment = f"Contract participation at transaction {transaction_data['txid']} rejected as contractAddress specified in floData ({parsed_data['contractAddress']}) does not match transaction's output address {outputlist[0]}"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(f"Error | {rejectComment}")
            return 0
        contractStatus = check_contract_status(parsed_data.get("contractName", ""), outputlist[0])
        if contractStatus == "closed":
            rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart contract {parsed_data.get('contractName')} at the {outputlist[0]} is closed."
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
            return 0
        expirytime_result = session.query(ContractStructure).filter_by(attribute="expiryTime").all()
        session.close()
        if expirytime_result:
            expirytime = expirytime_result[0].value.strip()
            expiry_split = expirytime.split(" ")
            parse_string = "{}/{}/{} {}".format(expiry_split[3], parsing.months[expiry_split[1]], expiry_split[2], expiry_split[4])
            expiry_obj = parsing.arrow.get(parse_string, "YYYY/M/D HH:mm:ss").replace(tzinfo=expiry_split[5][3:])
            blocktime_obj = parsing.arrow.get(transaction_data["time"]).to("Asia/Kolkata")
            if blocktime_obj > expiry_obj:
                rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart contract {parsed_data['contractName']}_{outputlist[0]} has expired."
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                pushData_SSEapi(rejectComment)
                return 0
        if "userChoice" in parsed_data and "exitconditions" not in contractStructure:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as userChoice {parsed_data['userChoice']} is not valid for Smart Contract {parsed_data['contractName']} at address {outputlist[0]}"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
        if parsed_data.get("tokenIdentification") != contractStructure.get("tokenIdentification"):
            rejectComment = f"Transaction {transaction_data['txid']} rejected because token {parsed_data['tokenIdentification']} does not match Smart Contract {parsed_data['contractName']} at {outputlist[0]}"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
        if "contractAmount" in contractStructure:
            try:
                contract_amount = float(contractStructure["contractAmount"])
                token_amount = float(parsed_data.get("tokenAmount", 0))
                if contract_amount != token_amount:
                    rejectComment = f"Transaction {transaction_data['txid']} rejected as transferred amount ({token_amount}) does not match contractAmount ({contract_amount})."
                    logger.info(rejectComment)
                    rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                    pushData_SSEapi(rejectComment)
                    return 0
            except Exception as e:
                logger.error(f"Error parsing contractAmount check: {e}")
                return 0
        partialTransferCounter = 0
        maximumsubscriptionamount = None
        amountDeposited = Decimal(0)
        if "maximumsubscriptionamount" in contractStructure:
            try:
                maximumsubscriptionamount = float(contractStructure["maximumsubscriptionamount"])
                session = create_database_session_orm('smart_contract', {'contract_name': parsed_data.get('contractName', ''), 'contract_address': outputlist[0]}, ContractBase)
                query_data = session.query(ContractParticipants.tokenAmount).all()
                amountDeposited = sum(Decimal(str(amount[0])) if amount[0] is not None else Decimal(0) for amount in query_data)
                session.close()
            except Exception as e:
                logger.error(f"Error fetching participants for max subscription: {e}")
                return 0
            if amountDeposited >= maximumsubscriptionamount:
                rejectComment = f"Transaction {transaction_data['txid']} rejected because max subscription amount has been reached for Smart Contract {parsed_data.get('contractName')} at {outputlist[0]}"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                pushData_SSEapi(rejectComment)
                return 0
            elif perform_decimal_operation("addition", float(amountDeposited), float(parsed_data.get("tokenAmount", 0))) > maximumsubscriptionamount:
                if "contractAmount" in contractStructure:
                    rejectComment = f"Transaction {transaction_data['txid']} rejected as contractAmount surpasses maximum subscription amount {maximumsubscriptionamount}."
                    logger.info(rejectComment)
                    rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                    pushData_SSEapi(rejectComment)
                    return 0
                else:
                    partialTransferCounter = 1
                    rejectComment = f"Transaction {transaction_data['txid']} rejected as partial transfer of token {contractStructure.get('tokenIdentification')} is not allowed for Smart Contract {parsed_data.get('contractName')} at {outputlist[0]}"
                    logger.info(rejectComment)
                    rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                    pushData_SSEapi(rejectComment)
                    return 0
        if "exitconditions" in contractStructure:
            exitconditionsList = [contractStructure["exitconditions"][key] for key in contractStructure["exitconditions"]]
            if parsed_data["userChoice"] in exitconditionsList:
                transfer_result = transferToken(parsed_data["tokenIdentification"], parsed_data["tokenAmount"], inputlist[0], outputlist[0], transaction_data, parsed_data, blockinfo=blockinfo)
                if transfer_result != 0:
                    session.add(ContractParticipants(participantAddress=inputadd, tokenAmount=parsed_data["tokenAmount"], userChoice=parsed_data["userChoice"], transactionHash=transaction_data["txid"], blockNumber=transaction_data["blockheight"], blockHash=transaction_data["blockhash"]))
                    session.commit()
                    add_contract_transaction_history(contract_name=parsed_data["contractName"], contract_address=outputlist[0], transactionType="participation", transactionSubType=None, sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data["tokenAmount"], blockNumber=blockinfo["height"], blockHash=blockinfo["hash"], blocktime=blockinfo["time"], transactionHash=transaction_data["txid"], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
                    system_session = create_database_session_orm("system_dbs", {"db_name": "system"}, SystemBase)
                    system_session.add(ContractAddressMapping(address=inputadd, addressType="participant", tokenAmount=parsed_data["tokenAmount"], contractName=parsed_data["contractName"], contractAddress=outputlist[0], transactionHash=transaction_data["txid"], blockNumber=transaction_data["blockheight"], blockHash=transaction_data["blockhash"]))
                    system_session.commit()
                    system_session.close()
                    connection2 = create_database_connection("system_dbs", {"db_name": "system"})
                    rows = connection2.execute(text("SELECT * FROM tokenAddressMapping WHERE tokenAddress=:tokenAddress AND token=:token"), {"tokenAddress": outputlist[0], "token": parsed_data["tokenIdentification"]}).fetchall()
                    if not rows:
                        connection2.execute(text("INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES (:tokenAddress, :token, :transactionHash, :blockNumber, :blockHash)"), {"tokenAddress": outputlist[0], "token": parsed_data["tokenIdentification"], "transactionHash": transaction_data["txid"], "blockNumber": transaction_data["blockheight"], "blockHash": transaction_data["blockhash"]})
                    connection2.close()
                    updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}_{outputlist[0]}", transactionType="ote-externaltrigger-participation")
                    return 1
                else:
                    logger.info("Token transfer failed.")
                    return 0
            else:
                rejectComment = f"Transaction {transaction_data['txid']} rejected due to invalid userChoice for Smart Contract {parsed_data['contractName']} at {outputlist[0]}"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                pushData_SSEapi(rejectComment)
                return 0
        elif "payeeAddress" in contractStructure:
            transferAmount = parsed_data["tokenAmount"] if partialTransferCounter == 0 else perform_decimal_operation("subtraction", maximumsubscriptionamount, float(amountDeposited))
            transfer_result = transferToken(parsed_data["tokenIdentification"], transferAmount, inputlist[0], outputlist[0], transaction_data, parsed_data, blockinfo=blockinfo)
            if transfer_result != 0:
                session.add(ContractParticipants(participantAddress=inputadd, tokenAmount=transferAmount, userChoice="-", transactionHash=transaction_data["txid"], blockNumber=transaction_data["blockheight"], blockHash=transaction_data["blockhash"]))
                session.commit()
                session.close()
                add_contract_transaction_history(contract_name=parsed_data["contractName"], contract_address=outputlist[0], transactionType="participation", transactionSubType=None, sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=transferAmount, blockNumber=blockinfo["height"], blockHash=blockinfo["hash"], blocktime=blockinfo["time"], transactionHash=transaction_data["txid"], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
                system_session = create_database_session_orm("system_dbs", {"db_name": "system"}, SystemBase)
                system_session.add(ContractAddressMapping(address=inputadd, addressType="participant", tokenAmount=transferAmount, contractName=parsed_data["contractName"], contractAddress=outputlist[0], transactionHash=transaction_data["txid"], blockNumber=transaction_data["blockheight"], blockHash=transaction_data["blockhash"]))
                system_session.commit()
                system_session.close()
                connection2 = create_database_connection("system_dbs", {"db_name": "system"})
                rows = connection2.execute(text("SELECT * FROM tokenAddressMapping WHERE tokenAddress=:tokenAddress AND token=:token"), {"tokenAddress": outputlist[0], "token": parsed_data["tokenIdentification"]}).fetchall()
                if not rows:
                    connection2.execute(text("INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES (:tokenAddress, :token, :transactionHash, :blockNumber, :blockHash)"), {"tokenAddress": outputlist[0], "token": parsed_data["tokenIdentification"], "transactionHash": transaction_data["txid"], "blockNumber": transaction_data["blockheight"], "blockHash": transaction_data["blockhash"]})
                connection2.close()
                updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}_{outputlist[0]}", transactionType="ote-internaltrigger-participation")
                return 1
            else:
                logger.info("Token transfer failed in internal trigger contract.")
                return 0
        return 1
    except Exception as e:
        logger.error(f"Unhandled error in process_one_time_event_transfer: {e}")
        return 0

def process_continuous_event_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd, connection, contract_session, contractStructure):
    try:
        logger.info(f"Processing continuous event transfer for transaction {transaction_data['txid']}")
        inputlist_amount = Decimal(str(inputlist[1])) if len(inputlist) > 1 else Decimal('0')
        outputlist_amount = Decimal(str(outputlist[1])) if len(outputlist) > 1 else Decimal('0')
        contract_subtype = contract_session.query(ContractStructure.value).filter(ContractStructure.attribute == 'subtype').first()
        contract_subtype = contract_subtype[0] if contract_subtype else None
        if not contract_subtype:
            logger.error("Contract subtype not found in contract structure.")
            return 0
        if contract_subtype == 'tokenswap':
            original_accepting_amount = Decimal(parsed_data['tokenAmount'])
            participantAdd_txhash = connection.execute(text('SELECT participantAddress, transactionHash FROM contractparticipants')).fetchall()
            participantAdd_txhash_T = list(zip(*participantAdd_txhash))
            if participantAdd_txhash_T and transaction_data['txid'] in list(participantAdd_txhash_T[1]):
                logger.warning(f"Transaction {transaction_data['txid']} rejected as it already exists in contract db.")
                pushData_SSEapi(f"Error | Transaction {transaction_data['txid']} rejected as it already exists.")
                return 0
            if 'contractAddress' in parsed_data:
                if parsed_data['contractAddress'] != outputlist[0]:
                    rejectComment = f"Contract participation at transaction {transaction_data['txid']} rejected as contractAddress specified in floData {parsed_data['contractAddress']} doesn't match output address {outputlist[0]}"
                    logger.info(rejectComment)
                    rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                    pushData_SSEapi(f"Error | Mismatch in contract address for transaction {transaction_data['txid']}")
                    return 0
            pricetype = contractStructure.get('pricetype', '').strip().lower()
            if pricetype in ['predetermined', 'determined']:
                swapPrice = Decimal(contractStructure.get('price') or '0')
            elif pricetype == 'dynamic':
                if transaction_data['senderAddress'] == contractStructure['oracle_address']:
                    logger.warning(f"Transaction {transaction_data['txid']} rejected: oracle address {contractStructure['oracle_address']} trying to participate.")
                    pushData_SSEapi(f"Transaction {transaction_data['txid']} rejected: oracle address trying to participate.")
                    return 0
                swapPrice = Decimal(fetchDynamicSwapPrice(contractStructure, blockinfo))
                update_dynamic_swap_price(session=contract_session, price=swapPrice, price_timestamp=blockinfo['time'], blockheight=blockinfo['height'])
            else:
                logger.error(f"Unknown pricetype {pricetype}")
                return 0
            swapAmount = perform_decimal_operation('division', Decimal(parsed_data['tokenAmount']), swapPrice)
            logger.info(f"Calculated swapAmount = {swapAmount}")
            subquery = contract_session.query(func.max(ContractDeposits.id)).group_by(ContractDeposits.transactionHash)
            active_contract_deposits = contract_session.query(ContractDeposits).filter(ContractDeposits.id.in_(subquery), ContractDeposits.status.notin_(['deposit-return', 'consumed']), ContractDeposits.status == 'active').order_by(ContractDeposits.id.asc()).all()
            query_data = contract_session.query(ContractDeposits.depositBalance).filter(ContractDeposits.id.in_(subquery), ContractDeposits.status.notin_(['deposit-return']), ContractDeposits.status == 'active').all()
            available_deposit_sum = sum(Decimal(str(amount[0])) if amount[0] is not None else Decimal(0) for amount in query_data)
            logger.info(f"Available deposit sum = {available_deposit_sum}")
            if available_deposit_sum < Decimal('0.00000001'):
                rejectComment = f"No selling token is deposited in contract. Rejecting tx {transaction_data['txid']}"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, parsed_data["type"], contractStructure["contractAddress"], inputlist[0], outputlist[0], rejectComment)
                pushData_SSEapi(rejectComment)
                return 0
            fulfilled_swapAmount = min(swapAmount, available_deposit_sum)
            usd_required = perform_decimal_operation('multiplication', fulfilled_swapAmount, swapPrice)
            refund_usd_amount = perform_decimal_operation('subtraction', Decimal(parsed_data['tokenAmount']), usd_required) if swapAmount > available_deposit_sum else Decimal(0)
            logger.info(f"Will fulfill {fulfilled_swapAmount} selling tokens for {usd_required} accepting tokens. Refund required: {refund_usd_amount}")
            returnval = transferToken(parsed_data['tokenIdentification'], Decimal(parsed_data['tokenAmount']), inputlist[0], outputlist[0], transaction_data=transaction_data, parsed_data=parsed_data, isInfiniteToken=None, blockinfo=blockinfo, transactionType='tokenswapParticipation')
            if returnval == 0:
                logger.info("ERROR | Failed transferring accepting token during participation.")
                return 0
            if refund_usd_amount > Decimal(0):
                logger.info(f"Refunding {refund_usd_amount} {parsed_data['tokenIdentification']}# to participant {inputlist[0]}")
                returnval = transferToken(parsed_data['tokenIdentification'], refund_usd_amount, outputlist[0], inputlist[0], transaction_data=transaction_data, parsed_data=parsed_data, isInfiniteToken=None, blockinfo=blockinfo, transactionType='tokenswapRefund')
                if returnval == 0:
                    logger.critical("CRITICAL ERROR | Failed to refund participant. SYSTEM INCONSISTENT.")
                    return 0
                add_contract_transaction_history(
                    contract_name=parsed_data['contractName'],
                    contract_address=outputlist[0],
                    transactionType='tokenswapRefund',
                    transactionSubType=None,
                    sourceFloAddress=outputlist[0],
                    destFloAddress=inputlist[0],
                    transferAmount=refund_usd_amount,
                    transferToken=contractStructure['accepting_token'],
                    blockNumber=blockinfo['height'],
                    blockHash=blockinfo['hash'],
                    blocktime=blockinfo['time'],
                    transactionHash=transaction_data['txid'],
                    jsonData=json.dumps(transaction_data),
                    parsedFloData=json.dumps(parsed_data)
                )
            remaining_swapAmount = fulfilled_swapAmount
            for a_deposit in active_contract_deposits:
                if remaining_swapAmount <= Decimal('0'):
                    break
                deposit_balance_decimal = Decimal(str(a_deposit.depositBalance or '0'))
                used_amount = min(deposit_balance_decimal, remaining_swapAmount)
                usd_for_depositor = perform_decimal_operation('multiplication', used_amount, swapPrice)
                returnval = transferToken(contractStructure['accepting_token'], usd_for_depositor, contractStructure['contractAddress'], a_deposit.depositorAddress, transaction_data=transaction_data, parsed_data=parsed_data, isInfiniteToken=None, blockinfo=blockinfo, transactionType='tokenswapDepositSettlement')
                if returnval == 0:
                    logger.critical("CRITICAL ERROR | Failed deposit settlement.")
                    return 0
                add_contract_transaction_history(
                    contract_name=parsed_data['contractName'],
                    contract_address=contractStructure['contractAddress'],
                    transactionType='tokenswapDepositSettlement',
                    transactionSubType=None,
                    sourceFloAddress=contractStructure['contractAddress'],
                    destFloAddress=a_deposit.depositorAddress,
                    transferAmount=usd_for_depositor,
                    transferToken=contractStructure['accepting_token'],
                    blockNumber=blockinfo['height'],
                    blockHash=blockinfo['hash'],
                    blocktime=blockinfo['time'],
                    transactionHash=transaction_data['txid'],
                    jsonData=json.dumps(transaction_data),
                    parsedFloData=json.dumps(parsed_data)
                )
                new_deposit_balance = perform_decimal_operation('subtraction', deposit_balance_decimal, used_amount)
                if new_deposit_balance == Decimal('0'):
                    systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
                    try:
                        systemdb_session.query(TimeActions).filter(TimeActions.transactionHash == a_deposit.transactionHash, TimeActions.contractName == contractStructure['contractName'], TimeActions.contractAddress == contractStructure['contractAddress'], TimeActions.activity == 'contract-deposit', TimeActions.status == 'active').update({TimeActions.status: 'consumed', TimeActions.blockNumber: blockinfo['height']})
                        systemdb_session.commit()
                        logger.info(f"Marked TimeAction as consumed for fully consumed deposit {a_deposit.transactionHash}")
                    finally:
                        systemdb_session.close()
                contract_session.add(ContractDeposits(depositorAddress=a_deposit.depositorAddress, depositAmount=-used_amount, depositBalance=new_deposit_balance, expiryTime=a_deposit.expiryTime, unix_expiryTime=a_deposit.unix_expiryTime, status='consumed' if new_deposit_balance == Decimal('0') else 'active', transactionHash=a_deposit.transactionHash, blockNumber=blockinfo['height'], blockHash=blockinfo['hash']))
                remaining_swapAmount = perform_decimal_operation('subtraction', remaining_swapAmount, used_amount)
            returnval = transferToken(contractStructure['selling_token'], fulfilled_swapAmount, outputlist[0], inputlist[0], transaction_data=transaction_data, parsed_data=parsed_data, isInfiniteToken=None, blockinfo=blockinfo, transactionType='tokenswapParticipationSettlement')
            if returnval == 0:
                logger.info("CRITICAL ERROR | Failed transfer of selling token to participant.")
                return 0
            add_contract_transaction_history(
                contract_name=parsed_data['contractName'],
                contract_address=outputlist[0],
                transactionType='tokenswapParticipationSettlement',
                transactionSubType=None,
                sourceFloAddress=outputlist[0],
                destFloAddress=inputlist[0],
                transferAmount=fulfilled_swapAmount,
                transferToken=contractStructure['selling_token'],
                blockNumber=blockinfo['height'],
                blockHash=blockinfo['hash'],
                blocktime=blockinfo['time'],
                transactionHash=transaction_data['txid'],
                jsonData=json.dumps(transaction_data),
                parsedFloData=json.dumps(parsed_data)
            )
            contract_session.add(ContractParticipants(participantAddress=transaction_data['senderAddress'], tokenAmount=Decimal(parsed_data['tokenAmount']), userChoice=swapPrice, transactionHash=transaction_data['txid'], blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], winningAmount=fulfilled_swapAmount))
            add_contract_transaction_history(contract_name=parsed_data['contractName'], contract_address=outputlist[0], transactionType='participation', transactionSubType='swap', sourceFloAddress=inputlist[0], destFloAddress=outputlist[0], transferAmount=original_accepting_amount,transferToken=contractStructure['accepting_token'], blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
            contract_session.commit()
            systemdb_connection = create_database_connection('system_dbs', {'db_name': 'system'})
            try:
                firstInteractionCheck = systemdb_connection.execute(text("SELECT * FROM tokenAddressMapping WHERE tokenAddress = :tokenAddress AND token = :token"), {"tokenAddress": inputlist[0], "token": contractStructure["selling_token"]}).fetchall()
                if len(firstInteractionCheck) == 0:
                    systemdb_connection.execute(text("INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES (:tokenAddress, :token, :transactionHash, :blockNumber, :blockHash)"), {"tokenAddress": inputlist[0], "token": contractStructure["selling_token"], "transactionHash": transaction_data["txid"], "blockNumber": transaction_data["blockheight"], "blockHash": transaction_data["blockhash"]})
            finally:
                systemdb_connection.close()
            updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}_{outputlist[0]}", transactionType='tokenswapParticipation')
            pushData_SSEapi(f"Token swap successfully performed at contract {parsed_data['contractName']}_{outputlist[0]} with transaction {transaction_data['txid']}")
            logger.info(f"✅ Swap completed for transaction {transaction_data['txid']}")
            return 1
        logger.info(f"Completed processing for transaction {transaction_data['txid']}")
        return 1
    except Exception as e:
        logger.error(f"Exception in process_continuous_event_transfer: {e}")
        logger.error(traceback.format_exc())
        return 0



def process_smart_contract_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    try:
        contract_name = parsed_data.get('contractName')
        contract_address = outputlist[0] if outputlist else None
        if not contract_name or not contract_address:
            rejectComment = f"Smart contract transfer at transaction {transaction_data['txid']} rejected due to missing contractName or contractAddress"
            logger.info(rejectComment)
            rejected_transaction_history(transaction_data, parsed_data, inputlist[0], contract_address or '-', rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
        if check_database_existence('smart_contract', {'contract_name': contract_name, 'contract_address': contract_address}):
            connection = None
            contract_session = None
            try:
                connection = create_database_connection('smart_contract', {'contract_name': contract_name, 'contract_address': contract_address})
                contract_session = create_database_session_orm('smart_contract', {'contract_name': contract_name, 'contract_address': contract_address}, ContractBase)
                contractStructure = extract_contractStructure(contract_name, contract_address)
                result = contract_session.query(ContractStructure.value).filter(ContractStructure.attribute == 'contractType').first()
                contract_type = result[0] if result else None
                if not contract_type:
                    rejectComment = f"Smart contract transfer at transaction {transaction_data['txid']} rejected due to missing contract type in contract structure"
                    logger.info(rejectComment)
                    rejected_transaction_history(transaction_data, parsed_data, inputlist[0], contract_address, rejectComment)
                    pushData_SSEapi(rejectComment)
                    return 0
                if contract_type == 'one-time-event':
                    return process_one_time_event_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd, connection, contract_session, contractStructure)
                elif contract_type == 'continuous-event':
                    return process_continuous_event_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd, connection, contract_session, contractStructure)
                else:
                    rejectComment = f"Smart contract transfer at transaction {transaction_data['txid']} rejected due to unknown contract type '{contract_type}'"
                    logger.info(rejectComment)
                    rejected_transaction_history(transaction_data, parsed_data, inputlist[0], contract_address, rejectComment)
                    pushData_SSEapi(rejectComment)
                    return 0
            except Exception as e:
                logger.error(f"Exception while processing smart contract transfer: {e}")
                logger.error(traceback.format_exc())
                rejectComment = f"Smart contract transfer at transaction {transaction_data['txid']} rejected due to DB error"
                rejected_transaction_history(transaction_data, parsed_data, inputlist[0], contract_address, rejectComment)
                pushData_SSEapi(rejectComment)
                return 0
            finally:
                if contract_session:
                    contract_session.close()
                if connection:
                    connection.close()
        else:
            rejectComment = f"Smart contract transfer at transaction {transaction_data['txid']} rejected as the smart contract does not exist"
            logger.info(rejectComment)
            rejected_transaction_history(transaction_data, parsed_data, inputlist[0], contract_address, rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
    except Exception as e:
        logger.error(f"Exception in process_smart_contract_transfer: {e}")
        logger.error(traceback.format_exc())
        rejectComment = f"Smart contract transfer at transaction {transaction_data['txid']} failed due to system error"
        rejected_transaction_history(transaction_data, parsed_data, inputlist[0], outputlist[0] if outputlist else '-', rejectComment)
        pushData_SSEapi(rejectComment)
        return 0

def process_nft_transfer(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd):
    if not is_a_contract_address(inputlist[0]) and not is_a_contract_address(outputlist[0]):
        # check if the token exists in the database
        if check_database_existence('token', {'token_name':f"{parsed_data['tokenIdentification']}"}):
            # Pull details of the token type from system.db database 
            connection = create_database_connection('system_dbs', {'db_name':'system'})
            db_details = connection.execute("SELECT db_name, db_type, keyword, object_format FROM databaseTypeMapping WHERE db_name='{}'".format(parsed_data['tokenIdentification']))
            db_details = list(zip(*db_details))
            if db_details[1][0] == 'infinite-token':
                db_object = json.loads(db_details[3][0])
                if db_object['root_address'] == inputlist[0]:
                    isInfiniteToken = True
                else:
                    isInfiniteToken = False
            else:
                isInfiniteToken = False

            # Check if the transaction hash already exists in the token db
            connection = create_database_connection('token', {'token_name':f"{parsed_data['tokenIdentification']}"})
            blockno_txhash = connection.execute('SELECT blockNumber, transactionHash FROM transactionHistory').fetchall()
            connection.close()
            blockno_txhash_T = list(zip(*blockno_txhash))

            if transaction_data['txid'] in list(blockno_txhash_T[1]):
                logger.warning(f"Transaction {transaction_data['txid']} already exists in the token db. This is unusual, please check your code")
                pushData_SSEapi(f"Error | Transaction {transaction_data['txid']} already exists in the token db. This is unusual, please check your code")
                return 0
            
            returnval = transferToken(parsed_data['tokenIdentification'], parsed_data['tokenAmount'], inputlist[0],outputlist[0], transaction_data, parsed_data, isInfiniteToken=isInfiniteToken, blockinfo = blockinfo)
            if returnval == 0:
                logger.info("Something went wrong in the token transfer method")
                pushData_SSEapi(f"Error | Something went wrong while doing the internal db transactions for {transaction_data['txid']}")
                return 0
            else:
                updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['tokenIdentification']}", transactionType='token-transfer')

            # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
            connection = create_database_connection('system_dbs', {'db_name':'system'})
            firstInteractionCheck = connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{outputlist[0]}' AND token='{parsed_data['tokenIdentification']}'").fetchall()

            if len(firstInteractionCheck) == 0:
                connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{outputlist[0]}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")

            connection.close()

            # Pass information to SSE channel
            headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
            # r = requests.post(tokenapi_sse_url, json={f"message': 'Token Transfer | name:{parsed_data['tokenIdentification']} | transactionHash:{transaction_data['txid']}"}, headers=headers)
            return 1
        else:
            rejectComment = f"Token transfer at transaction {transaction_data['txid']} rejected as a token with the name {parsed_data['tokenIdentification']} doesnt not exist"
            logger.info(rejectComment)                    
            rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
    
    else:
        rejectComment = f"Token transfer at transaction {transaction_data['txid']} rejected as either the input address or the output address is part of a contract address"
        logger.info(rejectComment)
        rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
        pushData_SSEapi(rejectComment)
        return 0


# todo Rule 47 - If the parsed data type is token incorporation, then check if the name hasn't been taken already
#  if it has been taken then reject the incorporation. Else incorporate it
def process_token_incorporation(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    logger.info("Processing token incorporation...")
    if is_a_contract_address(inputlist[0]):
        rejectComment = f"Token incorporation at transaction {transaction_data['txid']} rejected as either the input address is part of a contract address"
        logger.info(rejectComment); rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
    if check_database_existence('token', {'token_name': f"{parsed_data['tokenIdentification']}"}):
        rejectComment = f"Token incorporation rejected at transaction {transaction_data['txid']} as token {parsed_data['tokenIdentification']} already exists"
        logger.info(rejectComment); rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
    try:
        session = create_database_session_orm('token', {'token_name': f"{parsed_data['tokenIdentification']}"}, TokenBase)
        session.add(ActiveTable(address=inputlist[0], parentid=0, transferBalance=parsed_data['tokenAmount'], addressBalance=parsed_data['tokenAmount'], blockNumber=blockinfo['height']))
        session.add(TransferLogs(sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], sourceId=0, destinationId=1, blockNumber=transaction_data['blockheight'], time=transaction_data['time'], transactionHash=transaction_data['txid']))
        add_transaction_history(token_name=parsed_data['tokenIdentification'], sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash'], blocktime=transaction_data['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=parsed_data['type'], parsedFloData=json.dumps(parsed_data))
        session.commit(); session.close()
    except Exception as e:
        logger.error(f"Error during token incorporation DB insert: {e}", exc_info=True); return 0
    try:
        connection = create_database_connection('system_dbs', {'db_name': 'system'})
        connection.execute("""INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES (%s, %s, %s, %s, %s)""", (inputadd, parsed_data['tokenIdentification'], transaction_data['txid'], transaction_data['blockheight'], transaction_data['blockhash']))
        connection.execute("""INSERT INTO databaseTypeMapping (db_name, db_type, keyword, object_format, blockNumber) VALUES (%s, %s, %s, %s, %s)""", (parsed_data['tokenIdentification'], 'token', '', '', transaction_data['blockheight']))
    except Exception as e:
        logger.error(f"Error during token incorporation system DB insert: {e}", exc_info=True); return 0
    finally:
        connection.close()
    updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['tokenIdentification']}")
    logger.info(f"Token | Successfully incorporated token {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
    pushData_SSEapi(f"Token | Successfully incorporated token {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
    return 1

#   Rule 48 - If the parsed data type if smart contract incorporation, then check if the name hasn't been taken already
#      if it has been taken then reject the incorporation.
def process_smart_contract_incorporation(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    logger.info(f"Processing smart contract incorporation for transaction {transaction_data['txid']}")
    
    # Normalize typo
    if parsed_data.get("contractType") in ["continuous-event", "continuos-event"]: parsed_data["contractType"] = "continuous-event"

    # EARLY REJECTION if input address mismatch
    if parsed_data['contractAddress'] != inputadd:
        rejectComment = f"Contract Incorporation on transaction {transaction_data['txid']} rejected as contract address in Flodata and input address are different"
        logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, outputlist[0] if outputlist else None, rejectComment)
        pushData_SSEapi(f"Error | Contract Incorporation rejected as address in Flodata and input address are different at transaction {transaction_data['txid']}")
        return 0

    # Check if contract DB already exists
    if not check_database_existence('smart_contract', {'contract_name': parsed_data['contractName'], 'contract_address': parsed_data['contractAddress']}):
        
        # Check if address already used in token transactions
        systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
        tokenAddressMapping_of_contractAddress = systemdb_session.query(TokenAddressMapping).filter(TokenAddressMapping.tokenAddress == parsed_data['contractAddress']).all()
        systemdb_session.close()

        if len(tokenAddressMapping_of_contractAddress) == 0:
            if parsed_data['contractType'] == 'one-time-event':
                return create_one_time_event_contract(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)
            elif parsed_data['contractType'] == 'continuous-event':
                return create_continuous_event_contract(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)
            else:
                rejectComment = f"Smart contract incorporation on transaction {transaction_data['txid']} rejected: Unknown contractType {parsed_data['contractType']}"
                logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, outputlist[0] if outputlist else None, rejectComment)
                return 0
        else:
            rejectComment = f"Smart contract creation transaction {transaction_data['txid']} rejected as token transactions already exist on the address {parsed_data['contractAddress']}"
            logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, outputlist[0] if outputlist else None, rejectComment)
            return 0

    else:
        rejectComment = f"Transaction {transaction_data['txid']} rejected as a Smart Contract with the name {parsed_data['contractName']} at address {parsed_data['contractAddress']} already exists"
        logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, outputlist[0] if outputlist else None, rejectComment)
        return 0




def create_one_time_event_contract(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    logger.info("Smart contract is of the type one-time-event")

    # Either userchoices or payeeAddress condition should be present
    if not parsed_data.get('contractConditions') or ('userchoices' not in parsed_data['contractConditions'] and 'payeeAddress' not in parsed_data['contractConditions']):
        rejectComment = f"Either userchoice or payeeAddress should be part of the Contract conditions.\nSmart contract incorporation on transaction {transaction_data.get('txid','[unknown]')} rejected"
        logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, (outputlist[0] if outputlist else None), rejectComment)
        return 0

    # userchoice and payeeAddress conditions cannot come together
    if 'userchoices' in parsed_data['contractConditions'] and 'payeeAddress' in parsed_data['contractConditions']:
        rejectComment = f"Both userchoice and payeeAddress provided as part of the Contract conditions.\nSmart contract incorporation on transaction {transaction_data.get('txid','[unknown]')} rejected"
        logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, (outputlist[0] if outputlist else None), rejectComment)
        return 0

    # Contract address in FLOdata must match the input address
    if parsed_data.get('contractAddress') == inputadd:
        session = create_database_session_orm('smart_contract', {'contract_name': parsed_data.get('contractName',''), 'contract_address': parsed_data.get('contractAddress','')}, ContractBase)
        session.add(ContractStructure(attribute='contractType', index=0, value=parsed_data.get('contractType',''), blockNumber=blockinfo.get('height')))
        session.add(ContractStructure(attribute='subtype', index=0, value=parsed_data.get('subtype',''), blockNumber=blockinfo.get('height')))
        session.add(ContractStructure(attribute='contractName', index=0, value=parsed_data.get('contractName',''), blockNumber=blockinfo.get('height')))
        session.add(ContractStructure(attribute='tokenIdentification', index=0, value=parsed_data.get('tokenIdentification',''), blockNumber=blockinfo.get('height')))
        session.add(ContractStructure(attribute='contractAddress', index=0, value=parsed_data.get('contractAddress',''), blockNumber=blockinfo.get('height')))
        session.add(ContractStructure(attribute='floData', index=0, value=parsed_data.get('floData',''), blockNumber=blockinfo.get('height')))
        if parsed_data.get('contractConditions'):
            session.add(ContractStructure(attribute='expiryTime', index=0, value=parsed_data['contractConditions'].get('expiryTime',''), blockNumber=blockinfo.get('height')))
            session.add(ContractStructure(attribute='unix_expiryTime', index=0, value=parsed_data['contractConditions'].get('unix_expiryTime',''), blockNumber=blockinfo.get('height')))
            if 'contractAmount' in parsed_data['contractConditions']: session.add(ContractStructure(attribute='contractAmount', index=0, value=parsed_data['contractConditions']['contractAmount'], blockNumber=blockinfo.get('height')))
            if 'minimumsubscriptionamount' in parsed_data['contractConditions']: session.add(ContractStructure(attribute='minimumsubscriptionamount', index=0, value=parsed_data['contractConditions']['minimumsubscriptionamount'], blockNumber=blockinfo.get('height')))
            if 'maximumsubscriptionamount' in parsed_data['contractConditions']: session.add(ContractStructure(attribute='maximumsubscriptionamount', index=0, value=parsed_data['contractConditions']['maximumsubscriptionamount'], blockNumber=blockinfo.get('height')))
            if 'userchoices' in parsed_data['contractConditions']:
                try:
                    for k,v in literal_eval(parsed_data['contractConditions']['userchoices']).items():
                        session.add(ContractStructure(attribute='exitconditions', index=k, value=v, blockNumber=blockinfo.get('height')))
                except Exception as e:
                    logger.warning(f"Invalid userchoices parsing: {e}")
            if 'payeeAddress' in parsed_data['contractConditions']:
                session.add(ContractStructure(attribute='payeeAddress', index=0, value=json.dumps(parsed_data['contractConditions']['payeeAddress']), blockNumber=blockinfo.get('height')))
        add_contract_transaction_history(contract_name=parsed_data.get('contractName',''), contract_address=parsed_data.get('contractAddress',''), transactionType='smartContractIncorporation', transactionSubType=None, sourceFloAddress=inputadd, destFloAddress=(outputlist[0] if outputlist else None), transferAmount=None, blockNumber=blockinfo.get('height'), blockHash=blockinfo.get('hash',''), blocktime=blockinfo.get('time'), transactionHash=transaction_data.get('txid',''), jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
        session.commit(); session.close()
        blockchainReference = urljoin(neturl, f"tx/{transaction_data.get('txid','')}")
        session = create_database_session_orm('token', {'token_name': parsed_data.get('tokenIdentification','')}, TokenBase)
        session.add(TokenContractAssociation(tokenIdentification=parsed_data.get('tokenIdentification',''), contractName=parsed_data.get('contractName',''), contractAddress=parsed_data.get('contractAddress',''), blockNumber=transaction_data.get('blockheight'), blockHash=transaction_data.get('blockhash'), time=transaction_data.get('time'), transactionHash=transaction_data.get('txid',''), blockchainReference=blockchainReference, jsonData=json.dumps(transaction_data), transactionType=parsed_data.get('type',''), parsedFloData=json.dumps(parsed_data)))
        session.commit(); session.close()
        session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
        session.add(ActiveContracts(contractName=parsed_data.get('contractName',''), contractAddress=parsed_data.get('contractAddress',''), status='active', tokenIdentification=parsed_data.get('tokenIdentification',''), contractType=parsed_data.get('contractType',''), transactionHash=transaction_data.get('txid',''), blockNumber=transaction_data.get('blockheight'), blockHash=transaction_data.get('blockhash'), incorporationDate=transaction_data.get('time')))
        session.commit()
        session.add(ContractAddressMapping(address=inputadd, addressType='smartContractIncorporation', tokenAmount=None, contractName=parsed_data.get('contractName',''), contractAddress=inputadd, transactionHash=transaction_data.get('txid',''), blockNumber=transaction_data.get('blockheight'), blockHash=transaction_data.get('blockhash')))
        session.add(DatabaseTypeMapping(db_name=f"{parsed_data.get('contractName','')}_{inputadd}", db_type='smartcontract', keyword='', object_format='', blockNumber=transaction_data.get('blockheight')))
        session.add(TimeActions(time=parsed_data.get('contractConditions',{}).get('expiryTime',''), activity='contract-time-trigger', status='active', contractName=parsed_data.get('contractName',''), contractAddress=inputadd, contractType='one-time-event-trigger', tokens_db=json.dumps([parsed_data.get('tokenIdentification','')]), parsed_data=json.dumps(parsed_data), transactionHash=transaction_data.get('txid',''), blockNumber=transaction_data.get('blockheight')))
        session.commit(); session.close()
        updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data.get('contractName','')}_{parsed_data.get('contractAddress','')}")
        pushData_SSEapi(f"Contract | Contract incorporated at transaction {transaction_data.get('txid','')} with name {parsed_data.get('contractName','')}-{parsed_data.get('contractAddress','')}")
        return 1

    else:
        rejectComment = f"Contract Incorporation on transaction {transaction_data.get('txid','[unknown]')} rejected as contract address in Flodata and input address are different"
        logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, (outputlist[0] if outputlist else None), rejectComment)
        pushData_SSEapi(f"Error | Contract Incorporation rejected as address in Flodata and input address are different at transaction {transaction_data.get('txid','[unknown]')}")
        return 0



def create_continuous_event_contract(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    logger.debug("Smart contract is of the type continuous-event")

    # Contract address in FLOdata must match the input address
    if parsed_data.get('contractAddress') == inputadd:
        session = create_database_session_orm('smart_contract', {'contract_name': parsed_data.get('contractName',''), 'contract_address': parsed_data.get('contractAddress','')}, ContractBase)
        session.add(ContractStructure(attribute='contractType', index=0, value=parsed_data.get('contractType',''), blockNumber=blockinfo.get('height')))
        session.add(ContractStructure(attribute='contractName', index=0, value=parsed_data.get('contractName',''), blockNumber=blockinfo.get('height')))
        session.add(ContractStructure(attribute='contractAddress', index=0, value=parsed_data.get('contractAddress',''), blockNumber=blockinfo.get('height')))
        session.add(ContractStructure(attribute='floData', index=0, value=parsed_data.get('floData',''), blockNumber=blockinfo.get('height')))
        
        # Handle any stateF attributes
        if parsed_data.get('stateF') not in [{}, False, None]:
            for k,v in parsed_data['stateF'].items():
                session.add(ContractStructure(attribute=f"statef-{k}", index=0, value=v, blockNumber=blockinfo.get('height')))
        
        # Process contractConditions if subtype exists
        contractConditions = parsed_data.get('contractConditions', {})
        if 'subtype' in contractConditions:
            selling_token = contractConditions.get('selling_token','')
            accepting_token = contractConditions.get('accepting_token','')
            if (contractConditions.get('subtype') == 'tokenswap' and isinstance(selling_token,str) and isinstance(accepting_token,str) and check_database_existence('token', {'token_name': selling_token.split('#')[0]}) and check_database_existence('token', {'token_name': accepting_token.split('#')[0]})):
                session.add(ContractStructure(attribute='subtype', index=0, value=contractConditions.get('subtype',''), blockNumber=blockinfo.get('height')))
                session.add(ContractStructure(attribute='accepting_token', index=0, value=accepting_token, blockNumber=blockinfo.get('height')))
                session.add(ContractStructure(attribute='selling_token', index=0, value=selling_token, blockNumber=blockinfo.get('height')))
                pricetype = contractConditions.get('pricetype','')
                if pricetype not in ['predetermined','statef','dynamic']:
                    rejectComment = f"pricetype is not part of accepted parameters for a continuous event contract of the type token swap.\nSmart contract incorporation on transaction {transaction_data.get('txid','[unknown]')} rejected"
                    logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, (outputlist[0] if outputlist else None), rejectComment)
                    return 0
                session.add(ContractStructure(attribute='pricetype', index=0, value=pricetype, blockNumber=blockinfo.get('height')))
                if pricetype in ['predetermined','statef']:
                    session.add(ContractStructure(attribute='price', index=0, value=contractConditions.get('price',''), blockNumber=blockinfo.get('height')))
                elif pricetype == 'dynamic':
                    session.add(ContractStructure(attribute='price', index=0, value=contractConditions.get('price',''), blockNumber=blockinfo.get('height')))
                    session.add(ContractStructure(attribute='oracle_address', index=0, value=contractConditions.get('oracle_address',''), blockNumber=blockinfo.get('height')))
                blockchainReference = urljoin(neturl, f"tx/{transaction_data.get('txid','')}")
                session.add(ContractTransactionHistory(transactionType='smartContractIncorporation', sourceFloAddress=inputadd, destFloAddress=(outputlist[0] if outputlist else None), transferAmount=None, blockNumber=transaction_data.get('blockheight'), blockHash=transaction_data.get('blockhash'), time=transaction_data.get('time'), transactionHash=transaction_data.get('txid',''), blockchainReference=blockchainReference, jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data)))
                session.commit(); session.close()

                # Add token contract associations
                accepting_sending_tokenlist = [accepting_token, selling_token]
                for tkn in accepting_sending_tokenlist:
                    token_name = tkn.split('#')[0] if isinstance(tkn,str) else ''
                    token_session = create_database_session_orm('token', {'token_name': token_name}, TokenBase)
                    token_session.add(TokenContractAssociation(tokenIdentification=token_name, contractName=parsed_data.get('contractName',''), contractAddress=parsed_data.get('contractAddress',''), blockNumber=transaction_data.get('blockheight'), blockHash=transaction_data.get('blockhash'), time=transaction_data.get('time'), transactionHash=transaction_data.get('txid',''), blockchainReference=blockchainReference, jsonData=json.dumps(transaction_data), transactionType=parsed_data.get('type',''), parsedFloData=json.dumps(parsed_data)))
                    token_session.commit(); token_session.close()
                
                # Register smart contract address in system DB
                system_session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
                system_session.add(ActiveContracts(contractName=parsed_data.get('contractName',''), contractAddress=parsed_data.get('contractAddress',''), status='active', tokenIdentification=str(accepting_sending_tokenlist), contractType=parsed_data.get('contractType',''), transactionHash=transaction_data.get('txid',''), blockNumber=transaction_data.get('blockheight'), blockHash=transaction_data.get('blockhash'), incorporationDate=transaction_data.get('time')))
                system_session.commit()
                system_session.add(ContractAddressMapping(address=inputadd, addressType='smartContractIncorporation', tokenAmount=None, contractName=parsed_data.get('contractName',''), contractAddress=inputadd, transactionHash=transaction_data.get('txid',''), blockNumber=transaction_data.get('blockheight'), blockHash=transaction_data.get('blockhash')))
                system_session.add(DatabaseTypeMapping(db_name=f"{parsed_data.get('contractName','')}_{inputadd}", db_type='smartcontract', keyword='', object_format='', blockNumber=transaction_data.get('blockheight')))
                system_session.commit(); system_session.close()
                updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data.get('contractName','')}_{parsed_data.get('contractAddress','')}")
                pushData_SSEapi(f"Contract | Contract incorporated at transaction {transaction_data.get('txid','')} with name {parsed_data.get('contractName','')}_{parsed_data.get('contractAddress','')}")
                return 1
            else:
                rejectComment = f"One of the token for the swap does not exist.\nSmart contract incorporation on transaction {transaction_data.get('txid','[unknown]')} rejected"
                logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, (outputlist[0] if outputlist else None), rejectComment)
                return 0
        else:
            rejectComment = f"No subtype provided || mentioned tokens do not exist for the Contract of type continuous event.\nSmart contract incorporation on transaction {transaction_data.get('txid','[unknown]')} rejected"
            logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, (outputlist[0] if outputlist else None), rejectComment)
            return 0

    else:
        rejectComment = f"Contract Incorporation on transaction {transaction_data.get('txid','[unknown]')} rejected as contract address in Flodata and input address are different"
        logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractIncorporation', inputadd, inputadd, (outputlist[0] if outputlist else None), rejectComment)
        pushData_SSEapi(f"Error | Contract Incorporation rejected as address in Flodata and input address are different at transaction {transaction_data.get('txid','[unknown]')}")
        return 0


def process_smart_contract_pays(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    logger.info(f"Transaction {transaction_data['txid']} is of the type smartContractPays")
    committeeAddressList = refresh_committee_list(APP_ADMIN, neturl, blockinfo['time'])
    if inputlist[0] not in committeeAddressList:
        rejectComment = f"Transaction {transaction_data['txid']} rejected as input address, {inputlist[0]}, is not part of the committee address list"
        logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
    if not check_database_existence('smart_contract', {'contract_name': parsed_data['contractName'], 'contract_address': outputlist[0]}):
        rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]} doesn't exist"
        logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
    connection = None
    try:
        connection = create_database_connection('smart_contract', {'contract_name': parsed_data['contractName'], 'contract_address': outputlist[0]})
        participantAdd_txhash = connection.execute("SELECT sourceFloAddress, transactionHash FROM contractTransactionHistory WHERE transactionType != 'smartContractIncorporation'").fetchall()
        participantAdd_txhash_T = list(zip(*participantAdd_txhash)) if participantAdd_txhash else []
        if participantAdd_txhash_T and transaction_data['txid'] in participantAdd_txhash_T[1]:
            logger.warning(f"Transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db."); pushData_SSEapi(f"Error | Transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db."); return 0
        contractStructure = extract_contractStructure(parsed_data['contractName'], outputlist[0])
        if 'contractAddress' in contractStructure and outputlist[0] != contractStructure['contractAddress']:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]} hasn't expired yet"
            logger.warning(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
        if 'payeeAddress' in contractStructure:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]} has an internal trigger"
            logger.warning(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
    finally:
        if connection: connection.close()
    contractStatus = check_contract_status(parsed_data['contractName'], outputlist[0])
    if contractStatus == 'closed':
        rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart contract {parsed_data['contractName']} at {outputlist[0]} is closed"
        logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment); return 0
    session = None
    try:
        session = create_database_session_orm('smart_contract', {'contract_name': parsed_data['contractName'], 'contract_address': outputlist[0]}, ContractBase)
        result = session.query(ContractStructure).filter_by(attribute='expiryTime').first()
    except Exception as e:
        logger.error(f"Error querying expiry time: {e}")
        if session: session.rollback()
        raise
    finally:
        if session: session.close()
    if result:
        expirytime = result.value.strip(); expirytime_split = expirytime.split(' ')
        parse_string = f"{expirytime_split[3]}/{parsing.months[expirytime_split[1]]}/{expirytime_split[2]} {expirytime_split[4]}"
        expirytime_object = parsing.arrow.get(parse_string, 'YYYY/M/D HH:mm:ss').replace(tzinfo=expirytime_split[5][3:])
        blocktime_object = parsing.arrow.get(transaction_data['time']).to('Asia/Kolkata')
        if blocktime_object <= expirytime_object:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart contract {parsed_data['contractName']}_{outputlist[0]} has not expired and will not trigger"
            logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
    if 'exitconditions' in contractStructure:
        tempchoiceList = list(contractStructure['exitconditions'].values())
        if parsed_data['triggerCondition'] not in tempchoiceList:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as triggerCondition, {parsed_data['triggerCondition']}, has been passed to Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]} which doesn't accept any userChoice of the given name"
            logger.info(rejectComment); rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
    systemdb_session = None
    try:
        systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
        activecontracts_table_info = systemdb_session.query(ActiveContracts.blockHash, ActiveContracts.incorporationDate, ActiveContracts.expiryDate).filter(ActiveContracts.contractName == parsed_data['contractName'], ActiveContracts.contractAddress == outputlist[0], ActiveContracts.status == 'expired').first()
        timeactions_table_info = systemdb_session.query(TimeActions.time, TimeActions.activity, TimeActions.contractType, TimeActions.tokens_db, TimeActions.parsed_data).filter(TimeActions.contractName == parsed_data['contractName'], TimeActions.contractAddress == outputlist[0], TimeActions.status == 'active').first()
    finally:
        if systemdb_session: systemdb_session.close()
    incorporationDate = activecontracts_table_info.incorporationDate if activecontracts_table_info else None
    expiryDate = activecontracts_table_info.expiryDate if activecontracts_table_info else None
    timeActionTime = timeactions_table_info.time if timeactions_table_info else None
    timeActionActivity = timeactions_table_info.activity if timeactions_table_info else None
    contractType = timeactions_table_info.contractType if timeactions_table_info else None
    tokens_db = timeactions_table_info.tokens_db if timeactions_table_info else None
    parsed_data_ta = timeactions_table_info.parsed_data if timeactions_table_info else None
    if 'minimumsubscriptionamount' in contractStructure:
        minimumsubscriptionamount = float(contractStructure['minimumsubscriptionamount'])
        session = None
        try:
            session = create_database_session_orm('smart_contract', {'contract_name': parsed_data['contractName'], 'contract_address': outputlist[0]}, ContractBase)
            query_data = session.query(ContractParticipants.tokenAmount).all()
            amountDeposited = sum(Decimal(str(x[0])) if x[0] is not None else Decimal(0) for x in query_data)
        except Exception as e:
            logger.error(f"Error querying contract participants: {e}")
            if session: session.rollback()
            raise
        finally:
            if session: session.close()
        if amountDeposited < minimumsubscriptionamount:
            logger.info(f"Minimum subscription amount not reached for contract {parsed_data['contractName']}_{outputlist[0]}. Refunding tokens.")
            connection = None
            try:
                connection = create_database_connection('smart_contract', {'contract_name': parsed_data['contractName'], 'contract_address': outputlist[0]})
                contractParticipants = connection.execute('SELECT participantAddress, tokenAmount, transactionHash FROM contractparticipants').fetchall()
                for participant in contractParticipants:
                    participantAddress, tokenAmount, txHash = participant
                    row = connection.execute('SELECT value FROM contractstructure WHERE attribute="tokenIdentification"').fetchone()
                    tokenIdentification = row[0] if row else None
                    row2 = connection.execute('SELECT value FROM contractstructure WHERE attribute="contractAddress"').fetchone()
                    contractAddress = row2[0] if row2 else None
                    returnval = transferToken(tokenIdentification, tokenAmount, contractAddress, participantAddress, transaction_data, parsed_data, blockinfo=blockinfo)
                    if returnval == 0:
                        logger.error("CRITICAL ERROR during refund in smart contract minimum subscription check."); return 0
                    connection.execute("UPDATE contractparticipants SET winningAmount=%s WHERE participantAddress=%s AND transactionHash=%s", (tokenAmount, participantAddress, txHash))
            finally:
                if connection: connection.close()
            blockchainReference = neturl + 'tx/' + transaction_data['txid']
            session = None
            try:
                session = create_database_session_orm('smart_contract', {'contract_name': parsed_data['contractName'], 'contract_address': outputlist[0]}, ContractBase)
                session.add(ContractTransactionHistory(transactionType='trigger', transactionSubType='minimumsubscriptionamount-payback', sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=None, blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash'], time=transaction_data['time'], transactionHash=transaction_data['txid'], blockchainReference=blockchainReference, jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))); session.commit()
            finally:
                if session: session.close()
            close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], incorporationDate, expiryDate, blockinfo['time'], timeActionTime, timeActionActivity, parsed_data['contractName'], outputlist[0], contractType, tokens_db, parsed_data_ta, blockinfo['height'])
            updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}_{outputlist[0]}")
            pushData_SSEapi(f"Trigger | Minimum subscription amount not reached at contract {parsed_data['contractName']}_{outputlist[0]} at transaction {transaction_data['txid']}. Tokens will be refunded.")
            return 1
    connection = None
    try:
        connection = create_database_connection('smart_contract', {'contract_name': parsed_data['contractName'], 'contract_address': outputlist[0]})
        rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
        tokenSum = float(sum(Decimal(str(x[0])) for x in rows)) if rows else 0.0
        if tokenSum > 0:
            contractWinners = connection.execute("SELECT * FROM contractparticipants WHERE userChoice = %s", (parsed_data["triggerCondition"],)).fetchall()
            winnerRows = connection.execute("SELECT tokenAmount FROM contractparticipants WHERE userChoice = %s", (parsed_data["triggerCondition"],)).fetchall()
            winnerSum = float(sum(Decimal(str(x[0])) for x in winnerRows)) if winnerRows else 0.0
            row = connection.execute('SELECT value FROM contractstructure WHERE attribute="tokenIdentification"').fetchone()
            tokenIdentification = row[0] if row else None
            for winner in contractWinners:
                winnerAmount = "%.8f" % perform_decimal_operation('multiplication', perform_decimal_operation('division', winner[2], winnerSum), tokenSum)
                returnval = transferToken(tokenIdentification, winnerAmount, outputlist[0], winner[1], transaction_data, parsed_data, blockinfo=blockinfo)
                if returnval == 0:
                    logger.critical("Critical error during contract trigger token transfer."); return 0
                connection.execute("INSERT INTO contractwinners (participantAddress, winningAmount, userChoice, transactionHash, blockNumber, blockHash, referenceTxHash) VALUES (%s, %s, %s, %s, %s, %s, %s)", (winner[1], winnerAmount, parsed_data["triggerCondition"], transaction_data["txid"], blockinfo["height"], blockinfo["hash"], winner[4]))
    finally:
        if connection: connection.close()
    blockchainReference = urljoin(neturl, f"tx/{transaction_data['txid']}")
    session = None
    try:
        session = create_database_session_orm('smart_contract', {'contract_name': parsed_data['contractName'], 'contract_address': outputlist[0]}, ContractBase)
        session.add(ContractTransactionHistory(transactionType='trigger', transactionSubType='committee', sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=None, blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash'], time=transaction_data['time'], transactionHash=transaction_data['txid'], blockchainReference=blockchainReference, jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))); session.commit()
    finally:
        if session: session.close()
    close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], incorporationDate, expiryDate, blockinfo['time'], timeActionTime, timeActionActivity, contractStructure['contractName'], contractStructure['contractAddress'], contractType, tokens_db, parsed_data_ta, blockinfo['height'])
    updateLatestTransaction(transaction_data, parsed_data, f"{contractStructure['contractName']}_{contractStructure['contractAddress']}")
    pushData_SSEapi(f"Trigger | Contract triggered of the name {parsed_data['contractName']}_{outputlist[0]} is active currently at transaction {transaction_data['txid']}")
    return 1


def process_smart_contract_deposit(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    contract_address = outputlist[0]

    if check_database_existence('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': contract_address}):
        # Reject if the deposit expiry time is greater than incorporated blocktime
        expiry_time = convert_datetime_to_arrowobject(parsed_data['depositConditions']['expiryTime'])
        if blockinfo['time'] > expiry_time.timestamp:
            rejectComment = f"Contract deposit of transaction {transaction_data['txid']} rejected as expiryTime before current block time"
            logger.warning(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'deposit', contract_address, inputadd, contract_address, rejectComment)
            return 0

        # Check if the transaction hash already exists in the contract db (Safety check)
        connection = create_database_connection('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': contract_address})
        participantAdd_txhash = connection.execute('SELECT participantAddress, transactionHash FROM contractparticipants').fetchall()
        participantAdd_txhash_T = list(zip(*participantAdd_txhash))
        if len(participantAdd_txhash) != 0 and transaction_data['txid'] in list(participantAdd_txhash_T[1]):
            rejectComment = f"Contract deposit at transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db. This is unusual, please check your code"
            logger.warning(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'deposit', contract_address, inputadd, contract_address, rejectComment)
            return 0

        # if contractAddress was passed, then check if it matches the output address of this contract
        if 'contractAddress' in parsed_data:
            if parsed_data['contractAddress'] != contract_address:
                rejectComment = f"Contract deposit at transaction {transaction_data['txid']} rejected as contractAddress specified in floData, {parsed_data['contractAddress']}, doesnt not match with transaction's output address {contract_address}"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', contract_address, inputadd, contract_address, rejectComment)
                # Pass information to SSE channel
                pushData_SSEapi(f"Error| Mismatch in contract address specified in floData and the output address of the transaction {transaction_data['txid']}")
                return 0

        # pull out the contract structure into a dictionary
        contractStructure = extract_contractStructure(parsed_data['contractName'], contract_address)

        # Transfer the token
        logger.info(f"Initiating transfers for smartcontract deposit with transaction ID {transaction_data['txid']}")
        returnval = transferToken(parsed_data['tokenIdentification'], parsed_data['depositAmount'], inputlist[0], contract_address, transaction_data, parsed_data, blockinfo=blockinfo)
        if returnval == 0:
            logger.info("Something went wrong in the token transfer method")
            pushData_SSEapi(f"Error | Something went wrong while doing the internal db transactions for {transaction_data['txid']}")
            return 0

        # Push the deposit transaction into deposit database contract database
        session = create_database_session_orm('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': contract_address}, ContractBase)
        blockchainReference = urljoin(neturl, f"tx/{transaction_data['txid']}")
        session.add(ContractDeposits(depositorAddress=inputadd, depositAmount=parsed_data['depositAmount'], depositBalance=parsed_data['depositAmount'], expiryTime=parsed_data['depositConditions']['expiryTime'], unix_expiryTime=convert_datetime_to_arrowobject(parsed_data['depositConditions']['expiryTime']).timestamp, status='active', transactionHash=transaction_data['txid'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash']))
        session.add(ContractTransactionHistory(transactionType='smartContractDeposit', transactionSubType=None, sourceFloAddress=inputadd, destFloAddress=contract_address, transferAmount=parsed_data['depositAmount'],transferToken=contractStructure['selling_token'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash'], time=transaction_data['time'], transactionHash=transaction_data['txid'], blockchainReference=blockchainReference, jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data)))
        session.commit()
        session.close()

        session = create_database_session_orm('system_dbs', {'db_name': f"system"}, SystemBase)
        session.add(TimeActions(time=parsed_data['depositConditions']['expiryTime'], activity='contract-deposit', status='active', contractName=parsed_data['contractName'], contractAddress=contract_address, contractType='continuous-event-swap', tokens_db=f"{parsed_data['tokenIdentification']}", parsed_data=json.dumps(parsed_data), transactionHash=transaction_data['txid'], blockNumber=transaction_data['blockheight']))
        session.commit()
        pushData_SSEapi(f"Deposit Smart Contract Transaction {transaction_data['txid']} for the Smart contract named {parsed_data['contractName']} at the address {contract_address}")

        # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
        systemdb_connection = create_database_connection('system_dbs', {'db_name':'system'})
        firstInteractionCheck = systemdb_connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{contract_address}' AND token='{parsed_data['tokenIdentification']}'").fetchall()
        if len(firstInteractionCheck) == 0:
            systemdb_connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{contract_address}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")
        systemdb_connection.close()

        updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}_{contract_address}")
        return 1

    else:
        contract_address = parsed_data.get('contractAddress', outputlist[0])
        rejectComment = f"Deposit Transaction {transaction_data['txid']} rejected as a Smart Contract with the name {parsed_data['contractName']} at address {contract_address} doesnt exist"
        logger.info(rejectComment)
        rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractDeposit', contract_address, inputadd, contract_address, rejectComment)
        return 0




def process_nft_incorporation(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    try:
        if is_a_contract_address(inputlist[0]):
            rejectComment = f"NFT incorporation at transaction {transaction_data['txid']} rejected as either the input address is part of a contract address"
            logger.info(rejectComment); rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
        if check_database_existence('token', {'token_name': f"{parsed_data['tokenIdentification']}"}):
            rejectComment = f"Transaction {transaction_data['txid']} rejected as an NFT with the name {parsed_data['tokenIdentification']} has already been incorporated"
            logger.info(rejectComment); rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
        if 'nftHash' not in parsed_data:
            logger.warning(f"Transaction {transaction_data['txid']} rejected as nftHash is missing."); rejectComment = f"NFT incorporation rejected because nftHash is missing in floData for transaction {transaction_data['txid']}"
            rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
        session = create_database_session_orm('token', {'token_name': f"{parsed_data['tokenIdentification']}"}, TokenBase)
        try:
            session.add(ActiveTable(address=inputlist[0], parentid=0, transferBalance=parsed_data['tokenAmount'], addressBalance=parsed_data['tokenAmount'], blockNumber=blockinfo['height']))
            session.add(TransferLogs(sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], sourceId=0, destinationId=1, blockNumber=transaction_data['blockheight'], time=transaction_data['time'], transactionHash=transaction_data['txid']))
            add_transaction_history(token_name=parsed_data['tokenIdentification'], sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash'], blocktime=transaction_data['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=parsed_data['type'], parsedFloData=json.dumps(parsed_data))
            session.commit()
        except Exception as e:
            session.rollback(); logger.error(f"Error while incorporating NFT in token db: {e}"); return 0
        finally:
            session.close()
        connection = create_database_connection('system_dbs', {'db_name': 'system'})
        try:
            connection.execute("""INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES (:addr, :token, :txid, :blocknumber, :blockhash)""", {'addr': inputadd, 'token': parsed_data['tokenIdentification'], 'txid': transaction_data['txid'], 'blocknumber': transaction_data['blockheight'], 'blockhash': transaction_data['blockhash']})
            nft_data = {'sha256_hash': parsed_data['nftHash']}
            connection.execute("""INSERT INTO databaseTypeMapping (db_name, db_type, keyword, object_format, blockNumber) VALUES (:dbname, :dbtype, '', :object_format, :blocknumber)""", {'dbname': parsed_data['tokenIdentification'], 'dbtype': 'nft', 'object_format': json.dumps(nft_data), 'blocknumber': transaction_data['blockheight']})
        except Exception as e:
            logger.error(f"Error while inserting NFT into system db: {e}"); return 0
        finally:
            connection.close()
        updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['tokenIdentification']}")
        pushData_SSEapi(f"NFT | Successfully incorporated NFT {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error in NFT incorporation for tx {transaction_data['txid']}: {e}"); return 0



def process_infinite_token_incorporation(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    logger.info(f"Processing infinite token incorporation for transaction {transaction_data['txid']}")
    if is_a_contract_address(inputlist[0]) or is_a_contract_address(outputlist[0]):
        rejectComment = f"Infinite token incorporation at transaction {transaction_data['txid']} rejected as either the input address or output address is part of a contract address"
        logger.info(rejectComment); rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
    if check_database_existence('token', {'token_name': f"{parsed_data['tokenIdentification']}"}):
        rejectComment = f"Transaction {transaction_data['txid']} rejected as a token with the name {parsed_data['tokenIdentification']} has already been incorporated"
        logger.info(rejectComment); rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment); pushData_SSEapi(rejectComment); return 0
    parsed_data['tokenAmount'] = 0; tokendb_session = None; connection = None
    try:
        tokendb_session = create_database_session_orm('token', {'token_name': f"{parsed_data['tokenIdentification']}"}, TokenBase)
        tokendb_session.add(ActiveTable(address=inputlist[0], parentid=0, transferBalance=parsed_data['tokenAmount'], blockNumber=blockinfo['height']))
        tokendb_session.add(TransferLogs(sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], sourceId=0, destinationId=1, blockNumber=transaction_data['blockheight'], time=transaction_data['time'], transactionHash=transaction_data['txid']))
        add_transaction_history(token_name=parsed_data['tokenIdentification'], sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=parsed_data['type'], parsedFloData=json.dumps(parsed_data))
        connection = create_database_connection('system_dbs', {'db_name': 'system'})
        connection.execute(text("""INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES (:addr, :token, :txid, :blocknumber, :blockhash)"""), {'addr': inputadd, 'token': parsed_data['tokenIdentification'], 'txid': transaction_data['txid'], 'blocknumber': transaction_data['blockheight'], 'blockhash': transaction_data['blockhash']})
        info_object = {'root_address': inputadd}
        connection.execute(text("""INSERT INTO databaseTypeMapping (db_name, db_type, keyword, object_format, blockNumber) VALUES (:dbname, :dbtype, '', :object_format, :blocknumber)"""), {'dbname': parsed_data['tokenIdentification'], 'dbtype': 'infinite-token', 'object_format': json.dumps(info_object), 'blocknumber': transaction_data['blockheight']})
        tokendb_session.commit(); updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['tokenIdentification']}")
        logger.info(f"Token | Successfully incorporated token {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
        pushData_SSEapi(f"Token | Successfully incorporated token {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
        return 1
    except Exception as e:
        logger.error(f"Error during infinite token incorporation: {e}")
        if tokendb_session: tokendb_session.rollback(); return 0
    finally:
        if connection: connection.close()
        if tokendb_session: tokendb_session.close()




# Main processing functions START

def processTransaction(transaction_data, parsed_data, blockinfo):
    # Defensive check for parsed_data keys
    tx_type = parsed_data.get('type')
    if not tx_type:
        logger.info(f"Transaction {transaction_data.get('txid', '[unknown]')} rejected: Missing type field.")
        return 0

    inputlist, outputlist, inputadd = process_flo_checks(transaction_data)

    if inputlist is None or outputlist is None:
        logger.info(f"Transaction {transaction_data.get('txid', '[unknown]')} rejected during flo checks.")
        return 0

    # Defensive check
    if not isinstance(inputlist, list) or len(inputlist) == 0:
        logger.info(f"Transaction {transaction_data.get('txid', '[unknown]')} rejected: inputlist empty or invalid.")
        return 0

    transaction_data['senderAddress'] = inputlist[0]

    # Handle outputlist if it's either a single address or list
    if isinstance(outputlist, list) and len(outputlist) > 0:
        transaction_data['receiverAddress'] = outputlist[0]
    elif isinstance(outputlist, str):
        transaction_data['receiverAddress'] = outputlist
    else:
        logger.info(f"Transaction {transaction_data.get('txid', '[unknown]')} rejected: outputlist empty or invalid.")
        return 0

    logger.info(f"Input address list : {inputlist}")
    logger.info(f"Output address list : {outputlist}")

    # Dispatch based on transaction type
    if tx_type == 'transfer':
        logger.info(f"Transaction {transaction_data['txid']} is of the type transfer")

        transfer_type = parsed_data.get('transferType')
        if transfer_type == 'token':
            return process_token_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)
        elif transfer_type == 'smartContract':
            return process_smart_contract_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)
        elif transfer_type == 'nft':
            return process_nft_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)
        else:
            logger.info(f"Invalid transfer type in transaction {transaction_data['txid']}")
            return 0

    elif tx_type == 'tokenIncorporation':
        logger.info(f"Transaction {transaction_data['txid']} is of the type tokenIncorporation")
        return process_token_incorporation(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)

    elif tx_type == 'smartContractIncorporation':
        logger.info(f"Transaction {transaction_data['txid']} is of the type smartContractIncorporation")
        return process_smart_contract_incorporation(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)

    elif tx_type == 'smartContractPays':
        logger.info(f"Transaction {transaction_data['txid']} is of the type smartContractPays")
        return process_smart_contract_pays(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)

    elif tx_type == 'smartContractDeposit':
        logger.info(f"Transaction {transaction_data['txid']} is of the type smartContractDeposit")
        return process_smart_contract_deposit(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)

    elif tx_type == 'nftIncorporation':
        logger.info(f"Transaction {transaction_data['txid']} is of the type nftIncorporation")
        return process_nft_incorporation(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)

    elif tx_type == 'infiniteTokenIncorporation':
        logger.info(f"Transaction {transaction_data['txid']} is of the type infiniteTokenIncorporation")
        return process_infinite_token_incorporation(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd)

    else:
        logger.info(f"Transaction {transaction_data['txid']} rejected as it doesn't belong to any valid type")
        return 0

    return 1

def scanBlockchain():
    """
    Scans the blockchain from the last scanned block to the current block height,
    processing each block unless it's in the IGNORE_BLOCK_LIST.
    """
    max_retries = 50
    retries = 0
    while True:
        try:
            session = create_database_session_orm(
                'system_dbs',
                {'db_name': "system"},
                SystemBase
            )
            entry = session.query(SystemData).filter_by(attribute='lastblockscanned').first()
            startblock = int(entry.value) + 1 if entry else 0
            session.close()
            break
        except Exception as e:
            logger.info(
                f"Unable to connect to 'system' database: {e}. Retrying in {DB_RETRY_TIMEOUT} seconds."
            )
            retries += 1
            if retries >= max_retries:
                logger.critical("Exceeded maximum retries connecting to system DB. Aborting scan.")
                return
            time.sleep(DB_RETRY_TIMEOUT)

    try:
        current_index = fetch_current_block_height()
    except Exception as e:
        logger.error(f"Could not fetch current block height: {e}")
        return

    if startblock >= current_index:
        logger.info("No new blocks to scan.")
        return

    logger.info(f"Scanning from block {startblock} to {current_index - 1}")

    for blockindex in range(startblock, current_index):
        if blockindex in IGNORE_BLOCK_LIST:
            logger.info(f"Skipping block {blockindex} (in ignore list).")
            continue
        try:
            processBlock(blockindex=blockindex)
        except Exception as e:
            logger.error(f"Error processing block {blockindex}: {e}")




def switchNeturl(currentneturl):
    neturlindex = serverlist.index(currentneturl)
    return serverlist[(neturlindex + 1) % len(serverlist)]


def reconnectWebsocket(socket_variable):
    def to_ws_url(url):
        return url.replace("https://", "wss://").replace("http://", "ws://")

    i = 0
    newurl = neturl
    while not socket_variable.connected:
        logger.info(f"While loop {i} - attempting reconnect to {newurl}")
        time.sleep(3)
        try:
            scanBlockchain()
            ws_url = f"{to_ws_url(newurl)}/websocket"
            logger.info(f"Websocket connecting to {ws_url}")
            socket_variable.connect(ws_url)
            logger.info(f"Websocket reconnected successfully at {newurl}")
            break
        except Exception as e:
            logger.warning(f"Reconnect failed for {newurl}: {e}")
            newurl = switchNeturl(newurl)
            i += 1




def get_websocket_uri():
    current = neturl
    return current.replace("https://", "wss://").replace("http://", "ws://") + "/websocket"



async def connect_to_websocket(uri):
    while True:
        try:
            async with websockets.connect(uri) as websocket:
                subscription_request = {
                    "id": "0",
                    "method": "subscribeNewBlock",
                    "params": {}
                }
                await websocket.send(json.dumps(subscription_request))

                while True:
                    response = await websocket.recv()
                    logger.info(f"Received: {response}")
                    response = json.loads(response)

                    # Only handle messages with a "data" field
                    if "data" in response:
                        data = response["data"]
                        height = data.get("height")
                        blockhash = data.get("hash")

                        if not height:
                            logger.warning("Received block notification with missing height.")
                            continue
                        if not blockhash:
                            logger.warning("Received block notification with missing blockhash.")
                            continue

                        # GAP DETECTION START
                        # Check last block scanned in DB
                        session = create_database_session_orm(
                            'system_dbs',
                            {'db_name': "system"},
                            SystemBase
                        )
                        entry = session.query(SystemData).filter_by(attribute='lastblockscanned').first()
                        last_scanned = int(entry.value) if entry else -1
                        session.close()

                        if height > last_scanned + 1:
                            logger.info(f"Gap detected: scanning blocks {last_scanned+1} to {height-1}")
                            for h in range(last_scanned+1, height):
                                try:
                                    processBlock(blockindex=h)
                                except Exception as e:
                                    logger.error(f"Error processing missing block {h}: {e}")

                        # GAP DETECTION END

                        processBlock(blockindex=height, blockhash=blockhash)

                    elif "result" in response:
                        logger.info(f"WebSocket subscription response: {response}")

                    else:
                        logger.warning(f"Received unexpected websocket message: {response}")

        except Exception as e:
            logger.info(f"Connection error: {e}")
            await asyncio.sleep(5)
            scanBlockchain()




def initialize_neturl(blockbook_neturl_list):
    tried_servers = []

    while len(tried_servers) < len(blockbook_neturl_list):
        neturl = random.choice([url for url in blockbook_neturl_list if url not in tried_servers])
        try:
            test_url = f"{neturl}/api/latest-block"
            print(f"Testing BLOCKBOOK_NETURL: {test_url}")
            response = requests.get(test_url, verify=True, timeout=100)
            response.raise_for_status()

            response_data = response.json()

            if (
                "blockheight" in response_data and
                "blockhash" in response_data and
                "latest_time" in response_data
            ):
                print(f"Selected 'address_indexer' server: {neturl}")
                return neturl, "address_indexer"

            elif (
                "blockbook" in response_data and
                "backend" in response_data
            ):
                print(f"Selected 'blockbook_legacy' server: {neturl}")
                return neturl, "blockbook_legacy"

            else:
                raise Exception(f"Unrecognized API response structure: {response_data}")

        except Exception as e:
            print(f"Error testing {neturl}: {e}")
            tried_servers.append(neturl)

    raise Exception("No valid BLOCKBOOK_NETURL could be initialized.")



def newMultiRequest(apicall):
    current_server = neturl  # Start from validated working server
    retry_count = 0

    while True:
        try:
            url = f"{current_server}/api/{apicall}"
            logger.info(f"Calling the API: {url}")
            response = requests.get(url, verify=API_VERIFY, timeout=RETRY_TIMEOUT_SHORT)

            if response.status_code == 200:
                try:
                    response_data = response.json()

                    if 'txs' in response_data:
                        for tx in response_data['txs']:
                            normalize_transaction_data(tx)

                    return response_data

                except ValueError as e:
                    logger.error(f"Failed to parse JSON: {e}")
                    raise

            else:
                logger.warning(f"Received HTTP {response.status_code}: {response.text}")
                raise requests.exceptions.HTTPError(f"HTTP {response.status_code}")

        except Exception as e:
            logger.error(f"Request failed for {current_server}: {e}")
            retry_count += 1
            current_server = switchNeturl(current_server)
            logger.info(f"Switched to {current_server}. Retrying... Attempt #{retry_count}")
            time.sleep(2)





def pushData_SSEapi(message):
    '''signature = pyflo.sign_message(message.encode(), privKey)
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'Signature': signature}

    try:
        r = requests.post(sseAPI_url, json={'message': '{}'.format(message)}, headers=headers)
    except:
    logger.error("couldn't push the following message to SSE api {}".format(message))'''
    print('')




# MAIN EXECUTION STARTS 
# Configuration of required variables 
# MAIN EXECUTION STARTS
# Load configuration
config = configparser.ConfigParser()
config.read('config.ini')

# MySQL config
class MySQLConfig:
    def __init__(self):
        self.username = config['MYSQL']['USERNAME']
        self.password = config['MYSQL']['PASSWORD']
        self.host = config['MYSQL']['HOST']
        self.database_prefix = config['MYSQL']['DATABASE_PREFIX']

mysql_config = MySQLConfig()

# Setup logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.pool').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.dialects').setLevel(logging.WARNING)

formatter = logging.Formatter('%(asctime)s:%(name)s:%(message)s')
DATA_PATH = os.path.dirname(os.path.abspath(__file__))
file_handler = logging.FileHandler(os.path.join(DATA_PATH, 'tracking.log'))
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# Command-line arguments
parser = argparse.ArgumentParser(description='Track RMT using FLO data on the FLO blockchain - https://flo.cash')
parser.add_argument('-r', '--reset', nargs='?', const=1, type=int, help='Purge existing db and rebuild it from scratch')
parser.add_argument('-rb', '--rebuild', nargs='?', const=1, type=int, help='Rebuild database')
parser.add_argument('--keywords', nargs='+', help='Filter transactions by keywords during rebuild')
parser.add_argument('--testnet', action='store_true', help='Use testnet config regardless of config.ini')
parser.add_argument('--collect', action='store_true', help='Re-collect all blocks, sort, and rebuild latestCache')
args = parser.parse_args()


# Determine network
NET = 'testnet' if args.testnet else config['DEFAULT'].get('NET', 'mainnet').strip().lower()
if NET not in ['mainnet', 'testnet']:
    logger.error("NET in config.ini must be either 'mainnet' or 'testnet'")
    sys.exit(1)

if NET == 'testnet':
    mysql_config.database_prefix = mysql_config.database_prefix + "test"




# Load server list based on NET
url_key = 'BLOCKBOOK_TESTNET_URL_LIST' if NET == 'testnet' else 'BLOCKBOOK_MAINNET_URL_LIST'
serverlist_raw = config['DEFAULT'].get(url_key, '').strip()

if not serverlist_raw:
    logger.error(f"{url_key} is not defined in config.ini. Exiting.")
    sys.exit(1)

serverlist = [url.strip().rstrip('/') for url in serverlist_raw.split(',')]


# Set APP_ADMIN depending on NET
APP_ADMIN = 'FNcvkz9PZNZM3HcxM1XTrVL4tgivmCkHp9' if NET == 'mainnet' else 'oWooGLbBELNnwq8Z5YmjoVjw8GhBGH3qSP'



# Initialize working Blockbook URL and determine its type
try:
    neturl, blockbook_type = initialize_neturl(serverlist)
    logger.info(f"Initialized Blockbook server: {neturl}")
    logger.info(f"Blockbook API type detected: {blockbook_type}")
except Exception as e:
    logger.error(f"Failed to initialize valid Blockbook server: {e}")
    sys.exit(1)


api_url = neturl

# Set websocket URI
websocket_uri = get_websocket_uri()

# SSE endpoint
tokenapi_sse_url = f"{neturl}/sse"

# HTTPS certificate verification
API_VERIFY = config['DEFAULT'].get('API_VERIFY', 'True').strip().lower() == 'true'

# Load ignore lists
IGNORE_BLOCK_LIST = [int(s) for s in config['DEFAULT'].get('IGNORE_BLOCK_LIST', '').split(',') if s.strip().isdigit()]
IGNORE_TRANSACTION_LIST = [txid.strip() for txid in config['DEFAULT'].get('IGNORE_TRANSACTION_LIST', '').split(',') if txid.strip()]


def init_system_db(startblock):
    # Initialize system.db
    session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)

    existing_entry = session.query(SystemData).filter(SystemData.attribute == 'lastblockscanned').first()

    if not existing_entry:
        session.add(SystemData(attribute='lastblockscanned', value=str(startblock - 1)))
        session.commit()
    else:
        logger.info(f"SystemData already initialized with lastblockscanned = {existing_entry.value}")

    session.close()

def init_lastestcache_db():
    # Initialize latest cache DB
    session = create_database_session_orm('system_dbs', {'db_name': "latestCache"}, LatestCacheBase)
    session.commit()
    session.close()

def init_storage_if_not_exist(reset=False, exclude_backups=False):
    """
    Initialize or reset the storage by creating or dropping/recreating system and cache databases.
    When reset=True, also drops all token and smart contract databases.

    Args:
        reset (bool): If True, resets the databases by dropping and recreating them.
        exclude_backups (bool): If True, skips dropping databases with '_backup' in their names.
    """
    def ensure_database_exists(database_name, init_function=None):
        engine = create_engine(f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/", echo=False)
        with engine.connect() as connection:
            # Drop and recreate the database if reset is True
            if reset:
                if exclude_backups and "_backup" in database_name:
                    logger.info(f"Skipping reset for backup database '{database_name}'.")
                else:
                    connection.execute(f"DROP DATABASE IF EXISTS `{database_name}`")
                    logger.info(f"Database '{database_name}' dropped for reset.")
            logger.info(f"Rechecking database '{database_name}' exists.")
            connection.execute(f"CREATE DATABASE IF NOT EXISTS `{database_name}`")
            logger.info(f"Database '{database_name}' ensured to exist.")
            # Run initialization function if provided
            if init_function:
                init_function()

    def drop_token_and_smartcontract_databases():
        """
        Drop all token and smart contract databases when reset is True.
        Token databases: Named with prefix {prefix}_{token_name}_db.
        Smart contract databases: Named with prefix {prefix}_{contract_name}_{contract_address}_db.
        """
        engine = create_engine(f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/", echo=False)
        with engine.connect() as connection:
            logger.info("Dropping all token and smart contract databases as part of reset.")
            result = connection.execute("SHOW DATABASES")
            databases = [row[0] for row in result.fetchall()]
            for db_name in databases:
                if db_name.startswith(f"{mysql_config.database_prefix}_") and "_db" in db_name:
                    if exclude_backups and "_backup" in db_name:
                        logger.info(f"Skipping backup database '{db_name}'.")
                        continue
                    if not db_name.endswith("system_db") and not db_name.endswith("latestCache_db"):
                        connection.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
                        logger.info(f"Dropped database '{db_name}'.")

    if reset:
        # Drop all token and smart contract databases
        drop_token_and_smartcontract_databases()

    # Initialize the system database
    system_db_name = f"{mysql_config.database_prefix}_system_db"

    if NET == "testnet":
        start_block = int(config['DEFAULT']['START_BLOCK_TESTNET'])
    else:
        start_block = int(config['DEFAULT']['START_BLOCK_MAINNET'])


    ensure_database_exists(system_db_name, lambda: init_system_db(start_block))

    # Initialize the latest cache database
    latest_cache_db_name = f"{mysql_config.database_prefix}_latestCache_db"
    ensure_database_exists(latest_cache_db_name, init_lastestcache_db)


def fetch_current_block_height():
    """
    Fetches the current block height using the correct response structure
    based on the blockbook_type.

    Returns:
        int: Current block height
    """
    current_index = -1

    while current_index == -1:
        try:
            response = newMultiRequest('latest-block')

            if blockbook_type == "address_indexer":
                current_index = int(response.get('blockheight', -1))
            elif blockbook_type == "blockbook_legacy":
                current_index = int(response.get('blockbook', {}).get('bestHeight', -1))
            else:
                logger.error(f"Unsupported blockbook_type: {blockbook_type}")
                return -1

            if current_index == -1:
                logger.warning("Block height not found in response. Will retry.")
                logger.debug(f"Response received: {response}")
                time.sleep(1)

            else:
                logger.info(f"Current block height fetched: {current_index}")

        except Exception as e:
            logger.error(f"Error fetching current block height: {e}")
            logger.info("Waiting 1 second before retrying...")
            time.sleep(1)

    return current_index



def backup_database_to_temp(original_db, backup_db):
    """
    Back up the original database schema and data into a new temporary backup database.

    :param original_db: Name of the original database.
    :param backup_db: Name of the backup database.
    :return: True if successful, False otherwise.
    """
    try:
        # Ensure the backup database exists
        engine = create_engine(f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/", echo=False)
        with engine.connect() as connection:
            logger.info(f"Creating backup database '{backup_db}'...")
            connection.execute(f"DROP DATABASE IF EXISTS `{backup_db}`")
            connection.execute(f"CREATE DATABASE `{backup_db}`")
            logger.info(f"Temporary backup database '{backup_db}' created successfully.")
            
            # Verify database creation
            result = connection.execute(f"SHOW DATABASES LIKE '{backup_db}'").fetchone()
            if not result:
                raise RuntimeError(f"Backup database '{backup_db}' was not created successfully.")
    except Exception as e:
        logger.error(f"Failed to create backup database '{backup_db}': {e}")
        return False

    try:
        # Reflect original database schema
        original_engine = create_engine(f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{original_db}", echo=False)
        backup_engine = create_engine(f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{backup_db}", echo=False)

        logger.info(f"Connecting to original database: {original_engine.url}")
        logger.info(f"Connecting to backup database: {backup_engine.url}")

        from sqlalchemy.schema import MetaData
        metadata = MetaData()
        metadata.reflect(bind=original_engine)
        metadata.create_all(bind=backup_engine)

        SessionOriginal = sessionmaker(bind=original_engine)
        SessionBackup = sessionmaker(bind=backup_engine)
        session_original = SessionOriginal()
        session_backup = SessionBackup()

        for table in metadata.sorted_tables:
            table_name = table.name
            logger.info(f"Copying data from table '{table_name}'...")
            data = session_original.execute(table.select()).fetchall()

            if data:
                column_names = [column.name for column in table.columns]
                data_dicts = [dict(zip(column_names, row)) for row in data]
                try:
                    session_backup.execute(table.insert(), data_dicts)
                    session_backup.commit()
                except Exception as e:
                    logger.error(f"Error copying data from table '{table_name}': {e}")
                    logger.debug(f"Data causing the issue: {data_dicts}")
                    raise

        session_original.close()
        session_backup.close()
        logger.info(f"Data successfully backed up to '{backup_db}'.")
        return True

    except Exception as e:
        logger.error(f"Error copying data to backup database '{backup_db}': {e}")
        return False



def backup_and_rebuild_latestcache(keywords=None):
    """
    Back up the current databases, reset all databases, and rebuild the latestCache database.

    :param keywords: List of keywords to filter transactions. If None, processes all transactions.
    """
    # Define database names
    latestcache_db = f"{mysql_config.database_prefix}_latestCache_db"
    latestcache_backup_db = f"{latestcache_db}_backup"
    system_db = f"{mysql_config.database_prefix}_system_db"
    system_backup_db = f"{system_db}_backup"

    # Step 1: Create backups
    logger.info("Creating backups of the latestCache and system databases...")
    if not backup_database_to_temp(latestcache_db, latestcache_backup_db):
        logger.error(f"Failed to create backup for latestCache database '{latestcache_db}'.")
        return
    if not backup_database_to_temp(system_db, system_backup_db):
        logger.error(f"Failed to create backup for system database '{system_db}'.")
        return

    # Step 2: Reset databases (skip backup databases during reset)
    logger.info("Resetting all databases except backups...")
    try:
        init_storage_if_not_exist(reset=True, exclude_backups=True)
    except Exception as e:
        logger.error(f"Failed to reset databases: {e}\n{traceback.format_exc()}")
        return

    # Step 3: Extract last block scanned from backup
    try:
        logger.info("Extracting last block scanned from backup system database...")
        backup_engine = create_engine(
            f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{system_backup_db}",
            echo=False,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 300}
        )
        with sessionmaker(bind=backup_engine)() as session:
            last_block_scanned_entry = session.query(SystemData).filter_by(attribute='lastblockscanned').first()
            if not last_block_scanned_entry:
                raise ValueError("No 'lastblockscanned' entry found in backup system database.")
            last_block_scanned = int(last_block_scanned_entry.value)
            logger.info(f"Last block scanned retrieved: {last_block_scanned}")
    except Exception as e:
        logger.error(f"Failed to retrieve lastblockscanned from backup system database: {e}\n{traceback.format_exc()}")
        return

    # Step 4: Reprocess blocks from the backup
    try:
        logger.info("Starting reprocessing of blocks from backup...")
        backup_engine = create_engine(
            f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{latestcache_backup_db}",
            echo=False,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 300}
        )

        SessionMaker = sessionmaker(bind=backup_engine)

        # Fetch all IDs first
        with SessionMaker() as session:
            all_ids = [row.id for row in session.query(LatestBlocks.id).order_by(LatestBlocks.blockNumber).all()]

        if not all_ids:
            logger.warning("No blocks found in backed-up latestCache database. Aborting rebuild.")
            return

        chunk_size = 10
        for i in range(0, len(all_ids), chunk_size):
            batch_ids = all_ids[i:i + chunk_size]

            session = SessionMaker()
            try:
                blocks = session.query(LatestBlocks).filter(
                    LatestBlocks.id.in_(batch_ids)
                ).order_by(LatestBlocks.blockNumber).all()

                for block_entry in blocks:
                    try:
                        blockinfo = json.loads(block_entry.jsonData)
                        block_number = blockinfo.get("height", block_entry.blockNumber)
                        block_hash = blockinfo.get("hash", block_entry.blockHash)

                        logger.info(f"Reprocessing block {block_number} with hash {block_hash}...")
                        processBlock(
                            blockindex=block_number,
                            blockhash=block_hash,
                            blockinfo=blockinfo,
                            keywords=keywords,
                            force_store=True
                        )
                    except Exception as e:
                        logger.error(f"Error processing block {block_entry.blockNumber}: {e}\n{traceback.format_exc()}")

            finally:
                try:
                    session.rollback()
                except sqlalchemy.exc.DBAPIError as e:
                    if e.connection_invalidated:
                        logger.warning("Connection was invalidated during rollback. Safe to ignore.")
                    else:
                        logger.error(f"Rollback failed unexpectedly: {e}\n{traceback.format_exc()}")
                        raise
                session.close()

        logger.info("Rebuild of latestCache database completed successfully.")

    except Exception as e:
        logger.error(f"Error during rebuild of latestCache database from backup: {e}\n{traceback.format_exc()}")
        return

    # Step 5: Update lastblockscanned in the new system database
    try:
        logger.info("Updating lastblockscanned in the new system database...")
        engine = create_engine(
            f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{system_db}",
            echo=False,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 300}
        )
        with sessionmaker(bind=engine)() as session:
            entry = session.query(SystemData).filter_by(attribute='lastblockscanned').first()
            if entry:
                entry.value = str(last_block_scanned)
            else:
                session.add(SystemData(attribute='lastblockscanned', value=str(last_block_scanned)))
            session.commit()
            logger.info(f"Updated lastblockscanned to {last_block_scanned}.")
    except Exception as e:
        logger.error(f"Failed to update lastblockscanned: {e}\n{traceback.format_exc()}")
        return

    # Step 6: Process remaining blocks
    try:
        logger.info("Processing remaining blocks...")
        current_block_height = fetch_current_block_height()
        for blockindex in range(last_block_scanned + 1, current_block_height + 1):
            if blockindex in IGNORE_BLOCK_LIST:
                continue
            logger.info(f"Processing block {blockindex} from the blockchain...")
            processBlock(blockindex=blockindex, keywords=keywords)
    except Exception as e:
        logger.error(f"Error processing remaining blocks: {e}\n{traceback.format_exc()}")

# Delete database and smartcontract directory if reset is set to 1
if args.reset == 1:
    logger.info("Resetting the database. ")
    init_storage_if_not_exist(reset=True)
else:
    init_storage_if_not_exist()

# Backup and rebuild latestCache and system.db if rebuild flag is set
if args.rebuild == 1:
    # Use the unified rebuild function with or without keywords
    backup_and_rebuild_latestcache(keywords=args.keywords if args.keywords else None)
    logger.info("Rebuild completed. Exiting...")
    sys.exit(0)


if args.collect:
    import blockcollector_random_3 as block_collector

    logger.info("Starting block collector in --collect mode...")

    # Determine start block from config
    if NET == "testnet":
        start_block = int(config['DEFAULT']['START_BLOCK_TESTNET'])
    else:
        start_block = int(config['DEFAULT']['START_BLOCK_MAINNET'])

    # Determine latest block height dynamically
    latest_block = fetch_current_block_height()
    if latest_block < 0:
        logger.error("Unable to fetch latest block height. Exiting.")
        sys.exit(1)

    logger.info(f"Collector range determined: {start_block} → {latest_block}")

    # Build collector ranges list
    collector_ranges = [(start_block, latest_block)]

    # Run the collector with your dynamic range
    try:
        asyncio.run(block_collector.main(custom_ranges=collector_ranges))
    except Exception as e:
        logger.error(f"Block collector failed: {e}")
        sys.exit(1)

    logger.info("Renaming collector table to latestBlocks...")

    # Connect to the latestCache DB and rename table
    latest_cache_db_name = f"{mysql_config.database_prefix}_latestCache_db"
    engine_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{latest_cache_db_name}"
    engine = create_engine(engine_url, echo=False)

    with engine.connect() as connection:
        connection.execute("DROP TABLE IF EXISTS latestBlocks")
        connection.execute("RENAME TABLE latestBlocksCollectorSorted TO latestBlocks")

    logger.info("Collector data renamed to latestBlocks.")

    # Now run the rebuild
    backup_and_rebuild_latestcache(keywords=args.keywords if args.keywords else None)

    logger.info("✅ Collection and rebuild complete. Exiting.")
    sys.exit(0)


# Determine API source for block and transaction information
if __name__ == "__main__":
    # MAIN LOGIC STARTS
    # scan from the latest block saved locally to latest network block

    scanBlockchain()

    logger.debug("Completed first scan")

    # At this point the script has updated to the latest block
    # Now we connect to Blockbook's websocket API to get information about the latest blocks
    # Neturl is the URL for Blockbook API whose websocket endpoint is being connected to

    asyncio.get_event_loop().run_until_complete(connect_to_websocket(websocket_uri)) 
    