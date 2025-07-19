#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import parsing

# Simulated blockinfo
# For continuous-event, block time is arbitrary (use any time in the future)
blockinfo = {
    "time": 1700000000      # e.g. July 2025
}

# Simulated network (adjust if you're testing on mainnet)
NETWORK = "testnet"

# Your test contract
test_flo_data = """Create a smart contract of the name abcd-contract@ of the type one-time-event* using asset bioscope# at the FLO address oUihk92dwptN9MFuHT3zf1ecebbwt1KwsP$ with contract-conditions: (1) expiryTime= Thu Feb 29 2024 20:34:00 GMT+0530 (India Standard Time) (2) payeeAddress= oce73NUkHNzsfB7yk8sXa2pLMV5hr153eZ:100 end-contract-conditions"""

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
