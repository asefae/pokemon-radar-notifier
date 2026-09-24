import unittest

from matching import find_matches, normalize_text


class MatchingTests(unittest.TestCase):
    def test_normalization_handles_case_punctuation_and_yo(self):
        self.assertEqual(normalize_text("  ЁЖИК!!!  "), "ежик")
        self.assertEqual(normalize_text("Pika-chu"), "pika-chu")

    def test_exact_and_alias_matches(self):
        matches = find_matches(
            "На экране найден PIKACHU и немного текста",
            [("Пикачу", ["Pikachu"]), "Иви"],
            threshold=80,
        )
        self.assertEqual([match.target for match in matches], ["Пикачу"])
        self.assertEqual(matches[0].score, 100.0)

    def test_threshold_rejects_unrelated_text(self):
        self.assertEqual(find_matches("совсем другое слово", ["Пикачу"], 90), [])

    def test_line_matching_corrects_common_ocr_typo_and_preserves_target(self):
        matches = find_matches("Результат:\nP1kachu", [("Пикачу", ["Pikachu"])], 90)
        self.assertEqual(matches[0].target, "Пикачу")


if __name__ == "__main__":
    unittest.main()
