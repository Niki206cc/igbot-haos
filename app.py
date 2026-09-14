import os
import json
import time
import threading
import re
from datetime import datetime

import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, redirect, render_template_string, jsonify

from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ChallengeRequired, TwoFactorRequired

# =============================== CONFIG / PATHS ===============================
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/data/config.json")
LAST_POST_PATH = os.environ.get("LAST_POST_PATH", "/data/last_post.txt")
IG_SETTINGS_PATH = os.environ.get("IG_SETTINGS_PATH", "/data/ig_settings.json")
DEVICE_SEED_PATH = os.environ.get("DEVICE_SEED_PATH", "/data/device_seed.json")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))
DEFAULT_RSS = os.environ.get("DEFAULT_RSS", "https://www.montagneepaesi.com/feed/")
HUB_LINK = os.environ.get("HUB_LINK", "www.montagneepaesi.com/instagram")
WA_CHANNEL_URL = os.environ.get("WA_CHANNEL_URL", "https://whatsapp.com/channel/0029Vb7fcHT8aKvFAuCIfm0c")
IMAGES_DIR = os.environ.get("IMAGES_DIR", "/data/images")
LATEST_IMG_PATH = os.path.join(IMAGES_DIR, "latest.jpg")

logs = []
logs_lock = threading.Lock()
def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with logs_lock:
        logs.append(line)
        if len(logs) > 600: logs[:] = logs[-600:]

metrics = {"running":False,"last_title":"","last_link":"","last_published_at":"","last_error":"","posts_count":0}
metrics_lock = threading.Lock()
def set_metric(key,value):
    with metrics_lock: metrics[key]=value
def inc_posts_count():
    with metrics_lock: metrics["posts_count"]=int(metrics.get("posts_count",0))+1

bot_thread=None
bot_lock=threading.Lock()
stop_event=threading.Event()

def _ensure_parent(path):
    parent=os.path.dirname(path)
    if parent: os.makedirs(parent,exist_ok=True)
def _read_json(path,default):
    if os.path.exists(path):
        try:
            with open(path,"r",encoding="utf-8") as f:return json.load(f)
        except Exception:return default
    return default
def _write_json(path,data):
    _ensure_parent(path)
    with open(path,"w",encoding="utf-8") as f:json.dump(data,f,ensure_ascii=False,indent=2)
def load_config():
    base={"rss_url":DEFAULT_RSS,"username":"","password":"","sessionid":"","sessionid_only_test":True}
    cfg=_read_json(CONFIG_PATH,{})
    if isinstance(cfg,dict):base.update(cfg)
    return base
def save_config(cfg):_write_json(CONFIG_PATH,cfg)
def get_last_posted_url():
    if os.path.exists(LAST_POST_PATH):
        try:
            with open(LAST_POST_PATH,"r",encoding="utf-8") as f:return f.read().strip()
        except Exception:return ""
    return ""
def save_last_posted_url(url):
    _ensure_parent(LAST_POST_PATH)
    with open(LAST_POST_PATH,"w",encoding="utf-8") as f:f.write(url.strip())
def load_ig_settings():return _read_json(IG_SETTINGS_PATH,None)
def save_ig_settings(settings):
    try:_write_json(IG_SETTINGS_PATH,settings)
    except Exception:pass
def delete_ig_settings():
    try:
        if os.path.exists(IG_SETTINGS_PATH):os.remove(IG_SETTINGS_PATH)
    except Exception:pass
def load_device_seed():return _read_json(DEVICE_SEED_PATH,{})
def save_device_seed(seed):_write_json(DEVICE_SEED_PATH,seed)

def clean_text(s):
    if not s:return ""
    s=BeautifulSoup(s,"html.parser").get_text(" ",strip=True)
    return re.sub(r"\s+"," ",s).strip()
def clamp_caption(caption,max_len=2200):
    caption=caption.strip()
    return caption if len(caption)<=max_len else caption[:max_len-1].rstrip()+"…"
def hashtags_from_title(title,max_tags=8):
    stopwords={"di","a","da","in","con","su","per","tra","fra","il","lo","la","i","gli","le","un","una","uno","e","è","del","della","dei","delle","al","allo","alla","agli","alle","ai","dal","dallo","dalla","dai","dalle","nel","nello","nella","nei","nelle"}
    words=re.findall(r"[a-zA-ZàèéìòùÀÈÉÌÒÙ]+",title.lower())
    tags=[f"#{w}" for w in words if len(w)>=4 and w not in stopwords]
    return " ".join(["#montagneepaesi","#news","#notizie","#ultimora","#flashnews"]+tags[:max_tags])

def get_latest_entry(rss_url):
    try:
        feed=feedparser.parse(rss_url)
        return feed.entries[0] if feed.entries else None
    except Exception:return None
def get_featured_image_url(article_url):
    try:
        r=requests.get(article_url,timeout=20,headers={"User-Agent":"Mozilla/5.0"});r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        tag=soup.find("meta",property="og:image")
        return tag.get("content","").strip() if tag else ""
    except Exception:return ""
def get_article_excerpt(article_url,max_chars=900):
    try:
        r=requests.get(article_url,timeout=20,headers={"User-Agent":"Mozilla/5.0"});r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        desc=soup.find("meta",attrs={"name":"description"})
        txt=(desc.get("content","") if desc else "")
        if not txt:
            article=soup.find("article")
            txt=article.get_text(" ",strip=True) if article else ""
        txt=clean_text(txt)
        return txt[:max_chars].rstrip()+("…" if len(txt)>max_chars else "")
    except Exception:return ""
def get_excerpt_from_feed_entry(entry,max_chars=900):
    try:
        txt=""
        if hasattr(entry,"content") and entry.content:txt=entry.content[0].value
        if not txt and hasattr(entry,"summary"):txt=entry.summary
        txt=clean_text(txt)
        return txt[:max_chars].rstrip()+("…" if len(txt)>max_chars else "") if txt else ""
    except Exception:return ""
def download_image(url,out_path):
    try:
        r=requests.get(url,timeout=30,headers={"User-Agent":"Mozilla/5.0"});r.raise_for_status();_ensure_parent(out_path)
        with open(out_path,"wb") as f:f.write(r.content)
        return True
    except Exception:return False

def is_csrf_error(e):return "CSRF token missing or incorrect" in str(e)

class InstagramPoster:
    def __init__(self):
        self.cl=Client();self.logged_in=False
        # v1.1.6: do NOT force the obsolete Instagram 300.x app/user-agent.
        # Let the installed instagrapi version provide a coherent current app profile.
        # Preserve only stable hardware/device identity when available.
        seed=load_device_seed();dev=seed.get("device_settings")
        if isinstance(dev,dict) and dev:
            hardware={k:v for k,v in dev.items() if k not in ("app_version","version_code")}
            try:
                current=self.cl.get_settings() or {}
                current_dev=current.get("device_settings",{}) or {}
                current_dev.update(hardware)
                self.cl.set_settings({"device_settings":current_dev})
            except Exception:pass
    def try_restore_settings_session(self):
        s=load_ig_settings()
        if not s:return False
        try:
            # Remove stale app version metadata saved by the old 300.x client while
            # retaining identifiers/session data. instagrapi supplies current defaults.
            if isinstance(s,dict) and isinstance(s.get("device_settings"),dict):
                s=dict(s);ds=dict(s["device_settings"]);ds.pop("app_version",None);ds.pop("version_code",None);s["device_settings"]=ds
            self.cl.set_settings(s);self.cl.get_timeline_feed();self.logged_in=True
            log("♻️ Sessione Instagram ripristinata con profilo app corrente.");return True
        except Exception as e:
            log(f"⚠️ Sessione salvata non valida, la resetto: {e}");delete_ig_settings();self.logged_in=False;return False
    def login_with_userpass(self,username,password):
        if not username or not password:raise LoginRequired("Username/password mancanti.")
        self.cl.login(username,password,relogin=False);self.logged_in=True
        try:save_ig_settings(self.cl.get_settings())
        except Exception:pass
        try:self.cl.get_timeline_feed()
        except Exception:pass
        log("✅ Login con username/password completato e sessione salvata (ig_settings).")
    def login_for_posting(self,username,password):
        log("🔐 Login Instagram (posting) in corso...")
        if self.try_restore_settings_session():return
        self.login_with_userpass(username,password)
    def ensure_login(self):
        if not self.logged_in:raise LoginRequired("Non loggato.")
    def post_photo(self,image_path,caption):
        self.ensure_login();self.cl.photo_upload(image_path,caption)
        try:save_ig_settings(self.cl.get_settings())
        except Exception:pass
    @staticmethod
    def test_sessionid(sessionid):
        sid=(sessionid or "").strip()
        if not sid:return {"ok":False,"error":"sessionid vuoto"}
        cl=Client()
        try:
            cl.login_by_sessionid(sid);me=cl.account_info();return {"ok":True,"username":getattr(me,"username","") or "","note":"sessionid valido per web-flow (non garantisce upload)"}
        except Exception as e:return {"ok":False,"error":str(e)}

def bot_loop(username,password,rss_url):
    poster=InstagramPoster();set_metric("running",True);set_metric("last_error","")
    try:poster.login_for_posting(username,password)
    except Exception as e:log(f"❌ Errore login (posting): {e}");set_metric("last_error",str(e));set_metric("running",False);return
    os.makedirs(IMAGES_DIR,exist_ok=True)
    while not stop_event.is_set():
        try:
            entry=get_latest_entry(rss_url)
            if not entry:log("⚠️ Nessun articolo nel feed.");time.sleep(CHECK_INTERVAL);continue
            link=getattr(entry,"link","").strip();title=clean_text(getattr(entry,"title","").strip())
            if not link or not title:log("⚠️ Entry RSS incompleta (manca titolo/link).");time.sleep(CHECK_INTERVAL);continue
            if link==get_last_posted_url():log("ℹ️ Nessun nuovo articolo.");time.sleep(CHECK_INTERVAL);continue
            img_url=get_featured_image_url(link)
            if not img_url:log("❌ Immagine in evidenza non trovata (og:image).");time.sleep(CHECK_INTERVAL);continue
            excerpt=get_article_excerpt(link,900) or get_excerpt_from_feed_entry(entry,900);log(f"📝 Testo estratto: {len(excerpt)} caratteri")
            caption=clamp_caption(f"{title}\n\n{excerpt}\n\n{hashtags_from_title(title)}\n\n👉 {HUB_LINK}",2200)
            if not download_image(img_url,LATEST_IMG_PATH):log("❌ Download immagine fallito.");time.sleep(CHECK_INTERVAL);continue
            log(f"📸 Pubblico: {title}");log("📤 Carico il post su Instagram...");poster.post_photo(LATEST_IMG_PATH,caption)
            log("✅ Pubblicato su Instagram.");save_last_posted_url(link);set_metric("last_title",title);set_metric("last_link",link);set_metric("last_published_at",datetime.now().isoformat());inc_posts_count();set_metric("last_error","")
        except Exception as e:log(f"❌ Errore ciclo: {e}");set_metric("last_error",str(e))
        time.sleep(CHECK_INTERVAL)
    log("⏹️ Bot fermato.");set_metric("running",False)

app=Flask(__name__)
PAGE="""<!doctype html><html lang="it"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/><title>Montagne&Paesi IG Bot (HAOS)</title><style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0;background:#f6f6f6}.wrap{max-width:900px;margin:0 auto;padding:16px}.card{background:#fff;border:1px solid #e9e9e9;border-radius:16px;padding:14px;box-shadow:0 6px 18px rgba(0,0,0,.05);margin-bottom:14px}label{display:block;font-weight:800;margin:10px 0 6px}input{width:100%;padding:12px;border:1px solid #ddd;border-radius:12px;font-size:16px}.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}.btn{padding:12px 14px;border-radius:12px;border:0;cursor:pointer;font-weight:900}.start{background:#111;color:#fff}.stop{background:#fff;border:1px solid #111;color:#111}.muted{color:#666;font-size:14px}pre{background:#0b0b0b;color:#d6d6d6;padding:12px;border-radius:14px;overflow:auto;max-height:520px}a{color:#111}.pill{display:inline-block;padding:6px 10px;border-radius:999px;border:1px solid #ddd;font-weight:800;font-size:13px}.hint{color:#666;font-size:13px;margin-top:6px}.warn{color:#a30000;font-weight:800}.small{font-size:12px;color:#777;margin-top:6px}.chk{display:flex;align-items:center;gap:10px;margin-top:10px}.chk input{width:auto}</style></head><body><div class="wrap"><div class="card"><h2 style="margin:0 0 6px;">Montagne & Paesi → Instagram Bot (HAOS)</h2><div class="muted">Controllo automatico ogni <b>{{interval}}</b> secondi. <span class="pill">/metrics attivo</span></div><div class="hint"><span class="warn">Nota importante:</span> il <b>sessionid</b> di Firefox è un cookie <b>web</b>. Può risultare “valido” ma <b>non</b> garantisce l'upload. Questo bot pubblica usando <b>solo</b> username/password + sessione instagrapi persistente. Il sessionid resta qui solo per test.</div><form method="post" action="/save"><label>Instagram sessionid (solo test)</label><input name="sessionid" value="{{sessionid}}"/><div class="chk"><input id="sessionid_only_test" type="checkbox" name="sessionid_only_test" value="1" {{ 'checked' if sessionid_only_test else '' }}/><label for="sessionid_only_test" style="margin:0;font-weight:700;">Usa sessionid solo per test (consigliato)</label></div><div class="row"><button class="btn stop" formaction="/test_sessionid" formmethod="post">Test sessionid</button></div><label>Instagram username (necessario per pubblicare)</label><input name="username" value="{{username}}"/><label>Instagram password (necessaria per pubblicare)</label><input name="password" value="{{password}}" type="password"/><label>RSS Feed URL</label><input name="rss_url" value="{{rss_url}}"/><div class="row"><button class="btn start" formaction="/start" formmethod="post">Avvia bot</button><button class="btn stop" formaction="/stop" formmethod="post">Stop</button><button class="btn stop" type="submit">Salva</button></div><div class="muted" style="margin-top:10px;">Hub link: <b>{{hub}}</b> • Canale WhatsApp: <a href="{{wa}}" target="_blank">apri</a></div><div class="small">File persistenti: config.json, ig_settings.json, device_seed.json, last_post.txt in /data.</div></form></div><div class="card"><div class="row" style="align-items:center;justify-content:space-between;"><div><b>Stato:</b> <span id="status">...</span></div><button class="btn stop" onclick="refresh()">Aggiorna</button></div><pre id="log">Caricamento...</pre></div></div><script>async function refresh(){const st=await fetch('/status').then(r=>r.json());document.getElementById('status').textContent=st.running?'RUNNING':'STOPPED';const lg=await fetch('/logs').then(r=>r.json());document.getElementById('log').textContent=lg.lines.join("\\n");}setInterval(refresh,2500);refresh();</script></body></html>"""
@app.get("/")
def index():
    cfg=load_config();return render_template_string(PAGE,sessionid=cfg.get("sessionid",""),sessionid_only_test=bool(cfg.get("sessionid_only_test",True)),username=cfg.get("username",""),password=cfg.get("password",""),rss_url=cfg.get("rss_url",DEFAULT_RSS),interval=CHECK_INTERVAL,hub=HUB_LINK,wa=WA_CHANNEL_URL)
@app.post("/save")
def save():
    cfg=load_config();cfg["sessionid"]=request.form.get("sessionid","").strip();cfg["sessionid_only_test"]=bool(request.form.get("sessionid_only_test"));cfg["username"]=request.form.get("username","").strip();cfg["password"]=request.form.get("password","").strip();cfg["rss_url"]=request.form.get("rss_url","").strip() or DEFAULT_RSS;save_config(cfg);log("💾 Config salvata.");return redirect("/")
@app.post("/test_sessionid")
def test_sessionid():
    cfg=load_config();sid=request.form.get("sessionid","").strip() or cfg.get("sessionid","").strip();res=InstagramPoster.test_sessionid(sid);log(("✅ Test sessionid OK. Username: "+res.get("username","") if res.get("ok") else "❌ Test sessionid fallito: "+res.get("error","")));return redirect("/")
@app.post("/start")
def start():
    global bot_thread
    cfg=load_config();username=request.form.get("username","").strip() or cfg.get("username","").strip();password=request.form.get("password","").strip() or cfg.get("password","").strip();rss_url=request.form.get("rss_url","").strip() or cfg.get("rss_url",DEFAULT_RSS)
    if not username or not password:log("❌ Inserisci username e password (servono per pubblicare).");return redirect("/")
    cfg.update({"username":username,"password":password,"rss_url":rss_url});save_config(cfg)
    with bot_lock:
        if bot_thread is not None and bot_thread.is_alive():log("ℹ️ Bot già in esecuzione.");return redirect("/")
        stop_event.clear();bot_thread=threading.Thread(target=bot_loop,args=(username,password,rss_url),daemon=True);bot_thread.start();log("▶️ Bot avviato.")
    return redirect("/")
@app.post("/stop")
def stop():stop_event.set();log("⏹️ Stop richiesto.");return redirect("/")
@app.get("/status")
def status():
    with bot_lock:running=bot_thread is not None and bot_thread.is_alive()
    return jsonify({"running":running})
@app.get("/logs")
def get_logs():
    with logs_lock:return jsonify({"lines":logs[-600:]})
@app.get("/metrics")
def metrics_endpoint():
    with metrics_lock:return jsonify(metrics)
if __name__=="__main__":log("🟢 Web UI pronta.");app.run(host="0.0.0.0",port=8080)
