from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

def create_database_session_orm(type, parameters, base):
    # Common MySQL connection parameters
    username = 'your_username'   # replace with your MySQL username
    password = 'your_password'   # replace with your MySQL password
    host = 'localhost'           # replace with your MySQL host
    database = 'your_database'   # replace with your MySQL database name
    
    if type == 'token':
        # Connection string for MySQL
        token_name = parameters['token_name']
        engine = create_engine(
            f"mysql+pymysql://{username}:{password}@{host}/{database}", 
            echo=True
        )
        base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()

    elif type == 'smart_contract':
        # Connection string for MySQL
        contract_name = parameters['contract_name']
        contract_address = parameters['contract_address']
        engine = create_engine(
            f"mysql+pymysql://{username}:{password}@{host}/{database}", 
            echo=True
        )
        base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()

    elif type == 'system_dbs':
        # Connection string for MySQL
        db_name = parameters['db_name']
        engine = create_engine(
            f"mysql+pymysql://{username}:{password}@{host}/{database}", 
            echo=False
        )
        base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()

    return session
