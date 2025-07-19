#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Adjust this import to your actual path
import parsing

# Simulated blockinfo
blockinfo = {
    "time": "2025-07-10T12:34:56Z"   # Some ISO timestamp
}

# Simulated network (adjust as needed: "mainnet" or "testnet")
NETWORK = "testnet"

# The exact test input you provided
test_flo_data = """Create a smart contract of the name ABC-token-crowdfunding@ of the type one-time-event* using asset bioscope# at the FLO address oTvJzjA96hb57WakJSMQ8cXfAxCqQCqcKD$ with contract-conditions: (1) expiryTime= Fri Oct 06 2028 14:10:00 GMT+0530 (India Standard Time) (2) contractamount= 0.0001 (3) minimumsubscriptionamount= 0.0003 (4) maximumsubscriptionamount= 0.0005 (5) payeeAddress= oQotdnMBAP1wZ6Kiofx54S2jNjKGiFLYD7:100 end-contract-conditions"""


print("🧪 Testing smart contract creation parsing...\n")



# Run the parser
result = parsing.parse_flodata(test_flo_data, blockinfo, NETWORK)

# Print result
print("✅ Parsing Result:")
print(result)

# Check if noise
if result.get("type") == "noise":
    print("\n⚠️ Parser returned NOISE. This means it failed to recognize the contract.")
else:
    print("\n🎉 Parser recognized this as a valid contract creation transaction.")
