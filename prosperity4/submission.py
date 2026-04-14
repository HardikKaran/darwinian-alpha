from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict
import json


class ProductTrader:
    def __init__(self, symbol: str, position_limit: int):
        self.symbol = symbol
        self.position_limit = position_limit

    def get_orders(self, state: TradingState) -> List[Order]:
        raise NotImplementedError

    def load_state(self, data: dict) -> None:
        pass

    def dump_state(self, data: dict) -> None:
        pass

    def best_bid(self, od: OrderDepth):
        return max(od.buy_orders) if od.buy_orders else None

    def best_ask(self, od: OrderDepth):
        return min(od.sell_orders) if od.sell_orders else None

    def mid_price(self, od: OrderDepth):
        bid = self.best_bid(od)
        ask = self.best_ask(od)
        return (bid + ask) / 2 if bid is not None and ask is not None else None

    def current_position(self, state: TradingState) -> int:
        return state.position.get(self.symbol, 0)


# ---------------------------------------------------------------------------
# ASH_COATED_OSMIUM — tighter passive market-maker
# Spread ~16 ticks → post at FV ± 7 (1 tick inside ±8 half-spread).
# Earns 14 ticks/RT vs prior 8 ticks. TAKE_EDGE=1 captures more flow.
# Position limit bumped to 80 per competition rules.
# ---------------------------------------------------------------------------

class AshMarketMaker(ProductTrader):

    FV_INIT    = 10_000
    EMA_ALPHA  = 0.02
    QUOTE_HALF = 7      # 1 tick inside market's ~±8 half-spread; earns 14 ticks/RT
    SKEW_DIV   = 15     # gentle skew so quotes don't drift too far at max pos
    TAKE_EDGE  = 1      # take any ask < FV-1 or bid > FV+1

    def __init__(self, symbol="ASH_COATED_OSMIUM", position_limit=80):
        super().__init__(symbol, position_limit)
        self.fv: float | None = None

    def load_state(self, data: dict) -> None:
        self.fv = data.get(self.symbol + "_fv", self.FV_INIT)

    def dump_state(self, data: dict) -> None:
        data[self.symbol + "_fv"] = self.fv

    def get_orders(self, state: TradingState) -> List[Order]:
        orders: List[Order] = []
        od = state.order_depths.get(self.symbol)
        if od is None:
            return orders

        mid = self.mid_price(od)
        if mid is None:
            return orders

        self.fv = self.EMA_ALPHA * mid + (1 - self.EMA_ALPHA) * (self.fv or mid)
        fv  = self.fv
        pos = self.current_position(state)
        ba  = self.best_ask(od)
        bb  = self.best_bid(od)

        # 1. Aggressive takes — take any price 1+ tick through FV
        if ba is not None and ba < fv - self.TAKE_EDGE:
            qty = min(-od.sell_orders[ba], self.position_limit - pos)
            if qty > 0:
                orders.append(Order(self.symbol, ba, qty))
                pos += qty

        if bb is not None and bb > fv + self.TAKE_EDGE:
            qty = min(od.buy_orders[bb], self.position_limit + pos)
            if qty > 0:
                orders.append(Order(self.symbol, bb, -qty))
                pos -= qty

        # 2. Passive quotes with inventory skew
        skew    = round(pos / self.SKEW_DIV)
        my_bid  = round(fv) - self.QUOTE_HALF - skew
        my_ask  = round(fv) + self.QUOTE_HALF - skew
        buy_cap = self.position_limit - pos
        sel_cap = self.position_limit + pos

        if buy_cap > 0:
            orders.append(Order(self.symbol, my_bid,  buy_cap))
        if sel_cap > 0:
            orders.append(Order(self.symbol, my_ask, -sel_cap))

        return orders


# ---------------------------------------------------------------------------
# INTARIAN_PEPPER_ROOT — directional buy-and-hold trend rider
# Pepper trended +3,001 ticks over 3 days (~1 tick/timestamp).
# Strategy: aggressively build max long position, hold, and only sell on
# extreme spikes. Captures position_limit × trend = ~8,000 PnL per 1k ticks.
# ---------------------------------------------------------------------------

class PepperTrendTrader(ProductTrader):

    EMA_ALPHA  = 0.05   # slow EMA just for trend reference
    ASK_BUFFER = 20     # post ask at ba + 20; almost never sells, preserving longs

    def __init__(self, symbol="INTARIAN_PEPPER_ROOT", position_limit=80):
        super().__init__(symbol, position_limit)
        self.ema: float | None = None

    def load_state(self, data: dict) -> None:
        self.ema = data.get(self.symbol + "_ema")

    def dump_state(self, data: dict) -> None:
        data[self.symbol + "_ema"] = self.ema

    def get_orders(self, state: TradingState) -> List[Order]:
        orders: List[Order] = []
        od = state.order_depths.get(self.symbol)
        if od is None:
            return orders

        bb = self.best_bid(od)
        ba = self.best_ask(od)
        if bb is None or ba is None:
            return orders

        mid = (bb + ba) / 2
        self.ema = self.EMA_ALPHA * mid + (1 - self.EMA_ALPHA) * (self.ema or mid)

        pos = self.current_position(state)

        # 1. Aggressively buy — take full ask volume until max long
        if pos < self.position_limit:
            ask_vol = abs(od.sell_orders.get(ba, 0))
            qty = min(ask_vol, self.position_limit - pos)
            if qty > 0:
                orders.append(Order(self.symbol, ba, qty))
                pos += qty

        # 2. Passive bid 1 tick above best bid to mop up remaining capacity
        remaining = self.position_limit - pos
        if remaining > 0 and bb is not None:
            orders.append(Order(self.symbol, bb + 1, remaining))

        # 3. Wide passive ask — only sell on extreme spike (ba + ASK_BUFFER)
        #    Keeps the long position intact to ride the trend
        if pos > 0 and ba is not None:
            orders.append(Order(self.symbol, ba + self.ASK_BUFFER, -pos))

        return orders


# ---------------------------------------------------------------------------
# Trader — orchestrates all strategies
# ---------------------------------------------------------------------------

STRATEGIES: List[ProductTrader] = [
    AshMarketMaker(symbol="ASH_COATED_OSMIUM",       position_limit=80),
    PepperTrendTrader(symbol="INTARIAN_PEPPER_ROOT", position_limit=80),
]


class Trader:

    def run(self, state: TradingState):
        trader_data: dict = json.loads(state.traderData) if state.traderData else {}

        for s in STRATEGIES:
            s.load_state(trader_data)

        result: Dict[str, List[Order]] = {}
        for s in STRATEGIES:
            result[s.symbol] = s.get_orders(state)

        for s in STRATEGIES:
            s.dump_state(trader_data)

        return result, 0, json.dumps(trader_data)
