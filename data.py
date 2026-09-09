"""DexScreener data layer: discover fresh Solana memecoins and snapshot them.

We deliberately build on DexScreener rather than GMGN's reverse-engineered
endpoints: it is free, keyless, and not behind Cloudflare, so the paper loop
stays reliable. GMGN-specific signals (smart-money, holders) can be layered on
later without changing the engine.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Optional

import requests

from config import EXECUTION

BASE = "https://api.dexscreener.com"
HEADERS = {"User-Agent": "gmgn-paper/0.1 (research paper-trading bot)"}


@dataclass
class TokenState:
    """A single token's market state at one point in time."""

    ts: float                 # unix seconds when observed
    address: str
    symbol: str
    price_usd: float
    liquidity_usd: float
    volume_h1: float
    volume_m5: float
    change_m5: float
    change_h1: float
    change_h24: float
    age_minutes: float
    pair_address: str
    dex: str
    buys_m5: int = 0
    sells_m5: int = 0
    buys_h1: int = 0
    sells_h1: int = 0
    market_cap: float = 0.0   # what GMGN charts show; falls back to FDV

    # --- on-chain safety (populated by safety.attach_safety; see safety.py) --
    # Default "unknown": old snapshots and un-enriched tokens carry safety_known
    # =False, so a strategy that doesn't require safety behaves exactly as before.
    safety_known: bool = False
    mint_renounced: Optional[bool] = None    # dev can't mint more supply
    freeze_renounced: Optional[bool] = None  # dev can't freeze wallets (honeypot)
    lp_locked_pct: Optional[float] = None     # % of LP locked/burned
    rug_score: Optional[float] = None         # RugCheck 0=clean..100=risky
    rugged_flag: Optional[bool] = None        # already flagged as rugged

    def buy_ratio_h1(self) -> float:
        tot = self.buys_h1 + self.sells_h1
        return (self.buys_h1 / tot) if tot > 0 else 0.5

    def buy_ratio_m5(self) -> float:
        tot = self.buys_m5 + self.sells_m5
        return (self.buys_m5 / tot) if tot > 0 else 0.5

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TokenState":
        fields = cls.__dataclass_fields__
        return cls(**{k: d[k] for k in fields if k in d})


def _get(path: str, timeout: int = 12) -> Optional[object]:
    try:
        r = requests.get(BASE + path, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def _num(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def discover_addresses(chain: str = EXECUTION.chain, limit: int = 30) -> list[str]:
    """Latest token profiles for the chain -> candidate addresses.

    Many carry the `pump` suffix (pump.fun) which is exactly the meme universe.
    """
    profiles = _get("/token-profiles/latest/v1") or []
    addrs: list[str] = []
    for p in profiles:
        if p.get("chainId") == chain and p.get("tokenAddress"):
            addrs.append(p["tokenAddress"])
    # boosted list adds a few more actively-promoted tokens
    boosts = _get("/token-boosts/latest/v1") or []
    for b in boosts:
        if b.get("chainId") == chain and b.get("tokenAddress"):
            addrs.append(b["tokenAddress"])
    # de-dup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            out.append(a)
        if len(out) >= limit:
            break
    return out


def _pick_best_pair(pairs: list[dict], chain: str) -> Optional[dict]:
    """Choose the most liquid pair on the target chain for a token."""
    cands = [p for p in pairs if p.get("chainId") == chain]
    if not cands:
        return None
    return max(cands, key=lambda p: _num((p.get("liquidity") or {}).get("usd")))


def fetch_states(addresses: list[str], chain: str = EXECUTION.chain,
                 now: Optional[float] = None) -> list[TokenState]:
    """Fetch current market state for a batch of token addresses.

    DexScreener's /tokens endpoint accepts up to 30 comma-separated addresses.
    """
    if not addresses:
        return []
    now = now or time.time()
    states: list[TokenState] = []
    for i in range(0, len(addresses), 30):
        batch = addresses[i:i + 30]
        data = _get("/latest/dex/tokens/" + ",".join(batch))
        pairs = (data or {}).get("pairs") or []
        # group pairs by base token address
        by_token: dict[str, list[dict]] = {}
        for p in pairs:
            addr = ((p.get("baseToken") or {}).get("address") or "").strip()
            if addr:
                by_token.setdefault(addr, []).append(p)
        for addr, plist in by_token.items():
            p = _pick_best_pair(plist, chain)
            if not p:
                continue
            liq = _num((p.get("liquidity") or {}).get("usd"))
            vol = p.get("volume") or {}
            chg = p.get("priceChange") or {}
            txns = p.get("txns") or {}
            tx_m5 = txns.get("m5") or {}
            tx_h1 = txns.get("h1") or {}
            created = p.get("pairCreatedAt")
            age_min = ((now * 1000 - created) / 60_000.0) if created else 1e9
            states.append(TokenState(
                ts=now,
                address=addr,
                symbol=(p.get("baseToken") or {}).get("symbol") or "?",
                price_usd=_num(p.get("priceUsd")),
                liquidity_usd=liq,
                volume_h1=_num(vol.get("h1")),
                volume_m5=_num(vol.get("m5")),
                change_m5=_num(chg.get("m5")),
                change_h1=_num(chg.get("h1")),
                change_h24=_num(chg.get("h24")),
                age_minutes=age_min,
                pair_address=p.get("pairAddress") or "",
                dex=p.get("dexId") or "",
                buys_m5=int(_num(tx_m5.get("buys"))),
                sells_m5=int(_num(tx_m5.get("sells"))),
                buys_h1=int(_num(tx_h1.get("buys"))),
                sells_h1=int(_num(tx_h1.get("sells"))),
                market_cap=_num(p.get("marketCap")) or _num(p.get("fdv")),
            ))
        time.sleep(0.25)  # be polite to the API
    return states


def snapshot(chain: str = EXECUTION.chain, limit: int = 30) -> list[TokenState]:
    """One full discovery + state fetch cycle."""
    addrs = discover_addresses(chain=chain, limit=limit)
    return fetch_states(addrs, chain=chain)
