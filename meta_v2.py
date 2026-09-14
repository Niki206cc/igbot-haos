"""Montagne & Paesi Instagram Bot v2.0.1 - official Meta API publishing test."""
import html, json, os, re
from datetime import datetime
import requests, feedparser
from bs4 import BeautifulSoup
from flask import Flask, request, redirect, jsonify, render_template_string

APP_VERSION="2.0.1-test"
CONFIG_PATH=os.environ.get("CONFIG_PATH","/data/config.json")
LAST_POST_PATH=os.environ.get("LAST_POST_PATH","/data/last_post_meta.txt")
GRAPH_BASE=os.environ.get("META_GRAPH_BASE","https://graph.instagram.com")
DEFAULT_IG_USER_ID="17841409303885274"
DEFAULT_RSS="https://www.montagneepaesi.com/feed/"
HUB_LINK="www.montagneepaesi.com/instagram"
app=Flask(__name__); logs=[]
state={"meta_connected":False,"username":"","last_error":"","preview":{}}

def log(m):
 line=f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {m}"; logs.append(line); del logs[:-600]; print(line,flush=True)
def load_config():
 try:
  with open(CONFIG_PATH,"r",encoding="utf-8") as f: x=json.load(f); return x if isinstance(x,dict) else {}
 except Exception:return {}
def save_config(c):
 os.makedirs(os.path.dirname(CONFIG_PATH),exist_ok=True); t=CONFIG_PATH+".tmp"
 with open(t,"w",encoding="utf-8") as f: json.dump(c,f,ensure_ascii=False,indent=2)
 os.replace(t,CONFIG_PATH)
def api_error(r):
 try:d=r.json()
 except Exception:d={}
 if not r.ok: raise RuntimeError((d.get("error") or {}).get("message") or f"Meta API HTTP {r.status_code}")
 return d
def meta_test(token,uid):
 return api_error(requests.get(f"{GRAPH_BASE}/v24.0/{uid}",params={"fields":"id,username","access_token":token},timeout=20))
def clean(s): return re.sub(r"\s+"," ",BeautifulSoup(s or "","html.parser").get_text(" ",strip=True)).strip()
def latest_article(rss):
 f=feedparser.parse(rss)
 if not f.entries: raise RuntimeError("Nessun articolo disponibile nel feed RSS.")
 e=f.entries[0]; link=str(getattr(e,"link","")).strip(); title=clean(str(getattr(e,"title","")))
 if not link or not title: raise RuntimeError("Ultimo articolo RSS incompleto.")
 r=requests.get(link,timeout=20,headers={"User-Agent":"Mozilla/5.0"}); r.raise_for_status(); s=BeautifulSoup(r.text,"html.parser")
 og=s.find("meta",property="og:image"); image=(og.get("content") or "").strip() if og else ""
 if not image: raise RuntimeError("Immagine in evidenza pubblica (og:image) non trovata.")
 desc=s.find("meta",property="og:description") or s.find("meta",attrs={"name":"description"})
 excerpt=clean(desc.get("content","") if desc else str(getattr(e,"summary","")))[:900]
 words=[w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ0-9]+",title) if len(w)>3][:6]
 tags=" ".join("#"+re.sub(r"[^a-z0-9à-ÿ]","",w) for w in words)
 caption=f"{title}\n\n{excerpt}\n\n{tags}\n\n👉 {HUB_LINK}"[:2200]
 return {"title":title,"link":link,"image_url":image,"caption":caption}
def create_and_publish(token,uid,a):
 # Official two-step Instagram publishing flow: create media container, then publish it.
 d=api_error(requests.post(f"{GRAPH_BASE}/v24.0/{uid}/media",data={"image_url":a["image_url"],"caption":a["caption"],"access_token":token},timeout=30))
 cid=str(d.get("id") or "")
 if not cid: raise RuntimeError("Meta non ha restituito il creation_id.")
 log(f"📦 Container Meta creato: {cid}")
 d=api_error(requests.post(f"{GRAPH_BASE}/v24.0/{uid}/media_publish",data={"creation_id":cid,"access_token":token},timeout=30))
 mid=str(d.get("id") or "")
 if not mid: raise RuntimeError("Meta non ha restituito l'ID del post pubblicato.")
 return mid

def cfg_from_form():
 c=load_config(); c["meta_ig_user_id"]=request.form.get("ig_user_id","").strip() or str(c.get("meta_ig_user_id") or DEFAULT_IG_USER_ID); c["rss_url"]=request.form.get("rss_url","").strip() or str(c.get("rss_url") or DEFAULT_RSS)
 t=request.form.get("meta_access_token","").strip()
 if t:c["meta_access_token"]=t
 return c

PAGE='''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>M&P Instagram Meta</title><style>body{font-family:Arial;background:#f5f6f8;padding:20px;color:#222}.box{max-width:950px;margin:auto;background:white;padding:22px;border-radius:12px}input,textarea{width:100%;box-sizing:border-box;padding:10px;margin:5px 0 12px}.btn{padding:10px 14px;margin:4px;border:0;border-radius:7px;cursor:pointer}.p{background:#1769e0;color:white}.test{background:#b45309;color:white}pre{background:#111;color:#eee;padding:12px;border-radius:8px;min-height:150px;white-space:pre-wrap}.small{font-size:13px;color:#666}.warn{color:#a45b00}.preview{background:#f7f7f7;padding:12px;border-radius:8px;margin-top:12px}</style></head><body><div class="box"><h2>Montagne & Paesi → Instagram API ufficiale Meta</h2><div class="small">Versione <b>{{version}}</b></div><p class="warn"><b>Modalità test:</b> nessuna pubblicazione automatica. Il pulsante Test pubblicazione Meta pubblica davvero UNA sola volta l'ultimo articolo.</p><form method="post"><label>Instagram User ID</label><input name="ig_user_id" value="{{uid}}"><label>Instagram Access Token</label><input type="password" name="meta_access_token" placeholder="{% if saved %}Token salvato - lascia vuoto per mantenerlo{% else %}Incolla token Meta{% endif %}"><label>Feed RSS</label><input name="rss_url" value="{{rss}}"><button class="btn p" formaction="/save" formmethod="post">Salva configurazione</button><button class="btn p" formaction="/test_meta" formmethod="post">Test API Meta</button><button class="btn p" formaction="/preview" formmethod="post">Aggiorna anteprima</button><button class="btn test" formaction="/publish_test" formmethod="post" onclick="return confirm('ATTENZIONE: verrà pubblicato DAVVERO l’ultimo articolo su Instagram, una sola volta. Continuare?')">Test pubblicazione Meta</button></form>{% if preview %}<div class="preview"><b>Anteprima ultimo articolo</b><p><b>{{preview.title}}</b></p><p>{{preview.caption}}</p><div class="small">Immagine: {{preview.image_url}}</div></div>{% endif %}<h3>Stato</h3><div id="state">API Meta: non verificata</div><h3>Log</h3><button class="btn" onclick="copyLog()">Copia log</button><button class="btn" onclick="clearLog()">Azzera log</button><pre id="log"></pre><script>async function refresh(){let s=await(await fetch('/status')).json();document.getElementById('state').textContent=s.meta_connected?'API Meta collegata: @'+s.username:'API Meta: non verificata';document.getElementById('log').textContent=await(await fetch('/logs')).text()}async function copyLog(){try{await navigator.clipboard.writeText(document.getElementById('log').textContent);alert('Log copiato.')}catch(e){}}async function clearLog(){await fetch('/clear_logs',{method:'POST'});refresh()}setInterval(refresh,2500);refresh()</script></div></body></html>'''

@app.get("/")
def home():
 c=load_config(); return render_template_string(PAGE,version=APP_VERSION,uid=html.escape(str(c.get("meta_ig_user_id") or DEFAULT_IG_USER_ID)),rss=html.escape(str(c.get("rss_url") or DEFAULT_RSS)),saved=bool(c.get("meta_access_token")),preview=state.get("preview"))
@app.post("/save")
def save(): save_config(cfg_from_form()); log("💾 Configurazione salvata localmente. Token non visualizzato."); return redirect("/")
@app.post("/test_meta")
def test_meta_route():
 c=cfg_from_form(); save_config(c); t=str(c.get("meta_access_token") or "")
 try:
  if not t: raise RuntimeError("Token Meta mancante.")
  d=meta_test(t,c["meta_ig_user_id"]); state.update(meta_connected=True,username=str(d.get("username") or ""),last_error=""); log(f"✅ API Meta collegata: @{state['username']} • ID {d.get('id')}")
 except Exception as e: state.update(meta_connected=False,last_error=str(e)); log(f"❌ Test API Meta fallito: {e}")
 return redirect("/")
@app.post("/preview")
def preview():
 c=cfg_from_form(); save_config(c)
 try: state["preview"]=latest_article(c["rss_url"]); log(f"👁️ Anteprima pronta: {state['preview']['title']}")
 except Exception as e: state["last_error"]=str(e); log(f"❌ Anteprima fallita: {e}")
 return redirect("/")
@app.post("/publish_test")
def publish_test():
 c=cfg_from_form(); save_config(c); t=str(c.get("meta_access_token") or "")
 try:
  if not t: raise RuntimeError("Token Meta mancante.")
  a=latest_article(c["rss_url"]); state["preview"]=a
  try:
   with open(LAST_POST_PATH,"r",encoding="utf-8") as f:last=f.read().strip()
  except Exception:last=""
  if last==a["link"]: raise RuntimeError("Questo articolo risulta già pubblicato dal bot Meta. Test annullato per evitare duplicati.")
  log(f"🧪 Pubblicazione Meta singola: {a['title']}"); mid=create_and_publish(t,c["meta_ig_user_id"],a)
  os.makedirs(os.path.dirname(LAST_POST_PATH),exist_ok=True)
  with open(LAST_POST_PATH,"w",encoding="utf-8") as f:f.write(a["link"])
  state["last_error"]=""; log(f"✅ Pubblicazione ufficiale Meta riuscita. Media ID: {mid}"); log("⏹️ Fine test: nessun retry e nessuna automazione attiva.")
 except Exception as e: state["last_error"]=str(e); log(f"🛑 Test pubblicazione fermato: {e}")
 return redirect("/")
@app.get("/status")
def status(): return jsonify({"version":APP_VERSION,**{k:v for k,v in state.items() if k!="preview"}})
@app.get("/logs")
def get_logs(): return "\n".join(logs[-600:]),200,{"Content-Type":"text/plain; charset=utf-8"}
@app.post("/clear_logs")
def clear_logs(): logs.clear(); state["last_error"]=""; return jsonify({"ok":True})
if __name__=="__main__":
 log(f"🟢 Web UI pronta. Versione {APP_VERSION}."); log("🌐 Solo API ufficiale Meta. instagrapi non viene caricato."); log("🔒 Pubblicazione automatica disattivata; disponibile solo test manuale singolo."); app.run(host="0.0.0.0",port=8080)
