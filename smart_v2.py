"""Montagne & Paesi Instagram Bot v2.3.2 - smart queue, resilient fetch, precise hashtags."""
import re,time
import feedparser
import meta_v2 as core

APP_VERSION="2.3.2"
MAX_AGE_HOURS=18
MAX_QUEUE=60
FETCH_RETRY=300

EXCLUDE_PATTERNS=[r"\boroscopo\b",r"\bbenzina\b.*\bdiesel\b",r"\bprezzi (?:del|carburanti)\b",r"\bfisco\b",r"\b730\b",r"\bsenato\b",r"\bcamera dei deputati\b",r"\bgoverno\b",r"\bmeloni\b",r"\bemmy\b",r"\bwall street\b",r"\bborse? europee\b",r"\bspread\b",r"\bmercati azionari\b"]
LOCAL_TERMS=["bergamo","brescia","clusone","gandino","albino","ardesio","darfo","boario","val seriana","valseriana","val brembana","valbrembana","val camonica","valcamonica","valtrompia","franciacorta","iseo","lovere","pisogne","sarnico","sovere","pianico","rovetta","castione","presolana","schilpario","vilminore","colere","onore","songavazzo","fino del monte","ponte nossa","vertova","casnigo","leffe","nembro","alzano","seriate","treviglio","romano di lombardia","sirmione","desenzano","garda","orobie","lombardia"]
URGENT_TERMS=["incidente","schianto","mortale","morto","deceduto","ferito","elisoccorso","incendio","maltempo","temporale","frana","alluvione","caduta massi","strada chiusa","chiusura","rapina","arrestato","arresti","scomparso","disperso","soccorso","vigili del fuoco"]
SOCIAL_TERMS=["evento","festa","festival","concerto","sport","campion","oro","gara","mostra","foto","video","cervo","orso","lupo","montagna","alpeggio","rifugio","sagra"]

def norm(x):return re.sub(r"\s+"," ",core.clean(x)).strip().lower()
def excluded(title):
 t=norm(title)
 if any(x in t for x in LOCAL_TERMS):return False
 return any(re.search(p,t,re.I) for p in EXCLUDE_PATTERNS)
def score(title):
 t=norm(title);s=0
 if any(x in t for x in LOCAL_TERMS):s+=50
 if any(x in t for x in URGENT_TERMS):s+=35
 if any(x in t for x in SOCIAL_TERMS):s+=15
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
  if excluded(title):dropped+=1;continue
  added=float(x.get("added_at") or now);age_limit=6*3600 if any(k in norm(title) for k in URGENT_TERMS) else MAX_AGE_HOURS*3600
  if now-added>age_limit:dropped+=1;continue
  x["added_at"]=added;x["priority"]=score(title);kept.append(x)
 kept.sort(key=lambda x:(-int(x.get("priority",0)),-float(x.get("added_at",0))))
 if len(kept)>MAX_QUEUE:dropped+=len(kept)-MAX_QUEUE;kept=kept[:MAX_QUEUE]
 s["queue"]=kept;return dropped

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
  s=core.load_store();core.prune_history(s);dropped=prune_and_rank(s)
  if not s["initialized"]:s["seen"]=links[:];core.set_last(items[0]["link"]);s["initialized"]=True;core.save_store(s);core.sync_state(s);core.log("🛡️ Baseline coda intelligente salvata.");return
  seen=set(s["seen"]);queued={x["link"] for x in s["queue"]};qt={core.normalize_title(x.get("title","")) for x in s["queue"]};added=0;filtered=0
  for x in reversed([x for x in items if x["link"] not in seen and x["link"] not in queued]):
   s["seen"].append(x["link"])
   if excluded(x["title"]):filtered+=1;continue
   nt=core.normalize_title(x["title"])
   if core.duplicate_title(s,x["title"]) or nt in qt:core.log(f"⏭️ Duplicato ignorato (24h): {x['title']}");continue
   s["queue"].append(x);qt.add(nt);added+=1
  dropped+=prune_and_rank(s);s["seen"]=s["seen"][-500:];core.save_store(s);core.sync_state(s)
  if filtered:core.log(f"🧹 Filtro Instagram: esclusi {filtered} contenuti automatici/nazionali.")
  if dropped:core.log(f"⌛ Coda intelligente: rimossi {dropped} articoli vecchi/non prioritari.")
  if added:core.log(f"🧠 Coda intelligente: aggiunti {added} articoli. Totale: {len(s['queue'])}.")

def richer_hashtags(title,body):
 text=norm(title+" "+body);tags=[]
 def add(tag):
  tag=re.sub(r"[^A-Za-z0-9]","",tag)
  if tag and tag.lower() not in {x.lower() for x in tags}:tags.append(tag)
 def matches(patterns):return any(re.search(p,text,re.I) for p in patterns)
 places={"bergamo":["Bergamo","Bergamasca","Lombardia"],"brescia":["Brescia","Bresciano","Lombardia"],"clusone":["Clusone","ValSeriana","Orobie"],"gandino":["Gandino","ValGandino","ValSeriana"],"fino del monte":["FinoDelMonte","ValSeriana","Orobie"],"val seriana":["ValSeriana","Orobie","Bergamo"],"valseriana":["ValSeriana","Orobie","Bergamo"],"val brembana":["ValBrembana","Orobie","Bergamo"],"valcamonica":["ValCamonica","Brescia","Lombardia"],"val camonica":["ValCamonica","Brescia","Lombardia"],"franciacorta":["Franciacorta","Brescia","Lombardia"],"iseo":["Iseo","LagoDIseo","Brescia"]}
 for key,vals in places.items():
  if re.search(r"(?<!\w)"+re.escape(key)+r"(?!\w)",text):
   for v in vals:add(v)
 topics=[
  ([r"\bincidente\b",r"\bschianto\b"],["Incidente","Cronaca","SicurezzaStradale"]),
  ([r"\barrest\w*\b",r"\bdenunc\w*\b",r"\bspaccio\b",r"\bdroga\b"],["Cronaca","Carabinieri","Sicurezza"]),
  ([r"\bmaltempo\b",r"\btemporale\w*\b",r"\bfrana\b",r"\balluvion\w*\b"],["Maltempo","Meteo","Territorio"]),
  ([r"\bincendio\b",r"\bvigili del fuoco\b"],["Incendio","VigiliDelFuoco","Cronaca"]),
  ([r"\bevento\b",r"\bfesta\b",r"\bfestival\b",r"\bsagra\b"],["Eventi","Territorio","Cultura"]),
  ([r"\bsport\b",r"\bsportivo\b",r"\bsportiva\b",r"\bsportivi\b",r"\bsportive\b",r"\bgara\b",r"\bgare\b",r"\bcampion(?:e|essa|i|esse|ato|ati|ati|ati)\b",r"\btorneo\b",r"\bcoppa\b",r"\bcup\b"],["Sport","SportLocale","Territorio"]),
  ([r"\bmontagna\b",r"\borobie\b",r"\brifugio\b",r"\balpeggio\b"],["Montagna","Orobie","Natura"]),
  ([r"\bcomune\b",r"\bsindaco\b",r"\bamministrazione\b"],["Comuni","Territorio","PoliticaLocale"]),
  ([r"\bscuola\b",r"\buniversit\w*\b",r"\bunibg\b"],["Scuola","Universita","Bergamo"]),
  ([r"\baeroporto\b",r"\borio al serio\b"],["Aeroporto","OrioAlSerio","Bergamo"])]
 for patterns,vals in topics:
  if matches(patterns):
   for v in vals:add(v)
 stop={"della","delle","degli","dello","alla","alle","agli","allo","nella","nelle","negli","nello","dalla","dalle","dagli","dallo","come","contro","dopo","prima","nuovo","nuova","anni","oggi","ieri","domani","grande","spazio","mondo","citta","centro"}
 for w in re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9'-]{3,}",core.clean(title)):
  if w.lower() not in stop and w[:1].isupper():add(w)
  if len(tags)>=11:break
 add("MontagneEPaesi")
 for x in ["NotizieLocali","Territorio","Lombardia"]:
  if len(tags)<8:add(x)
 return " ".join("#"+x for x in tags[:12])
core.smart_hashtags=richer_hashtags

_original_build=core.build_article
def resilient_build(item,entry=None):
 try:return _original_build(item,entry)
 except (core.requests.Timeout,core.requests.ConnectionError) as e:raise RuntimeError("TEMP_FETCH: "+str(e))
core.build_article=resilient_build
_original_rate=core.is_rate_limit_error
def retryable_error(e):return str(e).startswith("TEMP_FETCH:") or _original_rate(e)
core.is_rate_limit_error=retryable_error
core.RATE_COOLDOWNS=[FETCH_RETRY,FETCH_RETRY,FETCH_RETRY]
core.discover=smart_discover
core.APP_VERSION=APP_VERSION
try:
 with core.store_lock:
  s=core.load_store();removed=prune_and_rank(s);core.save_store(s);core.sync_state(s)
 if removed:core.log(f"🧹 Migrazione coda v2.3.2: rimossi {removed} articoli automatici/vecchi.")
except Exception as e:core.log(f"⚠️ Migrazione coda intelligente non riuscita: {e}")
if __name__=="__main__":
 core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
 core.log("🧠 Hashtag con corrispondenza precisa + retry automatico + coda intelligente attivi.")
 core.app.run(host="0.0.0.0",port=8080)
