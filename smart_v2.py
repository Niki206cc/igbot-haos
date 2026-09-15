"""Montagne & Paesi Instagram Bot v2.3.5 - smart queue, resilient fetch, generic semantic hashtags."""
import re,time,unicodedata
from collections import Counter
import feedparser
import meta_v2 as core

APP_VERSION="2.3.5";MAX_AGE_HOURS=18;MAX_QUEUE=60;FETCH_RETRY=300
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
 title_raw=core.clean(title);lead_raw=core.clean((body or "")[:1000]);focus_raw=(title_raw+" "+lead_raw).strip();focus=norm(focus_raw);full=norm(title+" "+(body or ""));tags=[]
 def add(tag):
  tag=unicodedata.normalize("NFKD",tag).encode("ascii","ignore").decode();tag=re.sub(r"[^A-Za-z0-9]","",tag)
  if 3<=len(tag)<=35 and tag.lower() not in {x.lower() for x in tags}:tags.append(tag)
 def hf(p):return bool(re.search(p,focus,re.I))
 def ha(p):return bool(re.search(p,full,re.I))
 # Regole ad alta precisione per territori e aree editoriali piu frequenti.
 geo=[(r"\bbusto arsizio\b",["BustoArsizio","Varese","Lombardia"]),(r"\bvarese\b",["Varese","Lombardia"]),(r"\bbergamo\b",["Bergamo","Bergamasca","Lombardia"]),(r"\bbrescia\b",["Brescia","Bresciano","Lombardia"]),(r"\bfino del monte\b",["FinoDelMonte","ValSeriana","Orobie"]),(r"\bclusone\b",["Clusone","ValSeriana","Orobie"]),(r"\bgandino\b",["Gandino","ValGandino","ValSeriana"]),(r"\bval ?seriana\b",["ValSeriana","Orobie","Bergamo"]),(r"\bval ?brembana\b",["ValBrembana","Orobie","Bergamo"]),(r"\bval ?camonica\b",["ValCamonica","Brescia","Lombardia"]),(r"\bfranciacorta\b",["Franciacorta","Brescia","Lombardia"]),(r"\biseo\b",["Iseo","LagoDIseo","Brescia"]),(r"\borobie\b",["Orobie","Montagna","Lombardia"]),(r"\bbologna\b",["Bologna","EmiliaRomagna"]),(r"\bmilano\b",["Milano","Lombardia"]),(r"\broma\b",["Roma","Lazio"])]
 for p,vals in geo:
  if hf(p):
   for v in vals:add(v)
 named=[(r"\btre valli varesine(?: women's race| women.?s race)?\b",["TreValliVaresine"]),(r"\bdario acquaroli\b",["DarioAcquaroli"]),(r"\borobie cup(?: junior)?\b",["OrobieCup"]),(r"\bliceo minghetti\b",["LiceoMinghetti"]),(r"\b(?:universita|università) (?:degli studi )?di bergamo\b|\bunibg\b",["UniBg"])]
 for p,vals in named:
  if hf(p):
   for v in vals:add(v)
 # Temi generali: abbastanza ampi da funzionare su articoli mai visti, ma con match lessicali precisi.
 themes=[(r"\bscuol\w*\b|\bliceo\b|\bstudent\w*\b",["Scuola","Studenti"]),(r"\bistruzion\w*\b",["Istruzione"]),(r"\bprotest\w*\b|\bmanifestazion\w*\b",["Protesta"]),(r"\bmtb\b|\bmountain bike\b",["MTB","MountainBike"]),(r"\bciclism\w*\b|\bfederazione ciclistica\b",["Ciclismo"]),(r"\bcalcio\b",["Calcio"]),(r"\bbasket\b|\bpallacanestro\b",["Basket"]),(r"\bpallavolo\b|\bvolley\b",["Pallavolo"]),(r"\bsci\b|\bsciator\w*\b",["Sci"]),(r"\bnuoto\b|\bnuot\w*\b",["Nuoto"]),(r"\bincidente\b|\bschianto\b",["Cronaca","Incidente","SicurezzaStradale"]),(r"\barrest\w*\b|\bdenunc\w*\b|\bspaccio\b|\bdroga\b",["Cronaca","Sicurezza"]),(r"\bmaltempo\b|\btemporale\w*\b|\bfrana\b|\balluvion\w*\b",["Maltempo","Meteo"]),(r"\bincendio\b|\bvigili del fuoco\b",["Cronaca","VigiliDelFuoco"]),(r"\bfesta\b|\bfestival\b|\bsagra\b",["Eventi"]),(r"\bmontagna\b|\brifugio\b|\balpeggio\b",["Montagna","Natura"]),(r"\baeroporto\b|\borio al serio\b",["Aeroporto","OrioAlSerio"]),(r"\bministro\b|\bministero\b|\bparlamento\b|\bconsiglio comunale\b",["Politica"])]
 for p,vals in themes:
  if hf(p):
   for v in vals:add(v)
 # Entita generiche dal titolo/lead: solo sequenze con maiuscole, evitando la vecchia estrazione parola-per-parola.
 stop_first={"La","Il","Lo","Gli","Le","Un","Una","Uno","Nel","Nella","Nelle","Nei","Al","Alla","Alle","A","Da","Dal","Dalla","Di","Del","Della","Dei","Delle","Per","Con","Tra","Fra","Oggi","Ieri","Domani","Martedi","Mercoledi","Giovedi","Venerdi","Sabato","Domenica","Adnkronos"}
 candidates=[]
 for m in re.finditer(r"(?<!\w)([A-ZÀ-Ý][A-Za-zÀ-ÿ0-9'’.-]+(?:\s+(?:di|del|della|dei|degli|delle|e|[A-ZÀ-Ý][A-Za-zÀ-ÿ0-9'’.-]+)){0,3})",focus_raw):
  phrase=re.sub(r"\s+"," ",m.group(1)).strip(" .,:;–—-\"")
  words=phrase.split()
  if not words or words[0] in stop_first:continue
  if len(words)==1 and len(words[0])<5:continue
  candidates.append(phrase)
 counts=Counter(c.lower() for c in candidates)
 # Preferisce entita presenti nel titolo o ripetute nel lead. Massimo 3 per non creare hashtag casuali.
 used=0
 for phrase in candidates:
  if used>=3:break
  low=phrase.lower()
  if low in {"montagne e paesi","web info"}:continue
  if low in norm(title_raw) or counts[low]>=2:
   add("".join(w.capitalize() if w.lower() not in {"di","del","della","dei","degli","delle","e"} else w.capitalize() for w in phrase.split()));used+=1
 sport=bool(re.search(r"\b(?:sport|sportiv[oaie]|gara|gare|campionat[oi]|campion[ei]|campionessa|campionesse|torneo|coppa|cup|race|mtb|mountain bike|ciclism\w*|calcio|basket|pallacanestro|pallavolo|volley|sci|nuoto)\b",focus,re.I))
 event=bool(re.search(r"\b(?:gara|gare|campionato|torneo|coppa|cup|race|manifestazione|competizione)\b",focus,re.I))
 if sport:add("Sport")
 if sport and event:add("EventiSportivi")
 # Se si parla di reati/incidenti/proteste e non e ancora presente, Cronaca e un contesto utile.
 if hf(r"\b(?:protest\w*|incidente|schianto|arrest\w*|denunc\w*|spaccio|rapina|aggression\w*)\b"):add("Cronaca")
 # Brand sempre ultimo e mai sacrificato dal limite.
 tags=[x for x in tags if x.lower()!="montagneepaesi"][:11];tags.append("MontagneEPaesi")
 return " ".join("#"+x for x in tags)
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
 if removed:core.log(f"🧹 Migrazione coda v2.3.5: rimossi {removed} articoli automatici/vecchi.")
except Exception as e:core.log(f"⚠️ Migrazione coda intelligente non riuscita: {e}")
if __name__=="__main__":
 core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.");core.log("🧠 Hashtag generici da entita/tema + titolo/lead + brand garantito attivi.");core.app.run(host="0.0.0.0",port=8080)
