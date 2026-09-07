# STRATEGY — yes2re20260907grok

## Edge thesis (first principles)

Within a city's **IANA local calendar day**, when METAR posts a temperature
**strictly higher than every previous observation today**, that reading is the
**current known daily maximum**.

If that new high lands in a **higher temperature bucket** than the previous
running high:

- Buckets **strictly below** the new-high bucket are dead → buy **NO**
- The **new-high bucket** is the current known max → buy **YES** (ask ≥ 0.40)

## Hard gates (2026-09-07 audit)

1. **Local open window**: hour in **[14, 17)** only — no new fire after 17:00 local.
2. **yes_min_ask = 0.40** — dust YES aborted at execution.
3. **yes_requires_no_fill = true** — if no NO shares fill, YES is discarded (no unhedged YES).
4. **min_fire_notional_usdc = 5.0** — total fill cost below $5 fails the fire.
5. YES only on the bucket that **contains** the current running high.

## Primary trigger

METAR daily new high + bucket climb. Not TAF / market rank-1.

## Explicitly disabled

- LOW markets, B2 sleeve, lottery YES, sub-$5 fires, post-17:00 local opens.
