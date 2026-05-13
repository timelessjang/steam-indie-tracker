#!/usr/bin/env python3
"""
Steam Indie Tracker - Weekly Fetch Script
Fetches trending indie games from Steam, analyzes gameplay mechanics via Claude API,
and outputs a JSON data file for the static website.

Usage:
    python fetch.py                    # Run with Claude API analysis
    python fetch.py --no-ai            # Run without AI (tags only, no Claude API needed)
    python fetch.py --anthropic-key KEY # Pass API key directly

Environment variables:
    ANTHROPIC_API_KEY - Your Anthropic API key (for gameplay analysis)
"""

import json
import os
import re
import sys
import time
import argparse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# --- Config ---
KNOWN_AAA_PUBLISHERS = {
    "electronic arts", "ea", "ubisoft", "activision", "blizzard",
    "blizzard entertainment", "bethesda softworks", "rockstar games",
    "take-two interactive", "2k", "square enix", "capcom", "bandai namco",
    "sega", "konami", "warner bros", "sony", "playstation", "microsoft",
    "xbox game studios", "nintendo", "epic games", "valve", "riot games",
    "mihoyo", "hoyoverse", "cognosphere", "netease", "tencent",
    "deep silver", "thq nordic", "focus entertainment", "505 games",
    "paradox interactive", "nacon", "team17", "devolver digital",
    "annapurna interactive",  # these last few are indie publishers but well-known
}

# Large publishers that sometimes publish indie-ish games - we keep them but flag
INDIE_FRIENDLY_PUBLISHERS = {
    "devolver digital", "annapurna interactive", "team17", "raw fury",
    "humble games", "tinybuild", "daedalic entertainment",
}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def fetch_url(url, retries=3, delay=1.5):
    """Fetch URL with retries and rate limiting."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "SteamIndieTracker/1.0",
                "Accept": "application/json,text/xml,*/*",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"  Attempt {attempt+1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    return None


def fetch_weekly_top_sellers():
    """Fetch the Steam weekly top sellers RSS feed."""
    print("[1/5] Fetching Steam weekly top sellers RSS...")
    xml_text = fetch_url("https://store.steampowered.com/feeds/weeklytopsellers.xml")
    if not xml_text:
        print("  ERROR: Could not fetch RSS feed")
        return []

    # Parse RSS/RDF feed
    games = []
    root = ET.fromstring(xml_text)
    ns = {
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "": "http://purl.org/rss/1.0/",
        "content": "http://purl.org/rss/1.0/modules/content/",
    }

    for item in root.findall("{http://purl.org/rss/1.0/}item"):
        title_el = item.find("{http://purl.org/rss/1.0/}title")
        link_el = item.find("{http://purl.org/rss/1.0/}link")
        if title_el is None or link_el is None:
            continue

        title = title_el.text or ""
        link = link_el.text or ""
        # Extract rank and clean title: "#1 - Counter-Strike 2"
        rank_match = re.match(r"#(\d+)\s*-\s*(.+)", title)
        rank = int(rank_match.group(1)) if rank_match else 0
        clean_title = rank_match.group(2).strip() if rank_match else title.strip()

        # Extract appid from URL
        appid_match = re.search(r"/app/(\d+)/", link)
        appid = int(appid_match.group(1)) if appid_match else None

        if appid:
            games.append({
                "appid": appid,
                "name": clean_title,
                "rank": rank,
                "url": link.split("?")[0],
                "source": "weekly_top_sellers",
            })

    print(f"  Found {len(games)} games in weekly top sellers")
    return games


def fetch_popular_new_releases():
    """Fetch popular new releases from Steam search API."""
    print("[2/5] Fetching popular new releases...")
    url = (
        "https://store.steampowered.com/search/results/"
        "?filter=popularnew&category1=998&json=1&count=50&start=0"
    )
    text = fetch_url(url)
    if not text:
        print("  ERROR: Could not fetch popular new releases")
        return []

    games = []
    # The response is HTML with embedded game data, extract appids from links
    appid_pattern = re.compile(r"/app/(\d+)/")
    name_pattern = re.compile(r'class="title">([^<]+)<')

    # Try to parse as JSON first
    try:
        data = json.loads(text)
        if "items" in data:
            for item in data["items"]:
                appid_match = appid_pattern.search(item.get("logo", "") + item.get("name", ""))
                if not appid_match:
                    # Try extracting from the HTML content
                    appid_match = appid_pattern.search(str(item))
                if appid_match:
                    appid = int(appid_match.group(1))
                    # Extract name from HTML
                    name = ""
                    name_match = name_pattern.search(str(item))
                    if name_match:
                        name = name_match.group(1)
                    games.append({
                        "appid": appid,
                        "name": name,
                        "source": "popular_new",
                    })
    except json.JSONDecodeError:
        # Parse as HTML
        for m in appid_pattern.finditer(text):
            appid = int(m.group(1))
            if not any(g["appid"] == appid for g in games):
                games.append({
                    "appid": appid,
                    "name": "",
                    "source": "popular_new",
                })

    print(f"  Found {len(games)} games in popular new releases")
    return games


def fetch_app_details(appid):
    """Fetch detailed info for a single app from Steam API."""
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=english"
    text = fetch_url(url)
    if not text:
        return None

    try:
        data = json.loads(text)
        app_data = data.get(str(appid), {})
        if app_data.get("success") and "data" in app_data:
            return app_data["data"]
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def fetch_steamspy_data(appid):
    """Fetch tag data from SteamSpy API."""
    url = f"https://steamspy.com/api.php?request=appdetails&appid={appid}"
    text = fetch_url(url, retries=2, delay=2)
    if not text:
        return None

    try:
        data = json.loads(text)
        # SteamSpy sometimes returns wrong app data, verify appid
        if data.get("appid") == appid:
            return data
    except json.JSONDecodeError:
        pass
    return None


def is_indie_game(details, steamspy_data=None):
    """
    Determine if a game is likely indie based on available data.
    Returns (is_indie: bool, confidence: str, reason: str)
    """
    if not details:
        return False, "low", "no data"

    genres = [g["description"].lower() for g in details.get("genres", [])]
    developers = [d.lower() for d in details.get("developers", [])]
    publishers = [p.lower() for p in details.get("publishers", [])]

    # Check if tagged as indie
    has_indie_genre = "indie" in genres

    # Check publisher against known AAA
    is_aaa_publisher = any(
        pub in KNOWN_AAA_PUBLISHERS
        for pub in publishers
    )

    # Check if it's F2P (many F2P games are gacha/live service, not indie-spirited)
    is_free = details.get("is_free", False)

    # Check price - most indie games are under $40
    price_cents = 0
    if "price_overview" in details:
        price_cents = details["price_overview"].get("initial", 0)

    # Check release date - we want recent releases
    release_info = details.get("release_date", {})
    is_coming_soon = release_info.get("coming_soon", False)
    release_date_str = release_info.get("date", "")

    # Decision logic
    if is_aaa_publisher and not has_indie_genre:
        return False, "high", f"AAA publisher: {', '.join(publishers)}"

    if has_indie_genre and not is_aaa_publisher:
        return True, "high", "tagged indie + non-AAA publisher"

    if has_indie_genre and is_aaa_publisher:
        # Some AAA publishers publish indie games
        if any(pub in INDIE_FRIENDLY_PUBLISHERS for pub in publishers):
            return True, "medium", f"indie tag + indie-friendly publisher"
        return False, "medium", f"indie tag but AAA publisher: {', '.join(publishers)}"

    # Not tagged indie but small publisher
    if not is_aaa_publisher and price_cents > 0 and price_cents <= 3999:
        return True, "low", "non-AAA, reasonable price, but no indie tag"

    return False, "low", "does not match indie criteria"


def analyze_with_claude(games_data, api_key):
    """Use Claude API to analyze gameplay mechanics for a batch of games."""
    print("[4/5] Analyzing gameplay mechanics with Claude...")

    if not api_key:
        print("  No API key provided, skipping AI analysis")
        return {}

    games_prompt = ""
    for g in games_data:
        tags_str = ", ".join(g.get("top_tags", []))
        genres_str = ", ".join(g.get("genres", []))
        games_prompt += f"""
Game: {g['name']}
AppID: {g['appid']}
Genres: {genres_str}
Steam Tags: {tags_str}
Description: {g.get('short_description', 'N/A')}
Developer: {g.get('developer', 'N/A')}
---
"""

    prompt = f"""你是一个游戏设计分析师。请分析以下每款独立游戏的核心玩法组合，用中文回答。

对每款游戏，请提供：
1. gameplay_tags: 3-5个玩法机制标签（用简短中文词，如"Roguelike", "卡牌构筑", "节奏动作", "开放世界", "塔防", "弹幕射击", "模拟经营"等）
2. hook: 一句话描述这个游戏的核心创意hook是什么（什么让它与众不同）
3. formula: 玩法公式，格式如 "A + B + C"（如 "节奏战斗 + Roguelike + 格斗"）
4. trend_category: 这个游戏属于哪个趋势类别（如"概率/赌博机制热", "类魂动作热", "舒适模拟热", "Roguelike变体热"等）

请以JSON格式返回，key为appid（字符串）：
{{
  "12345": {{
    "gameplay_tags": ["标签1", "标签2", "标签3"],
    "hook": "一句话描述",
    "formula": "A + B + C",
    "trend_category": "趋势类别"
  }}
}}

仅返回JSON，不要其他文字。

以下是游戏列表：
{games_prompt}"""

    try:
        request_body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=request_body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        text = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                text += block["text"]

        # Clean and parse JSON
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        analyses = json.loads(text)
        print(f"  Successfully analyzed {len(analyses)} games")
        return analyses

    except Exception as e:
        print(f"  ERROR in Claude API call: {e}")
        return {}


def generate_fallback_analysis(game):
    """Generate basic analysis from Steam tags when Claude API is not available."""
    tags = game.get("top_tags", [])
    genres = game.get("genres", [])

    # Map common English tags to Chinese gameplay labels
    tag_map = {
        "roguelike": "Roguelike",
        "roguelite": "Roguelite",
        "deckbuilder": "卡牌构筑",
        "deck builder": "卡牌构筑",
        "card game": "卡牌游戏",
        "fps": "第一人称射击",
        "shooter": "射击",
        "survival": "生存",
        "crafting": "制作合成",
        "base building": "基地建设",
        "open world": "开放世界",
        "sandbox": "沙盒",
        "platformer": "平台跳跃",
        "metroidvania": "银河恶魔城",
        "puzzle": "解谜",
        "rhythm": "节奏",
        "tower defense": "塔防",
        "city builder": "城市建造",
        "management": "经营管理",
        "simulation": "模拟",
        "farming sim": "农场模拟",
        "fishing": "钓鱼",
        "cooking": "烹饪",
        "horror": "恐怖",
        "psychological horror": "心理恐怖",
        "soulslike": "类魂",
        "souls-like": "类魂",
        "turn-based": "回合制",
        "strategy": "策略",
        "rpg": "RPG",
        "action rpg": "动作RPG",
        "narrative": "叙事",
        "visual novel": "视觉小说",
        "co-op": "合作",
        "multiplayer": "多人",
        "pvp": "PvP",
        "party game": "派对游戏",
        "racing": "竞速",
        "fighting": "格斗",
        "beat 'em up": "清版动作",
        "hack and slash": "砍杀",
        "stealth": "潜行",
        "exploration": "探索",
        "cozy": "舒适",
        "relaxing": "休闲",
        "pixel graphics": "像素风",
        "retro": "复古",
        "2d": "2D",
        "3d": "3D",
        "top-down": "俯视角",
        "side scroller": "横版",
        "bullet hell": "弹幕",
        "idle": "放置",
        "clicker": "点击",
        "automation": "自动化",
        "dating sim": "恋爱模拟",
        "life sim": "生活模拟",
        "dungeon crawler": "地牢探索",
        "action": "动作",
        "adventure": "冒险",
        "indie": "独立游戏",
    }

    gameplay_tags = []
    for tag in tags[:8]:
        tag_lower = tag.lower()
        for en, cn in tag_map.items():
            if en in tag_lower and cn not in gameplay_tags:
                gameplay_tags.append(cn)
                break

    if not gameplay_tags:
        gameplay_tags = tags[:4]

    gameplay_tags = gameplay_tags[:5]

    return {
        "gameplay_tags": gameplay_tags,
        "hook": f"基于Steam标签的初步分类：{', '.join(tags[:5])}",
        "formula": " + ".join(gameplay_tags[:3]) if len(gameplay_tags) >= 2 else "待分析",
        "trend_category": "待分析",
    }


def main():
    parser = argparse.ArgumentParser(description="Steam Indie Tracker - Fetch & Analyze")
    parser.add_argument("--no-ai", action="store_true", help="Skip Claude API analysis")
    parser.add_argument("--anthropic-key", type=str, help="Anthropic API key")
    args = parser.parse_args()

    api_key = args.anthropic_key or os.environ.get("ANTHROPIC_API_KEY", "")

    print("=" * 60)
    print("Steam Indie Tracker - Weekly Fetch")
    print(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # Step 1: Fetch game lists
    top_sellers = fetch_weekly_top_sellers()
    new_releases = fetch_popular_new_releases()

    # Merge and deduplicate
    all_appids = {}
    for g in top_sellers:
        all_appids[g["appid"]] = g
    for g in new_releases:
        if g["appid"] not in all_appids:
            all_appids[g["appid"]] = g

    print(f"\n  Total unique games to check: {len(all_appids)}")

    # Step 2: Fetch details and filter for indie
    print("\n[3/5] Fetching app details and filtering indie games...")
    indie_games = []
    checked = 0

    for appid, game_info in list(all_appids.items()):
        checked += 1
        if checked > 60:  # Safety limit
            break

        print(f"  [{checked}/{min(len(all_appids), 60)}] Checking {game_info.get('name', appid)}...")
        time.sleep(0.5)  # Rate limit: Steam allows ~200 req / 5 min

        details = fetch_app_details(appid)
        if not details:
            print(f"    Skipped: could not fetch details")
            continue

        # Update name if we didn't have it
        if not game_info.get("name"):
            game_info["name"] = details.get("name", f"App {appid}")

        is_indie, confidence, reason = is_indie_game(details)
        print(f"    Indie: {is_indie} ({confidence}) - {reason}")

        if not is_indie:
            continue

        # Get SteamSpy tags
        time.sleep(1)  # SteamSpy rate limit
        spy_data = fetch_steamspy_data(appid)
        top_tags = []
        if spy_data and "tags" in spy_data:
            # Sort tags by vote count
            sorted_tags = sorted(spy_data["tags"].items(), key=lambda x: x[1], reverse=True)
            top_tags = [t[0] for t in sorted_tags[:15]]

        # Extract useful fields
        genres = [g["description"] for g in details.get("genres", [])]
        developers = details.get("developers", [])
        publishers = details.get("publishers", [])
        release_date = details.get("release_date", {}).get("date", "")
        price_info = details.get("price_overview", {})
        price_str = "Free" if details.get("is_free") else price_info.get("final_formatted", "N/A")

        indie_games.append({
            "appid": appid,
            "name": details.get("name", game_info.get("name", "")),
            "url": f"https://store.steampowered.com/app/{appid}/",
            "header_image": details.get("header_image", ""),
            "short_description": details.get("short_description", ""),
            "developer": ", ".join(developers),
            "publisher": ", ".join(publishers),
            "genres": genres,
            "top_tags": top_tags,
            "release_date": release_date,
            "price": price_str,
            "rank": game_info.get("rank"),
            "source": game_info.get("source", ""),
            "indie_confidence": confidence,
        })

    print(f"\n  Found {len(indie_games)} indie games")

    if not indie_games:
        print("  WARNING: No indie games found! Saving empty dataset.")

    # Step 3: AI Analysis
    analyses = {}
    if not args.no_ai and api_key and indie_games:
        analyses = analyze_with_claude(indie_games, api_key)
    elif indie_games:
        print("[4/5] Generating fallback analysis from tags...")
        for g in indie_games:
            analyses[str(g["appid"])] = generate_fallback_analysis(g)

    # Step 4: Merge analysis into game data
    print("[5/5] Generating output...")
    for g in indie_games:
        aid = str(g["appid"])
        if aid in analyses:
            g["analysis"] = analyses[aid]
        else:
            g["analysis"] = generate_fallback_analysis(g)

    # Build output
    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "week_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_checked": checked,
        "total_indie": len(indie_games),
        "used_ai": not args.no_ai and bool(api_key),
        "games": indie_games,
    }

    # Save
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, "weekly.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Also save a timestamped archive
    archive_path = os.path.join(OUTPUT_DIR, f"archive_{output['week_of']}.json")
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  Saved to {output_path}")
    print(f"  Archived to {archive_path}")
    print(f"\n{'=' * 60}")
    print(f"Done! {len(indie_games)} indie games analyzed.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
