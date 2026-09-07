"""Reversal fill module: capped FAK ladder + paper L2 matcher.

Design goals for speed vs price:
- Books and token metadata must already be in memory before FIRE (ARM prefetch).
- Parallel legs; each leg independent abort.
- Hard cap: never walk through market after scramble.
- Ladder: t=0 FAK@min(ask,cap) -> t=1.5s +1tick if still <=cap -> t=4s @cap -> t=8s abort.
- Dedup key is owned by strategy layer (city|date|direction).
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

FIRE_BUDGET_MS = 8000
LADDER_MS = (0, 1500, 4000)
ZERO = Decimal("0")

def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _dec(v, default="0") -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(default)

def best_ask(book: Any):
    if book is None:
        return None
    if isinstance(book, dict):
        raw = book.get("best_ask")
        if raw is None and book.get("asks"):
            row = book["asks"][0]
            raw = row.get("price") if isinstance(row, dict) else row
        return _dec(raw) if raw is not None else None
    raw = getattr(book, "best_ask", None)
    return _dec(raw) if raw is not None else None

def best_bid(book: Any):
    if book is None:
        return None
    if isinstance(book, dict):
        raw = book.get("best_bid")
        if raw is None and book.get("bids"):
            row = book["bids"][0]
            raw = row.get("price") if isinstance(row, dict) else row
        return _dec(raw) if raw is not None else None
    raw = getattr(book, "best_bid", None)
    return _dec(raw) if raw is not None else None

def tick_of(book: Any) -> Decimal:
    if isinstance(book, dict):
        return _dec(book.get("tick_size"), "0.01") or Decimal("0.01")
    return _dec(getattr(book, "tick_size", None), "0.01") or Decimal("0.01")

def cap_price(ask, cap, tick, extra_ticks=0):
    if ask is None or ask <= ZERO or ask > cap:
        return None
    px = ask + tick * extra_ticks
    if px > cap:
        px = cap
    if tick > 0:
        px = (px / tick).to_integral_value(rounding=ROUND_DOWN) * tick
    return px if px <= cap else cap

def plan_leg_attempts(leg, book, target_shares, now_utc, elapsed_ms, budget_ms=FIRE_BUDGET_MS):
    side = str(leg.get("side") or "BUY").upper()
    cap = _dec(leg.get("cap"), "0.50")
    tick = tick_of(book)
    token = leg.get("token_id")
    if token is None:
        return {"status": "missing_token", "leg": leg.get("leg")}
    if elapsed_ms > budget_ms:
        return {"status": "abort_timeout", "leg": leg.get("leg"), "unfilled": str(target_shares)}

    # SELL path (YES roll): FAK against bids; cap field is max slip from best bid
    if side == "SELL":
        bid = best_bid(book)
        if bid is None or bid <= ZERO:
            return {"status": "no_book", "leg": leg.get("leg"), "side": "SELL"}
        # limit = bid - slip (floor at 0.01)
        slip = cap if leg.get("roll") else Decimal("0.05")
        limit = bid - slip
        if limit < Decimal("0.01"):
            limit = Decimal("0.01")
        if tick > 0:
            limit = (limit / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        return {
            "status": "send_fak",
            "leg": leg.get("leg"),
            "token_id": token,
            "side": "SELL",
            "outcome": leg.get("outcome"),
            "order_type": "FAK",
            "limit_price": str(limit),
            "shares": str(target_shares),
            "best_bid": str(bid),
            "slip": str(slip),
            "elapsed_ms": elapsed_ms,
            "at_utc": iso_utc(now_utc),
        }

    ask = best_ask(book)
    if ask is None:
        return {"status": "no_book", "leg": leg.get("leg")}
    # Ladder: 0ms flat, 1500ms +1 tick, 4000ms still at +1 (cap-bounded)
    extra = 0
    if elapsed_ms >= LADDER_MS[1]:
        extra = 1
    if elapsed_ms >= LADDER_MS[2]:
        extra = 1  # still only +1 tick; never open-ended chase
    limit = cap_price(ask, cap, tick, extra_ticks=extra)
    if limit is None:
        return {
            "status": "abort_above_cap",
            "leg": leg.get("leg"),
            "best_ask": str(ask) if ask is not None else None,
            "cap": str(cap),
            "note": "scramble_already_repriced_stand_down",
        }
    return {
        "status": "send_fak",
        "leg": leg.get("leg"),
        "token_id": token,
        "side": leg.get("side", "BUY"),
        "outcome": leg.get("outcome"),
        "order_type": "FAK",
        "limit_price": str(limit),
        "shares": str(target_shares),
        "best_ask": str(ask),
        "cap": str(cap),
        "extra_ticks": extra,
        "elapsed_ms": elapsed_ms,
        "at_utc": iso_utc(now_utc),
    }

def plan_fire_cycle(fire_event, books_by_token, remaining_by_leg, now_utc, elapsed_ms, budget_ms=None):
    if budget_ms is None:
        budget_ms = int(fire_event.get("fire_budget_ms") or FIRE_BUDGET_MS)
    actions = []
    for leg in fire_event.get("legs") or []:
        name = str(leg.get("leg"))
        left = remaining_by_leg.get(name, ZERO)
        if left <= ZERO:
            continue
        actions.append(
            plan_leg_attempts(
                leg,
                books_by_token.get(str(leg.get("token_id") or "")),
                left,
                now_utc,
                elapsed_ms,
                budget_ms=budget_ms,
            )
        )
    return actions

def shares_from_notional(notional, cap):
    if cap <= ZERO:
        return ZERO
    return (notional / cap).quantize(Decimal("0.01"))

def size_legs(fire_event, budget_usdc):
    out = {}
    for leg in fire_event.get("legs") or []:
        pct = _dec(leg.get("notional_pct"), "0")
        cap = _dec(leg.get("cap"), "0.50")
        out[str(leg.get("leg"))] = shares_from_notional((budget_usdc * pct).quantize(Decimal("0.01")), cap)
    return out

def paper_match_fak(book, limit, shares):
    if book is None:
        return {"filled_shares": ZERO, "avg_price": None, "cost": ZERO, "unfilled": shares}
    asks = book.setdefault("asks", [])
    filled = ZERO
    cost = ZERO
    leftover_levels = []
    need = shares
    for level in asks:
        if isinstance(level, dict):
            px = _dec(level.get("price")); sz = _dec(level.get("size"), "0")
        else:
            px, sz = _dec(level[0]), _dec(level[1])
        if px > limit:
            leftover_levels.append({"price": str(px), "size": str(sz)})
            continue
        take = min(need, sz)
        if take > ZERO:
            filled += take; cost += take * px; sz -= take; need -= take
        if sz > ZERO:
            leftover_levels.append({"price": str(px), "size": str(sz)})
    book["asks"] = leftover_levels
    book["best_ask"] = leftover_levels[0]["price"] if leftover_levels else None
    avg = (cost / filled) if filled > ZERO else None
    return {"filled_shares": filled, "avg_price": avg, "cost": cost, "unfilled": need}

def paper_match_fak_sell(book, limit, shares):
    """Match a SELL FAK against bids (price >= limit preferred; take best bid)."""
    if book is None or shares <= ZERO:
        return {"filled_shares": ZERO, "cost": ZERO, "avg_price": None, "unfilled": shares}
    bids = []
    if isinstance(book, dict):
        raw = book.get("bids") or []
        for row in raw:
            if isinstance(row, dict):
                px, sz = _dec(row.get("price")), _dec(row.get("size") or row.get("quantity"))
            elif isinstance(row, (list, tuple)) and len(row) >= 2:
                px, sz = _dec(row[0]), _dec(row[1])
            else:
                continue
            if px >= limit and sz > 0:
                bids.append((px, sz))
    # walk bids high to low
    left = shares
    filled = ZERO
    cost = ZERO
    for px, sz in sorted(bids, key=lambda x: -x[0]):
        take = min(left, sz)
        if take <= 0:
            continue
        filled += take
        cost += take * px
        left -= take
        if left <= 0:
            break
    avg = (cost / filled) if filled > 0 else None
    return {"filled_shares": filled, "cost": cost, "avg_price": avg, "unfilled": left}

