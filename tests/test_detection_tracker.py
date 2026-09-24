import unittest

from detection_tracker import DetectionTracker
from matching import find_matches


def matches(text: str):
    return find_matches(text, ["Rayquaza", "Gible", "Ditto"], threshold=80)


class DetectionTrackerTests(unittest.TestCase):
    def test_first_appearance_notifies_once(self):
        tracker = DetectionTracker(lost_confirmation_scans=2, cooldown_seconds=60)
        self.assertEqual([event.kind for event in tracker.update(matches("Rayquaza"), 0)], ["notify"])
        self.assertEqual([event.reason for event in tracker.update(matches("Rayquaza"), 1)], ["cooldown"])

    def test_staying_visible_does_not_notify_again(self):
        tracker = DetectionTracker(lost_confirmation_scans=2, cooldown_seconds=0)
        tracker.update(matches("Rayquaza"), 0)
        self.assertEqual([event.reason for event in tracker.update(matches("Rayquaza"), 10)], ["active"])

    def test_disappear_then_reappear_notifies_again(self):
        tracker = DetectionTracker(lost_confirmation_scans=2, cooldown_seconds=60)
        tracker.update(matches("Rayquaza"), 0)
        tracker.update([], 1)
        lost = tracker.update([], 2)
        self.assertEqual([event.kind for event in lost], ["lost"])
        self.assertEqual([event.kind for event in tracker.update(matches("Rayquaza"), 3)], ["notify"])

    def test_multiple_pokemon_notify_independently(self):
        tracker = DetectionTracker(cooldown_seconds=60)
        events = tracker.update(matches("Rayquaza Gible Ditto"), 0)
        self.assertEqual({event.name for event in events}, {"Rayquaza", "Gible", "Ditto"})

    def test_one_missing_scan_does_not_remove_detection(self):
        tracker = DetectionTracker(lost_confirmation_scans=2)
        tracker.update(matches("Rayquaza"), 0)
        tracker.update([], 1)
        self.assertIn("rayquaza", tracker.active_detections)
        tracker.update(matches("Rayquaza"), 2)
        self.assertIn("rayquaza", tracker.active_detections)

    def test_zero_cooldown_still_requires_new_appearance(self):
        tracker = DetectionTracker(cooldown_seconds=0)
        tracker.update(matches("Rayquaza"), 0)
        self.assertEqual([event.reason for event in tracker.update(matches("Rayquaza"), 1)], ["active"])

    def test_cooldown_does_not_block_reappearance(self):
        tracker = DetectionTracker(lost_confirmation_scans=1, cooldown_seconds=60)
        tracker.update(matches("Rayquaza"), 0)
        tracker.update([], 1)
        self.assertEqual([event.kind for event in tracker.update(matches("Rayquaza"), 2)], ["notify"])


if __name__ == "__main__":
    unittest.main()
