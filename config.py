"""Central configuration and tunable strategy parameters.

`StrategyParams` is the object the optimizer searches over. Everything the
strategy needs to make a decision lives here so a single dict fully describes a
strategy variant — that is what makes replay-based tuning reproducible.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- paths -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SNAPSHOTS_FILE = DATA_DIR / "snapshots.jsonl"   # one market snapshot per line
TRADES_FILE = DATA_DIR / "trades.jsonl"         # closed trades log
STATE_FILE = DATA_DIR / "wallet_state.json"     # live wallet persistence
BEST_PARAMS_FILE = DATA_DIR / "best_params.json"
SCALP_PARAMS_FILE = DATA_DIR / "scalp_params.json"   # scalp preset persistence

DATA_DIR.mkdir(parents=True, exist_ok=True)


# --- market / execution realism -------------------------------------------
@dataclass
class ExecutionConfig:
    """How we simulate a real GMGN fill. These are costs, not strategy knobs."""

    chain: str = "solana"
    quote_ccy: str = "USD"           # we account PnL in USD for simplicity
    start_balance_usd: float = 1_000.0

    swap_fee_pct: float = 0.01       # GMGN ~1% per side
    priority_fee_usd: float = 0.20   # fixed network/priority cost per trade
    # Price-impact model: slippage_pct = impact_k * trade_usd / liquidity_usd
    impact_k: float = 0.9
    max_slippage_pct: float = 0.35   # cap; above this we treat fill as failed

    # --- scam / exit realism (memecoins) ---------------------------------
    # A "rug" = liquidity collapses vs entry. When current liquidity drops
    # below entry_liq * rug_liquidity_frac, the displayed price is fiction:
    # you can only dump into a dead pool and recover a tiny fraction.
    rug_liquidity_frac: float = 0.35
    rug_recovery_frac: float = 0.08     # of notional value you can salvage
    # If a held token vanishes from data for this many cycles, assume it was
    # delisted / rug-pulled and mark it near-zero.
    stale_cycles_to_rug: int = 3
    # Sell fills are worse than buys: fast dumping + sandwich/MEV on exit.
    sell_slippage_mult: float = 1.5     # multiply sell price-impact
    # Honeypot heuristic: heavy buys but ~no sells in the pool => can't exit.
    honeypot_min_sells_h1: int = 3      # below this (with buys) => trapped


# --- strategy knobs (the search space) ------------------------------------
@dataclass
class StrategyParams:
    """Tunable momentum parameters. The optimizer mutates these."""

    # universe filters
    min_liquidity_usd: float = 15_000.0
    max_liquidity_usd: float = 2_000_000.0
    min_volume_h1_usd: float = 20_000.0
    max_age_hours: float = 48.0       # only fresh tokens
    min_age_minutes: float = 10.0     # avoid the first chaotic minutes

    # entry signal
    min_vol_liq_ratio: float = 1.0    # 1h volume / liquidity (churn)
    min_price_change_m5: float = 1.0  # % in last 5m
    min_price_change_h1: float = 5.0  # % in last 1h
    max_price_change_h1: float = 300.0  # skip already-parabolic (likely top)
    min_buy_ratio_h1: float = 0.52    # buyers must outweigh sellers (1h)
    min_buy_ratio_m5: float = 0.50    # fresh buy pressure (5m)

    # position sizing / risk
    position_frac: float = 0.10       # fraction of wallet per entry
    max_open_positions: int = 5

    # exits
    take_profit_pct: float = 40.0
    stop_loss_pct: float = 18.0
    trailing_stop_pct: float = 15.0   # from peak after in profit
    trailing_arm_pct: float = 12.0    # arm trailing once up this much
    max_hold_minutes: float = 180.0

    # --- scalp add-ons (kucoin-style quick management) -------------------
    # All default to 0 == OFF, so the base momentum strategy is unchanged.
    # A "scalp" variant (see scalp_params()) turns these on for short trades
    # that grab a small profit fast and only let the occasional runner ride.
    quick_fix_pct: float = 0.0        # take profit once up this % ...
    quick_fix_after_min: float = 0.0  # ... but only after holding this long
    breakeven_arm_pct: float = 0.0    # once peak hits this %, protect entry
    breakeven_lock_pct: float = 0.0   # ... by exiting if we fall back to here
    # Give-back trailing: exit after surrendering this FRACTION of peak profit
    # (kucoin's "трейл" rule), gated by trailing_arm_pct. 0 == off.
    trailing_give_back_frac: float = 0.0

    # --- on-chain anti-rug entry gate (see safety.py) --------------------
    # All permissive by default so the base strategy and old (un-enriched)
    # snapshots are unaffected. The scalp preset turns these on.
    require_safety_data: bool = False    # if True, reject tokens w/o a report
    require_mint_renounced: bool = False
    require_freeze_renounced: bool = False
    min_lp_locked_pct: float = 0.0       # reject if LP locked below this %
    max_rug_score: float = 1e9           # reject if RugCheck score above this

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyParams":
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)

    def save(self, path: Path = BEST_PARAMS_FILE) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path = BEST_PARAMS_FILE) -> "StrategyParams":
        if path.exists():
            return cls.from_dict(json.loads(path.read_text()))
        return cls()


EXECUTION = ExecutionConfig()


def scalp_params() -> "StrategyParams":
    """Short-trade "scalp" preset: grab 5-30% per trade, fast.

    Philosophy (ported from the KuCoin scalper, scaled to memecoin volatility):
      * hard take-profit capped low (30%) — no moonshot greed;
      * quick_fix: bank a small win (~7%) once we've held a couple of minutes,
        so most trades close fast and green;
      * breakeven: after a +10% peak, never let a winner turn into a loss;
      * give-back trailing: once armed, exit if we surrender 45% of peak profit,
        which is what lets an occasional runner ride toward the 30% cap;
      * short max_hold (25 min) — memecoin momentum is minutes, not hours.
    Every exit still pays fees + slippage and respects rug/honeypot in the
    engine, so PnL is what you'd realistically keep.
    """
    return StrategyParams(
        # tighter, fresher universe for fast momentum
        min_liquidity_usd=20_000.0,
        max_liquidity_usd=1_500_000.0,
        min_volume_h1_usd=30_000.0,
        max_age_hours=24.0,
        min_age_minutes=10.0,
        min_vol_liq_ratio=1.2,
        min_price_change_m5=1.5,
        min_price_change_h1=5.0,
        max_price_change_h1=250.0,
        min_buy_ratio_h1=0.53,
        min_buy_ratio_m5=0.52,
        # sizing: $1000 wallet, ~$100/trade, up to 5 concurrent
        position_frac=0.10,
        max_open_positions=5,
        # exits: the 5-30% band
        take_profit_pct=30.0,      # hard cap / ceiling
        stop_loss_pct=12.0,        # tight risk per trade
        trailing_stop_pct=0.0,     # replaced by give-back trailing below
        trailing_arm_pct=10.0,     # arm trailing/give-back after +10%
        max_hold_minutes=25.0,     # short trades
        # scalp add-ons
        quick_fix_pct=7.0,         # bank ~7% fast ...
        quick_fix_after_min=2.0,   # ... after 2 min in the trade
        breakeven_arm_pct=10.0,    # after +10% peak ...
        breakeven_lock_pct=1.0,    # ... exit if we fall back to +1%
        trailing_give_back_frac=0.45,  # exit if 45% of peak profit given back
        # anti-rug gate: only trade tokens that pass an on-chain safety check
        require_safety_data=True,      # no report => don't touch it
        require_mint_renounced=True,   # dev can't print more supply
        require_freeze_renounced=True, # dev can't freeze wallets
        min_lp_locked_pct=90.0,        # LP must be ~fully locked/burned
        max_rug_score=25.0,            # RugCheck score must be clean-ish
    )
