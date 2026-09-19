#!/usr/bin/env python3
"""
web.py — мульти-вкладочный дашборд. Читает strategies.json (список стратегий)
и paper_state_<name>.json по каждой. Вкладки переключают стратегию; логи/журнал
как раньше. Сервер только читает — ничего не торгует.
"""
import os, json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", HERE)   # тот же, что у multi.py (Volume на Railway)
INDEX = os.path.join(DATA_DIR, "strategies.json")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8788"))
TOKEN = os.environ.get("DASH_TOKEN", "")

EMPTY = {"updated_at": 0, "label": "-", "stake_usd": 0, "rule_params": {}, "active": [],
         "summary": {"trades": 0, "wins": 0, "win_rate": 0, "pnl_usd": 0,
                     "avg_ret": 0, "median_ret": 0, "reasons": {}},
         "recent": [], "events": [], "_missing": True}


def strategies():
    try: return json.load(open(INDEX))
    except Exception: return []


def read_state(name):
    p = os.path.join(DATA_DIR, f"paper_state_{name}.json")
    if not os.path.exists(p):
        return dict(EMPTY, name=name)
    try:
        return json.load(open(p))
    except Exception:
        return dict(EMPTY, name=name, _error=True)


def strategies_summary():
    out = []
    for s in strategies():
        st = read_state(s["name"])
        sm = st.get("summary", {})
        out.append({"name": s["name"], "label": s["label"],
                    "pnl": sm.get("pnl_usd", 0), "trades": sm.get("trades", 0),
                    "win_rate": sm.get("win_rate", 0), "active": len(st.get("active", [])),
                    "updated_at": st.get("updated_at", 0)})
    return out


PAGE = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GMGN paper — мульти</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--line:#232a33;--fg:#e6edf3;--dim:#8b949e;
--grn:#2ea043;--grnbg:#0f2417;--red:#f85149;--redbg:#2a1416;--acc:#58a6ff;--warn:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:14px}
header{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
h1{font-size:17px;margin:0}.badge{background:var(--card);border:1px solid var(--line);
border-radius:999px;padding:3px 10px;font-size:12px;color:var(--dim)}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px}
.live{background:var(--grn);box-shadow:0 0 6px var(--grn)}.stale{background:var(--warn)}.dead{background:var(--red)}
.tabs{display:flex;gap:6px;overflow-x:auto;padding-bottom:8px;margin-bottom:14px;border-bottom:1px solid var(--line)}
.tab{flex:0 0 auto;background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:8px 12px;cursor:pointer;font-size:12px;white-space:nowrap;min-width:140px}
.tab.sel{border-color:var(--acc);background:#12233b}
.tab .lab{font-weight:600;color:var(--fg)}.tab .pl{font-size:15px;font-weight:700;margin-top:2px}
.pos{color:var(--grn)}.neg{color:var(--red)}.tab .sub{color:var(--dim);font-size:11px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi .lab{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.kpi .val{font-size:23px;font-weight:700;margin-top:3px}
h2{font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;margin:18px 0 8px;border-bottom:1px solid var(--line);padding-bottom:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--dim);font-weight:600;padding:6px 9px;font-size:12px}
td{padding:7px 9px;border-top:1px solid var(--line);white-space:nowrap}
tr.win td{background:linear-gradient(90deg,var(--grnbg),transparent 40%)}
tr.loss td{background:linear-gradient(90deg,var(--redbg),transparent 40%)}
.tag{display:inline-block;padding:1px 7px;border-radius:6px;font-size:11px;font-weight:600}
.t-TP{background:var(--grnbg);color:var(--grn)}.t-SL{background:var(--redbg);color:var(--red)}
.t-TIME{background:#1c2330;color:var(--acc)}.t-RUG{background:var(--redbg);color:var(--red)}
.t-CANCEL,.t-NODIP{background:#1c2330;color:var(--dim)}
.st-open{color:var(--grn)}.st-wait{color:var(--warn)}
.mono{font-variant-numeric:tabular-nums}.dim{color:var(--dim)}
.empty{color:var(--dim);padding:20px;text-align:center;border:1px dashed var(--line);border-radius:12px}
.sub{font-size:12px;color:var(--dim)}
.rbtn{margin-left:auto;background:var(--redbg);color:var(--red);border:1px solid #4a2020;
border-radius:8px;padding:5px 11px;font-size:12px;cursor:pointer}
.rbtn:hover{background:#3a1a1a}
.tk{color:var(--acc);text-decoration:none;font-weight:700}.tk:hover{text-decoration:underline}
.gm{font-size:10px;color:var(--dim);border:1px solid var(--line);border-radius:5px;padding:0 4px;margin-left:4px;text-decoration:none}
.gm:hover{border-color:var(--acc);color:var(--acc)}
</style></head><body><div class="wrap">
<header>
  <h1>🧠 GMGN paper — мульти</h1>
  <span class="badge" id="conn"><span class="dot dead"></span><span id="conntx">…</span></span>
  <span class="badge" id="rule">—</span>
  <span class="badge sub" id="upd"></span>
  <button id="reset" class="rbtn">🗑 Сброс вкладки</button>
</header>
<div class="tabs" id="tabs"></div>
<div class="kpis" id="kpis"></div>
<h2>В работе сейчас <span class="dim" id="nact"></span></h2>
<div id="active"></div>
<h2>Лента событий <span class="dim" id="nev"></span></h2>
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
const et=e=>{try{return new Date(e*1000).toLocaleTimeString('ru-RU')}catch(_){return'—'}};
const eti=iso=>{try{return new Date(iso).toLocaleTimeString('ru-RU')}catch(_){return iso||'—'}};
const TK=new URLSearchParams(location.search).get('token')||'';
const qs=extra=>{const p=new URLSearchParams();if(TK)p.set('token',TK);for(const k in extra)p.set(k,extra[k]);const s=p.toString();return s?('?'+s):''};
let SEL=localStorage.getItem('sel_strat')||'';

async function loadTabs(){
  let list=[];try{list=await (await fetch('/api/strategies'+qs(),{cache:'no-store'})).json();}catch(_){}
  if(!list.length){$('#tabs').innerHTML='<span class="sub">нет стратегий — ждём multi.py</span>';return;}
  if(!SEL||!list.find(s=>s.name===SEL))SEL=list[0].name;
  $('#tabs').innerHTML=list.map(s=>`<div class="tab ${s.name===SEL?'sel':''}" data-n="${s.name}">
    <div class="lab">${s.label}</div>
    <div class="pl ${cls(s.pnl)}">${money(s.pnl)}</div>
    <div class="sub">${s.trades} сд · win ${Math.round((s.win_rate||0)*100)}% · ${s.active}⏳</div></div>`).join('');
  document.querySelectorAll('.tab').forEach(el=>el.onclick=()=>{SEL=el.dataset.n;localStorage.setItem('sel_strat',SEL);loadTabs();tick();});
}

function render(s){
  const fresh=s.updated_at?(Date.now()/1000-s.updated_at):1e9;
  const d=$('#conn').querySelector('.dot');
  if(s._missing){d.className='dot dead';$('#conntx').textContent='нет данных';}
  else if(fresh<25){d.className='dot live';$('#conntx').textContent='LIVE';}
  else{d.className='dot stale';$('#conntx').textContent='нет обновл. '+Math.round(fresh)+'с';}
  const rp=s.rule_params||{};
  $('#rule').textContent=(s.label||'')+(rp.tp?(' · TP+'+(rp.tp*100)+'% / SL−'+(rp.sl*100)+'% / '+rp.window_min+'м · '+(rp.dip>0?'лимит −'+(rp.dip*100)+'%':'рынок')):'');
  $('#upd').textContent=s.updated_at?('обновлено '+et(s.updated_at)):'';
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
  $('#active').innerHTML=!A.length?'<div class="empty">Открытых позиций нет</div>':
   '<table><thead><tr><th>Токен</th><th>Статус</th><th>Сигнал</th><th>Вход</th><th>Тек.</th><th>P&L</th><th>Возраст</th></tr></thead><tbody>'+
   A.map(t=>{const w=t.state==='waiting_fill';return `<tr><td><a class="tk" href="https://gmgn.ai/sol/token/${t.ca}" target="_blank" rel="noopener">${t.symbol}</a><a class="gm" href="https://gmgn.ai/sol/token/${t.ca}" target="_blank" rel="noopener">GMGN↗</a></td>
     <td class="${w?'st-wait':'st-open'}">${w?'🆕 ждём':'🟢 в позиции'}</td>
     <td class="mono">${g(t.signal_price)}</td>
     <td class="mono">${t.entry_price?g(t.entry_price)+(t.filled_limit?' <span class="sub">лим</span>':' <span class="sub">рын</span>'):'—'}</td>
     <td class="mono">${g(t.current_price)}</td>
     <td class="mono ${cls(t.unreal_pct)}">${t.entry_price?pct(t.unreal_pct):'—'}</td>
     <td class="mono">${t.age_min.toFixed(1)}м</td></tr>`}).join('')+'</tbody></table>';

  const EV=s.events||[];$('#nev').textContent=EV.length?('· '+EV.length):'';
  function evrow(e){const T=eti(e.t);let ico='•',txt='';
    if(e.kind==='signal'){ico='🆕';txt=`<b>СИГНАЛ</b> ${e.symbol} — в ${eti(e.signal_ts)}, цена ${g(e.seen_price)}${e.limit&&e.limit!=e.seen_price?', лимит '+g(e.limit):''}`;}
    else if(e.kind==='entry'){ico='🟢';txt=`<b>ВХОД</b> ${e.symbol} <b>${e.via}</b> @ ${g(e.price)}`;}
    else if(e.kind==='exit'){const w=e.pnl>0;ico=w?'✅':'🔻';txt=`<b>ВЫХОД</b> ${e.symbol} <span class="tag t-${e.reason}">${e.reason}</span> @ ${g(e.price)} · <span class="${cls(e.net)}">${pct(e.net)} = ${money(e.pnl)}</span> <span class="sub">${e.held_min}м</span>`;}
    else if(e.kind==='skip'){ico='⚪';txt=`<b>ПРОПУСК</b> ${e.symbol} <span class="sub">${e.reason==='NODIP'?'нет отката':e.reason}</span>`;}
    else txt=e.kind+' '+(e.symbol||'');
    return `<tr><td class="mono dim" style="width:90px">${T}</td><td style="width:24px">${ico}</td><td>${txt}</td></tr>`;}
  $('#events').innerHTML=!EV.length?'<div class="empty">Событий пока нет</div>':'<table><tbody>'+EV.map(evrow).join('')+'</tbody></table>';

  const L=s.recent||[];$('#nlog').textContent=L.length?('· '+L.length):'';
  $('#log').innerHTML=!L.length?'<div class="empty">Закрытых сделок пока нет</div>':
   '<table><thead><tr><th>Закрыто</th><th>Токен</th><th>Вход</th><th>Выход</th><th>Держал</th><th>%</th><th>P&L</th></tr></thead><tbody>'+
   L.map(t=>{const w=(t.pnl_usd||0)>0;const h=t.exit_ts&&t.entry_ts?((t.exit_ts-t.entry_ts)/60).toFixed(1)+'м':'—';
     return `<tr class="${w?'win':'loss'}"><td class="mono dim">${et(t.exit_ts)}</td>
       <td><a class="tk" href="https://gmgn.ai/sol/token/${t.ca}" target="_blank" rel="noopener">${t.symbol}</a><a class="gm" href="https://gmgn.ai/sol/token/${t.ca}" target="_blank" rel="noopener">↗</a></td><td class="mono">${g(t.entry_price)} <span class="sub">${t.filled_limit?'лим':'рын'}</span></td>
       <td><span class="tag t-${t.exit_reason}">${t.exit_reason}</span> <span class="mono dim">${g(t.exit_price)}</span></td>
       <td class="mono dim">${h}</td><td class="mono ${cls(t.net_ret)}">${pct(t.net_ret)}</td>
       <td class="mono ${cls(t.pnl_usd)}"><b>${money(t.pnl_usd||0)}</b></td></tr>`}).join('')+'</tbody></table>';
}
async function tick(){if(!SEL)return;try{const r=await fetch('/api/state'+qs({strategy:SEL}),{cache:'no-store'});render(await r.json());}
  catch(_){const d=$('#conn').querySelector('.dot');d.className='dot dead';$('#conntx').textContent='сервер недоступен';}}
$('#reset').onclick=async()=>{
  if(!SEL)return;
  const lab=(document.querySelector('.tab.sel .lab')||{}).textContent||SEL;
  if(!confirm('Сбросить статистику вкладки «'+lab+'»?\nУдалит её сделки и события. Другие вкладки не тронутся.'))return;
  try{await fetch('/api/reset'+qs({strategy:SEL}),{method:'POST'});}catch(_){}
  setTimeout(()=>{loadTabs();tick();},900);
};
async function loop(){await loadTabs();await tick();}
loop();setInterval(loadTabs,5000);setInterval(tick,3000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _authed(self):
        if not TOKEN: return True
        q = parse_qs(urlsplit(self.path).query)
        return q.get("token", [""])[0] == TOKEN

    def _send(self, body, ctype):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self._authed():
            self.send_response(401); self.end_headers(); self.wfile.write(b"unauthorized"); return
        u = urlsplit(self.path); path = u.path; q = parse_qs(u.query)
        if path == "/api/strategies":
            self._send(json.dumps(strategies_summary(), ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/state":
            name = q.get("strategy", [""])[0]
            self._send(json.dumps(read_state(name), ensure_ascii=False), "application/json; charset=utf-8")
        elif path in ("/", "/index.html"):
            self._send(PAGE, "text/html; charset=utf-8")
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if not self._authed():
            self.send_response(401); self.end_headers(); return
        u = urlsplit(self.path); q = parse_qs(u.query)
        if u.path == "/api/reset":
            name = q.get("strategy", [""])[0]
            valid = {s["name"] for s in strategies()}
            if name in valid:
                try:
                    open(os.path.join(DATA_DIR, f".reset_{name}"), "w").close()
                    self._send(json.dumps({"ok": True}), "application/json"); return
                except Exception:
                    pass
            self.send_response(400); self.end_headers()
        else:
            self.send_response(404); self.end_headers()


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[web] мульти-дашборд: http://{HOST}:{PORT}", flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == "__main__":
    main()
