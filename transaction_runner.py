#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import hashlib
import os

# -----------------------------
# Base58 encode/decode logic
# -----------------------------

BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

def base58_encode(data):
    num = int.from_bytes(data, byteorder='big')
    encode = ''
    while num > 0:
        num, rem = divmod(num, 58)
        encode = BASE58_ALPHABET[rem] + encode

    pad = 0
    for byte in data:
        if byte == 0:
            pad += 1
        else:
            break
    return '1' * pad + encode

def check_flo_address(address, is_testnet=False):
    if not isinstance(address, str) or len(address) != 34:
        return False
    if any(c not in BASE58_ALPHABET for c in address):
        return False

    def base58_decode(s):
        num = 0
        for char in s:
            num *= 58
            num += BASE58_ALPHABET.index(char)
        full_bytes = num.to_bytes((num.bit_length() + 7) // 8, byteorder='big')
        n_pad = len(s) - len(s.lstrip('1'))
        return b'\x00' * n_pad + full_bytes

    decoded = base58_decode(address)
    if decoded is None or len(decoded) < 5:
        return False

    version_byte = decoded[0]

    if is_testnet:
        if version_byte != 0x73:
            return False
    else:
        if version_byte != 0x23:
            return False

    version_payload = decoded[:-4]
    checksum = decoded[-4:]
    hashed = hashlib.sha256(version_payload).digest()
    hashed = hashlib.sha256(hashed).digest()
    calculated_checksum = hashed[:4]

    return calculated_checksum == checksum

def generate_valid_flo_address(is_testnet=False):
    version_byte = b'\x73' if is_testnet else b'\x23'
    payload = hashlib.sha256(os.urandom(32)).digest()[:20]
    version_payload = version_byte + payload
    checksum = hashlib.sha256(hashlib.sha256(version_payload).digest()).digest()[:4]
    full_data = version_payload + checksum
    return base58_encode(full_data)

def generate_random_hex(bytes_len):
    return os.urandom(bytes_len).hex()

# -----------------------------
# Create dummy tx data
# -----------------------------

def create_dummy_transaction_data(flo_text, is_testnet=True):
    sender_addr = generate_valid_flo_address(is_testnet=is_testnet)
    receiver_addr = generate_valid_flo_address(is_testnet=is_testnet)

    hash = generate_random_hex(32)
    txid = generate_random_hex(32)

    
    blockinfo = {
        "time": 1700060000,
        "height": 123458,
        "hash": hash

    }
    
    transaction_data = {
        "txid": txid,
        "floData": flo_text,
        "vin": [
            {
                "value": 10.0,
                "addresses": [sender_addr]
            }
        ],
        "vout": [
            {
                "value": 5.0,
                "scriptPubKey": {
                    "addresses": [receiver_addr]
                }
            }
        ],
        "blockheight": blockinfo["height"],
        "time": blockinfo["time"],
        "blockhash": hash,
    }

    return transaction_data, blockinfo

# -----------------------------
# REAL PARSING AND PROCESSING
# -----------------------------

from parsing import parse_floData
from tracktokens34 import processTransaction

# -----------------------------
# MAIN DRIVER
# -----------------------------

if __name__ == "__main__":
    flo_text = "Deposit 100 teega1# to T1-B1@ its FLO address being oSKC6hej8b5yoL8ZKhrVYw5gxJuFKLorQB$ with deposit-conditions: (1) expiryTime= Thu Oct 20 2027 23:10:00 GMT+0530 (India Standard Time)"
    tx_data, blockinfo = create_dummy_transaction_data(flo_text, is_testnet=True)

    print("\n--- Original Transaction Data ---")
    print(json.dumps(tx_data, indent=4))

    # Show original addresses
    initial_sender = tx_data["vin"][0]["addresses"][0]
    initial_receiver = tx_data["vout"][0]["scriptPubKey"]["addresses"][0]

    print("\nGenerated sender address:", initial_sender)
    print("Generated receiver address:", initial_receiver)

    # Optional override
    sender_override = input("\nOverride sender address? (blank = keep generated): ").strip()
    receiver_override = input("Override receiver address? (blank = keep generated): ").strip()

    if sender_override:
        tx_data["vin"][0]["addresses"][0] = sender_override

    if receiver_override:
        tx_data["vout"][0]["scriptPubKey"]["addresses"][0] = receiver_override

    # Validate addresses
    final_sender = tx_data["vin"][0]["addresses"][0]
    final_receiver = tx_data["vout"][0]["scriptPubKey"]["addresses"][0]

    sender_valid = check_flo_address(final_sender, is_testnet=True)
    receiver_valid = check_flo_address(final_receiver, is_testnet=True)

    print("\nSender valid:", sender_valid)
    print("Receiver valid:", receiver_valid)

    # Edit floData
    print("\nCurrent FLO Data:")
    print(tx_data["floData"])
    new_text = input("\nEdit FLO data? Press Enter to keep as is, or type new text:\n> ").strip()
    if new_text:
        tx_data["floData"] = new_text
        flo_text = new_text
    else:
        flo_text = tx_data["floData"]

    # ✅ Parse floData again after edits
    parsed_data = parse_floData(flo_text, blockinfo, "testnet")

    # ✅ optionally update transaction floData with cleaned text
    if parsed_data.get("floData"):
        tx_data["floData"] = parsed_data["floData"]

    print("\n--- PARSING RESULT ---")
    print(json.dumps(parsed_data, indent=4))

    # Re-extract updated input/output
    vin0 = tx_data["vin"][0]
    vout0 = tx_data["vout"][0]

    input_address = vin0["addresses"][0]
    input_value = vin0.get("value", 0.0)

    output_address = vout0["scriptPubKey"]["addresses"][0]

    inputlist = [input_address, input_value]
    inputadd = input_address
    outputlist = [output_address]

    # Print BOTH before final confirmation
    print("\n✅ PLEASE VERIFY BELOW BEFORE PROCESSING\n")

    print("→ Parsed Data:")
    print(json.dumps(parsed_data, indent=4))

    print("\n→ Transaction Data:")
    print(json.dumps(tx_data, indent=4))

    print(f"\nFinal input address: {inputadd}")
    print(f"Final output address: {outputlist[0]}")

    confirm = input("\nProceed with processTransaction? (y/n): ").strip().lower()
    if confirm != "y":
        print("Aborted !!! Yayyy.")
        exit(0)

    # Process the transaction
    result = processTransaction(tx_data, parsed_data, blockinfo)

    print("\n✅ ProcessTransaction result:", result)
