from sqlalchemy import Column, BigInteger, Text, Float
from sqlalchemy.dialects.mysql import DOUBLE

from sqlalchemy.ext.declarative import declarative_base

TokenBase = declarative_base()
ContractBase = declarative_base()
SystemBase = declarative_base()
LatestCacheBase = declarative_base()

class ActiveTable(TokenBase):
    __tablename__ = "activeTable"

    id = Column('id', BigInteger, primary_key=True)
    address = Column('address', Text)
    parentid = Column('parentid', BigInteger)
    consumedpid = Column('consumedpid', Text)
    transferBalance = Column('transferBalance', DOUBLE)
    addressBalance = Column('addressBalance', DOUBLE)
    orphaned_parentid = Column('orphaned_parentid', BigInteger)
    blockNumber = Column('blockNumber', BigInteger)


class ConsumedTable(TokenBase):
    __tablename__ = "consumedTable"

    primaryKey = Column('primaryKey', BigInteger, primary_key=True)
    id = Column('id', BigInteger)
    address = Column('address', Text)
    parentid = Column('parentid', BigInteger)
    consumedpid = Column('consumedpid', Text)
    transferBalance = Column('transferBalance', DOUBLE)
    addressBalance = Column('addressBalance', DOUBLE)
    orphaned_parentid = Column('orphaned_parentid', BigInteger)
    blockNumber = Column('blockNumber', BigInteger)


class TransferLogs(TokenBase):
    __tablename__ = "transferlogs"

    primary_key = Column('id', BigInteger, primary_key=True)
    sourceFloAddress = Column('sourceFloAddress', Text)
    destFloAddress = Column('destFloAddress', Text)
    transferAmount = Column('transferAmount', DOUBLE)
    sourceId = Column('sourceId', BigInteger)
    destinationId = Column('destinationId', BigInteger)
    blockNumber = Column('blockNumber', BigInteger)
    time = Column('time', BigInteger)
    transactionHash = Column('transactionHash', Text)


class TransactionHistory(TokenBase):
    __tablename__ = "transactionHistory"

    primary_key = Column('id', BigInteger, primary_key=True)
    sourceFloAddress = Column('sourceFloAddress', Text)
    destFloAddress = Column('destFloAddress', Text)
    transferAmount = Column('transferAmount', DOUBLE)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)
    time = Column('time', BigInteger)
    transactionHash = Column('transactionHash', Text)
    blockchainReference = Column('blockchainReference', Text)
    jsonData = Column('jsonData', Text)
    transactionType = Column('transactionType', Text)
    parsedFloData = Column('parsedFloData', Text)


class TokenContractAssociation(TokenBase):
    __tablename__ = "tokenContractAssociation"

    primary_key = Column('id', BigInteger, primary_key=True)
    tokenIdentification = Column('tokenIdentification', Text)
    contractName = Column('contractName', Text)
    contractAddress = Column('contractAddress', Text)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)
    time = Column('time', BigInteger)
    transactionHash = Column('transactionHash', Text)
    blockchainReference = Column('blockchainReference', Text)
    jsonData = Column('jsonData', Text)
    transactionType = Column('transactionType', Text)
    parsedFloData = Column('parsedFloData', Text)


class ContractStructure(ContractBase):
    __tablename__ = "contractstructure"

    id = Column('id', BigInteger, primary_key=True)
    attribute = Column('attribute', Text)
    index = Column('index', BigInteger)
    value = Column('value', Text)
    blockNumber = Column('blockNumber', BigInteger)


class ContractParticipants(ContractBase):
    __tablename__ = "contractparticipants"

    id = Column('id', BigInteger, primary_key=True)
    participantAddress = Column('participantAddress', Text)
    tokenAmount = Column('tokenAmount', DOUBLE)
    userChoice = Column('userChoice', Text)
    transactionHash = Column('transactionHash', Text)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)
    winningAmount = Column('winningAmount', DOUBLE)


class ContractTransactionHistory(ContractBase):
    __tablename__ = "contractTransactionHistory"

    primary_key = Column('id', BigInteger, primary_key=True)
    transactionType = Column('transactionType', Text)
    transactionSubType = Column('transactionSubType', Text)
    sourceFloAddress = Column('sourceFloAddress', Text)
    destFloAddress = Column('destFloAddress', Text)
    transferAmount = Column('transferAmount', DOUBLE)
    transferToken = Column('transferToken',Text)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)
    time = Column('time', BigInteger)
    transactionHash = Column('transactionHash', Text)
    blockchainReference = Column('blockchainReference', Text)
    jsonData = Column('jsonData', Text)
    parsedFloData = Column('parsedFloData', Text)


class ContractDeposits(ContractBase):
    __tablename__ = "contractdeposits"

    id = Column('id', BigInteger, primary_key=True)
    depositorAddress = Column('depositorAddress', Text)
    depositAmount = Column('depositAmount', DOUBLE)
    depositBalance = Column('depositBalance', DOUBLE)
    expiryTime = Column('expiryTime', Text)
    unix_expiryTime = Column('unix_expiryTime', BigInteger)
    status = Column('status', Text)
    transactionHash = Column('transactionHash', Text)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)


class ConsumedInfo(ContractBase):
    __tablename__ = "consumedinfo"

    id = Column('id', BigInteger, primary_key=True)
    id_deposittable = Column('id_deposittable', BigInteger)
    transactionHash = Column('transactionHash', Text)
    blockNumber = Column('blockNumber', BigInteger)


class ContractWinners(ContractBase):
    __tablename__ = "contractwinners"

    id = Column('id', BigInteger, primary_key=True)
    participantAddress = Column('participantAddress', Text)
    winningAmount = Column('winningAmount', DOUBLE)
    userChoice = Column('userChoice', Text)
    transactionHash = Column('transactionHash', Text)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)
    referenceTxHash = Column('referenceTxHash', Text)

class ActiveContracts(SystemBase):
    __tablename__ = "activecontracts"

    id = Column('id', BigInteger, primary_key=True)
    contractName = Column('contractName', Text)
    contractAddress = Column('contractAddress', Text)
    status = Column('status', Text)
    tokenIdentification = Column('tokenIdentification', Text)
    contractType = Column('contractType', Text)
    transactionHash = Column('transactionHash', Text)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)
    incorporationDate = Column('incorporationDate', Text)
    expiryDate = Column('expiryDate', Text)
    closeDate = Column('closeDate', Text)


class SystemData(SystemBase):
    __tablename__ = "systemData"

    id = Column('id', BigInteger, primary_key=True)
    attribute = Column('attribute', Text)
    value = Column('value', Text)


class TokenAddressMapping(SystemBase):
    __tablename__ = "tokenAddressMapping"

    id = Column('id', BigInteger, primary_key=True)
    tokenAddress = Column('tokenAddress', Text)
    token = Column('token', Text)
    transactionHash = Column('transactionHash', Text)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)

class DatabaseTypeMapping(SystemBase):
    __tablename__ = "databaseTypeMapping"

    id = Column('id', BigInteger, primary_key=True)
    db_name = Column('db_name', Text)
    db_type = Column('db_type', Text)
    keyword = Column('keyword', Text)
    object_format = Column('object_format', Text)
    blockNumber = Column('blockNumber', BigInteger)


class TimeActions(SystemBase):
    __tablename__ = "time_actions"

    id = Column('id', BigInteger, primary_key=True)
    time = Column('time', Text)
    activity = Column('activity', Text)
    status = Column('status', Text)
    contractName = Column('contractName', Text)
    contractAddress = Column('contractAddress', Text)
    contractType = Column('contractType', Text)
    tokens_db = Column('tokens_db', Text)
    parsed_data = Column('parsed_data', Text)
    transactionHash = Column('transactionHash', Text)
    blockNumber = Column('blockNumber', BigInteger)


class RejectedContractTransactionHistory(SystemBase):
    __tablename__ = "rejectedContractTransactionHistory"

    primary_key = Column('id', BigInteger, primary_key=True)
    transactionType = Column('transactionType', Text)
    transactionSubType = Column('transactionSubType', Text)
    contractName = Column('contractName', Text)
    contractAddress = Column('contractAddress', Text)
    sourceFloAddress = Column('sourceFloAddress', Text)
    destFloAddress = Column('destFloAddress', Text)
    transferAmount = Column('transferAmount', DOUBLE)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)
    time = Column('time', BigInteger)
    transactionHash = Column('transactionHash', Text)
    blockchainReference = Column('blockchainReference', Text)
    jsonData = Column('jsonData', Text)
    rejectComment = Column('rejectComment', Text)
    parsedFloData = Column('parsedFloData', Text)


class RejectedTransactionHistory(SystemBase):
    __tablename__ = "rejectedTransactionHistory"

    primary_key = Column('id', BigInteger, primary_key=True)
    tokenIdentification = Column('tokenIdentification', Text)
    sourceFloAddress = Column('sourceFloAddress', Text)
    destFloAddress = Column('destFloAddress', Text)
    transferAmount = Column('transferAmount', DOUBLE)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)
    time = Column('time', BigInteger)
    transactionHash = Column('transactionHash', Text)
    blockchainReference = Column('blockchainReference', Text)
    jsonData = Column('jsonData', Text)
    rejectComment = Column('rejectComment', Text)
    transactionType = Column('transactionType', Text)
    parsedFloData = Column('parsedFloData', Text)

class ContractAddressMapping(SystemBase):
    __tablename__ = "contractAddressMapping"

    id = Column('id', BigInteger, primary_key=True)
    address = Column('address', Text)
    addressType = Column('addressType', Text)
    contractName = Column('contractName', Text)
    contractAddress = Column('contractAddress', Text)
    tokenAmount = Column('tokenAmount', Float)
    transactionHash = Column('transactionHash', Text)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)

class RatePairs(SystemBase):
    __tablename__ = "ratepairs"

    id = Column('id', BigInteger, primary_key=True)
    ratepair = Column('ratepair', Text)
    price = Column('price', DOUBLE)


class LatestTransactions(LatestCacheBase):
    __tablename__ = "latestTransactions"

    id = Column('id', BigInteger, primary_key=True)
    transactionHash = Column('transactionHash', Text)
    blockNumber = Column('blockNumber', BigInteger)
    jsonData = Column('jsonData', Text)
    transactionType = Column('transactionType', Text)
    parsedFloData = Column('parsedFloData', Text)
    db_reference = Column('db_reference', Text)


class LatestBlocks(LatestCacheBase):
    __tablename__ = "latestBlocks"

    id = Column('id', BigInteger, primary_key=True)
    blockNumber = Column('blockNumber', BigInteger)
    blockHash = Column('blockHash', Text)
    jsonData = Column('jsonData', Text)


class RecentBlocks(LatestCacheBase):
    __tablename__ = "RecentBlocks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    blockNumber = Column(BigInteger, unique=True, nullable=False)
    blockHash = Column(Text, nullable=False)


