"""Montagne & Paesi Instagram Bot v2.4.0 - controlled Meta probe + 150 smart queue."""
import threading
import time
import smart_v2 as smart
import meta_v2 as core

APP_VERSION = "2.4.0"
PROBE_INTERVAL = 3 * 3600
FIRST_PROBE_DELAY = 120
PROBE_TIMEOUT = 600

smart.MAX_QUEUE = 150
core.APP_VERSION = APP_VERSION


def probe_guard_loop():
    """Consente un singolo tentativo reale quando quota_usage resta bloccato."""
    while True:
        try:
            now = time.time()
            with core.store_lock:
                s = core.load_store()
                blocked = bool(s.get("limit_blocked", False))
                limit = s.get("adaptive_limit")
                queue = s.get("queue", [])
                probe_after = float(s.get("probe_after", 0) or 0)
                probe_active = bool(s.get("probe_active", False))

                if blocked and limit is not None and queue and not probe_active:
                    if probe_after <= 0:
                        s["probe_after"] = now + FIRST_PROBE_DELAY
                        core.save_store(s)
                        core.log("🧪 Probe Meta programmato: primo tentativo controllato tra 2 minuti se la quota resta bloccata.")
                    elif now >= probe_after:
                        old_limit = int(limit)
                        s["probe_previous_limit"] = old_limit
                        s["probe_active"] = True
                        s["probe_started_at"] = now
                        s["probe_last_published"] = str(core.state.get("last_published") or "")
                        s["probe_after"] = now + PROBE_INTERVAL
                        # Apre temporaneamente la guardia. Il publisher effettua il tentativo
                        # sulla testa della coda; un nuovo 2207042 ripristina subito la soglia.
                        s["adaptive_limit"] = None
                        s["limit_blocked"] = False
                        s["cooldown_until"] = 0
                        core.save_store(s)
                        core.sync_state(s)
                        core.log(f"🧪 Probe Meta: quota ancora bloccata alla soglia {old_limit}. Autorizzato un solo tentativo reale.")

                elif probe_active:
                    previous = str(s.get("probe_last_published") or "")
                    current = str(core.state.get("last_published") or "")
                    started = float(s.get("probe_started_at", now))

                    if blocked:
                        # Il tentativo ha ricevuto nuovamente 2207042.
                        s["probe_active"] = False
                        s["probe_after"] = now + PROBE_INTERVAL
                        core.save_store(s)
                        core.log(f"⏳ Probe Meta ancora bloccato: nuovo probe tra {PROBE_INTERVAL//3600} ore.")
                    elif current and current != previous:
                        # Almeno un post e stato pubblicato: Meta ha riaperto davvero.
                        s["probe_active"] = False
                        s["probe_after"] = 0
                        core.save_store(s)
                        core.log("🟢 Probe Meta riuscito: pubblicazione confermata, flusso normale riabilitato.")
                    elif now - started >= PROBE_TIMEOUT:
                        # Nessuna conferma entro 10 minuti (es. timeout sito): richiude la
                        # guardia invece di lasciare tentativi liberi.
                        old = int(s.get("probe_previous_limit") or 49)
                        s["adaptive_limit"] = old
                        s["limit_blocked"] = True
                        s["probe_active"] = False
                        s["probe_after"] = now + PROBE_INTERVAL
                        core.save_store(s)
                        core.sync_state(s)
                        core.log(f"⏳ Probe Meta senza esito entro {PROBE_TIMEOUT//60} minuti: guardia ripristinata a {old}, nuovo probe tra {PROBE_INTERVAL//3600} ore.")

                elif not blocked and probe_after and not probe_active:
                    s["probe_after"] = 0
                    core.save_store(s)
        except Exception as e:
            core.log(f"⚠️ Watchdog probe Meta: {e}")
        time.sleep(15)


try:
    with core.store_lock:
        s = core.load_store()
        removed = smart.prune_and_rank(s)
        core.save_store(s)
        core.sync_state(s)
    if removed:
        core.log(f"🧹 Migrazione coda v2.4.0: rimossi {removed} articoli scaduti/non prioritari; capienza massima 150.")
except Exception as e:
    core.log(f"⚠️ Migrazione coda v2.4.0 non riuscita: {e}")

threading.Thread(target=probe_guard_loop, daemon=True).start()

if __name__ == "__main__":
    core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
    core.log("🧠 Coda intelligente 150 + guardia adattiva + probe Meta controllato ogni 3 ore attivi.")
    core.app.run(host="0.0.0.0", port=8080)
