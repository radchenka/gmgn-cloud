#!/usr/bin/env python3
"""
Облачный вход (Railway): поднимает producer + agent + web в одном контейнере.
- web слушает 0.0.0.0:$PORT (Railway健康-чек порта)
- producer и agent крутятся фоновыми процессами
Всё на публичных keyless-API (DexScreener/GeckoTerminal). Никаких секретов.

Настройки через переменные окружения (все опциональны):
  RULE=A            правило выхода (A: TP+25%/SL-50%, B: TP+40%/SL-50%)
  ENTRY=market-below  нет отката за 5м: market-below | skip | market
  POLL=5            интервал опроса цены, сек
  SCAN=60           интервал скана рынка продюсером, сек
  DASH_TOKEN=...    если задан — дашборд требует ?token=... (иначе открыт)
"""
import os, sys, subprocess, time, signal, atexit

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
RULE = os.environ.get("RULE", "A")
ENTRY = os.environ.get("ENTRY", "skip")   # A2: лимит -10%, скип если нет отката
POLL = os.environ.get("POLL", "5")
SCAN = os.environ.get("SCAN", "60")

procs = []

def spawn(args):
    p = subprocess.Popen([PY, "-u"] + args, cwd=HERE)
    procs.append(p)
    return p

def shutdown(*_):
    for p in procs:
        try: p.terminate()
        except Exception: pass
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)
atexit.register(lambda: [p.terminate() for p in procs])

print(f"[cloud] старт: producer(scan={SCAN}) + multi (9 стратегий, poll={POLL}) + web",
      flush=True)
spawn(["producer.py", SCAN])
time.sleep(1)
spawn(["multi.py"])            # мульти-стратегийный движок (9 вкладок)
time.sleep(1)

# web в foreground, слушает 0.0.0.0:$PORT
os.environ["HOST"] = "0.0.0.0"
import web
web.main()
