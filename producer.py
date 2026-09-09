#!/usr/bin/env python3
"""
producer.py — генерит WINNER-STYLE сигналы ЛОКАЛЬНО и пишет прямо в очередь агента.
Никакого Telegram и api-ключей: это тот же детектор, что и tg_alert_bot.py
(reg.ru просто гоняет его копию). Сигнал = скан свежих pump.fun токенов по
публичным данным DexScreener + твои критерии is_winner_style().

    python3 signals_lab/producer.py           # скан каждые 60с
    python3 signals_lab/producer.py 45        # каждые 45с

Пишет строки в signals_lab/signals_in.jsonl — ту же очередь, что тейлит agent.py.
Дедуп по адресу с кулдауном (как в боте), чтобы не заводить один токен дважды.
"""
import os, sys, json, time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)

from data import snapshot                       # публичный скан токенов
import tg_alert_bot as BOT                       # переиспользуем is_winner_style + критерии

QUEUE = os.path.join(HERE, "signals_in.jsonl")


def to_queue(s):
    """TokenState -> queue dict (тот же контракт, что ждёт agent.py)."""
    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": getattr(s, "symbol", "?"),
        "ca": s.address,
        "buy5m": round(s.buy_ratio_m5(), 3),
        "ratio": round((s.buys_m5 / s.sells_m5), 2) if getattr(s, "sells_m5", 0) else None,
        "h1": round(s.change_h1, 1),
        "h24": round(s.change_h24, 1),
        "liq": round(s.liquidity_usd, 0),
        "age_h": round(s.age_minutes / 60.0, 2),
        "price_usd": s.price_usd,
    }


def enqueue(d):
    with open(QUEUE, "a") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    last = {}                                    # address -> last emit epoch
    print(f"[producer] старт {datetime.now(timezone.utc):%H:%M:%S} UTC | "
          f"скан каждые {interval}с | критерии: buy5m≥{BOT.MIN_BUY_5M} "
          f"liq {BOT.MIN_LIQ:,}-{BOT.MAX_LIQ:,} age≤{BOT.MAX_AGE_MIN}м → {QUEUE}")
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
            hits = [s for s in states if BOT.is_winner_style(s)]
            fired = 0
            for s in sorted(hits, key=lambda x: x.buy_ratio_m5(), reverse=True):
                if now - last.get(s.address, 0) < BOT.COOLDOWN_SEC:
                    continue
                enqueue(to_queue(s))
                last[s.address] = now
                fired += 1
                print(f"  [signal] {s.symbol:12} buy5m={s.buy_ratio_m5():.2f} "
                      f"1h={s.change_h1:+.0f}% liq=${s.liquidity_usd:,.0f} "
                      f"age={s.age_minutes/60:.1f}h")
            print(f"[{n}] {datetime.now(timezone.utc):%H:%M:%S} tracked={len(states)} "
                  f"hits={len(hits)} new={fired}")
        except Exception as e:
            print(f"[{n}] error: {type(e).__name__}: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
