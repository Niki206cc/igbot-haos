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


# ---------------- Config / Paths (persistenti su volume /data) ----------------
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/data/config.json")
LAST_POST_PATH = os.environ.get("LAST_POST_PATH", "/data/last_post.txt")
IG_SETTINGS_PATH = os.environ.get("IG_SETTINGS_PATH", "/data/ig_settings.json")

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))
DEFAULT_RSS = os.environ.get("DEFAULT_RSS", "https://www.montagneepaesi.com/feed/")
HUB_LINK = os.environ.get("HUB_LINK", "www.montagneepaesi.com/instagram")

WA_CHANNEL_URL = os.environ.get(
    "WA_CHANNEL_URL",
    "https://whatsapp.com/channel/0029Vb7fcHT8aKvFAuCIfm0c"
)

# ---------------- Log in memoria ----------------
logs = []
logs_lock = threading.Lock()

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with logs_lock:
        logs.append(line)
        if len(logs) > 400:
            logs[:] = logs[-400:]


# ---------------- Metriche (per dashboard HA) ----------------
metrics = {
    "running": False,
    "last_title": "",
    "last_link": "",
    "last_published_at": "",
    "last_error": "",
    "posts_count": 0
}
metrics_lock = threading.Lock()

def set_metric(key, value):
    with metrics_lock:
        metrics[key] = value

def inc_posts_count():
    with metrics_lock:
        metrics["posts_count"] = int(metrics.get("posts_count", 0)) + 1


# ---------------- Thread state ----------------
bot_thread = None
bot_lock = threading.Lock()
stop_event = threading.Event()


# ---------------- Persistenza config / last post ----------------
def _ensure_parent(path: str):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

def load_config():
    """
    Config salvata su /data/config.json
    """
    base = {"rss_url": DEFAULT_RSS, "username": "", "password": "", "sessionid": ""}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
            base.update(cfg)
            return base
        except Exception:
            return base
    return base

def save_config(cfg):
    _ensure_parent(CONFIG_PATH)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def get_last_posted_url():
    if os.path.exists(LAST_POST_PATH):
        with open(LAST_POST_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def save_last_posted_url(url: str):
    _ensure_parent(LAST_POST_PATH)
    with open(LAST_POST_PATH, "w", encoding="utf-8") as f:
        f.write(url.strip())


# ---------------- Persistenza sessione Instagram (instagrapi settings) ----------------
def load_ig_settings():
    if os.path.exists(IG_SETTINGS_PATH):
        try:
            with open(IG_SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_ig_settings(settings: dict):
    try:
        _ensure_parent(IG_SETTINGS_PATH)
        with open(IG_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False)
    except Exception:
        pass

def delete_ig_settings():
    try:
        if os.path.exists(IG_SETTINGS_PATH):
            os.remove(IG_SETTINGS_PATH)
    except Exception:
        pass


# ---------------- Utility testo ----------------
def clean_text(s: str) -> str:
    if not s:
        return ""
    s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def clamp_caption(caption: str, max_len: int = 2200) -> str:
    caption = caption.strip()
    if len(caption) <= max_len:
        return caption
    return caption[: max_len - 1].rstrip() + "…"

def hashtags_from_title(title: str, max_tags=8) -> str:
    stopwords = {
        "di","a","da","in","con","su","per","tra","fra","il","lo","la","i","gli","le",
        "un","una","uno","e","è","del","della","dei","delle","al","allo","alla","agli",
        "alle","ai","dal","dallo","dalla","dai","dalle","nel","nello","nella","nei","nelle"
    }
    words = re.findall(r"[a-zA-ZàèéìòùÀÈÉÌÒÙ]+", title.lower())
    tags = []
    for w in words:
        if len(w) >= 4 and w not in stopwords:
            tags.append(f"#{w}")

    fixed = ["#montagneepaesi", "#news", "#notizie", "#ultimora", "#flashnews"]
    return " ".join(fixed + tags[:max_tags])


# ---------------- RSS + estrazione contenuti ----------------
def get_latest_entry(rss_url: str):
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        return None
    return feed.entries[0]

def get_featured_image_url(article_url: str) -> str:
    try:
        r = requests.get(article_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"].strip()
    except Exception:
        pass
    return ""

def get_article_excerpt(article_url: str, max_chars: int = 900) -> str:
    """
    Estrae testo reale dall'articolo (WP), rimuovendo SOLO il box promo WhatsApp/Telegram.
    """
    try:
        r = requests.get(article_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        content = (
            soup.find("div", class_="entry-content")
            or soup.find("div", class_="td-post-content")
            or soup.find("article")
            or soup.find("main")
            or soup.body
        )
        if not content:
            return ""

        promo_phrase = "vuoi ricevere le notizie di montagne"
        wa_marker = "whatsapp.com/channel/0029vb7fcht8akvfaucifm0c"

        for node in content.find_all(["div", "section", "aside"]):
            txt = node.get_text(" ", strip=True).lower()
            html = str(node).lower().replace(" ", "")
            if promo_phrase in txt or wa_marker in html:
                node.decompose()

        parts = []
        for el in content.find_all(["p", "li"]):
            t = el.get_text(" ", strip=True)
            if not t:
                continue
            tl = t.lower()
            if promo_phrase in tl:
                continue
            if "whatsapp.com/channel/0029vb7fc" in tl.replace(" ", ""):
                continue
            if len(t) < 25:
                continue
            parts.append(t)

        text = clean_text(" ".join(parts))
        if not text:
            return ""

        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        return text
    except Exception:
        return ""

def get_excerpt_from_feed_entry(entry, max_chars: int = 900) -> str:
    """
    Fallback: prende testo dal feed (content/summary).
    """
    try:
        txt = ""
        if hasattr(entry, "content") and entry.content:
            txt = entry.content[0].value
        if not txt and hasattr(entry, "summary"):
            txt = entry.summary

        txt = clean_text(txt)
        if not txt:
            return ""

        if len(txt) > max_chars:
            txt = txt[:max_chars].rstrip() + "…"
        return txt
    except Exception:
        return ""

def download_image(url: str, out_path: str) -> bool:
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        _ensure_parent(out_path)
        with open(out_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception:
        return False


# ---------------- Instagram (instagrapi) ----------------
class InstagramPoster:
    def __init__(self):
        self.cl = Client()
        self.logged_in = False
        try:
            self.cl.set_user_agent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            )
        except Exception:
            pass

        # migliora consistenza in container (non obbligatorio, ma aiuta)
        try:
            self.cl.set_locale("it_IT")
            self.cl.set_country_code(39)
            # offset Italia in inverno = 3600; in estate = 7200
            self.cl.set_timezone_offset(3600)
        except Exception:
            pass

    def try_restore_session(self) -> bool:
        s = load_ig_settings()
        if not s:
            return False
        try:
            self.cl.set_settings(s)
            self.cl.get_timeline_feed()  # valida sessione
            self.logged_in = True
            log("♻️ Sessione Instagram ripristinata (ig_settings.json).")
            return True
        except Exception as e:
            log(f"⚠️ Sessione salvata non valida, la resetto: {e}")
            delete_ig_settings()
            self.logged_in = False
            return False

    def login(self, username: str, password: str, sessionid: str = ""):
        log("🔐 Login Instagram in corso...")

        # 1) priorità: sessionid inserito in GUI o in ENV
        sid = (sessionid or "").strip() or os.environ.get("IG_SESSIONID", "").strip()
        if sid:
            try:
                self.cl.login_by_sessionid(sid)
                self.logged_in = True
                try:
                    save_ig_settings(self.cl.get_settings())
                except Exception:
                    pass
                log("✅ Login via sessionid completato e sessione salvata.")
                return
            except Exception as e:
                # se fallisce, lo segnalo e ripiego
                log(f"⚠️ Login via sessionid fallito: {e} (ripiego su sessione salvata / user+pass)")

        # 2) prova sessione salvata (ig_settings.json)
        if self.try_restore_session():
            return

        # 3) login classico (senza relogin forzato)
        if not username or not password:
            raise LoginRequired("Username/password mancanti e sessionid non valido.")

        self.cl.login(username, password, relogin=False)
        self.logged_in = True

        try:
            save_ig_settings(self.cl.get_settings())
        except Exception:
            pass

        log("✅ Login con username/password completato e sessione salvata.")

    def ensure_login(self):
        if not self.logged_in:
            raise LoginRequired("Non loggato.")

    def post_photo(self, image_path: str, caption: str):
        self.ensure_login()
        log("📤 Carico il post su Instagram...")
        self.cl.photo_upload(image_path, caption)
        log("✅ Pubblicato su Instagram.")
        try:
            save_ig_settings(self.cl.get_settings())
        except Exception:
            pass


# ---------------- Bot loop ----------------
def bot_loop(username: str, password: str, rss_url: str, sessionid: str):
    poster = InstagramPoster()
    set_metric("running", True)
    set_metric("last_error", "")

    try:
        poster.login(username, password, sessionid=sessionid)
    except TwoFactorRequired:
        log("❌ Instagram richiede 2FA.")
        set_metric("last_error", "2FA required")
        set_metric("running", False)
        return
    except ChallengeRequired:
        log("❌ Instagram ha richiesto una Challenge (verifica).")
        set_metric("last_error", "Challenge required")
        set_metric("running", False)
        return
    except Exception as e:
        log(f"❌ Errore login: {e}")
        set_metric("last_error", str(e))
        set_metric("running", False)
        return

    os.makedirs("/data/images", exist_ok=True)

    while not stop_event.is_set():
        try:
            entry = get_latest_entry(rss_url)
            if not entry:
                log("⚠️ Nessun articolo nel feed.")
                time.sleep(CHECK_INTERVAL)
                continue

            link = getattr(entry, "link", "").strip()
            title = clean_text(getattr(entry, "title", "").strip())

            if not link or not title:
                log("⚠️ Entry RSS incompleta (manca titolo/link).")
                time.sleep(CHECK_INTERVAL)
                continue

            last = get_last_posted_url()
            if link == last:
                log("ℹ️ Nessun nuovo articolo.")
                time.sleep(CHECK_INTERVAL)
                continue

            img_url = get_featured_image_url(link)
            if not img_url:
                log("❌ Immagine in evidenza non trovata (og:image).")
                time.sleep(CHECK_INTERVAL)
                continue

            excerpt = get_article_excerpt(link, max_chars=900)
            if not excerpt:
                excerpt = get_excerpt_from_feed_entry(entry, max_chars=900)

            log(f"📝 Testo estratto: {len(excerpt)} caratteri")

            tags = hashtags_from_title(title)
            caption = f"""{title}

{excerpt}

{tags}

👉 {HUB_LINK}"""
            caption = clamp_caption(caption, 2200)

            img_path = "/data/images/latest.jpg"
            if not download_image(img_url, img_path):
                log("❌ Download immagine fallito.")
                time.sleep(CHECK_INTERVAL)
                continue

            log(f"📸 Pubblico: {title}")
            poster.post_photo(img_path, caption)
            save_last_posted_url(link)

            # metriche ok
            set_metric("last_title", title)
            set_metric("last_link", link)
            set_metric("last_published_at", datetime.now().isoformat())
            inc_posts_count()
            set_metric("last_error", "")

        except Exception as e:
            log(f"❌ Errore ciclo: {e}")
            set_metric("last_error", str(e))

        time.sleep(CHECK_INTERVAL)

    log("⏹️ Bot fermato.")
    set_metric("running", False)


# ---------------- Web UI (Flask) ----------------
app = Flask(__name__)

PAGE = """
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Montagne&Paesi IG Bot (HAOS)</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0;background:#f6f6f6}
    .wrap{max-width:900px;margin:0 auto;padding:16px}
    .card{background:#fff;border:1px solid #e9e9e9;border-radius:16px;padding:14px;box-shadow:0 6px 18px rgba(0,0,0,.05);margin-bottom:14px}
    label{display:block;font-weight:800;margin:10px 0 6px}
    input{width:100%;padding:12px;border:1px solid #ddd;border-radius:12px;font-size:16px}
    .row{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
    .btn{padding:12px 14px;border-radius:12px;border:0;cursor:pointer;font-weight:900}
    .start{background:#111;color:#fff}
    .stop{background:#fff;border:1px solid #111;color:#111}
    .muted{color:#666;font-size:14px}
    pre{background:#0b0b0b;color:#d6d6d6;padding:12px;border-radius:14px;overflow:auto;max-height:480px}
    a{color:#111}
    .pill{display:inline-block;padding:6px 10px;border-radius:999px;border:1px solid #ddd;font-weight:800;font-size:13px}
    .hint{color:#666;font-size:13px;margin-top:6px}
    .warn{color:#a30000;font-weight:800}
  </style>
</head>
<body>
<div class="wrap">

  <div class="card">
    <h2 style="margin:0 0 6px;">Montagne & Paesi → Instagram Bot (HAOS)</h2>
    <div class="muted">
      Controllo automatico ogni <b>{{interval}}</b> secondi.
      <span class="pill">/metrics attivo</span>
    </div>

    <div class="hint">
      <span class="warn">Nota:</span> il <b>sessionid</b> è sensibile (come una password). Se lo cambi su Instagram, aggiornalo qui.
      Se presente, il bot prova prima con sessionid, poi con sessione salvata, poi con username/password.
    </div>

    <form method="post" action="/save">
      <label>Instagram sessionid (opzionale ma consigliato)</label>
      <input name="sessionid" value="{{sessionid}}" placeholder="es. 123%3AABC..."/>

      <label>Instagram username (fallback)</label>
      <input name="username" value="{{username}}" placeholder="username"/>

      <label>Instagram password (fallback)</label>
      <input name="password" value="{{password}}" type="password" placeholder="password"/>

      <label>RSS Feed URL</label>
      <input name="rss_url" value="{{rss_url}}" placeholder="https://www.montagneepaesi.com/feed/"/>

      <div class="row">
        <button class="btn start" formaction="/start" formmethod="post">Avvia bot</button>
        <button class="btn stop" formaction="/stop" formmethod="post">Stop</button>
        <button class="btn stop" type="submit">Salva</button>
      </div>

      <div class="muted" style="margin-top:10px;">
        Hub link: <b>{{hub}}</b> • Canale WhatsApp: <a href="{{wa}}" target="_blank" rel="noopener">apri</a>
      </div>
    </form>
  </div>

  <div class="card">
    <div class="row" style="align-items:center;justify-content:space-between;">
      <div><b>Stato:</b> <span id="status">...</span></div>
      <button class="btn stop" onclick="refresh()">Aggiorna</button>
    </div>
    <pre id="log">Caricamento...</pre>
  </div>

</div>

<script>
async function refresh(){
  const st = await fetch('/status').then(r=>r.json());
  document.getElementById('status').textContent = st.running ? 'RUNNING' : 'STOPPED';

  const lg = await fetch('/logs').then(r=>r.json());
  document.getElementById('log').textContent = lg.lines.join("\\n");
}
setInterval(refresh, 2500);
refresh();
</script>
</body>
</html>
"""

@app.get("/")
def index():
    cfg = load_config()
    return render_template_string(
        PAGE,
        sessionid=cfg.get("sessionid",""),
        username=cfg.get("username",""),
        password=cfg.get("password",""),
        rss_url=cfg.get("rss_url", DEFAULT_RSS),
        interval=CHECK_INTERVAL,
        hub=HUB_LINK,
        wa=WA_CHANNEL_URL
    )

@app.post("/save")
def save():
    cfg = load_config()
    cfg["sessionid"] = request.form.get("sessionid","").strip()
    cfg["username"] = request.form.get("username","").strip()
    cfg["password"] = request.form.get("password","").strip()
    cfg["rss_url"] = request.form.get("rss_url","").strip() or DEFAULT_RSS
    save_config(cfg)
    log("💾 Config salvata.")
    return redirect("/")

@app.post("/start")
def start():
    global bot_thread
    cfg = load_config()

    sessionid = (request.form.get("sessionid","").strip() or cfg.get("sessionid","").strip())
    username  = (request.form.get("username","").strip()  or cfg.get("username","").strip())
    password  = (request.form.get("password","").strip()  or cfg.get("password","").strip())
    rss_url   = (request.form.get("rss_url","").strip()   or cfg.get("rss_url", DEFAULT_RSS))

    # Regola: se non hai sessionid, devi avere user+pass
    if not sessionid and (not username or not password):
        log("❌ Inserisci sessionid oppure username+password.")
        return redirect("/")

    cfg["sessionid"] = sessionid
    cfg["username"] = username
    cfg["password"] = password
    cfg["rss_url"] = rss_url
    save_config(cfg)

    with bot_lock:
        running = bot_thread is not None and bot_thread.is_alive()
        if running:
            log("ℹ️ Bot già in esecuzione.")
            return redirect("/")

        stop_event.clear()
        bot_thread = threading.Thread(
            target=bot_loop,
            args=(username, password, rss_url, sessionid),
            daemon=True
        )
        bot_thread.start()
        log("▶️ Bot avviato.")
        return redirect("/")

@app.post("/stop")
def stop():
    stop_event.set()
    log("⏹️ Stop richiesto.")
    return redirect("/")

@app.get("/status")
def status():
    with bot_lock:
        running = bot_thread is not None and bot_thread.is_alive()
    return jsonify({"running": running})

@app.get("/logs")
def get_logs():
    with logs_lock:
        return jsonify({"lines": logs[-400:]})

@app.get("/metrics")
def metrics_endpoint():
    with metrics_lock:
        return jsonify(metrics)

if __name__ == "__main__":
    log("🟢 Web UI pronta.")
    app.run(host="0.0.0.0", port=8080)
