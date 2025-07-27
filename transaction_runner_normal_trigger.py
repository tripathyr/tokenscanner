#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import logging
import sys
from tracktokens33_fix3 import (
    process_contract_time_trigger,
    create_database_session_orm,
    TimeActions
)
from models import SystemBase


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def main():
    try:
        # Use a block time AFTER the trigger time to activate contract triggers
        trigger_time_str = "Fri Sep 29 2025 19:10:00 GMT+0530"  # 1 minute after expiry
        dt = datetime.datetime.strptime(trigger_time_str, "%a %b %d %Y %H:%M:%S GMT%z")
        trigger_timestamp = int(dt.timestamp())
        block_time_after_trigger = trigger_timestamp  # exactly at trigger time

        blockinfo = {
            "time": block_time_after_trigger,
            "height": 1234591,  # adjust as needed, just after expiry block
            "hash": "d1e3c5a18a5f9b0d1c6e3a2fcb45a12f7d9e8c4321a3b4c5d6e7f8a9b0c1d2e3",
            "size": 1234
        }

        logger.info(f"Block info for processing contract time trigger: {blockinfo}")

        # Create systemdb session (adjust db_name if needed)
        systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
        logger.info("Connected to system database.")

        # Query active contracts with contract-time-trigger activity and active status
        active_contracts = systemdb_session.query(TimeActions).filter(
            TimeActions.activity == 'contract-time-trigger',
            TimeActions.status == 'active'
        ).all()

        logger.info(f"Found {len(active_contracts)} active contracts for time trigger processing.")

        if not active_contracts:
            logger.info("No active contract-time-trigger entries found. Exiting.")
            systemdb_session.close()
            return

        # Call your trigger processor function
        process_contract_time_trigger(blockinfo, systemdb_session, active_contracts)
        logger.info("Completed contract time trigger processing.")

        systemdb_session.close()
        logger.info("Database session closed.")

    except Exception as e:
        logger.error(f"Unhandled exception in main: {e}", exc_info=True)

if __name__ == "__main__":
    main()
