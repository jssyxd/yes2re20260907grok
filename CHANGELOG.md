# Changelog — yes2re20260907grok

## 2026-09-07 — Trigger rewrite: pure METAR daily new-high (first principles)

- **Primary fire signal is only** “METAR posts a temperature strictly above
  every prior obs today, and the new high is in a higher bucket than the
  previous running high.”
- Removed TAF / market rank-1 as the primary “break the favourite” trigger
  (they were causing dust-bucket fires such as kuala-lumpur @0.001 YES).
- First obs of the day = baseline only (no fire). Same-bucket new high = skip.
- Cascade NO on dead buckets below the new high; YES on the new-high bucket
  with execution `yes_min_ask` still enforced.
- Further bucket climb after fire → `re_roll_yes` (unchanged intent).
- Config: `require_consensus_filter=false`, `allow_market_consensus_reference=false`,
  `trigger=metar_daily_new_high`.
- Unit tests rewritten for the new semantics; 7/7 PASS.


## 2026-09-07 — Lottery YES blocked; min_ask enforced at execution

- **Root cause (sandbox fire `kuala-lumpur|2026-09-07|high`)**: strategy attached
  `min_ask=0.40` on the YES leg, but `re_execution.plan_leg_attempts` never
  read it — FAK walked a **0.001** dust ask and paper-filled 35.29 shares.
- **Fix**: BUY legs with `min_ask` now return `abort_below_min_ask` when
  `best_ask < min_ask` (stand down; no fill). Config `strategy.yes_min_ask`
  remains **0.40**.
- **Why that fire was not the intended edge**: ref_source was `market_rank1`,
  and earlier the same session’s consensus sample showed rank-1 YES **TWAP
  already 0.001** (thin lead, n_samples=2). The “new high” bucket was already
  priced as dead/dust — not a contested re-rating after a solid TAF/consensus
  high held for 1–2h. True edge requires a high-priced consensus high bucket
  *then* METAR break; dust rank-1 is noise, not opportunity.
- Further-break YES roll (`re_roll_yes`) and cascade NO remain as previously
  shipped.

## Upstream history (weatherbotyes2re lineage)


## 2026-09-04 — Fire deadlock fix; WS live feed; paper-ledger fix (audited)

- **obs sanity window (was: absolute 180 s age gate → structurally zero fires).**
  METAR/SPECI obs_time age swings 0-60 min on hourly cadence (US AWS publish
  ~7 min early); `require_fresh_obs_seconds=180` made `stale_obs` block every
  fire. Replaced with sanity window `max_obs_lookback_seconds=5400` /
  `max_obs_future_seconds=900`: any NEW observation (deduped by
  `is_new_obs_time`) may fire unless the feed is >90 min behind or the stamp
  is >15 min in the future. First live fire within 27 min of deploy.
- **Full skip audit.** `_r_cycle` no longer silently drops skips: every
  re_skip / re_skip_yes / re_disarm is logged with reason/jump/consensus
  (silent skips previously hid the 0-fire deadlock).
- **NO cap 0.65 → 0.85** (broken-bucket NO redeems ~1.0; wider cap = fills);
  YES leg cap unchanged 0.48.
- **Universe: 10 → all 49 cities** (drop `active_icaos` allowlist; both high
  & low directions). `idle_metar_interval_seconds` 45 → 60 (49 cities = 3
  CheckWX batches; 4320 req/day < 5000 paid cap).
- **Market WebSocket live** (`market_ws_transport.py` stdlib-only WS client
  through the CONNECT proxy + `ws_bridge.py` daemon thread). 2000+ tokens
  subscribed; fresh (<5 s) WS LocalOrderBook snapshots overlay the ladder
  cache (epoch-guarded, never clobbers newer REST data); auto-reconnect
  5/10/30 s; REST /books remains the correctness backbone (the public market
  channel is near-frozen per py-clob-client #292 — WS is an accelerator).
- **Paper ledger fix.** `release()` no longer clamps total debit to zero —
  a negative debit is realized profit (equity = initial − debit). The clamp
  had silently discarded +52.80 USDC of paper profit (cost 49.06 vs payout
  101.86). `total_debit_usdc()` now reads negative values directly instead of
  through the `parsed >= 0` filter.
- **Audit hardening (pi + omp cross-review 2026-09-04):** `ensure_tokens`
  and `mark_disconnected` thread-safety (dict-size-change race during
  reconnect); `_ws_pump` epoch comparison made real (docstring now honest).
- Verified: 7/7 scenario tests; equity 1000 → 1052.79 after first US market
  settlements (NO legs 4/4 wins; one YES lottery leg lost).

## 2026-09-03 — Dual-rate paper runner; no σ; real-API soak

- **Zero σ / bias / fade-NO / dead-NO / BUY-YES** on the run path.
- Dual-rate METAR/books (ARM ~8s; idle METAR ~45s + consensus books ~30s).
- Dual-source METAR (CheckWX + AWC); C/F via `c_to_market_unit`; Gamma rules cache 20min.
- Modules: `runner_impl.py`, `_r_globals.py`, `_r_state.py`, `_r_data.py`, `_r_cycle.py`, `_r_exec.py`.
- **10-min paper soak (real CheckWX+Gamma+CLOB):** 49/49 METAR, 98 rules, Atlanta+Denver ARMed, 0 FIRE, capital 1000 USDC, no cycle_error.

## 2026-09-02 — Merge poly-yes2 paper infra; drop σ

- Not merged: TAF/σ arms. This repo is strategy + paper runtime.
