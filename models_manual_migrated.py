from sqlalchemy import Column, Integer, Float, String
from sqlalchemy.ext.declarative import declarative_base

TokenBase = declarative_base()
ContractBase = declarative_base()
ContinuosContractBase = declarative_base()
SystemBase = declarative_base()
LatestCacheBase = declarative_base()

class ActiveTable(TokenBase):
    __tablename__ = "activeTable"

    id = Column('id', Integer, primary_key=True)
    address = Column('address', String(600))
    parentid = Column('parentid', Integer)
    consumedpid = Column('consumedpid', String(600))
    transferBalance = Column('transferBalance', Float)
    addressBalance = Column('addressBalance', Float)
    orphaned_parentid = Column('orphaned_parentid', Integer)
    blockNumber = Column('blockNumber', Integer)

class ConsumedTable(TokenBase):
    __tablename__ = "consumedTable"

    primaryKey = Column('primaryKey', Integer, primary_key=True)
    id = Column('id', Integer)
    address = Column('address', String(600))
    parentid = Column('parentid', Integer)
    consumedpid = Column('consumedpid', String(600))
    transferBalance = Column('transferBalance', Float)
    addressBalance = Column('addressBalance', Float)
    orphaned_parentid = Column('orphaned_parentid', Integer)
    blockNumber = Column('blockNumber', Integer)

class TransferLogs(TokenBase):
    __tablename__ = "transferlogs"

    primary_key = Column('id', Integer, primary_key=True)
    sourceFloAddress = Column('sourceFloAddress', String(600))
    destFloAddress = Column('destFloAddress', String(600))
    transferAmount = Column('transferAmount', Float)
    sourceId = Column('sourceId', Integer)
    destinationId = Column('destinationId', Integer)
    blockNumber = Column('blockNumber', Integer)
    time = Column('time', Integer)
    transactionHash = Column('transactionHash', String(600))

class TransactionHistory(TokenBase):
    __tablename__ = "transactionHistory"

    primary_key = Column('id', Integer, primary_key=True)
    sourceFloAddress = Column('sourceFloAddress', String(600))
    destFloAddress = Column('destFloAddress', String(600))
    transferAmount = Column('transferAmount', Float)
    blockNumber = Column('blockNumber', Integer)
    blockHash = Column('blockHash', String(600))
    time = Column('time', Integer)
    transactionHash = Column('transactionHash', String(600))
    blockchainReference = Column('blockchainReference', String(600))
    jsonData = Column('jsonData', String(600))
    transactionType = Column('transactionType', String(600))
    parsedFloData = Column('parsedFloData', String(600))

class TokenContractAssociation(TokenBase):
    __tablename__ = "tokenContractAssociation"

    primary_key = Column('id', Integer, primary_key=True)
    tokenIdentification = Column('tokenIdentification', String(600))
    contractName = Column('contractName', String(600))
    contractAddress = Column('contractAddress', String(600))
    blockNumber = Column('blockNumber', Integer)
    blockHash = Column('blockHash', String(600))
    time = Column('time', Integer)
    transactionHash = Column('transactionHash', String(600))
    blockchainReference = Column('blockchainReference', String(600))
    jsonData = Column('jsonData', String(600))
    transactionType = Column('transactionType', String(600))
    parsedFloData = Column('parsedFloData', String(600))

class ContractStructure(ContractBase):
    __tablename__ = "contractstructure"

    id = Column('id', Integer, primary_key=True)
    attribute = Column('attribute', String(600))
    index = Column('index', Integer)
    value = Column('value', String(600))

# Continue updating all classes similarly

class ActiveContracts(SystemBase):
    __tablename__ = "activecontracts"

    id = Column('id', Integer, primary_key=True)
    contractName = Column('contractName', String(600))
    contractAddress = Column('contractAddress', String(600))
    status = Column('status', String(600))
    tokenIdentification = Column('tokenIdentification', String(600))
    contractType = Column('contractType', String(600))
    transactionHash = Column('transactionHash', String(600))
    blockNumber = Column('blockNumber', Integer)
    blockHash = Column('blockHash', String(600))
    incorporationDate = Column('incorporationDate', String(600))
    expiryDate = Column('expiryDate', String(600))
    closeDate = Column('closeDate', String(600))

class SystemData(SystemBase):
    __tablename__ = "systemData"

    id = Column('id', Integer, primary_key=True)
    attribute = Column('attribute', String(600))
    value = Column('value', String(600))

# Continue for all other classes similarly
