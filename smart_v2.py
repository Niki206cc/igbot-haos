"""Montagne & Paesi Instagram Bot v2.3.3 - smart queue, resilient fetch, semantic hashtags."""
import re,time,unicodedata
import feedparser
import meta_v2 as core

APP_VERSION="2.3.3";MAX_AGE_HOURS=18;MAX_QUEUE=60;FETCH_RETRY=300
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
 raw=core.clean(title+" "+body);text=norm(raw);tags=[]
 def add(tag):
  tag=unicodedata.normalize("NFKD",tag).encode("ascii","ignore").decode();tag=re.sub(r"[^A-Za-z0-9]","",tag)
  if len(tag)>=3 and tag.lower() not in {x.lower() for x in tags}:tags.append(tag)
 def has(p):return bool(re.search(p,text,re.I))
 # Localita e aree: sempre hashtag composti, mai parole spezzate come #Busto #Arsizio.
 geo=[(r"\bbusto arsizio\b",["BustoArsizio","Varese","Lombardia"]),(r"\bvarese\b",["Varese","Lombardia"]),(r"\bbergamo\b",["Bergamo","Bergamasca","Lombardia"]),(r"\bbrescia\b",["Brescia","Bresciano","Lombardia"]),(r"\bfino del monte\b",["FinoDelMonte","ValSeriana","Orobie"]),(r"\bclusone\b",["Clusone","ValSeriana","Orobie"]),(r"\bgandino\b",["Gandino","ValGandino","ValSeriana"]),(r"\bval ?seriana\b",["ValSeriana","Orobie","Bergamo"]),(r"\bval ?brembana\b",["ValBrembana","Orobie","Bergamo"]),(r"\bval ?camonica\b",["ValCamonica","Brescia","Lombardia"]),(r"\bfranciacorta\b",["Franciacorta","Brescia","Lombardia"]),(r"\biseo\b",["Iseo","LagoDIseo","Brescia"]),(r"\borobie\b",["Orobie","Montagna","Lombardia"])]
 for p,vals in geo:
  if has(p):
   for v in vals:add(v)
 # Eventi/nome proprio riconoscibili: frasi complete e hashtag utili.
 named=[(r"\btre valli varesine(?: women's race| women.?s race)?\b",["TreValliVaresine"]),(r"\borobie cup(?: junior)?\b",["OrobieCup"]),(r"\b(?:universita|università) (?:degli studi )?di bergamo\b|\bunibg\b",["UniBg"])]
 for p,vals in named:
  if has(p):
   for v in vals:add(v)
 # Temi specifici. Non vengono aggiunte categorie non supportate dal testo.
 themes=[(r"\bciclism\w*\b",["Ciclismo","CiclismoItaliano"]),(r"\bciclism\w*\b.*\b(?:donn|femminil)\w*\b|\b(?:donn|femminil)\w*\b.*\bciclism\w*\b",["CiclismoFemminile"]),(r"\b(?:sport|sportiv[oaie]|gara|gare|campionat[oi]|campion[ei]|campionessa|campionesse|torneo|coppa|cup)\b",["Sport"]),(r"\bincidente\b|\bschianto\b",["Cronaca","Incidente","SicurezzaStradale"]),(r"\barrest\w*\b|\bdenunc\w*\b|\bspaccio\b|\bdroga\b",["Cronaca","Sicurezza"]),(r"\bmaltempo\b|\btemporale\w*\b|\bfrana\b|\balluvion\w*\b",["Maltempo","Meteo"]),(r"\bincendio\b|\bvigili del fuoco\b",["Cronaca","VigiliDelFuoco"]),(r"\bfesta\b|\bfestival\b|\bsagra\b|\bmanifestazione\b",["Eventi"]),(r"\bmontagna\b|\brifugio\b|\balpeggio\b",["Montagna","Natura"]),(r"\baeroporto\b|\borio al serio\b",["Aeroporto","OrioAlSerio"])]
 for p,vals in themes:
  if has(p):
   for v in vals:add(v)
 # Evento sportivo solo se sono presenti sia un evento/gara sia un riferimento sportivo reale.
 if has(r"\b(?:gara|gare|campionato|torneo|coppa|cup|race|manifestazione)\b") and has(r"\b(?:sport|sportiv[oaie]|ciclism\w*|calcio|basket|pallavolo|sci|atletica|nuoto)\b"):add("EventiSportivi")
 # Hashtag editoriali di contesto solo quando coerenti; nessuna estrazione cieca delle maiuscole dal titolo.
 if any(has(p) for p in [r"\bbergamo\b",r"\bbrescia\b",r"\bvarese\b",r"\bval ?seriana\b",r"\bval ?brembana\b",r"\bval ?camonica\b",r"\blombardia\b"]):add("NotizieLocali")
 add("MontagneEPaesi")
 return " ".join("#"+x for x in tags[:12])
core.smart_hashtags=richer_hashtags

_original_build=core.build_article
def resilient_build(item,entry=None):
 try:return _original_build(item,entry)
 except (core.requests.Timeout,core.requests.ConnectionError) as e:raise RuntimeError("TEMP_FETCH: "+str(e))
core.build_article=resilient_build
_original_rate=core.is_rate_limit_error
def retryable_error(e):return str(e).startswith("TEMP_FETCH:") or _original_rate(e)
core.is_rate_limit_error=retryable_error;core.RATE_COOLDOWNS=[FETCH_RETRY,FETCH_RETRY,FETCH_RETRY];core.discover=smart_discover;core.APP_VERSION=APP_VERSION
try:
 with core.store_lock:
  s=core.load_store();removed=prune_and_rank(s);core.save_store(s);core.sync_state(s)
 if removed:core.log(f"🧹 Migrazione coda v2.3.3: rimossi {removed} articoli automatici/vecchi.")
except Exception as e:core.log(f"⚠️ Migrazione coda intelligente non riuscita: {e}")
if __name__=="__main__":
 core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.");core.log("🧠 Hashtag semantici/composti + retry automatico + coda intelligente attivi.");core.app.run(host="0.0.0.0",port=8080)
