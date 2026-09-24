"""State machine for appearance, disappearance and notification decisions."""

from __future__ import annotations

import time
from dataclasses import dataclass

from matching import Match, normalize_text


@dataclass
class DetectionState:
    name: str
    last_seen: float
    last_notification: float | None = None
    missing_scans: int = 0
    area: str = ""


@dataclass(frozen=True)
class DetectionEvent:
    kind: str
    name: str
    reason: str
    score: float = 0.0
    area: str = ""


class DetectionTracker:
    """Tracks normalized target names across complete OCR scans."""

    def __init__(self, lost_confirmation_scans: int = 2, cooldown_seconds: int = 60) -> None:
        self.lost_confirmation_scans = max(1, lost_confirmation_scans)
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.active_detections: dict[str, DetectionState] = {}
        self.lost_detections: list[tuple[str, float]] = []

    def update(self, matches: list[Match], now: float | None = None) -> list[DetectionEvent]:
        current_time = time.monotonic() if now is None else now
        detected: dict[str, Match] = {}
        for match in matches:
            key = normalize_text(match.target)
            if key and (key not in detected or match.score > detected[key].score):
                detected[key] = match

        events: list[DetectionEvent] = []
        for key, state in list(self.active_detections.items()):
            if key in detected:
                state.missing_scans = 0
                state.last_seen = current_time
                state.area = detected[key].area
            else:
                state.missing_scans += 1
                if state.missing_scans >= self.lost_confirmation_scans:
                    self.active_detections.pop(key)
                    self.lost_detections.append((state.name, current_time))
                    events.append(DetectionEvent("lost", state.name, "lost"))

        for key, match in detected.items():
            state = self.active_detections.get(key)
            if state is None:
                state = DetectionState(match.target, current_time, area=match.area)
                self.active_detections[key] = state
                # A new appearance is a new event; cooldown must not suppress it.
                state.last_notification = current_time
                events.append(DetectionEvent("notify", match.target, "new", match.score, match.area))
            else:
                reason = "active"
                if state.last_notification is not None and (
                    current_time - state.last_notification < self.cooldown_seconds
                ):
                    reason = "cooldown"
                events.append(DetectionEvent("skip", state.name, reason, match.score, match.area))

        return events
