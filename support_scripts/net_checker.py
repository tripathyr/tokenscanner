import configparser
import pyflo

config = configparser.ConfigParser()
config.read('config.ini')

net = config['DEFAULT'].get('NET', 'mainnet')
print(f">{net}<")
print(net == 'testnet')
print(net.strip() == 'testnet')




address = "oWooGLbBELNnwq8Z5YmjoVjw8GhBGH3qSP"

print("Mainnet check:")
print(pyflo.is_address_valid(address, testnet=False))

print("Testnet check:")
print(pyflo.is_address_valid(address, testnet=True))
