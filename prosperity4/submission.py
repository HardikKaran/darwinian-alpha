# submission.py — Round 2 v2
# Parameters chosen from round2_eda.ipynb backtest analysis + IMC platform log tuning.
#
# ASH_COATED_OSMIUM  (stationary ~10 000)
#   Strategy : wall-mid market-maker, taker + passive
#   Taker    : buy any ask < FV, sell any bid > FV (dominates PnL)
#   Passive  : overbid/undercut best standing order to sit INSIDE the spread
#              (vs v1's fixed fv±3 which sat ~11 ticks below best_ask → near-zero fills)
#   Skew     : position/20 shift keeps quotes centred as inventory builds
#
# INTARIAN_PEPPER_ROOT  (trending +27% over 3 days, I(1))
#   Strategy : best-ask-only directional loader (v2 improvement)
#   Reasoning: ba−bb ≥ 2 always → passive bb+1 bids NEVER cross the ask.
#              Trend value (~240 000) >> spread cost (~560). Miss the trend = miss everything.
#   v1 flaw  : swept ALL ask levels per tick, paying ask_2 when ask_1 ran out.
#              IMC log shows 22 units bought at +3 ticks above best ask at ts=100.
#   Fix      : take only best ask level per tick → lower avg entry cost, same exposure.
#   Defence  : once pos=80 post an impossibly wide ask (deepest_ask+20) to guard the long.

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import math

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
ASH_SYMBOL    = "ASH_COATED_OSMIUM"
PEPPER_SYMBOL = "INTARIAN_PEPPER_ROOT"
ASH_LIMIT     = 80
PEPPER_LIMIT  = 80

# ASH parameters
ASH_SKEW_DIV   = 20   # inventory skew divisor (softer = less over-correction)
ASH_MAX_VOL    = 25   # max passive quote volume per side (prevents limit dumps)

# PEPPER parameters
PEPPER_AGG_TARGET = 80   # take aggressively until pos = 80 (full_agg)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _sorted_bids(od: OrderDepth):
    """Descending list of (price, abs_volume) for bids."""
    return sorted(od.buy_orders.items(), key=lambda x: -x[0])

def _sorted_asks(od: OrderDepth):
    """Ascending list of (price, abs_volume) for asks."""
    return [(p, abs(v)) for p, v in sorted(od.sell_orders.items(), key=lambda x: x[0])]

def _wall_mid(bids, asks) -> float | None:
    """Average of DEEPEST resting bid and ask (spoof-resistant fair value)."""
    if not bids or not asks:
        return None
    return (bids[-1][0] + asks[-1][0]) / 2.0


# ─────────────────────────────────────────────────────────────────────────────
# ASH market-maker
# ─────────────────────────────────────────────────────────────────────────────
def _ash_orders(od: OrderDepth, pos: int) -> List[Order]:
    """
    Two-phase market-maker for ASH_COATED_OSMIUM.

    Phase 1 – Taker: sweep any ask strictly inside wall_mid (buy) or
              any bid strictly above wall_mid (sell).  Accounts for ~650
              fill events over 3 days and dominates PnL.

    Phase 2 – Passive: post bid at wall_mid−X, ask at wall_mid+X.
              Accounts for ~75 fill events at X=3 (only viable X).
              Quotes are inventory-skewed and capped at ASH_MAX_VOL.
    """
    orders: List[Order] = []

    bids = _sorted_bids(od)
    asks = _sorted_asks(od)

    if not bids or not asks:
        return orders

    wm = _wall_mid(bids, asks)
    if wm is None:
        return orders

    # Inventory skew: nudge FV toward neutral to lean against inventory
    skew = pos / ASH_SKEW_DIV
    fv   = wm - skew

    max_buy  = ASH_LIMIT - pos
    max_sell = ASH_LIMIT + pos

    # ── Phase 1: aggressive takes ─────────────────────────────────────────────
    for ask_price, ask_vol in asks:
        if ask_price < fv and max_buy > 0:          # ask inside FV → buy
            vol = min(ask_vol, max_buy)
            orders.append(Order(ASH_SYMBOL, ask_price, vol))
            max_buy -= vol
            pos     += vol
        elif ask_price <= wm and pos < -5 and max_buy > 0:   # flatten short
            vol = min(ask_vol, min(-pos, max_buy))
            orders.append(Order(ASH_SYMBOL, ask_price, vol))
            max_buy -= vol
            pos     += vol

    for bid_price, bid_vol in bids:
        if bid_price > fv and max_sell > 0:         # bid above FV → sell
            vol = min(bid_vol, max_sell)
            orders.append(Order(ASH_SYMBOL, bid_price, -vol))
            max_sell -= vol
            pos      -= vol
        elif bid_price >= wm and pos > 5 and max_sell > 0:   # flatten long
            vol = min(bid_vol, min(pos, max_sell))
            orders.append(Order(ASH_SYMBOL, bid_price, -vol))
            max_sell -= vol
            pos      -= vol

    # ── Phase 2: passive quotes — overbid/undercut best standing orders ──────────
    # Post inside the spread (at best_bid+1 / best_ask-1) for much higher fill rate.
    # Fallback to fv±1 if no suitable standing level exists.
    wall_bid = bids[-1][0]   # deepest bid (used for wall_mid)
    wall_ask = asks[-1][0]   # deepest ask

    # Bid side: find highest standing bid with vol>1 below FV, overbid it
    passive_bid = wall_bid + 1
    for bp, bv in bids:
        overbid = bp + 1
        if bv > 1 and overbid < fv:
            passive_bid = max(passive_bid, overbid)
            break
        elif bp < fv:
            passive_bid = max(passive_bid, bp)
            break

    # Ask side: find lowest standing ask with vol>1 above FV, undercut it
    passive_ask = wall_ask - 1
    for ap, av in asks:
        undercut = ap - 1
        if av > 1 and undercut > fv:
            passive_ask = min(passive_ask, undercut)
            break
        elif ap > fv:
            passive_ask = min(passive_ask, ap)
            break

    # Don't cross
    passive_bid = int(passive_bid)
    passive_ask = int(passive_ask)
    if passive_bid >= passive_ask:
        passive_bid = int(math.floor(fv)) - 1
        passive_ask = int(math.ceil(fv))  + 1

    buy_vol  = min(max_buy,  ASH_MAX_VOL)
    sell_vol = min(max_sell, ASH_MAX_VOL)

    if buy_vol > 0:
        orders.append(Order(ASH_SYMBOL, passive_bid,  buy_vol))
    if sell_vol > 0:
        orders.append(Order(ASH_SYMBOL, passive_ask, -sell_vol))

    return orders


# ─────────────────────────────────────────────────────────────────────────────
# PEPPER directional loader
# ─────────────────────────────────────────────────────────────────────────────
def _pepper_orders(od: OrderDepth, pos: int) -> List[Order]:
    """
    Full-aggressive directional loader for INTARIAN_PEPPER_ROOT.

    Backtest conclusion: ba−bb ≥ 2 always, so passive bb+1 bids never fill.
    Trend value (~240 000 over 3 days) >> spread cost (~560 for 80 units).
    → Take aggressively to 80, then post a wide defensive ask to hold the long.

    Edge-cases handled:
    - Empty order book → return no orders
    - pos already at limit → only post defensive ask
    - Partial fill at limit boundary: volume clipped to remaining capacity
    """
    orders: List[Order] = []

    asks = _sorted_asks(od)
    bids = _sorted_bids(od)

    remaining = PEPPER_LIMIT - pos

    # At limit — post wide defensive ask so we don't accidentally unwind
    if remaining <= 0:
        if asks:
            guard_ask = asks[-1][0] + 20   # deepest ask + 20 ticks: essentially never fills
            orders.append(Order(PEPPER_SYMBOL, guard_ask, -1))
        return orders

    if not asks and not bids:
        return orders

    # Take only the best ask level this tick.
    # v1 swept all levels (ask_1 AND ask_2), paying ask_2 when ask_1 ran out.
    # IMC log: ts=100 bought 22 units at ask_2 (+3 ticks) → wasted 66 PnL.
    # By taking one level at a time, we fill at the lowest available price each tick.
    # Reaching pos=80 takes ~1-2 extra ticks but saves ~100-200 PnL on entry cost.
    ask_price, ask_vol = asks[0]
    vol = min(ask_vol, remaining)
    orders.append(Order(PEPPER_SYMBOL, ask_price, vol))

    return orders


# ─────────────────────────────────────────────────────────────────────────────
# Trader
# ─────────────────────────────────────────────────────────────────────────────
class Trader:
    """
    Round 2 submission.

    traderData: not required for either strategy (no cross-tick state).
    The field is kept in the return signature for API compliance.
    """

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        # ── ASH ───────────────────────────────────────────────────────────────
        ash_od = state.order_depths.get(ASH_SYMBOL)
        if ash_od is not None:
            ash_pos = state.position.get(ASH_SYMBOL, 0)
            result[ASH_SYMBOL] = _ash_orders(ash_od, ash_pos)

        # ── PEPPER ────────────────────────────────────────────────────────────
        pepper_od = state.order_depths.get(PEPPER_SYMBOL)
        if pepper_od is not None:
            pepper_pos = state.position.get(PEPPER_SYMBOL, 0)
            result[PEPPER_SYMBOL] = _pepper_orders(pepper_od, pepper_pos)

        conversions  = 0
        trader_data  = ""   # no persistent state needed
        return result, conversions, trader_data
