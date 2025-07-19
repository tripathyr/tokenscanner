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

# === Third-Party Library Imports ===
import arrow
import pyflo
import pymysql
import requests
import websockets
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from sqlalchemy import (
    BigInteger, Column, MetaData, create_engine, func, and_
)
from sqlalchemy.exc import (
    IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text

# === Project-Specific Imports ===
import parsing
from parsing import perform_decimal_operation
from statef_processing import process_stateF
from util_rollback import rollback_to_block
from models import (
    ActiveContracts, ActiveTable, ConsumedInfo, ConsumedTable, ContractAddressMapping,
    ContractBase, ContractDeposits, ContractParticipants,
    ContractStructure, ContractTransactionHistory,
    DatabaseTypeMapping, LatestBlocks, LatestCacheBase, LatestTransactions,
    RejectedContractTransactionHistory, RejectedTransactionHistory,
    SystemBase, SystemData, TimeActions, TokenAddressMapping, TokenBase,
    TokenContractAssociation, TransferLogs, TransactionHistory
)


# Disable the InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

#ENHANCEMENTS START


def update_dynamic_swap_price(session, price, timestamp, blockheight):
    new_metadata = {
        "price": float(price),
        "published_time": int(timestamp),
        "blockNumber": int(blockheight)  #
    }

    record = session.query(ContractStructure).filter_by(attribute="dynamicPriceUpdate").first()

    if record:
        try:
            current_metadata = json.loads(record.value)
            if int(current_metadata.get("blockNumber", -1)) == blockheight:  
                logger.info(f"Dynamic price already recorded for blockheight {blockheight}, skipping update.")
                return  # Skip update
        except (ValueError, json.JSONDecodeError):
            logger.warning("Malformed dynamicPriceUpdate JSON. Proceeding to overwrite.")

        record.value = json.dumps(new_metadata)
        record.blockNumber = blockheight
    else:
            session.add(ContractStructure(
            attribute="dynamicPriceUpdate",
            value=json.dumps(new_metadata),
            index=0,
            blockNumber=blockheight
        ))


def get_dynamic_swap_price(contract_name, contract_address):
    while True:
        try:
            session = create_database_session_orm(
                type='smart_contract',
                parameters={'contract_name': contract_name, 'contract_address': contract_address},
                base=ContractBase
            )

            record = session.query(ContractStructure).filter_by(attribute="dynamicPriceUpdate").first()
            session.close()

            if record:
                try:
                    return json.loads(record.value)
                except json.JSONDecodeError:
                    logger.warning(f"[{contract_name}] Malformed dynamicPriceUpdate JSON.")
                    return None

            return None

        except Exception as e:
            logger.warning(f"[{contract_name}] Failed to fetch dynamic price. Retrying in {DB_RETRY_TIMEOUT} seconds. Reason: {e}")
            time.sleep(DB_RETRY_TIMEOUT)




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
    """
    Adds a new block entry to the RecentBlocks table if it does not already exist.
    Maintains only the latest 1000 blocks by removing the oldest entry if necessary.

    :param block_number: The block number of the new block.
    :param block_hash: The hash of the new block.
    """
    while True:
        try:
            conn = create_database_connection('latest_cache', {'db_name': "latestCache"})
            break
        except Exception as e:
            logger.error(f"Error connecting to database: {e}")
            time.sleep(DB_RETRY_TIMEOUT)

    try:
        # Check if the block already exists
        existing_block = conn.execute(
            'SELECT * FROM RecentBlocks WHERE blockNumber = %s', (block_number,)
        ).fetchone()
        if existing_block:
            logger.info(f"Block {block_number} already exists. No action taken.")
            return

        # Add the new block entry
        conn.execute(
            'INSERT INTO RecentBlocks (blockNumber, blockHash) VALUES (%s, %s)',
            (block_number, block_hash)
        )
        logger.info(f"Added hash of block {block_number} with hash {block_hash} to detect reorganization of chain.")

        # Check the count of blocks
        block_count = conn.execute('SELECT COUNT(*) FROM RecentBlocks').fetchone()[0]

        # If more than 1000 blocks, delete the oldest entries
        if block_count > 1000:
            # Determine how many blocks to remove
            excess_count = block_count - 1000
            oldest_blocks = conn.execute(
                'SELECT id FROM RecentBlocks ORDER BY id ASC LIMIT %s',
                (excess_count,)
            ).fetchall()
            for block in oldest_blocks:
                conn.execute('DELETE FROM RecentBlocks WHERE id = %s', (block[0],))
            logger.info(
                f"Deleted {excess_count} oldest block(s) to maintain the limit of 1000 blocks."
            )

    except Exception as e:
        logger.error(f"Database error: {e}")
    finally:
        conn.close()


def detect_reorg():
    """
    Detects a blockchain reorganization by comparing local block hashes
    against the hashes from the Blockbook API.
    Returns the fork point block number if a fork is detected, otherwise None.
    """
    API_VERIFY = True
    ROLLBACK_BUFFER = 2

    try:
        logger.info("Fetching last scanned block from system database...")
        conn = create_database_connection('system_dbs', {'db_name': 'system'})
        result = conn.execute("SELECT value FROM systemData WHERE attribute = %s", ('lastblockscanned',))
        row = result.fetchone()
        conn.close()

        if not row:
            logger.error("No last scanned block found.")
            return None

        try:
            latest_block = int(row[0])
        except ValueError:
            logger.error("Invalid block number format in systemData.")
            return None

        logger.info(f"Last scanned block: {latest_block}")

        block_number = latest_block
        while block_number > 0:
            # Fetch local hash
            conn = create_database_connection('latest_cache', {'db_name': 'latestCache'})
            result = conn.execute("SELECT blockHash FROM RecentBlocks WHERE blockNumber = %s", (block_number,))
            local_row = result.fetchone()
            conn.close()

            if not local_row:
                logger.error(f"No local block data for height {block_number}.")
                return None

            local_hash = local_row[0]
            logger.info(f"Local hash at block {block_number}: {local_hash}")

            # Fetch remote hash from Blockbook
            try:
                if blockbook_type == "blockbook_legacy":
                    api_url = f"{neturl}/api/v2/block-index/{block_number}"
                    hash_key = "blockHash"
                elif blockbook_type == "address_indexer":
                    api_url = f"{neturl}/api/blockheight/{block_number}"
                    hash_key = "hash"
                else:
                    logger.error(f"Unknown blockbook_type: {blockbook_type}")
                    return None

                logger.info(f"Querying Blockbook API: {api_url}")
                response = requests.get(api_url, verify=API_VERIFY, timeout=RETRY_TIMEOUT_SHORT)
                response.raise_for_status()
                response_json = response.json()

                remote_hash = response_json.get(hash_key)
                if not remote_hash:
                    logger.error(f"Missing expected key '{hash_key}' in Blockbook response.")
                    return None

                logger.info(f"Blockbook hash at block {block_number}: {remote_hash}")

                # Compare hashes
                if local_hash != remote_hash:
                    logger.warning(f"Reorg detected at block {block_number}!")
                    logger.warning(f"Local hash: {local_hash}")
                    logger.warning(f"Remote hash: {remote_hash}")
                    return block_number - ROLLBACK_BUFFER

                logger.info(f"Block {block_number} matches. No reorg at this level.")
                return None  # No reorg detected

            except requests.RequestException as e:
                logger.error(f"Network/API error while checking block {block_number}: {e}")
                time.sleep(2)
                continue

            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                return None

    except Exception as e:
        logger.error(f"Error during reorg detection: {e}")
        return None




#ENHANCEMENTS END




RETRY_TIMEOUT_LONG = 30 * 60 # 30 mins
RETRY_TIMEOUT_SHORT = 60 # 1 min
DB_RETRY_TIMEOUT = 60 # 60 seconds





def process_committee_flodata(flodata):
    flo_address_list = []
    try:
        contract_committee_actions = flodata['token-tracker']['contract-committee']
    except KeyError:
        logger.info('Flodata related to contract committee')
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

"""  ?NOT USED?
def refresh_committee_list_old(admin_flo_id, api_url, blocktime):
    response = requests.get(f'{api_url}api/address/{admin_flo_id}', verify=API_VERIFY)
    if response.status_code == 200:
        response = response.json()
    else:
        logger.info('Response from the Blockbook API failed')
        sys.exit(0)

    committee_list = []
    response['transactions'].reverse()
    for idx, transaction in enumerate(response['transactions']):
        transaction_info = requests.get(f'{api_url}api/tx/{transaction}', verify=API_VERIFY)
        if transaction_info.status_code == 200:
            transaction_info = transaction_info.json()
            if transaction_info['vin'][0]['addresses'][0]==admin_flo_id and transaction_info['blocktime']<=blocktime:
                try:
                    tx_flodata = json.loads(transaction_info['floData'])
                    committee_list += process_committee_flodata(tx_flodata)
                except:
                    continue
    return committee_list
"""


def refresh_committee_list(admin_flo_id, api_url, blocktime):
    committee_list = []
    latest_param = 'true'
    mempool_param = 'false'
    init_id = None

    def process_transaction(transaction_info):

        normalize_transaction_data(transaction_info)

        if 'isCoinBase' in transaction_info or transaction_info['vin'][0]['addresses'][0] != admin_flo_id or transaction_info['blocktime'] > blocktime:
            return
        try:
            tx_flodata = json.loads(transaction_info['floData'])
            committee_list.extend(process_committee_flodata(tx_flodata))
        except:
            pass

    def send_api_request(url):
        while True:
            try:
                response = requests.get(url, verify=API_VERIFY)
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.info(f'Response from the Blockbook API failed. Retry in {RETRY_TIMEOUT_SHORT}s')
                    #sys.exit(0)
                    time.sleep(RETRY_TIMEOUT_SHORT)
            except:
                logger.info(f'Fetch from the Blockbook API failed. Retry in {RETRY_TIMEOUT_LONG}s...')
                time.sleep(RETRY_TIMEOUT_LONG)

    url = f'{api_url}api/address/{admin_flo_id}?details=txs'
    response = send_api_request(url)
    for transaction_info in response.get('txs', []):
        process_transaction(transaction_info)

    # while 'incomplete' in response:
    #     url = f'{api_url}api/address/{admin_flo_id}/txs?latest={latest_param}&mempool={mempool_param}&before={init_id}'
    #     response = send_api_request(url)
    #     for transaction_info in response.get('items', []):
    #         process_transaction(transaction_info)
    #     if 'incomplete' in response:
    #         init_id = response['initItem']

    return committee_list


def find_sender_receiver(transaction_data):

    normalize_transaction_data(transaction_data)

    # Create vinlist and outputlist
    vinlist = []
    querylist = []

    #totalinputval = 0
    #inputadd = ''

    # todo Rule 40 - For each vin, find the feeding address and the fed value. Make an inputlist containing [inputaddress, n value]
    for vin in transaction_data["vin"]:
        vinlist.append([vin["addresses"][0], float(vin["value"])])

    totalinputval = float(transaction_data["valueIn"])

    # todo Rule 41 - Check if all the addresses in a transaction on the input side are the same
    for idx, item in enumerate(vinlist):
        if idx == 0:
            temp = item[0]
            continue
        if item[0] != temp:
            logger.info(f"System has found more than one address as part of vin. Transaction {transaction_data['txid']} is rejected")
            return 0

    inputlist = [vinlist[0][0], totalinputval]
    inputadd = vinlist[0][0]

    # todo Rule 42 - If the number of vout is more than 2, reject the transaction
    if len(transaction_data["vout"]) > 2:
        logger.info(f"System has found more than 2 address as part of vout. Transaction {transaction_data['txid']} is rejected")
        return 0

    # todo Rule 43 - A transaction accepted by the system has two vouts, 1. The FLO address of the receiver
    #      2. Flo address of the sender as change address.  If the vout address is change address, then the other adddress
    #     is the recevier address

    outputlist = []
    addresscounter = 0
    inputcounter = 0
    for obj in transaction_data["vout"]:
        addresscounter = addresscounter + 1
        if inputlist[0] == obj["scriptPubKey"]["addresses"][0]:
            inputcounter = inputcounter + 1
            continue
        outputlist.append([obj["scriptPubKey"]["addresses"][0], obj["value"]])

    if addresscounter == inputcounter:
        outputlist = [inputlist[0]]
    elif len(outputlist) != 1:
        logger.info(f"Transaction's change is not coming back to the input address. Transaction {transaction_data['txid']} is rejected")
        return 0
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
    
    # Construct database name and URL
    if type == 'token':
        database_name = f"{mysql_config.database_prefix}_{parameters['token_name']}_db"
    elif type == 'smart_contract':
        database_name = f"{mysql_config.database_prefix}_{parameters['contract_name']}_{parameters['contract_address']}_db"
    else:
        raise ValueError(f"Unsupported database type: {type}")

    # Create a temporary engine to check database existence
    engine_url = f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{database_name}"
    try:
        engine = create_engine(engine_url, echo=False)
        connection = engine.connect()
        connection.close()
        return True
    except OperationalError:
        return False


def create_database_connection(type, parameters=None):
    """
    Creates a database connection using MySQL credentials from the config file.

    Args:
        type (str): Type of the database ('token', 'smart_contract', 'system_dbs', 'latest_cache').
        parameters (dict, optional): Parameters for dynamic database names.

    Returns:
        connection: SQLAlchemy connection object.
    """
    
    # Map database type to naming logic
    database_mapping = {
        'token': lambda: f"{mysql_config.database_prefix}_{parameters['token_name']}_db",
        'smart_contract': lambda: f"{mysql_config.database_prefix}_{parameters['contract_name']}_{parameters['contract_address']}_db",
        'system_dbs': lambda: f"{mysql_config.database_prefix}_system_db",
        'latest_cache': lambda: f"{mysql_config.database_prefix}_latestCache_db"
    }

    # Validate and construct the database name
    if type not in database_mapping:
        raise ValueError(f"Unknown database type: {type}")
    database_name = database_mapping[type]()

    # Create the database engine
    echo_setting = True if type in ['token', 'smart_contract'] else False
    engine = create_engine(f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{database_name}", echo=echo_setting)

    # Connect to the database
    return engine.connect()



from sqlalchemy.exc import SQLAlchemyError

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
            else:
                logger.info(f"Database '{database_name}' already exists.")

        # Connect to the specific database and initialize tables
        logger.info(f"Connecting to database '{database_name}'...")
        engine = create_engine(
            f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{database_name}",
            connect_args={"connect_timeout": DB_RETRY_TIMEOUT},
            echo=False
        )
        base.metadata.create_all(bind=engine)  # Create tables if they do not exist
        session = sessionmaker(bind=engine)()

        logger.info(f"Session created for database '{database_name}' successfully.")
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


def add_transaction_history(token_name, sourceFloAddress, destFloAddress, transferAmount, blockNumber, blockHash, blocktime, transactionHash, jsonData, transactionType, parsedFloData):
    while True:
        try:
            session = create_database_session_orm('token', {'token_name': token_name}, TokenBase)
            blockchainReference = neturl + 'tx/' + transactionHash
            session.add(TransactionHistory(
                                            sourceFloAddress=sourceFloAddress, 
                                            destFloAddress=destFloAddress,
                                            transferAmount=transferAmount,
                                            blockNumber=blockNumber,
                                            blockHash=blockHash,
                                            time=blocktime,
                                            transactionHash=transactionHash,
                                            blockchainReference=blockchainReference,
                                            jsonData=jsonData,
                                            transactionType=transactionType,
                                            parsedFloData=parsedFloData
                                        ))
            session.commit()
            session.close()
            break
        except:
            logger.info(f"Unable to connect to 'token({token_name})' database... retrying in {DB_RETRY_TIMEOUT} seconds")
            time.sleep(DB_RETRY_TIMEOUT)


def add_contract_transaction_history(contract_name, contract_address, transactionType, transactionSubType, sourceFloAddress, destFloAddress, transferAmount, blockNumber, blockHash, blocktime, transactionHash, jsonData, parsedFloData):
    while True:
        try:
            session = create_database_session_orm('smart_contract', {'contract_name': f"{contract_name}", 'contract_address': f"{contract_address}"}, ContractBase)
            blockchainReference = neturl + 'tx/' + transactionHash
            session.add(ContractTransactionHistory(transactionType=transactionType,
                                                    sourceFloAddress=sourceFloAddress,
                                                    destFloAddress=destFloAddress,
                                                    transferAmount=transferAmount,
                                                    blockNumber=blockNumber,
                                                    blockHash=blockHash,
                                                    time=blocktime,
                                                    transactionHash=transactionHash,
                                                    blockchainReference=blockchainReference,
                                                    jsonData=jsonData,
                                                    parsedFloData=parsedFloData
                                                    ))
            session.commit()
            session.close()
            break
        except:
            logger.info(f"Unable to connect to 'smart_contract({contract_name})' database... retrying in {DB_RETRY_TIMEOUT} seconds")
            time.sleep(DB_RETRY_TIMEOUT)


import traceback

def rejected_transaction_history(transaction_data, parsed_data, sourceFloAddress, destFloAddress, rejectComment):
    while True:
        try:
            # Create database session
            session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)

            # Construct blockchain reference URL
            blockchainReference = f"{neturl.rstrip('/')}/tx/{transaction_data['txid']}"
            
            # Add rejected transaction to the database
            session.add(RejectedTransactionHistory(
                tokenIdentification=parsed_data['tokenIdentification'],
                sourceFloAddress=sourceFloAddress,
                destFloAddress=destFloAddress,
                transferAmount=parsed_data['tokenAmount'],
                blockNumber=transaction_data['blockheight'],
                blockHash=transaction_data['blockhash'],
                time=transaction_data['time'],
                transactionHash=transaction_data['txid'],
                blockchainReference=blockchainReference,
                jsonData=json.dumps(transaction_data),
                rejectComment=rejectComment,
                transactionType=parsed_data['type'],
                parsedFloData=json.dumps(parsed_data)
            ))
            
            # Commit the session
            session.commit()

            # Close the session
            session.close()
            break

        except Exception as e:
            # Log the exception message and traceback
            logger.error(f"Error while processing rejected transaction: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")

            # Retry logic
            logger.info(f"Retrying connection to 'system' database in {DB_RETRY_TIMEOUT} seconds...")
            time.sleep(DB_RETRY_TIMEOUT)



def rejected_contract_transaction_history(transaction_data, parsed_data, transactionType, contractAddress, sourceFloAddress, destFloAddress, rejectComment):
    while True:
        try:
            session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
            blockchainReference = neturl + 'tx/' + transaction_data['txid']
            session.add(RejectedContractTransactionHistory(transactionType=transactionType,
                                                            contractName=parsed_data['contractName'],
                                                            contractAddress=contractAddress,
                                                            sourceFloAddress=sourceFloAddress,
                                                            destFloAddress=destFloAddress,
                                                            transferAmount=None,
                                                            blockNumber=transaction_data['blockheight'],
                                                            blockHash=transaction_data['blockhash'],
                                                            time=transaction_data['time'],
                                                            transactionHash=transaction_data['txid'],
                                                            blockchainReference=blockchainReference,
                                                            jsonData=json.dumps(transaction_data),
                                                            rejectComment=rejectComment,
                                                            parsedFloData=json.dumps(parsed_data)))
            session.commit()
            session.close()    
            break
        except:
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
    while True:
        try:
            # check contract address mapping db if the address is present, and return True or False based on that 
            session = create_database_session_orm('system_dbs', {'db_name':'system'}, SystemBase)

            # contract_number = session.query(func.sum(ContractAddressMapping.contractAddress)).filter(ContractAddressMapping.contractAddress == floAddress).all()[0][0]
            query_data = session.query(ContractAddressMapping.contractAddress).filter(ContractAddressMapping.contractAddress == floAddress).all()
            contract_number = sum(Decimal(f"{amount[0]}") if amount[0] is not None else Decimal(0) for amount in query_data)
            session.close()

            if contract_number is None or contract_number==0: 
                return False
            else:
                return True
        except:
            logger.info(f"Unable to connect to 'system' database... retrying in {DB_RETRY_TIMEOUT} seconds")
            time.sleep(DB_RETRY_TIMEOUT)

def fetchDynamicSwapPrice(contractStructure, blockinfo):
    """
    Fetch the latest dynamic swap price from the blockchain.

    :param contractStructure: Dictionary containing contract details (oracle_address, contractName, contractAddress, price).
    :param blockinfo: Block data for the current block being processed.
    :return: The latest price as a float.
    """
    oracle_address = contractStructure['oracle_address']
    api_endpoint = f'{api_url}api/address/{oracle_address}?details=txs'

    while True:
        try:
            # Fetch transactions for the oracle address
            response = requests.get(api_endpoint, verify=API_VERIFY, timeout=10)
            if response.status_code == 200:
                response = response.json()

                # If no transactions are found, return the default price
                if not response.get('txs', []):
                    return float(contractStructure['price'])

                # Process transactions to find the latest price update
                for transaction in response['txs']:
                    # Normalize transaction data
                    normalize_transaction_data(transaction)

                    # Extract floData, defaulting to an empty string if not present
                    floData = transaction.get('floData', '')

                    # Skip transactions with block time greater than or equal to the current block time
                    if transaction['time'] >= blockinfo['time']:
                        continue

                    # Validate and parse transaction data
                    try:
                        sender_address, receiver_address = find_sender_receiver(transaction)
                        if sender_address != oracle_address or receiver_address != contractStructure['contractAddress']:
                            continue

                        # Parse floData and check for price update structure
                        floData = json.loads(floData)
                        if (
                            floData.get('price-update', {}).get('contract-name') == contractStructure['contractName'] and
                            floData['price-update'].get('contract-address') == contractStructure['contractAddress']
                        ):
                            return float(floData['price-update']['price'])
                    except (AssertionError, KeyError, ValueError, json.JSONDecodeError):
                        # Ignore invalid transactions or parsing errors
                        continue

            else:
                logger.info(f'API error fetchDynamicSwapPrice (HTTP {response.status_code}). Retrying in {RETRY_TIMEOUT_LONG}s...')
                time.sleep(RETRY_TIMEOUT_LONG)

        except requests.RequestException as e:
            logger.info(f'API request error in fetchDynamicSwapPrice: {e}. Retrying in {RETRY_TIMEOUT_LONG}s...')
            time.sleep(RETRY_TIMEOUT_LONG)
        except Exception as e:
            logger.error(f'Unhandled exception in fetchDynamicSwapPrice: {e}. Retrying in {RETRY_TIMEOUT_LONG}s...')
            time.sleep(RETRY_TIMEOUT_LONG)

    # Return the default price if no valid transactions are found
    return float(contractStructure['price'])


def get_blockhash_by_height(blockindex):
    """
    Fetches the block hash for a given height using the correct Blockbook API,
    depending on the blockbook_type ('address_indexer' or 'blockbook_legacy').

    Returns:
        str: Block hash
    Raises:
        Exception: If the request fails or hash is not found
    """
    api_path = ""
    expected_key = ""

    if blockbook_type == "blockbook_legacy":
        api_path = f"v2/block-index/{blockindex}"
        expected_key = "blockHash"
    elif blockbook_type == "address_indexer":
        api_path = f"blockheight/{blockindex}"
        expected_key = "hash"
    else:
        raise Exception(f"Unsupported blockbook_type: {blockbook_type}")

    while True:
        try:
            response = newMultiRequest(api_path)
            if expected_key in response:
                return response[expected_key]
            else:
                logger.warning(f"Missing '{expected_key}' in response for block {blockindex}. Retrying...")
        except Exception as e:
            logger.warning(f"Error fetching block {blockindex} from API '{api_path}': {e}. Retrying...")

        time.sleep(1)



def processBlock(blockindex=None, blockhash=None, blockinfo=None, keywords=None):
    """
    Processes a block with optional keyword filtering.

    :param blockindex: The block index (height) to process.
    :param blockhash: The block hash to process.
    :param blockinfo: The block data to process. If not provided, it will be fetched.
    :param keywords: List of keywords to filter transactions. If None, processes all transactions.
    """
    global args

    while True:  # Loop to handle rollbacks
        # Retrieve block information if not already provided
        if blockinfo is None:
            if blockindex is not None and blockhash is None:
                logger.info(f'Processing block {blockindex}')
                blockhash = get_blockhash_by_height(blockindex)

            blockinfo = newMultiRequest(f"block/{blockhash}")
            logger.info(f"blockinfo = {blockinfo}")
            
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

        # Ensure processing continues even if the block exists
        if block_already_exists:
            logger.info(f"Block {blockindex} already exists but will continue processing its transactions.")

        # Detect reorg every 10 blocks
        if not args.rebuild and blockindex is not None and blockindex % 10 == 0:
            fork_point = detect_reorg()
            if fork_point is not None:
                logger.warning(f"Blockchain reorganization detected! Fork point at block {fork_point}.")

                # Handle rollback
                rollback_to_block(fork_point)

                # Restart processing from fork point
                blockindex = fork_point + 1
                blockhash = None
                blockinfo = None

                # Fetch new blockhash and blockinfo for the updated blockindex
                blockhash = get_blockhash_by_height(blockindex)
                blockinfo = newMultiRequest(f"block/{blockhash}")

                # Normalize the new block transactions
                normalize_block_transactions(blockinfo)

                logger.info(f"Rollback complete. Restarting processing from block {blockindex} with hash {blockhash}.")
                continue  # Restart loop from updated fork point

        # Perform expiry and deposit trigger checks
        checkLocal_time_expiry_trigger_deposit(blockinfo)

        # Process transactions in the block
        acceptedTxList = []
        logger.info("Before tx loop")

        for transaction_data in blockinfo["txs"]:
            transaction = transaction_data["txid"]

            try:
                text = transaction_data["floData"]
            except KeyError:
                text = ''
            text = text.replace("\n", " \n ")
            returnval = None
            parsed_data = parsing.parse_flodata(text, blockinfo, config['DEFAULT']['NET'])
            if parsed_data['type'] not in ['noise', None, '']:
                logger.info(f"Processing transaction {transaction}")
                logger.info(f"flodata {text} is parsed to {parsed_data}")
                returnval = processTransaction(transaction_data, parsed_data, blockinfo)

            if returnval == 1:
                acceptedTxList.append(transaction)
            elif returnval == 0:
                logger.info(f"Transfer for the transaction {transaction} is illegitimate. Moving on")

        logger.info("Completed tx loop")

        if len(acceptedTxList) > 0:
            tempinfo = blockinfo['txs'].copy()
            for tx in blockinfo['txs']:
                if tx['txid'] not in acceptedTxList:
                    tempinfo.remove(tx)
            blockinfo['txs'] = tempinfo

            try:
                updateLatestBlock(blockinfo)  # Core logic to update
                logger.info(f"Successfully updated latest block: {blockinfo['height']}")
            except Exception as e:
                logger.error(f"Error updating latest block {blockinfo['height']} in updateLatestBlock: {e}")

        try:
            session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
            entry = session.query(SystemData).filter(SystemData.attribute == 'lastblockscanned').first()
            entry.value = str(blockinfo['height'])
            session.commit()
            session.close()
        except Exception as e:
            logger.error(f"Error connecting to 'system' database: {e}. Retrying in {DB_RETRY_TIMEOUT} seconds")
            time.sleep(DB_RETRY_TIMEOUT)

        break  # Exit loop after processing is complete without a rollback




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
    
    # Apply normalization to handle old and new formats of transaction data
    if transaction_data:
        normalize_transaction_data(transaction_data)

    # provide default transactionType value
    if transactionType is None:
        try:
            transactionType=parsed_data['type']
        except:
            logger.info("This is a critical error. Please report to developers")

    while True:
        try:
            session = create_database_session_orm('token', {'token_name': f"{tokenIdentification}"}, TokenBase)
            break
        except:
            time.sleep(DB_RETRY_TIMEOUT)

    tokenAmount = float(tokenAmount)
    if isInfiniteToken == True:
        # Make new entry 
        receiverAddress_details = session.query(ActiveTable).filter(ActiveTable.address==outputAddress, ActiveTable.addressBalance!=None).first()
        if receiverAddress_details is None:
            addressBalance = tokenAmount
        else:
            addressBalance =  perform_decimal_operation('addition', receiverAddress_details.addressBalance, tokenAmount)
            receiverAddress_details.addressBalance = None
        session.add(ActiveTable(address=outputAddress, consumedpid='1', transferBalance=tokenAmount, addressBalance=addressBalance, blockNumber=blockinfo['height']))

        add_transaction_history(token_name=tokenIdentification, sourceFloAddress=inputAddress, destFloAddress=outputAddress, transferAmount=tokenAmount, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=transactionType, parsedFloData=json.dumps(parsed_data))
        session.commit()
        session.close()
        return 1

    else:
        # availableTokens = session.query(func.sum(ActiveTable.transferBalance)).filter_by(address=inputAddress).all()[0][0]

        query_data = session.query(ActiveTable.transferBalance).filter_by(address=inputAddress).all()
        availableTokens = float(sum(Decimal(f"{amount[0]}") if amount[0] is not None else Decimal(0) for amount in query_data))
        logger.info(f"The sender address {inputAddress} owns {availableTokens} {tokenIdentification.upper()} tokens")

        commentTransferAmount = float(tokenAmount)
        if availableTokens is None:
            logger.info(f"The sender address {inputAddress} doesn't own any {tokenIdentification.upper()} tokens")
            session.close()
            return 0

        elif availableTokens < commentTransferAmount:
            logger.info("The transfer amount is more than the user balance\nThis transaction will be discarded\n")
            session.close()
            return 0

        elif availableTokens >= commentTransferAmount:
            logger.info(f"System has accepted transfer of {commentTransferAmount} {tokenIdentification.upper()}# from {inputAddress} to {outputAddress}")
            table = session.query(ActiveTable).filter(ActiveTable.address == inputAddress).all()
            pidlst = []
            checksum = 0
            for row in table:
                if checksum >= commentTransferAmount:
                    break
                pidlst.append([row.id, row.transferBalance])
                checksum = perform_decimal_operation('addition', checksum, row.transferBalance)

            if checksum == commentTransferAmount:
                consumedpid_string = ''
                # Update all pids in pidlist's transferBalance to 0
                lastid = session.query(ActiveTable)[-1].id
                piddict = {}
                for piditem in pidlst:
                    entry = session.query(ActiveTable).filter(ActiveTable.id == piditem[0]).all()
                    consumedpid_string = consumedpid_string + '{},'.format(piditem[0])
                    piddict[piditem[0]] = piditem[1]
                    session.add(TransferLogs(sourceFloAddress=inputAddress, destFloAddress=outputAddress,
                                            transferAmount=entry[0].transferBalance, sourceId=piditem[0],
                                            destinationId=lastid + 1,
                                            blockNumber=blockinfo['height'], time=blockinfo['time'],
                                            transactionHash=transaction_data['txid']))
                    entry[0].transferBalance = 0

                if len(consumedpid_string) > 1:
                    consumedpid_string = consumedpid_string[:-1]

                # Make new entry
                receiverAddress_details = session.query(ActiveTable).filter(ActiveTable.address==outputAddress, ActiveTable.addressBalance!=None).first()
                if receiverAddress_details is None:
                    addressBalance = commentTransferAmount
                else:
                    addressBalance = perform_decimal_operation('addition', receiverAddress_details.addressBalance, commentTransferAmount)
                    receiverAddress_details.addressBalance = None
                session.add(ActiveTable(address=outputAddress, consumedpid=str(piddict), transferBalance=commentTransferAmount, addressBalance=addressBalance, blockNumber=blockinfo['height']))

                senderAddress_details = session.query(ActiveTable).filter_by(address=inputAddress).order_by(ActiveTable.id.desc()).first()
                senderAddress_details.addressBalance = perform_decimal_operation('subtraction', senderAddress_details.addressBalance, commentTransferAmount )

                # Migration
                # shift pid of used utxos from active to consumed
                for piditem in pidlst:
                    # move the parentids consumed to consumedpid column in both activeTable and consumedTable
                    entries = session.query(ActiveTable).filter(ActiveTable.parentid == piditem[0]).all()
                    process_pids(entries, session, piditem)

                    entries = session.query(ConsumedTable).filter(ConsumedTable.parentid == piditem[0]).all()
                    process_pids(entries, session, piditem)

                    # move the pids consumed in the transaction to consumedTable and delete them from activeTable
                    session.execute('INSERT INTO consumedTable (id, address, parentid, consumedpid, transferBalance, addressBalance, orphaned_parentid, blockNumber) SELECT id, address, parentid, consumedpid, transferBalance, addressBalance, orphaned_parentid, blockNumber FROM activeTable WHERE id={}'.format(piditem[0]))
                    session.execute('DELETE FROM activeTable WHERE id={}'.format(piditem[0]))
                    session.commit()
                session.commit()

            elif checksum > commentTransferAmount:
                consumedpid_string = ''
                # Update all pids in pidlist's transferBalance
                lastid = session.query(ActiveTable)[-1].id
                piddict = {}
                for idx, piditem in enumerate(pidlst):
                    entry = session.query(ActiveTable).filter(ActiveTable.id == piditem[0]).all()
                    if idx != len(pidlst) - 1:
                        session.add(TransferLogs(sourceFloAddress=inputAddress, destFloAddress=outputAddress,
                                                transferAmount=entry[0].transferBalance, sourceId=piditem[0],
                                                destinationId=lastid + 1,
                                                blockNumber=blockinfo['height'], time=blockinfo['time'],
                                                transactionHash=transaction_data['txid']))
                        entry[0].transferBalance = 0
                        piddict[piditem[0]] = piditem[1]
                        consumedpid_string = consumedpid_string + '{},'.format(piditem[0])
                    else:
                        session.add(TransferLogs(sourceFloAddress=inputAddress, destFloAddress=outputAddress,
                                                transferAmount=perform_decimal_operation('subtraction', piditem[1], perform_decimal_operation('subtraction', checksum, commentTransferAmount)),
                                                sourceId=piditem[0],
                                                destinationId=lastid + 1,
                                                blockNumber=blockinfo['height'], time=blockinfo['time'],
                                                transactionHash=transaction_data['txid']))
                        entry[0].transferBalance = perform_decimal_operation('subtraction', checksum, commentTransferAmount)

                if len(consumedpid_string) > 1:
                    consumedpid_string = consumedpid_string[:-1]

                # Make new entry
                receiverAddress_details = session.query(ActiveTable).filter(ActiveTable.address==outputAddress, ActiveTable.addressBalance!=None).first()
                if receiverAddress_details is None:
                    addressBalance = commentTransferAmount
                else:
                    addressBalance =  perform_decimal_operation('addition', receiverAddress_details.addressBalance, commentTransferAmount)
                    receiverAddress_details.addressBalance = None
                session.add(ActiveTable(address=outputAddress, parentid=pidlst[-1][0], consumedpid=str(piddict), transferBalance=commentTransferAmount, addressBalance=addressBalance, blockNumber=blockinfo['height']))

                senderAddress_details = session.query(ActiveTable).filter_by(address=inputAddress).order_by(ActiveTable.id.desc()).first()
                senderAddress_details.addressBalance = perform_decimal_operation('subtraction', senderAddress_details.addressBalance, commentTransferAmount)

                # Migration 
                # shift pid of used utxos from active to consumed
                for piditem in pidlst[:-1]:
                    # move the parentids consumed to consumedpid column in both activeTable and consumedTable
                    entries = session.query(ActiveTable).filter(ActiveTable.parentid == piditem[0]).all()
                    process_pids(entries, session, piditem)

                    entries = session.query(ConsumedTable).filter(ConsumedTable.parentid == piditem[0]).all()
                    process_pids(entries, session, piditem)

                    # move the pids consumed in the transaction to consumedTable and delete them from activeTable
                    session.execute('INSERT INTO consumedTable (id, address, parentid, consumedpid, transferBalance, addressBalance, orphaned_parentid, blockNumber) SELECT id, address, parentid, consumedpid, transferBalance, addressBalance, orphaned_parentid, blockNumber FROM activeTable WHERE id={}'.format(piditem[0]))
                    session.execute('DELETE FROM activeTable WHERE id={}'.format(piditem[0]))
                session.commit()
            
            add_transaction_history(token_name=tokenIdentification, sourceFloAddress=inputAddress, destFloAddress=outputAddress, transferAmount=tokenAmount, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=transactionType, parsedFloData=json.dumps(parsed_data))
            
            session.commit()
            session.close()
            return 1


def trigger_internal_contract_onvalue(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name, contract_address, transaction_subType):
    # Trigger the contract
    if tokenAmount_sum <= 0:
        # Add transaction to ContractTransactionHistory
        add_contract_transaction_history(contract_name=contract_name, contract_address=contract_address, transactionType='trigger', transactionSubType='zero-participation', sourceFloAddress='', destFloAddress='', transferAmount=0, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
        # Add transaction to latestCache
        updateLatestTransaction(transaction_data, parsed_data , f"{contract_name}-{contract_address}")

    else:
        payeeAddress = json.loads(contractStructure['payeeAddress'])
        tokenIdentification = contractStructure['tokenIdentification']

        for floaddress in payeeAddress.keys():
            transferAmount = perform_decimal_operation('multiplication', tokenAmount_sum, perform_decimal_operation('division', payeeAddress[floaddress], 100))
            returnval = transferToken(tokenIdentification, transferAmount, contract_address, floaddress, transaction_data=transaction_data, blockinfo = blockinfo, parsed_data = parsed_data)
            if returnval == 0:
                logger.critical("Something went wrong in the token transfer method while doing local Smart Contract Trigger")
                return 0

            # Add transaction to ContractTransactionHistory
            add_contract_transaction_history(contract_name=contract_name, contract_address=contract_address, transactionType='trigger', transactionSubType=transaction_subType, sourceFloAddress=contract_address, destFloAddress=floaddress, transferAmount=transferAmount, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
            # Add transaction to latestCache
            updateLatestTransaction(transaction_data, parsed_data , f"{contract_name}-{contract_address}")
    return 1


def process_minimum_subscriptionamount(contractStructure, connection, blockinfo, transaction_data, parsed_data):
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
            add_contract_transaction_history(contract_name=contractStructure['contractName'], contract_address=contractStructure['contractAddress'], transactionType=parsed_data['type'], transactionSubType='minimumsubscriptionamount-payback', sourceFloAddress=contractAddress, destFloAddress=participant[0], transferAmount=participant[1], blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
        return 1
    else:
        return 0


def process_maximum_subscriptionamount(contractStructure, connection, status, blockinfo, transaction_data, parsed_data):
    maximumsubscriptionamount = float(contractStructure['maximumsubscriptionamount'])
    rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
    tokenAmount_sum = float(sum(Decimal(f"{row[0]}") for row in rows))
    if tokenAmount_sum >= maximumsubscriptionamount:
        # Trigger the contract
        if status == 'close':
            success_returnval = trigger_internal_contract_onvalue(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name=contractStructure['contractName'], contract_address=contractStructure['contractAddress'], transaction_subType='maximumsubscriptionamount')
            if not success_returnval:
                return 0
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
    while True:
        try:
            connection = create_database_connection('system_dbs', {'db_name':'system'})
            break
        except:
            time.sleep(DB_RETRY_TIMEOUT)
    connection.execute("INSERT INTO activecontracts VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (None, contractStructure['contractName'], contractStructure['contractAddress'], contractStatus, contractStructure['tokenIdentification'], contractStructure['contractType'], transactionHash, blockNumber, blockHash, incorporationDate, expiryDate, closeDate))
    connection.execute("INSERT INTO time_actions VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (None, trigger_time, trigger_activity, contractStatus, contractName, contractAddress, contractType, tokens_db, parsed_data, transactionHash, blockHeight))
    connection.close()


def return_time_active_contracts(session, status='active', activity='contract-time-trigger'):
    sql_query = text("""
        SELECT t1.* 
        FROM time_actions t1 
        JOIN (
            SELECT contractName, contractAddress, MAX(id) AS max_id 
            FROM time_actions 
            GROUP BY contractName, contractAddress
        ) t2 
        ON t1.contractName = t2.contractName 
        AND t1.contractAddress = t2.contractAddress 
        AND t1.id = t2.max_id 
        WHERE t1.status = :status AND t1.activity = :activity
    """)
    active_contracts = session.execute(sql_query, {'status': status, 'activity': activity}).fetchall()
    return active_contracts



def return_time_active_deposits(session):
    # find all the deposits which are active
    # todo - sqlalchemy gives me warning with the following method
    subquery_filter = session.query(TimeActions.id).group_by(TimeActions.transactionHash,TimeActions.id).having(func.count(TimeActions.transactionHash)==1).subquery()
    active_deposits = session.query(TimeActions).filter(TimeActions.id.in_(subquery_filter.select()), TimeActions.status=='active', TimeActions.activity=='contract-deposit').all()
    return active_deposits


def process_contract_time_trigger(blockinfo, systemdb_session, active_contracts):   
    for query in active_contracts:
        query_time = convert_datetime_to_arrowobject(query.time)
        blocktime = parsing.arrow.get(blockinfo['time']).to('Asia/Kolkata')

        if query.activity == 'contract-time-trigger':
            contractStructure = extract_contractStructure(query.contractName, query.contractAddress)
            connection = create_database_connection('smart_contract', {'contract_name':f"{query.contractName}", 'contract_address':f"{query.contractAddress}"})
            if contractStructure['contractType'] == 'one-time-event':
                # TODO - FIGURE A BETTER SOLUTION FOR THIS 
                tx_type = 'trigger'
                data = [blockinfo['hash'], blockinfo['height'], blockinfo['time'], blockinfo['size'], tx_type]

                def _get_txid(data):
                    """
                    Generate a SHA256 hash of the input data.
                    :param data: The data to be hashed.
                    :return: The SHA256 hash as a hexadecimal string.
                    """
                    try:
                        # Ensure data is encoded before hashing
                        if isinstance(data, str):
                            data = data.encode('utf-8')
                        txid = hashlib.sha256(data).hexdigest()
                        return txid
                    except Exception as e:
                        logger.error(f"Failed to generate SHA256 hash: {e}")
                        raise e

                
                transaction_data = {}
                transaction_data['txid'] = _get_txid(data)
                transaction_data['blockheight'] = blockinfo['height']
                transaction_data['time'] = blockinfo['time']

                parsed_data = {}
                parsed_data['type'] = tx_type
                parsed_data['contractName'] = query.contractName
                parsed_data['contractAddress'] = query.contractAddress

                activecontracts_table_info = systemdb_session.query(ActiveContracts.blockHash, ActiveContracts.incorporationDate).filter(ActiveContracts.contractName==query.contractName, ActiveContracts.contractAddress==query.contractAddress, ActiveContracts.status=='active').first()

                if 'exitconditions' in contractStructure: # Committee trigger contract type
                    # maximumsubscription check, if reached then expire the contract 
                    if 'maximumsubscriptionamount' in contractStructure:
                        maximumsubscriptionamount = float(contractStructure['maximumsubscriptionamount'])
                        rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
                        tokenAmount_sum = float(sum(Decimal(f"{row[0]}") for row in rows))
                        if tokenAmount_sum >= maximumsubscriptionamount:
                            # Expire the contract
                            logger.info(f"Maximum Subscription amount {maximumsubscriptionamount} reached for {query.contractName}_{query.contractAddress}. Expiring the contract")
                            close_expire_contract(contractStructure, 'expired', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], None, query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                                   
                    if blocktime > query_time:
                        if 'minimumsubscriptionamount' in contractStructure:
                            if process_minimum_subscriptionamount(contractStructure, connection, blockinfo, transaction_data, parsed_data):
                                logger.info(f"Contract trigger time {query_time} achieved and Minimimum subscription amount reached for {query.contractName}_{query.contractAddress}. Closing the contract")
                                close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                                return 

                        # Expire the contract
                        logger.info(f"Contract trigger time {query_time} achieved for {query.contractName}_{query.contractAddress}. Expiring the contract")
                        close_expire_contract(contractStructure, 'expired', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], None, query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                
                elif 'payeeAddress' in contractStructure: # Internal trigger contract type
                    
                    # maximumsubscription check, if reached then trigger the contract
                    if 'maximumsubscriptionamount' in contractStructure:
                        maximumsubscriptionamount = float(contractStructure['maximumsubscriptionamount'])
                        rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
                        tokenAmount_sum = float(sum(Decimal(f"{row[0]}") for row in rows))
                        if tokenAmount_sum >= maximumsubscriptionamount:
                            # Trigger the contract
                            logger.info(f"Triggering the {query.contractName}_{query.contractAddress} as maximum subscription amount {maximumsubscriptionamount} has been reached ")
                            success_returnval = trigger_internal_contract_onvalue(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name=query.contractName, contract_address=query.contractAddress, transaction_subType='maximumsubscriptionamount')
                            if not success_returnval:
                                return 0
                            logger.info(f"Closing the {query.contractName}_{query.contractAddress} as maximum subscription amount {maximumsubscriptionamount} has been reached ")
                            close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                            return
      
                    if blocktime > query_time: 
                        if 'minimumsubscriptionamount' in contractStructure:
                            if process_minimum_subscriptionamount(contractStructure, connection, blockinfo, transaction_data, parsed_data):
                                logger.info(f"Contract trigger time {query_time} achieved and Minimimum subscription amount reached for {query.contractName}_{query.contractAddress}. Closing the contract")
                                close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                                return
                        
                        # Trigger the contract
                        rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
                        # Sum up using Decimal
                        tokenAmount_sum = float(sum(Decimal(f"{row[0]}") for row in rows))
                        logger.info(f"Triggering the contract {query.contractName}_{query.contractAddress}")
                        success_returnval = trigger_internal_contract_onvalue(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name=query.contractName, contract_address=query.contractAddress, transaction_subType='expiryTime')
                        if not success_returnval:
                            return 0

                        logger.info(f"Closing the contract {query.contractName}_{query.contractAddress}")
                        close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                        return


def process_contract_deposit_trigger(blockinfo, systemdb_session, active_deposits):
    for query in active_deposits:
        query_time = convert_datetime_to_arrowobject(query.time)
        blocktime = parsing.arrow.get(blockinfo['time']).to('Asia/Kolkata')
        if query.activity == 'contract-deposit':
            if blocktime > query_time:
                # find the status of the deposit 
                # the deposit is unique
                # find the total sum to be returned from the smart contract's participation table 
                contract_db = create_database_session_orm('smart_contract', {'contract_name': query.contractName, 'contract_address': query.contractAddress}, ContractBase)

                deposit_query = contract_db.query(ContractDeposits).filter(ContractDeposits.transactionHash == query.transactionHash).first()
                deposit_last_latest_entry = contract_db.query(ContractDeposits).filter(ContractDeposits.transactionHash == query.transactionHash).order_by(ContractDeposits.id.desc()).first()
                returnAmount = deposit_last_latest_entry.depositBalance
                depositorAddress = deposit_last_latest_entry.depositorAddress

                # Do a token transfer back to the deposit address 
                sellingToken = contract_db.query(ContractStructure.value).filter(ContractStructure.attribute == 'selling_token').first()[0]
                tx_block_string = f"{query.transactionHash}{blockinfo['height']}".encode('utf-8').hex()
                parsed_data = {}
                parsed_data['type'] = 'smartContractDepositReturn'
                parsed_data['contractName'] = query.contractName
                parsed_data['contractAddress'] = query.contractAddress
                transaction_data = {}
                transaction_data['txid'] = query.transactionHash
                transaction_data['blockheight'] = blockinfo['height']
                transaction_data['time'] = blockinfo['time']
                logger.info(f"Initiating smartContractDepositReturn after time expiry {query_time} for {depositorAddress} with amount {returnAmount} {sellingToken}# from {query.contractName}_{query.contractAddress} contract ")
                returnval = transferToken(sellingToken, returnAmount, query.contractAddress, depositorAddress, transaction_data=transaction_data, parsed_data=parsed_data, blockinfo=blockinfo)
                if returnval == 0:
                    logger.critical("Something went wrong in the token transfer method while return contract deposit. THIS IS CRITICAL ERROR")
                    return
                else:
                    contract_db.add(ContractDeposits(
                        depositorAddress = deposit_last_latest_entry.depositorAddress,
                        depositAmount = -abs(returnAmount),
                        depositBalance = 0,
                        expiryTime = deposit_last_latest_entry.expiryTime,
                        unix_expiryTime = deposit_last_latest_entry.unix_expiryTime,
                        status = 'deposit-return',
                        transactionHash = deposit_last_latest_entry.transactionHash,
                        blockNumber = blockinfo['height'],
                        blockHash = blockinfo['hash']
                    ))
                    logger.info(f"Successfully processed smartContractDepositReturn transaction ID {query.transactionHash} after time expiry {query_time} for {depositorAddress} with amount {returnAmount} {sellingToken}# from {query.contractName}_{query.contractAddress} contract ")
                    add_contract_transaction_history(contract_name=query.contractName, contract_address=query.contractAddress, transactionType='smartContractDepositReturn', transactionSubType=None, sourceFloAddress=query.contractAddress, destFloAddress=depositorAddress, transferAmount=returnAmount, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=deposit_last_latest_entry.transactionHash, jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))

                    systemdb_session.add(TimeActions(
                        time = query.time,
                        activity = query.activity,
                        status = 'returned',
                        contractName = query.contractName,
                        contractAddress = query.contractAddress,
                        contractType = query.contractType,
                        tokens_db = query.tokens_db,
                        parsed_data = query.parsed_data,
                        transactionHash = query.transactionHash,
                        blockNumber = blockinfo['height']
                    ))

                    contract_db.commit()
                    systemdb_session.commit()
                    updateLatestTransaction(transaction_data, parsed_data, f"{query.contractName}_{query.contractAddress}")


def checkLocal_time_expiry_trigger_deposit(blockinfo):
    # Connect to system.db with a session 
    while True:
        try:
            systemdb_session = create_database_session_orm('system_dbs', {'db_name':'system'}, SystemBase)

            break
        except:
            time.sleep(DB_RETRY_TIMEOUT)
    timeactions_tx_hashes = []
    active_contracts = return_time_active_contracts(systemdb_session)
    active_deposits = return_time_active_deposits(systemdb_session)
 
    process_contract_time_trigger(blockinfo, systemdb_session, active_contracts)
    process_contract_deposit_trigger(blockinfo, systemdb_session, active_deposits)

def check_reorg():
    connection = create_database_connection('system_dbs')
    BACK_TRACK_BLOCKS = 1000

    # Find latest block number in local database
    latest_block = connection.execute("SELECT max(blockNumber) FROM latestBlocks").fetchone()[0]
    block_number = latest_block

    # Decide correct API path and key
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
        # Get block hash from local DB
        local_result = connection.execute(
            f"SELECT blockHash FROM latestBlocks WHERE blockNumber = {block_number}"
        ).fetchone()

        if not local_result:
            logger.error(f"No local block hash found for height {block_number}")
            break

        local_hash = local_result[0]

        # Fetch from Blockbook
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
            sys.exit(0)  # TODO: Consider retry instead of exit

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
    while True:
        try:
            connection = create_database_connection('smart_contract', {'contract_name':f"{contractName}", 'contract_address':f"{contractAddress}"})
            break
        except:
            time.sleep(DB_RETRY_TIMEOUT)
    attributevaluepair = connection.execute("SELECT attribute, value FROM contractstructure WHERE attribute != 'flodata'").fetchall()
    contractStructure = {}
    conditionDict = {}
    counter = 0
    for item in attributevaluepair:
        if list(item)[0] == 'exitconditions':
            conditionDict[counter] = list(item)[1]
            counter = counter + 1
        else:
            contractStructure[list(item)[0]] = list(item)[1]
    if len(conditionDict) > 0:
        contractStructure['exitconditions'] = conditionDict
    del counter, conditionDict

    return contractStructure


def process_flo_checks(transaction_data):

    normalize_transaction_data(transaction_data)

    # Create vinlist and outputlist
    vinlist = []
    querylist = []

    # Extract VIN information
    for vin in transaction_data["vin"]:
        vinlist.append([vin["addresses"][0], float(vin["value"])])

    totalinputval = float(transaction_data["valueIn"])

    # Check if all the addresses in a transaction on the input side are the same
    for idx, item in enumerate(vinlist):
        if idx == 0:
            temp = item[0]
            continue
        if item[0] != temp:
            logger.info(f"System has found more than one address as part of vin. Transaction {transaction_data['txid']} is rejected")
            return None, None, None

    inputlist = [vinlist[0][0], totalinputval]
    inputadd = vinlist[0][0]

    # Check if the number of vout is more than 2 (Rule 42)
    if len(transaction_data["vout"]) > 2:
        logger.info(f"System has found more than 2 addresses as part of vout. Transaction {transaction_data['txid']} is rejected")
        return None, None, None

    # Extract output addresses (Rule 43)
    outputlist = []
    addresscounter = 0
    inputcounter = 0
    for obj in transaction_data["vout"]:
        if 'addresses' not in obj["scriptPubKey"]:
            continue
        if obj["scriptPubKey"]["addresses"]:
            addresscounter += 1
            if inputlist[0] == obj["scriptPubKey"]["addresses"][0]:
                inputcounter += 1
                continue
            outputlist.append([obj["scriptPubKey"]["addresses"][0], obj["value"]])

    if addresscounter == inputcounter:
        outputlist = [inputlist[0]]
    elif len(outputlist) != 1:
        logger.info(f"Transaction's change is not coming back to the input address. Transaction {transaction_data['txid']} is rejected")
        return None, None, None
    else:
        outputlist = outputlist[0]

    return inputlist, outputlist, inputadd


def process_token_transfer(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd):
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


def process_one_time_event_transfer(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd,connection,session,contractStructure):
    logger.info(f"Processing one-time event transfer for transaction {transaction_data['txid']}")

    # Check if the transaction hash already exists in the contract db (Safety check)
    participantAdd_txhash = connection.execute('SELECT participantAddress, transactionHash FROM contractparticipants').fetchall()
    participantAdd_txhash_T = list(zip(*participantAdd_txhash))

    if len(participantAdd_txhash) != 0 and transaction_data['txid'] in list(participantAdd_txhash_T[1]):
        logger.warning(f"Transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db. This is unusual, please check your code")
        pushData_SSEapi(f"Error | Transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db. This is unusual, please check your code")
        return 0

    # If contractAddress was passed, then check if it matches the output address of this contract
    if 'contractAddress' in parsed_data:
        if parsed_data['contractAddress'] != outputlist[0]:
            rejectComment = f"Contract participation at transaction {transaction_data['txid']} rejected as contractAddress specified in flodata, {parsed_data['contractAddress']}, does not match with transaction's output address {outputlist[0]}"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(f"Error| Mismatch in contract address specified in flodata and the output address of the transaction {transaction_data['txid']}")
            return 0

    # Check the status of the contract
    contractStatus = check_contract_status(parsed_data['contractName'], outputlist[0])

    if contractStatus == 'closed':
        rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart contract {parsed_data['contractName']} at the {outputlist[0]} is closed"
        logger.info(rejectComment)
        rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
        return 0
    else:
        session = create_database_session_orm('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': f"{outputlist[0]}"}, ContractBase)
        result = session.query(ContractStructure).filter_by(attribute='expiryTime').all()
        session.close()
        if result:
            # Now parse the expiry time in Python
            expirytime = result[0].value.strip()
            expirytime_split = expirytime.split(' ')
            parse_string = '{}/{}/{} {}'.format(expirytime_split[3], parsing.months[expirytime_split[1]], expirytime_split[2], expirytime_split[4])
            expirytime_object = parsing.arrow.get(parse_string, 'YYYY/M/D HH:mm:ss').replace(tzinfo=expirytime_split[5][3:])
            blocktime_object = parsing.arrow.get(transaction_data['time']).to('Asia/Kolkata')

            if blocktime_object > expirytime_object:
                rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart contract {parsed_data['contractName']}_{outputlist[0]} has expired and will not accept any user participation"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                pushData_SSEapi(rejectComment)
                return 0

    # Check if user choice has been passed to the wrong contract type
    if 'userChoice' in parsed_data and 'exitconditions' not in contractStructure:
        rejectComment = f"Transaction {transaction_data['txid']} rejected as userChoice, {parsed_data['userChoice']}, has been passed to Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]} which doesn't accept any userChoice"
        logger.info(rejectComment)
        rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
        pushData_SSEapi(rejectComment)
        return 0

    # Check if the right token is being sent for participation
    if parsed_data['tokenIdentification'] != contractStructure['tokenIdentification']:
        rejectComment = f"Transaction {transaction_data['txid']} rejected as the token being transferred, {parsed_data['tokenIdentification'].upper()}, is not part of the structure of Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]}"
        logger.info(rejectComment)
        rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
        pushData_SSEapi(rejectComment)
        return 0

    # Check if contractAmount is part of the contract structure, and enforce it if it is
    if 'contractAmount' in contractStructure:
        if float(contractStructure['contractAmount']) != float(parsed_data['tokenAmount']):
            rejectComment = f"Transaction {transaction_data['txid']} rejected as contractAmount being transferred is not part of the structure of Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]}"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0

    partialTransferCounter = 0
    # Check if maximum subscription amount has been reached
    if 'maximumsubscriptionamount' in contractStructure:
        # Now parse the expiry time in Python
        maximumsubscriptionamount = float(contractStructure['maximumsubscriptionamount'])
        session = create_database_session_orm('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': f"{outputlist[0]}"}, ContractBase)
        
        query_data = session.query(ContractParticipants.tokenAmount).all()
        amountDeposited = sum(Decimal(f"{amount[0]}") if amount[0] is not None else Decimal(0) for amount in query_data)

        session.close()

        if amountDeposited is None:
            amountDeposited = 0

        if amountDeposited >= maximumsubscriptionamount:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as maximum subscription amount has been reached for the Smart contract named {parsed_data['contractName']} at the address {outputlist[0]}"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
        elif (perform_decimal_operation('addition', float(amountDeposited), float(parsed_data['tokenAmount'])) > maximumsubscriptionamount):
            if 'contractAmount' in contractStructure:
                rejectComment = f"Transaction {transaction_data['txid']} rejected as the contractAmount surpasses the maximum subscription amount, {contractStructure['maximumsubscriptionamount']} {contractStructure['tokenIdentification'].upper()}, for the Smart contract named {parsed_data['contractName']} at the address {outputlist[0]}"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                pushData_SSEapi(rejectComment)
                return 0
            else:
                partialTransferCounter = 1
                rejectComment = f"Transaction {transaction_data['txid']} rejected as the partial transfer of token {contractStructure['tokenIdentification'].upper()} is not allowed, for the Smart contract named {parsed_data['contractName']} at the address {outputlist[0]}"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                pushData_SSEapi(rejectComment)
                return 0

    # Check if exitcondition exists as part of contract structure and is given in right format
    if 'exitconditions' in contractStructure:
        # This means the contract has an external trigger, ie. trigger coming from the contract committee
        exitconditionsList = []
        for condition in contractStructure['exitconditions']:
            exitconditionsList.append(contractStructure['exitconditions'][condition])

        if parsed_data['userChoice'] in exitconditionsList:
            if partialTransferCounter == 0:
                # Check if the tokenAmount being transferred exists in the address & do the token transfer
                returnval = transferToken(parsed_data['tokenIdentification'], parsed_data['tokenAmount'], inputlist[0], outputlist[0], transaction_data, parsed_data, blockinfo=blockinfo)
                if returnval != 0:
                    # Store participant details in the smart contract's db
                    session.add(ContractParticipants(participantAddress=inputadd,
                                                      tokenAmount=parsed_data['tokenAmount'],
                                                      userChoice=parsed_data['userChoice'],
                                                      transactionHash=transaction_data['txid'],
                                                      blockNumber=transaction_data['blockheight'],
                                                      blockHash=transaction_data['blockhash']))
                    session.commit()

                    # Store transfer as part of ContractTransactionHistory
                    add_contract_transaction_history(contract_name=parsed_data['contractName'], contract_address=outputlist[0], transactionType='participation', transactionSubType=None, sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))

                    # Store a mapping of participant address -> Contract participated in
                    system_session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
                    system_session.add(ContractAddressMapping(address=inputadd, addressType='participant',
                                                        tokenAmount=parsed_data['tokenAmount'],
                                                        contractName=parsed_data['contractName'],
                                                        contractAddress=outputlist[0],
                                                        transactionHash=transaction_data['txid'],
                                                        blockNumber=transaction_data['blockheight'],
                                                        blockHash=transaction_data['blockhash']))
                    system_session.commit()

                    # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
                    connection = create_database_connection('system_dbs', {'db_name': 'system'})
                    firstInteractionCheck = connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{outputlist[0]}' AND token='{parsed_data['tokenIdentification']}'").fetchall()
                    if len(firstInteractionCheck) == 0:
                        connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{outputlist[0]}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")
                    connection.close()
                    updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}_{outputlist[0]}", transactionType='ote-externaltrigger-participation')
                    return 1

                else:
                    logger.info("Something went wrong in the smartcontract token transfer method")
                    return 0
            elif partialTransferCounter == 1:
                # Transfer only part of the tokens users specified, till the time it reaches maximum amount
                returnval = transferToken(parsed_data['tokenIdentification'], perform_decimal_operation('subtraction', maximumsubscriptionamount, amountDeposited), inputlist[0], outputlist[0], transaction_data, parsed_data, blockinfo=blockinfo)
                if returnval != 0:
                    # Store participant details in the smart contract's db
                    session.add(ContractParticipants(participantAddress=inputadd,
                                                      tokenAmount=perform_decimal_operation('subtraction', maximumsubscriptionamount, amountDeposited),
                                                      userChoice=parsed_data['userChoice'],
                                                      transactionHash=transaction_data['txid'],
                                                      blockNumber=transaction_data['blockheight'],
                                                      blockHash=transaction_data['blockhash']))
                    session.commit()
                    session.close()

                    # Store transfer as part of ContractTransactionHistory
                    add_contract_transaction_history(contract_name=parsed_data['contractName'], contract_address=outputlist[0], transactionType='participation', transactionSubType=None, sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=perform_decimal_operation('subtraction', maximumsubscriptionamount, amountDeposited), blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))

                    # Store a mapping of participant address -> Contract participated in
                    system_session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
                    system_session.add(ContractAddressMapping(address=inputadd, addressType='participant',
                                                        tokenAmount=perform_decimal_operation('subtraction', maximumsubscriptionamount, amountDeposited),
                                                        contractName=parsed_data['contractName'],
                                                        contractAddress=outputlist[0],
                                                        transactionHash=transaction_data['txid'],
                                                        blockNumber=transaction_data['blockheight'],
                                                        blockHash=transaction_data['blockhash']))
                    system_session.commit()
                    system_session.close()
                    updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}_{outputlist[0]}", transactionType='ote-externaltrigger-participation')
                    return 1

                else:
                    logger.info("Something went wrong in the smartcontract token transfer method")
                    return 0

        else:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as wrong user choice entered for the Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]}"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0

    elif 'payeeAddress' in contractStructure:
        # This means the contract is of the type internal trigger
        if partialTransferCounter == 0:
            transferAmount = parsed_data['tokenAmount']
        elif partialTransferCounter == 1:
            transferAmount = perform_decimal_operation('subtraction', maximumsubscriptionamount, amountDeposited)

        # Check if the tokenAmount being transferred exists in the address & do the token transfer
        returnval = transferToken(parsed_data['tokenIdentification'], transferAmount, inputlist[0], outputlist[0], transaction_data, parsed_data, blockinfo=blockinfo)
        if returnval != 0:
            # Store participant details in the smart contract's db
            session.add(ContractParticipants(participantAddress=inputadd, tokenAmount=transferAmount, userChoice='-', transactionHash=transaction_data['txid'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash']))

            # Store transfer as part of ContractTransactionHistory
            add_contract_transaction_history(contract_name=parsed_data['contractName'], contract_address=outputlist[0], transactionType='participation', transactionSubType=None, sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=transferAmount, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
            session.commit()
            session.close()

            # Store a mapping of participant address -> Contract participated in
            system_session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
            system_session.add(ContractAddressMapping(address=inputadd, addressType='participant',
                                                tokenAmount=transferAmount,
                                                contractName=parsed_data['contractName'],
                                                contractAddress=outputlist[0],
                                                transactionHash=transaction_data['txid'],
                                                blockNumber=transaction_data['blockheight'],
                                                blockHash=transaction_data['blockhash']))
            system_session.commit()

            # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
            connection = create_database_connection('system_dbs', {'db_name': 'system'})
            firstInteractionCheck = connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{outputlist[0]}' AND token='{parsed_data['tokenIdentification']}'").fetchall()
            if len(firstInteractionCheck) == 0:
                connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{outputlist[0]}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")
            connection.close()
            updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}_{outputlist[0]}", transactionType='ote-internaltrigger-participation')
            return 1

        else:
            logger.info("Something went wrong in the smartcontract token transfer method")
            return 0

    return 1  # Indicate successful processing of the one-time event transfer





def process_continuous_event_transfer(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd, connection, contract_session, contractStructure):
    logger.info(f"Processing continuous event transfer for transaction {transaction_data['txid']}")

    # Determine the subtype of the contract
    contract_subtype = contract_session.query(ContractStructure.value).filter(ContractStructure.attribute == 'subtype').first()[0]
                    

    if contract_subtype == 'tokenswap':
        # Check if the transaction hash already exists in the contract db (Safety check)
        participantAdd_txhash = connection.execute('SELECT participantAddress, transactionHash FROM contractparticipants').fetchall()
        participantAdd_txhash_T = list(zip(*participantAdd_txhash))

        if len(participantAdd_txhash) != 0 and transaction_data['txid'] in list(participantAdd_txhash_T[1]):
            logger.warning(f"Transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db. This is unusual, please check your code")
            pushData_SSEapi(f"Error | Transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db. This is unusual, please check your code")
            return 0

        # if contractAddress was passed, then check if it matches the output address of this contract
        if 'contractAddress' in parsed_data:
            if parsed_data['contractAddress'] != outputlist[0]:
                rejectComment = f"Contract participation at transaction {transaction_data['txid']} rejected as contractAddress specified in flodata, {parsed_data['contractAddress']}, doesnt not match with transaction's output address {outputlist[0]}"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                # Pass information to SSE channel
                pushData_SSEapi(f"Error| Mismatch in contract address specified in flodata and the output address of the transaction {transaction_data['txid']}")
                return 0
        
        if contractStructure['pricetype'] in ['predetermined','determined']:
            swapPrice = float(contractStructure['price'])
        elif contractStructure['pricetype'] == 'dynamic':
            # Oracle address cannot be a participant in the contract. Check if the sender address is oracle address
            if transaction_data['senderAddress'] == contractStructure['oracle_address']:
                logger.warning(f"Transaction {transaction_data['txid']} rejected as the oracle addess {contractStructure['oracle_address']} is attempting to participate. Please report this to the contract owner")
                pushData_SSEapi(f"Transaction {transaction_data['txid']} rejected as the oracle addess {contractStructure['oracle_address']} is attempting to participate. Please report this to the contract owner")
                return 0

            swapPrice = fetchDynamicSwapPrice(contractStructure, blockinfo)


            # Use current blockinfo as the effective application time
            priceTimestamp = blockinfo['time']
            priceBlockheight = blockinfo['height']

            # Store dynamic price state
            update_dynamic_swap_price(
                session=contract_session,
                price=swapPrice,
                timestamp=priceTimestamp,
                blockheight=priceBlockheight
            )


        swapAmount = perform_decimal_operation('division', parsed_data['tokenAmount'], swapPrice)

        # Check if the swap amount is available in the deposits of the selling token 
        # if yes do the transfers, otherwise reject the transaction 
        # 
        subquery = contract_session.query(func.max(ContractDeposits.id)).group_by(ContractDeposits.transactionHash)
        active_contract_deposits = contract_session.query(ContractDeposits).filter(ContractDeposits.id.in_(subquery)).filter(ContractDeposits.status != 'deposit-return').filter(ContractDeposits.status != 'consumed').filter(ContractDeposits.status == 'active').all()

        # todo - what is the role of the next line? cleanup if not useful
        available_deposits = active_contract_deposits[:]

        # available_deposit_sum = contract_session.query(func.sum(ContractDeposits.depositBalance)).filter(ContractDeposits.id.in_(subquery)).filter(ContractDeposits.status != 'deposit-return').filter(ContractDeposits.status == 'active').all()

        query_data = contract_session.query(ContractDeposits.depositBalance).filter(ContractDeposits.id.in_(subquery)).filter(ContractDeposits.status != 'deposit-return').filter(ContractDeposits.status == 'active').all()

        available_deposit_sum = sum(Decimal(f"{amount[0]}") if amount[0] is not None else Decimal(0) for amount in query_data)
        if available_deposit_sum==0 or available_deposit_sum[0][0] is None:
            available_deposit_sum = 0
        else:
            available_deposit_sum = float(available_deposit_sum[0][0])


        if available_deposit_sum >= swapAmount:
            # Accepting token transfer from participant to smart contract address
            logger.info(f"Accepting 'tokenswapParticipation' transaction ID {transaction_data['txid']} from participant {inputlist[0]} for {parsed_data['tokenAmount']} {parsed_data['tokenIdentification']}# to smart contract address for swap amount {swapAmount} and swap-price {swapPrice}")
            returnval = transferToken(parsed_data['tokenIdentification'], parsed_data['tokenAmount'], inputlist[0], outputlist[0], transaction_data=transaction_data, parsed_data=parsed_data, isInfiniteToken=None, blockinfo=blockinfo, transactionType='tokenswapParticipation')
            if returnval == 0:
                logger.info("ERROR | Something went wrong in the token transfer method while doing local Smart Contract Participation")
                return 0

            # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
            systemdb_connection = create_database_connection('system_dbs', {'db_name': 'system'})
            firstInteractionCheck = systemdb_connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{outputlist[0]}' AND token='{parsed_data['tokenIdentification']}'").fetchall()
            if len(firstInteractionCheck) == 0:
                systemdb_connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{outputlist[0]}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")
            systemdb_connection.close()

            # ContractDepositTable 
            # For each unique deposit( address, expirydate, blocknumber) there will be 2 entries added to the table 
            # the consumption of the deposits will start form the top of the table 
            deposit_counter = 0 
            remaining_amount = swapAmount 
            for a_deposit in available_deposits:
                if a_deposit.depositBalance > remaining_amount:
                    # accepting token transfer from the contract to depositor's address 
                    returnval = transferToken(contractStructure['accepting_token'], perform_decimal_operation('multiply', remaining_amount, swapPrice), contractStructure['contractAddress'], a_deposit.depositorAddress, transaction_data=transaction_data, parsed_data=parsed_data, isInfiniteToken=None, blockinfo=blockinfo, transactionType='tokenswapDepositSettlement')
                    if returnval == 0:
                        logger.info("CRITICAL ERROR | Something went wrong in the token transfer method while doing local Smart Contract Particiaption deposit swap operation")
                        return 0

                    # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
                    systemdb_connection = create_database_connection('system_dbs', {'db_name':'system'})
                    firstInteractionCheck = systemdb_connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{a_deposit.depositorAddress}' AND token='{contractStructure['accepting_token']}'").fetchall()
                    if len(firstInteractionCheck) == 0:
                        systemdb_connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{a_deposit.depositorAddress}', '{contractStructure['accepting_token']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")
                    systemdb_connection.close()


                    contract_session.add(ContractDeposits(  depositorAddress= a_deposit.depositorAddress,
                                                            depositAmount= perform_decimal_operation('subtraction', 0, remaining_amount),
                                                            status='deposit-honor',
                                                            transactionHash= a_deposit.transactionHash,
                                                            blockNumber= blockinfo['height'],
                                                            blockHash= blockinfo['hash']))
                    
                    # if the total is consumsed then the following entry won't take place 
                    contract_session.add(ContractDeposits(  depositorAddress= a_deposit.depositorAddress,
                                                            depositBalance= perform_decimal_operation('subtraction', a_deposit.depositBalance, remaining_amount),
                                                            expiryTime = a_deposit.expiryTime,
                                                            unix_expiryTime = a_deposit.unix_expiryTime,
                                                            status='active',
                                                            transactionHash= a_deposit.transactionHash,
                                                            blockNumber= blockinfo['height'],
                                                            blockHash= blockinfo['hash']))
                    # ConsumedInfoTable 
                    contract_session.add(ConsumedInfo(  id_deposittable= a_deposit.id,
                                                        transactionHash= a_deposit.transactionHash,
                                                        blockNumber= blockinfo['height']))
                    remaining_amount = perform_decimal_operation('subtraction', remaining_amount, a_deposit.depositBalance)
                    remaining_amount = 0 
                    break
                                

                elif a_deposit.depositBalance <= remaining_amount:
                    # accepting token transfer from the contract to depositor's address 
                    logger.info(f"Performing 'tokenswapSettlement' transaction ID {transaction_data['txid']} from participant {a_deposit.depositorAddress} for {perform_decimal_operation('multiplication', a_deposit.depositBalance, swapPrice)} {contractStructure['accepting_token']}# ")
                    returnval = transferToken(contractStructure['accepting_token'], perform_decimal_operation('multiplication', a_deposit.depositBalance, swapPrice), contractStructure['contractAddress'], a_deposit.depositorAddress, transaction_data=transaction_data, parsed_data=parsed_data, isInfiniteToken=None, blockinfo=blockinfo, transactionType='tokenswapDepositSettlement')
                    if returnval == 0:
                        logger.info("CRITICAL ERROR | Something went wrong in the token transfer method while doing local Smart Contract Particiaption deposit swap operation")
                        return 0

                    # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
                    systemdb_connection = create_database_connection('system_dbs', {'db_name':'system'})
                    firstInteractionCheck = systemdb_connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{a_deposit.depositorAddress}' AND token='{contractStructure['accepting_token']}'").fetchall()
                    if len(firstInteractionCheck) == 0:
                        systemdb_connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{a_deposit.depositorAddress}', '{contractStructure['accepting_token']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")
                    systemdb_connection.close()

                    
                    contract_session.add(ContractDeposits(  depositorAddress= a_deposit.depositorAddress,
                                                            depositAmount= perform_decimal_operation('subtraction', 0, a_deposit.depositBalance),
                                                            status='deposit-honor',
                                                            transactionHash= a_deposit.transactionHash,
                                                            blockNumber= blockinfo['height'],
                                                            blockHash= blockinfo['hash']))

                    contract_session.add(ContractDeposits(  depositorAddress= a_deposit.depositorAddress,
                                                            depositBalance= 0,
                                                            expiryTime = a_deposit.expiryTime,
                                                            unix_expiryTime = a_deposit.unix_expiryTime,
                                                            status='consumed',
                                                            transactionHash= a_deposit.transactionHash,
                                                            blockNumber= blockinfo['height'],
                                                            blockHash= blockinfo['hash']))
                    # ConsumedInfoTable 
                    contract_session.add(ConsumedInfo(  id_deposittable= a_deposit.id,
                                                        transactionHash= a_deposit.transactionHash,
                                                        blockNumber= blockinfo['height']))
                    remaining_amount = perform_decimal_operation('subtraction', remaining_amount, a_deposit.depositBalance)

                    systemdb_session = create_database_session_orm('system_dbs', {'db_name':'system'}, SystemBase)
                    systemdb_entry = systemdb_session.query(TimeActions.activity, TimeActions.contractType, TimeActions.tokens_db, TimeActions.parsed_data).filter(TimeActions.transactionHash == a_deposit.transactionHash).first()
                    systemdb_session.add(TimeActions(
                        time = a_deposit.expiryTime,
                        activity = systemdb_entry[0],
                        status = 'consumed',
                        contractName = parsed_data['contractName'],
                        contractAddress = outputlist[0],
                        contractType = systemdb_entry[1],
                        tokens_db = systemdb_entry[2],
                        parsed_data = systemdb_entry[3],
                        transactionHash = a_deposit.transactionHash,
                        blockNumber = blockinfo['height']
                    ))
                    systemdb_session.commit()
                    del systemdb_session

            # token transfer from the contract to participant's address 
            logger.info(f"Performing 'tokenswapParticipationSettlement' transaction ID {transaction_data['txid']} from participant {outputlist[0]} for {swapAmount} {contractStructure['selling_token']}# ")
            returnval = transferToken(contractStructure['selling_token'], swapAmount, outputlist[0], inputlist[0], transaction_data=transaction_data, parsed_data=parsed_data, isInfiniteToken=None, blockinfo=blockinfo, transactionType='tokenswapParticipationSettlement')
            if returnval == 0:
                logger.info("CRITICAL ERROR | Something went wrong in the token transfer method while doing local Smart Contract Particiaption")
                return 0
            
            # ContractParticipationTable 
            contract_session.add(ContractParticipants(participantAddress = transaction_data['senderAddress'], tokenAmount= parsed_data['tokenAmount'], userChoice= swapPrice, transactionHash= transaction_data['txid'], blockNumber= blockinfo['height'], blockHash= blockinfo['hash'], winningAmount = swapAmount))

            add_contract_transaction_history(contract_name=parsed_data['contractName'], contract_address=outputlist[0], transactionType='participation', transactionSubType='swap', sourceFloAddress=inputlist[0], destFloAddress=outputlist[0], transferAmount=swapAmount, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
            
            contract_session.commit()
            contract_session.close()

            # If this is the first interaction of the participant's address with the given token name, add it to token mapping
            systemdb_connection = create_database_connection('system_dbs', {'db_name':'system'})
            firstInteractionCheck = systemdb_connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{inputlist[0]}' AND token='{contractStructure['selling_token']}'").fetchall()
            if len(firstInteractionCheck) == 0:
                systemdb_connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{inputlist[0]}', '{contractStructure['selling_token']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")
            systemdb_connection.close()

            updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}_{outputlist[0]}", transactionType='tokenswapParticipation')
            pushData_SSEapi(f"Token swap successfully performed at contract {parsed_data['contractName']}_{outputlist[0]} with the transaction {transaction_data['txid']}")

        else:
            # Reject the participation saying not enough deposit tokens are available
            rejectComment = f"Swap participation at transaction {transaction_data['txid']} rejected as requested swap amount is {swapAmount} but {available_deposit_sum} is available"
            logger.info(rejectComment)
            rejected_transaction_history(transaction_data, parsed_data, inputlist[0], outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0

    return 1  # Indicate successful processing of the continuous event transfer




def process_smart_contract_transfer(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd):
    # Check if the smart contract exists
    if check_database_existence('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': f"{outputlist[0]}"}):
        connection = create_database_connection('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"})
        contract_session = create_database_session_orm('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"}, ContractBase)
        
        contractStructure = extract_contractStructure(parsed_data['contractName'], outputlist[0])

        # Fetch the contract type
        contract_type = contract_session.query(ContractStructure.value).filter(ContractStructure.attribute == 'contractType').first()[0]

        # Process based on contract type
        if contract_type == 'one-time-event':
            return process_one_time_event_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd, connection, contract_session, contractStructure)

        elif contract_type == 'continuous-event':
            return process_continuous_event_transfer(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd, connection, contract_session, contractStructure)

        else:
            rejectComment = f"Smart contract transfer at transaction {transaction_data['txid']} rejected due to unknown contract type"
            logger.info(rejectComment)
            rejected_transaction_history(transaction_data, parsed_data, inputlist[0], outputlist[0], rejectComment)
            return 0

    else:
        rejectComment = f"Smart contract transfer at transaction {transaction_data['txid']} rejected as the smart contract does not exist"
        logger.info(rejectComment)
        rejected_transaction_history(transaction_data, parsed_data, inputlist[0], outputlist[0], rejectComment)
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
def process_token_incorporation(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd):
    logger.info("Processing token incorporation...")

    if not is_a_contract_address(inputlist[0]):
        if not check_database_existence('token', {'token_name':f"{parsed_data['tokenIdentification']}"}):
            session = create_database_session_orm('token', {'token_name': f"{parsed_data['tokenIdentification']}"}, TokenBase)
            session.add(ActiveTable(address=inputlist[0], parentid=0, transferBalance=parsed_data['tokenAmount'], addressBalance=parsed_data['tokenAmount'], blockNumber=blockinfo['height']))
            session.add(TransferLogs(sourceFloAddress=inputadd, destFloAddress=outputlist[0],
                                    transferAmount=parsed_data['tokenAmount'], sourceId=0, destinationId=1,
                                    blockNumber=transaction_data['blockheight'], time=transaction_data['time'],
                                    transactionHash=transaction_data['txid']))            
            
            add_transaction_history(token_name=parsed_data['tokenIdentification'], sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash'], blocktime=transaction_data['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=parsed_data['type'], parsedFloData=json.dumps(parsed_data))
            
            session.commit()
            session.close()

            # add it to token address to token mapping db table
            connection = create_database_connection('system_dbs', {'db_name':'system'})
            connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{inputadd}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}');")
            connection.execute(f"INSERT INTO databaseTypeMapping (db_name, db_type, keyword, object_format, blockNumber) VALUES ('{parsed_data['tokenIdentification']}', 'token', '', '', '{transaction_data['blockheight']}')")
            connection.close()

            updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['tokenIdentification']}")
            logger.info(f"Token | Successfully incorporated token {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
            pushData_SSEapi(f"Token | Successfully incorporated token {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
            return 1
        else:
            rejectComment = f"Token incorporation rejected at transaction {transaction_data['txid']} as token {parsed_data['tokenIdentification']} already exists"
            logger.info(rejectComment)
            rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
    else:
        rejectComment = f"Token incorporation at transaction {transaction_data['txid']} rejected as either the input address is part of a contract address"
        logger.info(rejectComment)
        rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
        pushData_SSEapi(rejectComment)
        return 0

#   Rule 48 - If the parsed data type if smart contract incorporation, then check if the name hasn't been taken already
#      if it has been taken then reject the incorporation.
def process_smart_contract_incorporation(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd):
        logger.info(f"Processing smart contract incorporation for transaction {transaction_data['txid']}")

        if not check_database_existence('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{parsed_data['contractAddress']}"}):
            # Cannot incorporate on an address with any previous token transaction
            systemdb_session = create_database_session_orm('system_dbs', {'db_name':'system'}, SystemBase)
            tokenAddressMapping_of_contractAddress = systemdb_session.query(TokenAddressMapping).filter(TokenAddressMapping.tokenAddress == parsed_data['contractAddress']).all()
            if len(tokenAddressMapping_of_contractAddress) == 0:
                # todo Rule 49 - If the contract name hasn't been taken before, check if the contract type is an authorized type by the system
                if parsed_data['contractType'] == 'one-time-event':
                    logger.info("Smart contract is of the type one-time-event")
                    # either userchoice or payeeAddress condition should be present. Check for it
                    if 'userchoices' not in parsed_data['contractConditions'] and 'payeeAddress' not in parsed_data['contractConditions']:
                        rejectComment = f"Either userchoice or payeeAddress should be part of the Contract conditions.\nSmart contract incorporation on transaction {transaction_data['txid']} rejected"
                        logger.info(rejectComment)
                        rejected_contract_transaction_history(transaction_data, parsed_data, 'incorporation', inputadd, inputadd, outputlist[0], rejectComment)
                        delete_contract_database({'contract_name': parsed_data['contractName'], 'contract_address': parsed_data['contractAddress']})
                        return 0

                    # userchoice and payeeAddress conditions cannot come together. Check for it
                    if 'userchoices' in parsed_data['contractConditions'] and 'payeeAddress' in parsed_data['contractConditions']:
                        rejectComment = f"Both userchoice and payeeAddress provided as part of the Contract conditions.\nSmart contract incorporation on transaction {transaction_data['txid']} rejected"
                        logger.info(rejectComment)
                        rejected_contract_transaction_history(transaction_data, parsed_data, 'incorporation', inputadd, inputadd, outputlist[0], rejectComment)
                        delete_contract_database({'contract_name': parsed_data['contractName'], 'contract_address': parsed_data['contractAddress']})
                        return 0

                    # todo Rule 50 - Contract address mentioned in flodata field should be same as the receiver FLO address on the output side
                    #    henceforth we will not consider any flo private key initiated comment as valid from this address
                    #    Unlocking can only be done through smart contract system address
                    if parsed_data['contractAddress'] == inputadd:
                        session = create_database_session_orm('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': f"{parsed_data['contractAddress']}"}, ContractBase)
                        session.add(ContractStructure(attribute='contractType', index=0, value=parsed_data['contractType'], blockNumber=blockinfo['height']))
                        session.add(ContractStructure(attribute='subtype', index=0, value=parsed_data['subtype'], blockNumber=blockinfo['height']))
                        session.add(ContractStructure(attribute='contractName', index=0, value=parsed_data['contractName'], blockNumber=blockinfo['height']))
                        session.add(ContractStructure(attribute='tokenIdentification', index=0, value=parsed_data['tokenIdentification'], blockNumber=blockinfo['height']))
                        session.add(ContractStructure(attribute='contractAddress', index=0, value=parsed_data['contractAddress'], blockNumber=blockinfo['height']))
                        session.add(ContractStructure(attribute='flodata', index=0, value=parsed_data['flodata'], blockNumber=blockinfo['height']))
                        session.add(ContractStructure(attribute='expiryTime', index=0, value=parsed_data['contractConditions']['expiryTime'], blockNumber=blockinfo['height']))
                        session.add(ContractStructure(attribute='unix_expiryTime', index=0, value=parsed_data['contractConditions']['unix_expiryTime'], blockNumber=blockinfo['height']))
                        if 'contractAmount' in parsed_data['contractConditions'].keys():
                            session.add(ContractStructure(attribute='contractAmount', index=0, value=parsed_data['contractConditions']['contractAmount'], blockNumber=blockinfo['height']))

                        if 'minimumsubscriptionamount' in parsed_data['contractConditions']:
                            session.add(ContractStructure(attribute='minimumsubscriptionamount', index=0, value=parsed_data['contractConditions']['minimumsubscriptionamount'], blockNumber=blockinfo['height']))

                        if 'maximumsubscriptionamount' in parsed_data['contractConditions']:
                            session.add(ContractStructure(attribute='maximumsubscriptionamount', index=0, value=parsed_data['contractConditions']['maximumsubscriptionamount'], blockNumber=blockinfo['height']))

                        if 'userchoices' in parsed_data['contractConditions']:
                            for key, value in literal_eval(parsed_data['contractConditions']['userchoices']).items():
                                session.add(ContractStructure(attribute='exitconditions', index=key, value=value, blockNumber=blockinfo['height']))

                        if 'payeeAddress' in parsed_data['contractConditions']:
                            session.add(ContractStructure(attribute='payeeAddress', index=0, value=json.dumps(parsed_data['contractConditions']['payeeAddress']), blockNumber=blockinfo['height']))

                        # Store transfer as part of ContractTransactionHistory
                        add_contract_transaction_history(contract_name=parsed_data['contractName'], contract_address=parsed_data['contractAddress'], transactionType='incorporation', transactionSubType=None, sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=None, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
                        session.commit()
                        session.close()

                        # add Smart Contract name in token contract association
                        blockchainReference = neturl + 'tx/' + transaction_data['txid']
                        session = create_database_session_orm('token', {'token_name': f"{parsed_data['tokenIdentification']}"}, TokenBase)
                        session.add(TokenContractAssociation(tokenIdentification=parsed_data['tokenIdentification'],
                                                            contractName=parsed_data['contractName'],
                                                            contractAddress=parsed_data['contractAddress'],
                                                            blockNumber=transaction_data['blockheight'],
                                                            blockHash=transaction_data['blockhash'],
                                                            time=transaction_data['time'],
                                                            transactionHash=transaction_data['txid'],
                                                            blockchainReference=blockchainReference,
                                                            jsonData=json.dumps(transaction_data),
                                                            transactionType=parsed_data['type'],
                                                            parsedFloData=json.dumps(parsed_data)))
                        session.commit()
                        session.close()

                        # Store smart contract address in system's db, to be ignored during future transfers
                        session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
                        session.add(ActiveContracts(contractName=parsed_data['contractName'],
                                                    contractAddress=parsed_data['contractAddress'], status='active',
                                                    tokenIdentification=parsed_data['tokenIdentification'],
                                                    contractType=parsed_data['contractType'],
                                                    transactionHash=transaction_data['txid'],
                                                    blockNumber=transaction_data['blockheight'],
                                                    blockHash=transaction_data['blockhash'],
                                                    incorporationDate=transaction_data['time']))
                        session.commit()

                        session.add(ContractAddressMapping(address=inputadd, addressType='incorporation',
                                                        tokenAmount=None,
                                                        contractName=parsed_data['contractName'],
                                                        contractAddress=inputadd,
                                                        transactionHash=transaction_data['txid'],
                                                        blockNumber=transaction_data['blockheight'],
                                                        blockHash=transaction_data['blockhash']))

                        session.add(DatabaseTypeMapping(db_name=f"{parsed_data['contractName']}_{inputadd}",
                                                        db_type='smartcontract',
                                                        keyword='',
                                                        object_format='',
                                                        blockNumber=transaction_data['blockheight']))

                        session.add(TimeActions(time=parsed_data['contractConditions']['expiryTime'], 
                                                activity='contract-time-trigger',
                                                status='active',
                                                contractName=parsed_data['contractName'],
                                                contractAddress=inputadd,
                                                contractType='one-time-event-trigger',
                                                tokens_db=json.dumps([parsed_data['tokenIdentification']]),
                                                parsed_data=json.dumps(parsed_data),
                                                transactionHash=transaction_data['txid'],
                                                blockNumber=transaction_data['blockheight']))

                        session.commit()
                        session.close()

                        updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}-{parsed_data['contractAddress']}")

                        pushData_SSEapi('Contract | Contract incorporated at transaction {} with name {}-{}'.format(transaction_data['txid'], parsed_data['contractName'], parsed_data['contractAddress']))
                        return 1
                    else:
                        rejectComment = f"Contract Incorporation on transaction {transaction_data['txid']} rejected as contract address in Flodata and input address are different"
                        logger.info(rejectComment)
                        rejected_contract_transaction_history(transaction_data, parsed_data, 'incorporation', inputadd, inputadd, outputlist[0], rejectComment)
                        pushData_SSEapi(f"Error | Contract Incorporation rejected as address in Flodata and input address are different at transaction {transaction_data['txid']}")
                        delete_contract_database({'contract_name': parsed_data['contractName'], 'contract_address': parsed_data['contractAddress']})
                        return 0
            
                if parsed_data['contractType'] == 'continuous-event' or parsed_data['contractType'] == 'continuos-event':
                    logger.debug("Smart contract is of the type continuous-event")
                    # Add checks to reject the creation of contract
                    if parsed_data['contractAddress'] == inputadd:
                        session = create_database_session_orm('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': f"{parsed_data['contractAddress']}"}, ContractBase)
                        session.add(ContractStructure(attribute='contractType', index=0, value=parsed_data['contractType'], blockNumber=blockinfo['height']))
                        session.add(ContractStructure(attribute='contractName', index=0, value=parsed_data['contractName'], blockNumber=blockinfo['height']))
                        session.add(ContractStructure(attribute='contractAddress', index=0, value=parsed_data['contractAddress'], blockNumber=blockinfo['height']))
                        session.add(ContractStructure(attribute='flodata', index=0, value=parsed_data['flodata'], blockNumber=blockinfo['height']))
                        
                        if parsed_data['stateF'] != {} and parsed_data['stateF'] is not False:
                            for key, value in parsed_data['stateF'].items():
                                session.add(ContractStructure(attribute=f'statef-{key}', index=0, value=value, blockNumber=blockinfo['height']))
                        
                        if 'subtype' in parsed_data['contractConditions']:
                            # todo: Check if the both the tokens mentioned exist if its a token swap
                            if (parsed_data['contractConditions']['subtype'] == 'tokenswap') and (check_database_existence('token', {'token_name':f"{parsed_data['contractConditions']['selling_token'].split('#')[0]}"})) and (check_database_existence('token', {'token_name':f"{parsed_data['contractConditions']['accepting_token'].split('#')[0]}"})):
                                session.add(ContractStructure(attribute='subtype', index=0, value=parsed_data['contractConditions']['subtype'], blockNumber=blockinfo['height']))
                                session.add(ContractStructure(attribute='accepting_token', index=0, value=parsed_data['contractConditions']['accepting_token'], blockNumber=blockinfo['height']))
                                session.add(ContractStructure(attribute='selling_token', index=0, value=parsed_data['contractConditions']['selling_token'], blockNumber=blockinfo['height']))

                                if parsed_data['contractConditions']['pricetype'] not in ['predetermined','statef','dynamic']:
                                    rejectComment = f"pricetype is not part of accepted parameters for a continuos event contract of the type token swap.\nSmart contract incorporation on transaction {transaction_data['txid']} rejected"
                                    logger.info(rejectComment)
                                    rejected_contract_transaction_history(transaction_data, parsed_data, 'incorporation', inputadd, inputadd, outputlist[0], rejectComment)
                                    delete_contract_database({'contract_name': parsed_data['contractName'], 'contract_address': parsed_data['contractAddress']})
                                    return 0
                                
                                # determine price
                                session.add(ContractStructure(attribute='pricetype', index=0, value=parsed_data['contractConditions']['pricetype'], blockNumber=blockinfo['height']))

                                if parsed_data['contractConditions']['pricetype'] in ['predetermined','statef']:
                                    session.add(ContractStructure(attribute='price', index=0, value=parsed_data['contractConditions']['price'], blockNumber=blockinfo['height']))
                                elif parsed_data['contractConditions']['pricetype'] in ['dynamic']:
                                    session.add(ContractStructure(attribute='price', index=0, value=parsed_data['contractConditions']['price'], blockNumber=blockinfo['height']))
                                    session.add(ContractStructure(attribute='oracle_address', index=0, value=parsed_data['contractConditions']['oracle_address'], blockNumber=blockinfo['height']))
                                    
                                # Store transfer as part of ContractTransactionHistory 
                                blockchainReference = neturl + 'tx/' + transaction_data['txid']
                                session.add(ContractTransactionHistory(transactionType='incorporation',
                                                                        sourceFloAddress=inputadd,
                                                                        destFloAddress=outputlist[0],
                                                                        transferAmount=None,
                                                                        blockNumber=transaction_data['blockheight'],
                                                                        blockHash=transaction_data['blockhash'],
                                                                        time=transaction_data['time'],
                                                                        transactionHash=transaction_data['txid'],
                                                                        blockchainReference=blockchainReference,
                                                                        jsonData=json.dumps(transaction_data),
                                                                        parsedFloData=json.dumps(parsed_data)
                                                                        ))
                                session.commit()
                                session.close()

                                # add Smart Contract name in token contract association
                                accepting_sending_tokenlist = [parsed_data['contractConditions']['accepting_token'], parsed_data['contractConditions']['selling_token']]
                                for token_name in accepting_sending_tokenlist:
                                    token_name = token_name.split('#')[0]
                                    session = create_database_session_orm('token', {'token_name': f"{token_name}"}, TokenBase)
                                    session.add(TokenContractAssociation(tokenIdentification=token_name,
                                                                            contractName=parsed_data['contractName'],
                                                                            contractAddress=parsed_data['contractAddress'],
                                                                            blockNumber=transaction_data['blockheight'],
                                                                            blockHash=transaction_data['blockhash'],
                                                                            time=transaction_data['time'],
                                                                            transactionHash=transaction_data['txid'],
                                                                            blockchainReference=blockchainReference,
                                                                            jsonData=json.dumps(transaction_data),
                                                                            transactionType=parsed_data['type'],
                                                                            parsedFloData=json.dumps(parsed_data)))
                                    session.commit()
                                    session.close()

                                # Store smart contract address in system's db, to be ignored during future transfers
                                session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
                                session.add(ActiveContracts(contractName=parsed_data['contractName'],
                                                            contractAddress=parsed_data['contractAddress'], status='active',
                                                            tokenIdentification=str(accepting_sending_tokenlist),
                                                            contractType=parsed_data['contractType'],
                                                            transactionHash=transaction_data['txid'],
                                                            blockNumber=transaction_data['blockheight'],
                                                            blockHash=transaction_data['blockhash'],
                                                            incorporationDate=transaction_data['time']))
                                session.commit()

                                # todo - Add a condition for rejected contract transaction on the else loop for this condition 
                                session.add(ContractAddressMapping(address=inputadd, addressType='incorporation',
                                                                    tokenAmount=None,
                                                                    contractName=parsed_data['contractName'],
                                                                    contractAddress=inputadd,
                                                                    transactionHash=transaction_data['txid'],
                                                                    blockNumber=transaction_data['blockheight'],
                                                                    blockHash=transaction_data['blockhash']))
                                session.add(DatabaseTypeMapping(db_name=f"{parsed_data['contractName']}_{inputadd}",
                                                                    db_type='smartcontract',
                                                                    keyword='',
                                                                    object_format='',
                                                                    blockNumber=transaction_data['blockheight']))
                                session.commit()
                                session.close()
                                updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}-{parsed_data['contractAddress']}")
                                pushData_SSEapi('Contract | Contract incorporated at transaction {} with name {}_{}'.format(transaction_data['txid'], parsed_data['contractName'], parsed_data['contractAddress']))
                                return 1
                        
                            else:
                                rejectComment = f"One of the token for the swap does not exist.\nSmart contract incorporation on transaction {transaction_data['txid']} rejected"
                                logger.info(rejectComment)
                                rejected_contract_transaction_history(transaction_data, parsed_data, 'incorporation', inputadd, inputadd, outputlist[0], rejectComment)
                                delete_contract_database({'contract_name': parsed_data['contractName'], 'contract_address': parsed_data['contractAddress']})
                                return 0

                        else:
                            rejectComment = f"No subtype provided || mentioned tokens do not exist for the Contract of type continuos event.\nSmart contract incorporation on transaction {transaction_data['txid']} rejected"
                            logger.info(rejectComment)
                            rejected_contract_transaction_history(transaction_data, parsed_data, 'incorporation', inputadd, inputadd, outputlist[0], rejectComment)
                            delete_contract_database({'contract_name': parsed_data['contractName'], 'contract_address': parsed_data['contractAddress']})
                            return 0
            
            else:
                rejectComment = f"Smart contract creation transaction {transaction_data['txid']} rejected as token transactions already exist on the address {parsed_data['contractAddress']}"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'incorporation', inputadd, inputadd, outputlist[0], rejectComment)
                delete_contract_database({'contract_name': parsed_data['contractName'], 'contract_address': parsed_data['contractAddress']})
                return 0

        else:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as a Smart Contract with the name {parsed_data['contractName']} at address {parsed_data['contractAddress']} already exists"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'incorporation', inputadd, inputadd, outputlist[0], rejectComment)
            delete_contract_database({'contract_name': parsed_data['contractName'], 'contract_address': parsed_data['contractAddress']})
            return 0

def process_smart_contract_pays(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd):
        logger.info(f"Transaction {transaction_data['txid']} is of the type smartContractPays")
        committeeAddressList = refresh_committee_list(APP_ADMIN, neturl, blockinfo['time'])
        # Check if input address is a committee address
        if inputlist[0] in committeeAddressList:
            # check if the contract exists
            if check_database_existence('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"}):
                # Check if the transaction hash already exists in the contract db (Safety check)
                connection = create_database_connection('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"})
                participantAdd_txhash = connection.execute(f"SELECT sourceFloAddress, transactionHash FROM contractTransactionHistory WHERE transactionType != 'incorporation'").fetchall()
                participantAdd_txhash_T = list(zip(*participantAdd_txhash))

                if len(participantAdd_txhash) != 0 and transaction_data['txid'] in participantAdd_txhash_T[1]:
                    logger.warning(f"Transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db. This is unusual, please check your code")
                    pushData_SSEapi(f"Error | Transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db. This is unusual, please check your code")
                    return 0

                # pull out the contract structure into a dictionary
                contractStructure = extract_contractStructure(parsed_data['contractName'], outputlist[0])

                # if contractAddress has been passed, check if output address is contract Incorporation address
                if 'contractAddress' in contractStructure:
                    if outputlist[0] != contractStructure['contractAddress']:
                        rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]} hasn't expired yet"
                        logger.warning(rejectComment)
                        rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment)
                        pushData_SSEapi(rejectComment)
                        return 0

                # check the type of smart contract ie. external trigger or internal trigger
                if 'payeeAddress' in contractStructure:
                    rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]} has an internal trigger"
                    logger.warning(rejectComment)
                    rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment)
                    pushData_SSEapi(rejectComment)
                    return 0

                # check the status of the contract
                contractStatus = check_contract_status(parsed_data['contractName'], outputlist[0])                
                contractList = []

                if contractStatus == 'closed':
                    rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart contract {parsed_data['contractName']} at the {outputlist[0]} is closed"
                    logger.info(rejectComment)
                    rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment)
                    return 0
                else:
                    session = create_database_session_orm('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': f"{outputlist[0]}"}, ContractBase)
                    result = session.query(ContractStructure).filter_by(attribute='expiryTime').all()
                    session.close()
                    if result:
                        # now parse the expiry time in python
                        expirytime = result[0].value.strip()
                        expirytime_split = expirytime.split(' ')
                        parse_string = '{}/{}/{} {}'.format(expirytime_split[3], parsing.months[expirytime_split[1]], expirytime_split[2], expirytime_split[4])
                        expirytime_object = parsing.arrow.get(parse_string, 'YYYY/M/D HH:mm:ss').replace(tzinfo=expirytime_split[5][3:])
                        blocktime_object = parsing.arrow.get(transaction_data['time']).to('Asia/Kolkata')

                        if blocktime_object <= expirytime_object:
                            rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart contract {parsed_data['contractName']}_{outputlist[0]} has not expired and will not trigger"
                            logger.info(rejectComment)
                            rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment)
                            pushData_SSEapi(rejectComment)
                            return 0

                # check if the user choice passed is part of the contract structure
                tempchoiceList = []
                for item in contractStructure['exitconditions']:
                    tempchoiceList.append(contractStructure['exitconditions'][item])

                if parsed_data['triggerCondition'] not in tempchoiceList:
                    rejectComment = f"Transaction {transaction_data['txid']} rejected as triggerCondition, {parsed_data['triggerCondition']}, has been passed to Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]} which doesn't accept any userChoice of the given name"
                    logger.info(rejectComment)
                    rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment)
                    pushData_SSEapi(rejectComment)
                    return 0
                
                systemdb_session = create_database_session_orm('system_dbs', {'db_name':'system'}, SystemBase)
                        
                activecontracts_table_info = systemdb_session.query(ActiveContracts.blockHash, ActiveContracts.incorporationDate, ActiveContracts.expiryDate).filter(ActiveContracts.contractName==parsed_data['contractName'], ActiveContracts.contractAddress==outputlist[0], ActiveContracts.status=='expired').first()  
                
                timeactions_table_info = systemdb_session.query(TimeActions.time, TimeActions.activity, TimeActions.contractType, TimeActions.tokens_db, TimeActions.parsed_data).filter(TimeActions.contractName==parsed_data['contractName'], TimeActions.contractAddress==outputlist[0], TimeActions.status=='active').first() 

                # check if minimumsubscriptionamount exists as part of the contract structure
                if 'minimumsubscriptionamount' in contractStructure:
                    # if it has not been reached, close the contract and return money
                    minimumsubscriptionamount = float(contractStructure['minimumsubscriptionamount'])
                    session = create_database_session_orm('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': f"{outputlist[0]}"}, ContractBase)
                    
                    # amountDeposited = session.query(func.sum(ContractParticipants.tokenAmount)).all()[0][0]
                    query_data = session.query(ContractParticipants.tokenAmount).all()
                    amountDeposited = sum(Decimal(f"{amount[0]}") if amount[0] is not None else Decimal(0) for amount in query_data)
                    session.close()

                    if amountDeposited is None:
                        amountDeposited = 0

                    if amountDeposited < minimumsubscriptionamount:
                        # close the contract and return the money
                        logger.info('Minimum subscription amount hasn\'t been reached\n The token will be returned back')
                        # Initialize payback to contract participants
                        connection = create_database_connection('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"})
                        contractParticipants = connection.execute('SELECT participantAddress, tokenAmount, transactionHash FROM contractparticipants').fetchall()[0][0]

                        for participant in contractParticipants:
                            tokenIdentification = connection.execute('SELECT * FROM contractstructure WHERE attribute="tokenIdentification"').fetchall()[0][0]
                            contractAddress = connection.execute('SELECT value FROM contractstructure WHERE attribute="contractAddress"').fetchall()[0][0]
                            returnval = transferToken(tokenIdentification, participant[1], contractAddress, participant[0], transaction_data, parsed_data, blockinfo = blockinfo)
                            if returnval == 0:
                                logger.info("CRITICAL ERROR | Something went wrong in the token transfer method while doing local Smart Contract Trigger")
                                return 0

                            connection.execute('update contractparticipants set winningAmount="{}" where participantAddress="{}" and transactionHash="{}"'.format((participant[1], participant[0], participant[4])))

                        # add transaction to ContractTransactionHistory
                        blockchainReference = neturl + 'tx/' + transaction_data['txid']
                        session = create_database_session_orm('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': f"{outputlist[0]}"}, ContractBase)
                        session.add(ContractTransactionHistory(transactionType='trigger',
                                                               transactionSubType='minimumsubscriptionamount-payback',
                                                               sourceFloAddress=inputadd,
                                                               destFloAddress=outputlist[0],
                                                               transferAmount=None,
                                                               blockNumber=transaction_data['blockheight'],
                                                               blockHash=transaction_data['blockhash'],
                                                               time=transaction_data['time'],
                                                               transactionHash=transaction_data['txid'],
                                                               blockchainReference=blockchainReference,
                                                               jsonData=json.dumps(transaction_data),
                                                               parsedFloData=json.dumps(parsed_data)
                                                               ))
                        session.commit()
                        session.close()                       

                        close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, activecontracts_table_info.expiryDate, blockinfo['time'], timeactions_table_info.time, timeactions_table_info.activity, parsed_data['contractName'], outputlist[0], timeactions_table_info.contractType, timeactions_table_info.tokens_db, timeactions_table_info.parsed_data, blockinfo['height'])

                        updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}_{outputlist[0]}")
                        pushData_SSEapi('Trigger | Minimum subscription amount not reached at contract {}_{} at transaction {}. Tokens will be refunded'.format(parsed_data['contractName'], outputlist[0], transaction_data['txid']))
                        return 1

                # Trigger the contract
                connection = create_database_connection('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"})
                rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
                tokenSum = float(sum(Decimal(f"{row[0]}") for row in rows))
                
                if tokenSum > 0:
                    contractWinners = connection.execute('SELECT * FROM contractparticipants WHERE userChoice="{}"'.format(parsed_data['triggerCondition'])).fetchall()

                    rows = connection.execute('SELECT tokenAmount FROM contractparticipants WHERE userChoice="{}"'.format(parsed_data['triggerCondition'])).fetchall()
                    winnerSum = float(sum(Decimal(f"{row[0]}") for row in rows))

                    tokenIdentification = connection.execute('SELECT value FROM contractstructure WHERE attribute="tokenIdentification"').fetchall()[0][0]

                    for winner in contractWinners:
                        winnerAmount = "%.8f" % perform_decimal_operation('multiplication', perform_decimal_operation('division', winner[2], winnerSum), tokenSum)
                        returnval = transferToken(tokenIdentification, winnerAmount, outputlist[0], winner[1], transaction_data, parsed_data, blockinfo = blockinfo)
                        if returnval == 0:
                            logger.critical("Something went wrong in the token transfer method while doing local Smart Contract Trigger")
                            return 0
                        
                        connection.execute(f"INSERT INTO contractwinners (participantAddress, winningAmount, userChoice, transactionHash, blockNumber, blockHash, referenceTxHash) VALUES('{winner[1]}', {winnerAmount}, '{parsed_data['triggerCondition']}', '{transaction_data['txid']}','{blockinfo['height']}','{blockinfo['hash']}', '{winner[4]}');")

                # add transaction to ContractTransactionHistory
                blockchainReference = neturl + 'tx/' + transaction_data['txid']
                session.add(ContractTransactionHistory(transactionType='trigger',
                                                       transactionSubType='committee',
                                                       sourceFloAddress=inputadd,
                                                       destFloAddress=outputlist[0],
                                                       transferAmount=None,
                                                       blockNumber=transaction_data['blockheight'],
                                                       blockHash=transaction_data['blockhash'],
                                                       time=transaction_data['time'],
                                                       transactionHash=transaction_data['txid'],
                                                       blockchainReference=blockchainReference,
                                                       jsonData=json.dumps(transaction_data),
                                                       parsedFloData=json.dumps(parsed_data)
                                                       ))
                session.commit()
                session.close()

                close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, activecontracts_table_info.expiryDate, blockinfo['time'], timeactions_table_info['time'], 'contract-time-trigger', contractStructure['contractName'], contractStructure['contractAddress'], contractStructure['contractType'], timeactions_table_info.tokens_db, timeactions_table_info.parsed_data, blockinfo['height'])                

                updateLatestTransaction(transaction_data, parsed_data, f"{contractStructure['contractName']}_{contractStructure['contractAddress']}")

                pushData_SSEapi('Trigger | Contract triggered of the name {}_{} is active currently at transaction {}'.format(parsed_data['contractName'], outputlist[0], transaction_data['txid']))
                return 1
            else:
                rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]} doesn't exist"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'trigger', outputlist[0], inputadd, outputlist[0], rejectComment)
                pushData_SSEapi(rejectComment)
                return 0

        else:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as input address, {inputlist[0]}, is not part of the committee address list"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0


def process_smart_contract_deposit(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd):
        if check_database_existence('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"}):
            # Reject if the deposit expiry time is greater than incorporated blocktime
            expiry_time = convert_datetime_to_arrowobject(parsed_data['depositConditions']['expiryTime'])
            if blockinfo['time'] > expiry_time.timestamp():
                rejectComment = f"Contract deposit of transaction {transaction_data['txid']} rejected as expiryTime before current block time"
                logger.warning(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'deposit', outputlist[0], inputadd, outputlist[0], rejectComment)
                return 0

            # Check if the transaction hash already exists in the contract db (Safety check)
            connection = create_database_connection('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"})
            participantAdd_txhash = connection.execute('SELECT participantAddress, transactionHash FROM contractparticipants').fetchall()
            participantAdd_txhash_T = list(zip(*participantAdd_txhash))

            if len(participantAdd_txhash) != 0 and transaction_data['txid'] in list(participantAdd_txhash_T[1]):
                rejectComment = f"Contract deposit at transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db. This is unusual, please check your code"
                logger.warning(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'deposit', outputlist[0], inputadd, outputlist[0], rejectComment)
                return 0

            # if contractAddress was passed, then check if it matches the output address of this contract
            if 'contractAddress' in parsed_data:
                if parsed_data['contractAddress'] != outputlist[0]:
                    rejectComment = f"Contract deposit at transaction {transaction_data['txid']} rejected as contractAddress specified in flodata, {parsed_data['contractAddress']}, doesnt not match with transaction's output address {outputlist[0]}"
                    logger.info(rejectComment)
                    rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                    # Pass information to SSE channel
                    pushData_SSEapi(f"Error| Mismatch in contract address specified in flodata and the output address of the transaction {transaction_data['txid']}")
                    return 0

            # pull out the contract structure into a dictionary
            contractStructure = extract_contractStructure(parsed_data['contractName'], outputlist[0])

            # Transfer the token 
            logger.info(f"Initiating transfers for smartcontract deposit with transaction ID {transaction_data['txid']}")
            returnval = transferToken(parsed_data['tokenIdentification'], parsed_data['depositAmount'], inputlist[0], outputlist[0], transaction_data, parsed_data, blockinfo=blockinfo)
            if returnval == 0:
                logger.info("Something went wrong in the token transfer method")
                pushData_SSEapi(f"Error | Something went wrong while doing the internal db transactions for {transaction_data['txid']}")
                return 0 

            # Push the deposit transaction into deposit database contract database 
            session = create_database_session_orm('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': f"{outputlist[0]}"}, ContractBase)
            blockchainReference = neturl + 'tx/' + transaction_data['txid']
            session.add(ContractDeposits(depositorAddress = inputadd, depositAmount = parsed_data['depositAmount'], depositBalance = parsed_data['depositAmount'], expiryTime = parsed_data['depositConditions']['expiryTime'], unix_expiryTime = convert_datetime_to_arrowobject(parsed_data['depositConditions']['expiryTime']).timestamp(), status = 'active', transactionHash = transaction_data['txid'], blockNumber = transaction_data['blockheight'], blockHash = transaction_data['blockhash']))
            session.add(ContractTransactionHistory(transactionType = 'smartContractDeposit',
                                                    transactionSubType = None,
                                                    sourceFloAddress = inputadd,
                                                    destFloAddress = outputlist[0],
                                                    transferAmount = parsed_data['depositAmount'],
                                                    blockNumber = transaction_data['blockheight'],
                                                    blockHash = transaction_data['blockhash'],
                                                    time = transaction_data['time'],
                                                    transactionHash = transaction_data['txid'],
                                                    blockchainReference = blockchainReference,
                                                    jsonData = json.dumps(transaction_data),
                                                    parsedFloData = json.dumps(parsed_data)
                                                    ))
            session.commit()
            session.close()

            session = create_database_session_orm('system_dbs', {'db_name': f"system"}, SystemBase)
            session.add(TimeActions(time=parsed_data['depositConditions']['expiryTime'], 
                                    activity='contract-deposit',
                                    status='active',
                                    contractName=parsed_data['contractName'],
                                    contractAddress=outputlist[0],
                                    contractType='continuos-event-swap',
                                    tokens_db=f"{parsed_data['tokenIdentification']}",
                                    parsed_data=json.dumps(parsed_data),
                                    transactionHash=transaction_data['txid'],
                                    blockNumber=transaction_data['blockheight']))
            session.commit()
            pushData_SSEapi(f"Deposit Smart Contract Transaction {transaction_data['txid']} for the Smart contract named {parsed_data['contractName']} at the address {outputlist[0]}")

            # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
            systemdb_connection = create_database_connection('system_dbs', {'db_name':'system'})
            firstInteractionCheck = systemdb_connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{outputlist[0]}' AND token='{parsed_data['tokenIdentification']}'").fetchall()
            if len(firstInteractionCheck) == 0:
                systemdb_connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{outputlist[0]}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")
            systemdb_connection.close()
            
            updateLatestTransaction(transaction_data, parsed_data , f"{parsed_data['contractName']}_{outputlist[0]}")
            return 1

        else:
            rejectComment = f"Deposit Transaction {transaction_data['txid']} rejected as a Smart Contract with the name {parsed_data['contractName']} at address {outputlist[0]} doesnt exist"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractDeposit', outputlist[0], inputadd, outputlist[0], rejectComment)
            return 0

def process_nft_incorporation(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd):
    '''
            DIFFERENT BETWEEN TOKEN AND NFT
            System.db will have a different entry
            in creation nft word will be extra
            NFT Hash must be present
            Creation and transfer amount .. only integer parts will be taken
            Keyword nft must be present in both creation and transfer
    '''

    if not is_a_contract_address(inputlist[0]):
        if not check_database_existence('token', {'token_name': f"{parsed_data['tokenIdentification']}"}):
            session = create_database_session_orm('token', {'token_name': f"{parsed_data['tokenIdentification']}"}, TokenBase)
            session.add(ActiveTable(address=inputlist[0], parentid=0, transferBalance=parsed_data['tokenAmount'], addressBalance=parsed_data['tokenAmount'], blockNumber=blockinfo['height']))
            session.add(TransferLogs(sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], sourceId=0, destinationId=1, blockNumber=transaction_data['blockheight'], time=transaction_data['time'], transactionHash=transaction_data['txid']))
            add_transaction_history(token_name=parsed_data['tokenIdentification'], sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash'], blocktime=transaction_data['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=parsed_data['type'], parsedFloData=json.dumps(parsed_data))

            session.commit()
            session.close()

            # Add it to token address to token mapping db table
            connection = create_database_connection('system_dbs', {'db_name': 'system'})
            connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{inputadd}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}');")
            nft_data = {'sha256_hash': f"{parsed_data['nftHash']}"}
            connection.execute(f"INSERT INTO databaseTypeMapping (db_name, db_type, keyword, object_format, blockNumber) VALUES ('{parsed_data['tokenIdentification']}', 'nft', '', '{json.dumps(nft_data)}', '{transaction_data['blockheight']}')")
            connection.close()

            updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['tokenIdentification']}")
            pushData_SSEapi(f"NFT | Successfully incorporated NFT {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
            return 1
        else:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as an NFT with the name {parsed_data['tokenIdentification']} has already been incorporated"
            logger.info(rejectComment)
            rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
    else:
        rejectComment = f"NFT incorporation at transaction {transaction_data['txid']} rejected as either the input address is part of a contract address"
        logger.info(rejectComment)
        rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
        pushData_SSEapi(rejectComment)
        return 0

def process_infinite_token_incorporation(parsed_data, transaction_data, blockinfo, inputlist, outputlist, inputadd):
    logger.info(f"Processing infinite token incorporation for transaction {transaction_data['txid']}")

    # Ensure that neither the input nor output addresses are contract addresses
    if not is_a_contract_address(inputlist[0]) and not is_a_contract_address(outputlist[0]):
        # Check if the token already exists in the database
        if not check_database_existence('token', {'token_name': f"{parsed_data['tokenIdentification']}"}):
            parsed_data['tokenAmount'] = 0

            # Create a session to manage token incorporation
            try:
                tokendb_session = create_database_session_orm('token', {'token_name': f"{parsed_data['tokenIdentification']}"}, TokenBase)

                # Add initial token data to the database
                tokendb_session.add(
                    ActiveTable(
                        address=inputlist[0],
                        parentid=0,
                        transferBalance=parsed_data['tokenAmount'],
                        blockNumber=blockinfo['height']
                    )
                )
                tokendb_session.add(
                    TransferLogs(
                        sourceFloAddress=inputadd,
                        destFloAddress=outputlist[0],
                        transferAmount=parsed_data['tokenAmount'],
                        sourceId=0,
                        destinationId=1,
                        blockNumber=transaction_data['blockheight'],
                        time=transaction_data['time'],
                        transactionHash=transaction_data['txid']
                    )
                )

                # Add the transaction history for the token
                add_transaction_history(
                    token_name=parsed_data['tokenIdentification'],
                    sourceFloAddress=inputadd,
                    destFloAddress=outputlist[0],
                    transferAmount=parsed_data['tokenAmount'],
                    blockNumber=transaction_data['blockheight'],
                    blockHash=transaction_data['blockhash'],
                    blocktime=blockinfo['time'],
                    transactionHash=transaction_data['txid'],
                    jsonData=json.dumps(transaction_data),
                    transactionType=parsed_data['type'],
                    parsedFloData=json.dumps(parsed_data)
                )

                # Add to token address mapping
                connection = create_database_connection('system_dbs', {'db_name': 'system'})
                connection.execute("INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES (%s, %s, %s, %s, %s)", (inputadd, parsed_data['tokenIdentification'], transaction_data['txid'], transaction_data['blockheight'], transaction_data['blockhash']))


                # Add to database type mapping
                info_object = {'root_address': inputadd}
                connection.execute("INSERT INTO databaseTypeMapping (db_name, db_type, keyword, object_format, blockNumber) VALUES (%s, %s, %s, %s, %s)", (parsed_data['tokenIdentification'], 'infinite-token', '', json.dumps(info_object), transaction_data['blockheight']))


                # Commit the session and close connections
                updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['tokenIdentification']}")
                tokendb_session.commit()
                logger.info(f"Token | Successfully incorporated token {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")

            except Exception as e:
                logger.error(f"Error during infinite token incorporation: {e}")
                return 0
            finally:
                connection.close()
                tokendb_session.close()

            pushData_SSEapi(f"Token | Successfully incorporated token {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
            return 1
        else:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as a token with the name {parsed_data['tokenIdentification']} has already been incorporated"
            logger.info(rejectComment)
            rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0
    else:
        rejectComment = f"Infinite token incorporation at transaction {transaction_data['txid']} rejected as either the input address or output address is part of a contract address"
        logger.info(rejectComment)
        rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
        pushData_SSEapi(rejectComment)
        return 0



# Main processing functions START

def processTransaction(transaction_data, parsed_data, blockinfo):
    
    inputlist, outputlist, inputadd = process_flo_checks(transaction_data)

    if inputlist is None or outputlist is None:
        return 0

    logger.info(f"Input address list : {inputlist}")
    logger.info(f"Output address list : {outputlist}")

    transaction_data['senderAddress'] = inputlist[0]
    transaction_data['receiverAddress'] = outputlist[0]

    # Process transaction based on type
    if parsed_data['type'] == 'transfer':
        logger.info(f"Transaction {transaction_data['txid']} is of the type transfer")

        if parsed_data['transferType'] == 'token':
            return process_token_transfer(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd)
        elif parsed_data['transferType'] == 'smartContract':
            return process_smart_contract_transfer(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd)
        elif parsed_data['transferType'] == 'nft':
            return process_nft_transfer(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd)
        else:
            logger.info(f"Invalid transfer type in transaction {transaction_data['txid']}")
            return 0

    elif parsed_data['type'] == 'tokenIncorporation':
        logger.info(f"Transaction {transaction_data['txid']} is of the type tokenIncorporation")
        return process_token_incorporation(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd)

    elif parsed_data['type'] == 'smartContractIncorporation':
        logger.info(f"Transaction {transaction_data['txid']} is of the type smartContractIncorporation")
        return process_smart_contract_incorporation(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd)

    elif parsed_data['type'] == 'smartContractPays':
        logger.info(f"Transaction {transaction_data['txid']} is of the type smartContractPays")
        return process_smart_contract_pays(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd)

    elif parsed_data['type'] == 'smartContractDeposit':
        logger.info(f"Transaction {transaction_data['txid']} is of the type smartContractDeposit")
        return process_smart_contract_deposit(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd)

    elif parsed_data['type'] == 'nftIncorporation':
        logger.info(f"Transaction {transaction_data['txid']} is of the type nftIncorporation")
        return process_nft_incorporation(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd)

    elif parsed_data['type'] == 'infiniteTokenIncorporation':
        logger.info(f"Transaction {transaction_data['txid']} is of the type infiniteTokenIncorporation")
        return process_infinite_token_incorporation(parsed_data, transaction_data, blockinfo,inputlist, outputlist, inputadd)

    else:
        logger.info(f"Transaction {transaction_data['txid']} rejected as it doesn't belong to any valid type")
        return 0

    return 1

def scanBlockchain():
    """
    Scans the blockchain from the last scanned block to the current block height,
    processing each block unless it's in the IGNORE_BLOCK_LIST.
    """
    while True:
        try:
            session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
            entry = session.query(SystemData).filter_by(attribute='lastblockscanned').first()
            startblock = int(entry.value) + 1 if entry else 0
            session.commit()
            session.close()
            break
        except:
            logger.info(f"Unable to connect to 'system' database... retrying in {DB_RETRY_TIMEOUT} seconds")
            time.sleep(DB_RETRY_TIMEOUT)

    # Get latest block height with built-in retry
    current_index = fetch_current_block_height()

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
    i = 0
    newurl = neturl
    while not socket_variable.connected:
        logger.info(f"While loop {i} - attempting reconnect to {newurl}")
        time.sleep(3)
        try:
            scanBlockchain()
            logger.info(f"Websocket connecting to {newurl}/socket.io/socket.io.js")
            socket_variable.connect(f"{newurl.replace('https', 'wss')}/socket.io/socket.io.js")
            logger.info(f"Websocket reconnected successfully at {newurl}")
            break  # Optional: break out once connected
        except Exception as e:
            logger.warning(f"Reconnect failed for {newurl}: {e}")
            newurl = switchNeturl(newurl)
            i += 1



def get_websocket_uri():
    current = neturl
    if config['DEFAULT']['NET'] == 'testnet':
        return f"{current.replace('https', 'wss')}/websocket"
    return f"{current.replace('https', 'wss')}/websocket"


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
                    if 'height' in response['data'].keys():
                        if response['data']['height'] is None or response['data']['height']=='':
                            print('blockheight is none')
                            # todo: remove these debugger lines
                        if response['data']['hash'] is None or response['data']['hash']=='':
                            print('blockhash is none')
                            # todo: remove these debugger lines
                            # If this is the issue need to proceed forward only once blockbook has consolitated 
                        processBlock(blockindex=response['data']['height'], blockhash=response['data']['hash'])
        
        except Exception as e:
            logger.info(f"Connection error: {e}")
            # Add a delay before attempting to reconnect
            await asyncio.sleep(5)  # You can adjust the delay as needed
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
args = parser.parse_args()

# Determine network
NET = 'testnet' if args.testnet else config['DEFAULT'].get('NET', 'mainnet').strip().lower()
if NET not in ['mainnet', 'testnet']:
    logger.error("NET in config.ini must be either 'mainnet' or 'testnet'")
    sys.exit(1)

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
websocket_uri = f"{neturl.replace('https', 'wss')}/websocket"

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
    ensure_database_exists(system_db_name, lambda: init_system_db(int(config['DEFAULT']['START_BLOCK'])))

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
        init_storage_if_not_exist(reset=True, exclude_backups=True)  # Pass a flag to avoid dropping backup databases
    except Exception as e:
        logger.error(f"Failed to reset databases: {e}")
        return

    # Step 3: Extract last block scanned from backup
    try:
        logger.info("Extracting last block scanned from backup system database...")
        backup_engine = create_engine(f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{system_backup_db}", echo=False)
        with sessionmaker(bind=backup_engine)() as session:
            last_block_scanned_entry = session.query(SystemData).filter_by(attribute='lastblockscanned').first()
            if not last_block_scanned_entry:
                raise ValueError("No 'lastblockscanned' entry found in backup system database.")
            last_block_scanned = int(last_block_scanned_entry.value)
            logger.info(f"Last block scanned retrieved: {last_block_scanned}")
    except Exception as e:
        logger.error(f"Failed to retrieve lastblockscanned from backup system database: {e}")
        return

    # Step 4: Reprocess blocks from the backup
    try:
        logger.info("Starting reprocessing of blocks from backup...")
        backup_engine = create_engine(f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{latestcache_backup_db}", echo=False)
        with sessionmaker(bind=backup_engine)() as session:
            stored_blocks = session.query(LatestBlocks).order_by(LatestBlocks.blockNumber).all()
            if not stored_blocks:
                logger.warning("No blocks found in backed-up latestCache database. Aborting rebuild.")
                return

            for block_entry in stored_blocks:
                try:
                    blockinfo = json.loads(block_entry.jsonData)
                    block_number = blockinfo.get("height", block_entry.blockNumber)
                    block_hash = blockinfo.get("hash", block_entry.blockHash)

                    logger.info(f"Reprocessing block {block_number} with hash {block_hash}...")
                    processBlock(blockindex=block_number, blockhash=block_hash, blockinfo=blockinfo, keywords=keywords)
                except Exception as e:
                    logger.error(f"Error processing block {block_entry.blockNumber}: {e}")
        logger.info("Rebuild of latestCache database completed successfully.")
    except Exception as e:
        logger.error(f"Error during rebuild of latestCache database from backup: {e}")
        return

    # Step 5: Update lastblockscanned in the new system database
    try:
        logger.info("Updating lastblockscanned in the new system database...")
        engine = create_engine(f"mysql+pymysql://{mysql_config.username}:{mysql_config.password}@{mysql_config.host}/{system_db}", echo=False)
        with sessionmaker(bind=engine)() as session:
            entry = session.query(SystemData).filter_by(attribute='lastblockscanned').first()
            if entry:
                entry.value = str(last_block_scanned)
            else:
                session.add(SystemData(attribute='lastblockscanned', value=str(last_block_scanned)))
            session.commit()
            logger.info(f"Updated lastblockscanned to {last_block_scanned}.")
    except Exception as e:
        logger.error(f"Failed to update lastblockscanned: {e}")
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
        logger.error(f"Error processing remaining blocks: {e}")





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
    