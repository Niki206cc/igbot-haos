"""Safety wrapper for Montagne & Paesi Instagram bot.

Version 1.1.0
- Stops the bot automatically on Instagram integrity/upload restrictions.
- Shows a clear BLOCKED status in the web UI.
- Exposes the application version in the web UI, /status and /metrics.

The original app.py remains untouched so this patch is easy to review or revert.
"""

import app as core

APP_VERSION = "1.1.0"


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
                # IMPORTANT: stop immediately so the 60-second loop cannot
                # attempt another upload after the current cycle.
                core.set_metric("last_error", f"Instagram integrity restriction: {exc}")
                core.set_metric("running", False)
                core.stop_event.set()
                core.log("🚨 Instagram ha bloccato gli upload automatici (integrity restriction).")
                core.log("⏹️ Bot fermato automaticamente: nessun altro tentativo verrà eseguito.")
            raise


# bot_loop resolves InstagramPoster from the app module globals at runtime.
core.InstagramPoster = SafeInstagramPoster


# -----------------------------------------------------------------------------
# Status endpoint: show BLOCKED immediately even while the worker thread is
# finishing its current sleep/cleanup.
# -----------------------------------------------------------------------------
def safe_status():
    with core.bot_lock:
        thread_alive = core.bot_thread is not None and core.bot_thread.is_alive()

    with core.metrics_lock:
        last_error = str(core.metrics.get("last_error", ""))

    blocked = (
        "integrity" in last_error.lower()
        or "restricted from uploading" in last_error.lower()
    )

    return core.jsonify({
        "running": bool(thread_alive and not blocked),
        "blocked": blocked,
        "version": APP_VERSION,
    })


core.app.view_functions["status"] = safe_status


# -----------------------------------------------------------------------------
# Web UI version + blocked state
# -----------------------------------------------------------------------------
core.PAGE = core.PAGE.replace(
    '<h2 style="margin:0 0 6px;">Montagne & Paesi → Instagram Bot (HAOS)</h2>',
    '<h2 style="margin:0 0 6px;">Montagne & Paesi → Instagram Bot (HAOS)</h2>'
    f'<div class="small" style="margin-bottom:8px;">Versione <b>{APP_VERSION}</b></div>'
)

core.PAGE = core.PAGE.replace(
    "document.getElementById('status').textContent = st.running ? 'RUNNING' : 'STOPPED';",
    "document.getElementById('status').textContent = st.blocked ? 'BLOCCATO DA INSTAGRAM' : (st.running ? 'RUNNING' : 'STOPPED');"
)


# -----------------------------------------------------------------------------
# Run Flask app
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    core.log(f"🟢 Web UI pronta. Versione {APP_VERSION}.")
    core.app.run(host="0.0.0.0", port=8080)
