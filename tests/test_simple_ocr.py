import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import ocr_engine
from config_manager import Region
from ocr_engine import SimpleOCRWorker


class FakeCapture:
    def grab(self, region):
        return Image.new("RGB", (region.width, region.height), "white")


class SimpleOCRTests(unittest.TestCase):
    def test_simple_worker_calls_psm11_and_saves_raw_frame(self):
        region = Region("radar", 0, 0, 20, 10)
        with tempfile.TemporaryDirectory() as directory:
            worker = SimpleOCRWorker(FakeCapture(), [region], debug_path=Path(directory) / "debug_raw.png")
            with mock.patch.object(ocr_engine.pytesseract, "image_to_string", return_value="Tentacool\n") as ocr:
                result = worker._ocr(region)
            self.assertEqual(result.text, "Tentacool\n")
            self.assertTrue((Path(directory) / "debug_raw.png").is_file())
            self.assertEqual(ocr.call_args.kwargs["config"], "--psm 11")
            self.assertIn("Tentacool", result.lines)


if __name__ == "__main__":
    unittest.main()
