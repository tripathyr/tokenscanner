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

def generate_random_hex(bytes_len):
    return os.urandom(bytes_len).hex()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def main():
    try:
        # Set test expiry datetime
        expiry_str = "Tue Oct 19 2027 23:10:00 GMT+0530 (India Standard Time)"
        expiry_str_clean = expiry_str.split(" (", 1)[0]
        dt = datetime.datetime.strptime(expiry_str_clean, "%a %b %d %Y %H:%M:%S %Z%z")
        expiry_timestamp = int(dt.timestamp())

        # Simulate block time after expiry
        block_time_after_expiry = expiry_timestamp + 600
        block_hash = generate_random_hex(32)

        blockinfo = {
            "time": block_time_after_expiry,
            "height": 812349,
            "hash": block_hash,
        }

        logger.info(f"🧱 Simulated block info: {blockinfo}")

        # Connect to system DB
        systemdb_session = create_database_session_orm('system_dbs', {'db_name': 'system'}, SystemBase)
        logger.info("✅ Connected to system DB")

        # Filter only expired & active deposits
        expired_untriggered = systemdb_session.query(TimeActions).filter(
            TimeActions.activity == 'contract-deposit',
            TimeActions.status == 'active'
        ).all()

        filtered = []
        for ta in expired_untriggered:
            try:
                # Defensive check for missing expiryTime
                expiry_time = ta.parsed_data and eval(ta.parsed_data).get("expiryTime")
                if not expiry_time:
                    continue
                expiry_ts = int(expiry_time)
                if expiry_ts < block_time_after_expiry:
                    filtered.append(ta)
            except Exception as e:
                logger.warning(f"Skipping malformed record {ta.contractName}: {e}")

        logger.info(f"🧪 Found {len(filtered)} expired, untriggered contract deposits")

        if not filtered:
            logger.info("🚫 No eligible expired untriggered deposits to process.")
            return

        process_contract_deposit_trigger(blockinfo, systemdb_session, filtered)
        logger.info("✅ Completed processing expired untriggered deposits")

    except Exception as e:
        logger.error(f"❌ Error during processing: {e}", exc_info=True)

    finally:
        try:
            systemdb_session.close()
            logger.info("🔒 DB session closed.")
        except Exception:
            pass

if __name__ == "__main__":
    main()
