"""Offline regression dataset validation."""
import json
import unittest
from pathlib import Path


class DatasetTests(unittest.TestCase):
    def test_historical_dataset_is_valid_and_ids_unique(self):
        items = json.loads(Path("comments.json").read_text(encoding="utf-8"))
        self.assertEqual(len(items), 19)
        ids = [x["id"] for x in items]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(isinstance(x.get("text"), str) for x in items))

    def test_previous_output_matches_historical_ids(self):
        source = json.loads(Path("comments.json").read_text(encoding="utf-8"))
        output = json.loads(Path("generated_replies.json").read_text(encoding="utf-8"))
        self.assertEqual({x["id"] for x in source}, {x["id"] for x in output})


if __name__ == "__main__": unittest.main()
