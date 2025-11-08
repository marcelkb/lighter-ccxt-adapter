import logging
import os

from dotenv import load_dotenv
from const import EOrderSide, EOrderType
from Lighter import Lighter

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def main():
    load_dotenv()
    ACCOUNT_ID = int(os.environ.get("ACCOUNT_ID"))
    PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
    WALLET = os.environ.get("WALLET")
    API_KEY_INDEX = int(os.environ.get("API_KEY_INDEX"))

    L1_WALLET_ADDRESS = os.environ.get("L1_WALLET_ADDRESS")

    exchange = Lighter({
        'walletAddress': L1_WALLET_ADDRESS,
        "apiKey": WALLET,
        "secret": PRIVATE_KEY,
        "apiKeyIndex": API_KEY_INDEX,
        'options': {'account_id': ACCOUNT_ID},
    })
    symbol = 'ETH/USDC:USDC'  # market symbol

    ORDER_PLACEMENT = True
    AMOUNT = 0.01

    # load markets
    exchange.load_markets()

    # Fetch the latest ticker for the symbol
    ticker = exchange.fetch_ticker(symbol)
    print(f"{symbol} price: {ticker['last']}")

    # Fetch accounts
    print("Fetching accounts")
    print(exchange.fetch_accounts())

    orders = exchange.fetch_open_orders(symbol)
    # for order in orders:
    #     print(f"cancel order {order['id']} for {order['symbol']}")
    #     exchange.cancel_order(order["id"])
    funding = exchange.fetch_funding_rate(symbol)
    print(
        f"funding rate for {funding['symbol']}: {funding['info']['fundingRate']}@{funding['interval']} , lastTime: {funding['fundingDatetime']}, yearly: {funding['info']['fundingRateAnnualized'] * 100}%")
    funding = exchange.fetch_funding_rate("BTC/USDC:USDC")
    print(
        f"funding rate for {funding['info']['symbol']}: {funding['info']['fundingRate']}@{funding['interval']} , lastTime: {funding['fundingDatetime']}, yearly: {funding['info']['fundingRateAnnualized'] * 100}%")
    position = exchange.fetch_position(symbol)
    if position is not None:
        print(
            f"position: {position['info']['unrealisedPnl']} {position['info']['curRealisedPnl']} {position['info']['size']}")
    else:
        print(f"position: None")
    print(f"open orders:  {exchange.fetch_open_orders(symbol)}")
    print(f"all orders:  {exchange.fetch_orders(symbol)}")
    print(f"single order:  {exchange.fetch_order(order_id='562949453226153', symbol=symbol)}")

    # Fetch balance
    print("Fetching account balance")
    print(exchange.fetch_balance())

    print("Fetching ohclv")
    print(exchange.fetch_ohlcv(symbol, timeframe='1h', limit=10))

    print("fetch leverage")
    print(exchange.fetch_leverage(symbol=symbol))

    # Loop through each open order and cancel it
    for order in orders:
        print(f"Canceling order {order['id']} for {order['symbol']}")
        print(exchange.cancel_order(order["id"]))

    exchange.set_leverage(3, symbol=symbol)

    if ORDER_PLACEMENT:
        # Create a new limit order
        print(f"Creating LIMIT BUY order for {symbol}")
        print(exchange.create_order(symbol, EOrderType.LIMIT.value, EOrderSide.BUY.value, AMOUNT, ticker['last'] * 0.5))

    # Fetch currencies
    print("Fetching currencies")
    print(exchange.fetch_currencies())

    # Fetch markets
    print("Fetching markets")
    print(exchange.fetch_markets())

    # if ORDER_PLACEMENT:
    #     # Create another limit order
    #     print(f"Creating another LIMIT BUY order for {symbol}")
    #     result = exchange.create_order(symbol, EOrderType.LIMIT.value, EOrderSide.BUY.value, AMOUNT,
    #                                    ticker['last'] * 0.5)

    # if ORDER_PLACEMENT:
    #     # Fetch details of created order
    #     print(f"Fetching details for order {result['id']}")
    #     print(exchange.fetch_order(result['id'], symbol))

    # Cancel all fetched orders
    # print(f"Canceling all fetched orders for {symbol}")
    # orders = exchange.fetch_orders(symbol)
    # for order in orders:
    #     print(f"Canceling order {order['id']}")
    #     result = exchange.cancel_order(order["id"])
    #     print(result)

    # # Fetch trade history
    print(f"Fetching trades for {symbol}")
    print(exchange.fetch_trades(symbol))

    # # Fetch my trades
    print("Fetching my trades")
    print(exchange.fetch_my_trades())

    # Set leverage
    # print(f"Setting leverage for {symbol}")
    # print(exchange.set_leverage(symbol))

    if ORDER_PLACEMENT:
        # Create market and limit orders
        print(f"Creating MARKET BUY order for {symbol}")
        print(exchange.create_market_order(symbol, EOrderSide.BUY.value, AMOUNT, ticker['last'] * 1.01))

        print(f"Creating LIMIT BUY order for {symbol}")
        print(exchange.create_limit_order(symbol, EOrderSide.BUY.value, AMOUNT, ticker['last'] * 0.5))

    # Fetch leverage
    # print(f"Fetching leverage for {symbol}")
    # print(exchange.fetch_leverage(symbol))

    if ORDER_PLACEMENT:
        # Create TP order
        print(f"Creating TAKE PROFIT MARKET SELL order for {symbol}")
        print(exchange.create_order(
            symbol,
            EOrderType.MARKET.value,
            EOrderSide.SELL.value,
            AMOUNT,
            ticker['last'] * 1.01,
            params={'takeProfitPrice': '250', 'reduceOnly': True}
        ))

        # Create SL order
        print(f"Creating STOP LOSS MARKET SELL order for {symbol}")
        print(exchange.create_order(
            symbol,
            EOrderType.MARKET.value,
            EOrderSide.SELL.value,
            AMOUNT,
            ticker['last'] * 1.01,
            params={'stopLossPrice': '100', 'reduceOnly': True}
        ))

    exchange.close()


if __name__ == '__main__':
    main()
