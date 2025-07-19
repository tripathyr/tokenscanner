import hashlib
def base58_encode(data):
    BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    num = int.from_bytes(data, byteorder='big')
    encode = ''
    while num > 0:
        num, rem = divmod(num, 58)
        encode = BASE58_ALPHABET[rem] + encode

    # Add '1' for each leading zero byte
    pad = 0
    for byte in data:
        if byte == 0:
            pad += 1
        else:
            break

    return '1' * pad + encode


version_byte = bytes([0x73])  # FLO testnet pubKeyHash
payload = hashlib.sha256(b"dummy test").digest()[:20]

version_payload = version_byte + payload
checksum = hashlib.sha256(hashlib.sha256(version_payload).digest()).digest()[:4]

full_data = version_payload + checksum
address = base58_encode(full_data)

print(address)
