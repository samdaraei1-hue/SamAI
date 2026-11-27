"""Offline tests for structured AI result validation; no Gemini calls."""
import unittest

from think_tank import ThinkTank


class SchemaSafetyTests(unittest.TestCase):
    def test_exact_id_matching(self):
        ThinkTank._validate_ids([{"id": "001"}, {"id": "xyz"}], [{"id": "xyz"}, {"id": "001"}])

    def test_duplicate_or_missing_id_is_rejected(self):
        for result in ([{"id": "001"}, {"id": "001"}], [{"id": "001"}]):
            with self.assertRaises(ValueError):
                ThinkTank._validate_ids([{"id": "001"}, {"id": "xyz"}], result)


if __name__ == "__main__": unittest.main()
