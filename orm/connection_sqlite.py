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