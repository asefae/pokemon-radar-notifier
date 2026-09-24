"""Local Windows notifications with a graceful non-Windows/headless fallback."""

from __future__ import annotations

import logging
try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows development
    winsound = None  # type: ignore[assignment]

try:
    from winotify import Notification, audio
except ImportError:  # pragma: no cover
    Notification = audio = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)


def notify(title: str, message: str, sound: bool = True) -> bool:
    delivered = False
    if Notification is not None:
        try:
            toast = Notification(app_id="Pokémon Radar", title=title, msg=message)
            if sound and audio is not None:
                toast.set_audio(audio.Default, loop=False)
            toast.show()
            delivered = True
        except Exception:
            LOGGER.exception("Не удалось показать toast-уведомление")
    if sound:
        try:
            if winsound is None:
                raise RuntimeError("winsound доступен только в Windows")
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
    return delivered
