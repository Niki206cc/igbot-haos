"""Dashboard v4: nuova grafica, miniature in coda e pubblicazione manuale immediata."""
import re
import threading
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from flask import request, jsonify

import meta_v2 as core

UA = {"User-Agent": "Mozilla/5.0 (MontagnePaesi-IGBot)"}


# --- Stato esteso per la dashboard ---------------------------------------------
def rich_sync_state(s):
    day = datetime.now().strftime("%Y-%m-%d")
    q = []
    for x in s.get("queue", []):
        q.append({
            "title": x.get("title", ""),
            "link": x.get("link", ""),
            "image": x.get("image") or "",
            "national": bool(x.get("national")),
            "urgent": bool(core.is_urgent_item(x)) if hasattr(core, "is_urgent_item") else False,
        })
    with core.lock:
        core.state["queue"] = q
        core.state["adaptive_limit"] = s.get("adaptive_limit")
        core.state["last_post"] = s.get("last_post")
        core.state["published_today"] = int((s.get("stats", {}).get(day) or {}).get("published", 0))
        core.state["national_today"] = core.national_count_today(s) if hasattr(core, "national_count_today") else 0
        core.state["national_max"] = getattr(core, "NATIONAL_DAILY_MAX", 10)
core.sync_state = rich_sync_state
core.state.setdefault("manual_status", "")


# --- Miniature: og:image degli articoli in coda --------------------------------
def og_image(link):
    try:
        r = requests.get(link, timeout=15, headers=UA)
        r.raise_for_status()
        m = BeautifulSoup(r.text, "html.parser").find("meta", property="og:image")
        return (m.get("content") or "").strip() if m else ""
    except Exception:
        return ""


def thumbnail_filler():
    while True:
        try:
            with core.store_lock:
                s = core.load_store()
                todo = next((x.get("link") for x in s.get("queue", []) if "image" not in x and x.get("link")), None)
            if todo:
                img = og_image(todo)
                with core.store_lock:
                    s = core.load_store()
                    for x in s.get("queue", []):
                        if x.get("link") == todo:
                            x["image"] = img
                    core.save_store(s)
                    core.sync_state(s)
                time.sleep(1)
                continue
        except Exception as e:
            core.log(f"⚠️ Miniature coda: {e}")
        time.sleep(5)

threading.Thread(target=thumbnail_filler, daemon=True).start()


# --- Pubblicazione manuale immediata -------------------------------------------
manual_lock = threading.Lock()


def set_manual(msg):
    core.state["manual_status"] = msg
    core.log(msg)


def page_title(link):
    r = requests.get(link, timeout=20, headers=UA)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    m = soup.find("meta", property="og:title")
    t = (m.get("content") or "").strip() if m else ""
    if not t and soup.title:
        t = soup.title.get_text(" ", strip=True)
    t = re.sub(r"\s*[-–|]\s*Montagne\s*&(amp;)?\s*Paesi\s*$", "", t, flags=re.I)
    return core.clean(t)


def manual_worker(link):
    try:
        cfg = core.load_config()
        token = str(cfg.get("meta_access_token") or "")
        uid = str(cfg.get("meta_ig_user_id") or core.DEFAULT_IG_USER_ID)
        if not token:
            set_manual("❌ Pubblicazione manuale: token Meta mancante.")
            return
        core.publishing_limit(token, uid, True)
        used = core.state.get("quota_usage")
        with core.store_lock:
            s = core.load_store()
            queued = next((x for x in s.get("queue", []) if x.get("link") == link), None)
            # Manuale: usa il limite reale di Meta, non la soglia prudenziale del publisher.
            limit = core.state.get("quota_total") or s.get("adaptive_limit") or 50
        if used is not None and int(used) >= int(limit):
            set_manual(f"⛔ Nessuno slot libero: quota Meta {used}/{limit}. Riprova quando si libera un posto.")
            return
        title = (queued or {}).get("title") or page_title(link)
        if not title:
            set_manual("❌ Pubblicazione manuale: titolo dell'articolo non trovato.")
            return
        set_manual(f"⚡ Pubblicazione manuale in corso: {title}")
        item = {"title": title, "link": link, "national": bool((queued or {}).get("national"))}
        a = core.build_article(item)
        core.state["preview"] = a
        mid = core.publish(token, uid, a)
        with core.store_lock:
            s = core.load_store()
            s["queue"] = [x for x in s.get("queue", []) if x.get("link") != link]
            if link not in s.get("seen", []):
                s.setdefault("seen", []).append(link)
            core.remember_title(s, title)
            core.stat_inc(s, "published")
            core.save_store(s)
            core.sync_state(s)
        core.set_last(link)
        core.state["last_published"] = title
        core.on_published(item)
        core.publishing_limit(token, uid, True)
        set_manual(f"✅ Pubblicato manualmente su Instagram: {title} (Media ID {mid})")
    except Exception as e:
        set_manual(f"❌ Pubblicazione manuale fallita: {e}")
    finally:
        manual_lock.release()


@core.app.post("/manual_publish")
def manual_publish():
    data = request.get_json(silent=True) or {}
    link = str(data.get("link") or "").strip()
    if not re.match(r"^https?://", link):
        return jsonify({"ok": False, "error": "Inserisci il link completo dell'articolo (https://...)."}), 400
    if not manual_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "C'è già una pubblicazione manuale in corso."}), 409
    threading.Thread(target=manual_worker, args=(link,), daemon=True).start()
    return jsonify({"ok": True})


# --- Nuova dashboard ------------------------------------------------------------
core.PAGE = r'''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>M&P · Instagram Bot</title>
<style>
:root{--bg:#f2f4f7;--card:#fff;--ink:#16202c;--mute:#6b7685;--line:#e4e8ee;--brand:#1f6f4a;--brand2:#2e9b67;--red:#c0392b;--amber:#c27c0e;--blue:#2563eb}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);font-size:15px}
header{background:linear-gradient(120deg,#16402c,#1f6f4a 60%,#2e9b67);color:#fff;padding:18px 22px}
.hwrap{max-width:1100px;margin:auto;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.brand{font-weight:700;font-size:20px;letter-spacing:.2px}.brand small{display:block;font-weight:400;font-size:12px;opacity:.8}
.pill{display:inline-flex;align-items:center;gap:6px;padding:5px 11px;border-radius:999px;font-size:13px;font-weight:600;background:rgba(255,255,255,.16)}
.dot{width:9px;height:9px;border-radius:50%;background:#bbb}.on .dot{background:#5df29a;box-shadow:0 0 0 3px rgba(93,242,154,.25)}.off .dot{background:#ff7b6b}
.sp{flex:1}
main{max-width:1100px;margin:18px auto;padding:0 14px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.wide{grid-column:1/-1}.two{grid-column:span 2}
h3{margin:0 0 10px;font-size:15px;text-transform:uppercase;letter-spacing:.6px;color:var(--mute)}
.big{font-size:28px;font-weight:700}.sub{color:var(--mute);font-size:13px}
.bar{height:10px;background:#e9edf2;border-radius:99px;overflow:hidden;margin:8px 0 4px}.bar>i{display:block;height:100%;background:var(--brand2);width:0}
.btn{white-space:nowrap;border:0;border-radius:9px;padding:9px 14px;font-weight:600;font-size:14px;cursor:pointer;background:#eef1f5;color:var(--ink)}
.btn:hover{filter:brightness(.95)}.g{background:var(--brand);color:#fff}.r{background:var(--red);color:#fff}.b{background:var(--blue);color:#fff}.y{background:#f59e0b;color:#fff}
.ib{padding:6px 9px;font-size:13px}
input[type=text],input[type=password],input:not([type]){width:100%;padding:10px;border:1px solid var(--line);border-radius:9px;font-size:14px;margin:4px 0 10px}
.row{display:flex;gap:8px;align-items:center}.row input{margin:0}
.q{list-style:none;margin:0;padding:0}.q li{display:flex;gap:10px;align-items:center;padding:8px 4px;border-bottom:1px solid var(--line)}
.q li:last-child{border-bottom:0}.thumb{width:64px;height:64px;border-radius:9px;object-fit:cover;background:#e9edf2;flex:none}
.n{width:22px;text-align:right;color:var(--mute);font-size:13px;flex:none}.t{flex:1;min-width:0}.t a{color:var(--ink);text-decoration:none;font-weight:600;line-height:1.3}
.t a:hover{text-decoration:underline}.tags{margin-top:3px}.tag{display:inline-block;font-size:11px;font-weight:700;padding:2px 7px;border-radius:99px;margin-right:4px}
.tu{background:#fde2e0;color:var(--red)}.tn{background:#eceff3;color:var(--mute)}.tl{background:#dcf3e6;color:var(--brand)}
.act{display:flex;gap:4px;flex:none}
#manualMsg{margin-top:8px;font-size:13px}
pre{background:#0f1720;color:#d7e0ea;padding:12px;border-radius:10px;max-height:380px;overflow:auto;white-space:pre-wrap;font-size:12.5px;margin:8px 0 0}
details>summary{cursor:pointer;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:.6px;font-size:15px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}
.empty{color:var(--mute);padding:14px 4px}
@media(max-width:820px){main{grid-template-columns:1fr}.two{grid-column:auto}.grid2{grid-template-columns:1fr}.act .ib{padding:6px 7px}.thumb{width:52px;height:52px}}
</style></head><body>
<header><div class="hwrap">
 <div class="brand">Montagne &amp; Paesi → Instagram<small>Versione {{v}} · API ufficiale Meta</small></div>
 <span id="stpill" class="pill off"><span class="dot"></span><span id="st">—</span></span>
 <div class="sp"></div>
 <form method="post" style="margin:0"><button class="btn g" formaction="/start">▶ Avvia</button> <button class="btn r" formaction="/stop">■ Ferma</button></form>
</div></header>
<main>
 <div class="card"><h3>Quota Meta (24 ore)</h3><div class="big" id="quota">—</div><div class="bar"><i id="qbar"></i></div><div class="sub">Soglia di sicurezza: <b id="adaptive">—</b></div></div>
 <div class="card"><h3>Oggi</h3><div class="big" id="today">0</div><div class="sub">post pubblicati · nazionali <b id="nat">0</b></div></div>
 <div class="card"><h3>Stato pubblicazione</h3><div id="pstatus" style="font-weight:600">—</div><div class="sub" id="countdown"></div><div class="sub" id="diag"></div></div>

 <div class="card wide"><h3>⚡ Pubblica subito</h3>
  <div class="row"><input id="manualUrl" placeholder="Incolla il link dell'articolo: https://www.montagneepaesi.com/..."><button class="btn y" onclick="manualPublish()">Pubblica ora</button></div>
  <div class="sub">Salta la coda e il ritmo: parte subito se Meta ha uno slot libero (usa anche la riserva urgenti).</div>
  <div id="manualMsg"></div>
 </div>

 <div class="card wide"><div class="row" style="margin-bottom:6px"><h3 style="margin:0">Coda · <span id="qcount">0</span></h3><div class="sp"></div>
  <button class="btn ib" onclick="deleteSelected()">Elimina selezionati</button><button class="btn ib r" onclick="clearQueue()">Svuota coda</button></div>
  <div class="sub">⚡ pubblica subito · ★ in cima · ↑/↓ priorità · ✕ elimina</div>
  <ul class="q" id="queue"></ul>
 </div>

 {% if preview %}<div class="card wide"><h3>Ultima anteprima</h3><div class="row" style="align-items:flex-start">{% if preview.image_url %}<img class="thumb" style="width:110px;height:110px" src="{{preview.image_url}}">{% endif %}<div style="white-space:pre-wrap;font-size:14px">{{preview.caption}}</div></div></div>{% endif %}

 <div class="card wide"><details><summary>Log</summary>
  <div class="row" style="margin-top:8px"><button class="btn ib" id="copybtn" onclick="copyLog()">Copia log</button><button class="btn ib" onclick="clearLog()">Azzera log</button></div>
  <pre id="log"></pre></details></div>

 <div class="card wide"><details><summary>Impostazioni</summary><form method="post" style="margin-top:10px"><div class="grid2">
  <div><label class="sub">Instagram User ID</label><input name="meta_ig_user_id" value="{{uid}}"></div>
  <div><label class="sub">Access Token Meta</label><input type="password" name="meta_access_token" placeholder="{% if token %}Token salvato{% else %}Token non configurato{% endif %}"></div>
  <div><label class="sub">Feed RSS</label><input name="rss_url" value="{{rss}}"></div>
  <div><label class="sub">Intervallo controllo feed (secondi)</label><input name="check_interval" value="{{interval}}"></div>
  <div><label class="sub">WAHA URL</label><input name="waha_url" value="{{wu}}"></div>
  <div><label class="sub">Sessione WAHA</label><input name="waha_session" value="{{ws}}"></div>
  <div><label class="sub">Numero destinatario notifiche</label><input name="waha_number" value="{{wn}}"></div>
  <div><label class="sub">WAHA API Key</label><input type="password" name="waha_api_key" placeholder="{% if wk %}API Key salvata{% else %}API Key non configurata{% endif %}"></div>
 </div><button class="btn b" formaction="/save">Salva</button> <button class="btn" formaction="/preview">Anteprima</button> <button class="btn" formaction="/test_waha">Test WAHA</button></form></details></div>
</main>
<script>
let qsig='',logOpen=false;
const $=id=>document.getElementById(id);
function esc(s){return (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function renderQueue(q){
 const sig=JSON.stringify(q.map(x=>[x.link,x.image,x.urgent,x.national]));if(sig===qsig)return;qsig=sig;
 const checked=new Set([...document.querySelectorAll('.qsel:checked')].map(x=>x.value));
 if(!q.length){$('queue').innerHTML='<li class="empty">Nessun articolo in coda.</li>';return}
 $('queue').innerHTML=q.map((x,i)=>{
  const tag=x.urgent?'<span class="tag tu">URGENTE</span>':(x.national?'<span class="tag tn">NAZIONALE</span>':'<span class="tag tl">LOCALE</span>');
  const img=x.image?`<img class="thumb" loading="lazy" src="${esc(x.image)}" onerror="this.style.visibility='hidden'">`:'<div class="thumb"></div>';
  const L=esc(x.link);
  return `<li><input type="checkbox" class="qsel" value="${L}" ${checked.has(x.link)?'checked':''}><span class="n">${i+1}</span>${img}
   <div class="t"><a href="${L}" target="_blank" rel="noopener">${esc(x.title)}</a><div class="tags">${tag}</div></div>
   <div class="act"><button class="btn ib y" title="Pubblica subito" onclick="manualPublish('${L}')">⚡</button><button class="btn ib" title="In cima" onclick="moveQueue('${L}','top')">★</button><button class="btn ib" onclick="moveQueue('${L}','up')">↑</button><button class="btn ib" onclick="moveQueue('${L}','down')">↓</button><button class="btn ib r" onclick="deleteLinks(['${L}'])">✕</button></div></li>`}).join('');
}
async function refresh(){
 let s=await(await fetch('/status')).json();
 $('st').textContent=s.running?'ATTIVO':'FERMO';$('stpill').className='pill '+(s.running?'on':'off');
 $('pstatus').textContent=s.publish_status||'—';
 const u=s.quota_usage,t=s.quota_total;$('quota').textContent=(u==null||t==null)?'—':u+' / '+t;$('qbar').style.width=(u!=null&&t)?Math.min(100,u/t*100)+'%':'0';
 $('qbar').style.background=(u!=null&&t&&u/t>=.9)?'#c0392b':(u!=null&&t&&u/t>=.75)?'#f59e0b':'#2e9b67';
 $('adaptive').textContent=s.adaptive_limit==null?'in apprendimento':s.adaptive_limit;
 $('today').textContent=s.published_today||0;$('nat').textContent=(s.national_today||0)+' / '+(s.national_max||10);
 let q=s.queue||[];$('qcount').textContent=q.length;renderQueue(q);
 let sec=s.next_publish_in||0;$('countdown').textContent=q.length&&sec>0?'Prossimo tentativo tra '+(sec>=60?Math.ceil(sec/60)+' min':sec+' s'):'';
 $('diag').textContent=s.meta_diagnostic?'Diagnostica Meta: '+s.meta_diagnostic:'';
 if(s.manual_status)$('manualMsg').textContent=s.manual_status;
 if(document.querySelector('details[open] #log'))$('log').textContent=await(await fetch('/logs')).text();
}
async function manualPublish(link){
 link=link||$('manualUrl').value.trim();if(!link){alert('Incolla il link dell\'articolo.');return}
 if(!confirm('Pubblicare subito su Instagram?\n\n'+link))return;
 let r=await fetch('/manual_publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({link:link})});let d=await r.json();
 $('manualMsg').textContent=d.ok?'⏳ Richiesta inviata, pubblicazione in corso…':'❌ '+d.error;if(d.ok)$('manualUrl').value='';refresh()
}
async function moveQueue(link,action){await fetch('/queue_move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({link:link,action:action})});qsig='';refresh()}
async function deleteLinks(links){if(!links.length)return;if(confirm('Rimuovere dalla coda '+links.length+' articoli?')){await fetch('/queue_delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({links:links})});qsig='';refresh()}}
async function deleteSelected(){let links=[...document.querySelectorAll('.qsel:checked')].map(x=>x.value);if(!links.length){alert('Seleziona almeno un articolo.');return}deleteLinks(links)}
async function clearQueue(){if(confirm('Cancellare tutti gli articoli in coda?')){await fetch('/clear_queue',{method:'POST'});qsig='';refresh()}}
async function copyLog(){let txt=$('log').textContent,b=$('copybtn');try{await navigator.clipboard.writeText(txt);b.textContent='Copiato!'}catch(e){window.prompt('Copia manualmente il log:',txt)}setTimeout(()=>b.textContent='Copia log',1500)}
async function clearLog(){await fetch('/clear_logs',{method:'POST'});refresh()}
document.querySelector('details').addEventListener('toggle',refresh);
setInterval(refresh,2500);refresh();
</script></body></html>'''


# --- Avvio automatico dopo un riavvio del container ---------------------------
# Il publisher partiva solo premendo "Avvia": dopo un aggiornamento dello stack
# o un riavvio per limite di memoria il bot restava fermo e la coda (con le
# miniature) non veniva più aggiornata. Ora ricorda se era acceso e riparte.
import json as _json
import os as _os

AUTOSTART_PATH = "/data/autostart.json"


def _set_autostart(on):
    try:
        with open(AUTOSTART_PATH, "w", encoding="utf-8") as f:
            _json.dump({"running": bool(on)}, f)
    except Exception as e:
        core.log(f"⚠️ Avvio automatico: impossibile salvare lo stato ({e})")


def _want_autostart():
    try:
        with open(AUTOSTART_PATH, encoding="utf-8") as f:
            return bool(_json.load(f).get("running", True))
    except FileNotFoundError:
        return True  # nessuna preferenza salvata: il bot era acceso prima di questa versione
    except Exception:
        return True


def _wrap_route(endpoint, flag):
    original = core.app.view_functions.get(endpoint)
    if not original:
        return
    def wrapped(*a, **kw):
        _set_autostart(flag)
        return original(*a, **kw)
    core.app.view_functions[endpoint] = wrapped

_wrap_route("start", True)
_wrap_route("stop", False)


def startup_autostart():
    time.sleep(10)
    try:
        with core.store_lock:
            core.sync_state(core.load_store())  # elenco coda completo di miniature subito dopo l'avvio
    except Exception as e:
        core.log(f"⚠️ Sincronizzazione iniziale coda: {e}")
    if _os.getenv("AUTOSTART", "true").lower() in ("0", "false", "no"):
        return
    if not _want_autostart():
        core.log("⏸️ Bot lasciato fermo all'avvio: era stato fermato a mano.")
        return
    if not str(core.load_config().get("meta_access_token") or ""):
        core.log("⏸️ Avvio automatico saltato: token Meta non configurato.")
        return
    if core.state.get("running") or (core.bot_thread and core.bot_thread.is_alive()):
        return
    core.stop_event.clear()
    core.state["running"] = True
    core.state["last_error"] = ""
    core.bot_thread = threading.Thread(target=core.loop, daemon=True)
    core.bot_thread.start()
    core.log("▶️ Bot avviato automaticamente dopo il riavvio del container.")

threading.Thread(target=startup_autostart, daemon=True).start()
