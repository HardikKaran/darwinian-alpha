from datamodel import Order, TradingState
from strategies.base import ProductTrader
from typing import List
import math

SYMBOL = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 50

# Pepper drifted +3,000 over 3 days — it is non-stationary.  Mean-reversion
# loses money here.  Instead we ride the trend by quoting asymmetrically:
#   bid close to EMA  →  gets filled on dips, accumulating long exposure
#   ask far above EMA →  only sells when price is genuinely extended

EMA_ALPHA = 0.15        # moderately fast; tracks the trend without chasing ticks

# Quote offsets from EMA.  Both must exceed the half-spread (~6.5) so that
# passive fills are net-profitable.  Wider ask biases the book long.
BID_OFFSET = 5          # post bid at EMA - 5  (just inside the market, earns spread)
ASK_OFFSET = 10         # post ask at EMA + 10 (only sell if price very extended)

# Opportunistic take: cross the ask aggressively only when the price is well
# below the EMA — must exceed the half-spread to ensure positive expectancy.
TAKE_DIP_THRESHOLD = 9  # buy at market if best_ask < EMA - 9

# Order-book imbalance filter.  If the top-of-book is overwhelmingly one-sided,
# suppress the quote on the crowded side to avoid being run over.
OBI_BUY_PRESSURE  = 0.75   # obi > this → huge buy wall → pause asks
OBI_SELL_PRESSURE = 0.25   # obi < this → huge sell wall → pause bids

# Inventory skew: as we approach the position limit, widen the quote on the
# side that would increase exposure further, to slow down accumulation.
SKEW_DIVISOR = 8


class PepperTrendTrader(ProductTrader):
    """
    Trend-following market-maker for INTARIAN_PEPPER_ROOT.

    Key design choices vs the previous mean-reversion strategy:
    - NO mean-reversion entries.  Pepper is trending; fading moves loses money.
    - Asymmetric quoting: bid offset < ask offset → net long bias.
    - Quote offsets are both > half-spread (~6.5) so passive fills are profitable.
    - OBI filter pauses one side when the book is heavily one-sided.
    - Opportunistic aggressive buy only when dip exceeds the half-spread.
    - Inventory skew slows accumulation near the position limit.
    """

    def __init__(
        self,
        symbol: str = SYMBOL,
        position_limit: int = POSITION_LIMIT,
        ema_alpha: float = EMA_ALPHA,
        bid_offset: int = BID_OFFSET,
        ask_offset: int = ASK_OFFSET,
        take_dip_threshold: int = TAKE_DIP_THRESHOLD,
        obi_buy_pressure: float = OBI_BUY_PRESSURE,
        obi_sell_pressure: float = OBI_SELL_PRESSURE,
        skew_divisor: int = SKEW_DIVISOR,
    ):
        super().__init__(symbol, position_limit)
        self.ema_alpha = ema_alpha
        self.bid_offset = bid_offset
        self.ask_offset = ask_offset
        self.take_dip_threshold = take_dip_threshold
        self.obi_buy_pressure = obi_buy_pressure
        self.obi_sell_pressure = obi_sell_pressure
        self.skew_divisor = skew_divisor
        self.ema: float | None = None

    # --- state persistence ---

    def load_state(self, data: dict) -> None:
        self.ema = data.get(self.symbol + "_ema")

    def dump_state(self, data: dict) -> None:
        data[self.symbol + "_ema"] = self.ema

    # --- core logic ---

    def get_orders(self, state: TradingState) -> List[Order]:
        orders: List[Order] = []

        od = state.order_depths.get(self.symbol)
        if od is None:
            return orders

        best_bid = self.best_bid(od)
        best_ask = self.best_ask(od)
        if best_bid is None or best_ask is None:
            return orders

        mid = (best_bid + best_ask) / 2

        # Update EMA
        if self.ema is None:
            self.ema = mid
        else:
            self.ema = self.ema_alpha * mid + (1 - self.ema_alpha) * self.ema

        ema = self.ema
        pos = self.current_position(state)

        # ── OBI calculation ───────────────────────────────────────────────────
        bid_vol = od.buy_orders.get(best_bid, 0)
        ask_vol = abs(od.sell_orders.get(best_ask, 0))
        total_vol = bid_vol + ask_vol
        obi = bid_vol / total_vol if total_vol > 0 else 0.5

        allow_buy  = obi >= self.obi_sell_pressure   # suppress bids if sell wall
        allow_sell = obi <= self.obi_buy_pressure    # suppress asks if buy wall

        # ── 1. Opportunistic take on deep dip ─────────────────────────────────
        # Only cross the spread when the dip is large enough to guarantee profit
        # after paying the spread (threshold > half-spread of ~6.5).
        if allow_buy and best_ask < ema - self.take_dip_threshold:
            qty = min(ask_vol, self.position_limit - pos)
            if qty > 0:
                orders.append(Order(self.symbol, best_ask, qty))
                pos += qty

        # ── 2. Passive asymmetric quotes with inventory skew ──────────────────
        skew = round(pos / self.skew_divisor)

        # Shift both quotes down when long (skew > 0) to slow long accumulation
        # and encourage selling; shift up when short to encourage buying.
        my_bid = math.floor(ema) - self.bid_offset - skew
        my_ask = math.ceil(ema)  + self.ask_offset - skew

        buy_capacity  = self.position_limit - pos
        sell_capacity = self.position_limit + pos

        if allow_buy and buy_capacity > 0:
            orders.append(Order(self.symbol, my_bid, buy_capacity))

        if allow_sell and sell_capacity > 0:
            orders.append(Order(self.symbol, my_ask, -sell_capacity))

        return orders
