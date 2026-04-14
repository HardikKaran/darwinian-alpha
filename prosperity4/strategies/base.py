from datamodel import OrderDepth, TradingState, Order
from typing import List


class ProductTrader:
    """
    Base class for all per-product strategy implementations.
    Subclass this and implement `get_orders`.
    """

    def __init__(self, symbol: str, position_limit: int):
        self.symbol = symbol
        self.position_limit = position_limit

    def get_orders(self, state: TradingState) -> List[Order]:
        raise NotImplementedError

    # --- helpers ---

    def best_bid(self, order_depth: OrderDepth) -> int | None:
        return max(order_depth.buy_orders) if order_depth.buy_orders else None

    def best_ask(self, order_depth: OrderDepth) -> int | None:
        return min(order_depth.sell_orders) if order_depth.sell_orders else None

    def mid_price(self, order_depth: OrderDepth) -> float | None:
        bid = self.best_bid(order_depth)
        ask = self.best_ask(order_depth)
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        return None

    def current_position(self, state: TradingState) -> int:
        return state.position.get(self.symbol, 0)
