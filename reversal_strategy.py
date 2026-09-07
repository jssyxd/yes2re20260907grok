"""Daily METAR new-high trigger (yes2re20260907grok first principles).

Primary signal (only):
  Within the city's IANA local calendar day, a METAR observation posts a
  temperature strictly higher than every previous obs today (new daily high).
  If that new high lands in a *higher* temperature bucket than the previous
  running high, fire:

    - NO on every bucket strictly below the new-high bucket (now dead)
    - YES on the new-high bucket (current known daily max)

No TAF / market rank-1 "break the favourite" primary trigger.
Quality gates: HIGH-only, local hour window, obs sanity, already_fired /
further-break YES roll, yes_min_ask (execution), max_open, cross-midnight.

IDLE -> ARMED (running high established) -> FIRED (bucket climb on new high)
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from consensus_tracker import ConsensusTracker, DEFAULT_TRACKER

ARM_C = 1.0
MAX_BUCKET_JUMP = 3
NO_MAX_ASK = Decimal("0.85")
YES_MAX_ASK = Decimal("0.40")
NO_NOTIONAL_PCT = Decimal("0.50")
YES_NOTIONAL_PCT = Decimal("0.50")
HIGH_FIRE_LOCAL_HOUR = 14
HIGH_FIRE_LOCAL_HOUR_END = 17
LOW_FIRE_LOCAL_HOUR_END = 10
REQUIRE_FRESH_OBS_SECONDS = 180  # legacy absolute-age gate — deprecated 2026-09-03 (see OBS_* window below)
OBS_MAX_LOOKBACK_SECONDS = 5400  # 90 min sanity: obs older than this = stale feed, do not fire
OBS_MAX_FUTURE_SECONDS = 900     # 15 min sanity: US AWS stations publish ~7 min EARLY; >15 min ahead = bad stamp
CONSENSUS_WINDOW_SECONDS = 7200  # 2h default; config can set 3600
CONSENSUS_MIN_SAMPLES = 20
CONSENSUS_MIN_LEAD = Decimal("0.03")
ZERO = Decimal("0")


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def bucket_contains(bucket: dict[str, Any], value: float) -> bool:
    lo, hi = bucket.get("lo"), bucket.get("hi")
    return (lo is None or value >= float(lo)) and (hi is None or value < float(hi))


def ensure_re_state(state: dict[str, Any]) -> dict[str, Any]:
    tree = state.setdefault("weatherbotyes2re", {})
    for name in ("armed", "fired", "running_extremes", "taf_forecasts", "last_obs", "last_obs_time"):
        tree.setdefault(name, {})
    return tree


def mid_value(bucket: dict[str, Any]) -> float:
    lo, hi = bucket.get("lo"), bucket.get("hi")
    if lo is not None and hi is not None:
        return (float(lo) + float(hi)) / 2.0
    if lo is not None:
        return float(lo)
    if hi is not None:
        return float(hi)
    return 0.0


def ordered_buckets(buckets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(buckets, key=mid_value)


def find_bucket(buckets: list[dict[str, Any]], value: float) -> dict[str, Any] | None:
    for b in buckets:
        if bucket_contains(b, value):
            return b
    return None


def bucket_index(ordered: list[dict[str, Any]], bucket: dict[str, Any] | None) -> int | None:
    if bucket is None:
        return None
    bid = str(bucket.get("bucket_id") or bucket.get("id") or "")
    for i, b in enumerate(ordered):
        if str(b.get("bucket_id") or b.get("id") or "") == bid:
            return i
        if b is bucket:
            return i
    return None


def session_key(city_id: str, market_local_date: str, direction: str) -> str:
    return f"{city_id}|{market_local_date}|{direction}"


def prune_stale_sessions(state: dict[str, Any], cities: list[dict[str, Any]], now_utc: datetime | None = None) -> int:
    """Drop expired armed/fired/running_extremes/last_obs_time session entries.

    Pure, deterministic, never raises; only the four sections above are
    mutated (taf_forecasts / last_obs / other state content are untouched).
    A session key has the shape ``city_id|market_local_date|direction`` (see
    session_key). Removal rules:

      1. Non-today date: market-local date != the city's local today
         (cross-day carryover from a previous market day) -> delete — EXCEPT a
         ``fired`` marker whose session still has an open paper position. That
         marker is the session's one-fire dedupe credential: between a city's
         local-midnight rollover and the next rules-TTL refresh the cache can
         still feed the old-date rule, and dropping the marker while its
         position is open re-opened the 2026-09-06 re-fire loop (same breach
         obs -> already_fired miss -> re-fire -> cash reserved with no leg to
         settle). The marker is kept until the position settles.

      2. Unknown city: city_id no longer present in the registry -> delete
         (defensive: registration table shrank).
      3. low zombie (armed only): a ``low`` session whose city local hour is
         already past LOW_FIRE_LOCAL_HOUR_END — the strategy hour window can
         never fire it, so the armed entry would pin the run loop to
         fast-poll forever -> delete.

    Malformed keys (not exactly 3 ``|``-separated parts) are kept as-is, and
    an unparseable timezone for a known city makes that city's keys skipped,
    both without raising. Returns the total number of deleted entries.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    tree = ensure_re_state(state)
    positions = state.get("positions") or {}
    by_id = {c.get("city_id"): c for c in cities if c.get("city_id") is not None}
    removed = 0
    for section in ("armed", "fired", "running_extremes", "last_obs_time"):
        section_state = tree.get(section)
        if not isinstance(section_state, dict):
            continue
        for key in list(section_state):
            parts = key.split("|")
            if len(parts) != 3:
                continue  # malformed key — defensive: never delete
            city_id, market_local_date, direction = parts
            city = by_id.get(city_id)
            if city is None:
                del section_state[key]
                removed += 1
                continue
            tz_name = city.get("timezone")
            if not isinstance(tz_name, str) or not tz_name:
                continue  # cannot localize — skip this city, never raise
            try:
                local_dt = now_utc.astimezone(ZoneInfo(tz_name))
            except Exception:
                continue  # bad tz entry — skip this city, never raise
            if market_local_date != local_dt.date().isoformat():
                if section == "fired":
                    pos = positions.get(key)
                    if pos is not None and not pos.get("settled"):
                        continue  # keep the one-fire dedupe credential until the position settles
                del section_state[key]
                removed += 1
                continue
            if section == "armed" and direction == "low" and local_dt.hour > LOW_FIRE_LOCAL_HOUR_END:
                del section_state[key]
                removed += 1
    return removed


def update_running_extreme(state, city_id, market_local_date, direction, temp: float, now_utc: datetime):
    tree = ensure_re_state(state)
    key = session_key(city_id, market_local_date, direction)
    rec = tree["running_extremes"].get(key) or {"value": None, "obs_count": 0}
    prev = rec.get("value")
    if direction == "high":
        new_val = temp if prev is None else max(float(prev), temp)
    else:
        new_val = temp if prev is None else min(float(prev), temp)
    rec["value"] = new_val
    rec["obs_count"] = int(rec.get("obs_count") or 0) + 1
    rec["updated_at_utc"] = iso_utc(now_utc)
    tree["running_extremes"][key] = rec
    return rec


def hour_ok(
    direction: str,
    local_hour: int,
    high_hour: int,
    low_hour_end: int,
    high_hour_end: int | None = None,
) -> bool:
    """HIGH: [high_hour, high_hour_end) local hour window. LOW: hour <= low_hour_end."""
    if direction == "high":
        if high_hour_end is None:
            high_hour_end = 17
        return high_hour <= local_hour < high_hour_end
    return local_hour <= low_hour_end


def obs_is_fresh(obs_time_utc: datetime | None, now_utc: datetime, max_age: int) -> bool:
    if obs_time_utc is None:
        return False
    return (now_utc.astimezone(timezone.utc) - obs_time_utc.astimezone(timezone.utc)).total_seconds() <= max_age


def is_new_obs_time(state: dict[str, Any], key: str, obs_time_utc: datetime | None) -> bool:
    """Reject duplicate pushes of the same observation timestamp."""
    if obs_time_utc is None:
        return False
    tree = ensure_re_state(state)
    prev = tree["last_obs_time"].get(key)
    stamp = iso_utc(obs_time_utc)
    if prev == stamp:
        return False
    tree["last_obs_time"][key] = stamp
    return True


def reference_extreme_from_consensus(
    tracker: ConsensusTracker,
    city_id: str,
    market_local_date: str,
    direction: str,
    ordered: list[dict[str, Any]],
    now_utc: datetime,
    window_seconds: int,
) -> tuple[float | None, dict[str, Any] | None, str]:
    """When TAF missing: use long-horizon rank-1 bucket mid as reference extreme."""
    ranks = tracker.rank_buckets(city_id, market_local_date, direction, now_utc, window_seconds)
    if not ranks:
        return None, None, "no_consensus"
    top_id, twap, _ = ranks[0]
    for b in ordered:
        if str(b.get("bucket_id") or b.get("id") or "") == top_id:
            return mid_value(b), b, "market_rank1"
    return None, None, "rank1_unmapped"


def maybe_arm_or_fire(
    state: dict[str, Any],
    city: dict[str, Any],
    market_local_date: str,
    direction: str,
    buckets: list[dict[str, Any]],
    taf_extreme: float | None,
    observed_temp: float | None,
    obs_time_utc: datetime | None,
    now_utc: datetime,
    books_by_token: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    consensus_tracker: ConsensusTracker | None = None,
) -> list[dict[str, Any]]:
    """METAR daily new-high primary trigger.

    ``taf_extreme`` is accepted for API compatibility but is NOT used as a
    fire trigger. ``consensus_tracker`` is still sampled for optional
    diagnostics / future soft filters.
    """
    actions: list[dict[str, Any]] = []
    if observed_temp is None:
        return actions

    # HARD REJECT: LOW markets disabled
    if str(direction).lower() == "low":
        return [{"action_type": "re_skip", "reason": "low_disabled",
                 "key": session_key(city["city_id"], market_local_date, direction)}]

    cfg = config or {}
    max_jump = int(cfg.get("max_bucket_jump", MAX_BUCKET_JUMP))
    obs_lookback_s = int(cfg.get("max_obs_lookback_seconds", OBS_MAX_LOOKBACK_SECONDS))
    obs_future_s = int(cfg.get("max_obs_future_seconds", OBS_MAX_FUTURE_SECONDS))
    high_hour = int(cfg.get("high_fire_local_hour", cfg.get("high_fire_local_hour_start", HIGH_FIRE_LOCAL_HOUR)))
    high_hour_end = int(cfg.get("high_fire_local_hour_end", HIGH_FIRE_LOCAL_HOUR_END))
    low_hour_end = int(cfg.get("low_fire_local_hour_end", LOW_FIRE_LOCAL_HOUR_END))
    min_obs_before_fire = int(cfg.get("min_obs_before_fire", 1))  # need prior sample so "new high" is real

    tree = ensure_re_state(state)
    key = session_key(city["city_id"], market_local_date, direction)

    # Cross-midnight fail-closed
    try:
        city_tz = city.get("timezone")
        if not city_tz:
            raise ValueError("city missing timezone")
        local_today = now_utc.astimezone(ZoneInfo(city_tz)).date().isoformat()
    except Exception:
        return [{"action_type": "re_skip", "reason": "stale_market_date",
                 "key": key, "guard": "tz_unresolvable"}]
    if market_local_date != local_today:
        return [{"action_type": "re_skip", "reason": "stale_market_date", "key": key}]

    # Optional consensus sampling (diagnostic only — not a fire gate)
    tracker = consensus_tracker or DEFAULT_TRACKER
    try:
        tracker.record_books(
            city["city_id"], market_local_date, direction,
            buckets, books_by_token, now_utc,
        )
    except Exception:
        pass

    # max_open (only for first fire, not roll)
    max_open = int(cfg.get("max_open_positions") or 0)
    if max_open > 0 and key not in tree["fired"]:
        open_count = sum(1 for p in (state.get("positions") or {}).values() if not p.get("settled"))
        if open_count >= max_open:
            return [{"action_type": "re_skip", "reason": "max_open_positions",
                     "key": key, "open": open_count, "cap": max_open}]

    # New observation only
    if not is_new_obs_time(state, key, obs_time_utc):
        return [{"action_type": "re_skip", "reason": "duplicate_obs_time", "key": key}]

    # Obs age sanity
    if obs_time_utc is not None:
        age_s = (now_utc - obs_time_utc).total_seconds()
        if age_s > obs_lookback_s:
            return [{"action_type": "re_skip", "reason": "stale_obs", "key": key, "age_s": age_s}]
        if age_s < -obs_future_s:
            return [{"action_type": "re_skip", "reason": "obs_in_future", "key": key, "age_s": age_s}]

    local_hour = now_utc.astimezone(ZoneInfo(city["timezone"])).hour
    ordered = ordered_buckets(buckets)

    # Previous running high BEFORE this update
    prev_rec = (tree.get("running_extremes") or {}).get(key) or {}
    prev_val = prev_rec.get("value")
    prev_obs_count = int(prev_rec.get("obs_count") or 0)

    rec = update_running_extreme(
        state, city["city_id"], market_local_date, direction, float(observed_temp), now_utc
    )
    running = float(rec["value"])
    obs_count = int(rec.get("obs_count") or 0)

    run_b = find_bucket(ordered, running)
    run_i = bucket_index(ordered, run_b)
    if run_i is None or run_b is None:
        return [{"action_type": "re_skip", "reason": "bucket_unmapped", "key": key, "running": running}]

    # --- Not a new daily high? -------------------------------------------------
    # First sample of the day: establish baseline only, never fire.
    if prev_val is None:
        tree["armed"][key] = {
            "status": "armed",
            "running": running,
            "bucket_id": str(run_b.get("bucket_id") or run_b.get("id") or ""),
            "armed_at_utc": iso_utc(now_utc),
            "fast_poll": True,
            "trigger": "metar_baseline",
        }
        actions.append({
            "action_type": "re_arm",
            "key": key,
            "running": running,
            "reason": "baseline_high_established",
            "prefetch_tokens": True,
            "fast_poll": True,
            "fast_poll_seconds": int(cfg.get("fast_poll_seconds", 8)),
        })
        return actions

    prev_f = float(prev_val)
    is_new_high = float(observed_temp) > prev_f + 1e-9 and running > prev_f + 1e-9
    if not is_new_high:
        # still track arm for fast poll near high season
        if hour_ok(direction, local_hour, high_hour, low_hour_end, high_hour_end) and key not in tree["fired"]:
            tree["armed"][key] = {
                "status": "armed",
                "running": running,
                "bucket_id": str(run_b.get("bucket_id") or run_b.get("id") or ""),
                "armed_at_utc": iso_utc(now_utc),
                "fast_poll": True,
                "trigger": "metar_running",
            }
            actions.append({
                "action_type": "re_arm",
                "key": key,
                "running": running,
                "reason": "running_high_hold",
                "prefetch_tokens": True,
                "fast_poll": True,
                "fast_poll_seconds": int(cfg.get("fast_poll_seconds", 8)),
            })
        return actions

    # --- New daily high observed ----------------------------------------------
    prev_b = find_bucket(ordered, prev_f)
    prev_i = bucket_index(ordered, prev_b)
    if prev_i is None:
        # previous high unmapped — treat as baseline refresh
        tree["armed"][key] = {
            "status": "armed", "running": running,
            "bucket_id": str(run_b.get("bucket_id") or run_b.get("id") or ""),
            "armed_at_utc": iso_utc(now_utc), "fast_poll": True, "trigger": "metar_new_high_unmapped_prev",
        }
        return [{"action_type": "re_arm", "key": key, "running": running, "reason": "new_high_prev_unmapped"}]

    bucket_climb = run_i - prev_i  # high direction: positive = climbed
    if bucket_climb <= 0:
        # New high but still same (or lower-mapped) bucket — no dead-bucket change
        tree["armed"][key] = {
            "status": "armed", "running": running,
            "bucket_id": str(run_b.get("bucket_id") or run_b.get("id") or ""),
            "armed_at_utc": iso_utc(now_utc), "fast_poll": True, "trigger": "metar_new_high_same_bucket",
        }
        return [{"action_type": "re_skip", "reason": "new_high_same_bucket", "key": key,
                 "running": running, "prev": prev_f, "bucket_id": str(run_b.get("bucket_id") or run_b.get("id") or "")}]

    if bucket_climb > max_jump:
        # Cap cascade depth; still allow fire but limit NO legs to max_jump buckets
        pass

    if not hour_ok(direction, local_hour, high_hour, low_hour_end, high_hour_end):
        return [{"action_type": "re_skip", "reason": "hour_not_in_window", "key": key,
                 "jump": bucket_climb, "running": running}]

    if prev_obs_count < min_obs_before_fire:
        return [{"action_type": "re_skip", "reason": "insufficient_prior_obs", "key": key,
                 "obs_count": prev_obs_count, "need": min_obs_before_fire}]

    yes_cap = Decimal(str(cfg.get("yes_max_ask", YES_MAX_ASK)))
    yes_floor = Decimal(str(cfg.get("yes_min_ask", "0.40")))
    no_cap = str(cfg.get("no_max_ask", NO_MAX_ASK))
    no_pct = Decimal(str(cfg.get("no_notional_pct", NO_NOTIONAL_PCT)))
    yes_pct = Decimal(str(cfg.get("yes_notional_pct", YES_NOTIONAL_PCT)))
    fire_yes = bool(cfg.get("yes_leg_enabled", True))

    new_bucket_id = str(run_b.get("bucket_id") or run_b.get("id") or "")
    new_yes_token = run_b.get("yes_token_id") or run_b.get("_yes_token_id")

    # ---- Further-break roll if already fired ---------------------------------
    if key in tree["fired"]:
        prev_fire = tree["fired"].get(key) or {}
        prev_bucket_id = str(prev_fire.get("new_bucket_id") or "")
        if prev_bucket_id == new_bucket_id:
            return [{"action_type": "re_skip", "reason": "already_fired", "key": key}]
        # climbed further → roll YES
        sell_slip = str(cfg.get("yes_roll_sell_slip", "0.05"))
        roll = {
            "action_type": "re_roll_yes",
            "key": key,
            "city_id": city["city_id"],
            "icao": city.get("icao"),
            "market_local_date": market_local_date,
            "direction": direction,
            "prev_bucket_id": prev_bucket_id,
            "new_bucket_id": new_bucket_id,
            "prev_running": prev_fire.get("running_extreme"),
            "running_extreme": running,
            "jump": bucket_climb,
            "trigger": "metar_new_high_roll",
            "legs": [],
            "fire_budget_ms": int(cfg.get("fire_budget_ms", 8000)),
        }
        pos = (state.get("positions") or {}).get(key) or {}
        prev_yes_token = None
        for leg in (pos.get("legs") or []):
            if isinstance(leg, dict) and (
                leg.get("leg") == "buy_yes_new" or str(leg.get("outcome", "")).upper() == "YES"
            ):
                prev_yes_token = leg.get("token_id")
                break
        if prev_yes_token is None:
            prev_yes_token = prev_fire.get("new_yes_token")
        if prev_yes_token:
            roll["legs"].append({
                "leg": "sell_yes_old", "token_id": prev_yes_token, "side": "SELL",
                "outcome": "YES", "cap": sell_slip, "notional_pct": "1.0", "roll": True,
            })
        # NO on previous landing bucket (now dead)
        if prev_bucket_id:
            for b in ordered:
                if str(b.get("bucket_id") or b.get("id") or "") == prev_bucket_id:
                    tok = b.get("no_token_id") or b.get("_no_token_id")
                    if tok:
                        roll["legs"].append({
                            "leg": "buy_no_broken", "token_id": tok, "side": "BUY",
                            "outcome": "NO", "cap": no_cap, "notional_pct": str(no_pct), "roll": True,
                        })
                    break
        if fire_yes and new_yes_token and yes_cap >= yes_floor:
            roll["legs"].append({
                "leg": "buy_yes_new", "token_id": new_yes_token, "side": "BUY",
                "outcome": "YES", "cap": str(yes_cap), "notional_pct": str(yes_pct),
                "min_ask": str(yes_floor), "roll": True,
            })
        tree["fired"][key] = {
            "status": "fired_roll", "at_utc": iso_utc(now_utc),
            "jump": bucket_climb, "running_extreme": running,
            "new_bucket_id": new_bucket_id, "new_yes_token": new_yes_token,
            "prev_bucket_id": prev_bucket_id, "trigger": "metar_new_high_roll",
        }
        actions.append(roll)
        return actions

    # ---- First fire on bucket-climbing new high ------------------------------
    # Dead buckets: every ordered bucket strictly below the new-high bucket
    # (cascade, depth capped by max_jump from the previous high bucket).
    climb = min(bucket_climb, max_jump)
    dead_indices = list(range(prev_i, run_i))  # [prev, run) — prev high bucket is now dead too if we left it
    # Actually: previous high was in prev_b; that bucket may still contain temps
    # equal to old high, but NEW high is in a higher bucket → all buckets with
    # hi <= new bucket's lo are dead for the daily HIGH market.
    # Practical: NO on buckets from max(0, run_i - climb) .. run_i-1
    dead_indices = list(range(max(0, run_i - climb), run_i))

    fire = {
        "key": key,
        "city_id": city["city_id"],
        "icao": city.get("icao"),
        "market_local_date": market_local_date,
        "direction": direction,
        "ref_extreme": prev_f,
        "ref_source": "metar_prev_high",
        "taf_extreme": float(taf_extreme) if taf_extreme is not None else None,
        "running_extreme": running,
        "jump": bucket_climb,
        "broken_bucket_id": str(ordered[dead_indices[-1]].get("bucket_id") or ordered[dead_indices[-1]].get("id") or "") if dead_indices else None,
        "broken_no_token": None,
        "new_bucket_id": new_bucket_id,
        "new_yes_token": new_yes_token,
        "trigger": "metar_new_high",
        "prev_high": prev_f,
        "legs": [],
        "fire_budget_ms": int(cfg.get("fire_budget_ms", 8000)),
    }

    n_dead = max(len(dead_indices), 1)
    for j, idx in enumerate(dead_indices):
        b = ordered[idx]
        tok = b.get("no_token_id") or b.get("_no_token_id")
        if not tok:
            continue
        leg_name = "buy_no_broken" if j == len(dead_indices) - 1 else f"buy_no_dead_{j}"
        fire["legs"].append({
            "leg": leg_name,
            "token_id": tok,
            "side": "BUY",
            "outcome": "NO",
            "cap": no_cap,
            "notional_pct": str(no_pct / n_dead),
        })
        if leg_name == "buy_no_broken":
            fire["broken_no_token"] = tok
            fire["broken_bucket_id"] = str(b.get("bucket_id") or b.get("id") or "")

    if fire_yes and new_yes_token is not None and yes_cap >= yes_floor:
        fire["legs"].append({
            "leg": "buy_yes_new",
            "token_id": new_yes_token,
            "side": "BUY",
            "outcome": "YES",
            "cap": str(yes_cap),
            "notional_pct": str(yes_pct),
            "min_ask": str(yes_floor),
        })

    if not fire["legs"]:
        return [{"action_type": "re_skip", "reason": "no_tradeable_legs", "key": key,
                 "jump": bucket_climb, "running": running}]

    tree["fired"][key] = {
        "status": "fired",
        "at_utc": iso_utc(now_utc),
        "jump": bucket_climb,
        "ref_source": "metar_prev_high",
        "running_extreme": running,
        "new_bucket_id": new_bucket_id,
        "new_yes_token": new_yes_token,
        "trigger": "metar_new_high",
        "prev_high": prev_f,
    }
    tree["armed"].pop(key, None)
    actions.append({"action_type": "re_fire", **fire})
    return actions
