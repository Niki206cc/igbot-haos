"""Montagne & Paesi Instagram Bot v2.4.1 - controlled Meta probe + 150 smart queue + non-fatal media errors."""
import threading
import time
import smart_v2 as smart
import meta_v2 as core

APP_VERSION = "2.4.1"
PROBE_INTERVAL = 3 * 3600
FIRST_PROBE_DELAY = 120
PROBE_TIMEOUT = 600

smart.MAX_QUEUE = 150
core.APP_VERSION = APP_VERSION


def is_unsupported_media_error(e):
    """Errori permanenti del singolo media che non devono fermare il bot."""
    text = str(e).lower()
    if isinstance(e, core.MetaError):
        try:
            er = e.error or {}
            text += " " + str(er.get("message") or "").lower()
            text += " " + str(er.get("error_user_title") or "").lower()
            text += " " + str(er.get("error_user_msg") or "").lower()
        except Exception:
            pass
    return any(x in text for x in [
        "aspect ratio is not supported",
        "unsupported aspect ratio",
        "image aspect ratio",
    ])


# Mantiene scanner e publisher vivi se una singola immagine non e compatibile con Instagram.
_original_rate_error = core.is_rate_limit_error
def resilient_publish_error(e):
    if is_unsupported_media_error(e):
        return True
    return _original_rate_error(e)
core.is_rate_limit_error = resilient_publish_error

# Per gli errori permanenti di aspect ratio non ha senso aspettare 5 minuti e riprovare
# all'infinito la stessa testa della coda. Intercettiamo build/publish nel publisher tramite
# un wrapper di publish: al primo errore marchiamo il media come SKIP_MEDIA; il loop core lo
# tratta come errore retryable, ma il watchdog sottostante rimuove subito la testa della coda.
_original_publish = core.publish
def resilient_publish(token, user_id, article):
    try:
        return _original_publish(token, user_id, article)
    except Exception as e:
        if is_unsupported_media_error(e):
            raise RuntimeError("SKIP_MEDIA_ASPECT: " + str(e))
        raise
core.publish = resilient_publish

# Estende la classificazione retryable per impedire che il core arresti tutto.
_previous_rate_error = core.is_rate_limit_error
def keep_alive_error(e):
    return str(e).startswith("SKIP_MEDIA_ASPECT:") or _previous_rate_error(e)
core.is_rate_limit_error = keep_alive_error


def bad_media_queue_watchdog():
    """Rimuove dalla coda l'articolo che ha fallito definitivamente per aspect ratio."""
    last_seen = ""
    while True:
        try:
            err = str(core.state.get("last_error") or "")
            if err.startswith("SKIP_MEDIA_ASPECT:") and err != last_seen:
                last_seen = err
                with core.store_lock:
                    s = core.load_store()
                    if s.get("queue"):
                        bad = s["queue"].pop(0)
                        core.save_store(s)
                        core.sync_state(s)
                        core.log(f"⏭️ Immagine con proporzioni non supportate: articolo saltato senza fermare il bot: {bad.get('title','')}")
                        core.waha("⚠️ Instagram: articolo saltato perché l'immagine ha proporzioni non supportate.\n\n" + str(bad.get("title") or ""))
                # Azzera il cooldown introdotto dal core per questo errore permanente.
                with core.store_lock:
                    s = core.load_store()
                    s["cooldown_until"] = 0
                    s["rate_limit_level"] = 0
                    core.save_store(s)
                    core.sync_state(s)
        except Exception as e:
            core.log(f"⚠️ Watchdog media incompatibile: {e}")
        time.sleep(2)


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
                        s["probe_active"] = False
                        s["probe_after"] = now + PROBE_INTERVAL
                        core.save_store(s)
                        core.log(f"⏳ Probe Meta ancora bloccato: nuovo probe tra {PROBE_INTERVAL//3600} ore.")
                    elif current and current != previous:
                        s["probe_active"] = False
                        s["probe_after"] = 0
                        core.save_store(s)
                        core.log("🟢 Probe Meta riuscito: pubblicazione confermata, flusso normale riabilitato.")
                    elif now - started >= PROBE_TIMEOUT:
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
        core.log(f"🧹 Migrazione coda v2.4.1: rimossi {removed} articoli scaduti/non prioritari; capienza massima 150.")
except Exception as e:
    core.log(f"⚠️ Migrazione coda v2.4.1 non riuscita: {e}")

threading.Thread(target=probe_guard_loop, daemon=True).start()
threading.Thread(target=bad_media_queue_watchdog, daemon=True).start()

if __name__ == "__main__":
    core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
    core.log("🧠 Coda intelligente 150 + guardia adattiva + probe Meta controllato ogni 3 ore attivi.")
    core.log("🖼️ Errori permanenti di aspect ratio isolati: il singolo articolo viene saltato senza fermare scanner e publisher.")
    core.app.run(host="0.0.0.0", port=8080)
