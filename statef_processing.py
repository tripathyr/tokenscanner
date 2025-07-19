import requests
from operator import attrgetter
import json
import pdb

'''
    USD-INR 
    https://api.exchangerate-api.com/v4/latest/usd 

    Parsed stateF
    "stateF":{
      "bitcoin_price_source":"bitpay",
      "usd_inr_exchange_source":"bitpay"
    }
'''

'''
stateF notes for amount split on contracts

stateF_object = {
    "floaddresses": "oPkHWcvqBHfCortTHScrVBjXLsZhWie99C-oPkHWcvqBHfCortTHScrVBjXLsZhWie99C-oPkHWcvqBHfCortTHScrVBjXLsZhWie99C",
    "splits": "10-20-30",
}

'''

# stateF 
stateF_address = 'oPkHWcvqBHfCortTHScrVBjXLsZhWie99C'

stateF_object = {
      "bitcoin_price_source":"bitpay",
      "usd_inr_exchange_source":"bitpay"
    }

# Flodata object 
flodata_object = {
    "bitpay": {
        "bitcoin_price_source":{
            "api" : "https://bitpay.com/api/rates",
            "path" : [2,"rate"],
            "data_type" : "float"
        },
        "usd_inr_exchange_source":{
            "api" : "https://api.exchangerate-api.com/v4/latest/usd",
            "path" : ["rates","INR"],
            "data_type" : "float"
        }
    }
}


def pull_stateF(floID):
    response = requests.get(f"https://flosight-testnet.ranchimall.net/api/txs/?address={floID}")
    if response.status_code == 200:
        address_details = response.json()
        latest_stateF = address_details['txs'][0]['floData']
        latest_stateF = json.loads(latest_stateF)
        return latest_stateF['stateF']
    else:
        print('API response not valid')

def query_api(api_object):
    api, path, data_type = api_object.values()
    response = requests.get(api)
    if response.status_code == 200:
        # Use path keys to reach the value 
        api_response = response.json()
        for key in path:
            api_response = api_response[key]
        # todo: how to use datatype to convert 
        if data_type == 'float':
            value_at_path = float(api_response)
        return value_at_path
    else:
        print('API response not valid')

def process_stateF(stateF_object, stateF_address):
    flodata_object = pull_stateF(stateF_address)
    processed_values = {}
    for key, value in stateF_object.items():
        external_value = query_api(flodata_object[value][key])
        processed_values[key] = external_value
    return processed_values

if __name__ == '__main__':
    processed_statef = process_stateF(stateF_object, stateF_address)
    print(processed_statef)