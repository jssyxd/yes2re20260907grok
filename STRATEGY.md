# STRATEGY — yes2re20260907grok

## Edge thesis (first principles)

Within a city's **IANA local calendar day**, when METAR posts a temperature
**strictly higher than every previous observation today**, that reading is the
**current known daily maximum**.

If that new high lands in a **higher temperature bucket** than the previous
running high:

- Buckets **strictly below** the new-high bucket are dead for the daily HIGH market → buy **NO**
- The **new-high bucket** is the current known max → buy **YES** (only if ask ≥ `yes_min_ask`)

Edge = fact updated by METAR, books may lag.

**Not used as a fire trigger:** TAF TX, market rank-1 “break the favourite”.

## Rules

1. **Direction**: HIGH only (config + strategy hard reject LOW).
2. **Primary signal**: METAR daily new high **and** bucket climb (`run_i > prev_i`).
3. **Baseline**: first obs of the day only establishes running high (no fire).
4. **Same-bucket new high**: skip (`new_high_same_bucket`) — dead set unchanged.
5. **Legs**: cascade NO on dead buckets (depth ≤ `max_bucket_jump`); YES on current high with `min_ask` (default 0.40).
6. **NO:YES notional**: 1:1; NO cap 0.85.
7. **Further climb after fire**: `re_roll_yes` — SELL old YES FAK, NO on prior landing, YES on new high.
8. **Windows**: local hour ≥ `high_fire_local_hour` (14); obs lookback 90min / future 15min.
9. **Safety**: cross-midnight fail-closed, already_fired, duplicate_obs_time, max_open.

## Explicitly disabled

- LOW markets
- B2 sleeve
- TAF / rank-1 as primary break trigger
- YES fills with best_ask < 0.40

## Lottery YES (forbidden)

Execution enforces `yes_min_ask` (default **0.40**). Below floor → `abort_below_min_ask`.
