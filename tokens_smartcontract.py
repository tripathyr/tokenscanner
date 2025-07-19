                     
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
    # Now we connect to flosight's websocket API to get information about the l>
    # Neturl is the URL for Flosight API whose websocket endpoint is being conn>

    asyncio.get_event_loop().run_until_complete(connect_to_websocket(websocket_>


