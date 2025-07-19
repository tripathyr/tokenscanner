#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import parsing

# Simulated blockinfo
# For continuous-event, block time is arbitrary (use any time in the future)
blockinfo = {
    "time": 1300000000      # e.g. July 2025
}

# Simulated network (adjust if you're testing on mainnet)
NETWORK = "testnet"

# Your test contract
test_flo_data = """Create a smart contract of the name all-crowd-fund-7@ of the type one-time-event* using asset bioscope# at the FLO address oYX4GvBYtfTBNyUFRCdtYubu7ZS4gchvrb$ with contract-conditions:(1) expiryTime= Sun Nov 15 2022 12:30:00 GMT+0530 (2) payeeAddress=oQotdnMBAP1wZ6Kiofx54S2jNjKGiFLYD7:1:oMunmikKvxsMSTYzShm2X5tGrYDt9EYPij:29:oRpvvGEVKwWiMnzZ528fPhiA2cZA3HgXY5:30:oWpVCjPDGzaiVfEFHs6QVM56V1uY1HyCJJ:40 (3) minimumsubscriptionamount=1 (4) contractAmount=0.6 end-contract-conditions """

print("🧪 Testing smart contract creation parsing...\n")

# Run the parser
result = parsing.parse_flodata(test_flo_data, blockinfo, NETWORK)

# Print result
print("✅ Parsing Result:")
print(result)

if result.get("type") == "noise":
    print("\n⚠️ Parser returned NOISE. This means it failed to recognize the contract.")
else:
    print("\n🎉 Parser recognized this as a valid contract creation transaction.")
