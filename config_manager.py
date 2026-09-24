"""Persistent, type-safe configuration for the local Pokémon radar."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Region:
    """A named screen rectangle in physical screen coordinates."""

    name: str
    left: int
    top: int
    width: int
    height: int

    def as_mss_dict(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }

    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0

    def validation_error(self) -> str:
        if not self.name.strip():
            return "region name is empty"
        if self.width <= 0 or self.height <= 0:
            return f"invalid size {self.width}x{self.height}"
        if abs(self.left) > 100000 or abs(self.top) > 100000:
            return f"coordinates are outside a plausible desktop: {self.left},{self.top}"
        return ""


@dataclass
class MatchTarget:
    name: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    """Application settings. Values are deliberately JSON serializable."""

    regions: list[Region] = field(default_factory=list)
    targets: list[MatchTarget] = field(default_factory=list)
    threshold: int = 85
    ocr_threshold: int = 160
    cooldown_seconds: int = 60
    lost_confirmation_scans: int = 2
    scan_interval_seconds: float = 1.0
    # Capture and OCR are deliberately independent.  The old scan interval is
    # retained for config compatibility, but is no longer used by the workers.
    capture_interval_ms: int = 150
    ocr_interval_ms: int = 300
    preprocessing_mode: str = "balanced"
    debug_frames: bool = False
    debug_directory: str = "debug_frames"
    pipeline_diagnostics: bool = False
    tesseract_cmd: str = ""
    language: str = "eng"
    telegram_enabled: bool = False
    telegram_token: str = ""
    telegram_chat_id: str = ""
    sound_enabled: bool = True
    windows_notifications: bool = True
    telegram_send_screenshots: bool = False


def default_config_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("config.json")
    return Path(__file__).resolve().with_name("config.json")


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def config_from_dict(data: dict[str, Any]) -> AppConfig:
    regions: list[Region] = []
    for raw in data.get("regions", []):
        if not isinstance(raw, dict):
            continue
        try:
            region = Region(
                name=str(raw.get("name", "Область")),
                left=_int(raw.get("left"), 0),
                top=_int(raw.get("top"), 0),
                width=_int(raw.get("width"), 0),
                height=_int(raw.get("height"), 0),
            )
        except (TypeError, ValueError):
            continue
        if region.is_valid():
            regions.append(region)

    targets: list[MatchTarget] = []
    raw_targets = data.get("targets", [])
    if isinstance(raw_targets, dict):
        raw_targets = [{"name": key, "aliases": value} for key, value in raw_targets.items()]
    for raw in raw_targets:
        if isinstance(raw, str):
            targets.append(MatchTarget(raw))
        elif isinstance(raw, dict) and raw.get("name"):
            aliases = raw.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [aliases]
            targets.append(MatchTarget(str(raw["name"]), [str(x) for x in aliases]))

    mode = str(data.get("preprocessing_mode", "balanced")).casefold()
    if mode not in {"fast", "balanced", "accurate"}:
        mode = "balanced"
    return AppConfig(
        regions=regions,
        targets=targets,
        threshold=max(0, min(100, _int(data.get("threshold"), 85))),
        ocr_threshold=max(0, min(255, _int(data.get("ocr_threshold"), 160))),
        cooldown_seconds=max(0, _int(data.get("cooldown_seconds"), 60)),
        lost_confirmation_scans=max(1, _int(data.get("lost_confirmation_scans"), 2)),
        scan_interval_seconds=max(0.5, min(10.0, _float(data.get("scan_interval_seconds"), 1.0))),
        capture_interval_ms=max(100, min(1000, _int(data.get("capture_interval_ms"), 150))),
        ocr_interval_ms=max(200, min(2000, _int(data.get("ocr_interval_ms"), 300))),
        preprocessing_mode=mode,
        debug_frames=bool(data.get("debug_frames", False)),
        debug_directory=str(data.get("debug_directory", "debug_frames")),
        pipeline_diagnostics=bool(data.get("pipeline_diagnostics", False)),
        tesseract_cmd=str(data.get("tesseract_cmd", "")),
        language=str(data.get("language", "eng")),
        telegram_enabled=bool(data.get("telegram_enabled", False)),
        telegram_token=str(data.get("telegram_token", "")),
        telegram_chat_id=str(data.get("telegram_chat_id", "")),
        sound_enabled=bool(data.get("sound_enabled", True)),
        windows_notifications=bool(data.get("windows_notifications", True)),
        telegram_send_screenshots=bool(data.get("telegram_send_screenshots", False)),
    )


def load_config(path: Path | str | None = None) -> AppConfig:
    config_path = Path(path) if path else default_config_path()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return AppConfig()
    return config_from_dict(data if isinstance(data, dict) else {})


def save_config(config: AppConfig, path: Path | str | None = None) -> Path:
    config_path = Path(path) if path else default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path
