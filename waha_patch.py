"""WAHA dashboard/config patch for Instagram bot v1.1.4."""
import safe_app as safe

core = safe.core
APP_VERSION = "1.1.4"
core.metrics["version"] = APP_VERSION

# Keep the original config loader from app.py. WAHA values are stored in /data/config.json.
base_load_config = safe._original_load_config
base_save_config = safe._original_save_config


def load_config_v114():
    cfg = base_load_config()
    cfg.setdefault("waha_url", safe.DEFAULT_WAHA_URL)
    cfg.setdefault("waha_session", safe.DEFAULT_WAHA_SESSION)
    cfg.setdefault("waha_number", safe.DEFAULT_WAHA_NUMBER)
    cfg.setdefault("waha_api_key", "")
    return cfg


core.load_config = load_config_v114


def notification_config_v114():
    cfg = core.load_config()
    url = str(cfg.get("waha_url") or safe.DEFAULT_WAHA_URL).strip().rstrip("/")
    if url.endswith("/dashboard"):
        url = url[:-10].rstrip("/")
    session = str(cfg.get("waha_session") or safe.DEFAULT_WAHA_SESSION).strip()
    number = str(cfg.get("waha_number") or safe.DEFAULT_WAHA_NUMBER).strip()
    api_key = str(cfg.get("waha_api_key") or "").strip()
    digits = "".join(ch for ch in number if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("3"):
        digits = "39" + digits
    return url, session, digits, api_key


def send_waha_notification_v114(message: str) -> bool:
    url, session, number, api_key = notification_config_v114()
    if not url or not session or not number:
        core.log("⚠️ Notifica WAHA non configurata.")
        return False
    if not api_key:
        core.log("⚠️ WAHA API Key mancante: inseriscila nella dashboard e premi Salva WAHA.")
        return False
    try:
        endpoint = f"{url}/api/sendText"
        payload = {"session": session, "chatId": f"{number}@c.us", "text": message}
        headers = {"X-Api-Key": api_key}
        r = core.requests.post(endpoint, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        core.log("📲 Notifica inviata tramite WAHA.")
        return True
    except Exception as exc:
        core.log(f"⚠️ Invio notifica WAHA fallito: {exc}")
        return False


# Replace globals used by stop_with_error and the existing /test_waha route.
safe.send_waha_notification = send_waha_notification_v114


@core.app.post("/save_waha")
def save_waha():
    cfg = core.load_config()
    cfg["waha_url"] = core.request.form.get("waha_url", "").strip() or safe.DEFAULT_WAHA_URL
    cfg["waha_session"] = core.request.form.get("waha_session", "").strip() or safe.DEFAULT_WAHA_SESSION
    cfg["waha_number"] = core.request.form.get("waha_number", "").strip() or safe.DEFAULT_WAHA_NUMBER
    new_key = core.request.form.get("waha_api_key", "").strip()
    # Empty field means keep the already saved key.
    if new_key:
        cfg["waha_api_key"] = new_key
    base_save_config(cfg)
    core.log("💾 Configurazione WAHA salvata. La API Key non viene mostrata nella dashboard.")
    return core.redirect("/")


# Override status so the UI reports the current wrapper version.
def status_v114():
    with core.bot_lock:
        thread_alive = core.bot_thread is not None and core.bot_thread.is_alive()
    with core.metrics_lock:
        last_error = str(core.metrics.get("last_error", ""))
    blocked = "integrity" in last_error.lower() or "restricted from uploading" in last_error.lower()
    return core.jsonify({"running": bool(thread_alive and not blocked), "blocked": blocked, "version": APP_VERSION})


core.app.view_functions["status"] = status_v114

cfg = core.load_config()
waha_url = str(cfg.get("waha_url", safe.DEFAULT_WAHA_URL)).replace('"', '&quot;')
waha_session = str(cfg.get("waha_session", safe.DEFAULT_WAHA_SESSION)).replace('"', '&quot;')
waha_number = str(cfg.get("waha_number", safe.DEFAULT_WAHA_NUMBER)).replace('"', '&quot;')
key_saved = bool(str(cfg.get("waha_api_key", "")).strip())
key_state = "API Key salvata" if key_saved else "API Key NON configurata"

# safe_app already changed the original heading to 1.1.3: bump the visible version.
core.PAGE = core.PAGE.replace("Versione <b>1.1.3</b>", f"Versione <b>{APP_VERSION}</b>")
core.PAGE = core.PAGE.replace("Sicurezza 1.1.3:", "Sicurezza 1.1.4:")

waha_box = f'''
<div style="margin-top:18px;padding:14px;border:1px solid #ddd;border-radius:10px;">
  <h3 style="margin-top:0;">Notifiche WhatsApp WAHA</h3>
  <div class="small" style="margin-bottom:10px;">{key_state}. La chiave viene salvata in /data/config.json e non viene mai mostrata a video.</div>
  <label>WAHA URL</label>
  <input name="waha_url" value="{waha_url}" placeholder="http://192.168.1.69:3000">
  <label>Sessione</label>
  <input name="waha_session" value="{waha_session}" placeholder="default">
  <label>Numero destinatario</label>
  <input name="waha_number" value="{waha_number}" placeholder="393xxxxxxxxx">
  <label>WAHA API Key</label>
  <input type="password" name="waha_api_key" value="" autocomplete="new-password" placeholder="Lascia vuoto per mantenere la chiave già salvata">
  <div style="margin-top:10px;">
    <button class="btn" formaction="/save_waha" formmethod="post">Salva WAHA</button>
    <button class="btn" formaction="/test_waha" formmethod="post">Test WhatsApp WAHA</button>
  </div>
</div>
'''

# Insert the configuration panel inside the existing main form, before its closing tag.
marker = "</form>"
if marker in core.PAGE:
    core.PAGE = core.PAGE.replace(marker, waha_box + marker, 1)


if __name__ == "__main__":
    core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
    core.log("📲 WAHA 1.1.4: API Key configurabile dalla dashboard.")
    core.app.run(host="0.0.0.0", port=8080)
