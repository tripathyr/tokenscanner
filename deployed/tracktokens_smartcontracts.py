import argparse 
import configparser 
import json
import logging 
import os 
import shutil 
import sys 
#import pyflo 
import requests 
from sqlalchemy import create_engine, func, and_  
from sqlalchemy.orm import sessionmaker 
import time 
import arrow 
import parsing 
from parsing import perform_decimal_operation
import re 
from datetime import datetime 
from ast import literal_eval 
from models import SystemData, TokenBase, ActiveTable, ConsumedTable, TransferLogs, TransactionHistory, TokenContractAssociation, ContractBase, ContractStructure, ContractParticipants, ContractTransactionHistory, ContractDeposits, ConsumedInfo, ContractWinners, ContinuosContractBase, ContractStructure2, ContractParticipants2, ContractDeposits2, ContractTransactionHistory2, SystemBase, ActiveContracts, SystemData, ContractAddressMapping, TokenAddressMapping, DatabaseTypeMapping, TimeActions, RejectedContractTransactionHistory, RejectedTransactionHistory, LatestCacheBase, LatestTransactions, LatestBlocks 
from statef_processing import process_stateF 
import asyncio
import websockets
from decimal import Decimal
import pdb
from util_rollback import rollback_to_block


RETRY_TIMEOUT_LONG = 30 * 60 # 30 mins
RETRY_TIMEOUT_SHORT = 60 # 1 min
DB_RETRY_TIMEOUT = 60 # 60 seconds


def newMultiRequest(apicall):
    current_server = serverlist[0]
    while True:
        try:
            response = requests.get(f"{current_server}api/{apicall}", verify=API_VERIFY)
            logger.info(f"Called the API {current_server}api/{apicall}")
            if response.status_code == 200:
                return response.json()  # Use the built-in .json() method
            else:
                logger.info(f"Response status code - \n{response.status_code}")
                logger.info(f"Response content -\n{response.content}")
                raise Exception("Non-200 status code")
        except Exception as e:
            logger.info(f"newMultiRequest() exception: {e}. Switching server...")
            current_server = switchNeturl(current_server)
            logger.info(f"newMultiRequest() switched to {current_server}")
            time.sleep(2)


def pushData_SSEapi(message):
    '''signature = pyflo.sign_message(message.encode(), privKey)
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'Signature': signature}

    try:
        r = requests.post(sseAPI_url, json={'message': '{}'.format(message)}, headers=headers)
    except:
    logger.error("couldn't push the following message to SSE api {}".format(message))'''
    print('')


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
    response = requests.get(f'{api_url}api/v1/address/{admin_flo_id}', verify=API_VERIFY)
    if response.status_code == 200:
        response = response.json()
    else:
        logger.info('Response from the Flosight API failed')
        sys.exit(0)

    committee_list = []
    response['transactions'].reverse()
    for idx, transaction in enumerate(response['transactions']):
        transaction_info = requests.get(f'{api_url}api/v1/tx/{transaction}', verify=API_VERIFY)
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
                    logger.info(f'Response from the Flosight API failed. Retry in {RETRY_TIMEOUT_SHORT}s')
                    #sys.exit(0)
                    time.sleep(RETRY_TIMEOUT_SHORT)
            except:
                logger.info(f'Fetch from the Flosight API failed. Retry in {RETRY_TIMEOUT_LONG}s...')
                time.sleep(RETRY_TIMEOUT_LONG)

    url = f'{api_url}api/address/{admin_flo_id}?details=txs'
    response = send_api_request(url)
    for transaction_info in response.get('txs', []):
        process_transaction(transaction_info)

    while 'incomplete' in response:
        url = f'{api_url}api/address/{admin_flo_id}/txs?latest={latest_param}&mempool={mempool_param}&before={init_id}'
        response = send_api_request(url)
        for transaction_info in response.get('items', []):
            process_transaction(transaction_info)
        if 'incomplete' in response:
            init_id = response['initItem']

    return committee_list


def find_sender_receiver(transaction_data):
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
    if type == 'token':
        path = os.path.join(config['DEFAULT']['DATA_PATH'], 'tokens', f'{parameters["token_name"]}.db')
        return os.path.isfile(path)
    
    if type == 'smart_contract':
        path = os.path.join(config['DEFAULT']['DATA_PATH'], 'smartContracts', f"{parameters['contract_name']}-{parameters['contract_address']}.db")
        return os.path.isfile(path)


def create_database_connection(type, parameters=None):
    if type == 'token':
        path = os.path.join(config['DEFAULT']['DATA_PATH'], 'tokens', f"{parameters['token_name']}.db")
        engine = create_engine(f"sqlite:///{path}", echo=True)
    elif type == 'smart_contract':
        path = os.path.join(config['DEFAULT']['DATA_PATH'], 'smartContracts', f"{parameters['contract_name']}-{parameters['contract_address']}.db")
        engine = create_engine(f"sqlite:///{path}", echo=True)
    elif type == 'system_dbs':
        path = os.path.join(config['DEFAULT']['DATA_PATH'], f"system.db")
        engine = create_engine(f"sqlite:///{path}", echo=False)
    elif type == 'latest_cache':
        path = os.path.join(config['DEFAULT']['DATA_PATH'], f"latestCache.db")
        engine = create_engine(f"sqlite:///{path}", echo=False)

    connection = engine.connect()
    return connection


def create_database_session_orm(type, parameters, base):
    if type == 'token':
        path = os.path.join(config['DEFAULT']['DATA_PATH'], 'tokens', f"{parameters['token_name']}.db")
        engine = create_engine(f"sqlite:///{path}", echo=True)
        base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()

    elif type == 'smart_contract':
        path = os.path.join(config['DEFAULT']['DATA_PATH'], 'smartContracts', f"{parameters['contract_name']}-{parameters['contract_address']}.db")
        engine = create_engine(f"sqlite:///{path}", echo=True)
        base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()

    elif type == 'system_dbs':
        path = os.path.join(config['DEFAULT']['DATA_PATH'], f"{parameters['db_name']}.db")
        engine = create_engine(f"sqlite:///{path}", echo=False)
        base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()
    
    return session


def delete_contract_database(parameters):
    if check_database_existence('smart_contract', {'contract_name':f"{parameters['contract_name']}", 'contract_address':f"{parameters['contract_address']}"}):
        path = os.path.join(config['DEFAULT']['DATA_PATH'], 'smartContracts', f"{parameters['contract_name']}-{parameters['contract_address']}.db")
        os.remove(path)


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


def rejected_transaction_history(transaction_data, parsed_data, sourceFloAddress, destFloAddress, rejectComment):
    while True:
        try:
            session = create_database_session_orm('system_dbs', {'db_name': "system"}, TokenBase)
            blockchainReference = neturl + 'tx/' + transaction_data['txid']
            session.add(RejectedTransactionHistory(tokenIdentification=parsed_data['tokenIdentification'],
                                                sourceFloAddress=sourceFloAddress, destFloAddress=destFloAddress,
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
            session.commit()
            session.close()
            break
        except:
            logger.info(f"Unable to connect to 'system' database... retrying in {DB_RETRY_TIMEOUT} seconds")
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

""" ? NOT USED ?
def fetchDynamicSwapPrice_old(contractStructure, transaction_data, blockinfo):
    oracle_address = contractStructure['oracle_address']
    # fetch transactions from the blockchain where from address : oracle-address... to address: contract address
    # find the first contract transaction which adheres to price change format
    # {"price-update":{"contract-name": "", "contract-address": "", "price": 3}}
    response = requests.get(f'{neturl}api/v1/address/{oracle_address}', verify=API_VERIFY)
    if response.status_code == 200:
        response = response.json()
        if 'transactions' not in response.keys(): # API doesn't return 'transactions' key, if 0 txs present on address
            return float(contractStructure['price'])
        else:
            transactions = response['transactions']
            for transaction_hash in transactions:
                transaction_response = requests.get(f'{neturl}api/v1/tx/{transaction_hash}', verify=API_VERIFY)
                if transaction_response.status_code == 200:
                    transaction = transaction_response.json()
                    floData = transaction['floData']
                    # If the blocktime of the transaction is < than the current block time
                    if transaction['time'] < blockinfo['time']:
                        # Check if flodata is in the format we are looking for
                        # ie. {"price-update":{"contract-name": "", "contract-address": "", "price": 3}}
                        # and receiver address should be contractAddress
                        try:
                            assert transaction_data['receiverAddress'] == contractStructure['contractAddress']
                            assert find_sender_receiver(transaction)[0] == oracle_address
                            floData = json.loads(floData)
                            # Check if the contract name and address are right
                            assert floData['price-update']['contract-name'] == contractStructure['contractName']
                            assert floData['price-update']['contract-address'] == contractStructure['contractAddress']
                            return float(floData['price-update']['price'])
                        except:
                            continue
                    else:
                        continue
                else:
                    logger.info('API error while fetchDynamicSwapPrice')
                    sys.exit(0)
            return float(contractStructure['price'])
    else:
        logger.info('API error fetchDynamicSwapPrice')
        sys.exit(0)
"""

def fetchDynamicSwapPrice(contractStructure, blockinfo):
    oracle_address = contractStructure['oracle_address']
    # fetch transactions from the blockchain where from address : oracle-address... to address: contract address
    # find the first contract transaction which adheres to price change format
    # {"price-update":{"contract-name": "", "contract-address": "", "price": 3}}
    is_incomplete_key_present = False
    latest_param = 'true'
    mempool_param = 'false'
    init_id = None
    while True:
        try:
            response = requests.get(f'{api_url}api/address/{oracle_address}?details=txs', verify=API_VERIFY)
            if response.status_code == 200:
                response = response.json()
                if len(response['txs']) == 0: 
                    return float(contractStructure['price'])
                else:
                    for transaction in response['txs']:
                        if 'floData' in transaction.keys():
                            floData = transaction['floData']
                        else:
                            floData = ''
                        # If the blocktime of the transaction is < than the current block time
                        if transaction['time'] < blockinfo['time']:
                            # Check if flodata is in the format we are looking for
                            # ie. {"price-update":{"contract-name": "", "contract-address": "", "price": 3}}
                            # and receiver address should be contractAddress
                            try:
                                sender_address, receiver_address = find_sender_receiver(transaction)
                                assert sender_address == oracle_address
                                assert receiver_address == contractStructure['contractAddress']
                                floData = json.loads(floData)
                                # Check if the contract name and address are right
                                assert floData['price-update']['contract-name'] == contractStructure['contractName']
                                assert floData['price-update']['contract-address'] == contractStructure['contractAddress']
                                return float(floData['price-update']['price'])
                            except:
                                continue
                        else:
                            continue
                break
            else:
                logger.info(f'API error fetchDynamicSwapPrice. Retry in {RETRY_TIMEOUT_LONG}s...')
                #sys.exit(0)
                time.sleep(RETRY_TIMEOUT_LONG)
        except:
            logger.info(f'API error fetchDynamicSwapPrice. Retry in {RETRY_TIMEOUT_LONG}s...')
            time.sleep(RETRY_TIMEOUT_LONG)

    return float(contractStructure['price'])


def processBlock(blockindex=None, blockhash=None):
    if blockindex is not None and blockhash is None:
        logger.info(f'Processing block {blockindex}') 
        # Get block details 
        while blockhash is None or blockhash == '':
            response = newMultiRequest(f"blockheight/{blockindex}") 
            try:            
                blockhash = response['hash'] 
            except:
                logger.info(f"API call block-index/{blockindex} failed to give proper response. Retrying.")

    blockinfo = newMultiRequest(f"block/{blockhash}")

    #TODO: Check for reorg in here

    # Check and perform operations which do not require blockchain intervention
    checkLocal_expiry_trigger_deposit(blockinfo)

    # todo Rule 8 - read every transaction from every block to find and parse flodata
    counter = 0
    acceptedTxList = []
    # Scan every transaction
    logger.info("Before tx loop")

    for transaction_data in blockinfo["txs"]:
        transaction = transaction_data["txid"]
        
        try:
            text = transaction_data["floData"]
        except:
            text = ''
        text = text.replace("\n", " \n ")
        # todo Rule 9 - Reject all noise transactions. Further rules are in parsing.py
        returnval = None
        parsed_data = parsing.parse_flodata(text, blockinfo, config['DEFAULT']['NET'])
        if parsed_data['type'] not in ['noise', None, '']:
            logger.info(f"Processing transaction {transaction}")
            logger.info(f"flodata {text} is parsed to {parsed_data}")
            returnval = processTransaction(transaction_data, parsed_data, blockinfo)

        if returnval == 1:
            acceptedTxList.append(transaction)
        elif returnval == 0:
            logger.info("Transfer for the transaction %s is illegitimate. Moving on" % transaction)

    logger.info("Completed tx loop")

    if len(acceptedTxList) > 0:
        tempinfo = blockinfo['txs'].copy()
        for tx in blockinfo['txs']:
            if tx['txid'] not in acceptedTxList:
                tempinfo.remove(tx)
        blockinfo['txs'] = tempinfo
        updateLatestBlock(blockinfo)
    
    try:
        session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
        entry = session.query(SystemData).filter(SystemData.attribute == 'lastblockscanned').all()[0]
        entry.value = str(blockinfo['height'])
        session.commit()
        session.close()
    except:
        logger.info(f"Unable to connect to 'system' database... retrying in {DB_RETRY_TIMEOUT} seconds")
        time.sleep(DB_RETRY_TIMEOUT)


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
    conn.execute("INSERT INTO latestTransactions(transactionHash, blockNumber, jsonData, transactionType, parsedFloData, db_reference) VALUES (?,?,?,?,?,?)", (transactionData['txid'], transactionData['blockheight'], json.dumps(transactionData), transactionType, json.dumps(parsed_data), db_reference))
    #conn.commit()
    conn.close()


def updateLatestBlock(blockData):
    # connect to latest block db
    while True:
        try:
            conn = create_database_connection('latest_cache', {'db_name':"latestCache"})
            break
        except:
            time.sleep(DB_RETRY_TIMEOUT)
    conn.execute('INSERT INTO latestBlocks(blockNumber, blockHash, jsonData) VALUES (?,?,?)', (blockData['height'], blockData['hash'], json.dumps(blockData)))
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

        commentTransferAmount = float(tokenAmount)
        if availableTokens is None:
            logger.info(f"The sender address {inputAddress} doesn't own any {tokenIdentification.upper()} tokens")
            session.close()
            return 0

        elif availableTokens < commentTransferAmount:
            logger.info("The transfer amount passed in the comments is more than the user owns\nThis transaction will be discarded\n")
            session.close()
            return 0

        elif availableTokens >= commentTransferAmount:
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


def trigger_internal_contract(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name, contract_address, transaction_subType):
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
            success_returnval = trigger_internal_contract(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name=contractStructure['contractName'], contract_address=contractStructure['contractAddress'], transaction_subType='maximumsubscriptionamount')
            if not success_returnval:
                return 0
        return 1
    else:
        return 0


def check_contract_status(contractName, contractAddress):
    # Status of the contract is at 2 tables in system.db
    # activecontracts and time_actions
    # select the last entry form the colum 
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
    connection.execute('INSERT INTO activecontracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (None, contractStructure['contractName'], contractStructure['contractAddress'], contractStatus, contractStructure['tokenIdentification'], contractStructure['contractType'], transactionHash, blockNumber, blockHash, incorporationDate, expiryDate, closeDate))
    connection.execute('INSERT INTO time_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (None, trigger_time, trigger_activity, contractStatus, contractName, contractAddress, contractType, tokens_db, parsed_data, transactionHash, blockHeight))
    connection.close()


def return_active_contracts(session):
    active_contracts = session.execute('''SELECT t1.* FROM time_actions t1 JOIN ( SELECT contractName, contractAddress, MAX(id) AS max_id FROM time_actions GROUP BY contractName, contractAddress ) t2 ON t1.contractName = t2.contractName AND t1.contractAddress = t2.contractAddress AND t1.id = t2.max_id WHERE t1.status = 'active' AND t1.activity = 'contract-time-trigger' ''').all()
    return active_contracts


def return_active_deposits(session):
    # find all the deposits which are active
    # todo - sqlalchemy gives me warning with the following method
    subquery_filter = session.query(TimeActions.id).group_by(TimeActions.transactionHash).having(func.count(TimeActions.transactionHash)==1).subquery()
    active_deposits = session.query(TimeActions).filter(TimeActions.id.in_(subquery_filter), TimeActions.status=='active', TimeActions.activity=='contract-deposit').all()
    return active_deposits


def checkLocal_expiry_trigger_deposit(blockinfo):
    # Connect to system.db with a session 
    while True:
        try:
            systemdb_session = create_database_session_orm('system_dbs', {'db_name':'system'}, SystemBase)

            break
        except:
            time.sleep(DB_RETRY_TIMEOUT)
    timeactions_tx_hashes = []
    active_contracts = return_active_contracts(systemdb_session)
    active_deposits = return_active_deposits(systemdb_session)
    
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

                def _get_txid():
                    while True:
                        try:
                            response = requests.get(f'https://stdops.ranchimall.net/hash?data={data}', verify=API_VERIFY)
                            if response.status_code == 200:
                                txid = response.json()
                                return txid
                            elif response.status_code == 404:
                                logger.info(f'Internal trigger has failed (404) for getting txid from stdops.ranchimall.net. Retry in {RETRY_TIMEOUT_LONG}s')
                                time.sleep(RETRY_TIMEOUT_LONG)
                        except:
                            logger.info(f'Internal trigger has failed for getting txid from stdops.ranchimall.net. Retry in {RETRY_TIMEOUT_LONG}s')
                            time.sleep(RETRY_TIMEOUT_LONG)

                
                transaction_data = {}
                transaction_data['txid'] = _get_txid()
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
                            close_expire_contract(contractStructure, 'expired', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], None, query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                                   
                    if blocktime > query_time:
                        if 'minimumsubscriptionamount' in contractStructure:
                            if process_minimum_subscriptionamount(contractStructure, connection, blockinfo, transaction_data, parsed_data):
                                close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                                return 

                        # Expire the contract
                        close_expire_contract(contractStructure, 'expired', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], None, query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                
                elif 'payeeAddress' in contractStructure: # Internal trigger contract type
                    
                    # maximumsubscription check, if reached then trigger the contract
                    if 'maximumsubscriptionamount' in contractStructure:
                        maximumsubscriptionamount = float(contractStructure['maximumsubscriptionamount'])
                        rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
                        tokenAmount_sum = float(sum(Decimal(f"{row[0]}") for row in rows))
                        if tokenAmount_sum >= maximumsubscriptionamount:
                            # Trigger the contract
                            success_returnval = trigger_internal_contract(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name=query.contractName, contract_address=query.contractAddress, transaction_subType='maximumsubscriptionamount')
                            if not success_returnval:
                                return 0

                            close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                            return
      
                    if blocktime > query_time: 
                        if 'minimumsubscriptionamount' in contractStructure:
                            if process_minimum_subscriptionamount(contractStructure, connection, blockinfo, transaction_data, parsed_data):
                                close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                                return
                        
                        # Trigger the contract
                        rows = connection.execute('SELECT tokenAmount FROM contractparticipants').fetchall()
                        # Sum up using Decimal
                        tokenAmount_sum = float(sum(Decimal(f"{row[0]}") for row in rows))
                        success_returnval = trigger_internal_contract(tokenAmount_sum, contractStructure, transaction_data, blockinfo, parsed_data, connection, contract_name=query.contractName, contract_address=query.contractAddress, transaction_subType='expiryTime')
                        if not success_returnval:
                            return 0

                        close_expire_contract(contractStructure, 'closed', transaction_data['txid'], blockinfo['height'], blockinfo['hash'], activecontracts_table_info.incorporationDate, blockinfo['time'], blockinfo['time'], query.time, query.activity, query.contractName, query.contractAddress, query.contractType, query.tokens_db, query.parsed_data, blockinfo['height'])
                        return

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
                    updateLatestTransaction(transaction_data, parsed_data, f"{query.contractName}-{query.contractAddress}")


def check_reorg():
    connection = create_database_connection('system_dbs')
    blockbook_api_url = 'https://blockbook.ranchimall.net/'
    BACK_TRACK_BLOCKS = 1000

    # find latest block number in local database
    latest_block = list(connection.execute("SELECT max(blockNumber) from latestBlocks").fetchone())[0]
    block_number = latest_block

    while block_number > 0:
        # get the block hash
        block_hash = list(connection.execute(f"SELECT blockHash from latestBlocks WHERE blockNumber = {block_number}").fetchone())[0] 

        # Check if the block is in blockbook (i.e, not dropped in reorg)
        response = requests.get(f'{blockbook_api_url}api/blockheight/{block_number}', verify=API_VERIFY)
        if response.status_code == 200:
            response = response.json()
            if response['hash'] == block_hash: # local blockhash matches with blockbook hash
                break
            else: # check for older blocks to trace where reorg has happened
                block_number -= BACK_TRACK_BLOCKS
                continue
        else:
            logger.info('Response from the Blockbook API failed')
            sys.exit(0) #TODO test reorg fix and remove this

    connection.close()

    # rollback if needed
    if block_number != latest_block:
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


def processTransaction(transaction_data, parsed_data, blockinfo):
    # Do the necessary checks for the inputs and outputs
    # todo Rule 38 - Here we are doing FLO processing. We attach asset amounts to a FLO address, so every FLO address
    #        will have multiple feed ins of the asset. Each of those feedins will be an input to the address.
    #        an address can also spend the asset. Each of those spends is an output of that address feeding the asset into some
    #        other address an as input
    # Rule 38 reframe - For checking any asset transfer on the flo blockchain it is possible that some transactions may use more than one
    # vins. However in any single transaction the system considers valid, they can be only one source address from which the flodata is
    # originting. To ensure consistency, we will have to check that even if there are more than one vins in a transaction, there should be
    # exactly one FLO address on the originating side and that FLO address should be the owner of the asset tokens being transferred

    # Create vinlist and outputlist
    vinlist = []
    querylist = []

    # Extract VIN information
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

    # Check if the number of vout is more than 2 (Rule 42)
    if len(transaction_data["vout"]) > 2:
        logger.info(f"System has found more than 2 addresses as part of vout. Transaction {transaction_data['txid']} is rejected")
        return 0

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
        return 0
    else:
        outputlist = outputlist[0]

    logger.info(f"Input address list : {inputlist}")
    logger.info(f"Output address list : {outputlist}")

    transaction_data['senderAddress'] = inputlist[0]
    transaction_data['receiverAddress'] = outputlist[0]

    # All FLO checks completed at this point.
    # Semantic rules for parsed data begins
    
    # todo Rule 44 - Process as per the type of transaction
    if parsed_data['type'] == 'transfer':
        logger.info(f"Transaction {transaction_data['txid']} is of the type transfer")

        # todo Rule 45 - If the transfer type is token, then call the function transferToken to adjust the balances
        if parsed_data['transferType'] == 'token':
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

        # todo Rule 46 - If the transfer type is smart contract, then call the function transferToken to do sanity checks & lock the balance
        elif parsed_data['transferType'] == 'smartContract':
            if check_database_existence('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"}):
                # Check type of contract and categorize between into ote-participation or continuous-event participation
                # todo - replace all connection queries with session queries
                connection = create_database_connection('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"})
                contract_session = create_database_session_orm('smart_contract', {'contract_name':f"{parsed_data['contractName']}", 'contract_address':f"{outputlist[0]}"}, ContractBase)
                contract_type = contract_session.query(ContractStructure.value).filter(ContractStructure.attribute == 'contractType').first()[0]

                contractStructure = extract_contractStructure(parsed_data['contractName'], outputlist[0])

                if contract_type == 'one-time-event':
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
                            # Store transfer as part of RejectedContractTransactionHistory
                            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                            # Pass information to SSE channel
                            pushData_SSEapi(f"Error| Mismatch in contract address specified in flodata and the output address of the transaction {transaction_data['txid']}")
                            return 0

                    # check the status of the contract
                    contractStatus = check_contract_status(parsed_data['contractName'], outputlist[0])
                    contractList = []

                    if contractStatus == 'closed':
                        rejectComment = f"Transaction {transaction_data['txid']} closed as Smart contract {parsed_data['contractName']} at the {outputlist[0]} is closed"
                        logger.info(rejectComment)
                        rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
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

                            if blocktime_object > expirytime_object:
                                rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart contract {parsed_data['contractName']}-{outputlist[0]} has expired and will not accept any user participation"
                                logger.info(rejectComment)
                                rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                                pushData_SSEapi(rejectComment)
                                return 0

                    # check if user choice has been passed, to the wrong contract type
                    if 'userChoice' in parsed_data and 'exitconditions' not in contractStructure:
                        rejectComment = f"Transaction {transaction_data['txid']} rejected as userChoice, {parsed_data['userChoice']}, has been passed to Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]} which doesn't accept any userChoice"
                        logger.info(rejectComment)
                        rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                        pushData_SSEapi(rejectComment)
                        return 0

                    # check if the right token is being sent for participation
                    if parsed_data['tokenIdentification'] != contractStructure['tokenIdentification']:
                        rejectComment = f"Transaction {transaction_data['txid']} rejected as the token being transferred, {parsed_data['tokenIdentidication'].upper()}, is not part of the structure of Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]}"
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
                    # Check if maximum subscription amount has reached
                    if 'maximumsubscriptionamount' in contractStructure:
                        # now parse the expiry time in python
                        maximumsubscriptionamount = float(contractStructure['maximumsubscriptionamount'])
                        session = create_database_session_orm('smart_contract', {'contract_name': f"{parsed_data['contractName']}", 'contract_address': f"{outputlist[0]}"}, ContractBase)
                        # amountDeposited = session.query(func.sum(ContractParticipants.tokenAmount)).all()[0][0]
                        
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

                    # Check if exitcondition exists as part of contractstructure and is given in right format
                    if 'exitconditions' in contractStructure:
                        # This means the contract has an external trigger, ie. trigger coming from the contract committee
                        exitconditionsList = []
                        for condition in contractStructure['exitconditions']:
                            exitconditionsList.append(contractStructure['exitconditions'][condition])

                        if parsed_data['userChoice'] in exitconditionsList:
                            if partialTransferCounter == 0:
                                # Check if the tokenAmount being transferred exists in the address & do the token transfer
                                returnval = transferToken(parsed_data['tokenIdentification'], parsed_data['tokenAmount'], inputlist[0], outputlist[0], transaction_data, parsed_data, blockinfo = blockinfo)
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
                                    session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
                                    session.add(ContractAddressMapping(address=inputadd, addressType='participant',
                                                                        tokenAmount=parsed_data['tokenAmount'],
                                                                        contractName=parsed_data['contractName'],
                                                                        contractAddress=outputlist[0],
                                                                        transactionHash=transaction_data['txid'],
                                                                        blockNumber=transaction_data['blockheight'],
                                                                        blockHash=transaction_data['blockhash']))
                                    session.commit()

                                    # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
                                    connection = create_database_connection('system_dbs', {'db_name':'system'})
                                    firstInteractionCheck = connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{outputlist[0]}' AND token='{parsed_data['tokenIdentification']}'").fetchall()
                                    if len(firstInteractionCheck) == 0:
                                        connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{outputlist[0]}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")
                                    connection.close()
                                    updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}-{outputlist[0]}", transactionType='ote-externaltrigger-participation')
                                    return 1

                                else:
                                    logger.info("Something went wrong in the smartcontract token transfer method")
                                    return 0
                            elif partialTransferCounter == 1:
                                # Transfer only part of the tokens users specified, till the time it reaches maximumamount
                                returnval = transferToken(parsed_data['tokenIdentification'], perform_decimal_operation('subtraction', maximumsubscriptionamount, amountDeposited), inputlist[0], outputlist[0], transaction_data, parsed_data, blockinfo = blockinfo)
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
                                    session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
                                    session.add(ContractAddressMapping(address=inputadd, addressType='participant',
                                                                        tokenAmount=perform_decimal_operation('subtraction', maximumsubscriptionamount, amountDeposited),
                                                                        contractName=parsed_data['contractName'],
                                                                        contractAddress=outputlist[0],
                                                                        transactionHash=transaction_data['txid'],
                                                                        blockNumber=transaction_data['blockheight'],
                                                                        blockHash=transaction_data['blockhash']))
                                    session.commit()
                                    session.close()
                                    updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}-{outputlist[0]}", transactionType='ote-externaltrigger-participation')
                                    return 1

                                else:
                                    logger.info("Something went wrong in the smartcontract token transfer method")
                                    return 0

                        else:
                            rejectComment = f"Transaction {transaction_data['txid']} rejected as wrong userchoice entered for the Smart Contract named {parsed_data['contractName']} at the address {outputlist[0]}"
                            logger.info(rejectComment)
                            rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                            pushData_SSEapi(rejectComment)
                            return 0

                    elif 'payeeAddress' in contractStructure:
                        # this means the contract is of the type internal trigger
                        if partialTransferCounter == 0:
                            transferAmount = parsed_data['tokenAmount']
                        elif partialTransferCounter == 1:
                            transferAmount = perform_decimal_operation('subtraction', maximumsubscriptionamount, amountDeposited)
                        
                        # Check if the tokenAmount being transferred exists in the address & do the token transfer
                        returnval = transferToken(parsed_data['tokenIdentification'], transferAmount, inputlist[0], outputlist[0], transaction_data, parsed_data, blockinfo = blockinfo)
                        if returnval != 0:
                            # Store participant details in the smart contract's db
                            session.add(ContractParticipants(participantAddress=inputadd, tokenAmount=transferAmount, userChoice='-', transactionHash=transaction_data['txid'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash']))

                            # Store transfer as part of ContractTransactionHistory
                            add_contract_transaction_history(contract_name=parsed_data['contractName'], contract_address=outputlist[0], transactionType='participation', transactionSubType=None, sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=transferAmount, blockNumber=blockinfo['height'], blockHash=blockinfo['hash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), parsedFloData=json.dumps(parsed_data))
                            session.commit()
                            session.close()

                            # Store a mapping of participant address -> Contract participated in
                            session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
                            session.add(ContractAddressMapping(address=inputadd, addressType='participant',
                                                                tokenAmount=transferAmount,
                                                                contractName=parsed_data['contractName'],
                                                                contractAddress=outputlist[0],
                                                                transactionHash=transaction_data['txid'],
                                                                blockNumber=transaction_data['blockheight'],
                                                                blockHash=transaction_data['blockhash']))
                            session.commit()

                            # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
                            connection = create_database_connection('system_dbs', {'db_name':'system'})
                            firstInteractionCheck = connection.execute(f"SELECT * FROM tokenAddressMapping WHERE tokenAddress='{outputlist[0]}' AND token='{parsed_data['tokenIdentification']}'").fetchall()
                            if len(firstInteractionCheck) == 0:
                                connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{outputlist[0]}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}')")
                            connection.close()
                            updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}-{outputlist[0]}", transactionType='ote-internaltrigger-participation')
                            return 1

                        else:
                            logger.info("Something went wrong in the smartcontract token transfer method")
                            return 0            

                elif contract_type == 'continuos-event':
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

                        # swapAmount = float(parsed_data['tokenAmount'])/swapPrice
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
                            # accepting token transfer from participant to smart contract address 
                            returnval = transferToken(parsed_data['tokenIdentification'], parsed_data['tokenAmount'], inputlist[0], outputlist[0], transaction_data=transaction_data, parsed_data=parsed_data, isInfiniteToken=None, blockinfo=blockinfo, transactionType='tokenswapParticipation')
                            if returnval == 0:
                                logger.info("ERROR | Something went wrong in the token transfer method while doing local Smart Contract Particiaption")
                                return 0
                            
                            # If this is the first interaction of the outputlist's address with the given token name, add it to token mapping
                            systemdb_connection = create_database_connection('system_dbs', {'db_name':'system'})
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

                            updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}-{outputlist[0]}", transactionType='tokenswapParticipation')
                            pushData_SSEapi(f"Token swap successfully performed at contract {parsed_data['contractName']}-{outputlist[0]} with the transaction {transaction_data['txid']}")

                        else:
                            # Reject the participation saying not enough deposit tokens are available 
                            rejectComment = f"Swap participation at transaction {transaction_data['txid']} rejected as requested swap amount is {swapAmount} but {available_deposit_sum} is available"
                            logger.info(rejectComment)
                            rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
                            pushData_SSEapi(rejectComment)
                            return 0

                else:
                    rejectComment = f"Transaction {transaction_data['txid']} rejected as the participation doesn't belong to any valid contract type"
                    logger.info(rejectComment)                    
                    rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                    return 0

            else:
                rejectComment = f"Transaction {transaction_data['txid']} rejected as a Smart Contract with the name {parsed_data['contractName']} at address {outputlist[0]} doesnt exist"
                logger.info(rejectComment)
                rejected_contract_transaction_history(transaction_data, parsed_data, 'participation', outputlist[0], inputadd, outputlist[0], rejectComment)
                return 0

        elif parsed_data['transferType'] == 'nft':
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
    elif parsed_data['type'] == 'tokenIncorporation':
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

    # todo Rule 48 - If the parsed data type if smart contract incorporation, then check if the name hasn't been taken already
    #      if it has been taken then reject the incorporation.
    elif parsed_data['type'] == 'smartContractIncorporation':
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
                        session.add(ContractStructure(attribute='contractType', index=0, value=parsed_data['contractType']))
                        session.add(ContractStructure(attribute='subtype', index=0, value=parsed_data['subtype']))
                        session.add(ContractStructure(attribute='contractName', index=0, value=parsed_data['contractName']))
                        session.add(ContractStructure(attribute='tokenIdentification', index=0, value=parsed_data['tokenIdentification']))
                        session.add(ContractStructure(attribute='contractAddress', index=0, value=parsed_data['contractAddress']))
                        session.add(ContractStructure(attribute='flodata', index=0, value=parsed_data['flodata']))
                        session.add(ContractStructure(attribute='expiryTime', index=0, value=parsed_data['contractConditions']['expiryTime']))
                        session.add(ContractStructure(attribute='unix_expiryTime', index=0, value=parsed_data['contractConditions']['unix_expiryTime']))
                        if 'contractAmount' in parsed_data['contractConditions'].keys():
                            session.add(ContractStructure(attribute='contractAmount', index=0, value=parsed_data['contractConditions']['contractAmount']))

                        if 'minimumsubscriptionamount' in parsed_data['contractConditions']:
                            session.add(ContractStructure(attribute='minimumsubscriptionamount', index=0, value=parsed_data['contractConditions']['minimumsubscriptionamount']))
                        if 'maximumsubscriptionamount' in parsed_data['contractConditions']:
                            session.add(ContractStructure(attribute='maximumsubscriptionamount', index=0, value=parsed_data['contractConditions']['maximumsubscriptionamount']))
                        if 'userchoices' in parsed_data['contractConditions']:
                            for key, value in literal_eval(parsed_data['contractConditions']['userchoices']).items():
                                session.add(ContractStructure(attribute='exitconditions', index=key, value=value))

                        if 'payeeAddress' in parsed_data['contractConditions']:
                            # in this case, expirydate( or maximumamount) is the trigger internally. Keep a track of expiry dates
                            session.add(ContractStructure(attribute='payeeAddress', index=0, value=json.dumps(parsed_data['contractConditions']['payeeAddress'])))

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

                        session.add(DatabaseTypeMapping(db_name=f"{parsed_data['contractName']}-{inputadd}",
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
                        session.add(ContractStructure(attribute='contractType', index=0, value=parsed_data['contractType']))
                        session.add(ContractStructure(attribute='contractName', index=0, value=parsed_data['contractName']))
                        session.add(ContractStructure(attribute='contractAddress', index=0, value=parsed_data['contractAddress']))
                        session.add(ContractStructure(attribute='flodata', index=0, value=parsed_data['flodata']))
                        
                        if parsed_data['stateF'] != {} and parsed_data['stateF'] is not False:
                            for key, value in parsed_data['stateF'].items():
                                session.add(ContractStructure(attribute=f'statef-{key}', index=0, value=value))
                        
                        if 'subtype' in parsed_data['contractConditions']:
                            # todo: Check if the both the tokens mentioned exist if its a token swap
                            if (parsed_data['contractConditions']['subtype'] == 'tokenswap') and (check_database_existence('token', {'token_name':f"{parsed_data['contractConditions']['selling_token'].split('#')[0]}"})) and (check_database_existence('token', {'token_name':f"{parsed_data['contractConditions']['accepting_token'].split('#')[0]}"})):
                                session.add(ContractStructure(attribute='subtype', index=0, value=parsed_data['contractConditions']['subtype']))
                                session.add(ContractStructure(attribute='accepting_token', index=0, value=parsed_data['contractConditions']['accepting_token']))
                                session.add(ContractStructure(attribute='selling_token', index=0, value=parsed_data['contractConditions']['selling_token']))

                                if parsed_data['contractConditions']['pricetype'] not in ['predetermined','statef','dynamic']:
                                    rejectComment = f"pricetype is not part of accepted parameters for a continuos event contract of the type token swap.\nSmart contract incorporation on transaction {transaction_data['txid']} rejected"
                                    logger.info(rejectComment)
                                    rejected_contract_transaction_history(transaction_data, parsed_data, 'incorporation', inputadd, inputadd, outputlist[0], rejectComment)
                                    delete_contract_database({'contract_name': parsed_data['contractName'], 'contract_address': parsed_data['contractAddress']})
                                    return 0
                                
                                # determine price
                                session.add(ContractStructure(attribute='pricetype', index=0, value=parsed_data['contractConditions']['pricetype']))

                                if parsed_data['contractConditions']['pricetype'] in ['predetermined','statef']:
                                    session.add(ContractStructure(attribute='price', index=0, value=parsed_data['contractConditions']['price']))
                                elif parsed_data['contractConditions']['pricetype'] in ['dynamic']:
                                    session.add(ContractStructure(attribute='price', index=0, value=parsed_data['contractConditions']['price']))
                                    session.add(ContractStructure(attribute='oracle_address', index=0, value=parsed_data['contractConditions']['oracle_address']))
                                    
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
                                session.add(DatabaseTypeMapping(db_name=f"{parsed_data['contractName']}-{inputadd}",
                                                                    db_type='smartcontract',
                                                                    keyword='',
                                                                    object_format='',
                                                                    blockNumber=transaction_data['blockheight']))
                                session.commit()
                                session.close()
                                updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}-{parsed_data['contractAddress']}")
                                pushData_SSEapi('Contract | Contract incorporated at transaction {} with name {}-{}'.format(transaction_data['txid'], parsed_data['contractName'], parsed_data['contractAddress']))
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

    elif parsed_data['type'] == 'smartContractPays':
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
                            rejectComment = f"Transaction {transaction_data['txid']} rejected as Smart contract {parsed_data['contractName']}-{outputlist[0]} has not expired and will not trigger"
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

                        updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['contractName']}-{outputlist[0]}")
                        pushData_SSEapi('Trigger | Minimum subscription amount not reached at contract {}-{} at transaction {}. Tokens will be refunded'.format(parsed_data['contractName'], outputlist[0], transaction_data['txid']))
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

                updateLatestTransaction(transaction_data, parsed_data, f"{contractStructure['contractName']}-{contractStructure['contractAddress']}")

                pushData_SSEapi('Trigger | Contract triggered of the name {}-{} is active currently at transaction {}'.format(parsed_data['contractName'], outputlist[0], transaction_data['txid']))
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

    elif parsed_data['type'] == 'smartContractDeposit':
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
                rejectComment = f"Transaction {transaction_data['txid']} rejected as it already exists in the Smart Contract db. This is unusual, please check your code"
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
            
            updateLatestTransaction(transaction_data, parsed_data , f"{parsed_data['contractName']}-{outputlist[0]}")
            return 1

        else:
            rejectComment = f"Transaction {transaction_data['txid']} rejected as a Smart Contract with the name {parsed_data['contractName']} at address {outputlist[0]} doesnt exist"
            logger.info(rejectComment)
            rejected_contract_transaction_history(transaction_data, parsed_data, 'smartContractDeposit', outputlist[0], inputadd, outputlist[0], rejectComment)
            return 0
    
    elif parsed_data['type'] == 'nftIncorporation':
        '''
            DIFFERENT BETWEEN TOKEN AND NFT
            System.db will have a different entry
            in creation nft word will be extra
            NFT Hash must be present
            Creation and transfer amount .. only integer parts will be taken
            Keyword nft must be present in both creation and transfer
        '''
        if not is_a_contract_address(inputlist[0]):
            if not check_database_existence('token', {'token_name':f"{parsed_data['tokenIdentification']}"}):
                session = create_database_session_orm('token', {'token_name': f"{parsed_data['tokenIdentification']}"}, TokenBase)
                session.add(ActiveTable(address=inputlist[0], parentid=0, transferBalance=parsed_data['tokenAmount'], addressBalance=parsed_data['tokenAmount'], blockNumber=blockinfo['height']))
                session.add(TransferLogs(sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], sourceId=0, destinationId=1, blockNumber=transaction_data['blockheight'], time=transaction_data['time'], transactionHash=transaction_data['txid']))
                add_transaction_history(token_name=parsed_data['tokenIdentification'], sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash'], blocktime=transaction_data['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=parsed_data['type'], parsedFloData=json.dumps(parsed_data))
                
                session.commit()
                session.close()

                # add it to token address to token mapping db table
                connection = create_database_connection('system_dbs', {'db_name':'system'})
                connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{inputadd}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}');")
                nft_data = {'sha256_hash': f"{parsed_data['nftHash']}"}
                connection.execute(f"INSERT INTO databaseTypeMapping (db_name, db_type, keyword, object_format, blockNumber) VALUES ('{parsed_data['tokenIdentification']}', 'nft', '', '{json.dumps(nft_data)}', '{transaction_data['blockheight']}')")
                connection.close()

                updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['tokenIdentification']}")
                pushData_SSEapi(f"NFT | Succesfully incorporated NFT {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
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

    elif parsed_data['type'] == 'infiniteTokenIncorporation':
        if not is_a_contract_address(inputlist[0]) and not is_a_contract_address(outputlist[0]):
            if not check_database_existence('token', {'token_name':f"{parsed_data['tokenIdentification']}"}):
                parsed_data['tokenAmount'] = 0
                tokendb_session = create_database_session_orm('token', {'token_name': f"{parsed_data['tokenIdentification']}"}, TokenBase)
                tokendb_session.add(ActiveTable(address=inputlist[0], parentid=0, transferBalance=parsed_data['tokenAmount'], blockNumber=blockinfo['height']))
                tokendb_session.add(TransferLogs(sourceFloAddress=inputadd, destFloAddress=outputlist[0],
                                        transferAmount=parsed_data['tokenAmount'], sourceId=0, destinationId=1,
                                        blockNumber=transaction_data['blockheight'], time=transaction_data['time'],
                                        transactionHash=transaction_data['txid']))
                
                add_transaction_history(token_name=parsed_data['tokenIdentification'], sourceFloAddress=inputadd, destFloAddress=outputlist[0], transferAmount=parsed_data['tokenAmount'], blockNumber=transaction_data['blockheight'], blockHash=transaction_data['blockhash'], blocktime=blockinfo['time'], transactionHash=transaction_data['txid'], jsonData=json.dumps(transaction_data), transactionType=parsed_data['type'], parsedFloData=json.dumps(parsed_data))

                
                # add it to token address to token mapping db table
                connection = create_database_connection('system_dbs', {'db_name':'system'})
                connection.execute(f"INSERT INTO tokenAddressMapping (tokenAddress, token, transactionHash, blockNumber, blockHash) VALUES ('{inputadd}', '{parsed_data['tokenIdentification']}', '{transaction_data['txid']}', '{transaction_data['blockheight']}', '{transaction_data['blockhash']}');")
                info_object = {'root_address': inputadd}
                connection.execute("""INSERT INTO databaseTypeMapping (db_name, db_type, keyword, object_format, blockNumber) VALUES (?, ?, ?, ?, ?)""", (parsed_data['tokenIdentification'], 'infinite-token', '', json.dumps(info_object), transaction_data['blockheight']))
                updateLatestTransaction(transaction_data, parsed_data, f"{parsed_data['tokenIdentification']}")
                tokendb_session.commit()
                connection.close()
                tokendb_session.close()
                pushData_SSEapi(f"Token | Succesfully incorporated token {parsed_data['tokenIdentification']} at transaction {transaction_data['txid']}")
                return 1
            else:
                rejectComment = f"Transaction {transaction_data['txid']} rejected as a token with the name {parsed_data['tokenIdentification']} has already been incorporated"
                logger.info(rejectComment)
                rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
                pushData_SSEapi(rejectComment)
                return 0
        else:
            rejectComment = f"Infinite token incorporation at transaction {transaction_data['txid']} rejected as either the input address is part of a contract address"
            logger.info(rejectComment)
            rejected_transaction_history(transaction_data, parsed_data, inputadd, outputlist[0], rejectComment)
            pushData_SSEapi(rejectComment)
            return 0


def scanBlockchain():
    # Read start block no
    while True:
        try:
            session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
            startblock = int(session.query(SystemData).filter_by(attribute='lastblockscanned').all()[0].value) + 1
            session.commit()
            session.close()
            break
        except:
            logger.info(f"Unable to connect to 'system' database... retrying in {DB_RETRY_TIMEOUT} seconds")
            time.sleep(DB_RETRY_TIMEOUT)

    # todo Rule 6 - Find current block height
    #      Rule 7 - Start analysing the block contents from starting block to current height

    # Find current block height
    current_index = -1
    while(current_index == -1):
        response = newMultiRequest('latest-block')
        try:
            current_index = response['blockheight']
        except:
            logger.info('Latest block count response from multiRequest() is not in the right format. Displaying the data received in the log below')
            logger.info(response)
            logger.info('Program will wait for 1 seconds and try to reconnect')
            time.sleep(1)
        else:
            logger.info("Current block height is %s" % str(current_index))
            break
    
    for blockindex in range(startblock, current_index):
        if blockindex in IGNORE_BLOCK_LIST:
            continue
        processBlock(blockindex=blockindex) 

    # At this point the script has updated to the latest block
    # Now we connect to flosight's websocket API to get information about the latest blocks

def switchNeturl(currentneturl):
    # Use modulo operation to simplify the logic
    neturlindex = serverlist.index(currentneturl)
    return serverlist[(neturlindex + 1) % len(serverlist)]


def reconnectWebsocket(socket_variable):
    # Switch a to different flosight
    # neturl = switchNeturl(neturl)
    # Connect to Flosight websocket to get data on new incoming blocks
    i=0
    newurl = serverlist[0]
    while(not socket_variable.connected):
        logger.info(f"While loop {i}")
        logger.info(f"Sleeping for 3 seconds before attempting reconnect to {newurl}")
        time.sleep(3)
        try:
            scanBlockchain()
            logger.info(f"Websocket endpoint which is being connected to {newurl}socket.io/socket.io.js")
            socket_variable.connect(f"{newurl}socket.io/socket.io.js")
            i=i+1
        except:
            logger.info(f"disconnect block: Failed reconnect attempt to {newurl}")
            newurl = switchNeturl(newurl)
            i=i+1


def get_websocket_uri(testnet=False):
    if testnet:
        return "wss://blockbook-testnet.ranchimall.net/websocket"
    else:
        return "wss://blockbook.ranchimall.net/websocket"

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


# MAIN EXECUTION STARTS 
# Configuration of required variables 
config = configparser.ConfigParser()
config.read('config.ini')

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s:%(name)s:%(message)s')
file_handler = logging.FileHandler(os.path.join(config['DEFAULT']['DATA_PATH'],'tracking.log'))
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)


#  Rule 1     - Read command line arguments to reset the databases as blank
#  Rule 2     - Read config to set testnet/mainnet
#  Rule 3     - Set flo blockexplorer location depending on testnet or mainnet
#  Rule 4     - Set the local flo-cli path depending on testnet or mainnet ( removed this feature | Flosights are the only source )
#  Rule 5     - Set the block number to scan from


# Read command line arguments
parser = argparse.ArgumentParser(description='Script tracks RMT using FLO data on the FLO blockchain - https://flo.cash')
parser.add_argument('-r', '--reset', nargs='?', const=1, type=int, help='Purge existing db and rebuild it from scratch')
parser.add_argument('-rb', '--rebuild', nargs='?', const=1, type=int, help='Rebuild it')
parser.add_argument("--testnet", action="store_true", help="Use the testnet URL")
args = parser.parse_args()

dirpath = os.path.join(config['DEFAULT']['DATA_PATH'], 'tokens')
if not os.path.isdir(dirpath):
    os.mkdir(dirpath)
dirpath = os.path.join(config['DEFAULT']['DATA_PATH'], 'smartContracts')
if not os.path.isdir(dirpath):
    os.mkdir(dirpath)

# Read configuration

# todo - write all assertions to make sure default configs are right 
if (config['DEFAULT']['NET'] != 'mainnet') and (config['DEFAULT']['NET'] != 'testnet'):
    logger.error("NET parameter in config.ini invalid. Options are either 'mainnet' or 'testnet'. Script is exiting now")
    sys.exit(0)

# Specify mainnet and testnet server list for API calls and websocket calls 
# Specify ADMIN ID
serverlist = None
if config['DEFAULT']['NET'] == 'mainnet':
    serverlist = config['DEFAULT']['MAINNET_FLOSIGHT_SERVER_LIST']
    APP_ADMIN = 'FNcvkz9PZNZM3HcxM1XTrVL4tgivmCkHp9'
    websocket_uri = get_websocket_uri(testnet=False)
elif config['DEFAULT']['NET'] == 'testnet':
    serverlist = config['DEFAULT']['TESTNET_FLOSIGHT_SERVER_LIST']
    APP_ADMIN = 'oWooGLbBELNnwq8Z5YmjoVjw8GhBGH3qSP'
    websocket_uri = get_websocket_uri(testnet=True)
serverlist = serverlist.split(',')
neturl = config['DEFAULT']['FLOSIGHT_NETURL']
api_url = neturl
tokenapi_sse_url = config['DEFAULT']['TOKENAPI_SSE_URL']
API_VERIFY = config['DEFAULT']['API_VERIFY']
if API_VERIFY == 'False':
    API_VERIFY = False
elif API_VERIFY == 'True':
    API_VERIFY = True
else:
    API_VERIFY = True


IGNORE_BLOCK_LIST = config['DEFAULT']['IGNORE_BLOCK_LIST'].split(',')
IGNORE_BLOCK_LIST = [int(s) for s in IGNORE_BLOCK_LIST]
IGNORE_TRANSACTION_LIST = config['DEFAULT']['IGNORE_TRANSACTION_LIST'].split(',')


def create_dir_if_not_exist(dir_path, reset = False):
    if os.path.exists(dir_path):
        if reset: 
            shutil.rmtree(dir_path)
            os.mkdir(dir_path)
    else:
        os.mkdir(dir_path) 

def init_system_db(startblock):
    # Initialize system.db
    session = create_database_session_orm('system_dbs', {'db_name': "system"}, SystemBase)
    session.add(SystemData(attribute='lastblockscanned', value=startblock - 1))
    session.commit()
    session.close()

def init_lastestcache_db():
    # Initialize latest cache DB
    session = create_database_session_orm('system_dbs', {'db_name': "latestCache"}, LatestCacheBase)
    session.commit()
    session.close()

def init_storage_if_not_exist(reset = False):
    
    token_dir_path = os.path.join(config['DEFAULT']['DATA_PATH'], 'tokens')
    create_dir_if_not_exist(token_dir_path, reset)

    smart_contract_dir_path = os.path.join(config['DEFAULT']['DATA_PATH'], 'smartContracts')
    create_dir_if_not_exist(smart_contract_dir_path, reset)
    
    system_db_path = os.path.join(config['DEFAULT']['DATA_PATH'], 'system.db')
    if os.path.exists(system_db_path):
        if reset: 
            os.remove(system_db_path)
            init_system_db(int(config['DEFAULT']['START_BLOCK']))
    else:
        init_system_db(int(config['DEFAULT']['START_BLOCK']))
            
    
    latestCache_db_path = os.path.join(config['DEFAULT']['DATA_PATH'], 'latestCache.db')
    if os.path.exists(latestCache_db_path):
        if reset: 
            os.remove(latestCache_db_path)
            init_lastestcache_db()
    else:
        init_lastestcache_db()

# Delete database and smartcontract directory if reset is set to 1
if args.reset == 1:
    logger.info("Resetting the database. ")
    init_storage_if_not_exist(reset=True)
else:
    init_storage_if_not_exist()


# Determine API source for block and transaction information
if __name__ == "__main__":
    # MAIN LOGIC STARTS
    # scan from the latest block saved locally to latest network block
    scanBlockchain()

    logger.debug("Completed first scan")

    # At this point the script has updated to the latest block
    # Now we connect to flosight's websocket API to get information about the latest blocks
    # Neturl is the URL for Flosight API whose websocket endpoint is being connected to

    asyncio.get_event_loop().run_until_complete(connect_to_websocket(websocket_uri)) 