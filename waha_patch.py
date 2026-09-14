"""Dashboard/diagnostics patch for Instagram bot v1.1.7."""
import safe_app as safe

core = safe.core
APP_VERSION = "1.1.7"
core.metrics["version"] = APP_VERSION
base_load_config = safe._original_load_config
base_save_config = safe._original_save_config


def load_config_v117():
    cfg = base_load_config()
    cfg.setdefault("waha_url", safe.DEFAULT_WAHA_URL)
    cfg.setdefault("waha_session", safe.DEFAULT_WAHA_SESSION)
    cfg.setdefault("waha_number", safe.DEFAULT_WAHA_NUMBER)
    cfg.setdefault("waha_api_key", "")
    return cfg
core.load_config = load_config_v117


def notification_config_v117():
    cfg = core.load_config()
    url = str(cfg.get("waha_url") or safe.DEFAULT_WAHA_URL).strip().rstrip("/")
    if url.endswith("/dashboard"):
        url = url[:-10].rstrip("/")
    session = str(cfg.get("waha_session") or safe.DEFAULT_WAHA_SESSION).strip()
    number = str(cfg.get("waha_number") or safe.DEFAULT_WAHA_NUMBER).strip()
    api_key = str(cfg.get("waha_api_key") or "").strip()
    digits = "".join(ch for ch in number if ch.isdigit())
    if digits.startswith("00"): digits = digits[2:]
    if len(digits) == 10 and digits.startswith("3"): digits = "39" + digits
    return url, session, digits, api_key


def send_waha_notification_v117(message):
    url, session, number, api_key = notification_config_v117()
    if not url or not session or not number:
        core.log("⚠️ Notifica WAHA non configurata.")
        return False
    if not api_key:
        core.log("⚠️ WAHA API Key mancante: inseriscila nella dashboard e premi Salva WAHA.")
        return False
    try:
        r = core.requests.post(f"{url}/api/sendText", json={"session":session,"chatId":f"{number}@c.us","text":message}, headers={"X-Api-Key":api_key}, timeout=10)
        r.raise_for_status()
        core.log("📲 Notifica inviata tramite WAHA.")
        return True
    except Exception as exc:
        core.log(f"⚠️ Invio notifica WAHA fallito: {exc}")
        return False
safe.send_waha_notification = send_waha_notification_v117


@core.app.post("/save_waha")
def save_waha():
    cfg = core.load_config()
    cfg["waha_url"] = core.request.form.get("waha_url", "").strip() or safe.DEFAULT_WAHA_URL
    cfg["waha_session"] = core.request.form.get("waha_session", "").strip() or safe.DEFAULT_WAHA_SESSION
    cfg["waha_number"] = core.request.form.get("waha_number", "").strip() or safe.DEFAULT_WAHA_NUMBER
    new_key = core.request.form.get("waha_api_key", "").strip()
    if new_key: cfg["waha_api_key"] = new_key
    base_save_config(cfg)
    core.log("💾 Configurazione WAHA salvata. La API Key non viene mostrata nella dashboard.")
    return core.redirect("/")


@core.app.post("/clear_logs")
def clear_logs():
    with core.logs_lock: core.logs.clear()
    core.set_metric("last_error", "")
    return core.jsonify({"ok": True})


def status_v117():
    with core.bot_lock:
        thread_alive = core.bot_thread is not None and core.bot_thread.is_alive()
    with core.metrics_lock:
        last_error = str(core.metrics.get("last_error", ""))
    blocked = "integrity" in last_error.lower() or "restricted from uploading" in last_error.lower()
    return core.jsonify({"running": bool(thread_alive and not blocked), "blocked": blocked, "version": APP_VERSION})
core.app.view_functions["status"] = status_v117

# v1.1.7: apply instagrapi's own supported/current app profile explicitly and log
# exactly what the client will present. No guessed version numbers are hard-coded.
BasePoster = safe.SafeInstagramPoster
class DiagnosticInstagramPoster(BasePoster):
    def __init__(self):
        super().__init__()
        try:
            self.cl.set_app()
            core.log("📱 Profilo app Instagram applicato tramite instagrapi set_app().")
        except Exception as exc:
            core.log(f"⚠️ set_app() non disponibile/applicabile: {exc}")
        self._log_profile()

    def _log_profile(self):
        try:
            ds = getattr(self.cl, "device_settings", None) or (self.cl.get_settings() or {}).get("device_settings", {}) or {}
            app_version = ds.get("app_version", "?")
            version_code = ds.get("version_code", "?")
            android_version = ds.get("android_version", "?")
            model = ds.get("model", "?")
            core.log(f"🔎 Profilo Instagram effettivo: app={app_version} • version_code={version_code} • Android={android_version} • model={model}")
        except Exception as exc:
            core.log(f"⚠️ Impossibile leggere diagnostica profilo Instagram: {exc}")

    def try_restore_settings_session(self):
        s = core.load_ig_settings()
        if not s:
            return False
        try:
            # Current instagrapi supports upgrading the saved app profile while restoring
            # session/device identifiers. Use it when available, then explicitly set_app.
            try:
                self.cl.set_settings(s)
                self.cl.set_app()
            except Exception:
                self.cl.set_settings(s)
            self._log_profile()
            self.cl.get_timeline_feed()
            self.logged_in = True
            core.log("♻️ Sessione Instagram ripristinata con profilo app corrente.")
            return True
        except Exception as exc:
            core.log(f"⚠️ Sessione salvata non valida, la resetto: {exc}")
            core.delete_ig_settings()
            self.logged_in = False
            return False

safe.SafeInstagramPoster = DiagnosticInstagramPoster
core.InstagramPoster = DiagnosticInstagramPoster

cfg = core.load_config()
waha_url = str(cfg.get("waha_url", safe.DEFAULT_WAHA_URL)).replace('"','&quot;')
waha_session = str(cfg.get("waha_session", safe.DEFAULT_WAHA_SESSION)).replace('"','&quot;')
waha_number = str(cfg.get("waha_number", safe.DEFAULT_WAHA_NUMBER)).replace('"','&quot;')
key_saved = bool(str(cfg.get("waha_api_key", "")).strip())
key_state = "API Key salvata" if key_saved else "API Key NON configurata"

# Remove the old wrapper version line, then add exactly one current version line.
core.PAGE = core.PAGE.replace('<div class="small" style="margin-bottom:8px;">Versione <b>1.1.6</b></div>', '')
core.PAGE = core.PAGE.replace('Versione <b>1.1.3</b>', '')
core.PAGE = core.PAGE.replace('<h2 style="margin:0 0 6px;">Montagne & Paesi → Instagram Bot (HAOS)</h2>', '<h2 style="margin:0 0 6px;">Montagne & Paesi → Instagram Bot (HAOS)</h2><div class="small" style="margin-bottom:8px;">Versione <b>1.1.7</b></div>')

waha_box = f'''<div style="margin-top:18px;padding:14px;border:1px solid #ddd;border-radius:10px;"><h3 style="margin-top:0;">Notifiche WhatsApp WAHA</h3><div class="small" style="margin-bottom:10px;">{key_state}. La chiave viene salvata in /data/config.json e non viene mai mostrata a video.</div><label>WAHA URL</label><input name="waha_url" value="{waha_url}"><label>Sessione</label><input name="waha_session" value="{waha_session}"><label>Numero destinatario</label><input name="waha_number" value="{waha_number}"><label>WAHA API Key</label><input type="password" name="waha_api_key" value="" autocomplete="new-password" placeholder="Lascia vuoto per mantenere la chiave già salvata"><div style="margin-top:10px;"><button class="btn" formaction="/save_waha" formmethod="post">Salva WAHA</button><button class="btn" formaction="/test_waha" formmethod="post">Test WhatsApp WAHA</button></div></div>'''
if "</form>" in core.PAGE: core.PAGE = core.PAGE.replace("</form>", waha_box + "</form>", 1)

core.PAGE = core.PAGE.replace('<button class="btn start" formaction="/start" formmethod="post">Avvia bot</button>', '<button class="btn start" formaction="/start" formmethod="post">Avvia bot</button><button class="btn stop" formaction="/test_upload" formmethod="post" onclick="return confirm(\'Il test pubblicherà DAVVERO l’ultimo articolo del feed su Instagram una sola volta. Continuare?\')">Test pubblicazione singola</button><button class="btn" formaction="/test_waha" formmethod="post">Test WhatsApp WAHA</button>')
old = '<button class="btn stop" onclick="refresh()">Aggiorna</button>'
new = '''<div class="row" style="margin-top:0;"><button class="btn stop" type="button" onclick="copyLog()">Copia log</button><button class="btn stop" type="button" onclick="clearLog()">Azzera log</button><button class="btn stop" type="button" onclick="refresh()">Aggiorna</button></div>'''
core.PAGE = core.PAGE.replace(old, new)
js = r'''async function copyLog(){const text=document.getElementById('log').textContent||'';try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);}else{const ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();document.execCommand('copy');ta.remove();}alert('Log copiato negli appunti.');}catch(e){alert('Impossibile copiare automaticamente il log.');}}async function clearLog(){if(!confirm('Azzerare il log e cancellare l’ultimo errore visualizzato?'))return;try{const r=await fetch('/clear_logs',{method:'POST'});if(!r.ok)throw new Error('HTTP '+r.status);document.getElementById('log').textContent='';await refresh();}catch(e){alert('Errore durante l’azzeramento del log: '+e.message);}}'''
core.PAGE = core.PAGE.replace("async function refresh(){", js + "\nasync function refresh(){", 1)

if __name__ == "__main__":
    core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
    core.log("📱 Instagram 1.1.7: set_app() esplicito + diagnostica profilo effettivo.")
    core.log("📲 WAHA attivo. 🧹 Copia/Azzera log attivi.")
    core.app.run(host="0.0.0.0", port=8080)
