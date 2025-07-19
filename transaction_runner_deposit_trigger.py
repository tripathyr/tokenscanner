#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import logging
import os
import sys
from tracktokens33_fix2 import (
    process_contract_deposit_trigger,
    create_database_session_orm,
    TimeActions
)
from models import SystemBase

# Function to generate random hex string for blockhash
def generate_random_hex(bytes_len):
    return os.urandom(bytes_len).hex()

# Configure basic logging to console with timestamp and level
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def main():
    try:
        # Set expiry datetime and block info after expiry time
        expiry_str = "tue oct 19 2027 23:10:00 gmt+0530 (india standard time)"
        
        # 🛠 Remove the trailing timezone label so we can parse
        expiry_str_clean = expiry_str.split(" (", 1)[0]

        # ⏰ Convert to datetime using proper format
        dt = datetime.datetime.strptime(expiry_str_clean, "%a %b %d %Y %H:%M:%S %Z%z")
        expiry_timestamp = int(dt.timestamp())

        # Add 10 minutes for post-expiry block time
        block_time_after_expiry = expiry_timestamp + 600

        # Generate a random block hash
        block_hash = generate_random_hex(32)

        blockinfo = {
            "time": block_time_after_expiry,
            "height": 812349,  # Block after the one where deposit was made
            "hash": block_hash,
        }

        logger.info(f"Block info for processing: {blockinfo}")

        # Connect to system database
        systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
        logger.info("Connected to system database.")

        # Get all active contract deposits
        active_deposits = systemdb_session.query(TimeActions).filter(
            TimeActions.activity == 'contract-deposit',
            TimeActions.status == 'active'
        ).all()

        logger.info(f"Found {len(active_deposits)} active contract deposits to process.")

        if not active_deposits:
            logger.info("No active deposits found. Exiting.")
            systemdb_session.close()
            return

        # Process them
        process_contract_deposit_trigger(blockinfo, systemdb_session, active_deposits)
        logger.info("Completed processing contract deposit expiry refunds.")

        systemdb_session.close()
        logger.info("Database session closed.")

    except Exception as e:
        logger.error(f"Unhandled exception in main: {e}", exc_info=True)

if __name__ == "__main__":
    main()
