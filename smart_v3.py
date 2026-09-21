"""Montagne & Paesi Instagram Bot v2.6.4 - adaptive speed, manual queue controls, resilient media, guarded Meta probes."""
import os
import threading
import time

os.environ["TZ"] = "Europe/Rome"
if hasattr(time, "tzset"):
    time.tzset()

import smart_v2 as smart
import meta_v2 as core
from flask import request, jsonify

APP_VERSION = "2.6.3"
PROBE_INTERVAL = 30 * 60
FIRST_PROBE_DELAY = 30 * 60
PROBE_TIMEOUT = 180
NORMAL_GAP = 90
BUSY_GAP = 60
BUSY_QUEUE = 50
MIDNIGHT_KEEP = 0

smart.MAX_QUEUE = 150
core.APP_VERSION = APP_VERSION
core.PUBLISH_GAP = NORMAL_GAP

# --- Ordine manuale persistente -------------------------------------------------
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

# --- Errori media non fatali ----------------------------------------------------
def is_unsupported_media_error(e):
    text=str(e).lower()
    if isinstance(e,core.MetaError):
        try:
            er=e.error or {};text += " "+str(er.get("message") or "").lower()+" "+str(er.get("error_user_title") or "").lower()+" "+str(er.get("error_user_msg") or "").lower()
        except Exception:pass
    return any(x in text for x in ["aspect ratio is not supported","unsupported aspect ratio","image aspect ratio","only photo or video can be accepted as media type","only photo or video can be accepted","invalid media type","unsupported media type"])

_original_publish=core.publish
def resilient_publish(token,user_id,article):
    try:return _original_publish(token,user_id,article)
    except Exception as e:
        if is_unsupported_media_error(e):raise RuntimeError("SKIP_MEDIA_INVALID: "+str(e))
        raise
core.publish=resilient_publish

# Immagine assente: problema del singolo articolo, mai arrestare tutto il bot.
_original_build=core.build_article
def resilient_build(item,entry=None):
    try:return _original_build(item,entry)
    except Exception as e:
        t=str(e)
        if "immagine in evidenza pubblica non trovata" in t.lower():
            raise RuntimeError("TEMP_IMAGE_MISSING: "+t)
        # I nuovi precheck del core devono mantenere il marker, altrimenti
        # il core li tratta come errori generici e applica un cooldown di 5 minuti.
        if t.startswith("MEDIA_PRECHECK_INVALID:") or t.startswith("MEDIA_PRECHECK_TEMP:"):
            raise
        raise
core.build_article=resilient_build

_original_rate=core.is_rate_limit_error
def keep_alive_error(e):
    t=str(e)
    return t.startswith("SKIP_MEDIA_INVALID:") or t.startswith("TEMP_IMAGE_MISSING:") or t.startswith("MEDIA_PRECHECK_INVALID:") or t.startswith("MEDIA_PRECHECK_TEMP:") or _original_rate(e)
core.is_rate_limit_error=keep_alive_error

def media_queue_watchdog():
    last_seen=""
    while True:
        try:
            err=str(core.state.get("last_error") or "")
            if (err.startswith("SKIP_MEDIA_INVALID:") or err.startswith("MEDIA_PRECHECK_INVALID:")) and err!=last_seen:
                last_seen=err
                with core.store_lock:
                    s=core.load_store()
                    if s.get("queue"):
                        bad=s["queue"].pop(0);core.save_store(s);core.sync_state(s);core.log(f"⏭️ Media non compatibile con Instagram: articolo saltato senza fermare il bot: {bad.get('title','')}");core.waha("⚠️ Instagram: articolo saltato perché il media non è compatibile.\n\n"+str(bad.get("title") or ""))
                    s=core.load_store();s["cooldown_until"]=0;s["rate_limit_level"]=0;core.save_store(s);core.sync_state(s)
            elif (err.startswith("TEMP_IMAGE_MISSING:") or err.startswith("MEDIA_PRECHECK_TEMP:")) and err!=last_seen:
                last_seen=err
                # Sposta in fondo alla coda: potrà essere riprovato più tardi senza bloccare gli altri.
                with core.store_lock:
                    s=core.load_store();q=s.get("queue",[])
                    if q:
                        bad=q.pop(0);bad["image_retry_count"]=int(bad.get("image_retry_count",0))+1
                        if bad["image_retry_count"] < 3:
                            q.append(bad);core.log(f"🖼️ Immagine non disponibile: articolo spostato in fondo alla coda (tentativo {bad['image_retry_count']}/3): {bad.get('title','')}")
                        else:
                            core.log(f"⏭️ Immagine ancora assente dopo 3 tentativi: articolo rimosso dalla coda: {bad.get('title','')}")
                            core.waha("⚠️ Instagram: articolo saltato dopo 3 tentativi perché l'immagine non è disponibile.\n\n"+str(bad.get("title") or ""))
                        s["queue"]=q;s["cooldown_until"]=0;s["rate_limit_level"]=0;core.save_store(s);core.sync_state(s)
        except Exception as e:core.log(f"⚠️ Watchdog media: {e}")
        time.sleep(2)

# --- Velocita adattiva ----------------------------------------------------------
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

# Corregge il vecchio testo "2 minuti" del core.
_core_log=core.log
def patched_log(msg):
    if msg=="▶️ Publisher adattivo avviato: 2 minuti tra i post, con guardia automatica sul limite Meta.":
        msg="▶️ Publisher adattivo avviato: 90 secondi normali / 60 secondi con coda >50, con guardia Meta."
    _core_log(msg)
core.log=patched_log

# --- Pulizia automatica coda a mezzanotte --------------------------------------
# La coda del giorno precedente non deve mai arrivare al giorno successivo.
# Salviamo l'ultimo giorno controllato nello store: così la pulizia funziona
# sia al cambio data con container acceso, sia dopo un riavvio avvenuto dopo mezzanotte.
def midnight_queue_cleanup():
    while True:
        try:
            day=time.strftime("%Y-%m-%d",time.localtime())
            with core.store_lock:
                s=core.load_store();last=str(s.get("queue_cleanup_day") or "")
                if last!=day:
                    q=s.get("queue",[]);removed=len(q)
                    s["queue"]=[];s["queue_cleanup_day"]=day
                    # Un eventuale cooldown/probe del giorno prima non deve trattenere i nuovi articoli.
                    s["cooldown_until"]=0;s["probe_after"]=0;s["probe_active"]=False
                    core.save_store(s);core.sync_state(s)
                    if removed:core.log(f"🌙 Nuovo giorno: coda del giorno precedente svuotata, rimossi {removed} articoli.")
                    else:core.log("🌙 Nuovo giorno: coda già vuota.")
        except Exception as e:core.log(f"⚠️ Pulizia giornaliera coda fallita: {e}")
        time.sleep(20)

# --- Probe Meta ----------------------------------------------------------------
# La quota letta da Meta è informativa: quando siamo alla soglia appresa facciamo
# un solo tentativo reale ogni 30 minuti. Se riesce, richiudiamo la guardia e
# riproveremo più tardi; se Meta risponde 2207042, il core riapprende il blocco.
def probe_guard_loop():
    while True:
        try:
            now=time.time()
            used=core.state.get("quota_usage")
            with core.store_lock:
                s=core.load_store();limit=s.get("adaptive_limit");queue=s.get("queue",[]);probe_after=float(s.get("probe_after",0) or 0);probe_active=bool(s.get("probe_active",False))
                at_guard=bool(limit is not None and used is not None and int(used)>=int(limit))
                if at_guard and queue and not probe_active:
                    if probe_after<=0:
                        s["probe_after"]=now+FIRST_PROBE_DELAY;core.save_store(s);core.log("🧪 Soglia Meta raggiunta: probe reale programmato tra 30 minuti.")
                    elif now>=probe_after:
                        old_limit=int(limit);s["probe_previous_limit"]=old_limit;s["probe_active"]=True;s["probe_started_at"]=now;s["probe_last_published"]=str(core.state.get("last_published") or "");s["probe_after"]=now+PROBE_INTERVAL;s["adaptive_limit"]=None;s["limit_blocked"]=False;s["cooldown_until"]=0;core.save_store(s);core.sync_state(s);core.log(f"🧪 Probe Meta controllato: autorizzato UN tentativo oltre la soglia {old_limit}.")
                elif probe_active:
                    previous=str(s.get("probe_last_published") or "");current=str(core.state.get("last_published") or "");started=float(s.get("probe_started_at",now))
                    if bool(s.get("limit_blocked",False)):
                        old=int(s.get("probe_previous_limit") or 49);s["adaptive_limit"]=old;s["probe_active"]=False;s["probe_after"]=now+PROBE_INTERVAL;core.save_store(s);core.sync_state(s);core.log(f"⏳ Probe Meta bloccato: guardia {old} ripristinata; nuovo tentativo tra {PROBE_INTERVAL//60} minuti.")
                    elif current and current!=previous:
                        old=int(s.get("probe_previous_limit") or 49);s["adaptive_limit"]=old;s["limit_blocked"]=False;s["probe_active"]=False;s["probe_after"]=now+PROBE_INTERVAL;core.save_store(s);core.sync_state(s);core.log(f"✅ Probe Meta riuscito oltre soglia: guardia {old} ripristinata; nuovo probe tra {PROBE_INTERVAL//60} minuti se necessario.")
                    elif now-started>=PROBE_TIMEOUT:
                        old=int(s.get("probe_previous_limit") or 49);s["adaptive_limit"]=old;s["limit_blocked"]=True;s["probe_active"]=False;s["probe_after"]=now+PROBE_INTERVAL;core.save_store(s);core.sync_state(s);core.log(f"⏳ Probe Meta senza esito: guardia {old} ripristinata.")
                elif not at_guard and probe_after and not probe_active:
                    s["probe_after"]=0;core.save_store(s)
        except Exception as e:core.log(f"⚠️ Watchdog probe Meta: {e}")
        time.sleep(10)

try:
    with core.store_lock:
        s=core.load_store();removed=smart.prune_and_rank(s);core.save_store(s);core.sync_state(s)
    if removed:core.log(f"🧹 Migrazione coda v2.5.2: rimossi {removed} articoli scaduti/non prioritari; capienza massima 150.")
except Exception as e:core.log(f"⚠️ Migrazione coda v2.5.2 non riuscita: {e}")

def quota_minute_loop():
    while True:
        try:
            cfg=core.load_config();token=str(cfg.get("meta_access_token") or "");uid=str(cfg.get("meta_ig_user_id") or core.DEFAULT_IG_USER_ID)
            if token and core.state.get("running"):
                before=core.state.get("quota_usage");core.publishing_limit(token,uid,True);after=core.state.get("quota_usage")
                if before is not None and after is not None and int(after)<int(before):
                    core.log(f"📉 Quota Meta scesa: {before} → {after}. Il publisher può sfruttare subito gli slot liberati.")
                    with core.store_lock:
                        s=core.load_store();limit=s.get("adaptive_limit")
                        if limit is not None and int(after)<int(limit):
                            s["limit_blocked"]=False;s["cooldown_until"]=0;s["probe_after"]=0;core.save_store(s);core.sync_state(s)
        except Exception as e:core.log(f"⚠️ Controllo quota minuto: {e}")
        time.sleep(60)

threading.Thread(target=probe_guard_loop,daemon=True).start()
threading.Thread(target=quota_minute_loop,daemon=True).start()
threading.Thread(target=media_queue_watchdog,daemon=True).start()
threading.Thread(target=speed_watchdog,daemon=True).start()
threading.Thread(target=midnight_queue_cleanup,daemon=True).start()

if __name__=="__main__":
    core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
    core.log("🕒 Fuso orario forzato: Europe/Rome.")
    core.log("🧠 Coda 150 + priorità manuale + eliminazione selettiva + guardia adattiva.")
    core.log("🌙 Cambio giorno: la coda residua del giorno precedente viene svuotata completamente, anche dopo un riavvio.")
    core.log("🧪 Alla soglia Meta: quota controllata ogni minuto; probe reale di sicurezza ogni 30 minuti.")
    core.log("⚡ Ritmo adattivo: 90 secondi normale, 60 secondi con oltre 50 articoli in coda.")
    core.log("🖼️ Pre-controllo media: verifica raggiungibilità URL; la compatibilità finale viene verificata direttamente da Meta.")
    core.app.run(host="0.0.0.0",port=8080)
