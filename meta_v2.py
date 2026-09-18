"""Montagne & Paesi Instagram Bot v2.2.7 - adaptive Meta publishing guard."""
import html,json,os,re,threading,time,unicodedata
from datetime import datetime,timedelta
import requests,feedparser
from bs4 import BeautifulSoup
from flask import Flask,request,redirect,jsonify,render_template_string
APP_VERSION="2.2.7";CONFIG_PATH=os.environ.get("CONFIG_PATH","/data/config.json");LAST_POST_PATH="/data/last_post_meta.txt";STATE_PATH="/data/meta_queue_state.json";GRAPH_BASE="https://graph.instagram.com";DEFAULT_IG_USER_ID="17841409303885274";DEFAULT_RSS="https://www.montagneepaesi.com/feed/";HUB_LINK="www.montagneepaesi.com";PUBLISH_GAP=120;DUPLICATE_TTL=86400;RATE_COOLDOWNS=[1800,3600,7200];META_LIMIT_SUBCODE=2207042;QUOTA_REFRESH=600;ADAPTIVE_MARGIN=1
app=Flask(__name__);logs=[];lock=threading.RLock();store_lock=threading.RLock();stop_event=threading.Event();bot_thread=None;scanner_thread=None
state={"running":False,"meta_connected":False,"username":"","last_error":"","last_check":"","last_published":"","preview":{},"queue":[],"next_publish_at":0,"meta_diagnostic":"","quota_usage":None,"quota_total":None,"quota_duration":None,"publish_status":"","adaptive_limit":None}
class MetaError(RuntimeError):
 def __init__(self,response,data):
  self.status=response.status_code;self.data=data if isinstance(data,dict) else {};self.error=self.data.get("error") if isinstance(self.data.get("error"),dict) else {};self.headers={k:v for k,v in response.headers.items() if k.lower() in ("x-app-usage","x-page-usage","x-business-use-case-usage","retry-after")};super().__init__(self.error.get("message") or f"Meta API HTTP {self.status}")
def log(m):
 line=f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {m}"
 with lock:logs.append(line);del logs[:-800]
 print(line,flush=True)
def atomic_json(path,data):
 os.makedirs(os.path.dirname(path),exist_ok=True);p=path+".tmp"
 with open(p,"w",encoding="utf-8") as f:json.dump(data,f,ensure_ascii=False,indent=2)
 os.replace(p,path)
def load_config():
 try:
  with open(CONFIG_PATH,"r",encoding="utf-8") as f:x=json.load(f);return x if isinstance(x,dict) else {}
 except Exception:return {}
def save_config(c):atomic_json(CONFIG_PATH,c)
def load_store():
 try:
  with open(STATE_PATH,"r",encoding="utf-8") as f:x=json.load(f)
  if not isinstance(x,dict):raise ValueError()
 except Exception:x={}
 for k,v in [("initialized",False),("seen",[]),("queue",[]),("stats",{}),("reports_sent",[]),("title_history",[]),("rate_limit_level",0),("cooldown_until",0),("adaptive_limit",None),("limit_blocked",False)]:x.setdefault(k,v)
 return x
def save_store(x):atomic_json(STATE_PATH,x)
def api(r):
 try:d=r.json()
 except Exception:d={}
 if not r.ok:raise MetaError(r,d)
 return d
def meta_diag(e):
 if not isinstance(e,MetaError):return str(e)
 er=e.error;parts=[f"HTTP={e.status}",f"type={er.get('type','-')}",f"code={er.get('code','-')}",f"subcode={er.get('error_subcode','-')}",f"message={er.get('message','-')}"]
 if er.get("error_user_title"):parts.append(f"user_title={er.get('error_user_title')}")
 if er.get("error_user_msg"):parts.append(f"user_msg={er.get('error_user_msg')}")
 if er.get("fbtrace_id"):parts.append(f"fbtrace_id={er.get('fbtrace_id')}")
 if e.headers:parts.append("headers="+json.dumps(e.headers,ensure_ascii=False))
 return " | ".join(parts)
def log_meta_error(e):d=meta_diag(e);state["meta_diagnostic"]=d;log("🔬 Diagnostica Meta: "+d);return d
def is_content_publish_limit(e):
 if not isinstance(e,MetaError):return False
 try:return int(e.error.get("error_subcode",0))==META_LIMIT_SUBCODE
 except Exception:return False
def publishing_limit(t,u,quiet=False):
 try:
  d=api(requests.get(f"{GRAPH_BASE}/v24.0/{u}/content_publishing_limit",params={"fields":"config,quota_usage","access_token":t},timeout=20));row=(d.get("data") or [{}])[0];cfg=row.get("config") or {};state["quota_usage"]=int(row.get("quota_usage",0));state["quota_total"]=int(cfg.get("quota_total",0)) if cfg.get("quota_total") is not None else None;state["quota_duration"]=int(cfg.get("quota_duration",0)) if cfg.get("quota_duration") is not None else None
  if not quiet:log("📊 Content Publishing Limit: "+json.dumps(d,ensure_ascii=False))
  return d
 except Exception as e:
  if not quiet:log("ℹ️ Content Publishing Limit non disponibile: "+meta_diag(e))
  return None
def meta_test(t,u):return api(requests.get(f"{GRAPH_BASE}/v24.0/{u}",params={"fields":"id,username","access_token":t},timeout=20))
def clean(x):return re.sub(r"\s+"," ",BeautifulSoup(x or "","html.parser").get_text(" ",strip=True)).strip()
def normalize_title(x):x=unicodedata.normalize("NFKD",clean(x)).encode("ascii","ignore").decode().lower();x=re.sub(r"\s+[2-9]\s*$","",x);return re.sub(r"[^a-z0-9]+"," ",x).strip()
def prune_history(s):s["title_history"]=[x for x in s.get("title_history",[]) if float(x.get("ts",0))>=time.time()-DUPLICATE_TTL]
def duplicate_title(s,title):prune_history(s);n=normalize_title(title);return bool(n) and any(x.get("normalized")==n for x in s["title_history"])
def remember_title(s,title):
 prune_history(s);n=normalize_title(title)
 if n:s["title_history"].append({"normalized":n,"title":clean(title),"ts":time.time()})
def is_rate_limit_error(e):return any(x in str(e).lower() for x in ["too many actions","rate limit","too many calls","request limit","temporarily blocked","try again later"])
def is_promo(x):return any(z in x.lower() for z in ["ricevi gratis le notizie di montagne","iscriviti al nostro canale whatsapp","clicca qui per iscriverti al canale","seguici anche su telegram","unisciti al canale telegram","clicca qui per iscriverti su telegram"])
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
def smart_hashtags(title,body):
 stop={"della","delle","degli","dello","alla","alle","agli","allo","nella","nelle","negli","nello","dalla","dalle","dagli","dallo","oltre","anche","sono","come","dopo","prima","durante","verso","senza","sulla","sulle","sugli","sullo","questa","questo","presentazione","evento","articolo","notizia","festeggia","attivita","torna","anni","strada","nuovo","nuova","oggi","ieri","domani","grande"};text=clean(title);alltext=(title+" "+body).lower();tags=[]
 def add(v):
  v=unicodedata.normalize("NFKD",v).encode("ascii","ignore").decode();v=re.sub(r"[^A-Za-z0-9]","",v)
  if len(v)>=3 and v.lower() not in stop and v.lower() not in {x.lower() for x in tags}:tags.append(v)
 for place in ["Gandino","Clusone","Bergamo","Brescia","Franciacorta","ValSeriana","ValBrembana","ValCamonica","Lombardia"]:
  if place.lower() in alltext:add(place)
 for q in re.findall(r"[\"“”']([^\"“”']{3,60})[\"“”']",text):
  words=[w for w in re.findall(r"[A-Za-zÀ-ÿ0-9]+",q) if w.lower() not in stop]
  if words:add("".join(w[:1].upper()+w[1:] for w in words[:4]))
 for w in re.findall(r"[A-Za-zÀ-ÿ0-9]+",text):
  if len(w)>=6 and w.lower() not in stop and w[:1].isupper():add(w[:1].upper()+w[1:])
 add("MontagneEPaesi");return " ".join("#"+x for x in tags[:7])
def feed_items(rss):
 f=feedparser.parse(rss)
 if not f.entries:raise RuntimeError("Nessun articolo disponibile nel feed RSS.")
 out=[]
 for e in f.entries:
  link=str(getattr(e,"link","")).strip();title=clean(str(getattr(e,"title","")))
  if link and title:out.append({"title":title,"link":link})
 if not out:raise RuntimeError("Feed RSS senza articoli validi.")
 return out,f.entries
def build_article(item,entry=None):
 link=item["link"];title=item["title"];r=requests.get(link,timeout=20,headers={"User-Agent":"Mozilla/5.0"});r.raise_for_status();s=BeautifulSoup(r.text,"html.parser");og=s.find("meta",property="og:image");image=(og.get("content") or "").strip() if og else ""
 if not image:raise RuntimeError("Immagine in evidenza pubblica non trovata.")
 # Meta supporta solo JPEG per i post immagine: verifica il media prima di creare il container.
 try:
  ir=requests.get(image,timeout=20,headers={"User-Agent":"Mozilla/5.0"},stream=True,allow_redirects=True)
  ir.raise_for_status();ctype=str(ir.headers.get("Content-Type") or "").split(";",1)[0].strip().lower()
  head=next(ir.iter_content(chunk_size=16),b"")
  ir.close()
  jpeg_magic=bool(head.startswith(b"\xff\xd8\xff"))
  if ctype not in ("image/jpeg","image/jpg") or not jpeg_magic:raise RuntimeError(f"MEDIA_PRECHECK_INVALID: formato immagine non JPEG ({ctype or 'Content-Type assente'}).")
 except RuntimeError:raise
 except Exception as e:raise RuntimeError("MEDIA_PRECHECK_TEMP: immagine non raggiungibile pubblicamente: "+str(e))
 body=article_text(s,entry or type("E",(),{"summary":""})());tags=smart_hashtags(title,body);suffix=f"\n\n{tags}\n\n👉 {HUB_LINK}";n=max(0,2200-len(title)-4-len(suffix));body=body[:n].rstrip()
 if len(body)>=n and n>4:body=body.rsplit(" ",1)[0].rstrip()+"…"
 return {"title":title,"link":link,"image_url":image,"caption":f"{title}\n\n{body}{suffix}"[:2200]}
def latest_article(rss):items,entries=feed_items(rss);return build_article(items[0],entries[0])
def wait_container(t,cid):
 # Meta raccomanda polling circa una volta al minuto per non oltre 5 minuti.
 for attempt in range(1,6):
  d=api(requests.get(f"{GRAPH_BASE}/v24.0/{cid}",params={"fields":"status_code,status","access_token":t},timeout=20));status=str(d.get("status_code") or "").upper();detail=str(d.get("status") or "");log(f"⏳ Container Meta {cid}: {status or detail or 'stato non disponibile'} ({attempt}/5)")
  if status=="FINISHED":return
  if status=="PUBLISHED":return
  if status in ("ERROR","EXPIRED"):raise RuntimeError(f"Elaborazione media Meta fallita: {detail or status}")
  if status not in ("","IN_PROGRESS"):log(f"ℹ️ Stato container Meta non previsto: {status}.")
  if attempt<5 and stop_event.wait(60):raise RuntimeError("Pubblicazione interrotta: arresto bot richiesto.")
 raise RuntimeError("Il container Meta non è diventato pronto entro 5 minuti.")
def publish(t,u,a):
 d=api(requests.post(f"{GRAPH_BASE}/v24.0/{u}/media",data={"image_url":a["image_url"],"caption":a["caption"],"access_token":t},timeout=30));cid=str(d.get("id") or "")
 if not cid:raise RuntimeError("creation_id Meta mancante.")
 log(f"📦 Container Meta creato: {cid}");wait_container(t,cid)
 try:d=api(requests.post(f"{GRAPH_BASE}/v24.0/{u}/media_publish",data={"creation_id":cid,"access_token":t},timeout=30))
 except Exception as e:log_meta_error(e);publishing_limit(t,u);raise
 mid=str(d.get("id") or "")
 if not mid:raise RuntimeError("Media ID Meta mancante.")
 return mid
def set_last(x):
 os.makedirs("/data",exist_ok=True)
 with open(LAST_POST_PATH,"w",encoding="utf-8") as f:f.write(x)
def stat_inc(s,key):
 day=datetime.now().strftime("%Y-%m-%d");s["stats"].setdefault(day,{"published":0,"errors":0});s["stats"][day][key]=int(s["stats"][day].get(key,0))+1
def sync_state(s):
 with lock:state["queue"]=[{"title":x.get("title",""),"link":x.get("link","")} for x in s.get("queue",[])];state["adaptive_limit"]=s.get("adaptive_limit")
def discover(rss):
 items,_=feed_items(rss);links=[x["link"] for x in items]
 with store_lock:
  s=load_store();prune_history(s)
  if not s["initialized"]:s["seen"]=links[:];set_last(items[0]["link"]);s["initialized"]=True;save_store(s);sync_state(s);log("🛡️ Baseline coda iniziale salvata.");return
  seen=set(s["seen"]);queued={x["link"] for x in s["queue"]};qt={normalize_title(x.get("title","")) for x in s["queue"]};new=[x for x in items if x["link"] not in seen and x["link"] not in queued];added=0
  for x in reversed(new):
   nt=normalize_title(x["title"])
   if duplicate_title(s,x["title"]) or nt in qt:log(f"⏭️ Duplicato ignorato (24h): {x['title']}");s["seen"].append(x["link"]);continue
   s["queue"].append(x);qt.add(nt);s["seen"].append(x["link"]);added+=1
  s["seen"]=s["seen"][-300:];save_store(s)
  if added:log(f"📚 Aggiunti {added} articoli alla coda. Totale in coda: {len(s['queue'])}.")
  sync_state(s)
def waha(msg):
 c=load_config();url=str(c.get("waha_url") or "").rstrip("/");session=str(c.get("waha_session") or "default");number="".join(ch for ch in str(c.get("waha_number") or "") if ch.isdigit());key=str(c.get("waha_api_key") or "")
 if number.startswith("00"):number=number[2:]
 if len(number)==10 and number.startswith("3"):number="39"+number
 if not(url and number and key):return False
 try:requests.post(f"{url}/api/sendText",json={"session":session,"chatId":f"{number}@c.us","text":msg},headers={"X-Api-Key":key},timeout=10).raise_for_status();log("📲 Notifica WAHA inviata.");return True
 except Exception as e:log(f"⚠️ WAHA fallito: {e}");return False
def report_loop():
 while True:
  try:
   now=datetime.now()
   if now.hour>=9:
    day=(now.date()-timedelta(days=1)).isoformat()
    with store_lock:
     s=load_store()
     if day not in s["reports_sent"]:
      st=s["stats"].get(day,{"published":0,"errors":0});msg=f"Montagne & Paesi – Resoconto Instagram\nIeri, {day}, sono stati pubblicati {int(st.get('published',0))} articoli su Instagram.\nArticoli attualmente in coda: {len(s['queue'])}.\nErrori di pubblicazione: {int(st.get('errors',0))}."
      if waha(msg):s["reports_sent"].append(day);s["reports_sent"]=s["reports_sent"][-60:];save_store(s)
  except Exception as e:log(f"⚠️ Resoconto giornaliero fallito: {e}")
  time.sleep(30)
def scanner_loop(rss,interval):
 global scanner_thread
 log(f"🔎 Scanner RSS indipendente avviato: controllo ogni {interval} secondi.")
 while not stop_event.is_set():
  try:state["last_check"]=datetime.now().strftime("%Y-%m-%d %H:%M:%S");discover(rss)
  except Exception as e:log(f"⚠️ Scansione RSS fallita: {e}")
  if stop_event.wait(interval):break
 scanner_thread=None;log("🔎 Scanner RSS fermo.")
def loop():
 global bot_thread,scanner_thread
 c=load_config();interval=max(60,int(c.get("check_interval") or 60));token=str(c.get("meta_access_token") or "");uid=str(c.get("meta_ig_user_id") or DEFAULT_IG_USER_ID);rss=str(c.get("rss_url") or DEFAULT_RSS);next_pub=0;next_quota_refresh=0;guard_logged=False
 try:
  if not token:raise RuntimeError("Token Meta mancante.")
  m=meta_test(token,uid);state["meta_connected"]=True;state["username"]=str(m.get("username") or "");publishing_limit(token,uid)
  with store_lock:s=load_store();sync_state(s);next_pub=max(0,float(s.get("cooldown_until",0)))
  scanner_thread=threading.Thread(target=scanner_loop,args=(rss,interval),daemon=True);scanner_thread.start();log("▶️ Publisher adattivo avviato: 2 minuti tra i post, con guardia automatica sul limite Meta.")
  while not stop_event.is_set():
   now=time.time()
   if now>=next_quota_refresh:publishing_limit(token,uid,True);next_quota_refresh=now+QUOTA_REFRESH
   with store_lock:s=load_store();prune_history(s);item=s["queue"][0] if s["queue"] else None;cooldown=float(s.get("cooldown_until",0));limit=s.get("adaptive_limit");blocked=bool(s.get("limit_blocked",False));sync_state(s)
   used=state.get("quota_usage");due=max(next_pub,cooldown)
   guard=bool(item and limit is not None and used is not None and int(used)>=int(limit))
   if guard:
    state["publish_status"]=f"Attesa quota adattiva ({used}/{limit})";state["next_publish_at"]=next_quota_refresh
    if not guard_logged:log(f"🧠 Guardia adattiva: quota {used}, soglia sicura {limit}. Nessun container creato; controllo sola lettura ogni {QUOTA_REFRESH//60} minuti.");guard_logged=True
    if stop_event.wait(2):break
    continue
   if blocked and limit is not None and used is not None and int(used)<int(limit):
    with store_lock:s=load_store();s["limit_blocked"]=False;save_store(s)
    blocked=False;log(f"🟢 Quota scesa a {used}, sotto la soglia adattiva {limit}: pubblicazioni riabilitate.")
   guard_logged=False
   if item and now>=due:
    with store_lock:
     s=load_store()
     if duplicate_title(s,item["title"]):s["queue"]=[x for x in s["queue"] if x.get("link")!=item["link"]];save_store(s);sync_state(s);continue
    state["publish_status"]="Pubblicazione in corso";log(f"🆕 Pubblicazione dalla coda: {item['title']}")
    try:
     a=build_article(item);state["preview"]=a;mid=publish(token,uid,a)
     with store_lock:s=load_store();s["queue"]=[x for x in s["queue"] if x.get("link")!=item["link"]];set_last(item["link"]);remember_title(s,item["title"]);stat_inc(s,"published");s["rate_limit_level"]=0;s["cooldown_until"]=0;s["limit_blocked"]=False;save_store(s);sync_state(s);has_more=bool(s["queue"])
     state["last_published"]=item["title"];state["last_error"]="";state["publish_status"]="Operativo";log(f"✅ Pubblicato via API Meta. Media ID: {mid}");publishing_limit(token,uid,True);next_pub=time.time()+PUBLISH_GAP;state["next_publish_at"]=next_pub if has_more else 0
    except Exception as e:
     if is_content_publish_limit(e):
      used=state.get("quota_usage")
      with store_lock:
       s=load_store();old=s.get("adaptive_limit")
       learned=max(1,int(used)-ADAPTIVE_MARGIN) if used is not None else (int(old) if old else 49)
       s["adaptive_limit"]=min(int(old),learned) if old is not None else learned;s["limit_blocked"]=True;s["cooldown_until"]=0;save_store(s);sync_state(s)
      state["adaptive_limit"]=s["adaptive_limit"];state["last_error"]=str(e);state["publish_status"]=f"Attesa quota adattiva ({used}/{s['adaptive_limit']})";next_quota_refresh=time.time()+QUOTA_REFRESH;state["next_publish_at"]=next_quota_refresh
      log(f"🧠 Limite Meta appreso: blocco a quota {used}; nuova soglia preventiva {s['adaptive_limit']}. Da ora nessun container finché la quota non scende sotto la soglia.");continue
     if is_rate_limit_error(e):
      with store_lock:s=load_store();level=min(int(s.get("rate_limit_level",0)),len(RATE_COOLDOWNS)-1);wait=RATE_COOLDOWNS[level];s["rate_limit_level"]=min(level+1,len(RATE_COOLDOWNS)-1);s["cooldown_until"]=time.time()+wait;save_store(s);sync_state(s);next_pub=s["cooldown_until"]
      state["next_publish_at"]=next_pub;state["last_error"]=str(e);state["publish_status"]="Cooldown Meta";continue
     with store_lock:s=load_store();stat_inc(s,"errors");save_store(s)
     raise
   elif item:state["next_publish_at"]=due
   else:state["next_publish_at"]=0;state["publish_status"]="In attesa di articoli"
   if stop_event.wait(2):break
 except Exception as e:state["last_error"]=str(e);log(f"🛑 Bot fermato per errore: {e}");stop_event.set();waha(f"Montagne & Paesi - Instagram Bot fermato per errore: {e}")
 finally:state["running"]=False;state["next_publish_at"]=0;bot_thread=None;log("⏹️ Publisher Instagram fermo.")
def formcfg():
 c=load_config()
 for k,default in [("meta_ig_user_id",DEFAULT_IG_USER_ID),("rss_url",DEFAULT_RSS),("waha_url",""),("waha_session","default"),("waha_number","")]:c[k]=request.form.get(k,"").strip() or str(c.get(k) or default)
 for k in ["meta_access_token","waha_api_key"]:
  v=request.form.get(k,"").strip()
  if v:c[k]=v
 try:c["check_interval"]=max(60,int(request.form.get("check_interval","") or c.get("check_interval") or 60))
 except Exception:c["check_interval"]=60
 return c
PAGE='''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>M&P Instagram Bot</title><style>body{font-family:Arial;background:#f5f6f8;padding:20px}.box{max-width:950px;margin:auto;background:#fff;padding:22px;border-radius:12px}input{width:100%;box-sizing:border-box;padding:9px;margin:4px 0 10px}.btn{padding:10px 14px;margin:4px;border:0;border-radius:7px}.start{background:#16803b;color:#fff}.stop{background:#b42318;color:#fff}.blue{background:#1769e0;color:#fff}.danger{background:#8b0000;color:#fff}.card{padding:12px;background:#f7f7f7;border-radius:8px;margin-top:14px}pre{background:#111;color:#eee;padding:12px;max-height:420px;overflow:auto;white-space:pre-wrap}.small{font-size:13px;color:#666}#queue li{margin:7px 0}</style></head><body><div class="box"><h2>Montagne & Paesi → Instagram Bot</h2><div class="small">Versione <b>{{v}}</b> • API ufficiale Meta</div><form method="post"><h3>Instagram</h3><label>Instagram User ID</label><input name="meta_ig_user_id" value="{{uid}}"><label>Access Token Meta</label><input type="password" name="meta_access_token" placeholder="{% if token %}Token salvato{% else %}Token non configurato{% endif %}"><label>Feed RSS</label><input name="rss_url" value="{{rss}}"><label>Intervallo controllo feed (secondi)</label><input name="check_interval" value="{{interval}}"><h3>WhatsApp WAHA</h3><label>WAHA URL</label><input name="waha_url" value="{{wu}}"><label>Sessione</label><input name="waha_session" value="{{ws}}"><label>Numero destinatario</label><input name="waha_number" value="{{wn}}"><label>WAHA API Key</label><input type="password" name="waha_api_key" placeholder="{% if wk %}API Key salvata{% else %}API Key non configurata{% endif %}"><button class="btn blue" formaction="/save">Salva</button><button class="btn blue" formaction="/preview">Anteprima</button><button class="btn blue" formaction="/test_waha">Test WAHA</button><button class="btn start" formaction="/start">Avvia bot</button><button class="btn stop" formaction="/stop">Ferma bot</button></form><div class="card"><b>Stato:</b> <span id="st"></span><br><b>Pubblicazione:</b> <span id="pstatus">-</span><br><b>Quota Meta:</b> <span id="quota">-</span><br><b>Soglia adattiva:</b> <span id="adaptive">-</span><br><span id="countdown" class="small"></span><br><span id="diag" class="small"></span></div><div class="card"><b>Coda — <span id="qcount">0</span></b> <button class="btn danger" onclick="clearQueue()">Cancella coda</button><ol id="queue"></ol></div>{% if preview %}<div class="card" style="white-space:pre-wrap"><b>Anteprima</b>\n\n{{preview.caption}}</div>{% endif %}<h3>Log</h3><button class="btn" id="copybtn" onclick="copyLog()">Copia log</button><button class="btn" onclick="clearLog()">Azzera log</button><pre id="log"></pre><script>async function refresh(){let s=await(await fetch('/status')).json();st.textContent=s.running?'ATTIVO':'FERMO';pstatus.textContent=s.publish_status||'-';quota.textContent=(s.quota_usage==null||s.quota_total==null)?'-':s.quota_usage+' / '+s.quota_total;adaptive.textContent=s.adaptive_limit==null?'In apprendimento':s.adaptive_limit;let q=s.queue||[];qcount.textContent=q.length;queue.innerHTML='';q.forEach(x=>{let li=document.createElement('li');li.textContent=x.title;queue.appendChild(li)});let sec=s.next_publish_in||0;countdown.textContent=q.length&&sec>0?'Prossimo controllo/tentativo tra '+Math.ceil(sec/60)+' minuti':'';diag.textContent=s.meta_diagnostic?'Ultima diagnostica Meta: '+s.meta_diagnostic:'';log.textContent=await(await fetch('/logs')).text()}async function clearQueue(){if(confirm('Cancellare tutti gli articoli attualmente in coda?')){await fetch('/clear_queue',{method:'POST'});refresh()}}async function copyLog(){let txt=document.getElementById('log').textContent,b=document.getElementById('copybtn');try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(txt)}else{let t=document.createElement('textarea');t.value=txt;t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.focus();t.select();t.setSelectionRange(0,t.value.length);if(!document.execCommand('copy'))throw new Error('copy');document.body.removeChild(t)}b.textContent='Copiato!';setTimeout(()=>b.textContent='Copia log',1500)}catch(e){window.prompt('Copia manualmente il log:',txt)}}async function clearLog(){await fetch('/clear_logs',{method:'POST'});refresh()}setInterval(refresh,2500);refresh()</script></div></body></html>'''
@app.get("/")
def home():
 c=load_config();return render_template_string(PAGE,v=APP_VERSION,uid=html.escape(str(c.get("meta_ig_user_id") or DEFAULT_IG_USER_ID)),rss=html.escape(str(c.get("rss_url") or DEFAULT_RSS)),interval=c.get("check_interval",60),token=bool(c.get("meta_access_token")),wu=html.escape(str(c.get("waha_url") or "")),ws=html.escape(str(c.get("waha_session") or "default")),wn=html.escape(str(c.get("waha_number") or "")),wk=bool(c.get("waha_api_key")),preview=state["preview"])
@app.post("/save")
def save():save_config(formcfg());return redirect("/")
@app.post("/preview")
def preview():
 c=formcfg();save_config(c)
 try:state["preview"]=latest_article(c["rss_url"])
 except Exception as e:log(f"❌ Anteprima fallita: {e}")
 return redirect("/")
@app.post("/start")
def start():
 global bot_thread
 save_config(formcfg())
 if state["running"]:return redirect("/")
 stop_event.clear();state["running"]=True;state["last_error"]="";bot_thread=threading.Thread(target=loop,daemon=True);bot_thread.start();return redirect("/")
@app.post("/stop")
def stop():stop_event.set();state["running"]=False;log("⏹️ Arresto richiesto.");return redirect("/")
@app.post("/clear_queue")
def clear_queue():
 with store_lock:s=load_store();n=len(s.get("queue",[]));s["queue"]=[];save_store(s);sync_state(s)
 with lock:state["next_publish_at"]=0
 log(f"🗑️ Coda cancellata manualmente: rimossi {n} articoli.");return jsonify({"ok":True,"removed":n})
@app.post("/test_waha")
def test_waha():save_config(formcfg());waha("Test Montagne & Paesi: Instagram Bot v2.2.7 funzionante.");return redirect("/")
@app.get("/status")
def status():
 with lock:d={k:v for k,v in state.items() if k!="preview"};d["queue"]=list(state["queue"]);d["version"]=APP_VERSION;d["next_publish_in"]=max(0,int(state["next_publish_at"]-time.time()+.999)) if state["next_publish_at"] else 0
 return jsonify(d)
@app.get("/logs")
def getlogs():
 with lock:return "\n".join(logs),200,{"Content-Type":"text/plain; charset=utf-8"}
@app.post("/clear_logs")
def clearlogs():
 with lock:logs.clear()
 return jsonify({"ok":True})
threading.Thread(target=report_loop,daemon=True).start()
if __name__=="__main__":log(f"🟢 Web UI pronta. Versione {APP_VERSION}.");log("🧠 Guardia adattiva Content Publishing Meta attiva.");app.run(host="0.0.0.0",port=8080)
