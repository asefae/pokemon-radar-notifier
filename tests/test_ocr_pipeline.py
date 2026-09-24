import threading
import time
import unittest
import os
from unittest import mock

try:
    from PIL import Image
except ImportError:  # pragma: no cover - dependency-availability test path
    Image = None

from config_manager import Region
import ocr_engine
from ocr_engine import (
    CapturedFrame,
    CapturedRegion,
    CaptureWorker,
    LatestFrameQueue,
    OCRWorker,
    preprocess_variants,
)
from screen_capture import ScreenCapture, validate_tesseract


class LatestFrameQueueTests(unittest.TestCase):
    def test_queue_replaces_stale_frames_and_never_grows(self):
        frames = LatestFrameQueue()
        region = Region("test", 0, 0, 1, 1)
        for number in range(10):
            frames.put(CapturedFrame(number, [CapturedRegion(region, object())], 0, 0))
        self.assertEqual(frames.qsize(), 1)
        self.assertEqual(frames.replacements, 9)
        self.assertEqual(frames.get().frame_id, 9)

    def test_invalid_tesseract_path_is_reported(self):
        ok, message = validate_tesseract(r"D:\does-not-exist\tesseract.exe")
        self.assertFalse(ok)
        self.assertIn("does not exist", message)


class SlowOCRTests(unittest.TestCase):
    def test_slow_ocr_does_not_overlap_or_build_queue(self):
        frames = LatestFrameQueue()
        region = Region("test", 0, 0, 1, 1)
        active = 0
        maximum = 0
        lock = threading.Lock()

        def recognize(_image, lang="", config=""):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return "Pikachu"

        worker = OCRWorker(
            None,
            None,
            frame_queue=frames,
            ocr_interval_ms=200,
            preprocessing_mode="fast",
        )
        worker._recognize = lambda *args: recognize(None)  # type: ignore[method-assign]
        worker.start()
        for number in range(8):
            frames.put(CapturedFrame(number, [CapturedRegion(region, object())], 0, 0))
        time.sleep(0.15)
        worker.stop()
        self.assertEqual(maximum, 1)
        self.assertLessEqual(frames.qsize(), 1)
        self.assertGreater(worker.stats.replaced_frames, 0)


@unittest.skipUnless(Image is not None, "Pillow is not installed")
class FakeCapture:
    def __init__(self):
        self.calls = 0

    def grab(self, region):
        self.calls += 1
        return Image.new("RGB", (region.width, region.height), (220, 220, 220))


@unittest.skipUnless(Image is not None, "Pillow is not installed")
class CaptureIntegrationTests(unittest.TestCase):
    def test_capture_reports_first_valid_frame_size_and_mean(self):
        region = Region("test", 2, 3, 8, 6)
        frames = LatestFrameQueue()
        worker = CaptureWorker(FakeCapture(), [region], interval_ms=100, frame_queue=frames)
        worker.start()
        self.assertTrue(worker.ready.wait(1.0))
        worker.stop()
        frame = frames.get_nowait()
        captured = frame.regions[0]
        self.assertEqual(captured.image_size, (8, 6))
        self.assertIsNotNone(captured.mean_pixel)
        self.assertEqual(worker.stats.first_valid_frame_id, frame.frame_id)

    def test_fast_mode_keeps_unthresholded_variants(self):
        variants = preprocess_variants(Image.new("RGB", (10, 4), "white"), "fast", 160)
        self.assertEqual({"raw_gray", "upscaled", "normal", "inverted"}, set(variants))
        self.assertEqual(variants["raw_gray"].size, (10, 4))
        self.assertEqual(variants["upscaled"].size, (30, 12))

    def test_mocked_ocr_uses_both_psm_modes_and_publishes_text(self):
        if ocr_engine.pytesseract is None:
            self.skipTest("pytesseract is not installed")
        region = Region("test", 0, 0, 10, 10)
        frames = LatestFrameQueue()
        worker = OCRWorker(None, None, frame_queue=frames, preprocessing_mode="fast", ocr_interval_ms=200)
        with mock.patch.object(ocr_engine.pytesseract, "image_to_string", return_value="Pikachu\n") as recognize:
            worker.start()
            frames.put(CapturedFrame(1, [CapturedRegion(region, Image.new("RGB", (10, 10), "white"))], 0, 0))
            deadline = time.time() + 1
            while time.time() < deadline and not worker.drain_scans():
                time.sleep(0.01)
            worker.stop()
        self.assertGreaterEqual(recognize.call_count, 8)
        configs = {call.kwargs["config"] for call in recognize.call_args_list}
        self.assertEqual({"--psm 6", "--psm 11"}, configs)

    def test_real_tesseract_screenshot_check_skips_when_unavailable(self):
        ok, message = validate_tesseract()
        if not ok:
            self.skipTest(f"real Tesseract unavailable: {message}")
        if os.environ.get("POKMS_RUN_SCREENSHOT_TEST") != "1":
            self.skipTest("opt-in hardware screenshot test; set POKMS_RUN_SCREENSHOT_TEST=1")
        capture = ScreenCapture()
        try:
            if capture._sct is None:  # type: ignore[attr-defined]
                self.skipTest("mss could not open a desktop capture session")
            monitor = capture._sct.monitors[1]  # type: ignore[union-attr]
            image = capture.grab(Region("desktop", monitor["left"], monitor["top"], 1, 1))
            command = ocr_engine.pytesseract.pytesseract.tesseract_cmd
            detected = ocr_engine.detect_tesseract()
            ocr_engine.pytesseract.pytesseract.tesseract_cmd = detected or "tesseract"
            try:
                self.assertIsInstance(ocr_engine.pytesseract.image_to_string(image), str)
            finally:
                ocr_engine.pytesseract.pytesseract.tesseract_cmd = command
        finally:
            capture.close()


if __name__ == "__main__":
    unittest.main()
