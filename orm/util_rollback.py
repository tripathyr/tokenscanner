import argparse 
from sqlalchemy import create_engine, func 
from sqlalchemy.orm import sessionmaker 
from src.backend.models import SystemData, TokenBase, ActiveTable, ConsumedTable, TransferLogs, TransactionHistory, TokenContractAssociation, RejectedTransactionHistory, ContractBase, ContractStructure, ContractParticipants, ContractTransactionHistory, ContractDeposits, ConsumedInfo, ContractWinners, ContinuosContractBase, ContractStructure2, ContractParticipants2, ContractDeposits2, ContractTransactionHistory2, SystemBase, ActiveContracts, SystemData, ContractAddressMapping, TokenAddressMapping, DatabaseTypeMapping, TimeActions, RejectedContractTransactionHistory, RejectedTransactionHistory, LatestCacheBase, LatestTransactions, LatestBlocks
from ast import literal_eval 
import os 
import json 
import logging 
import sys 
from src.backend.parsing import perform_decimal_operation


apppath = os.path.dirname(os.path.realpath(__file__)) 

# helper functions 
def check_database_existence(type, parameters):
    if type == 'token':
        return os.path.isfile(f"./tokens/{parameters['token_name']}.db")

    if type == 'smart_contract':
        return os.path.isfile(f"./smartContracts/{parameters['contract_name']}-{parameters['contract_address']}.db")


def create_database_connection(type, parameters):
    if type == 'token':
        engine = create_engine(f"sqlite:///tokens/{parameters['token_name']}.db", echo=True)
    elif type == 'smart_contract':
        engine = create_engine(f"sqlite:///smartContracts/{parameters['contract_name']}-{parameters['contract_address']}.db", echo=True)
    elif type == 'system_dbs':
        engine = create_engine(f"sqlite:///{parameters['db_name']}.db", echo=False)

    connection = engine.connect()
    return connection


def create_database_session_orm(type, parameters, base):
    if type == 'token':
        engine = create_engine(f"sqlite:///tokens/{parameters['token_name']}.db", echo=True)
        base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()

    elif type == 'smart_contract':
        engine = create_engine(f"sqlite:///smartContracts/{parameters['contract_name']}-{parameters['contract_address']}.db", echo=True)
        base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()
    
    elif type == 'system_dbs':
        engine = create_engine(f"sqlite:///{parameters['db_name']}.db", echo=False)
        base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()
    else:
        pdb.set_trace()
    
    return session


def inspect_parsed_flodata(parsed_flodata, inputAddress, outputAddress):
    if parsed_flodata['type'] == 'transfer':
        if parsed_flodata['transferType'] == 'token':
            return {'type':'tokentransfer', 'token_db':f"{parsed_flodata['tokenIdentification']}", 'token_amount':f"{parsed_flodata['tokenAmount']}"}
        if parsed_flodata['transferType'] == 'smartContract':
            return {'type':'smartContract', 'contract_db': f"{parsed_flodata['contractName']}-{outputAddress}" ,'accepting_token_db':f"{parsed_flodata['']}", 'receiving_token_db':f"{parsed_flodata['tokenIdentification']}" ,'token_amount':f"{parsed_flodata['tokenAmount']}"}
        if parsed_flodata['transferType'] == 'swapParticipation':
            return {'type':'swapParticipation', 'contract_db': f"{parsed_flodata['contractName']}-{outputAddress}" ,'accepting_token_db':f"{parsed_flodata['']}", 'receiving_token_db':f"{parsed_flodata['tokenIdentification']}" ,'token_amount':f"{parsed_flodata['tokenAmount']}"}
        if parsed_flodata['transferType'] == 'nft':
            return {'type':'nfttransfer', 'nft_db':f"{parsed_flodata['tokenIdentification']}", 'token_amount':f"{parsed_flodata['tokenAmount']}"}
    if parsed_flodata['type'] == 'tokenIncorporation':
        return {'type':'tokenIncorporation', 'token_db':f"{parsed_flodata['tokenIdentification']}", 'token_amount':f"{parsed_flodata['tokenAmount']}"}
    if parsed_flodata['type'] == 'smartContractPays':
        # contract address, token | both of them come from 
        sc_session = create_database_session_orm('smart_contract', {'contract_name':f"{parsed_flodata['contractName']}", 'contract_address':f"{outputAddress}"}, ContractBase)
        token_db = sc_session.query(ContractStructure.value).filter(ContractStructure.attribute=='tokenIdentification').first()[0]
        return {'type':'smartContractPays', 'token_db':f"{token_db}" , 'contract_db':f"{parsed_flodata['contractName']}-{outputAddress}", 'triggerCondition':f"{parsed_flodata['triggerCondition']}"}
    if parsed_flodata['type'] == 'smartContractIncorporation':
        return {'type':'smartContractIncorporation', 'contract_db':f"{parsed_flodata['contractName']}-{outputAddress}", 'triggerCondition':f"{parsed_flodata['triggerCondition']}"}


def getDatabase_from_parsedFloData(parsed_flodata, inputAddress, outputAddress):
    tokenlist = []
    contractlist = []
    if parsed_flodata['type'] == 'transfer':
        if parsed_flodata['transferType'] == 'token':
            #return {'type':'token_db', 'token_db':f"{parsed_flodata['tokenIdentification']}"}
            tokenlist.append(parsed_flodata['tokenIdentification'])
        elif parsed_flodata['transferType'] == 'smartContract':
            #return {'type':'smartcontract_db', 'contract_db': f"{parsed_flodata['contractName']}-{outputAddress}" ,'token_db':f"{parsed_flodata['tokenIdentification']}"}
            tokenlist.append(parsed_flodata['tokenIdentification'])
            contractlist.append(f"{parsed_flodata['contractName']}-{outputAddress}")
        elif parsed_flodata['transferType'] == 'swapParticipation':
            #return {'type':'swapcontract_db', 'contract_db': f"{parsed_flodata['contractName']}-{outputAddress}" ,'accepting_token_db':f"{parsed_flodata['contract-conditions']['accepting_token']}", 'selling_token_db':f"{parsed_flodata['contract-conditions']['selling_token']}"}
            tokenlist.append(parsed_flodata['contract-conditions']['accepting_token'])
            tokenlist.append(parsed_flodata['contract-conditions']['selling_token'])
            contractlist.append(f"{parsed_flodata['contractName']}-{outputAddress}")
        elif parsed_flodata['transferType'] == 'nft':
            #return {'type':'nft_db', 'token_db':f"{parsed_flodata['tokenIdentification']}"}
            tokenlist.append(parsed_flodata['tokenIdentification'])
    elif parsed_flodata['type'] == 'smartContractPays':
        # contract address, token | both of them come from 
        sc_session = create_database_session_orm('smart_contract', {'contract_name':f"{parsed_flodata['contractName']}", 'contract_address':f"{outputAddress}"}, ContractBase)
        token_db = sc_session.query(ContractStructure.value).filter(ContractStructure.attribute=='tokenIdentification').first()[0]
        #return {'type':'smartcontract_db', 'contract_db':f"{parsed_flodata['contractName']}-{outputAddress}", 'token_db':f"{token_db}"}
        tokenlist.append(token_db)
        contractlist.append(f"{parsed_flodata['contractName']}-{outputAddress}")
    elif parsed_flodata['type'] == 'smartContractIncorporation':
        #return {'type':'smartcontract_db', 'contract_db':f"{parsed_flodata['contractName']}-{outputAddress}"}
        contractlist.append(f"{parsed_flodata['contractName']}-{outputAddress}")
    elif parsed_flodata['type'] == 'tokenIncorporation':
        #return {'type':'token_db', 'token_db':f"{parsed_flodata['tokenIdentification']}"}
        tokenlist.append(parsed_flodata['tokenIdentification'])

    return tokenlist, contractlist


def calc_pid_amount(transferBalance, consumedpid):
    consumedpid_sum = 0
    for key in list(consumedpid.keys()):
        consumedpid_sum = perform_decimal_operation('addition', consumedpid_sum, float(consumedpid[key]))
    return transferBalance - consumedpid_sum


def find_addressBalance_from_floAddress(database_session, floAddress):
    query_output = database_session.query(ActiveTable).filter(ActiveTable.address==floAddress, ActiveTable.addressBalance!=None).first()
    if query_output is None:
        return 0
    else:
        return query_output.addressBalance


def rollback_address_balance_processing(db_session, senderAddress, receiverAddress, transferBalance):
    # Find out total sum of address
    # Find out the last entry where address balance is not null, if exists make it null 
    
    # Calculation phase 
    current_receiverBalance = find_addressBalance_from_floAddress(db_session, receiverAddress)
    current_senderBalance = find_addressBalance_from_floAddress(db_session ,senderAddress)
    new_receiverBalance = perform_decimal_operation('subtraction', current_receiverBalance, transferBalance)
    new_senderBalance = perform_decimal_operation('addition', current_senderBalance, transferBalance)

    # Insertion phase 
    # if new receiver balance is 0, then only insert sender address balance 
    # if receiver balance is not 0, then update previous occurence of the receiver address and sender balance 
    # for sender, find out weather 
    # either query out will not come or the last occurence will have address
    # for sender, in all cases we will update the addressBalance of last occurences of senderfloaddress
    # for receiver, if the currentaddressbalance is 0 then do nothing .. and if the currentaddressbalance is not 0 then update the last occurence of receiver address 
    sender_query = db_session.query(ActiveTable).filter(ActiveTable.address==senderAddress).order_by(ActiveTable.id.desc()).first() 
    sender_query.addressBalance = new_senderBalance 
    
    if new_receiverBalance != 0 and new_receiverBalance > 0:
        receiver_query = db_session.query(ActiveTable).filter(ActiveTable.address==receiverAddress).order_by(ActiveTable.id.desc()).limit(2).all()
        if len(receiver_query) == 2:
            receiver_query[1].addressBalance = new_receiverBalance


def find_input_output_addresses(transaction_data):
    # Create vinlist and outputlist
    vinlist = []
    querylist = []
    
    for vin in transaction_data["vin"]:
        vinlist.append([vin["addresses"][0], float(vin["value"])])

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


def rollback_database(blockNumber, dbtype, dbname):
    if dbtype == 'token':
        # Connect to database
        db_session = create_database_session_orm('token', {'token_name':dbname}, TokenBase)
        while(True):
            subqry = db_session.query(func.max(ActiveTable.id))
            activeTable_entry = db_session.query(ActiveTable).filter(ActiveTable.id == subqry).first()
            if activeTable_entry.blockNumber <= blockNumber:
                break
            outputAddress = activeTable_entry.address
            transferAmount = activeTable_entry.transferBalance
            inputAddress = None

            # Find out consumedpid and partially consumed pids 
            parentid = None
            orphaned_parentid = None
            consumedpid = None
            if activeTable_entry.parentid is not None:
                parentid = activeTable_entry.parentid
            if activeTable_entry.orphaned_parentid is not None:
                orphaned_parentid = activeTable_entry.orphaned_parentid
            if activeTable_entry.consumedpid is not None:
                consumedpid = literal_eval(activeTable_entry.consumedpid)

            # filter out based on consumped pid and partially consumed pids 
            if parentid is not None:
                # find query in activeTable with the parentid
                activeTable_pid_entry = db_session.query(ActiveTable).filter(ActiveTable.id == parentid).all()[0]
                # calculate the amount taken from parentid 
                activeTable_pid_entry.transferBalance = activeTable_pid_entry.transferBalance + calc_pid_amount(activeTable_entry.transferBalance, consumedpid)
                inputAddress = activeTable_pid_entry.address
            
            if orphaned_parentid is not None:
                try:
                    orphaned_parentid_entry = db_session.query(ConsumedTable).filter(ConsumedTable.id == orphaned_parentid).all()[0]
                    inputAddress = orphaned_parentid_entry.address
                except:
                    pdb.set_trace()

            if consumedpid != {}:
                # each key of the pid is totally consumed and with its corresponding value written in the end 
                # how can we maintain the order of pid consumption? The bigger pid number will be towards the end 
                # 1. pull the pid number and its details from the consumedpid table 
                for key in list(consumedpid.keys()):
                    consumedpid_entry = db_session.query(ConsumedTable).filter(ConsumedTable.id == key).all()[0]
                    newTransferBalance = consumedpid_entry.transferBalance + consumedpid[key]
                    db_session.add(ActiveTable(id=consumedpid_entry.id, address=consumedpid_entry.address, parentid=consumedpid_entry.parentid ,consumedpid=consumedpid_entry.consumedpid, transferBalance=newTransferBalance, addressBalance = None, orphaned_parentid=consumedpid_entry.orphaned_parentid ,blockNumber=consumedpid_entry.blockNumber))
                    inputAddress = consumedpid_entry.address
                    db_session.delete(consumedpid_entry)

                    orphaned_parentid_entries = db_session.query(ActiveTable).filter(ActiveTable.orphaned_parentid == key).all()
                    if len(orphaned_parentid_entries) != 0:
                        for orphan_entry in orphaned_parentid_entries:
                            orphan_entry.parentid = orphan_entry.orphaned_parentid
                            orphan_entry.orphaned_parentid = None
                    
                    orphaned_parentid_entries = db_session.query(ConsumedTable).filter(ConsumedTable.orphaned_parentid == key).all()
                    if len(orphaned_parentid_entries) != 0:
                        for orphan_entry in orphaned_parentid_entries:
                            orphan_entry.parentid = orphan_entry.orphaned_parentid
                            orphan_entry.orphaned_parentid = None
            
            # update addressBalance
            rollback_address_balance_processing(db_session, inputAddress, outputAddress, transferAmount)

            # delete operations 
            # delete the last row in activeTable and transactionTable 
            db_session.delete(activeTable_entry)
        
        db_session.query(TransactionHistory).filter(TransactionHistory.blockNumber > blockNumber).delete()
        db_session.query(TransferLogs).filter(TransferLogs.blockNumber > blockNumber).delete()
        db_session.commit()

    elif dbtype == 'smartcontract':
        db_session = create_database_session_orm('smart_contract', {'contract_name':f"{dbname['contract_name']}", 'contract_address':f"{dbname['contract_address']}"}, ContractBase)
        db_session.query(ContractTransactionHistory).filter(ContractTransactionHistory.blockNumber > blockNumber).delete()
        db_session.query(ContractParticipants).filter(ContractParticipants.blockNumber > blockNumber).delete()
        db_session.query(ContractDeposits).filter(ContractDeposits.blockNumber > blockNumber).delete()
        db_session.query(ConsumedInfo).filter(ConsumedInfo.blockNumber > blockNumber).delete()
        db_session.query(ContractWinners).filter(ContractWinners.blockNumber > blockNumber).delete()
        db_session.commit()


def delete_database_old(blockNumber, dbname):
    db_session = create_database_session_orm('system_dbs', {'db_name':'system'}, SystemBase)
    databases_to_delete = db_session.query(DatabaseTypeMapping.db_name, DatabaseTypeMapping.db_type).filter(DatabaseTypeMapping.blockNumber>blockNumber).all()

    db_names, db_type = zip(*databases_to_delete)

    for database in databases_to_delete:
        if database[1] in ['token','infinite-token']:
            dirpath = os.path.join(apppath, 'tokens', f"{dbname}.db")
            if os.path.exists(dirpath):
                os.remove(dirpath)
        elif database[1] in ['smartcontract']:
            dirpath = os.path.join(apppath, 'smartcontracts', f"{dbname}.db")
            if os.path.exists(dirpath):
                os.remove(dirpath)
    return db_names


def delete_database(blockNumber, dbname):
    db_session = create_database_session_orm('system_dbs', {'db_name':'system'}, SystemBase)
    databases_to_delete = db_session.query(DatabaseTypeMapping.db_name, DatabaseTypeMapping.db_type).filter(DatabaseTypeMapping.db_name == dbname).all()

    db_names, db_type = zip(*databases_to_delete)

    for database in databases_to_delete:
        if database[1] in ['token','infinite-token','nft']:
            dirpath = os.path.join(apppath, 'tokens', f"{dbname}.db")
            if os.path.exists(dirpath):
                os.remove(dirpath)
        elif database[1] in ['smartcontract']:
            dirpath = os.path.join(apppath, 'smartContracts', f"{dbname}.db")
            if os.path.exists(dirpath):
                os.remove(dirpath)
    return db_names


def system_database_deletions(blockNumber):

    latestcache_session = create_database_session_orm('system_dbs', {'db_name': 'latestCache'}, LatestCacheBase) 

    # delete latestBlocks & latestTransactions entry
    latestcache_session.query(LatestBlocks).filter(LatestBlocks.blockNumber > blockNumber).delete() 
    latestcache_session.query(LatestTransactions).filter(LatestTransactions.blockNumber > blockNumber).delete() 

    # delete activeContracts, contractAddressMapping, DatabaseAddressMapping, rejectedContractTransactionHistory, rejectedTransactionHistory, tokenAddressMapping
    systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
    activeContracts_session = systemdb_session.query(ActiveContracts).filter(ActiveContracts.blockNumber > blockNumber).delete()
    contractAddressMapping_queries = systemdb_session.query(ContractAddressMapping).filter(ContractAddressMapping.blockNumber > blockNumber).delete()
    databaseTypeMapping_queries = systemdb_session.query(DatabaseTypeMapping).filter(DatabaseTypeMapping.blockNumber > blockNumber).delete()
    rejectedContractTransactionHistory_queries = systemdb_session.query(RejectedContractTransactionHistory).filter(RejectedContractTransactionHistory.blockNumber > blockNumber).delete()
    rejectedTransactionHistory_queries = systemdb_session.query(RejectedTransactionHistory).filter(RejectedTransactionHistory.blockNumber > blockNumber).delete()
    tokenAddressMapping_queries = systemdb_session.query(TokenAddressMapping).filter(TokenAddressMapping.blockNumber > blockNumber).delete()
    timeAction_queries = systemdb_session.query(TimeActions).filter(TimeActions.blockNumber > blockNumber).delete()
    systemdb_session.query(SystemData).filter(SystemData.attribute=='lastblockscanned').update({SystemData.value:str(blockNumber)})

    latestcache_session.commit()
    systemdb_session.commit()
    latestcache_session.close()
    systemdb_session.close()


def return_token_contract_set(rollback_block):
    latestcache_session = create_database_session_orm('system_dbs', {'db_name': 'latestCache'}, LatestCacheBase) 
    latestBlocks = latestcache_session.query(LatestBlocks).filter(LatestBlocks.blockNumber > rollback_block).all() 
    lblocks_dict = {} 
    blocknumber_list = [] 
    for block in latestBlocks:
        block_dict = block.__dict__
        lblocks_dict[block_dict['blockNumber']] = {'blockHash':f"{block_dict['blockHash']}", 'jsonData':f"{block_dict['jsonData']}"}
        blocknumber_list.insert(0,block_dict['blockNumber'])

    tokendb_set = set()
    smartcontractdb_set = set()

    for blockindex in blocknumber_list:
        # Find the all the transactions that happened in this block 
        try:
            block_tx_hashes = json.loads(lblocks_dict[str(blockindex)]['jsonData'])['tx']
        except:
            print(f"Block {blockindex} is not found in latestCache. Skipping this block")
            continue

        for txhash in block_tx_hashes:
            # Get the transaction details 
            transaction = latestcache_session.query(LatestTransactions).filter(LatestTransactions.transactionHash == txhash).first() 
            transaction_data = json.loads(transaction.jsonData) 
            inputAddress, outputAddress = find_input_output_addresses(transaction_data) 
            parsed_flodata = literal_eval(transaction.parsedFloData) 
            tokenlist, contractlist = getDatabase_from_parsedFloData(parsed_flodata, inputAddress, outputAddress) 
            
            for token in tokenlist:
                tokendb_set.add(token)
            
            for contract in contractlist:
                smartcontractdb_set.add(contract)

    return tokendb_set, smartcontractdb_set


def initiate_rollback_process():
    '''
    tokendb_set, smartcontractdb_set = return_token_contract_set(rollback_block)
    '''
    
    # Connect to system.db 
    systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
    db_names = systemdb_session.query(DatabaseTypeMapping).all()
    for db in db_names: 
        if db.db_type in ['token', 'nft', 'infinite-token']:
            if db.blockNumber > rollback_block:
                delete_database(rollback_block, f"{db.db_name}")
            else:
                rollback_database(rollback_block, 'token', f"{db.db_name}")
        elif db.db_type in ['smartcontract']:
            if db.blockNumber > rollback_block:
                delete_database(rollback_block, f"{db.db_name}")
            else:
                db_split = db.db_name.rsplit('-',1)
                db_name = {'contract_name':db_split[0], 'contract_address':db_split[1]}
                rollback_database(rollback_block, 'smartcontract', db_name)
    
    '''
    for token_db in tokendb_set:
        token_session = create_database_session_orm('token', {'token_name': token_db}, TokenBase) 
        if token_session.query(TransactionHistory.blockNumber).first()[0] > rollback_block:
            delete_database(rollback_block, token_db)
            token_session.commit()
        else:
            rollback_database(rollback_block, 'token', token_db)
        token_session.close()
    
    for contract_db in smartcontractdb_set:
        contract_session = create_database_session_orm('smartcontract', {'db_name': contract_db}, ContractBase) 
        if contract_session.query(TransactionHistory.blockNumber).first()[0] > rollback_block:
            delete_database(rollback_block, contract_db) 
            contract_session.commit() 
        else:
            rollback_database(rollback_block, 'smartcontract', contract_db)
        contract_session.close()
    '''
    
    system_database_deletions(rollback_block)

    # update lastblockscanned in system_dbs
    latestCache_session = create_database_session_orm('system_dbs', {'db_name': 'latestCache'}, LatestCacheBase)
    lastblockscanned = latestCache_session.query(LatestBlocks.blockNumber).order_by(LatestBlocks.id.desc()).first()[0]
    latestCache_session.close()

    systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
    lastblockscanned_query = systemdb_session.query(SystemData).filter(SystemData.attribute=='lastblockscanned').first()
    lastblockscanned_query.value = rollback_block
    systemdb_session.commit()
    systemdb_session.close()

def rollback_to_block(block_number):
    global rollback_block
    rollback_block = block_number
    start_rollback_process()

def start_rollback_process():
    systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
    lastblockscanned_query = systemdb_session.query(SystemData).filter(SystemData.attribute=='lastblockscanned').first()
    if(rollback_block > int(lastblockscanned_query.value)):
        print('Rollback block is greater than the last scanned block\n Exiting ....')
        sys.exit(0)
    else:
        initiate_rollback_process()

if __name__ == "__main__":
    # Take input from user reg how many blocks to go back in the blockchain
    parser = argparse.ArgumentParser(description='Script tracks RMT using FLO data on the FLO blockchain - https://flo.cash') 
    parser.add_argument('-rb', '--toblocknumer', nargs='?', type=int, help='Rollback the script to the specified block number') 
    parser.add_argument('-r', '--blockcount', nargs='?', type=int, help='Rollback the script to the number of blocks specified') 
    args = parser.parse_args() 

    # Get all the transaction and blockdetails from latestCache reg the transactions in the block
    systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase) 
    lastscannedblock = systemdb_session.query(SystemData.value).filter(SystemData.attribute=='lastblockscanned').first() 
    systemdb_session.close() 
    lastscannedblock = int(lastscannedblock.value) 
    if (args.blockcount and args.toblocknumber):
        print("You can only specify one of the options -b or -c")
        sys.exit(0)
    elif args.blockcount:
        rollback_block = lastscannedblock - args.blockcount
    elif args.toblocknumer:
        rollback_block = args.toblocknumer
    else:
        print("Please specify the number of blocks to rollback")
        sys.exit(0)

    
    start_rollback_process()