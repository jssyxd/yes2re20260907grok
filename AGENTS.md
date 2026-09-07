# Repository guidelines (lean)

Paper-only HIGH temperature reversal bot for Polymarket daily markets.

## Do

- Keep changes paper-safe (no wallet, no live CLOB submit).
- Prefer small, testable edits; run `python3 tests_reversal.py` after strategy changes.
- HIGH-only, no sleeve, yes_min_ask ≥ 0.40, NO cap 0.85, NO:YES 1:1.

## Don’t

- Re-enable LOW or B2 sleeve without an explicit design review.
- Add heavy skill/instruction stacks; this file stays short.
- Commit secrets (`.env`, keys).

## Key entrypoints

- `reversal_runner.py run|once|status`
- `python3 -m tools.paper_dashboard`
- Strategy: `reversal_strategy.py` → `maybe_arm_or_fire`
- Config: `config/yes2re_reversal.json`

## Tests

```bash
python3 tests_reversal.py
```
