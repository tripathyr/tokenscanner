    if type == 'token':
        path = os.path.join(config['DEFAULT']['DATA_PATH'], 'tokens', f"{parameters['token_name']}.db")
        engine = create_engine(f"sqlite:///{path}", echo=True)
        base.metadata.create_all(bind=engine) ''' no change '''
        session = sessionmaker(bind=engine)() ''' no change '''

        if type == 'token':
        # Connection string for MySQL
        token_name = parameters['token_name']
        engine = create_engine(
            f"mysql+pymysql://{username}:{password}@{host}/{database}", 
            echo=True
        )
        base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()