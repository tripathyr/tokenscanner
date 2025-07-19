#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Adjust this import if your code is in a package or different file
import parsing

# Simulated blockinfo
# Use an arbitrary future block time
blockinfo = {
    "time": 1300000000    # e.g. ~2025
}

# Simulated network
NETWORK = "testnet"

# All your test lists
text_lists = {
    "text_list": [
        "create 500 million rmt#",
        "transfer 200 rmt#",
        "Create Smart Contract with the name India-elections-2019@ of the type one-time-event* using the asset rmt# at the address F7osBpjDDV1mSSnMNrLudEQQ3cwDJ2dPR1$ with contract-conditions: (1) contractAmount=0.001rmt (2) userChoices=Narendra Modi wins| Narendra Modi loses (3) expiryTime= Wed May 22 2019 21:00:00 GMT+0530",
        "send 0.001 rmt# to india-elections-2019@ to FLO address F7osBpjDDV1mSSnMNrLudEQQ3cwDJ2dPR1 with the userchoice:'narendra modi wins'",
        "india-elections-2019@ winning-choice:'narendra modi wins'",
        "Create Smart Contract with the name India-elections-2019@ of the type one-time-event* using the asset rmt# at the address F7osBpjDDV1mSSnMNrLudEQQ3cwDJ2dPR1$ with contract-conditions: (1) contractAmount=0.001rmt (2) expiryTime= Wed May 22 2019 21:00:00 GMT+0530",
        "send 0.001 rmt# to india-elections-2019@ to FLO address F7osBpjDDV1mSSnMNrLudEQQ3cwDJ2dPR1",
        "Create Smart Contract with the name swap-rupee-bioscope@ of the type continuous-event* at the address oRRCHWouTpMSPuL6yZRwFCuh87ZhuHoL78$ with contract-conditions : (1) subtype = tokenswap (2) accepting_token = rupee# (3) selling_token = bioscope# (4) price = '15' (5) priceType = ‘predetermined’ (6) direction = oneway",
        "Deposit 15 bioscope# to swap-rupee-bioscope@ its FLO address being oRRCHWouTpMSPuL6yZRwFCuh87ZhuHoL78$ with deposit-conditions: (1) expiryTime= Wed Nov 17 2021 21:00:00 GMT+0530 ",
        "Send 15 rupee# to swap-rupee-article@ its FLO address being FJXw6QGVVaZVvqpyF422Aj4FWQ6jm8p2dL$",
        "send 0.001 rmt# to india-elections-2019@ to FLO address F7osBpjDDV1mSSnMNrLudEQQ3cwDJ2dPR1 with the userchoice:'narendra modi wins'"
    ],

    "text_list1": [
        'create usd# as infinite-token',
        'transfer 10 usd#',
        'Create 100 albumname# as NFT with 2CF24DBA5FB0A30E26E83B2AC5B9E29E1B161E5C1FA7425E73043362938B9824 as asset hash',
        'Transfer 10 albumname# nft',
        'Create 400 rmt#',
        'Transfer 20 rmt#'
    ],

    "text_list2": [
        '''Create Smart Contract with the name swap-rupee-bioscope@ of the type continuous-event* 
        at the address stateF=bitcoin_price_source:bitpay:usd_inr_exchange_source:bitpay end-stateF oYzeeUBWRpzRuczW6myh2LHGnXPyR2Bc6k$ with contract-conditions :
        (1) subtype = tokenswap
        (2) accepting_token = rupee#
        (3) selling_token = sreeram#
        (4) price = "15"
        (5) priceType="predetermined" end-contract-conditions''',

        '''
        Create a smart contract of the name simple-crowd-fund@ of the type one-time-event* using asset bioscope# at the FLO address oQkpZCBcAWc945viKqFmJVbVG4aKY4V3Gz$ with contract-conditions:(1) expiryTime= Tue Sep 13 2022 16:10:00 GMT+0530  (2) payeeAddress=oQotdnMBAP1wZ6Kiofx54S2jNjKGiFLYD7 end-contract-conditions
        ''',

        '''
        Create a smart contract of the name simple-crowd-fund@ of the type one-time-event* using asset bioscope# at the FLO address oQkpZCBcAWc945viKqFmJVbVG4aKY4V3Gz$ with contract-conditions:(1) expiryTime= Tue Sep 13 2022 16:10:00 GMT+0530  (2) payeeAddress=oU412TvcMe2ah2xzqFpA95vBJ1RoPZY1LR:10:oVq6QTUeNLh8sapQ6J6EjMQMKHxFCt3uAq:20:oLE79kdHPEZ2bxa3PwtysbJeLo9hvPgizU:60:ocdCT9RAzWVsUncMu24r3HXKXFCXD7gTqh:10 end-contract-conditions
        ''',

        '''
        Create a smart contract of the name simple-crowd-fund@ of the type one-time-event* using asset bioscope# at the FLO address oQkpZCBcAWc945viKqFmJVbVG4aKY4V3Gz$ with contract-conditions:(1) expiryTime= Tue Sep 13 2022 16:10:00 GMT+0530  (2) payeeAddress=oU412TvcMe2ah2xzqFpA95vBJ1RoPZY1LR end-contract-conditions
        ''',

        '''
        Create a smart contract of the name all-crowd-fund-7@ of the type one-time-event* using asset bioscope# at the FLO address oYX4GvBYtfTBNyUFRCdtYubu7ZS4gchvrb$ with contract-conditions:(1) expiryTime= Sun Nov 15 2022 12:30:00 GMT+0530 (2) payeeAddress=oQotdnMBAP1wZ6Kiofx54S2jNjKGiFLYD7:10:oMunmikKvxsMSTYzShm2X5tGrYDt9EYPij:20:oRpvvGEVKwWiMnzZ528fPhiA2cZA3HgXY5:30:oWpVCjPDGzaiVfEFHs6QVM56V1uY1HyCJJ:40 (3) minimumsubscriptionamount=1 (5) contractAmount=0.6 end-contract-conditions
        ''',

        '''
        Create a smart contract of the name all-crowd-fund-7@ of the type one-time-event* using asset bioscope# at the FLO address oYX4GvBYtfTBNyUFRCdtYubu7ZS4gchvrb$ with contract-conditions:(1) expiryTime= Sun Nov 15 2022 12:30:00 GMT+0530 (2) payeeAddress=oQotdnMBAP1wZ6Kiofx54S2jNjKGiFLYD7:0:oMunmikKvxsMSTYzShm2X5tGrYDt9EYPij:30:oRpvvGEVKwWiMnzZ528fPhiA2cZA3HgXY5:30:oWpVCjPDGzaiVfEFHs6QVM56V1uY1HyCJJ:40 (3) minimumsubscriptionamount=1 (4) contractAmount=0.6 end-contract-conditions
        ''',

        '''send 0.02 bioscope# to twitter-survive@ to FLO address oVbebBNuERWbouDg65zLfdataWEMTnsL8r with the userchoice: survives''',

        '''
        Create a smart contract of the name twitter-survive@ of the type one-time-event* using asset bioscope# at the FLO address oVbebBNuERWbouDg65zLfdataWEMTnsL8r$ with contract-conditions:(1) expiryTime= Sun Nov 15 2022 14:55:00 GMT+0530  (2) userchoices= survives | dies (3) minimumsubscriptionamount=0.04 (4) maximumsubscriptionamount=1 (5) contractAmount=0.02 end-contract-conditions
        ''',

        '''
        create 0 teega# 
        ''',
        '''Create a smart contract of the name all-crowd-fund-7@ of the type one-time-event* using asset bioscope# at the FLO address oYX4GvBYtfTBNyUFRCdtYubu7ZS4gchvrb$ with contract-conditions:(1) expiryTime= Sun Nov 15 2022 12:30:00 GMT+0530 (2) payeeAddress=oQotdnMBAP1wZ6Kiofx54S2jNjKGiFLYD7:1:oMunmikKvxsMSTYzShm2X5tGrYDt9EYPij:29:oRpvvGEVKwWiMnzZ528fPhiA2cZA3HgXY5:30:oWpVCjPDGzaiVfEFHs6QVM56V1uY1HyCJJ:40 (3) minimumsubscriptionamount=1 (4) contractAmount=0.6 end-contract-conditions '''

    ]
}

# Run the parser on all inputs
for list_name, texts in text_lists.items():
    print(f"\n=== Running tests for {list_name} ===\n")

    for idx, test_text in enumerate(texts, 1):
        print(f"--- Test #{idx} ---")
        print("INPUT TEXT:")
        print(test_text.strip())
        print("\nParsing result:")

        result = parsing.parse_flodata(test_text, blockinfo, NETWORK)

        print(result)

        if result.get("type") == "noise":
            print("⚠️  Returned NOISE (not recognized).")
        else:
            print("🎉 Successfully recognized transaction type:", result.get("type"))

        print("\n" + "=" * 50 + "\n")
