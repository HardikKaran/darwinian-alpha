from datamodel import OrderDepth, TradingState, Order
from typing import List
import json


class Trader:
    """
    Single-file Trader class for submission.
    All strategy logic must live in this file when uploading.
    """

    def run(self, state: TradingState):
        result = {}
        conversions = 0
        trader_data = ""

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            # TODO: dispatch to per-product logic
            result[product] = orders

        return result, conversions, trader_data
