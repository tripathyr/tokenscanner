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
test_flo_data = """Create Smart Contract with the name swap-teega-bioscope@ of the type continuous-event* at the address oNKUkFHWBFCLhkS5z2LqAnCYfta3DRwVvg$ with contract-conditions : (1) subtype = tokenswap (2) accepting_token = bioscope# (3) selling_token = teega# (4) price = '0.5' (5) priceType = predetermined end-contract-conditions"""

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
