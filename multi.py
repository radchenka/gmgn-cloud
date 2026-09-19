#!/usr/bin/env python3
"""
multi.py — гоняет НЕСКОЛЬКО стратегий параллельно на одних сигналах.

Один продюсер → одна очередь signals_in.jsonl → по каждому сигналу создаётся
сделка в КАЖДОЙ стратегии. Опрос цены общий (один fetch на токен на всех).
У каждой стратегии свои файлы: paper_state_<name>.json / paper_trades_<name>.jsonl
/ events_<name>.jsonl. Веб показывает вкладки по strategies.json.
"""
import os, sys, json, time, datetime
from dataclasses import asdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent as AG
from agent import Trade, live_prices
import data as DATA

# постоянное хранилище: на Railway смонтируй Volume и задай DATA_DIR=/data,
# тогда данные вкладок переживают передеплои. Локально/без Volume — рядом с кодом.
DATA_DIR = os.environ.get("DATA_DIR", HERE)
os.makedirs(DATA_DIR, exist_ok=True)

QUEUE = os.path.join(HERE, "signals_in.jsonl")           # транзиентная труба (рядом с producer)
OFFSET = os.path.join(HERE, ".queue_offset_multi")       # офсет рядом с очередью (эфемерный, сбрасывается вместе)
INDEX = os.path.join(DATA_DIR, "strategies.json")
FLOW = os.path.join(DATA_DIR, "flow_log.jsonl")          # intra-trade поток по токенам

# ---- конфигурации (вкладки) ----
STRATEGIES = [
    {"name": "limit_tp25_w12", "label": "Лимит −10% · TP+25% · 12м",
     "dip": 0.10, "wait": 5, "tp": 0.25, "sl": 0.50, "window": 12, "entry": "skip"},
    {"name": "mkt_tp10_w6",  "label": "Рынок · TP+10% · 6м",
     "dip": 0, "wait": 0, "tp": 0.10, "sl": 0.50, "window": 6, "entry": "market"},
    {"name": "mkt_tp10_w8",  "label": "Рынок · TP+10% · 8м",
     "dip": 0, "wait": 0, "tp": 0.10, "sl": 0.50, "window": 8, "entry": "market"},
    {"name": "mkt_tp12_w6",  "label": "Рынок · TP+12% · 6м",
     "dip": 0, "wait": 0, "tp": 0.12, "sl": 0.50, "window": 6, "entry": "market"},
    {"name": "mkt_tp12_w8",  "label": "Рынок · TP+12% · 8м",
     "dip": 0, "wait": 0, "tp": 0.12, "sl": 0.50, "window": 8, "entry": "market"},
    {"name": "mkt_tp15_w6",  "label": "Рынок · TP+15% · 6м",
     "dip": 0, "wait": 0, "tp": 0.15, "sl": 0.50, "window": 6, "entry": "market"},
    {"name": "mkt_tp15_w8",  "label": "Рынок · TP+15% · 8м",
     "dip": 0, "wait": 0, "tp": 0.15, "sl": 0.50, "window": 8, "entry": "market"},
    {"name": "mkt_tp15_w10", "label": "Рынок · TP+15% · 10м",
     "dip": 0, "wait": 0, "tp": 0.15, "sl": 0.50, "window": 10, "entry": "market"},
    {"name": "mkt_tp15_w12", "label": "Рынок · TP+15% · 12м",
     "dip": 0, "wait": 0, "tp": 0.15, "sl": 0.50, "window": 12, "entry": "market"},
    {"name": "mkt_tp20_w6",  "label": "Рынок · TP+20% · 6м",
     "dip": 0, "wait": 0, "tp": 0.20, "sl": 0.50, "window": 6, "entry": "market"},
    {"name": "mkt_tp20_w8",  "label": "Рынок · TP+20% · 8м",
     "dip": 0, "wait": 0, "tp": 0.20, "sl": 0.50, "window": 8, "entry": "market"},
    {"name": "mkt_tp25_w12", "label": "Рынок · TP+25% · 12м",
     "dip": 0, "wait": 0, "tp": 0.25, "sl": 0.50, "window": 12, "entry": "market"},
    {"name": "mkt_tp25_w20", "label": "Рынок · TP+25% · 20м",
     "dip": 0, "wait": 0, "tp": 0.25, "sl": 0.50, "window": 20, "entry": "market"},
]

STAKE = AG.STAKE_USD


def paths(name):
    return (os.path.join(DATA_DIR, f"paper_state_{name}.json"),
            os.path.join(DATA_DIR, f"paper_trades_{name}.jsonl"),
            os.path.join(DATA_DIR, f"events_{name}.jsonl"))

def reset_flag(name):
    return os.path.join(DATA_DIR, f".reset_{name}")


def load_journal(name):
    _, tr, _ = paths(name); out = []
    if os.path.exists(tr):
        for ln in open(tr):
            ln = ln.strip()
            if ln:
                try: out.append(json.loads(ln))
                except Exception: pass
    return out


def load_events(name, limit=80):
    _, _, ev = paths(name); out = []
    if os.path.exists(ev):
        try:
            for ln in open(ev).read().splitlines()[-limit:]:
                ln = ln.strip()
                if ln: out.append(json.loads(ln))
        except Exception: pass
    return out


def make_trade(sig, cfg, signal_price, liq):
    ts = sig.get("_ts_epoch") or time.time()
    t = Trade(ca=sig["ca"], symbol=sig.get("symbol", "?"), signal_ts=ts,
              signal_price=signal_price, liq_usd=liq, rule=cfg["name"],
              dip=cfg["dip"], wait_min=cfg["wait"], tp=cfg["tp"], sl=cfg["sl"],
              window_min=cfg["window"], stake=STAKE)
    t.executor = AG._PAPER
    t.entry_mode = cfg["entry"]
    return t


def write_state(cfg, active, prices, closed, events):
    state, _, _ = paths(cfg["name"])
    now = time.time()
    act = []
    for t in active:
        p, _ = prices.get(t.ca, (0, 0))
        base = t.entry_price or t.signal_price
        unreal = (p / base - 1) if (p and base) else 0.0
        act.append({"symbol": t.symbol, "ca": t.ca, "rule": t.rule, "state": t.state,
                    "signal_price": t.signal_price, "limit_price": t.limit_price,
                    "entry_price": t.entry_price, "current_price": p,
                    "filled_limit": t.filled_limit, "unreal_pct": unreal,
                    "tp_price": t.tp_price, "sl_price": t.sl_price,
                    "age_min": (now - t.signal_ts) / 60.0})
    snap = {"updated_at": now, "name": cfg["name"], "label": cfg["label"],
            "stake_usd": STAKE,
            "rule_params": {"tp": cfg["tp"], "sl": cfg["sl"],
                            "window_min": cfg["window"], "entry": cfg["entry"],
                            "dip": cfg["dip"]},
            "active": act, "summary": AG._summary(closed),
            "recent": closed[-100:][::-1], "events": events[-80:][::-1]}
    tmp = state + ".tmp"
    with open(tmp, "w") as f: json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, state)


def run(poll=None):
    try: sys.stdout.reconfigure(line_buffering=True)
    except Exception: pass
    poll = poll or AG.POLL_SEC
    json.dump([{"name": s["name"], "label": s["label"]} for s in STRATEGIES],
              open(INDEX, "w"), ensure_ascii=False)
    print(f"[multi] {len(STRATEGIES)} стратегий, poll={poll}s, очередь={QUEUE}", flush=True)
    S = {}
    for cfg in STRATEGIES:
        S[cfg["name"]] = {"cfg": cfg, "active": [], "closed": load_journal(cfg["name"]),
                          "events": load_events(cfg["name"])}
        write_state(cfg, [], {}, S[cfg["name"]]["closed"], S[cfg["name"]]["events"])

    def emit(st, kind, **d):
        rec = {"t": datetime.datetime.now(datetime.timezone.utc).isoformat(), "kind": kind, **d}
        _, _, evf = paths(st["cfg"]["name"])
        try:
            with open(evf, "a") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception: pass
        st["events"].append(rec)

    off = 0
    if os.path.exists(OFFSET):
        try: off = int(open(OFFSET).read().strip())
        except Exception: off = 0

    while True:
        # 0) запросы сброса вкладок (веб пишет флаг .reset_<name>)
        for name, st in S.items():
            fl = reset_flag(name)
            if os.path.exists(fl):
                st["active"] = []; st["closed"] = []; st["events"] = []
                state, trf, evf = paths(name)
                for p in (trf, evf):
                    try: open(p, "w").close()
                    except Exception: pass
                try: os.remove(fl)
                except Exception: pass
                write_state(st["cfg"], [], {}, [], [])
                print(f"[reset] вкладка {name} сброшена", flush=True)

        # 1) ingest new signals -> сделка в каждой стратегии
        if os.path.exists(QUEUE):
            with open(QUEUE) as f:
                f.seek(off)
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try: sig = json.loads(line)
                    except Exception: continue
                    sym = sig.get("symbol", "?")
                    # протухший сигнал?
                    try:
                        age = (time.time() - datetime.datetime.fromisoformat(sig["ts_utc"]).timestamp()) / 60.0
                        if age > 20:
                            continue
                    except Exception: pass
                    px = live_prices([sig["ca"]]).get(sig["ca"], (0, 0))
                    if px[0] <= 0: continue
                    sig["_ts_epoch"] = time.time()
                    for name, st in S.items():
                        t = make_trade(sig, st["cfg"], px[0], px[1])
                        st["active"].append(t)
                        emit(st, "signal", symbol=t.symbol, ca=t.ca[:8],
                             signal_ts=sig.get("ts_utc"), seen_price=round(t.signal_price, 10),
                             limit=round(t.limit_price, 10), buy5m=sig.get("buy5m"), liq=sig.get("liq"))
                    print(f"[new] {sym} → {len(S)} стратегий", flush=True)
                off = f.tell()
            with open(OFFSET, "w") as fo: fo.write(str(off))

        # 2) общий опрос — полные состояния (цена+ликвидность+поток) на всех
        all_cas = {t.ca for st in S.values() for t in st["active"]}
        prices = {}
        now = time.time()
        if all_cas:
            try:
                states = DATA.fetch_states(list(all_cas))
            except Exception:
                states = []
            for s in states:
                prices[s.address] = (float(s.price_usd or 0), float(s.liquidity_usd or 0))
                # intra-trade поток: пишем раз на токен за тик (для будущей модели выхода)
                try:
                    with open(FLOW, "a") as f:
                        f.write(json.dumps({
                            "t": round(now, 1), "ca": s.address, "symbol": s.symbol,
                            "price": s.price_usd, "liq": s.liquidity_usd,
                            "vol_h1": getattr(s, "volume_h1", None),
                            "buys_m5": getattr(s, "buys_m5", None),
                            "sells_m5": getattr(s, "sells_m5", None),
                        }, ensure_ascii=False) + "\n")
                except Exception:
                    pass
        for name, st in S.items():
            still = []
            for t in st["active"]:
                p, liq = prices.get(t.ca, (0, 0))
                if liq: t.liq_usd = liq
                prev = t.state
                done = t.on_price(p, now)
                if prev == "waiting_fill" and t.state != "waiting_fill" and t.entry_price > 0:
                    emit(st, "entry", symbol=t.symbol,
                         via=("лимит" if t.filled_limit else "рынок"),
                         price=round(t.entry_price, 10),
                         tp=round(t.tp_price, 10), sl=round(t.sl_price, 10))
                if done:
                    if t.exit_reason in ("CANCEL", "NODIP") or t.entry_price <= 0:
                        emit(st, "skip", symbol=t.symbol, reason=t.exit_reason)
                    else:
                        _, trf, _ = paths(name)
                        with open(trf, "a") as f: f.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")
                        st["closed"].append(asdict(t))
                        held = (t.exit_ts - t.entry_ts) / 60.0 if t.entry_ts else 0
                        emit(st, "exit", symbol=t.symbol, reason=t.exit_reason,
                             price=round(t.exit_price, 10), net=round(t.net_ret, 4),
                             pnl=round(t.pnl_usd, 2), held_min=round(held, 1))
                else:
                    still.append(t)
            st["active"] = still
            write_state(st["cfg"], st["active"], prices, st["closed"], st["events"])
        time.sleep(poll)


if __name__ == "__main__":
    run()
