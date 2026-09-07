"""CLI paper dashboard: equity / PnL / win-rate / open MTM.

Usage:
  python3 -m tools.paper_dashboard
  python3 -m tools.paper_dashboard --state data/yes2re_state.json
  python3 reversal_runner.py status --pnl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# allow running from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_mtm import compute_paper_pnl


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="yes2re20260907grok paper PnL dashboard")
    ap.add_argument("--state", default="data/yes2re_state.json")
    ap.add_argument("--config", default="config/yes2re_reversal.json")
    ap.add_argument("--json", action="store_true", help="raw JSON output")
    args = ap.parse_args(argv)

    state_path = Path(args.state)
    if not state_path.exists():
        print(f"state not found: {state_path}", file=sys.stderr)
        return 1
    state = json.loads(state_path.read_text())
    initial = None
    cfg_path = Path(args.config)
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
            initial = cfg.get("paper_initial_capital_usdc")
        except Exception:
            pass
    pnl = compute_paper_pnl(state, books_by_token=None, initial_capital=initial)
    if args.json:
        print(json.dumps(pnl, indent=2))
        return 0

    print("=== yes2re20260907grok Paper Dashboard ===")
    print(f"  Initial capital : {pnl['initial_capital_usdc']} USDC")
    print(f"  Cash            : {pnl['cash_usdc']} USDC")
    print(f"  Net debit       : {pnl['net_debit_usdc']} USDC")
    print(f"  Realized PnL    : {pnl['realized_pnl_usdc']} USDC")
    print(f"  Unrealized PnL  : {pnl['unrealized_pnl_usdc']} USDC")
    print(f"  Total PnL       : {pnl['total_pnl_usdc']} USDC")
    print(f"  Equity (MTM)    : {pnl['equity_usdc']} USDC")
    print(f"  Open legs       : {pnl['open_legs']}")
    print(f"  Settled (cost>0): {pnl['settled_legs_with_cost']}")
    print(f"  Won / Lost      : {pnl['won_legs']} / {pnl['lost_legs']}")
    print(f"  Win rate        : {pnl['win_rate']}")
    print("  By side:")
    for side, s in pnl["by_side"].items():
        print(f"    {side}: settled={s['settled']} won={s['won']} lost={s['lost']} "
              f"realized={s['realized_pnl_usdc']} wr={s['win_rate']}")
    if pnl["open_positions_mtm"]:
        print("  Open MTM detail:")
        for row in pnl["open_positions_mtm"][:20]:
            print(f"    {row['key']} {row['leg']} {row['outcome']} "
                  f"sh={row['shares']} cost={row['cost']} mark={row['mark']} mtm={row['mtm']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
