"""Montagne & Paesi Instagram Bot v2.0 - official Meta API setup.

This first v2 build intentionally DOES NOT publish anything.
It only stores the Meta access token locally in /data/config.json and tests
that the official Instagram API can read the connected professional account.
"""
import html
import json
import os
from datetime import datetime

import requests
from flask import Flask, request, redirect, jsonify, render_template_string

APP_VERSION = "2.0.0-test"
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/data/config.json")
GRAPH_BASE = os.environ.get("META_GRAPH_BASE", "https://graph.instagram.com")
DEFAULT_IG_USER_ID = "17841409303885274"

app = Flask(__name__)
logs = []


def log(message):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    logs.append(line)
    del logs[:-600]
    print(line, flush=True)


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def meta_test(token, user_id):
    # Official Instagram Login API. Read-only test: no media container is created.
    url = f"{GRAPH_BASE}/v24.0/{user_id}"
    r = requests.get(url, params={"fields": "id,username", "access_token": token}, timeout=20)
    data = r.json() if r.content else {}
    if not r.ok:
        message = data.get("error", {}).get("message") if isinstance(data, dict) else None
        raise RuntimeError(message or f"Meta API HTTP {r.status_code}")
    return data


PAGE = '''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Montagne & Paesi - Instagram Meta API</title><style>
body{font-family:Arial,sans-serif;background:#f5f6f8;margin:0;padding:20px;color:#222}.box{max-width:900px;margin:auto;background:#fff;padding:22px;border-radius:12px;box-shadow:0 2px 12px #0001}input{width:100%;box-sizing:border-box;padding:10px;margin:5px 0 14px;border:1px solid #bbb;border-radius:7px}.btn{padding:10px 14px;margin:4px;border:0;border-radius:7px;cursor:pointer}.primary{background:#1769e0;color:#fff}.danger{background:#eee}pre{background:#111;color:#eee;padding:12px;border-radius:8px;min-height:160px;max-height:420px;overflow:auto;white-space:pre-wrap}.ok{color:#087a2f;font-weight:bold}.warn{color:#a45b00}.small{font-size:13px;color:#666}.row{display:flex;gap:8px;flex-wrap:wrap}</style></head><body><div class="box">
<h2>Montagne & Paesi → Instagram API ufficiale Meta</h2><div class="small">Versione <b>{{version}}</b></div>
<p class="warn"><b>Modalità sicurezza:</b> questa versione non può pubblicare post. Il test esegue esclusivamente una lettura dell'account tramite API Meta.</p>
<form method="post"><label>Instagram User ID</label><input name="ig_user_id" value="{{user_id}}" autocomplete="off"><label>Instagram Access Token</label><input type="password" name="meta_access_token" value="" autocomplete="new-password" placeholder="{% if token_saved %}Token già salvato - lascia vuoto per mantenerlo{% else %}Incolla qui il token generato da Meta{% endif %}"><div class="small">Il token viene salvato solamente in /data/config.json del container e non viene mostrato nel pannello.</div><div class="row" style="margin-top:12px"><button class="btn primary" formaction="/save_meta" formmethod="post">Salva configurazione Meta</button><button class="btn primary" formaction="/test_meta" formmethod="post">Test API Meta</button></div></form>
<h3>Stato</h3><div id="state">{{state}}</div><h3>Log</h3><div class="row"><button class="btn danger" type="button" onclick="copyLog()">Copia log</button><button class="btn danger" type="button" onclick="clearLog()">Azzera log</button></div><pre id="log"></pre>
<script>async function refresh(){try{let r=await fetch('/status');let s=await r.json();document.getElementById('state').textContent=s.meta_connected?('API Meta collegata: @'+s.username):'API Meta: non verificata';let l=await fetch('/logs');document.getElementById('log').textContent=await l.text();}catch(e){}}async function copyLog(){let t=document.getElementById('log').textContent||'';try{await navigator.clipboard.writeText(t);alert('Log copiato.')}catch(e){alert('Copia non disponibile.')}}async function clearLog(){await fetch('/clear_logs',{method:'POST'});refresh()}setInterval(refresh,2500);refresh();</script></div></body></html>'''

state = {"meta_connected": False, "username": "", "last_error": ""}


@app.get("/")
def home():
    cfg = load_config()
    return render_template_string(PAGE, version=APP_VERSION, user_id=html.escape(str(cfg.get("meta_ig_user_id") or DEFAULT_IG_USER_ID)), token_saved=bool(cfg.get("meta_access_token")), state="API Meta collegata: @" + state["username"] if state["meta_connected"] else "API Meta: non verificata")


@app.post("/save_meta")
def save_meta():
    cfg = load_config()
    user_id = request.form.get("ig_user_id", "").strip() or DEFAULT_IG_USER_ID
    token = request.form.get("meta_access_token", "").strip()
    cfg["meta_ig_user_id"] = user_id
    if token:
        cfg["meta_access_token"] = token
    save_config(cfg)
    log("💾 Configurazione Meta salvata localmente. Token non visualizzato.")
    return redirect("/")


@app.post("/test_meta")
def test_meta_route():
    cfg = load_config()
    user_id = request.form.get("ig_user_id", "").strip() or str(cfg.get("meta_ig_user_id") or DEFAULT_IG_USER_ID)
    entered_token = request.form.get("meta_access_token", "").strip()
    token = entered_token or str(cfg.get("meta_access_token") or "").strip()
    if not token:
        log("❌ Token Meta mancante. Incollalo nel campo Access Token e salva.")
        return redirect("/")
    if entered_token:
        cfg["meta_access_token"] = entered_token
    cfg["meta_ig_user_id"] = user_id
    save_config(cfg)
    try:
        log("🔎 Test API ufficiale Meta in corso (sola lettura, nessuna pubblicazione)...")
        data = meta_test(token, user_id)
        username = str(data.get("username") or "")
        returned_id = str(data.get("id") or "")
        state.update(meta_connected=True, username=username, last_error="")
        log(f"✅ API Meta collegata correttamente: @{username} • ID {returned_id}")
    except Exception as exc:
        state.update(meta_connected=False, username="", last_error=str(exc))
        log(f"❌ Test API Meta fallito: {exc}")
    return redirect("/")


@app.get("/status")
def status():
    return jsonify({"version": APP_VERSION, **state})


@app.get("/logs")
def get_logs():
    return "\n".join(logs[-600:]), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.post("/clear_logs")
def clear_logs():
    logs.clear()
    state["last_error"] = ""
    return jsonify({"ok": True})


if __name__ == "__main__":
    log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
    log("🔒 instagrapi disattivato: nessun login simulato e nessuna pubblicazione automatica.")
    log("🌐 Pronto per il test in sola lettura dell'API ufficiale Meta.")
    app.run(host="0.0.0.0", port=8080)
