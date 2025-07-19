import json

# Your fixed function from earlier
def process_committee_flodata(flodata):
    """
    Processes floData for contract-committee and returns
    a dict of actions: {"add": [...], "remove": [...]}
    """
    actions = {"add": [], "remove": []}
    try:
        contract_committee_actions = flodata['token-tracker']['contract-committee']
    except KeyError:
        print('No contract-committee entry found in floData.')
    else:
        for action in contract_committee_actions:
            if action in actions:
                for floid in contract_committee_actions[action]:
                    clean_addr = floid.strip()
                    actions[action].append(clean_addr)
    return actions

# Simulate refresh_committee_list logic in-memory
def test_refresh_committee_list(transactions):
    """
    Takes a list of floData JSON strings, simulates scanning transactions.
    Returns the final committee list.
    """
    address_actions = {}

    # Simulate scanning transactions oldest to newest
    for i, floData_str in enumerate(transactions):
        print(f"\n[TEST] Processing transaction #{i+1}")
        tx_flodata = json.loads(floData_str)
        actions = process_committee_flodata(tx_flodata)

        for addr in actions["add"]:
            address_actions[addr] = "add"
            print(f"Marked {addr} as ADD")

        for addr in actions["remove"]:
            address_actions[addr] = "remove"
            print(f"Marked {addr} as REMOVE")

    # Final committee list = all addresses whose last action was "add"
    committee_list = [
        addr for addr, action in address_actions.items() if action == "add"
    ]

    print(f"\n[TEST] Final committee list: {committee_list}")
    return committee_list

# Example test scenario
if __name__ == "__main__":
    # Define a series of test transactions
    transactions = [
        # Transaction 1: ADD
        '{"token-tracker":{"contract-committee":{"add":["oasi4pXJmfLGvpPaqW225Cc2uXtEViAEMn"]}}}',

        # Transaction 2: REMOVE
        '{"token-tracker":{"contract-committee":{"remove":["oasi4pXJmfLGvpPaqW225Cc2uXtEViAEMn"]}}}',

        # Transaction 3: ADD again
        '{"token-tracker":{"contract-committee":{"add":["oasi4pXJmfLGvpPaqW225Cc2uXtEViAEMn"]}}}'
    ]

    # Run the test
    final_list = test_refresh_committee_list(transactions)
