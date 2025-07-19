import logging
from dateutil import tz

# Import your function
from parsing import extract_contract_conditions

# Optional: configure logging to console
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")



# Dummy function required if not imported:
def extract_substring_between(test_str, sub1, sub2):
    idx1 = test_str.index(sub1)
    idx2 = test_str.index(sub2)
    return test_str[idx1 + len(sub1): idx2]

# -----------------------------
# Example test cases
# -----------------------------

test_cases = [
    # ✅ normal case
    {
        "desc": "Normal expiry time (GMT+0530)",
        "text": """
            contract-conditions: (1) expiryTime= Sat Apr 29 2023 20:50:00 GMT+0530 (2) payeeAddress= oXYZabc123 end-contract-conditions
        """,
        "contract_type": "one-time-event",
        "blocktime": 1682788800  # some timestamp after Apr 29 2023
    },

    # ✅ different day name
    {
        "desc": "Different weekday name (Saturday written fully)",
        "text": """
            contract-conditions: (1) expiryTime= Saturday Apr 29 2023 20:50:00 GMT+0530 (2) payeeAddress= oXYZabc123 end-contract-conditions
        """,
        "contract_type": "one-time-event",
        "blocktime": 1682788800
    },

    # ✅ uppercase month
    {
        "desc": "Uppercase month",
        "text": """
            contract-conditions: (1) expiryTime= Sat APR 29 2023 20:50:00 GMT+0530 (2) payeeAddress= oXYZabc123 end-contract-conditions
        """,
        "contract_type": "one-time-event",
        "blocktime": 1682788800
    },

    # ✅ No timezone specified
    {
        "desc": "No timezone specified (should default to UTC)",
        "text": """
            contract-conditions: (1) expiryTime= Sat Apr 29 2023 20:50:00 (2) payeeAddress= oXYZabc123 end-contract-conditions
        """,
        "contract_type": "one-time-event",
        "blocktime": 1682788800
    },

    # ✅ time earlier than block time (should reject)
    {
        "desc": "Expiry before blocktime (should reject)",
        "text": """
            contract-conditions: (1) expiryTime= Sat Apr 29 2020 20:50:00 GMT+0530 (2) payeeAddress= oXYZabc123 end-contract-conditions
        """,
        "contract_type": "one-time-event",
        "blocktime": 1682788800
    }
]

for test in test_cases:
    print("\n-------------------------------")
    print("TEST:", test["desc"])
    result = extract_contract_conditions(
        text=test["text"],
        contract_type=test["contract_type"],
        blocktime=test["blocktime"]
    )
    print("Result:", result)
