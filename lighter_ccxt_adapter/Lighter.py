import asyncio
import math
import random
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from decimal import Decimal
from enum import Enum

import ccxt
from ccxt import InvalidOrder, BadRequest, AuthenticationError, OrderNotFound
from ccxt.base.types import Strings, Int, Str, Num, OrderSide, OrderType, Balances, Market, Ticker, OrderBook, Trade, \
    Order, Position, FundingRate

import lighter  # official SDK
from lighter import OrderBooks, OrderBookDetails, OrderBookDetails, Orders, AccountPosition, DetailedAccount, \
    Candlestick, Candlesticks, Trades, SubAccounts, DetailedAccounts, PerpsOrderBookDetail

from lighter_ccxt_adapter.abstract.lighter import ImplicitAPI
from lighter_ccxt_adapter.const import EOrderSide, EOrderType, EOrderStatus


# ---------- small async helper ----------
def run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop = asyncio.get_event_loop()

    if loop.is_running():
        # if already in an async env, use nest_asyncio
        try:
            import nest_asyncio
            nest_asyncio.apply()
        except Exception:
            pass
        return loop.run_until_complete(coro)
    else:
        return loop.run_until_complete(coro)


class Lighter(ccxt.Exchange, ImplicitAPI):
    base_url = "https://mainnet.zklighter.elliot.ai"

    def __init__(self, config: Dict[str, Any] = {}):
        super().__init__(config)

        self.account_id = self.safe_value(self.options, "account_id", 0)
        self.api_key = self.safe_string(config, 'apiKey', self.apiKey)
        self.private_key = self.safe_string(config, 'secret', self.secret)
        self.api_key_index = self.safe_integer(config, 'apiKeyIndex', 0)

        # SDK clients
        configuration = lighter.Configuration(
            host=self.base_url,
            api_key=self.api_key
        )
        self.api_client = lighter.ApiClient(configuration)
        self.signer_client = None
        if self.private_key:
            self.signer_client = lighter.SignerClient(
                url=self.base_url,
                account_index=self.account_id,
                api_private_keys={self.api_key_index: self.private_key},
            )

        # HTTP APIs exposed by the SDK
        self.root_api = lighter.RootApi(self.api_client)
        self.account_api = lighter.AccountApi(self.api_client)
        self.order_api = lighter.OrderApi(self.api_client)
        self.candlestick_api = lighter.CandlestickApi(self.api_client)
        self.block_api = lighter.BlockApi(self.api_client)
        self.transaction_api = lighter.TransactionApi(self.api_client)

        # caches
        self.markets_cache = None
        self.markets_by_id: Dict[str, Dict[str, Any]] = {}

        # capability flags
        self.has.update({
            "spot": False,
            "margin": False,
            "swap": True,
            "future": False,
            "option": False,
            "fetchMarkets": True,
            "fetchCurrencies": False,
            "fetchTicker": True,
            "fetchTickers": True,
            "fetchOrderBook": True,
            "fetchOHLCV": True,
            "fetchBalance": True,
            "fetchTrades": True,
            "fetchMyTrades": True,  # via account trades endpoint if present, else NotSupported
            "createOrder": True,
            "cancelOrder": True,
            "cancelAllOrders": True,
            "fetchOrder": True,
            "fetchOrders": True,  # closed/history
            "fetchOpenOrders": False,  # suggest WS-based cache for true open orders
            "fetchClosedOrders": True,
            "fetchPositions": True,
            "fetchPosition": True,
            "setLeverage": False,
            "fetchLeverage": False,
            "setMarginMode": False,
            "fetchFundingRate": True,  # via candlesticks/funding series (public)
            "fetchFundingRates": True,
            "fetchFundingHistory": False,  # account funding history not via public HTTP list
            "addMargin": False,
            "reduceMargin": False,
        })

        self.urls.update({
            "api": {
                "public": self.base_url,
                "private": self.base_url,
            },
            "www": "https://lighter.xyz",
            "doc": "https://github.com/elliottech/lighter-python/tree/main/docs",
        })

        # default options
        self.options = self.deep_extend({
            "account_id": 0,
            "defaultType": "swap",
            "createMarketBuyOrderRequiresPrice": False,
        }, self.options)

        self.fees.update({
            'swap': {
                'taker': self.parse_number('0.0002'),
                'maker': self.parse_number('0.0002'),
            },
            'spot': {
                'taker': self.parse_number('0.0002'),
                'maker': self.parse_number('0.0002'),
            },
        })

        self.name = "Lighter"
        self.rateLimit = 1000

    # -------------- helpers --------------
    def precision_from_string(self, precision_str: str) -> int:
        if not precision_str:
            return 0
        if '.' in str(precision_str):
            return len(str(precision_str).split('.')[1])
        return 0

    def lighter_to_ccxt_symbol(self, lighter_symbol: str) -> str:
        # BTC_USDC -> BTC/USDC:USDC
        if '_' in lighter_symbol:
            base, quote = lighter_symbol.split('_', 1)
            return f"{base}/{quote}:{quote}"
        return lighter_symbol

    def market_id_to_symbol(self, marketId):
        symbol = None
        if marketId is not None:
            for market in self.markets.values():
                if market["id"] == marketId:
                    return market["symbol"]
        return None

    def symbol_to_market_id(self, symbol):
        if symbol is not None:
            market = self.markets.get(symbol)
            if market is None:
                raise ccxt.ExchangeError(f"{self.id} symbol {symbol} not found in markets")
            market_id = market.get('id')
            return market_id

    def ccxt_to_base(self, symbol):
        if '/' in symbol:
            base, rest = symbol.split('/', 1)
            quote = rest.split(':', 1)[0]
            return f"{base}"
        return symbol

    def ccxt_to_lighter_symbol(self, symbol: str) -> str:
        # BTC/USDC or BTC/USDC:USDC -> BTC_USDC
        if '/' in symbol:
            base, rest = symbol.split('/', 1)
            quote = rest.split(':', 1)[0]
            return f"{base}_{quote}"
        return symbol

    def safe_number(self, obj: Any, key: str, default=None):
        v = self.safe_value(obj, key)
        if v is None:
            return default
        try:
            return float(v)
        except Exception:
            try:
                return float(Decimal(str(v)))
            except Exception:
                return default

    # -------------- markets --------------
    def fetch_markets(self, params={}) -> List[Market]:
        """
        Discover markets via RootApi.info() and map to CCXT format.
        """
        info = run(self.order_api.order_books())
        # The SDK’s info() returns a structure with markets. Two patterns supported:
        # 1) {"markets": [...]} or 2) directly a list. We handle both.
        markets_raw = info.get("markets") if isinstance(info, dict) else info
        if markets_raw is None:
            # some SDKs embed within "data"
            markets_raw: OrderBooks = self.safe_value(info, "data", {}).get("markets") if isinstance(info, dict) else []

        result = []
        for m in markets_raw.order_books or []:
            # expected fields: symbol, base_token, quote_token, price_tick, size_tick, status, min/max sizes
            m: lighter.models.order_books = m
            market_id = m.market_id
            base = m.symbol
            quote = "USDC"
            symbol = f"{base}/{quote}:{quote}"
            price_precision = m.supported_price_decimals
            amount_precision = m.supported_size_decimals

            entry = {
                'id': market_id,
                'symbol': symbol,
                'base': base,
                'quote': quote,
                'settle': quote,
                'baseId': base,
                'quoteId': quote,
                'settleId': quote,
                'type': 'swap',
                'spot': False,
                'margin': False,
                'swap': True,
                'future': False,
                'option': False,
                'active': (self.safe_string(m, 'status', 'active') == 'active'),
                'contract': True,
                'linear': True,
                'inverse': False,
                'contractSize': 1,
                'expiry': None,
                'expiryDatetime': None,
                'strike': None,
                'optionType': None,
                'precision': {
                    'amount': amount_precision,
                    'price': price_precision,
                },
                'limits': {
                    'amount': {
                        'min': m.min_base_amount,
                        'max': self.safe_number(m, 'max_order_size'),
                    },
                    'price': {
                        'min': None,
                        'max': None,
                    },
                    'cost': {
                        'min': self.parse_number('10'),
                        'max': None,
                    },
                },
                'info': m,
            }
            result.append(entry)

        self.markets_cache = {mk['symbol']: mk for mk in result}
        self.markets_by_id = {mk['id']: mk for mk in result if mk.get('id')}
        return result

    def fetch_ticker(self, symbol: str, params={}) -> Ticker:
        self.load_markets()
        id = self.symbol_to_market_id(symbol)
        details: OrderBookDetails = run(self.order_api.order_book_details(market_id=id))
        detail: PerpsOrderBookDetail = details.order_book_details[0]
        detail: PerpsOrderBookDetail = detail
        price = detail.last_trade_price

        ts = self.milliseconds()
        return {
            'symbol': symbol,
            'timestamp': ts,
            'datetime': self.iso8601(ts),
            'high': detail.daily_price_high,
            'low': detail.daily_price_low,
            'bid': price,
            'bidVolume': None,
            'ask': price,
            'askVolume': None,
            'last': price,
            'average': None,
            'baseVolume': detail.daily_base_token_volume,
            'quoteVolume': detail.daily_quote_token_volume,
            'info': detail,
        }

    def fetch_tickers(self, symbols: List[str] = None, params={}) -> Dict[str, Ticker]:
        self.load_markets()
        # If SDK supports multi symbol stats, call it. Else loop.
        result: Dict[str, Ticker] = {}
        if symbols:
            for s in symbols:
                result[s] = self.fetch_ticker(s, params)
        else:
            # fall back to all markets
            for s in self.markets:
                result[s] = self.fetch_ticker(s, params)
        return result

    def fetch_trades(self, symbol: str, since: Int = None, limit: Int = None, params={}) -> List[Trade]:
        self.load_markets()
        market_id = self.symbol_to_market_id(symbol)
        if limit is None:
            limit = 100
        auth_token, _ = self.signer_client.create_auth_token_with_expiry()
        resp: Trades = run(
            self.order_api.trades(sort_by="timestamp", market_id=market_id, account_index=self.account_id, limit=limit,
                                  authorization=auth_token))
        out = []
        for trade in resp.trades:  # type Trade
            out.append({
                'id': trade.trade_id,
                'order': None,
                'timestamp': trade.timestamp,
                'datetime': self.iso8601(trade.timestamp),
                'symbol': symbol,
                'type': None,
                'side': EOrderSide.SELL if trade.is_maker_ask else EOrderSide.BUY,
                'takerOrMaker': None,
                'price': trade.price,
                'amount': trade.size,
                'cost': trade.usd_amount,
                'fee': trade.maker_fee,
                'info': trade.to_dict(),
            })
        return out

    def fetch_my_trades(self, symbol: Str = None, since: Int = None, limit: Int = None, params={}):
        if symbol is None:
            symbol = "ETH/USDC:USDC"  # default TODO
        return self.fetch_trades(symbol, since, limit, params)

    def fetch_ohlcv(self, symbol: str, timeframe='1m', since: Int = None, limit: Int = None, params={}) -> List[
        List[Num]]:
        self.load_markets()
        market_id = self.markets[symbol]["id"]
        interval = timeframe
        if interval == "2h":
            interval = "1h"  # fallback to next detailed timeframe
        end_timestamp = int(datetime.now().timestamp())
        if since is None:
            since = self._calculate_since(timeframe, limit, end_timestamp)
        if limit is None:
            limit = 1000
        resp: Candlesticks = run(
            self.candlestick_api.candlesticks(market_id=market_id, resolution=interval, end_timestamp=end_timestamp,
                                              start_timestamp=since, count_back=limit))
        out = []
        for c in resp.candlesticks:  # type: Candlestick
            out.append([
                c.timestamp,
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume1
            ])
        return out

    def _calculate_since(
            self,
            timeframe: str,
            limit: Optional[int],
            end_timestamp: int
    ) -> int:
        DEFAULT_LIMIT = 100
        limit = limit or DEFAULT_LIMIT

        # Aktuelle Zeit
        now = datetime.fromtimestamp(end_timestamp)

        # Timeframe-Mapping zu timedelta
        timeframe_deltas = {
            '1m': timedelta(minutes=1),
            '3m': timedelta(minutes=3),
            '5m': timedelta(minutes=5),
            '15m': timedelta(minutes=15),
            '30m': timedelta(minutes=30),
            '1h': timedelta(hours=1),
            '2h': timedelta(hours=2),
            '4h': timedelta(hours=4),
            '6h': timedelta(hours=6),
            '8h': timedelta(hours=8),
            '12h': timedelta(hours=12),
            '1d': timedelta(days=1),
            '3d': timedelta(days=3),
            '1w': timedelta(weeks=1),
            '1M': timedelta(days=30),  # Näherung
        }

        delta = timeframe_deltas[timeframe]

        # Berechne Startzeit
        start_time = now - (delta * limit)

        return int(start_time.timestamp())

    # -------------- account --------------
    def fetch_balance(self, params={}) -> Balances:
        self.load_markets()
        acct = run(self.account_api.account(by="index", value=str(self.account_id)))
        detailedAccount: DetailedAccount = acct.accounts[0]
        available = detailedAccount.available_balance
        collateral = detailedAccount.collateral
        totalAssetValue = detailedAccount.total_asset_value

        code = "USDC"
        account = self.account()
        account['free'] = available
        account['used'] = str(float(collateral) - float(available))
        account['total'] = collateral
        result = {'info': detailedAccount.to_dict()}
        result[code] = account
        return self.safe_balance(result)

    def fetch_positions(self, symbols: Strings = None, params={}) -> List[Position]:
        self.load_markets()
        acct = run(self.account_api.account(by="index", value=str(self.account_id)))
        detailedAccount = acct.accounts[0]
        parsed = [self._parse_position(p) for p in detailedAccount.positions]
        if symbols:
            parsed = [p for p in parsed if p['symbol'] in symbols]
        return parsed

    def fetch_position(self, symbol: str, params={}) -> Position:
        poss = self.fetch_positions(params=params)
        for p in poss:
            if p['symbol'] == symbol:
                return p
        return None

    def _parse_position(self, position: AccountPosition) -> Dict[str, Any]:
        market_id = position.market_id
        symbol = position.symbol + "/USDC:USDC"
        ts = self.milliseconds()
        if position.sign == 1:
            side = EOrderSide.BUY
        else:
            side = EOrderSide.SELL
        size = position.position
        entry_price = position.avg_entry_price
        mark_price = -1
        notional = position.position_value
        leverage = 1  # TODO
        unreal = position.unrealized_pnl
        realized = position.realized_pnl
        liq = position.liquidation_price
        total_funding_payout = position.total_funding_paid_out
        margin_mode = 'cross' if position.margin_mode == 0 else "isolated"
        # percentage PnL
        pct = None
        if entry_price and mark_price and size:
            try:
                pct = ((mark_price - entry_price) / entry_price) * 100.0 * (1 if side == 'long' else -1)
            except Exception:
                pct = None

        additionalInfo = {}
        additionalInfo['unrealisedPnl'] = unreal
        additionalInfo['curRealisedPnl'] = realized
        additionalInfo['size'] = size
        additionalInfo['total_funding_payout'] = total_funding_payout

        return {
            'info': self.extend(position.to_dict(), additionalInfo),
            'id': 0,
            'symbol': symbol,
            'timestamp': ts,
            'datetime': self.iso8601(ts) if ts else None,
            'hedged': False,
            'side': side,
            'contracts': size,
            'contractSize': 1,
            'entryPrice': entry_price,
            'markPrice': mark_price,
            'notional': notional,
            'leverage': leverage,
            'collateral': 0,
            'initialMargin': 0,
            'initialMarginPercentage': (1 / leverage * 100) if leverage else None,
            'maintenanceMargin': 0,
            'maintenanceMarginPercentage': 0,
            'unrealizedPnl': unreal,
            'realizedPnl': realized,
            'liquidationPrice': liq,
            'marginMode': margin_mode,
            'marginRatio': 0,
            'percentage': pct,
        }

    def create_order(self, symbol: str, type: str, side: str, amount: float, price: Optional[float] = None,
                     params: Optional[Dict] = None) -> Order:
        if self.signer_client is None:
            raise AuthenticationError("Private key required to create orders on Lighter")
        self.load_markets()
        market_id = self.markets[symbol]["id"]
        client_order_index = random.randint(1000000, 9999999)

        data = self.markets_by_id[market_id]
        if isinstance(data, list):
            precision_amount = int(self.markets_by_id[market_id][0]["precision"]["amount"])
            precision_price = int(self.markets_by_id[market_id][0]["precision"]["price"])
        else:
            precision_amount = int(self.markets_by_id[market_id]["precision"]["amount"])
            precision_price = int(self.markets_by_id[market_id]["precision"]["price"])
        if params == {}:
            if precision_price == 1 and side == EOrderSide.SELL.value:
                precision_price = 0  # by precision 1 no multiplicator but only for sell?

        tx = None
        err = None

        if price is None:
            raise RuntimeError("price needed, also for market orders to apply slippage protection")

        # Read reduceOnly from params (accept both spellings). Used both for
        # the signer call below and for the response dict's `reduceOnly`
        # field — previously the response always reported True when params
        # was None, which lied to callers about plain entries.
        reduce_only = False
        if params:
            reduce_only = bool(params.get("reduceOnly") or params.get("reduce_only"))

        if params is not None and params != {}:
            if "takeProfitPrice" in params:
                takeProfitPrice = params['takeProfitPrice']
                # SL/TP triggers must be reduce-only — without it, a fill that
                # exceeds the remaining position by even a single base-unit
                # (rounding / matching-engine quirk) flips the position into
                # the opposite direction and leaves dust the caller can't
                # reconcile. signer default is reduce_only=False, so force it.
                tx, tx_hash, err = run(self.signer_client.create_tp_order(
                    market_index=market_id,
                    client_order_index=client_order_index,
                    base_amount=int(float(amount) * 10 ** precision_amount),
                    price=int(float(price) * 10 ** precision_price),
                    is_ask=side == EOrderSide.SELL,
                    trigger_price=int(float(takeProfitPrice) * 10 ** precision_price),
                    reduce_only=True,
                ))
                reduce_only = True
            elif "stopLossPrice" in params:
                stopLossPrice = params['stopLossPrice']
                tx, tx_hash, err = run(self.signer_client.create_sl_order(
                    market_index=market_id,
                    client_order_index=client_order_index,
                    base_amount=int(float(amount) * 10 ** precision_amount),
                    price=int(float(price) * 10 ** precision_price),
                    is_ask=side == EOrderSide.SELL,
                    trigger_price=int(float(stopLossPrice) * 10 ** precision_price),
                    reduce_only=True,
                ))
                reduce_only = True
            # Was `elif "reduceOnly" or "reduce_only" in params:` — Python
            # operator precedence parses that as `("reduceOnly") or
            # ("reduce_only" in params)`, which is always True. Fixed and
            # generalised: any non-TP/SL order goes through the limit/market
            # path with the resolved reduce_only flag.
            else:
                tx, tx_hash, err = self._create_limit_order(
                    amount, client_order_index, market_id, price, side, type,
                    reduce_only=reduce_only,
                )
        else:
            tx, tx_hash, err = self._create_limit_order(
                amount, client_order_index, market_id, price, side, type,
                reduce_only=reduce_only,
            )

        if err is not None:
            print(f"error occured: {err}")
            raise InvalidOrder(self.id + ' ' + str(err))

        id = None
        status = "open"
        info = {}
        if tx is not None:
            id = tx.order_book_index

            # Lighter accepts the signed tx synchronously but may not yet
            # have assigned an order_book_index by the time we get the
            # receipt. The order IS on the book — just un-indexed. Without
            # an id the caller can't poll fill status or cancel the order,
            # so a slow-filling limit becomes a zombie: it rests until the
            # market eventually crosses the price (sometimes many minutes
            # later), fills, and leaves the caller with an untracked
            # position. Poll fetch_open_orders for our own
            # client_order_index for a few seconds so we surface the real
            # id as soon as indexing catches up.
            #
            # ONLY for plain limit entries. Triggered orders (stopLossPrice
            # / takeProfitPrice) have a slower indexing path on Lighter
            # that frequently exceeds the 7s poll budget — the wait blocks
            # the caller's main loop without producing an id, then the
            # bot places a duplicate on the next reconcile cycle. The bot
            # adopts unmatched reduce-only stops/TPs on its own reconcile
            # path (see live_engine.LiveEngine.reconcile's SL/TP integrity
            # checks), so returning id=None immediately for triggered
            # orders is the lower-latency, duplicate-free path. Market
            # orders fill instantly and don't rest in open_orders at all,
            # so polling for them is meaningless (handled below by the
            # FilledOrderPlaceholderId fallback).
            is_triggered = bool(
                params and (
                    params.get("takeProfitPrice")
                    or params.get("stopLossPrice")
                )
            )
            if (
                id is None
                and type == EOrderType.LIMIT.value
                and not is_triggered
            ):
                for wait_s in (0.5, 0.5, 1.0, 1.0, 2.0, 2.0):  # ~7s total
                    time.sleep(wait_s)
                    try:
                        resting = self.fetch_open_orders(symbol=symbol)
                    except Exception:
                        resting = []
                    for o in resting:
                        coid = o.get("clientOrderId")
                        if coid is None:
                            continue
                        # SDK may surface clientOrderId as int or str
                        # depending on version — coerce both sides.
                        try:
                            if int(coid) == int(client_order_index):
                                id = o.get("id")
                                status = o.get("status") or "open"
                                break
                        except (TypeError, ValueError):
                            continue
                    if id is not None:
                        break

            if id is not None:
                order = self.fetch_order(order_id=id, symbol=symbol)
                status = order["status"]
            else:
                if type == EOrderType.MARKET.value:
                    id = "FilledOrderPlaceholderId"
                    status = "closed"
                else:
                    status = "open"
            info = tx.__dict__
            info["origQty"] = amount

        return self.safe_order({  # TODO values
            'info': info,
            'id': id,
            'order': id,
            'clientOrderId': id,
            'timestamp': self.iso8601(int(time.time() * 1000)),
            'datetime': self.iso8601(int(time.time() * 1000)),
            'symbol': symbol,
            'type': type,
            'timeInForce': False,
            'postOnly': True,
            'reduceOnly': reduce_only,
            'side': side,
            'price': price,
            'triggerPrice': price,
            'takeProfitPrice': None,
            'stopLossPrice': None,  # TODO exists?
            'amount': amount,
            'cost': None,
            'average': None,
            'filled': None,
            'remaining': None,
            'status': EOrderStatus.valueOf(status.lower()),
            'fee':
                {
                    'cost': 0,
                    'currency': 'USDC',
                    'rate': 0.004
                },
            'trades': []})

    def _create_limit_order(self, amount: float, client_order_index: int, market_id: int, price: float | None,
                            side, type, reduce_only: bool = False):
        data = self.markets_by_id[market_id]
        if isinstance(data, list):
            precision_amount = int(self.markets_by_id[market_id][0]["precision"]["amount"])
            precision_price = int(self.markets_by_id[market_id][0]["precision"]["price"])
        else:
            precision_amount = int(self.markets_by_id[market_id]["precision"]["amount"])
            precision_price = int(self.markets_by_id[market_id]["precision"]["price"])
        if precision_price == 1 and side == EOrderSide.SELL.value:
            precision_price = 0 # by precision 1 no multiplicator but only for sell, esp. btc case?

        if type == EOrderType.LIMIT.value:
            return run(self.signer_client.create_order(
                market_index=market_id,
                client_order_index=client_order_index,
                base_amount=int(float(amount) * 10**precision_amount),
                price=int(float(price) * 10**precision_price),
                is_ask=side == EOrderSide.SELL.value,
                order_type=lighter.SignerClient.ORDER_TYPE_LIMIT,
                time_in_force=lighter.SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                reduce_only=reduce_only,
                trigger_price=0,
            ))
        else:
            slippage = 1.01 if side == EOrderSide.BUY.value else 0.99
            return run(self.signer_client.create_market_order(
                market_index=market_id,
                client_order_index=client_order_index,
                base_amount=int(float(amount) * 10**precision_amount),
                avg_execution_price=int(float(price) * 10**precision_price * slippage), # worst acceptable price for the order
                is_ask=side == EOrderSide.SELL.value,
                reduce_only=reduce_only,
            ))

    def cancel_order(self, id: str, symbol: str = None, params={}) -> Order:
        if self.signer_client is None:
            raise AuthenticationError("Private key required to cancel orders on Lighter")
        self.load_markets()
        if symbol is None:
            market_id = 0  # todo fallback?
        else:
            market_id = int(self.markets[symbol]["id"])
        if market_id is None:
            market_id = 0  # fallback  TODO
        canceledOrder, tx_hash, err = run(self.signer_client.cancel_order(market_index=market_id, order_index=int(id)))
        if err is not None:
            raise InvalidOrder(err)
        return canceledOrder is not None and tx_hash is not None and err is None

    def cancel_all_orders(self, symbol: str = None, params={}) -> List[Order]:
        open_orders = self.fetch_open_orders(symbol=symbol)
        for order in open_orders:
            self.cancel_order(order['id'], order['symbol'], params)

    def fetch_order(self, order_id, symbol=None, params=None):
        orders = self.fetch_orders(symbol=symbol, params=params)
        for order in orders:
            if order["id"] == order_id:
                return order

    # def _parse_order(self, resp):
    #     return {
    #         "id": resp.id,
    #         "symbol": resp.symbol,
    #         "status": resp.status,
    #         "type": resp.type,
    #         "side": resp.side,
    #         "price": float(resp.price or 0),
    #         "amount": float(resp.quantity or 0),
    #         "filled": float(resp.filled or 0),
    #         "remaining": float(resp.remaining or 0),
    #         "info": resp.to_dict(),
    #     }

    def fetch_orders(self, symbol: str = None, since: Int = None, limit: Int = None, params={}) -> List[Order]:
        open_orders = self.fetch_open_orders(symbol, since, limit, params)
        closed_orders = self.fetch_closed_orders(symbol, since, limit, params)
        return open_orders + closed_orders

    def fetch_open_orders(self, symbol: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = None,
                          params: Optional[Dict] = None) -> List[Dict]:
        self.load_markets()
        auth_token, _ = self.signer_client.create_auth_token_with_expiry()
        orders: Orders = run(self.order_api.account_active_orders(account_index=self.account_id,
                                                                  market_id=self.markets[symbol]["id"],
                                                                  authorization=auth_token, auth=auth_token))
        out = []
        for o in orders.orders:  # type Orders
            out.append(self._parse_order(o))
        return out

    def fetch_closed_orders(self, symbol: str = None, since: Int = None, limit: Int = None, params={}) -> List[Order]:
        self.load_markets()
        auth_token, _ = self.signer_client.create_auth_token_with_expiry()
        orders: Orders = run(self.order_api.account_inactive_orders(account_index=self.account_id, limit=100,
                                                                    market_id=self.markets[symbol]["id"],
                                                                    authorization=auth_token, auth=auth_token))
        out = []
        for o in orders.orders:  # type Orders
            out.append(self._parse_order(o))
        return out

    # -------------- parsers --------------
    def parse_order_status(self, status: str) -> str:
        # Output values MUST be members of EOrderStatus (see const.py) —
        # the caller in create_order feeds the result into
        # EOrderStatus.valueOf(...) which raises on unknown strings. Lighter
        # reports several states the original mapping didn't cover
        # (notably 'pending' on resting trigger orders like SL/TP),
        # which used to crash the entire create_order call after the
        # signed tx had already been accepted by the venue — leaving the
        # bot with a placed-but-untracked protective order.
        mapping = {
            'open': 'open',
            'partially_filled': 'partially-filled',
            'partially-filled': 'partially-filled',
            'filled': 'closed',        # ccxt convention: filled order → 'closed'
            'closed': 'closed',
            'canceled': 'canceled',
            'cancelled': 'canceled',
            'expired': 'canceled',
            'rejected': 'rejected',
            'triggered': 'open',
            'untriggered': 'open',
            'pending': 'open',         # awaiting trigger / matching — still on book
            'in-progress': 'open',
            'in_progress': 'open',
            'reduceOnlyCanceled': 'canceled',
        }
        # Default to 'open' for any unknown state so the caller doesn't
        # crash on EOrderStatus lookup. Safer than the previous identity
        # default which guaranteed a downstream ValueError.
        return mapping.get((status or '').lower(), 'open')

    def _parse_order(self, order: lighter.models.Order) -> Order:
        # Normalize a variety of shapes: tx receipts, order DTOs, etc.
        oid = order.order_id
        market_id = order.market_index
        symbol = self.market_id_to_symbol(order.market_index)
        ts = order.timestamp
        side = order.side
        if side == "" and not order.is_ask:
            side = EOrderSide.BUY.value
        type_ = order.type
        # `base_price` is a scaled int (units of 10**-price_decimals) and
        # `*_base_amount` are scaled strings (units of 10**-size_decimals).
        # Sending side scales UP (line ~547); receive side must scale DOWN,
        # otherwise downstream consumers see notional inflated by
        # 10**(price_decimals + size_decimals) and any fee/PnL math derived
        # from `cost` is wildly off.
        market = self.markets_by_id.get(market_id)
        if isinstance(market, list):
            market = market[0]
        try:
            price_dec = int(market["precision"]["price"]) if market else 0
            size_dec = int(market["precision"]["amount"]) if market else 0
        except (KeyError, TypeError, ValueError):
            price_dec = 0
            size_dec = 0
        price_scale = 10 ** price_dec
        size_scale = 10 ** size_dec
        price = order.price
        try:
            avg = float(order.base_price) / price_scale if order.base_price else None
        except (TypeError, ValueError):
            avg = None
        try:
            amount = float(order.initial_base_amount) / size_scale
        except (TypeError, ValueError):
            amount = 0.0
        try:
            filled = float(order.filled_base_amount) / size_scale
        except (TypeError, ValueError):
            filled = 0.0
        remaining = None
        if amount is not None and filled is not None:
            remaining = max(0.0, amount - filled)
        status = self.parse_order_status(order.status)
        client_oid = order.client_order_id
        fee = None
        if 'fee' in order:
            f = order.fee
            fee = {'cost': self.safe_number(f, 'cost'), 'currency': self.safe_string(f, 'currency')}

        # trigger_price's on-wire scaling is INCONSISTENT across markets: some
        # come back as a scaled int (units 10**-price_decimals, like base_price
        # — e.g. 82709 for a SOL stop), others already as a plain decimal string
        # (e.g. "0.08197" for a DOGE stop). Blindly dividing by price_scale is
        # wrong for the decimal ones: it under-scales them by ~10**price_dec
        # (observed live: a 0.085 DOGE stop parsed as 8.5e-08), which makes the
        # engine's adoption matcher (_find_matching_reduce_stop_id) reject our
        # own resting SL/TP. Every cycle then re-places it as a duplicate until
        # Lighter's per-market order cap (21720) is hit and the position is
        # reported UNPROTECTED. Disambiguate by picking whichever interpretation
        # is closest to this order's own limit `price` (the SL/TP limit bound
        # always sits within slippage of the trigger), so both wire formats and
        # any per-market scale resolve correctly without symbol-specific guesses.
        additional = {}
        trig_scaled = None
        if order.trigger_price is not None:
            try:
                raw_trig = float(order.trigger_price)
            except (TypeError, ValueError):
                raw_trig = None
            if raw_trig is not None:
                candidates = [raw_trig]
                if price_scale and price_scale != 1:
                    candidates.append(raw_trig / price_scale)
                try:
                    ref_px = float(price) if price not in (None, "", 0) else None
                except (TypeError, ValueError):
                    ref_px = None
                if ref_px and ref_px > 0:
                    trig_scaled = min(candidates, key=lambda c: abs(c - ref_px))
                else:
                    # No usable reference price (pure trigger, no limit bound):
                    # fall back to the scaled interpretation, matching the
                    # historical scaled-int behaviour.
                    trig_scaled = raw_trig / price_scale if price_scale else raw_trig
        # Orders placed with BOTH a trigger and a limit bound (our SL/TP go
        # through create_sl_order / create_tp_order with a `price`) come back
        # typed as "stop-loss-limit" / "take-profit-limit", NOT the plain
        # hyphen form. Handle both, otherwise the typed trigger key is never
        # set and the engine's adoption matcher falls back to the RAW (scaled)
        # info.trigger_price — a ~1000x mismatch that makes it re-place the
        # SL/TP every cycle until the venue's per-market order cap (21720) is
        # hit and the position is reported UNPROTECTED.
        if order.type in ("take-profit", "take-profit-limit"):
            additional["takeProfitPrice"] = trig_scaled
        elif order.type in ("stop-loss", "stop-loss-limit"):
            additional["stopLossPrice"] = trig_scaled

        return self.extend({
            'id': oid,
            'reduceOnly': order.reduce_only,
            'clientOrderId': client_oid,
            'timestamp': ts,
            'datetime': self.iso8601(ts),
            'lastTradeTimestamp': None,
            'status': status,
            'symbol': symbol,
            'type': type_,
            'timeInForce': order.time_in_force,
            'postOnly': True,
            'side': side,
            'price': price,
            'average': avg,
            'amount': amount,
            'filled': filled,
            'remaining': remaining,
            'cost': (avg or price) * filled if (filled is not None and (avg or price)) else None,
            'trades': None,
            'fee': fee,
            'info': order.to_dict(),
        }, additional)

    def set_margin_mode(self, marginMode: str, symbol: Str = None, params={}):
        self.load_markets()
        leverage = self.safe_integer(params, 'leverage', 3)
        if marginMode == "isolated":
            run(self.signer_client.update_leverage(market_index=self.markets[symbol]["id"], leverage=leverage,
                                                   margin_mode=self.signer_client.ISOLATED_MARGIN_MODE))
        else:
            run(self.signer_client.update_leverage(market_index=self.markets[symbol]["id"], leverage=leverage,
                                                   margin_mode=self.signer_client.CROSS_MARGIN_MODE))

    def set_leverage(self, leverage: Int, symbol: Str = None, params={}):
        self.load_markets()
        # Accept marginMode via params (ccxt convention). Default kept as
        # 'isolated' for backward compatibility with any caller that's been
        # relying on the previous hardcoded behaviour.
        margin_mode_str = (params or {}).get('marginMode', 'isolated').lower()
        if margin_mode_str == 'cross':
            mode = self.signer_client.CROSS_MARGIN_MODE
        else:
            mode = self.signer_client.ISOLATED_MARGIN_MODE
        run(self.signer_client.update_leverage(market_index=self.markets[symbol]["id"], leverage=leverage,
                                               margin_mode=mode))

    def fetch_leverage(self, symbol: str, params={}):
        baseSymbol = self.ccxt_to_base(symbol)
        detailedAccounts: DetailedAccounts = run(self.account_api.account(by="index", value=f"{self.account_id}"))
        detailedAccount: DetailedAccount = detailedAccounts.accounts[0]
        positions: List[AccountPosition] = detailedAccount.positions
        for position in positions:
            if position.symbol == baseSymbol:
               lev = 100 / float(position.initial_margin_fraction)
               return int(lev)

        id = self.symbol_to_market_id(symbol)
        details: OrderBookDetails = run(self.order_api.order_book_details(market_id=id))
        detail: PerpsOrderBookDetail = details.order_book_details[0]
        detail: PerpsOrderBookDetail = detail
        lev = 10000 / float(detail.default_initial_margin_fraction)
        return int(lev)

    def fetch_margin_mode(self, symbol: str, params={}):
        baseSymbol = self.ccxt_to_base(symbol)
        detailedAccounts: DetailedAccounts = run(self.account_api.account(by="index", value=f"{self.account_id}"))
        detailedAccount: DetailedAccount = detailedAccounts.accounts[0]
        positions: List[AccountPosition] = detailedAccount.positions
        for position in positions:
            if position.symbol == baseSymbol:
                if position.margin_mode == 1:
                    return "isolated"
                else:
                    return "cross"

    def sign(self, path: str, api: str = "public", method: str = "GET", params: Optional[Dict] = None,
             headers: Optional[Dict] = None, body: Optional[Any] = None):
        """
        Build URL, headers, body. For private endpoints call the signer supplied in options['signer'].
        The signer must return a dict of headers to attach (including signature and nonce if required).
        """
        params = params or {}
        headers = headers or {}
        url = self.urls["api"][api] + "/" + path.lstrip("/")

        if api == "public":
            # replace placeholders in path, e.g. /prices/{symbol}
            used_keys = []
            for k, v in params.items():
                placeholder = "{" + k + "}"
                if placeholder in url:
                    url = url.replace(placeholder, str(v))
                    used_keys.append(k)

            # remove used params
            for k in used_keys:
                params.pop(k, None)

            if method == "GET":
                if params:
                    url += "?" + self.urlencode(params)
                body = None
            else:
                body = self.json(params) if params else None
                headers["Content-Type"] = "application/json"

            return {
                "url": url,
                "method": method,
                "body": body,
                "headers": headers,
            }

    def get_base_token(self, symbol: str) -> str:
        return symbol.replace("/USDT:USDT", "").replace("/USDC:USDC", "")

    def fetch_funding_rate(self, symbol: str, params: object = {}) -> FundingRate | None:
        data = self.publicGetApiFunding(params)
        if "funding_rates" in data:
            for rate in data["funding_rates"]:
                if rate["symbol"] == self.get_base_token(symbol) and rate["exchange"] == "lighter":
                    fr = self._parse_funding_rate(symbol, rate)
                    return fr

    def _parse_funding_rate(self, symbol, rate) -> FundingRate:
        # Summary
        # {
        #     "market_id": 42,
        #     "exchange": "binance",
        #     "symbol": "SPX",
        #     "rate": 0.0001
        # },
        #
        symbol = symbol
        funding = self.safe_number(rate, 'rate')
        markPx = 0
        oraclePx = 0
        fundingTimestamp = (int(math.floor(self.milliseconds()) / 60 / 60 / 1000) + 1) * 60 * 60 * 1000

        additionalInfo = {}
        additionalInfo['fundingDatetime'] = fundingTimestamp
        additionalInfo['fundingRateAnnualized'] = funding * 3 * 365
        additionalInfo['fundingRate'] = funding

        return {
            'info': self.extend(rate, additionalInfo),
            'symbol': symbol,
            'markPrice': markPx,
            'indexPrice': oraclePx,
            'interestRate': None,
            'estimatedSettlePrice': None,
            'timestamp': None,
            'datetime': None,
            'fundingRate': funding,
            'fundingTimestamp': fundingTimestamp,
            'fundingDatetime': self.iso8601(fundingTimestamp),
            'nextFundingRate': None,
            'nextFundingTimestamp': None,
            'nextFundingDatetime': None,
            'previousFundingRate': None,
            'previousFundingTimestamp': None,
            'previousFundingDatetime': None,
            'interval': '8h',
        }

    def fetch_accounts(self, params={}):
        accounts: SubAccounts = run(self.account_api.accounts_by_l1_address(l1_address=self.walletAddress))
        return accounts.to_dict()

    def close(self):
        return run(self.api_client.close())
