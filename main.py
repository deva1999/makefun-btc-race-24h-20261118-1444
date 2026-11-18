"""Dip-buy mean-reversion bot for the make.fun BTC 24h race.

Idea, in one paragraph: Coinbase Wrapped BTC drifts and snaps back on a
one-minute tape, so this bot waits for price to fall a clear distance below
its own recent average and buys the dip itself, at full size, the moment the
dip prints -- no confirmation layer, because in this race family the ranking
is decided by when the canonical dip trade is taken, not by which trade is
picked. It then sells into the recovery at a modest take-profit, cuts the
trade at a wide stop, and otherwise stays out of the way. Long only, one
open position, whole-cash entries, integer arithmetic throughout.

Mechanics that matter:
- A trade is decided on the bar that just closed and fills at the next
  bar's open, so the trigger must fire on the dip bar itself. The buy
  condition is evaluated on the close of every bar against the mean of the
  last 120 closes; the first bar that closes 70 bps or more below that
  mean is the entry. Nothing waits for a second confirming bar.
- A full-size round trip costs about 26 bps in fee, slippage and price
  impact, so the take-profit (80 bps from the cost basis) and the dip
  depth (70 bps) are both set well clear of that cost. The stop sits at
  150 bps so a normal chop does not shake the bot out of a dip it just
  bought; the take-profit is the exit that is expected to fire.
- After any exit the bot rests 10 bars before it may buy again, so a
  stop-out is never chased straight back into the same falling tape.
- The bot keeps its own history of closes from the batches it is sent,
  including any warm-up bars, and never trades while warming up. It uses
  only integer arithmetic on the price integers the harness sends, seeds
  nothing and reads no clock, no environment and no file, so two runs on
  the same bars produce the same orders byte for byte.

Calibrated on the kit's frozen 24h dev window (BTC fell 2.8% across it):
dip 70 bps / take-profit 80 bps / stop 150 bps / 120-bar mean was the
strongest of the dip family that also held up on held-out 12-hour
sub-windows (median about +$5.90 there, profitable on 5 of 7). The dev
number itself is one trial and is not the server result.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

# Strategy constants, all in basis points unless noted.
MEAN_BARS = 120        # rolling mean window, in one-minute bars
DIP_BPS = 70           # buy when close is this far below the rolling mean
TAKE_PROFIT_BPS = 80   # sell when close is this far above the cost basis
STOP_BPS = 150         # sell when close is this far below the cost basis
COOLDOWN_BARS = 10     # bars to rest after an exit before buying again
MAX_SLIPPAGE_BPS = 200  # fills move at most ~64 bps a minute; 200 is slack

# Close history, oldest first. Appended once per batch, in batch order.
_closes = []
# Sequence number of the batch on which the last sell was decided.
_last_sell_seq = -10**9


def _rolling_mean():
    """Mean of the last MEAN_BARS closes, as an integer price."""
    window = _closes[-MEAN_BARS:]
    return sum(window) // len(window)


def _cost_basis_price(state):
    """Average entry price implied by the held position's cost basis.

    The harness reports cost in quote units (fees included), so the basis
    price is cost * 10 ** (qty_scale + price_scale) // (qty * 10 **
    quote_scale). All integers; returns None when flat.
    """
    holding = state.positions.get(SYMBOL)
    if holding is None or holding.qty <= 0:
        return None
    symbol = state.symbols[SYMBOL]
    quote_scale = state.hello["capital"]["quote_scale"]
    factor = 10 ** (symbol["qty_scale"] + symbol["price_scale"] - quote_scale)
    return holding.cost * factor // holding.qty


def on_batch(state, batch):
    global _last_sell_seq
    close = state.close(SYMBOL)
    if close is None:
        return []
    _closes.append(close)

    if state.warming_up:
        return []

    holding = state.positions.get(SYMBOL)
    if holding is not None and holding.qty > 0:
        entry = _cost_basis_price(state)
        if entry is not None:
            if close * 10000 >= entry * (10000 + TAKE_PROFIT_BPS):
                _last_sell_seq = state.seq
                return [{
                    "side": "sell",
                    "symbol": SYMBOL,
                    "qty": holding.qty,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }]
            if close * 10000 <= entry * (10000 - STOP_BPS):
                _last_sell_seq = state.seq
                return [{
                    "side": "sell",
                    "symbol": SYMBOL,
                    "qty": holding.qty,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }]
        return []

    # Flat: buy the dip at full size, the moment it prints.
    if len(_closes) < MEAN_BARS:
        return []
    if state.seq - _last_sell_seq < COOLDOWN_BARS:
        return []
    mean = _rolling_mean()
    if close * 10000 > mean * (10000 - DIP_BPS):
        return []
    cash = state.cash
    if cash < 1000000:  # below the 1 USDC minimum notional; stay flat
        return []
    return [{
        "side": "buy",
        "symbol": SYMBOL,
        "notional_quote_units": cash,
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
    }]


makefun_bars.run(on_batch)
