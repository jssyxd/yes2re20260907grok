#!/usr/bin/env python3
"""Standalone paper simulator for weatherbotyes2re."""
from __future__ import annotations
import argparse, json, time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from re_execution import paper_match_fak, plan_fire_cycle, size_legs
from reversal_strategy import maybe_arm_or_fire, ensure_re_state
from consensus_tracker import ConsensusTracker
TZ = "Asia/Shanghai"

PAPER_CFG = {
    "require_consensus_filter": False,
    "consensus_min_samples": 3,
    "consensus_window_seconds": 7200,
    "consensus_min_lead": "0.01",
    "allow_market_consensus_reference": False,
    "no_max_ask": "0.85",
    "yes_max_ask": "0.85",
    "yes_min_ask": "0.40",
    "no_notional_pct": "0.50",
    "yes_notional_pct": "0.50",
    "yes_leg_enabled": True,
    "high_fire_local_hour": 14,
    "max_bucket_jump": 3,
    "min_obs_before_fire": 1,
}


def make_buckets():
    out = []
    for t in range(28, 36):
        out.append({"bucket_id": f"h{t}", "lo": float(t), "hi": float(t+1), "no_token_id": f"NO-{t}", "yes_token_id": f"YES-{t}"})
    return out


def make_city():
    return {"city_id": "shanghai", "icao": "ZSPD", "timezone": TZ}


def make_book(ask, depth=8.0, tick=0.01, levels=4):
    asks = []
    px = Decimal(str(ask)); step = Decimal(str(tick)); sz = Decimal(str(depth))
    for i in range(levels):
        asks.append({"price": str(px + step*i), "size": str(sz/(i+1))})
    return {"best_ask": asks[0]["price"], "tick_size": str(tick), "asks": asks}


def seed_consensus_rank1(tracker: ConsensusTracker, city, date, direction, top_bucket_id="h31", now=None):
    now = now or datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    for i in range(40):
        t = now - timedelta(minutes=40 - i)
        tracker.record(city["city_id"], date, direction, top_bucket_id, best_ask=0.55, best_bid=0.52, ask_depth=10, now_utc=t)
        for bid, ask in (("h30", 0.22), ("h32", 0.18), ("h33", 0.12)):
            tracker.record(city["city_id"], date, direction, bid, best_ask=ask, best_bid=max(0.01, ask - 0.03), ask_depth=3, now_utc=t)


def apply_scramble(books, broken_no, new_yes, step):
    no_book = books.get(broken_no)
    if no_book and no_book.get("best_ask"):
        books[broken_no] = make_book(min(float(no_book["best_ask"])+0.04*step, 0.90), depth=max(1.0, 6.0-step*2))
    if new_yes and new_yes in books and books[new_yes].get("best_ask"):
        books[new_yes] = make_book(min(float(books[new_yes]["best_ask"])+0.06*step, 0.90), depth=max(0.5, 4.0-step*1.5))


def run_fire_window(fire, books, budget_usdc, now, scramble=True):
    remaining = size_legs(fire, budget_usdc)
    fills = {name: {"shares": Decimal("0"), "cost": Decimal("0")} for name in remaining}
    log = []
    for step, elapsed in [(0,0),(1,1600),(2,4100),(3,8100)]:
        if scramble and step>0:
            apply_scramble(books, fire.get("broken_no_token"), fire.get("new_yes_token"), step)
        for intent in plan_fire_cycle(fire, books, remaining, now+timedelta(milliseconds=elapsed), elapsed):
            log.append(intent)
            if intent.get("status") != "send_fak":
                continue
            match = paper_match_fak(books.get(intent["token_id"]) or {}, Decimal(intent["limit_price"]), Decimal(intent["shares"]))
            fills[intent["leg"]]["shares"] += match["filled_shares"]
            fills[intent["leg"]]["cost"] += match["cost"]
            remaining[intent["leg"]] = match["unfilled"]
            intent["fill"] = {"filled": str(match["filled_shares"]), "avg": str(match["avg_price"]) if match["avg_price"] is not None else None, "unfilled": str(match["unfilled"])}
    return fills, remaining, log


def scenario_one_bucket_fill():
    """METAR new high climbs one bucket → NO on dead + YES on new high."""
    state = {}; city = make_city(); buckets = make_buckets()
    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)  # 16:00 Shanghai
    tracker = ConsensusTracker(min_samples=3)
    books = {"NO-31": make_book(0.42, depth=10), "YES-32": make_book(0.45, depth=8)}
    for t in range(28, 36):
        books[f"YES-{t}"] = make_book(0.55 if t == 32 else 0.15, depth=5)
        books[f"NO-{t}"] = make_book(0.40, depth=8)
    actions = []
    # baseline then climb 31.4 (h31) -> 32.1 (h32)
    for temp, offset in ((30.2, 0), (31.4, 30), (32.1, 90)):
        ts = now + timedelta(seconds=offset)
        actions.extend(maybe_arm_or_fire(
            state, city, "2026-09-01", "high", buckets, None, temp, ts, ts, books, PAPER_CFG, tracker
        ))
    fire = next((a for a in actions if a.get("action_type") == "re_fire"), None)
    if fire is None:
        return {"name": "one_bucket_fill", "actions": [a.get("action_type") for a in actions],
                "ok": False, "error": "no_fire", "reasons": [a.get("reason") for a in actions]}
    fills, leftover, log = run_fire_window(fire, books, Decimal("20"), now + timedelta(seconds=90))
    ok = fills.get("buy_no_broken", {}).get("shares", 0) > 0 or any(
        v.get("shares", 0) > 0 for k, v in fills.items() if k.startswith("buy_no")
    )
    return {
        "name": "one_bucket_fill",
        "actions": [a["action_type"] for a in actions],
        "fire_jump": fire["jump"],
        "trigger": fire.get("trigger"),
        "fills": {k: {kk: str(vv) for kk, vv in v.items()} for k, v in fills.items()},
        "send_faks": sum(1 for x in log if x.get("status") == "send_fak"),
        "ok": bool(ok),
    }


def scenario_two_bucket_cascade():
    """New high jumps 2+ buckets → cascade NO + YES on current high."""
    state = {}; city = make_city(); buckets = make_buckets()
    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    tracker = ConsensusTracker(min_samples=3)
    books = {}
    for t in range(28, 36):
        books[f"YES-{t}"] = make_book(0.50, depth=5)
        books[f"NO-{t}"] = make_book(0.40, depth=8)
    actions = []
    # baseline 31.2 (h31) then 33.2 (h33) → climb 2
    for temp, offset in ((31.2, 0), (33.2, 60)):
        ts = now + timedelta(seconds=offset)
        actions.extend(maybe_arm_or_fire(
            state, city, "2026-09-01", "high", buckets, None, temp, ts, ts, books, PAPER_CFG, tracker
        ))
    fire = next((a for a in actions if a.get("action_type") == "re_fire"), None)
    legs = [x["leg"] for x in (fire or {}).get("legs", [])]
    has_no = any(l.startswith("buy_no") for l in legs)
    has_yes = "buy_yes_new" in legs
    return {
        "name": "two_bucket_cascade",
        "types": [a["action_type"] for a in actions],
        "legs": legs,
        "ok": fire is not None and has_no and has_yes and fire.get("jump", 0) >= 2,
        "trigger": (fire or {}).get("trigger"),
    }


def scenario_stale_obs_no_fire():
    state = {}; city = make_city(); buckets = make_buckets()
    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    tracker = ConsensusTracker(min_samples=3)
    # establish baseline with fresh obs
    maybe_arm_or_fire(state, city, "2026-09-01", "high", buckets, None, 31.2, now, now, {}, PAPER_CFG, tracker)
    # new high but obs is 100 min old → stale
    actions = maybe_arm_or_fire(
        state, city, "2026-09-01", "high", buckets, None, 32.5,
        now - timedelta(minutes=100), now, {}, PAPER_CFG, tracker,
    )
    return {
        "name": "stale_obs_no_fire",
        "types": [a.get("action_type") for a in actions],
        "ok": all(a.get("action_type") != "re_fire" for a in actions),
    }


def scenario_morning_skip():
    state = {}; city = make_city(); buckets = make_buckets()
    # 02:00 UTC = 10:00 Shanghai — before high_fire_local_hour 14
    now = datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)
    tracker = ConsensusTracker(min_samples=3)
    actions = []
    for temp, offset in ((30.0, 0), (32.1, 30)):
        ts = now + timedelta(seconds=offset)
        actions.extend(maybe_arm_or_fire(
            state, city, "2026-09-01", "high", buckets, None, temp, ts, ts, {}, PAPER_CFG, tracker
        ))
    return {
        "name": "morning_skip",
        "types": [a.get("action_type") for a in actions],
        "ok": all(a.get("action_type") != "re_fire" for a in actions),
    }


def scenario_cap_abort_no_chase():
    state = {}; city = make_city(); buckets = make_buckets()
    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    tracker = ConsensusTracker(min_samples=3)
    books = {"NO-31": make_book(0.95, depth=5), "YES-32": make_book(0.90, depth=5)}
    actions = []
    for temp, offset in ((31.2, 0), (32.1, 30)):
        ts = now + timedelta(seconds=offset)
        actions.extend(maybe_arm_or_fire(
            state, city, "2026-09-01", "high", buckets, None, temp, ts, ts, books, PAPER_CFG, tracker
        ))
    fire = next((a for a in actions if a.get("action_type") == "re_fire"), None)
    if fire is None:
        return {"name": "cap_abort_no_chase", "ok": True, "note": "no_fire"}
    fills, leftover, log = run_fire_window(fire, books, Decimal("20"), now)
    aborts = sum(1 for x in log if x.get("status") == "abort_above_cap")
    return {"name": "cap_abort_no_chase", "aborts": aborts, "ok": aborts >= 1 or all(
        float(v.get("shares") or 0) == 0 for v in fills.values()
    )}


def scenario_no_double_fire():
    state = {}; city = make_city(); buckets = make_buckets()
    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    tracker = ConsensusTracker(min_samples=3)
    books = {f"NO-{t}": make_book(0.40, depth=8) for t in range(28, 36)}
    books.update({f"YES-{t}": make_book(0.50, depth=5) for t in range(28, 36)})
    actions = []
    for temp, offset in ((31.2, 0), (32.1, 30), (32.3, 60)):
        ts = now + timedelta(seconds=offset)
        actions.extend(maybe_arm_or_fire(
            state, city, "2026-09-01", "high", buckets, None, temp, ts, ts, books, PAPER_CFG, tracker
        ))
    fires = [a for a in actions if a.get("action_type") == "re_fire"]
    # second new high same bucket should not re_fire; only one first fire
    return {"name": "no_double_fire", "n_fire": len(fires), "ok": len(fires) == 1}


def scenario_consensus_blocks_non_leader():
    """Legacy name kept: under new trigger, LOW is hard-rejected (not consensus)."""
    state = {}; city = make_city(); buckets = make_buckets()
    now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    tracker = ConsensusTracker(min_samples=3)
    actions = maybe_arm_or_fire(
        state, city, "2026-09-01", "low", buckets, None, 20.0, now, now, {}, PAPER_CFG, tracker
    )
    return {
        "name": "consensus_blocks_non_leader",
        "types": [a.get("action_type") for a in actions],
        "ok": actions and actions[0].get("reason") == "low_disabled",
    }


def run_scenarios():
    results=[]; failed=0
    for fn in (
        scenario_one_bucket_fill,
        scenario_two_bucket_cascade,
        scenario_stale_obs_no_fire,
        scenario_morning_skip,
        scenario_cap_abort_no_chase,
        scenario_no_double_fire,
        scenario_consensus_blocks_non_leader,
    ):
        r=fn(); results.append(r)
        if not r.get("ok"): failed += 1
    return results, failed


def live_loop(seconds, budget, tick):
    state={}; city=make_city(); buckets=make_buckets()
    tracker = ConsensusTracker(min_samples=3)
    base_local=datetime(2026,9,1,16,0,tzinfo=ZoneInfo(TZ))
    seed_consensus_rank1(tracker, city, "2026-09-01", "high", "h31", base_local.astimezone(timezone.utc))
    books={"NO-31": make_book(0.40, depth=12), "YES-32": make_book(0.30, depth=9)}
    for t in range(28, 36):
        books[f"YES-{t}"] = make_book(0.55 if t == 31 else 0.15, depth=5)
    journal=[]; fired_event=None; fill_result=None; temps=[]
    t0=time.time(); end=t0+seconds; step_i=0
    while time.time()<end:
        elapsed=time.time()-t0; frac=elapsed/max(seconds,1)
        temp = 30.4 if frac<0.25 else 30.9 if frac<0.45 else 31.2 if frac<0.55 else 32.15
        synth=(base_local+timedelta(seconds=elapsed)).astimezone(timezone.utc)
        actions=maybe_arm_or_fire(state, city, "2026-09-01", "high", buckets, 31.0, temp, synth, synth, books, PAPER_CFG, tracker)
        for a in actions:
            journal.append({"t":round(elapsed,2),"temp":temp,"action_type":a.get("action_type"),"reason":a.get("reason")})
            if a.get("action_type")=="re_fire" and fired_event is None:
                fired_event=a
                fill_result=run_fire_window(a, deepcopy(books), budget, synth, scramble=True)
        step_i += 1; temps.append(temp); time.sleep(tick)
    fills, leftover, log = fill_result if fill_result else ({}, {}, [])
    return {"seconds":seconds,"steps":step_i,"last_temp":temps[-1] if temps else None,"journal_types":[j.get("action_type") for j in journal],"fired":fired_event is not None,"jump":(fired_event or {}).get("jump"),"fills":{k:{kk:str(vv) for kk,vv in v.items()} for k,v in fills.items()} if fills else {},"leftover":{k:str(v) for k,v in leftover.items()} if leftover else {},"fak_intents":[x.get("status") for x in log],"positions":ensure_re_state(state)["fired"]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--seconds", type=int, default=30)
    p.add_argument("--budget", type=float, default=20.0)
    p.add_argument("--tick", type=float, default=1.0)
    p.add_argument("--scenarios-only", action="store_true")
    args=p.parse_args()
    scenarios, failed = run_scenarios()
    out={"scenarios":scenarios,"scenario_failures":failed}
    if not args.scenarios_only:
        out["live_loop"]=live_loop(args.seconds, Decimal(str(args.budget)), args.tick)
    print(json.dumps(out, indent=2, default=str))
    if failed: raise SystemExit(1)


if __name__=="__main__":
    main()
