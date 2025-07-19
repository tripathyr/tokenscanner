import re
import hashlib

def check_flo_address(address, is_testnet=False):
    BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

    # Quick regex check for length and characters
    regex = r"^[Feo][" + BASE58_ALPHABET + r"]{33}$"
    if not re.match(regex, address):
        return False

    # Base58 decode
    def base58_decode(s):
        num = 0
        for char in s:
            num *= 58
            if char not in BASE58_ALPHABET:
                return None
            num += BASE58_ALPHABET.index(char)
        # Convert to bytes
        full_bytes = num.to_bytes((num.bit_length() + 7) // 8, byteorder='big')
        # Add leading zero bytes if address starts with '1's
        n_pad = len(s) - len(s.lstrip('1'))
        return b'\x00' * n_pad + full_bytes

    decoded = base58_decode(address)
    if decoded is None or len(decoded) < 5:
        return False

    # Check checksum
    version_payload = decoded[:-4]
    checksum = decoded[-4:]
    hashed = hashlib.sha256(version_payload).digest()
    hashed = hashlib.sha256(hashed).digest()
    calculated_checksum = hashed[:4]

    return calculated_checksum == checksum


# ============================
# TEST ADDRESSES
# ============================

addresses_to_test = [
    "FUSBi3sQ9YFRs76wiMrDYmhqguPCXue8po",
    "es7CU9oZwHq4hk6QwnLY2HFp6MrBKE5bLo",
    "FRBAN4f9ni8PpkarF643suoYMRS6jAY4bC",
    "oUihk92dwptN9MFuHT3zf1ecebbwt1KwsP"
]

print("Testing FLO addresses:\n")

for addr in addresses_to_test:
    valid = check_flo_address(addr)
    print(f"{addr:40} → Valid: {valid}")
