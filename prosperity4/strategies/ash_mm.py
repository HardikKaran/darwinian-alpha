from datamodel import Order, TradingState
from strategies.base import ProductTrader
from typing import List

SYMBOL = "ASH_COATED_OSMIUM"
POSITION_LIMIT = 50

# ASH is stationary around 10,000.  A very slow EMA lets the fair value
# drift with any multi-day structural shift without chasing tick noise.
EMA_ALPHA = 0.02
FAIR_VALUE_INIT = 10_000

# We quote ±QUOTE_HALF ticks from fair value as passive limit orders.
# The market spread is ~16 ticks, so ±4 puts us well inside and earns
# most of the spread while still being passive.
QUOTE_HALF = 4

# Inventory skew: shift both quotes by position / SKEW_DIVISOR so that
# a long inventory cheapens our ask and a short inventory raises our bid.
SKEW_DIVISOR = 5

# Aggressive take edge: cross the market only when the price offered is
# more than TAKE_EDGE ticks through fair value (genuine mispricing).
TAKE_EDGE = 2


class AshMarketMaker(ProductTrader):
    """
    Passive market-maker for ASH_COATED_OSMIUM.

    Three layers of activity:
    1. Aggressive takes  — hit obvious mispricings (ask < FV - TAKE_EDGE or bid > FV + TAKE_EDGE).
    2. Passive quotes    — post bid/ask at FV ± QUOTE_HALF, shifted by inventory.
    3. EMA fair value    — slow EMA tracks any structural drift away from 10,000.
    """

    def __init__(
        self,
        symbol: str = SYMBOL,
        position_limit: int = POSITION_LIMIT,
        fair_value_init: float = FAIR_VALUE_INIT,
        ema_alpha: float = EMA_ALPHA,
        quote_half: int = QUOTE_HALF,
        skew_divisor: int = SKEW_DIVISOR,
        take_edge: int = TAKE_EDGE,
    ):
        super().__init__(symbol, position_limit)
        self.fair_value_init = fair_value_init
        self.ema_alpha = ema_alpha
        self.quote_half = quote_half
        self.skew_divisor = skew_divisor
        self.take_edge = take_edge
        self.fv: float | None = None

    # --- state persistence ---

    def load_state(self, data: dict) -> None:
        self.fv = data.get(self.symbol + "_fv", self.fair_value_init)

    def dump_state(self, data: dict) -> None:
        data[self.symbol + "_fv"] = self.fv

    # --- core logic ---

    def get_orders(self, state: TradingState) -> List[Order]:
        orders: List[Order] = []

        od = state.order_depths.get(self.symbol)
        if od is None:
            return orders

        mid = self.mid_price(od)
        if mid is None:
            # One-sided book — still update EMA with what we have, skip quoting
            return orders

        # Update fair-value EMA
        if self.fv is None:
            self.fv = mid
        else:
            self.fv = self.ema_alpha * mid + (1 - self.ema_alpha) * self.fv

        fv = self.fv
        pos = self.current_position(state)
        best_ask = self.best_ask(od)
        best_bid = self.best_bid(od)

        # ── 1. Aggressive takes ───────────────────────────────────────────────
        if best_ask is not None and best_ask < fv - self.take_edge:
            qty = min(-od.sell_orders[best_ask], self.position_limit - pos)
            if qty > 0:
                orders.append(Order(self.symbol, best_ask, qty))
                pos += qty

        if best_bid is not None and best_bid > fv + self.take_edge:
            qty = min(od.buy_orders[best_bid], self.position_limit + pos)
            if qty > 0:
                orders.append(Order(self.symbol, best_bid, -qty))
                pos -= qty

        # ── 2. Passive market-making with inventory skew ──────────────────────
        skew = round(pos / self.skew_divisor)
        my_bid = round(fv) - self.quote_half - skew
        my_ask = round(fv) + self.quote_half - skew

        buy_capacity  = self.position_limit - pos
        sell_capacity = self.position_limit + pos

        if buy_capacity > 0:
            orders.append(Order(self.symbol, my_bid, buy_capacity))
        if sell_capacity > 0:
            orders.append(Order(self.symbol, my_ask, -sell_capacity))

        return orders
