"""Montagne & Paesi Instagram Bot v2.3.0 - smart editorial queue layer."""
import re,time,threading
from datetime import datetime
import feedparser
import meta_v2 as core

APP_VERSION="2.3.0"
MAX_AGE_HOURS=18
MAX_QUEUE=60

# Contenuti automatici/nazionali a bassa priorita per Instagram.
EXCLUDE_PATTERNS=[
 r"\boroscopo\b",r"\bbenzina\b.*\bdiesel\b",r"\bprezzi (?:del|carburanti)\b",
 r"\bfisco\b",r"\b730\b",r"\bsenato\b",r"\bcamera dei deputati\b",
 r"\bgoverno\b",r"\bmeloni\b",r"\bemmy\b",r"\bwall street\b",r"\bborse? europee\b",
 r"\bspread\b",r"\bmercati azionari\b"
]

LOCAL_TERMS=[
 "bergamo","brescia","clusone","gandino","albino","ardesio","darfo","boario",
 "val seriana","valseriana","val brembana","valbrembana","val camonica","valcamonica",
 "valtrompia","franciacorta","iseo","lovère","lovere","pisogne","sarnico","sovere",
 "pianico","rovetta","castione","presolana","schilpario","vilminore","colere","onore",
 "songavazzo","fino del monte","ponte nossa","vertova","casnigo","leffe","nembro",
 "alzano","seriate","treviglio","romano di lombardia","sirmione","desenzano","garda",
 "orobie","lombardia"
]
URGENT_TERMS=[
 "incidente","schianto","mortale","morto","deceduto","ferito","elisoccorso","incendio",
 "maltempo","temporale","frana","alluvione","caduta massi","strada chiusa","chiusura",
 "rapina","arrestato","arresti","scomparso","disperso","soccorso","vigili del fuoco"
]
SOCIAL_TERMS=[
 "evento","festa","festival","concerto","sport","campion","oro","gara","mostra",
 "foto","video","cervo","orso","lupo","montagna","alpeggio","rifugio","sagra"
]

def norm(x):
 return re.sub(r"\s+"," ",core.clean(x)).strip().lower()

def excluded(title):
 t=norm(title)
 # Una notizia locale non viene esclusa solo perche contiene anche un termine nazionale.
 if any(x in t for x in LOCAL_TERMS):return False
 return any(re.search(p,t,re.I) for p in EXCLUDE_PATTERNS)

def score(title):
 t=norm(title);s=0
 if any(x in t for x in LOCAL_TERMS):s+=50
 if any(x in t for x in URGENT_TERMS):s+=35
 if any(x in t for x in SOCIAL_TERMS):s+=15
 # Penalizza contenuti tipicamente nazionali/automatici senza eliminarli quando non c'e match netto.
 if any(x in t for x in ["nazionale","internazionale","politica","economia","agenzia"]):s-=15
 return s

def entry_ts(e):
 for k in ("published_parsed","updated_parsed"):
  v=getattr(e,k,None)
  if v:
   try:return time.mktime(v)
   except Exception:pass
 return time.time()

def prune_and_rank(s):
 now=time.time();kept=[];dropped=0
 for x in s.get("queue",[]):
  title=x.get("title","")
  if excluded(title):
   dropped+=1;continue
  added=float(x.get("added_at") or now)
  # Le urgenze invecchiano piu rapidamente: non ha senso pubblicare cronaca vecchia.
  age_limit=6*3600 if any(k in norm(title) for k in URGENT_TERMS) else MAX_AGE_HOURS*3600
  if now-added>age_limit:
   dropped+=1;continue
  x["added_at"]=added;x["priority"]=score(title);kept.append(x)
 kept.sort(key=lambda x:(-int(x.get("priority",0)),-float(x.get("added_at",0))))
 if len(kept)>MAX_QUEUE:
  dropped+=len(kept)-MAX_QUEUE;kept=kept[:MAX_QUEUE]
 s["queue"]=kept
 return dropped

def smart_discover(rss):
 f=feedparser.parse(rss)
 if not f.entries:raise RuntimeError("Nessun articolo disponibile nel feed RSS.")
 items=[]
 for e in f.entries:
  link=str(getattr(e,"link","")).strip();title=core.clean(str(getattr(e,"title","")))
  if link and title:items.append({"title":title,"link":link,"added_at":entry_ts(e),"priority":score(title)})
 if not items:raise RuntimeError("Feed RSS senza articoli validi.")
 links=[x["link"] for x in items]
 with core.store_lock:
  s=core.load_store();core.prune_history(s)
  dropped=prune_and_rank(s)
  if not s["initialized"]:
   s["seen"]=links[:];core.set_last(items[0]["link"]);s["initialized"]=True;core.save_store(s);core.sync_state(s);core.log("🛡️ Baseline coda intelligente salvata.");return
  seen=set(s["seen"]);queued={x["link"] for x in s["queue"]};qt={core.normalize_title(x.get("title","")) for x in s["queue"]};added=0;filtered=0
  for x in reversed([x for x in items if x["link"] not in seen and x["link"] not in queued]):
   s["seen"].append(x["link"])
   if excluded(x["title"]):filtered+=1;continue
   nt=core.normalize_title(x["title"])
   if core.duplicate_title(s,x["title"]) or nt in qt:
    core.log(f"⏭️ Duplicato ignorato (24h): {x['title']}");continue
   s["queue"].append(x);qt.add(nt);added+=1
  dropped+=prune_and_rank(s)
  s["seen"]=s["seen"][-500:];core.save_store(s);core.sync_state(s)
  if filtered:core.log(f"🧹 Filtro Instagram: esclusi {filtered} contenuti automatici/nazionali.")
  if dropped:core.log(f"⌛ Coda intelligente: rimossi {dropped} articoli vecchi/non prioritari.")
  if added:core.log(f"🧠 Coda intelligente: aggiunti {added} articoli. Totale: {len(s['queue'])}.")

# Innesta la nuova politica editoriale mantenendo API Meta, quota adattiva, WAHA e dashboard esistenti.
core.discover=smart_discover
core.APP_VERSION=APP_VERSION

# Migrazione immediata della coda gia presente: filtra e ordina senza cancellare lo stato seen.
try:
 with core.store_lock:
  s=core.load_store();n0=len(s.get("queue",[]));removed=prune_and_rank(s);core.save_store(s);core.sync_state(s)
 if removed:core.log(f"🧹 Migrazione coda v2.3.0: rimossi {removed} articoli automatici/vecchi; rimasti {len(s.get('queue',[]))}.")
except Exception as e:core.log(f"⚠️ Migrazione coda intelligente non riuscita: {e}")

if __name__=="__main__":
 core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
 core.log("🧠 Coda editoriale intelligente + guardia adattiva Meta attive.")
 core.app.run(host="0.0.0.0",port=8080)
