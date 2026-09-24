"""Two-stage, latest-frame OCR pipeline.

Capture and recognition intentionally have separate workers.  Capture never
waits for Tesseract: a one-item latest-value queue replaces stale frames,
which keeps latency bounded when OCR is slow.  No worker calls Tk directly.
"""

from __future__ import annotations

import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import pytesseract
    from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageStat
except ImportError:  # pragma: no cover
    pytesseract = None  # type: ignore[assignment]
    Image = ImageChops = ImageEnhance = ImageFilter = ImageOps = ImageStat = None  # type: ignore[assignment]

from config_manager import Region
from screen_capture import ScreenCapture, detect_tesseract, validate_tesseract


@dataclass
class CapturedRegion:
    region: Region
    image: Any = None
    error: str = ""
    validation: str = ""
    image_size: tuple[int, int] | None = None
    mean_pixel: float | None = None


@dataclass
class CapturedFrame:
    frame_id: int
    regions: list[CapturedRegion]
    capture_started: float
    capture_finished: float


class LatestFrameQueue:
    """Thread-safe queue with capacity one and explicit replacement metrics."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._value: CapturedFrame | None = None
        self.replacements = 0
        self.puts = 0
        self.gets = 0

    def put(self, value: CapturedFrame) -> None:
        with self._condition:
            if self._value is not None:
                self.replacements += 1
            self._value = value
            self.puts += 1
            self._condition.notify()

    def put_nowait(self, value: CapturedFrame) -> None:
        self.put(value)

    def get(self, timeout: float | None = None) -> CapturedFrame:
        with self._condition:
            if self._value is None and not self._condition.wait(timeout):
                raise queue.Empty
            if self._value is None:
                raise queue.Empty
            value, self._value = self._value, None
            self.gets += 1
            return value

    def get_nowait(self) -> CapturedFrame:
        return self.get(timeout=0)

    def qsize(self) -> int:
        with self._condition:
            return int(self._value is not None)

    def empty(self) -> bool:
        return self.qsize() == 0

    @property
    def maxsize(self) -> int:
        return 1

    def clear(self) -> None:
        with self._condition:
            self._value = None


@dataclass
class CaptureStats:
    captured: int = 0
    replacements: int = 0
    queue_size: int = 0
    last_capture_started: float = 0.0
    last_capture_finished: float = 0.0
    capture_interval_average: float = 0.0
    valid_frames: int = 0
    capture_errors: int = 0
    first_valid_frame_id: int = 0
    last_frame_id: int = 0
    last_error: str = ""
    last_regions: list[dict[str, Any]] = field(default_factory=list)


class CaptureWorker:
    """Periodically capture all configured regions into a latest-frame queue."""

    def __init__(
        self,
        capture: ScreenCapture,
        regions: list[Region],
        interval_ms: int = 150,
        frame_queue: LatestFrameQueue | None = None,
    ) -> None:
        self.capture, self.regions = capture, list(regions)
        self.interval_ms = max(100, min(1000, int(interval_ms)))
        self.frame_queue = frame_queue or LatestFrameQueue()
        self._frame_queue = self.frame_queue
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = CaptureStats()
        self.ready = threading.Event()
        self._capture_intervals: list[float] = []
        self._previous_capture_started = 0.0

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="screen-capture", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        if thread is None or not thread.is_alive():
            self._thread = None

    def _run(self) -> None:
        frame_id = 0
        while not self._stop.is_set():
            started = time.perf_counter()
            captured: list[CapturedRegion] = []
            for region in self.regions:
                if self._stop.is_set():
                    break
                try:
                    validation = region.validation_error()
                    if validation:
                        try:
                            raise ValueError(validation)
                        except ValueError:
                            details = traceback.format_exc()
                        captured.append(CapturedRegion(region, error=details, validation=validation))
                        self.stats.capture_errors += 1
                        continue
                    image = self.capture.grab(region)
                    image_size = getattr(image, "size", None)
                    if not image_size or image_size[0] <= 0 or image_size[1] <= 0:
                        raise RuntimeError(f"capture returned an invalid image size: {image_size!r}")
                    mean_pixel = None
                    if ImageStat is not None:
                        try:
                            means = ImageStat.Stat(image).mean
                            mean_pixel = round(sum(means) / len(means), 2)
                        except Exception:
                            mean_pixel = None
                    mismatch = ""
                    if tuple(image_size) != (region.width, region.height):
                        mismatch = f"image size {tuple(image_size)} differs from requested {region.width}x{region.height}"
                    captured.append(CapturedRegion(
                        region, image=image, validation=mismatch,
                        image_size=tuple(image_size), mean_pixel=mean_pixel,
                    ))
                except Exception as exc:
                    details = traceback.format_exc()
                    captured.append(CapturedRegion(region, error=details, validation="capture failed"))
                    self.stats.capture_errors += 1
                    self.stats.last_error = details
            finished = time.perf_counter()
            if captured and not self._stop.is_set():
                frame_id += 1
                self.frame_queue.put(CapturedFrame(frame_id, captured, started, finished))
                self.stats.captured += 1
                valid = [item for item in captured if item.image is not None and not item.error]
                self.stats.valid_frames += int(bool(valid))
                if valid and not self.stats.first_valid_frame_id:
                    self.stats.first_valid_frame_id = frame_id
                    self.ready.set()
                self.stats.last_frame_id = frame_id
                self.stats.last_regions = [
                    {
                        "name": item.region.name,
                        "coords": item.region.as_mss_dict(),
                        "image_size": item.image_size,
                        "mean_pixel": item.mean_pixel,
                        "validation": item.validation,
                        "error": item.error,
                    }
                    for item in captured
                ]
                self.stats.replacements = self.frame_queue.replacements
                self.stats.queue_size = self.frame_queue.qsize()
                self.stats.last_capture_started = started
                self.stats.last_capture_finished = finished
                if self._previous_capture_started:
                    self._capture_intervals.append(started - self._previous_capture_started)
                    self._capture_intervals = self._capture_intervals[-100:]
                    self.stats.capture_interval_average = sum(self._capture_intervals) / len(self._capture_intervals)
                self._previous_capture_started = started
            wait_for = max(0.0, self.interval_ms / 1000.0 - (finished - started))
            self._stop.wait(wait_for)


@dataclass
class OCRResult:
    region: Region
    text: str
    error: str = ""
    cycle_id: int = 0
    lines: list[str] = field(default_factory=list)
    variant_names: list[str] = field(default_factory=list)
    ocr_started: float = 0.0
    ocr_finished: float = 0.0


@dataclass
class OCRScan:
    frame_id: int
    results: list[OCRResult]
    capture_started: float
    ocr_started: float
    ocr_finished: float
    notification_sent: float | None = None


@dataclass
class OCRStats:
    processed: int = 0
    replaced_frames: int = 0
    queue_size: int = 0
    ocr_latency_average: float = 0.0
    last_capture_started: float = 0.0
    last_ocr_started: float = 0.0
    last_ocr_finished: float = 0.0
    errors: int = 0
    last_error: str = ""
    last_frame_id: int = 0
    last_text_length: int = 0
    first_valid_frame_id: int = 0


def _adaptive_threshold(image: Any, threshold: int, inverted: bool = False) -> Any:
    """Pillow-only adaptive threshold, avoiding a new OpenCV dependency."""
    if ImageFilter is None or ImageChops is None:
        return image
    background = image.filter(ImageFilter.GaussianBlur(radius=9))
    # Difference from local background; the offset keeps text visible on
    # lightly varying backgrounds.
    local = ImageChops.subtract(image, background, scale=1, offset=128)
    result = local.point(lambda pixel: 255 if pixel >= max(1, threshold - 128) else 0)
    return ImageOps.invert(result) if inverted and ImageOps is not None else result


def preprocess_variants(image: Any, mode: str = "balanced", threshold: int = 160) -> dict[str, Any]:
    """Create raw grayscale, upscaled and thresholded variants.

    Keeping the unthresholded variants is important for light/outlined game
    fonts: thresholding every fast-mode image was a regression that could make
    Tesseract see a blank frame.
    """
    if ImageOps is None or ImageEnhance is None:
        raise RuntimeError("Установите Pillow для предварительной обработки OCR.")
    mode = mode.casefold()
    scale = 4 if mode == "accurate" else 3
    contrast = 2.25 if mode == "accurate" else (1.8 if mode == "balanced" else 1.25)
    raw_gray = ImageOps.grayscale(image)
    gray = raw_gray.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
    gray = ImageEnhance.Contrast(gray).enhance(contrast)
    normal = gray.point(lambda pixel: 255 if pixel >= threshold else 0)
    inverted = ImageOps.invert(normal)
    variants = {"raw_gray": raw_gray, "upscaled": gray, "normal": normal, "inverted": inverted}
    if mode == "fast":
        return variants
    adaptive = _adaptive_threshold(gray, threshold)
    variants.update({"adaptive": adaptive, "adaptive_inverted": ImageOps.invert(adaptive)})
    return variants


def _safe_filename(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip() or "region"


class OCRWorker:
    """Single, non-overlapping OCR consumer for the capture queue."""

    def __init__(
        self,
        capture: ScreenCapture | None,
        regions: list[Region] | None,
        language: str = "rus+eng",
        tesseract_cmd: str = "",
        interval: float = 0.3,
        threshold: int = 160,
        callback: Callable[[OCRResult], None] | None = None,
        *,
        frame_queue: LatestFrameQueue | None = None,
        capture_interval_ms: int = 150,
        ocr_interval_ms: int | None = None,
        preprocessing_mode: str = "balanced",
        debug_directory: str | Path = "debug_frames",
        save_debug: bool = False,
    ) -> None:
        self.capture, self.regions = capture, list(regions or [])
        self.language, self.threshold = language, threshold
        self.callback = callback
        self.capture_interval_ms = max(100, min(1000, int(capture_interval_ms)))
        # ``interval`` is retained for callers of the original API.
        self.ocr_interval_ms = max(200, min(2000, int(ocr_interval_ms if ocr_interval_ms is not None else interval * 1000)))
        self.preprocessing_mode = preprocessing_mode if preprocessing_mode in {"fast", "balanced", "accurate"} else "balanced"
        self.debug_directory = Path(debug_directory)
        self.save_debug = save_debug
        self.frame_queue = frame_queue or LatestFrameQueue()
        self._frame_queue = self.frame_queue
        self.capture_worker: CaptureWorker | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._results: queue.Queue[OCRScan] = queue.Queue(maxsize=1)
        self._queue = self._results  # compatibility for integrations that inspect the queue
        self.stats = OCRStats()
        self._latencies: list[float] = []
        self._latest_images: dict[str, Any] = {}
        self._latest_images_lock = threading.Lock()
        self._callback_results: queue.Queue[OCRResult] = queue.Queue()
        resolved_tesseract = (tesseract_cmd or "").strip() or detect_tesseract()
        self.tesseract_status = validate_tesseract(resolved_tesseract)
        if resolved_tesseract and pytesseract:
            pytesseract.pytesseract.tesseract_cmd = resolved_tesseract

    @property
    def queue(self) -> LatestFrameQueue:
        return self.frame_queue

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    @property
    def first_valid_frame_ready(self) -> bool:
        return bool(self.capture_worker and self.capture_worker.ready.is_set())

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        if self.capture is not None and self.capture_worker is None:
            self.capture_worker = CaptureWorker(
                self.capture, self.regions, self.capture_interval_ms, frame_queue=self.frame_queue
            )
            self.capture_worker.start()
        self._thread = threading.Thread(target=self._run, name="ocr-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self.capture_worker:
            self.capture_worker.stop(timeout)
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        if thread is None or not thread.is_alive():
            self._thread = None
            self.capture_worker = None

    def _recognize(self, region: Region, image: Any = None, error: str = "", cycle_id: int = 0) -> OCRResult:
        started = time.perf_counter()
        if error:
            return OCRResult(region, "", error, cycle_id, ocr_started=started, ocr_finished=time.perf_counter())
        try:
            if pytesseract is None or ImageOps is None or ImageEnhance is None:
                raise RuntimeError("Установите pytesseract и Pillow.")
            if image is None:
                if self.capture is None:
                    raise RuntimeError("Нет источника захвата.")
                image = self.capture.grab(region)
            with self._latest_images_lock:
                self._latest_images[region.name] = image.copy()
            variants = preprocess_variants(image, self.preprocessing_mode, self.threshold)
            if self.save_debug:
                self.debug_directory.mkdir(parents=True, exist_ok=True)
                image.save(
                    self.debug_directory / f"cycle-{cycle_id}-{_safe_filename(region.name)}-raw.png"
                )
            lines: list[str] = []
            names: list[str] = []
            for variant_name, processed in variants.items():
                if self.save_debug:
                    self.debug_directory.mkdir(parents=True, exist_ok=True)
                    processed.save(
                        self.debug_directory
                        / f"cycle-{cycle_id}-{_safe_filename(region.name)}-{variant_name}.png"
                    )
                    if variant_name == "normal":
                        processed.save(
                            self.debug_directory
                            / f"cycle-{cycle_id}-{_safe_filename(region.name)}-threshold.png"
                        )
                for psm in (6, 11):
                    config = f"--psm {psm}"
                    try:
                        value = pytesseract.image_to_string(processed, lang=self.language, config=config)
                    except Exception as primary_error:
                        if self.language.casefold() == "eng":
                            raise
                        # A previous config may request rus+eng although only
                        # the default English traineddata is installed.
                        try:
                            value = pytesseract.image_to_string(processed, lang="eng", config=config)
                        except Exception:
                            raise primary_error
                    for line in value.splitlines():
                        line = line.strip()
                        if line and line not in lines:
                            lines.append(line)
                    if value.strip():
                        names.append(f"{variant_name}/psm{psm}")
            finished = time.perf_counter()
            return OCRResult(region, "\n".join(lines), "", cycle_id, lines, names, started, finished)
        except Exception as exc:
            details = traceback.format_exc()
            return OCRResult(region, "", details, cycle_id, ocr_started=started, ocr_finished=time.perf_counter())

    def _remember_frame(self, frame: CapturedFrame) -> None:
        with self._latest_images_lock:
            for item in frame.regions:
                if item.image is not None:
                    try:
                        self._latest_images[item.region.name] = item.image.copy()
                    except Exception:
                        self._latest_images[item.region.name] = item.image

    def _publish(self, scan: OCRScan) -> None:
        try:
            self._results.put_nowait(scan)
        except queue.Full:
            try:
                self._results.get_nowait()
            except queue.Empty:
                pass
            self._results.put_nowait(scan)

    def _run(self) -> None:
        last_started = 0.0
        while not self._stop.is_set():
            try:
                frame = self.frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            wait_for = self.ocr_interval_ms / 1000.0 - (time.perf_counter() - last_started)
            if last_started and wait_for > 0 and self._stop.wait(wait_for):
                break
            started = time.perf_counter()
            last_started = started
            self._remember_frame(frame)
            results = [self._recognize(item.region, item.image, item.error, frame.frame_id)
                       for item in frame.regions]
            finished = time.perf_counter()
            self.stats.processed += 1
            self.stats.replaced_frames = self.frame_queue.replacements
            self.stats.queue_size = self.frame_queue.qsize()
            self.stats.last_capture_started = frame.capture_started
            self.stats.last_ocr_started = started
            self.stats.last_ocr_finished = finished
            self.stats.last_frame_id = frame.frame_id
            self.stats.first_valid_frame_id = (
                self.capture_worker.stats.first_valid_frame_id if self.capture_worker else
                self.stats.first_valid_frame_id
            )
            errors = [result for result in results if getattr(result, "error", "")]
            self.stats.errors += len(errors)
            if errors:
                self.stats.last_error = getattr(errors[-1], "error", "")
            self.stats.last_text_length = sum(len(getattr(result, "text", result if isinstance(result, str) else "")) for result in results)
            latency = finished - started
            self._latencies.append(latency)
            self._latencies = self._latencies[-100:]
            self.stats.ocr_latency_average = sum(self._latencies) / len(self._latencies)
            scan = OCRScan(frame.frame_id, results, frame.capture_started, started, finished)
            self._publish(scan)
            for result in results:
                if self.callback:
                    # Callbacks are drained by the Tk thread, never here.
                    self._callback_results.put(result)

    def drain_scans(self) -> list[OCRScan]:
        values: list[OCRScan] = []
        while True:
            try:
                values.append(self._results.get_nowait())
            except queue.Empty:
                return values

    def drain_callbacks(self) -> int:
        """Invoke compatibility callbacks on the caller's thread."""
        count = 0
        while True:
            try:
                result = self._callback_results.get_nowait()
            except queue.Empty:
                return count
            if self.callback:
                self.callback(result)
            count += 1

    def diagnostics(self) -> dict[str, Any]:
        capture_stats = self.capture_worker.stats if self.capture_worker else None
        return {
            "tesseract": self.tesseract_status[1],
            "queue_size": self.frame_queue.qsize(),
            "queue_puts": self.frame_queue.puts,
            "queue_gets": self.frame_queue.gets,
            "queue_replacements": self.frame_queue.replacements,
            "processed": self.stats.processed,
            "errors": self.stats.errors,
            "last_frame_id": self.stats.last_frame_id,
            "last_text_length": self.stats.last_text_length,
            "first_valid_frame_id": self.stats.first_valid_frame_id,
            "first_valid_frame_ready": self.first_valid_frame_ready,
            "capture": capture_stats,
        }

    def run_diagnostics(self, directory: str | Path) -> list[OCRResult]:
        """Save current raw/processed frames and OCR them synchronously."""
        self.save_latest_variants(directory)
        return self.recognize_latest_frames()

    def save_latest_frame(self, directory: str | Path) -> list[Path]:
        """Save the most recent raw frame for each region on explicit user request."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        with self._latest_images_lock:
            latest_images = list(self._latest_images.items())
        for name, image in latest_images:
            path = target / f"latest-{_safe_filename(name)}.png"
            image.save(path)
            saved.append(path)
        return saved

    def save_latest_variants(self, directory: str | Path) -> list[Path]:
        """Save raw, grayscale/upscaled and thresholded images for inspection."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        with self._latest_images_lock:
            latest_images = list(self._latest_images.items())
        for name, image in latest_images:
            raw_path = target / f"latest-{_safe_filename(name)}-raw.png"
            image.save(raw_path)
            saved.append(raw_path)
            for variant_name, variant in preprocess_variants(image, self.preprocessing_mode, self.threshold).items():
                path = target / f"latest-{_safe_filename(name)}-{variant_name}.png"
                variant.save(path)
                saved.append(path)
                if variant_name == "normal":
                    threshold_path = target / f"latest-{_safe_filename(name)}-threshold.png"
                    variant.save(threshold_path)
                    saved.append(threshold_path)
        return saved

    def recognize_saved_frame(self, path: str | Path, region_name: str = "saved") -> OCRResult:
        """Run the same OCR pipeline on a user-selected saved image."""
        if Image is None:
            raise RuntimeError("Pillow is required to open a saved frame.")
        image = Image.open(path).convert("RGB")
        region = Region(region_name, 0, 0, image.width, image.height)
        return self._recognize(region, image, cycle_id=self.stats.last_frame_id + 1)

    def recognize_latest_frames(self) -> list[OCRResult]:
        with self._latest_images_lock:
            latest_images = list(self._latest_images.items())
        return [
            self._recognize(
                Region(name, 0, 0, image.width, image.height),
                image,
                cycle_id=self.stats.last_frame_id + 1,
            )
            for name, image in latest_images
        ]

    def drain(self) -> list[OCRResult]:
        """Compatibility API returning the newest scan's individual results."""
        return [result for scan in self.drain_scans() for result in scan.results]


class SimpleOCRWorker:
    """Temporary reliable OCR path: one capture and one OCR thread."""

    def __init__(self, capture: ScreenCapture, regions: list[Region], language: str = "eng",
                 tesseract_cmd: str = "", interval_ms: int = 1000,
                 debug_path: str | Path = "debug_raw.png") -> None:
        self.capture = capture
        self.regions = list(regions)
        self.language = language or "eng"
        self.interval_ms = max(1000, int(interval_ms))
        self.debug_path = Path(debug_path)
        self._stop = threading.Event()
        self._test_now = threading.Event()
        self._thread: threading.Thread | None = None
        self._results: queue.Queue[OCRResult] = queue.Queue()
        self.last_error = ""
        if tesseract_cmd and pytesseract:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="simple-ocr", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._test_now.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None

    def request_test(self) -> None:
        self._test_now.set()

    def _ocr(self, region: Region) -> OCRResult:
        started = time.perf_counter()
        try:
            if pytesseract is None or ImageOps is None or ImageEnhance is None:
                raise RuntimeError("Установите Pillow и pytesseract.")
            image = self.capture.grab(region)
            self.debug_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(self.debug_path)
            gray = ImageOps.grayscale(image)
            scaled = gray.resize((gray.width * 3, gray.height * 3), Image.Resampling.LANCZOS)
            enhanced = ImageEnhance.Contrast(scaled).enhance(1.8)
            value = pytesseract.image_to_string(
                enhanced, lang=self.language, config="--psm 11"
            )
            return OCRResult(
                region, value, "", 0,
                [line.strip() for line in value.splitlines() if line.strip()],
                ["simple/grayscale/3x/contrast/psm11"],
                started, time.perf_counter(),
            )
        except Exception:
            error = traceback.format_exc()
            self.last_error = error
            return OCRResult(region, "", error, 0, [], [], started, time.perf_counter())

    def _run(self) -> None:
        while not self._stop.is_set():
            test_requested = self._test_now.is_set()
            self._test_now.clear()
            for region in self.regions:
                if self._stop.is_set():
                    break
                self._results.put(self._ocr(region))
            if test_requested:
                continue
            self._stop.wait(self.interval_ms / 1000)

    def drain(self) -> list[OCRResult]:
        results: list[OCRResult] = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except queue.Empty:
                return results
