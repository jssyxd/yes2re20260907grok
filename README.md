# yes2re20260907grok

Paper-only Polymarket daily **HIGH temperature** reversal bot.

Fork lineage: `weatherbotyes2re@c575d93` + cross-midnight / TAF / book / dedupe safety hardening.

**No live orders. No wallet. Paper only.**

## Strategy (locked)

- **HIGH only** — LOW markets hard-rejected (config + strategy).
- **NO cap 0.85** (unified, all jumps).
- **Multi-jump cascade**: on a break of N buckets, buy NO on each broken bucket; buy YES only on the **current METAR-proven latest high bucket**.
- **NO:YES notional = 1:1**.
- **No lottery YES**: `yes_min_ask = 0.40` — YES leg aborted below this.
- **No B2 sleeve** (code path removed / disabled).
- Safety: stale_market_date guard (fail-closed), prune keeps open fired markers, already_fired per session_key, obs sanity window (90min/15min).

First priority: profitability. Second: fast fills with high win-rate evidence.

## Run (paper)

```bash
export CHECKWX_API_KEY=...   # optional; AWC works keyless (no TAF)
python3 tests_reversal.py
python3 reversal_runner.py run --config config/yes2re_reversal.json
python3 reversal_runner.py status
python3 -m tools.paper_dashboard
```

## Config knobs

| Key | Default | Note |
|-----|---------|------|
| `paper_initial_capital_usdc` | 1000 | |
| `fire_budget_usdc` | 60 | ≤ 6% of 1000 |
| `max_open_positions` | 0 | 0 = unlimited; cash until settlement releases |
| `strategy.no_max_ask` | 0.85 | |
| `strategy.yes_min_ask` | 0.40 | lottery floor |
| `strategy.max_bucket_jump` | 3 | cascade depth |
| `directions_enabled` | ["high"] | |

## Paper PnL

- `health.json` updated each cycle (when wired).
- `python3 -m tools.paper_dashboard` — equity, realized/unrealized, win rate, NO/YES split.
- `reversal_runner.py status` includes `pnl` block.

Equity ≈ initial − net_debit + unrealized MTM (open legs marked to book when available).

## Layout

- `reversal_strategy.py` — arm/fire state machine + guards
- `_r_cycle.py` / `_r_exec.py` / `_r_state.py` — dual-rate loop, settle, state
- `re_execution.py` — capped FAK ladder
- `paper_mtm.py` / `tools/paper_dashboard.py` — MTM + CLI
- `config/yes2re_reversal.json` — runtime config

See [STRATEGY.md](STRATEGY.md) for rules detail.
