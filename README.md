![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

# Ligther CCXT Adapter

A CCXT-compatible adapter/wrapper for the Lighter Python SDK. It maps Lighter SDK methods onto familiar CCXT interfaces.

- CCXT: https://docs.ccxt.com/
- Lighter SDK (Python): https://github.com/elliottech/lighter-python/

# Features

- CCXT-style API backed by the Lighter SDK
- Simple environment-based configuration
- Python 3.11+ support, aiohttp till 3.9.3

## Installation

For installation and inclusion in another projects use

```
pip install {localpath}\lighter_ccxt_adapter      
or
pip install git+https://github.com/marcelkb/lighter-ccxt-adapter.git  
```

For windows Lighter SDK > 0.1.4 is needed which includes windows compiled signer (commit from 13.10.2025).
Alternative check out repository and install locally with

```
pip install {localpath}\lighter-python 
or
pip install git+https://github.com/elliottech/lighter-python.git
```

## Environment Setup

Create a `.env` file in the project root with the following variables:

```
ACCOUNT_ID=Your Lighter Account ID
PRIV= Your Api private key
WALLET= Your Api puplic wallet address
API_KEY_INDEX= Your api index
L1_WALLET_ADDRESS= Your lighter main wallet Adddress
```

## Usage

```
from lighter_ccxt_adapter import Lighter

    load_dotenv(".env.lighter")
    ACCOUNT_ID = int(os.environ.get("ACCOUNT_ID"))
    PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
    WALLET = os.environ.get("WALLET")
    API_KEY_INDEX = int(os.environ.get("API_KEY_INDEX"))

    L1_WALLET_ADDRESS = os.environ.get("L1_WALLET_ADDRESS")
    exchange = Lighter.Lighter({
            'walletAddress': L1_WALLET_ADDRESS,
            "apiKey": WALLET,
            "secret": PRIVATE_KEY,
            "apiKeyIndex": API_KEY_INDEX,
            'options': {'account_id': ACCOUNT_ID},
        })

```
