import hashlib

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
    if len(address) != 34:
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
        if version_byte != 0x73:          # ✅ Correct for FLO testnet
            return False
    else:
        if version_byte != 0x23:          # ✅ Correct for FLO mainnet
            return False

    version_payload = decoded[:-4]
    checksum = decoded[-4:]
    hashed = hashlib.sha256(version_payload).digest()
    hashed = hashlib.sha256(hashed).digest()
    calculated_checksum = hashed[:4]

    return calculated_checksum == checksum


def generate_valid_flo_address(is_testnet=False):
    version_byte = b'\x73' if is_testnet else b'\x23'
    payload = hashlib.sha256(b"dummy payload").digest()[:20]
    version_payload = version_byte + payload
    checksum = hashlib.sha256(hashlib.sha256(version_payload).digest()).digest()[:4]
    full_data = version_payload + checksum
    return base58_encode(full_data)


def create_dummy_transaction_data(flo_text, is_testnet=True):
    sender_addr = generate_valid_flo_address(is_testnet=is_testnet)
    receiver_addr = generate_valid_flo_address(is_testnet=is_testnet)

    blockinfo = {
        "time": 1700000000,
        "height": 123456,
        "blockhash": "FAKE_BLOCK_HASH"
    }

    transaction_data = {
        "txid": "FAKE_TXID_1234567890",
        "floData": flo_text,
        "vin": [
            {
                "addr": sender_addr,
                "value": 10.0,
            }
        ],
        "vout": [
            {
                "addr": receiver_addr,
                "value": 5.0,
            }
        ],
        "blockheight": blockinfo["height"],
        "time": blockinfo["time"],
        "blockhash": blockinfo["blockhash"],
    }

    return transaction_data, blockinfo


flo_text = "create 500 million rmt#"
tx_data, blockinfo = create_dummy_transaction_data(flo_text, is_testnet=True)

print("Sender address:", tx_data["vin"][0]["addr"])
print("Receiver address:", tx_data["vout"][0]["addr"])
print("Sender valid?", check_flo_address(tx_data["vin"][0]["addr"], is_testnet=True))
print("Receiver valid?", check_flo_address(tx_data["vout"][0]["addr"], is_testnet=True))
