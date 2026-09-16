"""Montagne & Paesi Instagram Bot v2.4.0 - controlled Meta probe + 150 smart queue."""
import threading
import time
import smart_v2 as smart
import meta_v2 as core

APP_VERSION = "2.4.0"
PROBE_INTERVAL = 3 * 3600
FIRST_PROBE_DELAY = 120

# La coda intelligente resta ordinata per priorita/freschezza, ma durante blocchi lunghi
# conserva fino a 150 articoli invece di sacrificarli gia a quota 60.
smart.MAX_QUEUE = 150
core.APP_VERSION = APP_VERSION


def probe_guard_loop():
    """Sblocca un solo probe controllato se il contatore Meta resta fermo.

    Il core continua a usare quota_usage e la soglia adattiva. Quando Meta dichiara
    ancora una quota bloccante per ore, questo watchdog consente un tentativo reale.
    Se Meta risponde ancora 2207042, il core reimpara la soglia e il prossimo probe
    viene rinviato. Se il tentativo riesce, il publisher puo riprendere normalmente
    fino a un eventuale nuovo blocco reale.
    """
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

                if blocked and limit is not None and queue:
                    if probe_after <= 0:
                        # Al primo avvio della 2.4.0 non aspettiamo altre ore: il blocco
                        # puo essere gia vecchio. Facciamo il primo probe dopo 2 minuti.
                        s["probe_after"] = now + FIRST_PROBE_DELAY
                        s["probe_active"] = False
                        core.save_store(s)
                        core.log("🧪 Probe Meta programmato: primo tentativo controllato tra 2 minuti se la quota resta bloccata.")
                    elif now >= probe_after and not probe_active:
                        old_limit = int(limit)
                        s["probe_previous_limit"] = old_limit
                        s["probe_active"] = True
                        s["probe_started_at"] = now
                        s["probe_after"] = now + PROBE_INTERVAL
                        # Disabilita temporaneamente la guardia: il core fara UN tentativo.
                        # Se fallisce con 2207042, il core rimette subito adaptive_limit.
                        s["adaptive_limit"] = None
                        s["limit_blocked"] = False
                        s["cooldown_until"] = 0
                        core.save_store(s)
                        core.sync_state(s)
                        core.log(f"🧪 Probe Meta: quota ancora bloccata alla soglia {old_limit}. Autorizzato un tentativo reale controllato.")
                elif probe_active:
                    # Se dopo il probe il core ha rimesso limit_blocked=True significa
                    # che Meta ha rifiutato di nuovo. Se invece resta False, il probe e
                    # riuscito e lasciamo proseguire normalmente.
                    if blocked:
                        s["probe_active"] = False
                        s["probe_after"] = now + PROBE_INTERVAL
                        core.save_store(s)
                        core.log(f"⏳ Probe Meta ancora bloccato: nuovo tentativo non prima di {PROBE_INTERVAL//3600} ore.")
                    elif now - float(s.get("probe_started_at", now)) > 180:
                        s["probe_active"] = False
                        s["probe_after"] = 0
                        core.save_store(s)
                        core.log("🟢 Probe Meta riuscito: pubblicazioni normali riabilitate.")
                elif not blocked and probe_after:
                    s["probe_after"] = 0
                    s["probe_active"] = False
                    core.save_store(s)
        except Exception as e:
            core.log(f"⚠️ Watchdog probe Meta: {e}")
        time.sleep(15)


# Ripulisce/riordina subito la coda con il nuovo limite 150 senza cancellare gli articoli esistenti.
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
