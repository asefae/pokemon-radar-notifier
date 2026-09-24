"""Screen capture and a safe Tk-based region selector (no game interaction)."""

from __future__ import annotations

from config_manager import Region
from pathlib import Path
import ctypes
import os
import shutil
from typing import Any

try:
    import mss
    from PIL import Image
except ImportError:  # pragma: no cover
    mss = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]


class ScreenCapture:
    def __init__(self) -> None:
        self._sct = None
        if mss is not None:
            try:
                self._sct = mss.mss()
            except Exception:
                # Keep the UI usable so the user can install/fix the capture backend.
                self._sct = None

    def grab(self, region: Region):
        if self._sct is None or Image is None:
            raise RuntimeError("Для захвата экрана установите mss и Pillow.")
        shot = self._sct.grab(region.as_mss_dict())
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def close(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass


def detect_tesseract() -> str | None:
    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    discovered = shutil.which("tesseract")
    return discovered


def validate_tesseract(command: str | None = None) -> tuple[bool, str]:
    """Validate the configured executable without hiding the subprocess error."""
    candidate = (command or "").strip() or detect_tesseract()
    if not candidate:
        return False, "Tesseract executable was not found. Install Tesseract or set its path."
    path = Path(candidate)
    if not path.is_file() and shutil.which(candidate) is None:
        return False, f"Tesseract path does not exist: {candidate}"
    try:
        import pytesseract

        old = pytesseract.pytesseract.tesseract_cmd
        pytesseract.pytesseract.tesseract_cmd = candidate
        try:
            version = str(pytesseract.get_tesseract_version()).splitlines()[0]
        finally:
            pytesseract.pytesseract.tesseract_cmd = old
        return True, f"{candidate} ({version})"
    except Exception as exc:
        return False, f"{candidate}: {exc}"


def screen_diagnostics() -> dict[str, Any]:
    """Return best-effort Windows virtual-screen and DPI information."""
    result: dict[str, Any] = {"platform": os.name}
    if os.name != "nt":
        return result
    try:
        user32 = ctypes.windll.user32
        result.update(
            virtual_left=user32.GetSystemMetrics(76),
            virtual_top=user32.GetSystemMetrics(77),
            virtual_width=user32.GetSystemMetrics(78),
            virtual_height=user32.GetSystemMetrics(79),
        )
        try:
            result["dpi"] = user32.GetDpiForSystem()
        except Exception:
            result["dpi"] = "unavailable"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def show_region_preview(parent, image) -> None:
    import tkinter as tk
    from PIL import ImageTk

    window = tk.Toplevel(parent)
    window.title("Проверка области")
    window.transient(parent)
    image.thumbnail((900, 600))
    photo = ImageTk.PhotoImage(image)
    label = tk.Label(window, image=photo)
    label.image = photo
    label.pack(padx=8, pady=8)
    tk.Button(window, text="Закрыть", command=window.destroy).pack(pady=(0, 8))


def select_regions(
    parent,
    names: list[str],
    existing: list[Region] | None = None,
) -> list[Region]:
    """Let the user draw one rectangle for every name on a desktop overlay."""
    import tkinter as tk
    from PIL import ImageGrab, ImageTk

    try:
        screenshot = ImageGrab.grab(all_screens=True)
    except Exception as exc:
        raise RuntimeError(f"Не удалось получить снимок экрана: {exc}") from exc
    window = tk.Toplevel(parent)
    window.title("Выбор областей")
    window.attributes("-fullscreen", True)
    window.attributes("-topmost", True)
    canvas = tk.Canvas(window, cursor="crosshair", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    photo = ImageTk.PhotoImage(screenshot)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    selected: list[Region] = []
    screen_info = screen_diagnostics()
    virtual_left = int(screen_info.get("virtual_left", 0) or 0)
    virtual_top = int(screen_info.get("virtual_top", 0) or 0)
    current_name = names[0] if names else "Область"
    label = canvas.create_text(20, 20, anchor="nw", fill="red", font=("Segoe UI", 16, "bold"),
                               text=f"Выделите: {current_name}")
    start: list[int] = []
    rectangle: list[int] = []

    def press(event) -> None:
        start[:] = [event.x, event.y]
        if rectangle:
            canvas.delete(rectangle.pop())
        rectangle.append(canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="red", width=2))

    def release(event) -> None:
        if not start:
            return
        x1, y1 = start
        left, top = min(x1, event.x), min(y1, event.y)
        region = Region(
            nonlocal_current[0], left + virtual_left, top + virtual_top,
            abs(event.x - x1), abs(event.y - y1)
        )
        if region.is_valid():
            selected.append(region)
            if len(selected) < len(names):
                nonlocal_current[0] = names[len(selected)]
                canvas.itemconfigure(label, text=f"Выделите: {nonlocal_current[0]}")
            else:
                window.destroy()

    nonlocal_current = [current_name]
    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<ButtonRelease-1>", release)
    window.bind("<Escape>", lambda _event: window.destroy())
    window.grab_set()
    window.wait_window()
    return selected
