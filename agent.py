#!/usr/bin/env python3
"""
agent.py — paper-агент для WINNER-STYLE сигналов (20-мин скальп на GMGN).

Правило (из бэктеста, устойчиво out-of-sample):
  вход в КАЖДЫЙ сигнал -> лимитка на откате -DIP% (ждать WAIT мин, иначе рынок)
  выход -> TP / SL / жёсткий таймаут WINDOW мин
  A (спокойный): TP+25% / SL-50%  -> OOS +9.7%/сиг, win 81%
  B (доходный):  TP+40% / SL-50%  -> OOS +12.6%/сиг, win 69%

Издержки берутся из gmgn_paper/config.EXECUTION (единый источник).
Цены живьём — data.fetch_states() (DexScreener). Ничего РЕАЛЬНОГО не покупает:
это пейпер. Реальное исполнение на GMGN пользователь подключает сам.

Режимы:
  python3 signals_lab/agent.py --replay            # прогнать на истории (валидация)
  python3 signals_lab/agent.py --live [--rule B]   # слушать очередь signals_in.jsonl
  python3 signals_lab/agent.py --report            # показать P&L из журнала

Приём live-сигналов: эмиттер (reg.ru) после отправки в Telegram дописывает строку
JSON в signals_lab/signals_in.jsonl:
  {"ts_utc":"...","symbol":"...","ca":"...pump","buy5m":..,"ratio":..,"h1":..,"liq":..,"age_h":..}
Агент тейлит этот файл. (getUpdates не годится — сигналы идут исходящими в личку.)
"""
import os, sys, json, time, csv, datetime, argparse
from dataclasses import dataclass, asdict, field

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config as CFG                       # gmgn_paper/config.py
EXEC = CFG.EXECUTION
from executor import PaperExecutor, GMGNExecutor
_PAPER = PaperExecutor(EXEC)               # дефолтный исполнитель (бумага)

RULES = {
    "A": {"dip": 0.10, "wait_min": 5, "tp": 0.25, "sl": 0.50, "window_min": 20},
    "B": {"dip": 0.10, "wait_min": 5, "tp": 0.40, "sl": 0.50, "window_min": 20},
}
STAKE_USD = 100.0
POLL_SEC = 5     # как часто опрашивать цену в live (сек). Ниже = точнее ловим TP/SL,
                 # но чаще дёргаем DexScreener. Меняется флагом --poll.
# что делать, если откат -10% НЕ наступил за wait_min минут:
#   "market-below"  — войти по рынку, ТОЛЬКО если цена <= сигнала (не выше)  [умолч.]
#   "skip"          — не входить вообще (только чистые откаты -10%)
#   "market"        — войти по рынку всегда, даже выше сигнала (догон)
ENTRY_MODE = "market-below"
# фильтр «перегретых» сигналов: гипотеза — buy5m под 0.90+ = истощение → раг.
# берём только buy5m <= BUY5M_MAX. Настраивается env (Railway Variables).
BUY5M_MAX = float(os.environ.get("BUY5M_MAX", "0.85"))
# аварийный выход по коллапсу ликвидности (раг): если текущая liq упала ниже
# entry_liq * RUG_FRAC — выходим немедленно, не ждём -50% стопа.
RUG_FRAC = float(os.environ.get("RUG_FRAC", "0.5"))

QUEUE = os.path.join(HERE, "signals_in.jsonl")
TRADES = os.path.join(HERE, "paper_trades.jsonl")
OFFSET = os.path.join(HERE, ".queue_offset")
STATE = os.path.join(HERE, "paper_state.json")      # live snapshot for the web UI
EVENTS = os.path.join(HERE, "events.jsonl")         # structured, timestamped action log


def log_event(kind, **data):
    """Append one timestamped event to events.jsonl and echo a readable line."""
    rec = {"t": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "kind": kind, **data}
    try:
        with open(EVENTS, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    print("[" + kind + "] " + "  ".join(f"{k}={v}" for k, v in data.items()))
    return rec


# ---------- costs (delegated to the executor; paper == prior formula) ----------
def roundtrip_cost(liq_usd, stake=STAKE_USD):
    return _PAPER.cost(liq_usd, stake)


# ---------- trade state machine ----------
@dataclass
class Trade:
    ca: str
    symbol: str
    signal_ts: float
    signal_price: float
    liq_usd: float
    rule: str
    dip: float
    wait_min: int
    tp: float
    sl: float
    window_min: int
    stake: float = STAKE_USD
    # dynamic
    state: str = "waiting_fill"      # waiting_fill -> open -> closed
    limit_price: float = 0.0
    entry_price: float = 0.0
    entry_ts: float = 0.0
    tp_price: float = 0.0
    sl_price: float = 0.0
    peak: float = 0.0
    entry_liq: float = 0.0        # ликвидность на момент входа (для раг-выхода)
    exit_price: float = 0.0
    exit_ts: float = 0.0
    exit_reason: str = ""
    filled_limit: bool = False
    gross_ret: float = 0.0
    net_ret: float = 0.0
    pnl_usd: float = 0.0

    def __post_init__(self):
        self.limit_price = self.signal_price * (1 - self.dip)
        self.executor = None            # set by make_trade; falls back to _PAPER
        self.entry_mode = ENTRY_MODE    # set by make_trade

    def _ex(self):
        return self.executor or _PAPER

    def _open_at(self, want_price, now, filled_limit):
        kind = "limit" if filled_limit else "market"
        price = self._ex().entry_fill(want_price, kind, self)   # actual fill
        self.entry_price = price
        self.entry_ts = now
        self.tp_price = price * (1 + self.tp)
        self.sl_price = price * (1 - self.sl)
        self.peak = price
        self.entry_liq = self.liq_usd          # запоминаем ликвидность входа
        self.filled_limit = filled_limit
        self.state = "open"

    def _close(self, want_price, now, reason):
        price = self._ex().exit_fill(want_price, reason, self)  # actual fill
        self.exit_price = price
        self.exit_ts = now
        self.exit_reason = reason
        self.gross_ret = price / self.entry_price - 1
        self.net_ret = self.gross_ret - self._ex().cost(self.liq_usd, self.stake)
        self.pnl_usd = self.stake * self.net_ret
        self.state = "closed"

    def on_price(self, price, now):
        """Advance the trade given a fresh price tick. Returns True if closed.

        Robust to stalls (laptop sleep, frozen loop): the 20-min window is
        enforced by wall-clock even if the price feed is missing, so a position
        can never be held far past its window."""
        age_min = (now - self.signal_ts) / 60.0
        px_ok = price is not None and price > 0

        if self.state == "waiting_fill":
            # never entered and window already gone (e.g. woke up late) -> cancel
            if age_min >= self.window_min:
                self.state = "closed"; self.exit_reason = "CANCEL"; return True
            if not px_ok:
                return False
            if price <= self.limit_price:                       # limit filled (got the dip)
                self._open_at(self.limit_price, now, True)
            elif age_min >= self.wait_min:                       # no dip within wait window
                if self.entry_mode == "market":
                    self._open_at(price, now, False)             # chase at market
                elif self.entry_mode == "market-below" and price <= self.signal_price:
                    self._open_at(price, now, False)             # market, but only if not above signal
                else:                                            # skip: don't chase
                    self.state = "closed"; self.exit_reason = "NODIP"; return True
            return False

        if self.state == "open":
            # price available: original order SL -> TP -> TIME (matches backtest)
            if px_ok:
                self.peak = max(self.peak, price)
                # раг: ликвидность обвалилась -> выходим немедленно по рынку
                if self.entry_liq and 0 < self.liq_usd < self.entry_liq * RUG_FRAC:
                    self._close(price, now, "RUG"); return True
                if price <= self.sl_price:
                    self._close(self.sl_price, now, "SL"); return True
                if price >= self.tp_price:
                    self._close(self.tp_price, now, "TP"); return True
                if age_min >= self.window_min:
                    self._close(price, now, "TIME"); return True
            # price feed down but window elapsed -> still force-close (live safety)
            elif age_min >= self.window_min:
                self._close(self.entry_price, now, "TIME"); return True
        return False


# ---------- live price feed via DexScreener (data.fetch_states) ----------
def live_prices(cas):
    """Return {ca: (price_usd, liq_usd)} for a batch of token addresses."""
    if not cas:
        return {}
    try:
        import data
        states = data.fetch_states(list(cas))
        out = {}
        for s in states:
            out[s.address] = (float(s.price_usd or 0), float(s.liquidity_usd or 0))
        return out
    except Exception as e:
        print(f"[warn] price fetch failed: {e}", file=sys.stderr)
        return {}


# ---------- persistence ----------
def log_trade(t: Trade):
    with open(TRADES, "a") as f:
        f.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")


def notify_close(t: Trade, enabled):
    if not enabled:
        return
    try:
        import notify
        emoji = "🟢" if t.pnl_usd > 0 else "🔴"
        notify.send(
            f"{emoji} PAPER {t.symbol} [{t.rule}] {t.exit_reason} "
            f"{t.net_ret:+.1%}  = ${t.pnl_usd:+.2f}\n"
            f"вход {'лимит' if t.filled_limit else 'рынок'} @ {t.entry_price:.6g} → "
            f"выход @ {t.exit_price:.6g}"
        )
    except Exception:
        pass


def _load_journal():
    out = []
    if os.path.exists(TRADES):
        for line in open(TRADES):
            line = line.strip()
            if line:
                try: out.append(json.loads(line))
                except Exception: pass
    return out


def _load_events(limit=80):
    out = []
    if os.path.exists(EVENTS):
        try:
            lines = open(EVENTS).read().splitlines()[-limit:]
            for ln in lines:
                ln = ln.strip()
                if ln:
                    out.append(json.loads(ln))
        except Exception:
            pass
    return out


def _summary(closed):
    import statistics as st
    n = len(closed)
    if not n:
        return {"trades": 0, "wins": 0, "win_rate": 0, "pnl_usd": 0,
                "avg_ret": 0, "median_ret": 0, "reasons": {}}
    wins = sum(1 for t in closed if t.get("pnl_usd", 0) > 0)
    rets = [t.get("net_ret", 0) for t in closed]
    reasons = {}
    for t in closed:
        reasons[t.get("exit_reason", "?")] = reasons.get(t.get("exit_reason", "?"), 0) + 1
    return {"trades": n, "wins": wins, "win_rate": wins / n,
            "pnl_usd": sum(t.get("pnl_usd", 0) for t in closed),
            "avg_ret": st.mean(rets), "median_ret": st.median(rets),
            "reasons": reasons}


def write_state(active, prices, closed, rule, sim_now=None, events=None):
    """Atomically dump a snapshot for the web dashboard.
    sim_now: если задан (демо), возраст позиции считается от него (sim-часы);
    иначе от реального времени. updated_at всегда реальное (для индикатора LIVE)."""
    now = time.time()
    age_clock = sim_now if sim_now is not None else now
    act = []
    for t in active:
        p, _ = prices.get(t.ca, (0, 0))
        base = t.entry_price or t.signal_price
        unreal = (p / base - 1) if (p and base) else 0.0
        act.append({
            "symbol": t.symbol, "ca": t.ca, "rule": t.rule, "state": t.state,
            "signal_price": t.signal_price, "limit_price": t.limit_price,
            "entry_price": t.entry_price, "current_price": p,
            "filled_limit": t.filled_limit,
            "unreal_pct": unreal, "tp_price": t.tp_price, "sl_price": t.sl_price,
            "age_min": (age_clock - t.signal_ts) / 60.0,
        })
    snap = {"updated_at": now, "rule": rule, "stake_usd": STAKE_USD,
            "rule_params": RULES[rule], "active": act,
            "summary": _summary(closed),
            "recent": closed[-100:][::-1],
            "events": (events or [])[-80:][::-1]}
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def make_trade(sig: dict, rule: str, signal_price: float, liq: float,
               executor=None, entry_mode=None) -> Trade:
    r = RULES[rule]
    ts = sig.get("_ts_epoch")
    if ts is None:
        ts = datetime.datetime.fromisoformat(sig["ts_utc"]).timestamp()
    t = Trade(ca=sig["ca"], symbol=sig.get("symbol", "?"),
              signal_ts=ts, signal_price=signal_price, liq_usd=liq,
              rule=rule, **r)
    t.executor = executor or _PAPER
    t.entry_mode = entry_mode or ENTRY_MODE
    return t


# ---------- LIVE engine ----------
def build_executor(kind):
    """kind: paper | gmgn-dry | gmgn-live"""
    if kind == "paper":
        return _PAPER
    if kind in ("gmgn-dry", "gmgn-live"):
        return GMGNExecutor(mode="dry" if kind == "gmgn-dry" else "live",
                            api_key=_cfg("GMGN_API_KEY"),
                            wallet_key=_cfg("GMGN_PRIVATE_KEY"), exec_cfg=EXEC)
    raise ValueError(f"unknown executor {kind}")


def _cfg(key):
    env = os.path.join(ROOT, ".env")
    if os.environ.get(key):
        return os.environ[key].strip()
    if os.path.exists(env):
        for line in open(env):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def run_live(rule="A", notify_enabled=False, executor=None, entry_mode=None, poll=None):
    executor = executor or _PAPER
    entry_mode = entry_mode or ENTRY_MODE
    poll = poll or POLL_SEC
    print(f"[agent] LIVE executor={executor.name} rule={rule} entry={entry_mode} "
          f"{RULES[rule]} stake=${STAKE_USD} poll={poll}s  queue={QUEUE}")
    active: list[Trade] = []
    closed = _load_journal()          # persist history/summary across restarts
    events = _load_events()           # persist recent event feed across restarts
    def emit(kind, **d):
        events.append(log_event(kind, **d))
    off = 0
    if os.path.exists(OFFSET):
        try: off = int(open(OFFSET).read().strip())
        except Exception: off = 0
    write_state(active, {}, closed, rule, events=events)
    while True:
        # 1) ingest new signals from queue
        if os.path.exists(QUEUE):
            with open(QUEUE) as f:
                f.seek(off)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        sig = json.loads(line)
                    except Exception:
                        continue
                    sym = sig.get("symbol", "?")
                    # фильтр «перегретых»: buy5m выше кэпа -> чаще раг, пропускаем
                    try:
                        b5 = float(sig.get("buy5m"))
                        if b5 > BUY5M_MAX:
                            emit("hot_skip", symbol=sym, buy5m=b5)
                            continue
                    except (TypeError, ValueError):
                        pass
                    # skip stale signals (e.g. leftover queue after a sleep/restart)
                    try:
                        age_min = (time.time() -
                                   datetime.datetime.fromisoformat(sig["ts_utc"]).timestamp()) / 60.0
                        if age_min > RULES[rule]["window_min"]:
                            emit("stale_skip", symbol=sym, signal_ts=sig.get("ts_utc"),
                                 age_min=round(age_min, 1))
                            continue
                    except Exception:
                        pass
                    px = live_prices([sig["ca"]]).get(sig["ca"], (0, 0))
                    if px[0] <= 0:
                        emit("no_price_skip", symbol=sym, ca=sig["ca"][:8])
                        continue
                    sig["_ts_epoch"] = time.time()          # entry clock starts now
                    t = make_trade(sig, rule, signal_price=px[0], liq=px[1],
                                   executor=executor, entry_mode=entry_mode)
                    active.append(t)
                    emit("signal", symbol=t.symbol, ca=t.ca[:8],
                         signal_ts=sig.get("ts_utc"), seen_price=round(t.signal_price, 10),
                         limit=round(t.limit_price, 10),
                         buy5m=sig.get("buy5m"), liq=sig.get("liq"))
                off = f.tell()
            with open(OFFSET, "w") as fo:
                fo.write(str(off))
        # 2) tick active trades
        if active:
            prices = live_prices({t.ca for t in active})
            now = time.time()
            still = []
            for t in active:
                p, liq = prices.get(t.ca, (0, 0))
                if liq:
                    t.liq_usd = liq
                prev = t.state
                did_close = t.on_price(p, now)
                # entry transition (waiting_fill -> open)
                if prev == "waiting_fill" and t.state != "waiting_fill" and t.entry_price > 0:
                    emit("entry", symbol=t.symbol,
                         via=("лимит" if t.filled_limit else "рынок"),
                         price=round(t.entry_price, 10),
                         tp=round(t.tp_price, 10), sl=round(t.sl_price, 10))
                if did_close:
                    if t.exit_reason in ("CANCEL", "NODIP") or t.entry_price <= 0:
                        emit("skip", symbol=t.symbol, reason=t.exit_reason)
                    else:
                        log_trade(t); notify_close(t, notify_enabled)
                        closed.append(asdict(t))
                        held = (t.exit_ts - t.entry_ts) / 60.0 if t.entry_ts else 0
                        emit("exit", symbol=t.symbol, reason=t.exit_reason,
                             price=round(t.exit_price, 10), net=round(t.net_ret, 4),
                             pnl=round(t.pnl_usd, 2), held_min=round(held, 1))
                else:
                    still.append(t)
            active = still
            write_state(active, prices, closed, rule, events=events)
        else:
            write_state(active, {}, closed, rule, events=events)
        time.sleep(poll)


# ---------- REPLAY engine (validate against history using cached OHLCV) ----------
def run_replay(rule="A", entry_mode=None):
    entry_mode = entry_mode or ENTRY_MODE
    print(f"[agent] REPLAY rule={rule} entry={entry_mode} {RULES[rule]} stake=${STAKE_USD}")
    sig_rows = list(csv.DictReader(open(os.path.join(HERE, "signals.csv"))))
    def cache(ca):
        p = os.path.join(HERE, "cache", f"{ca}.json")
        if not os.path.exists(p): return None
        try: return json.load(open(p))
        except Exception: return None
    trades = []
    for r in sig_rows:
        rec = cache(r["ca"])
        if not rec or rec.get("status") != "ok" or not rec.get("ohlcv"):
            continue
        sig_ts = datetime.datetime.fromisoformat(r["ts_utc"]).timestamp()
        m0 = sig_ts - (sig_ts % 60)
        cands = [c for c in rec["ohlcv"] if m0 <= c[0] <= m0 + 25 * 60]
        if not cands:
            continue
        sig_price = None
        for c in cands:
            if c[0] == m0:
                sig_price = c[4]; break
        if not sig_price:
            continue
        liq = float(r["liq"] or 30000)
        sig = {"ca": r["ca"], "symbol": r["symbol"], "ts_utc": r["ts_utc"],
               "_ts_epoch": sig_ts}
        t = make_trade(sig, rule, sig_price, liq, entry_mode=entry_mode)
        # feed each minute candle as ticks: low then high then close (order-agnostic
        # conservative: check low first so SL/limit can trigger before TP within a bar)
        for c in cands:
            for px, tick_ts in ((c[3], c[0] + 20), (c[2], c[0] + 40), (c[4], c[0] + 59)):
                if t.on_price(px, tick_ts):
                    break
            if t.state == "closed":
                break
        if t.state != "closed":                  # window end
            t._close(cands[-1][4], cands[-1][0] + 59, "TIME")
        trades.append(t)
    _report_list(trades, tag=f"REPLAY rule {rule}")
    return trades


def _report_list(trades, tag=""):
    # actual trades only (NODIP/CANCEL never opened -> entry_price 0)
    closed = [t for t in trades if t.state == "closed" and t.entry_price > 0]
    skipped = sum(1 for t in trades if t.state == "closed" and t.entry_price <= 0)
    if not closed:
        print(f"нет сделок (пропущено без отката: {skipped})"); return
    tot = sum(t.pnl_usd for t in closed)
    wins = sum(1 for t in closed if t.pnl_usd > 0)
    fills = sum(1 for t in closed if t.filled_limit)
    import statistics as st
    rets = [t.net_ret for t in closed]
    reasons = {}
    for t in closed:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print(f"\n=== {tag} ===")
    print(f"сделок={len(closed)}  побед={wins} ({wins/len(closed):.0%})  "
          f"лимитка налилась={fills/len(closed):.0%}  пропущено без отката={skipped}")
    print(f"ИТОГО P&L = ${tot:,.0f}  (стейк ${STAKE_USD})")
    print(f"среднее/сделку = {st.mean(rets):+.1%} = ${st.mean(rets)*STAKE_USD:+.2f}  "
          f"медиана = {st.median(rets):+.1%}")
    print(f"выходы: {reasons}")


def run_report():
    if not os.path.exists(TRADES):
        print("журнал пуст:", TRADES); return
    trades = []
    for line in open(TRADES):
        line = line.strip()
        if not line: continue
        d = json.loads(line)
        t = Trade(**{k: d[k] for k in d if k in Trade.__dataclass_fields__})
        trades.append(t)
    _report_list(trades, tag="LIVE PAPER (журнал)")


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)   # живой лог под nohup/pipe
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--rule", default="A", choices=list(RULES))
    ap.add_argument("--executor", default="paper",
                    choices=["paper", "gmgn-dry", "gmgn-live"],
                    help="paper (по умолч.) | gmgn-dry (реальные котировки, без свопа) | gmgn-live")
    ap.add_argument("--notify", action="store_true", help="слать закрытия в Telegram")
    ap.add_argument("--entry", default=ENTRY_MODE,
                    choices=["skip", "market-below", "market"],
                    help="нет отката за 5м: skip=не входить | "
                         "market-below=рынок только если не выше сигнала (умолч.) | market=рынок всегда")
    ap.add_argument("--poll", type=int, default=POLL_SEC,
                    help=f"интервал опроса цены в live, сек (умолч. {POLL_SEC})")
    a = ap.parse_args()
    if a.replay:
        run_replay(a.rule, entry_mode=a.entry)
    elif a.report:
        run_report()
    elif a.live:
        run_live(a.rule, a.notify, executor=build_executor(a.executor),
                 entry_mode=a.entry, poll=a.poll)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
