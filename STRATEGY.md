# STRATEGY — yes2re20260907grok

## Edge thesis

When live METAR proves the daily high has broken the reference extreme (TAF TX preferred, else market rank-1 consensus) by one or more buckets, the broken buckets are dead (NO → ~1 at settlement by temperature monotonicity). Buy NO on dead buckets and YES on the current proven high bucket before the book fully prices the break.

## Rules

1. **Direction**: HIGH only. LOW is rejected at strategy entry and via config `directions_enabled`.
2. **Reference**: TAF TX if available (converted to market unit); else 1–2h rank-1 YES consensus.
3. **Jump**: up to `max_bucket_jump` (default 3). Market-ref oversized jumps still filtered when configured; TAF multi-jump allowed for cascade.
4. **Legs**:
   - NO on every broken bucket (cascade), cap **0.85**.
   - YES only on **current** METAR-proven high bucket, cap from config, **min_ask 0.40** (no lottery).
   - Notional NO:YES = **1:1**.
5. **One logical fire per** `city|date|high` session (already_fired). Further breaks that require rolling YES are future work on top of this base; initial release fires cascade once per session.
6. **Windows**: high local hour ≥ 14; obs sanity lookback 90min / future 15min.
7. **Consensus**: broken bucket should be long-horizon rank-1 (configurable samples/window).
8. **Execution**: in-memory L2 FAK, 8s budget, abort above cap.

## Explicitly disabled

- LOW markets
- B2 pre-breach sleeve
- YES fills below 0.40 ask
- Live / wallet paths

## Safety (from 2026-09 paper incidents)

- `stale_market_date` fail-closed (bad tz → skip, never self-disable)
- prune keeps `fired` marker while position still open (prevents cross-midnight re-fire loop)
- `already_fired` / duplicate_obs_time guards
- book warm path before fire when available

## What this release does *not* yet fully automate

Rolling YES on **further** breaks within the same session (sell prior YES FAK → buy new dead NO → buy newest YES) is specified as the intended behaviour; the first ship focuses on correct first-fire cascade + safety. Track as follow-up if paper evidence shows frequent multi-step highs.
