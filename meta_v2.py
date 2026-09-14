"""Montagne & Paesi Instagram Bot v2.1.1 - official Meta API only."""
import html,json,os,re,threading,time
from datetime import datetime
import requests,feedparser
from bs4 import BeautifulSoup
from flask import Flask,request,redirect,jsonify,render_template_string
APP_VERSION="2.1.1";CONFIG_PATH=os.environ.get("CONFIG_PATH","/data/config.json");LAST_POST_PATH="/data/last_post_meta.txt";GRAPH_BASE="https://graph.instagram.com";DEFAULT_IG_USER_ID="17841409303885274";DEFAULT_RSS="https://www.montagneepaesi.com/feed/";HUB_LINK="www.montagneepaesi.com"
app=Flask(__name__);logs=[];lock=threading.Lock();stop_event=threading.Event();bot_thread=None
state={"running":False,"meta_connected":False,"username":"","last_error":"","last_check":"","last_published":"","preview":{}}
def log(m):
 line=f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {m}"
 with lock:logs.append(line);del logs[:-600]
 print(line,flush=True)
def load_config():
 try:
  with open(CONFIG_PATH,"r",encoding="utf-8") as f:x=json.load(f);return x if isinstance(x,dict) else {}
 except Exception:return {}
def save_config(c):
 os.makedirs(os.path.dirname(CONFIG_PATH),exist_ok=True);p=CONFIG_PATH+".tmp"
 with open(p,"w",encoding="utf-8") as f:json.dump(c,f,ensure_ascii=False,indent=2)
 os.replace(p,CONFIG_PATH)
def api(r):
 try:d=r.json()
 except Exception:d={}
 if not r.ok:raise RuntimeError((d.get("error") or {}).get("message") or f"Meta API HTTP {r.status_code}")
 return d
def meta_test(t,u):return api(requests.get(f"{GRAPH_BASE}/v24.0/{u}",params={"fields":"id,username","access_token":t},timeout=20))
def clean(x):return re.sub(r"\s+"," ",BeautifulSoup(x or "","html.parser").get_text(" ",strip=True)).strip()
def is_promo(x):
 y=x.lower();return any(z in y for z in ["ricevi gratis le notizie di montagne","iscriviti al nostro canale whatsapp","clicca qui per iscriverti al canale","seguici anche su telegram","unisciti al canale telegram","clicca qui per iscriverti su telegram"])
def article_text(s,e):
 for sel in [".elementor-widget-theme-post-content",".entry-content",".post-content","article"]:
  box=s.select_one(sel)
  if not box:continue
  parts=[];skip=False
  for p in box.find_all(["p","h2","h3"]):
   x=clean(p.get_text(" ",strip=True));y=x.lower()
   if "ricevi gratis le notizie di montagne" in y or "iscriviti al nostro canale whatsapp" in y:skip=True
   if not skip and len(x)>=25 and not is_promo(x) and x not in parts:parts.append(x)
   if skip and "telegram" in y and "clicca qui" in y:skip=False
  text="\n\n".join(parts)
  if len(text)>=150:return text
 raw=clean(str(getattr(e,"summary","")));return "" if is_promo(raw) else raw
def latest_article(rss):
 f=feedparser.parse(rss)
 if not f.entries:raise RuntimeError("Nessun articolo disponibile nel feed RSS.")
 e=f.entries[0];link=str(getattr(e,"link","")).strip();title=clean(str(getattr(e,"title","")))
 if not link or not title:raise RuntimeError("Ultimo articolo RSS incompleto.")
 r=requests.get(link,timeout=20,headers={"User-Agent":"Mozilla/5.0"});r.raise_for_status();s=BeautifulSoup(r.text,"html.parser");og=s.find("meta",property="og:image");image=(og.get("content") or "").strip() if og else ""
 if not image:raise RuntimeError("Immagine in evidenza pubblica non trovata.")
 body=article_text(s,e);words=[w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ0-9]+",title) if len(w)>3][:6];tags=" ".join("#"+re.sub(r"[^a-z0-9à-ÿ]","",w) for w in words);suffix=f"\n\n{tags}\n\n👉 {HUB_LINK}";n=max(0,2200-len(title)-4-len(suffix));body=body[:n].rstrip()
 if len(body)>=n and n>4:body=body.rsplit(" ",1)[0].rstrip()+"…"
 return {"title":title,"link":link,"image_url":image,"caption":f"{title}\n\n{body}{suffix}"[:2200]}
def wait_container(t,cid):
 # Meta can accept /media before the image container is actually ready. Poll the
 # SAME container only; never recreate it, so a transient processing delay cannot
 # generate duplicate posts.
 for attempt in range(1,13):
  d=api(requests.get(f"{GRAPH_BASE}/v24.0/{cid}",params={"fields":"status_code,status","access_token":t},timeout=20));status=str(d.get("status_code") or "").upper();detail=str(d.get("status") or "")
  log(f"⏳ Container Meta {cid}: {status or detail or 'stato non disponibile'} ({attempt}/12)")
  if status=="FINISHED":return
  if status in ("ERROR","EXPIRED"):raise RuntimeError(f"Elaborazione media Meta fallita: {detail or status}")
  if stop_event.wait(5):raise RuntimeError("Pubblicazione interrotta: arresto bot richiesto.")
 raise RuntimeError("Il container Meta non è diventato pronto entro 60 secondi.")
def publish(t,u,a):
 d=api(requests.post(f"{GRAPH_BASE}/v24.0/{u}/media",data={"image_url":a["image_url"],"caption":a["caption"],"access_token":t},timeout=30));cid=str(d.get("id") or "")
 if not cid:raise RuntimeError("creation_id Meta mancante.")
 log(f"📦 Container Meta creato: {cid}");wait_container(t,cid)
 d=api(requests.post(f"{GRAPH_BASE}/v24.0/{u}/media_publish",data={"creation_id":cid,"access_token":t},timeout=30));mid=str(d.get("id") or "")
 if not mid:raise RuntimeError("Media ID Meta mancante.")
 return mid
def last_link():
 try:
  with open(LAST_POST_PATH,"r",encoding="utf-8") as f:return f.read().strip()
 except Exception:return ""
def set_last(x):
 os.makedirs("/data",exist_ok=True)
 with open(LAST_POST_PATH,"w",encoding="utf-8") as f:f.write(x)
def waha(msg):
 c=load_config();url=str(c.get("waha_url") or "").rstrip("/");session=str(c.get("waha_session") or "default");number="".join(ch for ch in str(c.get("waha_number") or "") if ch.isdigit());key=str(c.get("waha_api_key") or "")
 if number.startswith("00"):number=number[2:]
 if len(number)==10 and number.startswith("3"):number="39"+number
 if not(url and number and key):log("⚠️ WAHA non completamente configurato.");return False
 try:requests.post(f"{url}/api/sendText",json={"session":session,"chatId":f"{number}@c.us","text":msg},headers={"X-Api-Key":key},timeout=10).raise_for_status();log("📲 Notifica WAHA inviata.");return True
 except Exception as e:log(f"⚠️ WAHA fallito: {e}");return False
def loop():
 global bot_thread
 c=load_config();interval=max(60,int(c.get("check_interval") or 60));token=str(c.get("meta_access_token") or "");uid=str(c.get("meta_ig_user_id") or DEFAULT_IG_USER_ID);rss=str(c.get("rss_url") or DEFAULT_RSS)
 try:
  if not token:raise RuntimeError("Token Meta mancante.")
  meta_test(token,uid);log(f"▶️ Bot automatico avviato. Controllo ogni {interval} secondi.")
  while not stop_event.is_set():
   state["last_check"]=datetime.now().strftime("%Y-%m-%d %H:%M:%S");a=latest_article(rss);state["preview"]=a;last=last_link()
   if not last:set_last(a["link"]);log("🛡️ Baseline iniziale salvata: nessun vecchio articolo pubblicato.")
   elif a["link"]!=last:
    log(f"🆕 Nuovo articolo: {a['title']}");mid=publish(token,uid,a);set_last(a["link"]);state["last_published"]=a["title"];log(f"✅ Pubblicato via API Meta. Media ID: {mid}")
   if stop_event.wait(interval):break
 except Exception as e:
  state["last_error"]=str(e);log(f"🛑 Bot fermato per errore: {e}");waha(f"Montagne & Paesi - Instagram Bot fermato per errore: {e}")
 finally:state["running"]=False;bot_thread=None;log("⏹️ Bot automatico fermo.")
def formcfg():
 c=load_config()
 for k,default in [("meta_ig_user_id",DEFAULT_IG_USER_ID),("rss_url",DEFAULT_RSS),("waha_url",""),("waha_session","default"),("waha_number","")]:c[k]=request.form.get(k,"").strip() or str(c.get(k) or default)
 for k in ["meta_access_token","waha_api_key"]:
  v=request.form.get(k,"").strip()
  if v:c[k]=v
 try:c["check_interval"]=max(60,int(request.form.get("check_interval","") or c.get("check_interval") or 60))
 except Exception:c["check_interval"]=60
 return c
PAGE='''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>M&P Instagram Bot</title><style>body{font-family:Arial;background:#f5f6f8;padding:20px}.box{max-width:950px;margin:auto;background:#fff;padding:22px;border-radius:12px}input{width:100%;box-sizing:border-box;padding:9px;margin:4px 0 10px}.btn{padding:10px 14px;margin:4px;border:0;border-radius:7px;cursor:pointer}.start{background:#16803b;color:#fff}.stop{background:#b42318;color:#fff}.blue{background:#1769e0;color:#fff}.card{padding:12px;background:#f7f7f7;border-radius:8px;margin-top:14px}.preview{white-space:pre-wrap}pre{background:#111;color:#eee;padding:12px;max-height:420px;overflow:auto;white-space:pre-wrap}.small{font-size:13px;color:#666}</style></head><body><div class="box"><h2>Montagne & Paesi → Instagram Bot</h2><div class="small">Versione <b>{{v}}</b> • API ufficiale Meta</div><form method="post"><h3>Instagram</h3><label>Instagram User ID</label><input name="meta_ig_user_id" value="{{uid}}"><label>Access Token Meta</label><input type="password" name="meta_access_token" placeholder="{% if token %}Token salvato{% else %}Token non configurato{% endif %}"><label>Feed RSS</label><input name="rss_url" value="{{rss}}"><label>Intervallo controllo (secondi, minimo 60)</label><input name="check_interval" value="{{interval}}"><h3>WhatsApp WAHA</h3><label>WAHA URL</label><input name="waha_url" value="{{wu}}"><label>Sessione</label><input name="waha_session" value="{{ws}}"><label>Numero destinatario</label><input name="waha_number" value="{{wn}}"><label>WAHA API Key</label><input type="password" name="waha_api_key" placeholder="{% if wk %}API Key salvata{% else %}API Key non configurata{% endif %}"><button class="btn blue" formaction="/save" formmethod="post">Salva</button><button class="btn blue" formaction="/preview" formmethod="post">Anteprima</button><button class="btn blue" formaction="/test_waha" formmethod="post">Test WAHA</button><button class="btn start" formaction="/start" formmethod="post">Avvia bot</button><button class="btn stop" formaction="/stop" formmethod="post">Ferma bot</button></form><div class="card"><b>Stato:</b> <span id="st"></span></div>{% if preview %}<div class="card preview"><b>Anteprima</b>\n\n{{preview.caption}}</div>{% endif %}<h3>Log</h3><button class="btn" onclick="copyLog()">Copia log</button><button class="btn" onclick="clearLog()">Azzera log</button><pre id="log"></pre><script>async function refresh(){let s=await(await fetch('/status')).json();document.getElementById('st').textContent=s.running?'ATTIVO':'FERMO';document.getElementById('log').textContent=await(await fetch('/logs')).text()}async function copyLog(){await navigator.clipboard.writeText(document.getElementById('log').textContent)}async function clearLog(){await fetch('/clear_logs',{method:'POST'});refresh()}setInterval(refresh,2500);refresh()</script></div></body></html>'''
@app.get("/")
def home():
 c=load_config();return render_template_string(PAGE,v=APP_VERSION,uid=html.escape(str(c.get("meta_ig_user_id") or DEFAULT_IG_USER_ID)),rss=html.escape(str(c.get("rss_url") or DEFAULT_RSS)),interval=c.get("check_interval",60),token=bool(c.get("meta_access_token")),wu=html.escape(str(c.get("waha_url") or "")),ws=html.escape(str(c.get("waha_session") or "default")),wn=html.escape(str(c.get("waha_number") or "")),wk=bool(c.get("waha_api_key")),preview=state["preview"])
@app.post("/save")
def save():save_config(formcfg());log("💾 Configurazione salvata. Le chiavi segrete non vengono visualizzate.");return redirect("/")
@app.post("/preview")
def preview():
 c=formcfg();save_config(c)
 try:state["preview"]=latest_article(c["rss_url"]);log(f"👁️ Anteprima pronta: {state['preview']['title']}")
 except Exception as e:log(f"❌ Anteprima fallita: {e}")
 return redirect("/")
@app.post("/start")
def start():
 global bot_thread
 save_config(formcfg())
 if state["running"]:log("ℹ️ Bot già attivo.");return redirect("/")
 stop_event.clear();state["running"]=True;state["last_error"]="";bot_thread=threading.Thread(target=loop,daemon=True);bot_thread.start();return redirect("/")
@app.post("/stop")
def stop():stop_event.set();state["running"]=False;log("⏹️ Arresto richiesto dal pannello.");return redirect("/")
@app.post("/test_waha")
def test_waha():save_config(formcfg());waha("Test Montagne & Paesi: notifiche Instagram Bot v2.1.1 funzionanti.");return redirect("/")
@app.get("/status")
def status():return jsonify({k:v for k,v in state.items() if k!="preview"}|{"version":APP_VERSION})
@app.get("/logs")
def getlogs():
 with lock:return "\n".join(logs),200,{"Content-Type":"text/plain; charset=utf-8"}
@app.post("/clear_logs")
def clearlogs():
 with lock:logs.clear()
 return jsonify({"ok":True})
if __name__=="__main__":log(f"🟢 Web UI pronta. Versione {APP_VERSION}.");log("🌐 API ufficiale Meta; attesa elaborazione container prima della pubblicazione.");app.run(host="0.0.0.0",port=8080)
