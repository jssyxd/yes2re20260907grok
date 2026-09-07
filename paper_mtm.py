"""Paper mark-to-market, PnL, win-rate for yes2re20260907grok.

Equity = initial_capital - net_debit + unrealized_mtm
  net_debit  = total reserved cost of fills (negative when profit released)
  unrealized = sum over open legs of shares * mark (best bid preferred, else mid)

Win rate = settled legs with cost>0 that won / such legs.
HIGH-only / NO / YES breakdowns included when possible.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


ZERO = Decimal("0")


def _d(v: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(default)


def _leg_cost(leg: dict[str, Any]) -> Decimal:
    return _d(leg.get("cost_usdc") or leg.get("cost") or 0)


def _leg_shares(leg: dict[str, Any]) -> Decimal:
    return _d(leg.get("shares") or leg.get("filled_shares") or 0)


def _leg_payout(leg: dict[str, Any]) -> Decimal:
    return _d(leg.get("payout_usdc") or leg.get("payout") or 0)


def mark_price(leg: dict[str, Any], books_by_token: dict[str, Any] | None) -> Decimal | None:
    """Best available mark for an open leg: best bid of its token, else mid."""
    if not books_by_token:
        return None
    token = str(leg.get("token_id") or "")
    book = books_by_token.get(token)
    if book is None:
        return None
    # book may be LocalOrderBook-like or dict snapshot
    bids = getattr(book, "bids", None) or (book.get("bids") if isinstance(book, dict) else None) or []
    asks = getattr(book, "asks", None) or (book.get("asks") if isinstance(book, dict) else None) or []
    best_bid = None
    best_ask = None
    if bids:
        try:
            best_bid = _d(bids[0][0] if isinstance(bids[0], (list, tuple)) else bids[0].get("price"))
        except Exception:
            pass
    if asks:
        try:
            best_ask = _d(asks[0][0] if isinstance(asks[0], (list, tuple)) else asks[0].get("price"))
        except Exception:
            pass
    if best_bid is not None and best_ask is not None:
        return (best_bid + best_ask) / 2
    if best_bid is not None:
        return best_bid
    if best_ask is not None:
        return best_ask
    return None


def compute_paper_pnl(
    state: dict[str, Any],
    books_by_token: dict[str, Any] | None = None,
    initial_capital: float | None = None,
) -> dict[str, Any]:
    initial = _d(initial_capital if initial_capital is not None else state.get("paper_initial_capital_usdc") or 1000)
    debit = _d(state.get("paper_total_debit_usdc") or 0)
    positions = state.get("positions") or {}

    realized = ZERO
    unrealized = ZERO
    open_legs = 0
    settled_legs = 0
    won_legs = 0
    lost_legs = 0
    by_side = {
        "NO": {"settled": 0, "won": 0, "lost": 0, "realized": ZERO},
        "YES": {"settled": 0, "won": 0, "lost": 0, "realized": ZERO},
    }
    open_details: list[dict[str, Any]] = []

    for key, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        settled = bool(pos.get("settled"))
        legs = pos.get("legs") or []
        if isinstance(legs, dict):
            legs = list(legs.values())
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            cost = _leg_cost(leg)
            shares = _leg_shares(leg)
            if cost <= 0 and shares <= 0:
                continue
            outcome = str(leg.get("outcome") or leg.get("side") or "").upper()
            if outcome not in ("NO", "YES"):
                if "no" in str(leg.get("leg", "")).lower():
                    outcome = "NO"
                elif "yes" in str(leg.get("leg", "")).lower():
                    outcome = "YES"
            if settled:
                payout = _leg_payout(leg)
                pnl = payout - cost
                realized += pnl
                settled_legs += 1
                won = bool(leg.get("won") or leg.get("leg_won") or (payout > 0 and shares > 0))
                if won:
                    won_legs += 1
                else:
                    lost_legs += 1
                if outcome in by_side:
                    by_side[outcome]["settled"] += 1
                    by_side[outcome]["realized"] += pnl
                    if won:
                        by_side[outcome]["won"] += 1
                    else:
                        by_side[outcome]["lost"] += 1
            else:
                open_legs += 1
                mark = mark_price(leg, books_by_token)
                if mark is not None and shares > 0:
                    mtm = shares * mark - cost
                else:
                    mtm = -cost  # conservative: mark as full loss until book available
                    mark = None
                unrealized += mtm
                open_details.append({
                    "key": key,
                    "leg": leg.get("leg"),
                    "outcome": outcome,
                    "shares": str(shares),
                    "cost": str(cost),
                    "mark": str(mark) if mark is not None else None,
                    "mtm": str(mtm),
                })

    equity = initial - debit + unrealized
    # When debit already reflects released profits, equity ≈ initial - debit + unrealized
    # Alternative: cash = initial - debit; equity = cash + sum(shares*mark for open)
    cash = initial - debit
    win_rate = (won_legs / settled_legs) if settled_legs else None

    return {
        "initial_capital_usdc": str(initial),
        "cash_usdc": str(cash),
        "net_debit_usdc": str(debit),
        "realized_pnl_usdc": str(realized),
        "unrealized_pnl_usdc": str(unrealized),
        "total_pnl_usdc": str(realized + unrealized),
        "equity_usdc": str(equity),
        "open_legs": open_legs,
        "settled_legs_with_cost": settled_legs,
        "won_legs": won_legs,
        "lost_legs": lost_legs,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "by_side": {
            k: {
                "settled": v["settled"],
                "won": v["won"],
                "lost": v["lost"],
                "realized_pnl_usdc": str(v["realized"]),
                "win_rate": round(v["won"] / v["settled"], 4) if v["settled"] else None,
            }
            for k, v in by_side.items()
        },
        "open_positions_mtm": open_details,
    }
