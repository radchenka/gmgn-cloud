#!/usr/bin/env python3
"""
web.py — веб-дашборд paper-агента. Читает signals_lab/paper_state.json
(снимок, который agent.py --live пишет каждый тик) и paper_trades.jsonl.

    python3 signals_lab/web.py            # http://127.0.0.1:8788
    python3 signals_lab/agent.py --live   # в соседнем терминале — источник данных

Сервер только читает состояние: он ничего не торгует и не может влиять на агента.
"""
import os, json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "paper_state.json")
# env-aware for cloud (Railway sets PORT); local defaults unchanged
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8788"))
TOKEN = os.environ.get("DASH_TOKEN", "")   # если задан — дашборд требует ?token=…


def read_state():
    if not os.path.exists(STATE):
        return {"updated_at": 0, "rule": "-", "stake_usd": 0, "rule_params": {},
                "active": [], "summary": {"trades": 0, "wins": 0, "win_rate": 0,
                "pnl_usd": 0, "avg_ret": 0, "median_ret": 0, "reasons": {}},
                "recent": [], "_missing": True}
    try:
        return json.load(open(STATE))
    except Exception:
        return {"_error": True, "active": [], "recent": [],
                "summary": {"trades": 0, "wins": 0, "win_rate": 0, "pnl_usd": 0,
                            "avg_ret": 0, "median_ret": 0, "reasons": {}}}


PAGE = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GMGN paper-агент</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--line:#232a33;--fg:#e6edf3;--dim:#8b949e;
--grn:#2ea043;--grnbg:#0f2417;--red:#f85149;--redbg:#2a1416;--acc:#58a6ff;--warn:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:18px}
header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}
h1{font-size:18px;margin:0}.badge{background:var(--card);border:1px solid var(--line);
border-radius:999px;padding:3px 11px;font-size:12px;color:var(--dim)}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px}
.live{background:var(--grn);box-shadow:0 0 6px var(--grn)}.stale{background:var(--warn)}
.dead{background:var(--red)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi .lab{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.kpi .val{font-size:26px;font-weight:700;margin-top:4px}
.pos{color:var(--grn)}.neg{color:var(--red)}
h2{font-size:14px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;
margin:22px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--dim);font-weight:600;padding:7px 10px;font-size:12px}
td{padding:8px 10px;border-top:1px solid var(--line);white-space:nowrap}
tr.win td{background:linear-gradient(90deg,var(--grnbg),transparent 40%)}
tr.loss td{background:linear-gradient(90deg,var(--redbg),transparent 40%)}
.tag{display:inline-block;padding:1px 8px;border-radius:6px;font-size:11px;font-weight:600}
.t-TP{background:var(--grnbg);color:var(--grn)}.t-SL{background:var(--redbg);color:var(--red)}
.t-TIME{background:#1c2330;color:var(--acc)}.t-RUG{background:var(--redbg);color:var(--red)}
.st-open{color:var(--grn)}.st-wait{color:var(--warn)}
.bar{height:5px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:4px;width:110px}
.bar>i{display:block;height:100%;background:var(--acc)}
.mono{font-variant-numeric:tabular-nums}.dim{color:var(--dim)}
.empty{color:var(--dim);padding:24px;text-align:center;border:1px dashed var(--line);border-radius:12px}
.sub{font-size:12px;color:var(--dim)}
</style></head><body><div class="wrap">
<header>
  <h1>🧠 GMGN paper-агент</h1>
  <span class="badge" id="conn"><span class="dot dead"></span><span id="conntx">…</span></span>
  <span class="badge" id="rule">правило —</span>
  <span class="badge" id="stake">стейк —</span>
  <span class="badge sub" id="upd"></span>
</header>
<div class="kpis" id="kpis"></div>
<h2>В работе сейчас <span class="dim" id="nact"></span></h2>
<div class="sub" style="margin:-4px 0 10px">🆕 новый сигнал появляется здесь мгновенно (статус «ждём −10%»), затем «в позиции», при закрытии уходит в журнал ниже.</div>
<div id="active"></div>
<h2>Лента событий <span class="dim" id="nev"></span></h2>
<div class="sub" style="margin:-4px 0 10px">каждое действие агента с точным временем: сигнал → лимитка → вход/пропуск → выход. Полный лог — signals_lab/events.jsonl</div>
<div id="events"></div>
<h2>Журнал сделок <span class="dim" id="nlog"></span></h2>
<div id="log"></div>
</div>
<script>
const $=s=>document.querySelector(s);
const money=x=>(x>=0?'+$':'-$')+Math.abs(x).toFixed(2);
const pct=x=>(x>=0?'+':'')+(x*100).toFixed(1)+'%';
const cls=x=>x>=0?'pos':'neg';
const g=x=>x==null?'—':(+x).toPrecision(4);
const ts=e=>{if(!e)return'—';const d=new Date(e*1000);return d.toLocaleTimeString('ru-RU')};
function render(s){
  const fresh=s.updated_at?(Date.now()/1000-s.updated_at):1e9;
  const c=$('#conn'),d=c.querySelector('.dot');
  if(s._missing){d.className='dot dead';$('#conntx').textContent='агент не запущен';}
  else if(fresh<20){d.className='dot live';$('#conntx').textContent='LIVE';}
  else{d.className='dot stale';$('#conntx').textContent='нет обновлений '+Math.round(fresh)+'с';}
  $('#rule').textContent='правило '+(s.rule||'—')+(s.rule_params?.tp?(' · TP+'+(s.rule_params.tp*100)+'% / SL−'+(s.rule_params.sl*100)+'% / '+s.rule_params.window_min+'м'):'');
  $('#stake').textContent='стейк $'+(s.stake_usd||0);
  $('#upd').textContent=s.updated_at?('обновлено '+ts(s.updated_at)):'';
  const m=s.summary||{};
  $('#kpis').innerHTML=[
    ['P&L итого',`<span class="${cls(m.pnl_usd)}">${money(m.pnl_usd||0)}</span>`],
    ['Винрейт',`${Math.round((m.win_rate||0)*100)}% <span class="sub">(${m.wins||0}/${m.trades||0})</span>`],
    ['Сделок',m.trades||0],
    ['Ср./сделку',`<span class="${cls(m.avg_ret)}">${pct(m.avg_ret||0)}</span>`],
    ['Медиана',`<span class="${cls(m.median_ret)}">${pct(m.median_ret||0)}</span>`],
    ['В работе',(s.active||[]).length],
  ].map(([l,v])=>`<div class="kpi"><div class="lab">${l}</div><div class="val">${v}</div></div>`).join('');

  const A=s.active||[];$('#nact').textContent=A.length?('· '+A.length):'';
  if(!A.length){$('#active').innerHTML='<div class="empty">Открытых позиций нет — ждём сигналы</div>';}
  else{
    $('#active').innerHTML='<table><thead><tr><th>Токен</th><th>Статус</th><th>Сигнал</th><th>Лимит −10%</th><th>Вход</th><th>Тек. цена</th><th>P&L (нереал.)</th><th>Возраст / 20м</th></tr></thead><tbody>'+
    A.map(t=>{const wait=t.state==='waiting_fill';
      const agepc=Math.min(100,(t.age_min/(s.rule_params?.window_min||20))*100);
      const fresh=t.age_min<1;
      const status=wait
        ? '🆕 СИГНАЛ · ждём −10%'
        : (fresh?'🆕 только что вошли':'🟢 в позиции');
      return `<tr>
        <td><b>${t.symbol}</b> <span class="sub">${t.rule}</span>${fresh?' <span class="tag t-TP">NEW</span>':''}</td>
        <td class="${wait?'st-wait':'st-open'}">${status}</td>
        <td class="mono">${g(t.signal_price)}</td>
        <td class="mono dim">${g(t.limit_price)}</td>
        <td class="mono">${t.entry_price?g(t.entry_price)+(t.filled_limit?' <span class="sub">лим</span>':' <span class="sub">рын</span>'):'—'}</td>
        <td class="mono">${g(t.current_price)}</td>
        <td class="mono ${cls(t.unreal_pct)}">${t.entry_price?pct(t.unreal_pct):'—'}</td>
        <td><span class="mono">${t.age_min.toFixed(1)}м</span><div class="bar"><i style="width:${agepc}%"></i></div></td>
      </tr>`}).join('')+'</tbody></table>';
  }

  const EV=s.events||[];$('#nev').textContent=EV.length?('· '+EV.length):'';
  const et=iso=>{try{return new Date(iso).toLocaleTimeString('ru-RU')}catch(e){return iso||'—'}};
  function evrow(e){
    const T=et(e.t); let ico='•',txt='';
    if(e.kind==='signal'){ico='🆕';txt=`<b>СИГНАЛ</b> ${e.symbol} — сигнал в ${et(e.signal_ts)}, цена ${g(e.seen_price)}, лимит −10% ${g(e.limit)} <span class="sub">buy5m ${e.buy5m??'—'} · liq $${e.liq??'—'}</span>`;}
    else if(e.kind==='entry'){ico='🟢';txt=`<b>ВХОД</b> ${e.symbol} по <b>${e.via}</b> @ ${g(e.price)} <span class="sub">TP ${g(e.tp)} · SL ${g(e.sl)}</span>`;}
    else if(e.kind==='exit'){const w=e.pnl>0;ico=w?'✅':'🔻';txt=`<b>ВЫХОД</b> ${e.symbol} <span class="tag t-${e.reason}">${e.reason}</span> @ ${g(e.price)} · <span class="${cls(e.net)}">${pct(e.net)} = ${money(e.pnl)}</span> <span class="sub">держал ${e.held_min}м</span>`;}
    else if(e.kind==='skip'){ico='⚪';txt=`<b>ПРОПУСК</b> ${e.symbol} <span class="sub">${e.reason==='NODIP'?'не было отката −10%':e.reason}</span>`;}
    else if(e.kind==='hot_skip'){ico='🔥';txt=`пропуск ${e.symbol} <span class="sub">перегрет: buy5m ${e.buy5m}</span>`;}
    else if(e.kind==='stale_skip'){ico='⏭';txt=`пропуск ${e.symbol} <span class="sub">протухший сигнал, ${e.age_min}м</span>`;}
    else if(e.kind==='no_price_skip'){ico='⏭';txt=`пропуск ${e.symbol} <span class="sub">нет цены</span>`;}
    else txt=e.kind;
    return `<tr><td class="mono dim" style="width:90px">${T}</td><td style="width:24px">${ico}</td><td>${txt}</td></tr>`;
  }
  if(!EV.length){$('#events').innerHTML='<div class="empty">Событий пока нет</div>';}
  else{$('#events').innerHTML='<table><tbody>'+EV.map(evrow).join('')+'</tbody></table>';}

  const L=s.recent||[];$('#nlog').textContent=L.length?('· последние '+L.length):'';
  if(!L.length){$('#log').innerHTML='<div class="empty">Закрытых сделок пока нет</div>';}
  else{
    $('#log').innerHTML='<table><thead><tr><th>Закрыто</th><th>Токен</th><th>Вход</th><th>Выход</th><th>Держал</th><th>Итог %</th><th>P&L $</th></tr></thead><tbody>'+
    L.map(t=>{const win=(t.pnl_usd||0)>0;const held=t.exit_ts&&t.entry_ts?((t.exit_ts-t.entry_ts)/60).toFixed(1)+'м':'—';
      return `<tr class="${win?'win':'loss'}">
        <td class="mono dim">${ts(t.exit_ts)}</td>
        <td><b>${t.symbol}</b> <span class="sub">${t.rule}</span></td>
        <td class="mono">${g(t.entry_price)} <span class="sub">${t.filled_limit?'лим':'рын'}</span></td>
        <td><span class="tag t-${t.exit_reason}">${t.exit_reason}</span> <span class="mono dim">${g(t.exit_price)}</span></td>
        <td class="mono dim">${held}</td>
        <td class="mono ${cls(t.net_ret)}">${pct(t.net_ret)}</td>
        <td class="mono ${cls(t.pnl_usd)}"><b>${money(t.pnl_usd||0)}</b></td>
      </tr>`}).join('')+'</tbody></table>';
  }
}
const TK=new URLSearchParams(location.search).get('token')||'';
async function tick(){try{const r=await fetch('/api/state'+(TK?('?token='+encodeURIComponent(TK)):''),{cache:'no-store'});render(await r.json());}
  catch(e){const d=$('#conn').querySelector('.dot');d.className='dot dead';$('#conntx').textContent='сервер недоступен';}}
tick();setInterval(tick,3000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _authed(self):
        if not TOKEN:
            return True
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        return q.get("token", [""])[0] == TOKEN

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self._authed():
            self.send_response(401); self.end_headers()
            self.wfile.write(b"unauthorized: add ?token=...")
            return
        if path == "/api/state":
            self._send(json.dumps(read_state(), ensure_ascii=False), "application/json; charset=utf-8")
        elif path in ("/", "/index.html"):
            self._send(PAGE, "text/html; charset=utf-8")
        else:
            self.send_response(404); self.end_headers()


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[web] дашборд: http://{HOST}:{PORT}   (Ctrl+C — стоп)")
    print(f"[web] источник: {STATE}  {'НАЙДЕН' if os.path.exists(STATE) else 'НЕТ — запусти agent.py --live'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
