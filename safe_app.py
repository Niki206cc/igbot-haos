"""Safety wrapper for Montagne & Paesi Instagram bot.

Version 1.1.3
- Stops immediately on authentication/upload errors instead of retrying forever.
- Keeps /qe/expose/ 404 handling as successful post-upload secondary error.
- Sends WhatsApp error alerts through WAHA.
- Keeps one-shot publishing test and integrity protection.
"""

from datetime import datetime
import app as core

APP_VERSION = "1.1.3"
DEFAULT_WAHA_URL = "http://192.168.1.69:3000"
DEFAULT_WAHA_SESSION = "default"
DEFAULT_WAHA_NUMBER = "393398164623"

core.metrics["version"] = APP_VERSION


def _is_integrity_restriction(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "restricted from uploading" in text
        or ("error_domain" in text and "integrity" in text)
        or "integrity restriction" in text
    )


def _is_qe_expose_404(exc: Exception) -> bool:
    text = str(exc).lower()
    return "404" in text and "/api/v1/qe/expose/" in text


def _is_not_logged(exc: Exception) -> bool:
    text = str(exc).lower()
    return "non loggato" in text or "login_required" in text or "login required" in text


def _notification_config():
    cfg = core.load_config()
    url = str(cfg.get("waha_url") or DEFAULT_WAHA_URL).strip().rstrip("/")
    # Accept a pasted dashboard URL too.
    if url.endswith("/dashboard"):
        url = url[:-10].rstrip("/")
    session = str(cfg.get("waha_session") or DEFAULT_WAHA_SESSION).strip()
    number = str(cfg.get("waha_number") or DEFAULT_WAHA_NUMBER).strip()
    digits = "".join(ch for ch in number if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("3"):
        digits = "39" + digits
    return url, session, digits


def send_waha_notification(message: str) -> bool:
    """Send a best-effort WhatsApp alert. Notification failure never restarts the bot."""
    url, session, number = _notification_config()
    if not url or not session or not number:
        core.log("⚠️ Notifica WAHA non configurata.")
        return False
    try:
        endpoint = f"{url}/api/sendText"
        payload = {
            "session": session,
            "chatId": f"{number}@c.us",
            "text": message,
        }
        r = core.requests.post(endpoint, json=payload, timeout=10)
        r.raise_for_status()
        core.log("📲 Notifica errore inviata tramite WAHA.")
        return True
    except Exception as exc:
        core.log(f"⚠️ Invio notifica WAHA fallito: {exc}")
        return False


def stop_with_error(error, title="", prefix="Errore Instagram"):
    text = str(error)
    core.set_metric("last_error", text)
    core.set_metric("running", False)
    core.stop_event.set()
    core.log(f"🛑 Bot fermato automaticamente: {text}")
    msg = f"⚠️ Instagram Bot Montagne & Paesi FERMATO\n{prefix}: {text}"
    if title:
        msg += f"\nArticolo: {title}"
    msg += "\nNessun nuovo tentativo automatico verrà eseguito."
    send_waha_notification(msg)


class PostUploadedWithSecondaryApiError(Exception):
    pass


OriginalInstagramPoster = core.InstagramPoster


class SafeInstagramPoster(OriginalInstagramPoster):
    def post_photo(self, image_path: str, caption: str):
        try:
            return super().post_photo(image_path, caption)
        except Exception as exc:
            if _is_qe_expose_404(exc):
                core.log("⚠️ Upload inviato; errore API secondario /qe/expose/ 404 dopo la pubblicazione.")
                raise PostUploadedWithSecondaryApiError(str(exc)) from exc
            raise


core.InstagramPoster = SafeInstagramPoster


def safe_bot_loop(username: str, password: str, rss_url: str):
    poster = SafeInstagramPoster()
    core.set_metric("running", True)
    core.set_metric("last_error", "")

    try:
        poster.login_for_posting(username, password)
    except core.TwoFactorRequired as exc:
        stop_with_error(exc, prefix="Instagram richiede 2FA")
        return
    except core.ChallengeRequired as exc:
        stop_with_error(exc, prefix="Instagram richiede Challenge")
        return
    except Exception as exc:
        stop_with_error(exc, prefix="Errore login Instagram")
        return

    core.os.makedirs(core.IMAGES_DIR, exist_ok=True)

    while not core.stop_event.is_set():
        title = ""
        try:
            entry = core.get_latest_entry(rss_url)
            if not entry:
                core.log("⚠️ Nessun articolo nel feed.")
                core.stop_event.wait(core.CHECK_INTERVAL)
                continue

            link = getattr(entry, "link", "").strip()
            title = core.clean_text(getattr(entry, "title", "").strip())
            if not link or not title:
                core.log("⚠️ Entry RSS incompleta (manca titolo/link).")
                core.stop_event.wait(core.CHECK_INTERVAL)
                continue

            if link == core.get_last_posted_url():
                core.log("ℹ️ Nessun nuovo articolo.")
                core.stop_event.wait(core.CHECK_INTERVAL)
                continue

            img_url = core.get_featured_image_url(link)
            if not img_url:
                stop_with_error("Immagine in evidenza non trovata (og:image).", title, "Errore preparazione post")
                break

            excerpt = core.get_article_excerpt(link, max_chars=900)
            if not excerpt:
                excerpt = core.get_excerpt_from_feed_entry(entry, max_chars=900)
            core.log(f"📝 Testo estratto: {len(excerpt)} caratteri")

            tags = core.hashtags_from_title(title)
            caption = core.clamp_caption(f"{title}\n\n{excerpt}\n\n{tags}\n\n👉 {core.HUB_LINK}", 2200)

            if not core.download_image(img_url, core.LATEST_IMG_PATH):
                stop_with_error("Download immagine fallito.", title, "Errore preparazione post")
                break

            core.log(f"📸 Pubblico: {title}")
            core.log("📤 Carico il post su Instagram...")

            secondary_error = False
            try:
                poster.post_photo(core.LATEST_IMG_PATH, caption)
            except PostUploadedWithSecondaryApiError:
                secondary_error = True
            except Exception as exc:
                # v1.1.3: no upload retry at all. This avoids hammering Instagram
                # and prevents duplicate posts when the real upload state is uncertain.
                if _is_integrity_restriction(exc):
                    stop_with_error(exc, title, "Blocco integrity Instagram")
                elif _is_not_logged(exc):
                    stop_with_error(exc, title, "Sessione Instagram scaduta / non loggata")
                else:
                    stop_with_error(exc, title, "Errore pubblicazione Instagram")
                break

            core.save_last_posted_url(link)
            core.set_metric("last_title", title)
            core.set_metric("last_link", link)
            core.set_metric("last_published_at", datetime.now().isoformat())
            core.inc_posts_count()
            core.set_metric("last_error", "")
            if secondary_error:
                core.log("✅ Post considerato pubblicato: ignorato solo l'errore API secondario /qe/expose/ 404.")
            else:
                core.log("✅ Pubblicato su Instagram.")

        except Exception as exc:
            stop_with_error(exc, title, "Errore imprevisto del ciclo")
            break

        core.stop_event.wait(core.CHECK_INTERVAL)

    core.log("⏹️ Bot fermato.")
    core.set_metric("running", False)


core.bot_loop = safe_bot_loop


def safe_status():
    with core.bot_lock:
        thread_alive = core.bot_thread is not None and core.bot_thread.is_alive()
    with core.metrics_lock:
        last_error = str(core.metrics.get("last_error", ""))
    blocked = "integrity" in last_error.lower() or "restricted from uploading" in last_error.lower()
    return core.jsonify({"running": bool(thread_alive and not blocked), "blocked": blocked, "version": APP_VERSION})


core.app.view_functions["status"] = safe_status


@core.app.post("/test_upload")
def test_upload():
    with core.bot_lock:
        if core.bot_thread is not None and core.bot_thread.is_alive():
            core.log("⚠️ Test singolo non eseguito: il bot è già in esecuzione.")
            return core.redirect("/")

    cfg = core.load_config()
    username = core.request.form.get("username", "").strip() or cfg.get("username", "").strip()
    password = core.request.form.get("password", "").strip() or cfg.get("password", "").strip()
    rss_url = core.request.form.get("rss_url", "").strip() or cfg.get("rss_url", core.DEFAULT_RSS)
    if not username or not password:
        core.log("❌ Test pubblicazione: inserisci username e password.")
        return core.redirect("/")

    core.log("🧪 Test pubblicazione singola avviato: verrà fatto un solo tentativo.")
    core.set_metric("last_error", "")
    title = ""
    try:
        poster = SafeInstagramPoster()
        poster.login_for_posting(username, password)
        entry = core.get_latest_entry(rss_url)
        if not entry:
            raise RuntimeError("Nessun articolo disponibile nel feed RSS.")
        link = getattr(entry, "link", "").strip()
        title = core.clean_text(getattr(entry, "title", "").strip())
        if not link or not title:
            raise RuntimeError("Ultimo articolo RSS incompleto: manca titolo o link.")
        img_url = core.get_featured_image_url(link)
        if not img_url:
            raise RuntimeError("Immagine in evidenza non trovata (og:image).")
        excerpt = core.get_article_excerpt(link, max_chars=900) or core.get_excerpt_from_feed_entry(entry, max_chars=900)
        tags = core.hashtags_from_title(title)
        caption = core.clamp_caption(f"{title}\n\n{excerpt}\n\n{tags}\n\n👉 {core.HUB_LINK}", 2200)
        if not core.download_image(img_url, core.LATEST_IMG_PATH):
            raise RuntimeError("Download immagine fallito.")
        core.log(f"🧪 Test: pubblico una sola volta: {title}")
        secondary_error = False
        try:
            poster.post_photo(core.LATEST_IMG_PATH, caption)
        except PostUploadedWithSecondaryApiError:
            secondary_error = True
        core.save_last_posted_url(link)
        core.set_metric("last_title", title)
        core.set_metric("last_link", link)
        core.set_metric("last_published_at", datetime.now().isoformat())
        core.inc_posts_count()
        core.set_metric("last_error", "")
        core.set_metric("running", False)
        if secondary_error:
            core.log("✅ Test: upload accettato; ignorato l'errore API secondario /qe/expose/ 404. Nessun retry.")
        else:
            core.log("✅ Test pubblicazione riuscito: post pubblicato una sola volta.")
        core.log("⏹️ Il bot resta STOPPED.")
    except Exception as exc:
        stop_with_error(exc, title, "Errore test pubblicazione")
    return core.redirect("/")


@core.app.post("/test_waha")
def test_waha():
    ok = send_waha_notification("✅ Test WAHA - Instagram Bot Montagne & Paesi: notifiche operative.")
    if ok:
        core.log("✅ Test WAHA completato.")
    return core.redirect("/")


# Add WAHA defaults to persisted config without exposing secrets.
_original_load_config = core.load_config
_original_save_config = core.save_config


def load_config_with_waha():
    cfg = _original_load_config()
    cfg.setdefault("waha_url", DEFAULT_WAHA_URL)
    cfg.setdefault("waha_session", DEFAULT_WAHA_SESSION)
    cfg.setdefault("waha_number", DEFAULT_WAHA_NUMBER)
    return cfg


core.load_config = load_config_with_waha

# Web UI version and controls.
core.PAGE = core.PAGE.replace(
    '<h2 style="margin:0 0 6px;">Montagne & Paesi → Instagram Bot (HAOS)</h2>',
    '<h2 style="margin:0 0 6px;">Montagne & Paesi → Instagram Bot (HAOS)</h2>'
    f'<div class="small" style="margin-bottom:8px;">Versione <b>{APP_VERSION}</b></div>'
)
core.PAGE = core.PAGE.replace(
    '<button class="btn start" formaction="/start" formmethod="post">Avvia bot</button>',
    '<button class="btn start" formaction="/start" formmethod="post">Avvia bot</button>'
    '<button class="btn stop" formaction="/test_upload" formmethod="post" '
    'onclick="return confirm(\'Il test pubblicherà DAVVERO l’ultimo articolo del feed su Instagram una sola volta. Continuare?\')">Test pubblicazione singola</button>'
    '<button class="btn" formaction="/test_waha" formmethod="post">Test WhatsApp WAHA</button>'
)
core.PAGE = core.PAGE.replace(
    '<div class="muted" style="margin-top:10px;">\n        Hub link:',
    '<div class="hint" style="margin-top:10px;"><b>Sicurezza 1.1.3:</b> al primo errore di login/upload il bot si ferma e invia una notifica WhatsApp via WAHA.</div>'
    '<div class="muted" style="margin-top:10px;">\n        Hub link:'
)
core.PAGE = core.PAGE.replace(
    "document.getElementById('status').textContent = st.running ? 'RUNNING' : 'STOPPED';",
    "document.getElementById('status').textContent = st.blocked ? 'BLOCCATO DA INSTAGRAM' : (st.running ? 'RUNNING' : 'STOPPED');"
)


if __name__ == "__main__":
    core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
    core.log(f"📲 WAHA configurato: {DEFAULT_WAHA_URL} • sessione {DEFAULT_WAHA_SESSION}.")
    core.app.run(host="0.0.0.0", port=8080)
