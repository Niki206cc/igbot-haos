"""Safety wrapper for Montagne & Paesi Instagram bot.

Version 1.1.2
- Treats the known post-upload /qe/expose/ 404 as a secondary API error.
- Prevents duplicate retries when Instagram has already accepted the post.
- Keeps the one-shot Instagram publishing test.
- Stops automatically on Instagram integrity/upload restrictions.
- Shows a clear BLOCKED status in the web UI.
- Exposes the application version in the web UI, /status and /metrics.

The original app.py remains untouched so this patch is easy to review or revert.
"""

from datetime import datetime

import app as core

APP_VERSION = "1.1.2"


# -----------------------------------------------------------------------------
# Metrics / version
# -----------------------------------------------------------------------------
core.metrics["version"] = APP_VERSION


def _is_integrity_restriction(exc: Exception) -> bool:
    """Return True for Instagram upload restrictions that must not be retried."""
    text = str(exc).lower()
    return (
        "restricted from uploading" in text
        or ("error_domain" in text and "integrity" in text)
        or "integrity restriction" in text
    )


def _is_qe_expose_404(exc: Exception) -> bool:
    """Known instagrapi/Instagram secondary call that may fail after upload succeeds."""
    text = str(exc).lower()
    return "404" in text and "/api/v1/qe/expose/" in text


class PostUploadedWithSecondaryApiError(Exception):
    """The upload appears accepted, but instagrapi failed on a secondary API call."""


# -----------------------------------------------------------------------------
# Safe InstagramPoster
# -----------------------------------------------------------------------------
OriginalInstagramPoster = core.InstagramPoster


class SafeInstagramPoster(OriginalInstagramPoster):
    def post_photo(self, image_path: str, caption: str):
        try:
            return super().post_photo(image_path, caption)
        except Exception as exc:
            if _is_integrity_restriction(exc):
                core.set_metric("last_error", f"Instagram integrity restriction: {exc}")
                core.set_metric("running", False)
                core.stop_event.set()
                core.log("🚨 Instagram ha bloccato gli upload automatici (integrity restriction).")
                core.log("⏹️ Bot fermato automaticamente: nessun altro tentativo verrà eseguito.")
                raise

            if _is_qe_expose_404(exc):
                # Observed behaviour: Instagram publishes the photo and then
                # instagrapi fails on this secondary endpoint. Never retry the
                # upload automatically, otherwise the same post can be duplicated.
                core.log("⚠️ Upload inviato; errore API secondario /qe/expose/ 404 dopo la pubblicazione.")
                raise PostUploadedWithSecondaryApiError(str(exc)) from exc

            raise


core.InstagramPoster = SafeInstagramPoster


# -----------------------------------------------------------------------------
# Normal bot loop: protect against duplicate uploads on qe/expose 404.
# -----------------------------------------------------------------------------
def safe_bot_loop(username: str, password: str, rss_url: str):
    poster = SafeInstagramPoster()
    core.set_metric("running", True)
    core.set_metric("last_error", "")

    try:
        poster.login_for_posting(username, password)
    except core.TwoFactorRequired:
        core.log("❌ Instagram richiede 2FA: login dall'app e riprova.")
        core.set_metric("last_error", "2FA required")
        core.set_metric("running", False)
        return
    except core.ChallengeRequired:
        core.log("❌ Instagram richiede Challenge. Apri Instagram sul telefono e conferma la sicurezza.")
        core.set_metric("last_error", "Challenge required")
        core.set_metric("running", False)
        return
    except Exception as exc:
        core.log(f"❌ Errore login (posting): {exc}")
        core.set_metric("last_error", str(exc))
        core.set_metric("running", False)
        return

    core.os.makedirs(core.IMAGES_DIR, exist_ok=True)

    while not core.stop_event.is_set():
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
                core.log("❌ Immagine in evidenza non trovata (og:image).")
                core.stop_event.wait(core.CHECK_INTERVAL)
                continue

            excerpt = core.get_article_excerpt(link, max_chars=900)
            if not excerpt:
                excerpt = core.get_excerpt_from_feed_entry(entry, max_chars=900)
            core.log(f"📝 Testo estratto: {len(excerpt)} caratteri")

            tags = core.hashtags_from_title(title)
            caption = f"""{title}\n\n{excerpt}\n\n{tags}\n\n👉 {core.HUB_LINK}"""
            caption = core.clamp_caption(caption, 2200)

            if not core.download_image(img_url, core.LATEST_IMG_PATH):
                core.log("❌ Download immagine fallito.")
                core.stop_event.wait(core.CHECK_INTERVAL)
                continue

            core.log(f"📸 Pubblico: {title}")
            core.log("📤 Carico il post su Instagram...")

            published = False
            secondary_error = False
            try:
                poster.post_photo(core.LATEST_IMG_PATH, caption)
                published = True
            except PostUploadedWithSecondaryApiError:
                # The post was observed online despite this endpoint error.
                # Mark the RSS item as handled and NEVER retry it automatically.
                published = True
                secondary_error = True
            except Exception as exc:
                if core.is_csrf_error(exc) or ("login_required" in str(exc).lower()):
                    core.log("⚠️ Errore durante upload (CSRF/login_required): resetto sessione e riprovo 1 volta...")
                    core.delete_ig_settings()
                    poster = SafeInstagramPoster()
                    poster.login_for_posting(username, password)
                    try:
                        poster.post_photo(core.LATEST_IMG_PATH, caption)
                        published = True
                    except PostUploadedWithSecondaryApiError:
                        published = True
                        secondary_error = True
                else:
                    raise

            if published:
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
            core.log(f"❌ Errore ciclo: {exc}")
            core.set_metric("last_error", str(exc))

        core.stop_event.wait(core.CHECK_INTERVAL)

    core.log("⏹️ Bot fermato.")
    core.set_metric("running", False)


core.bot_loop = safe_bot_loop


# -----------------------------------------------------------------------------
# Status endpoint
# -----------------------------------------------------------------------------
def safe_status():
    with core.bot_lock:
        thread_alive = core.bot_thread is not None and core.bot_thread.is_alive()
    with core.metrics_lock:
        last_error = str(core.metrics.get("last_error", ""))
    blocked = "integrity" in last_error.lower() or "restricted from uploading" in last_error.lower()
    return core.jsonify({"running": bool(thread_alive and not blocked), "blocked": blocked, "version": APP_VERSION})


core.app.view_functions["status"] = safe_status


# -----------------------------------------------------------------------------
# One-shot publishing test
# -----------------------------------------------------------------------------
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

    link = ""
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
        caption = core.clamp_caption(f"""{title}\n\n{excerpt}\n\n{tags}\n\n👉 {core.HUB_LINK}""", 2200)

        if not core.download_image(img_url, core.LATEST_IMG_PATH):
            raise RuntimeError("Download immagine fallito.")

        core.log(f"🧪 Test: pubblico una sola volta: {title}")
        try:
            poster.post_photo(core.LATEST_IMG_PATH, caption)
            secondary_error = False
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
        core.log("⏹️ Il bot resta STOPPED. Premi Avvia bot solo quando vuoi riattivare l'automazione.")

    except Exception as exc:
        if not _is_integrity_restriction(exc):
            core.set_metric("last_error", str(exc))
            core.set_metric("running", False)
            core.log(f"❌ Test pubblicazione fallito: {exc}")
        else:
            core.log("🧪 Test terminato: Instagram mantiene il blocco sugli upload automatici.")

    return core.redirect("/")


# -----------------------------------------------------------------------------
# Web UI
# -----------------------------------------------------------------------------
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
)
core.PAGE = core.PAGE.replace(
    '<div class="muted" style="margin-top:10px;">\n        Hub link:',
    '<div class="hint" style="margin-top:10px;"><b>Test pubblicazione singola:</b> pubblica realmente l’ultimo articolo del feed una sola volta e non avvia il ciclo automatico.</div>'
    '<div class="muted" style="margin-top:10px;">\n        Hub link:'
)
core.PAGE = core.PAGE.replace(
    "document.getElementById('status').textContent = st.running ? 'RUNNING' : 'STOPPED';",
    "document.getElementById('status').textContent = st.blocked ? 'BLOCCATO DA INSTAGRAM' : (st.running ? 'RUNNING' : 'STOPPED');"
)


if __name__ == "__main__":
    core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
    core.app.run(host="0.0.0.0", port=8080)
