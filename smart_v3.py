"""Montagne & Paesi Instagram Bot v2.5.0 - adaptive speed, manual queue controls, Meta probe."""
import threading
import time
import smart_v2 as smart
import meta_v2 as core
from flask import request, jsonify

APP_VERSION = "2.5.0"
PROBE_INTERVAL = 3 * 3600
FIRST_PROBE_DELAY = 120
PROBE_TIMEOUT = 600
NORMAL_GAP = 90
BUSY_GAP = 60
BUSY_QUEUE = 50

smart.MAX_QUEUE = 150
core.APP_VERSION = APP_VERSION
core.PUBLISH_GAP = NORMAL_GAP

# --- Ordine manuale persistente -------------------------------------------------
# Quando l'utente riordina la coda, assegniamo manual_rank a tutti gli elementi.
# Il ranking automatico continua a funzionare finche la coda non viene modificata
# manualmente; in seguito i nuovi articoli vengono accodati dopo quelli ordinati.
_original_prune = smart.prune_and_rank
def manual_aware_prune(s):
    dropped = _original_prune(s)
    q = s.get("queue", [])
    if any("manual_rank" in x for x in q):
        q.sort(key=lambda x: (0, int(x.get("manual_rank", 10**9))) if "manual_rank" in x else (1, -int(x.get("priority", 0))))
    s["queue"] = q
    return dropped
smart.prune_and_rank = manual_aware_prune


def save_manual_order(q):
    for i, x in enumerate(q):
        x["manual_rank"] = i


@core.app.post("/queue_move")
def queue_move():
    data = request.get_json(silent=True) or {}
    link = str(data.get("link") or "")
    action = str(data.get("action") or "")
    with core.store_lock:
        s = core.load_store(); q = s.get("queue", [])
        idx = next((i for i,x in enumerate(q) if x.get("link") == link), -1)
        if idx < 0:return jsonify({"ok":False,"error":"Articolo non trovato"}),404
        if action == "top" and idx > 0:
            x=q.pop(idx);q.insert(0,x)
        elif action == "up" and idx > 0:
            q[idx-1],q[idx]=q[idx],q[idx-1]
        elif action == "down" and idx < len(q)-1:
            q[idx+1],q[idx]=q[idx],q[idx+1]
        save_manual_order(q);s["queue"]=q;core.save_store(s);core.sync_state(s)
    return jsonify({"ok":True})


@core.app.post("/queue_delete")
def queue_delete():
    data=request.get_json(silent=True) or {};links=set(str(x) for x in data.get("links",[]) if x)
    if not links:return jsonify({"ok":True,"removed":0})
    with core.store_lock:
        s=core.load_store();before=len(s.get("queue",[]));s["queue"]=[x for x in s.get("queue",[]) if x.get("link") not in links];removed=before-len(s["queue"]);save_manual_order(s["queue"]);core.save_store(s);core.sync_state(s)
    core.log(f"🗑️ Rimossi manualmente {removed} articoli dalla coda.")
    return jsonify({"ok":True,"removed":removed})


# --- UI coda -------------------------------------------------------------------
# Sostituisce solo il rendering JS della lista e aggiunge i controlli, senza
# duplicare il pannello/configurazione del core.
core.PAGE = core.PAGE.replace(
    '<b>Coda — <span id="qcount">0</span></b> <button class="btn danger" onclick="clearQueue()">Cancella coda</button>',
    '<b>Coda — <span id="qcount">0</span></b> <button class="btn danger" onclick="deleteSelected()">Elimina selezionati</button> <button class="btn danger" onclick="clearQueue()">Cancella coda</button><div class="small">★ porta subito in cima • ↑/↓ cambia priorità • seleziona più articoli e usa Elimina selezionati</div>'
)
core.PAGE = core.PAGE.replace(
    "q.forEach(x=>{let li=document.createElement('li');li.textContent=x.title;queue.appendChild(li)})",
    "q.forEach(x=>{let li=document.createElement('li');li.style.display='flex';li.style.gap='6px';li.style.alignItems='center';let cb=document.createElement('input');cb.type='checkbox';cb.className='qsel';cb.value=x.link;cb.style.width='auto';cb.style.margin='0';let tx=document.createElement('span');tx.textContent=x.title;tx.style.flex='1';let top=document.createElement('button');top.textContent='★';top.className='btn';top.onclick=()=>moveQueue(x.link,'top');let up=document.createElement('button');up.textContent='↑';up.className='btn';up.onclick=()=>moveQueue(x.link,'up');let dn=document.createElement('button');dn.textContent='↓';dn.className='btn';dn.onclick=()=>moveQueue(x.link,'down');let del=document.createElement('button');del.textContent='✕';del.className='btn danger';del.onclick=()=>deleteLinks([x.link]);li.append(cb,tx,top,up,dn,del);queue.appendChild(li)})"
)
core.PAGE = core.PAGE.replace(
    "async function clearQueue()",
    "async function moveQueue(link,action){await fetch('/queue_move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({link:link,action:action})});refresh()}async function deleteLinks(links){if(!links.length)return;if(confirm('Rimuovere dalla coda '+links.length+' articoli?')){await fetch('/queue_delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({links:links})});refresh()}}async function deleteSelected(){let links=[...document.querySelectorAll('.qsel:checked')].map(x=>x.value);if(!links.length){alert('Seleziona almeno un articolo.');return}deleteLinks(links)}async function clearQueue()"
)

# --- Aspect ratio non fatale ----------------------------------------------------
def is_unsupported_media_error(e):
    text=str(e).lower()
    if isinstance(e,core.MetaError):
        try:
            er=e.error or {};text += " "+str(er.get("message") or "").lower()+" "+str(er.get("error_user_title") or "").lower()+" "+str(er.get("error_user_msg") or "").lower()
        except Exception:pass
    return any(x in text for x in ["aspect ratio is not supported","unsupported aspect ratio","image aspect ratio"])

_original_publish=core.publish
def resilient_publish(token,user_id,article):
    try:return _original_publish(token,user_id,article)
    except Exception as e:
        if is_unsupported_media_error(e):raise RuntimeError("SKIP_MEDIA_ASPECT: "+str(e))
        raise
core.publish=resilient_publish
_original_rate=core.is_rate_limit_error
def keep_alive_error(e):return str(e).startswith("SKIP_MEDIA_ASPECT:") or _original_rate(e)
core.is_rate_limit_error=keep_alive_error


def bad_media_queue_watchdog():
    last_seen=""
    while True:
        try:
            err=str(core.state.get("last_error") or "")
            if err.startswith("SKIP_MEDIA_ASPECT:") and err!=last_seen:
                last_seen=err
                with core.store_lock:
                    s=core.load_store()
                    if s.get("queue"):
                        bad=s["queue"].pop(0);core.save_store(s);core.sync_state(s);core.log(f"⏭️ Immagine con proporzioni non supportate: articolo saltato senza fermare il bot: {bad.get('title','')}");core.waha("⚠️ Instagram: articolo saltato perché l'immagine ha proporzioni non supportate.\n\n"+str(bad.get("title") or ""))
                    s=core.load_store();s["cooldown_until"]=0;s["rate_limit_level"]=0;core.save_store(s);core.sync_state(s)
        except Exception as e:core.log(f"⚠️ Watchdog media incompatibile: {e}")
        time.sleep(2)


# --- Velocita adattiva -----------------------------------------------------------
def speed_watchdog():
    last=None
    while True:
        try:
            with core.store_lock:q=len(core.load_store().get("queue",[]))
            gap=BUSY_GAP if q>BUSY_QUEUE else NORMAL_GAP
            if core.PUBLISH_GAP!=gap:core.PUBLISH_GAP=gap
            if gap!=last:
                core.log(f"⚡ Ritmo pubblicazione: {gap} secondi tra i post (coda {q}).");last=gap
        except Exception as e:core.log(f"⚠️ Watchdog velocità: {e}")
        time.sleep(5)


# --- Probe Meta -----------------------------------------------------------------
def probe_guard_loop():
    while True:
        try:
            now=time.time()
            with core.store_lock:
                s=core.load_store();blocked=bool(s.get("limit_blocked",False));limit=s.get("adaptive_limit");queue=s.get("queue",[]);probe_after=float(s.get("probe_after",0) or 0);probe_active=bool(s.get("probe_active",False))
                if blocked and limit is not None and queue and not probe_active:
                    if probe_after<=0:s["probe_after"]=now+FIRST_PROBE_DELAY;core.save_store(s);core.log("🧪 Probe Meta programmato: primo tentativo controllato tra 2 minuti se la quota resta bloccata.")
                    elif now>=probe_after:
                        old_limit=int(limit);s["probe_previous_limit"]=old_limit;s["probe_active"]=True;s["probe_started_at"]=now;s["probe_last_published"]=str(core.state.get("last_published") or "");s["probe_after"]=now+PROBE_INTERVAL;s["adaptive_limit"]=None;s["limit_blocked"]=False;s["cooldown_until"]=0;core.save_store(s);core.sync_state(s);core.log(f"🧪 Probe Meta: quota ancora bloccata alla soglia {old_limit}. Autorizzato un solo tentativo reale.")
                elif probe_active:
                    previous=str(s.get("probe_last_published") or "");current=str(core.state.get("last_published") or "");started=float(s.get("probe_started_at",now))
                    if blocked:s["probe_active"]=False;s["probe_after"]=now+PROBE_INTERVAL;core.save_store(s);core.log(f"⏳ Probe Meta ancora bloccato: nuovo probe tra {PROBE_INTERVAL//3600} ore.")
                    elif current and current!=previous:s["probe_active"]=False;s["probe_after"]=0;core.save_store(s);core.log("🟢 Probe Meta riuscito: pubblicazione confermata, flusso normale riabilitato.")
                    elif now-started>=PROBE_TIMEOUT:
                        old=int(s.get("probe_previous_limit") or 49);s["adaptive_limit"]=old;s["limit_blocked"]=True;s["probe_active"]=False;s["probe_after"]=now+PROBE_INTERVAL;core.save_store(s);core.sync_state(s);core.log(f"⏳ Probe Meta senza esito entro {PROBE_TIMEOUT//60} minuti: guardia ripristinata a {old}, nuovo probe tra {PROBE_INTERVAL//3600} ore.")
                elif not blocked and probe_after and not probe_active:s["probe_after"]=0;core.save_store(s)
        except Exception as e:core.log(f"⚠️ Watchdog probe Meta: {e}")
        time.sleep(15)


try:
    with core.store_lock:
        s=core.load_store();removed=smart.prune_and_rank(s);core.save_store(s);core.sync_state(s)
    if removed:core.log(f"🧹 Migrazione coda v2.5.0: rimossi {removed} articoli scaduti/non prioritari; capienza massima 150.")
except Exception as e:core.log(f"⚠️ Migrazione coda v2.5.0 non riuscita: {e}")

threading.Thread(target=probe_guard_loop,daemon=True).start()
threading.Thread(target=bad_media_queue_watchdog,daemon=True).start()
threading.Thread(target=speed_watchdog,daemon=True).start()

if __name__=="__main__":
    core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
    core.log("🧠 Coda intelligente 150 + riordino manuale + eliminazione selettiva + guardia adattiva + probe Meta attivi.")
    core.log("⚡ Ritmo adattivo: 90 secondi normale, 60 secondi con oltre 50 articoli in coda.")
    core.log("🖼️ Errori permanenti di aspect ratio isolati: il singolo articolo viene saltato senza fermare il bot.")
    core.app.run(host="0.0.0.0",port=8080)
