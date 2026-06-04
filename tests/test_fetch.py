import importlib.util
import json
import unittest
from pathlib import Path


FETCH_PATH = Path(__file__).parents[1] / "scripts" / "fetch.py"
SPEC = importlib.util.spec_from_file_location("fetch", FETCH_PATH)
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)


class PopularNewReleaseParsingTests(unittest.TestCase):
    def test_parses_current_steam_apps_image_url(self):
        payload = json.dumps({
            "items": [{
                "name": "Example Game",
                "logo": "https://cdn.example/steam/apps/12345/capsule.jpg",
            }],
        })

        self.assertEqual(fetch.parse_popular_new_releases(payload), [{
            "appid": 12345,
            "name": "Example Game",
            "source": "popular_new",
        }])

    def test_parses_legacy_app_link_and_deduplicates(self):
        payload = json.dumps({
            "items": [
                {"name": "Example", "logo": "https://store.steampowered.com/app/42/"},
                {"name": "Example", "logo": "https://cdn.example/steam/apps/42/image.jpg"},
            ],
        })

        self.assertEqual(len(fetch.parse_popular_new_releases(payload)), 1)


if __name__ == "__main__":
    unittest.main()
