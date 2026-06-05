import importlib.util
import json
import sys
import unittest
from pathlib import Path


FETCH_PATH = Path(__file__).parents[1] / "scripts" / "fetch.py"
SPEC = importlib.util.spec_from_file_location("fetch", FETCH_PATH)
fetch = importlib.util.module_from_spec(SPEC)
sys.modules["fetch"] = fetch
SPEC.loader.exec_module(fetch)


class SteamParsingTests(unittest.TestCase):
    def test_parses_current_steam_apps_image_url(self):
        payload = json.dumps({
            "items": [{
                "name": "Example Game",
                "logo": "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/12345/capsule.jpg",
            }]
        })

        games = fetch.parse_search_results(payload, "popular_new", 100)

        self.assertEqual(games[0].appid, 12345)
        self.assertEqual(games[0].name, "Example Game")
        self.assertEqual(games[0].source, "popular_new")

    def test_large_publisher_is_rejected(self):
        ok, confidence, reason = fetch.is_indie_game({
            "genres": [{"description": "Indie"}],
            "publishers": ["Electronic Arts"],
            "developers": ["Small Team"],
        }, ["Indie"])

        self.assertFalse(ok)
        self.assertEqual(confidence, "high")
        self.assertIn("large publisher", reason)

    def test_rule_analysis_has_required_fields(self):
        analysis = fetch.analyze_game({
            "top_tags": ["Roguelike", "Deckbuilder", "Strategy"],
            "genres": ["Indie", "Strategy"],
            "short_description": "Build a deck and climb a dangerous tower.",
        })

        self.assertEqual(set(analysis), {"gameplay_tags", "hook", "formula", "trend_category"})
        self.assertIn("Roguelike", analysis["formula"])


if __name__ == "__main__":
    unittest.main()
