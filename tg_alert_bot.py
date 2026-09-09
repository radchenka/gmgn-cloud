#!/usr/bin/env python3
"""Real-time Telegram alert bot for winner-style memecoins.

Runs standalone (independent of any chat session): polls the live market on an
interval, applies the "winner signature" extracted from past winners, and pushes
a Telegram message the moment a fresh match appears — with dedupe + cooldown so
it never spams.

SETUP (once):
  1. In Telegram, message @BotFather -> /newbot -> copy the bot TOKEN.
  2. Message your new bot anything (so it can DM you), then open
     https://api.telegram.org/bot<TOKEN>/getUpdates and copy your chat "id".
     (or message @userinfobot to get your id)
  3. Export credentials and run:
       export TG_BOT_TOKEN="123456:ABC..."
       export TG_CHAT_ID="123456789"
       python tg_alert_bot.py            # every 60s
       python tg_alert_bot.py 45         # every 45s

Keep it running: `nohup python tg_alert_bot.py > tg_bot.log 2>&1 &`

NOTE: alerts are winner-STYLE matches for study/paper. Walk-forward showed the
strategy is net-negative — treat as signals to test, not guaranteed profit.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from data import snapshot

TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# --- winner signature (extracted from past winners: TORQUE/csvoss/PEPITO...) ---
MIN_BUY_5M = 0.75
MIN_BUY_1H = 0.68
MIN_LIQ = 22_000
MAX_LIQ = 90_000
MIN_VOL_LIQ = 5.0
MIN_CHG_1H = 25.0
MAX_CHG_1H = 400.0
MAX_AGE_MIN = 90.0
MIN_EARLY = 0.40           # 1h change / 24h change (move is recent)
COOLDOWN_SEC = 3600        # don't re-alert the same coin within this window


def _vl(s):
    return s.volume_h1 / s.liquidity_usd if s.liquidity_usd else 0.0


def _early(s):
    return (s.change_h1 / s.change_h24) if s.change_h24 > 0 else 0.0


def is_winner_style(s) -> bool:
    return (
        s.price_usd > 0
        and s.age_minutes <= MAX_AGE_MIN
        and MIN_LIQ <= s.liquidity_usd <= MAX_LIQ
        and _vl(s) >= MIN_VOL_LIQ
        and MIN_CHG_1H <= s.change_h1 <= MAX_CHG_1H
        and s.buy_ratio_m5() >= MIN_BUY_5M
        and s.buy_ratio_h1() >= MIN_BUY_1H
        and _early(s) >= MIN_EARLY
    )


def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        r = requests.post(url, timeout=15, data={
            "chat_id": CHAT_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": "true"})
        return r.status_code == 200
    except requests.RequestException:
        return False


def format_alert(s) -> str:
    return (
        f"🚀 <b>WINNER-STYLE</b>: {s.symbol}\n"
        f"buy5m <b>{s.buy_ratio_m5():.2f}</b> ({s.buys_m5}/{s.sells_m5})  "
        f"buy1h {s.buy_ratio_h1():.2f}\n"
        f"1h {s.change_h1:+.0f}%  24h {s.change_h24:+.0f}%  (EARLY {_early(s):.2f})\n"
        f"liq ${s.liquidity_usd:,.0f}  vol/liq {_vl(s):.1f}  age {s.age_minutes/60:.1f}h\n"
        f"price ${s.price_usd:.6g}\n"
        f"<code>{s.address}</code>\n"
        f"rugcheck.xyz/tokens/{s.address}\n"
        f"⚠ paper/study signal — not financial advice; use a hard stop."
    )


def main():
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    if not TOKEN or not CHAT_ID:
        print("ERROR: set TG_BOT_TOKEN and TG_CHAT_ID env vars first (see header).")
        return 1
    last_alert: dict[str, float] = {}
    ok = send_telegram("✅ gmgn winner-style bot запущен. Ищу в реальном времени…")
    print(f"[bot] started {datetime.now(timezone.utc):%H:%M:%S} UTC | "
          f"interval {interval}s | telegram {'OK' if ok else 'FAILED — check token/chat_id'}")
    n = 0
    while True:
        n += 1
        try:
            states = []
            for _ in range(4):
                states = snapshot()
                if states:
                    break
                time.sleep(8)
            now = time.time()
            hits = [s for s in states if is_winner_style(s)]
            fired = 0
            for s in sorted(hits, key=lambda x: x.buy_ratio_m5(), reverse=True):
                if now - last_alert.get(s.address, 0) < COOLDOWN_SEC:
                    continue
                if send_telegram(format_alert(s)):
                    last_alert[s.address] = now
                    fired += 1
            print(f"[{n}] {datetime.now(timezone.utc):%H:%M:%S} tracked={len(states)} "
                  f"style-hits={len(hits)} sent={fired}")
        except Exception as e:  # keep the bot alive no matter what
            print(f"[{n}] error: {type(e).__name__}: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
