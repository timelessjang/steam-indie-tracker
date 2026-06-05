#!/usr/bin/env python3
"""Fetch this week's hot independent games from Steam and publish site data."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MAX_CANDIDATES = 90
TARGET_GAMES = 15
RELEASE_WINDOW_DAYS = 7

AAA_PUBLISHER_KEYWORDS = {
    "activision", "amazon games", "bandai namco", "behaviour interactive",
    "bethesda", "blizzard", "capcom", "cd projekt", "cognosphere",
    "bungie", "deep silver", "electronic arts", "embark", "epic games", "fatshark",
    "focus entertainment", "gameloft", "gearbox", "hoyoverse", "konami",
    "krafton", "microsoft", "mihoyo", "nacon", "netease", "nexon", "nintendo", "paradox",
    "pearl abyss", "playstation", "riot games", "rockstar", "secret mode",
    "sega", "sony", "square enix", "sumo group", "take-two", "tencent",
    "thq nordic", "ubisoft", "unknown worlds", "valve", "warner bros",
    "xbox game studios", "2k",
}

NON_GAME_KEYWORDS = {"software", "video production", "utilities", "animation & modeling"}
LARGE_SERVICE_TAGS = {"mmorpg", "massively multiplayer"}


@dataclass
class Candidate:
    appid: int
    name: str
    source: str
    rank: int | None = None
    heat_score: int = 0


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def fetch_url(url: str, retries: int = 3, delay: float = 1.2) -> str | None:
    headers = {
        "User-Agent": "Mozilla/5.0 SteamIndieTracker/2.0",
        "Accept": "application/json,text/xml,text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            print(f"  Attempt {attempt}/{retries} failed: {url} -> {exc}")
            if attempt < retries:
                time.sleep(delay * attempt)
    return None


def parse_appids_from_text(text: str) -> list[int]:
    patterns = [
        re.compile(r"/app/(\d+)(?:/|$)"),
        re.compile(r"/apps/(\d+)(?:/|$)"),
        re.compile(r"steam/apps/(\d+)(?:/|$)"),
        re.compile(r'"appid"\s*:\s*(\d+)'),
    ]
    appids: list[int] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            appid = int(match.group(1))
            if appid not in appids:
                appids.append(appid)
    return appids


def parse_search_results(text: str, source: str, base_score: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    try:
        data = json.loads(text)
        items = data.get("items", [])
        for index, item in enumerate(items, start=1):
            haystack = json.dumps(item, ensure_ascii=False)
            appids = parse_appids_from_text(haystack)
            if not appids:
                continue
            name = html.unescape(re.sub(r"<[^>]+>", "", item.get("name", ""))).strip()
            candidates.append(Candidate(appids[0], name, source, heat_score=base_score - index))
    except json.JSONDecodeError:
        for index, appid in enumerate(parse_appids_from_text(text), start=1):
            candidates.append(Candidate(appid, "", source, heat_score=base_score - index))
    return candidates


def fetch_weekly_top_sellers() -> list[Candidate]:
    print("[1/5] Fetching Steam weekly top sellers...")
    text = fetch_url("https://store.steampowered.com/feeds/weeklytopsellers.xml")
    if not text:
        print("  Source unavailable")
        return []
    root = ET.fromstring(text)
    out: list[Candidate] = []
    for item in root.findall("{http://purl.org/rss/1.0/}item"):
        title = (item.findtext("{http://purl.org/rss/1.0/}title") or "").strip()
        link = (item.findtext("{http://purl.org/rss/1.0/}link") or "").strip()
        appids = parse_appids_from_text(link)
        if not appids:
            continue
        rank_match = re.match(r"#(\d+)\s*-\s*(.+)", title)
        rank = int(rank_match.group(1)) if rank_match else None
        name = rank_match.group(2).strip() if rank_match else title
        out.append(Candidate(appids[0], html.unescape(name), "weekly_top_sellers", rank, 220 - (rank or 99)))
    print(f"  Found {len(out)} candidates")
    return out


def fetch_search_candidates(filter_name: str, source: str, base_score: int, count: int = 100) -> list[Candidate]:
    params = urllib.parse.urlencode({
        "filter": filter_name,
        "category1": "998",
        "json": "1",
        "count": str(count),
        "start": "0",
        "l": "english",
    })
    text = fetch_url(f"https://store.steampowered.com/search/results/?{params}")
    if not text:
        return []
    return parse_search_results(text, source, base_score)


def fetch_hot_candidates() -> list[Candidate]:
    print("[2/5] Fetching Steam hot lists...")
    lists = [
        fetch_weekly_top_sellers(),
        fetch_search_candidates("popularnew", "popular_new", 180),
        fetch_search_candidates("topsellers", "top_sellers", 160),
        fetch_search_candidates("trending", "trending", 140),
    ]
    merged: dict[int, Candidate] = {}
    for source_list in lists:
        for candidate in source_list:
            existing = merged.get(candidate.appid)
            if existing:
                existing.heat_score += candidate.heat_score
                if not existing.rank:
                    existing.rank = candidate.rank
                if existing.source != candidate.source:
                    existing.source = f"{existing.source},{candidate.source}"
            else:
                merged[candidate.appid] = candidate
    candidates = sorted(merged.values(), key=lambda c: c.heat_score, reverse=True)
    print(f"  Total unique candidates: {len(candidates)}")
    return candidates[:MAX_CANDIDATES]


def fetch_app_details(appid: int) -> dict | None:
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=english"
    text = fetch_url(url, retries=2)
    if not text:
        return None
    try:
        data = json.loads(text).get(str(appid), {})
        if data.get("success"):
            return data.get("data")
    except json.JSONDecodeError:
        return None
    return None


def fetch_steamspy_tags(appid: int) -> list[str]:
    text = fetch_url(f"https://steamspy.com/api.php?request=appdetails&appid={appid}", retries=2, delay=2)
    if not text:
        return []
    try:
        tags = json.loads(text).get("tags", {})
    except json.JSONDecodeError:
        return []
    if isinstance(tags, dict):
        return [name for name, _ in sorted(tags.items(), key=lambda item: item[1], reverse=True)[:15]]
    if isinstance(tags, list):
        return tags[:15]
    return []


def normalize_names(values: list[str]) -> list[str]:
    return [value.strip().lower() for value in values if value and value.strip()]


def has_big_publisher(publishers: list[str]) -> str | None:
    normalized = normalize_names(publishers)
    for publisher in normalized:
        for keyword in AAA_PUBLISHER_KEYWORDS:
            if keyword in publisher:
                return publisher
    return None


def parse_steam_release_date(value: str, fallback_year: int) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    if re.fullmatch(r"\d{4}", value):
        return date(int(value), 1, 1)
    formats = ("%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%d %B, %Y")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    formats_without_year = ("%b %d", "%B %d", "%d %b", "%d %B")
    for fmt in formats_without_year:
        try:
            parsed = datetime.strptime(value, fmt)
            return date(fallback_year, parsed.month, parsed.day)
        except ValueError:
            pass
    return None


def release_window(run_date: date, days: int = RELEASE_WINDOW_DAYS) -> tuple[date, date]:
    return run_date - timedelta(days=days), run_date


def is_recent_release(details: dict, run_date: date, days: int = RELEASE_WINDOW_DAYS) -> tuple[bool, str]:
    release = details.get("release_date", {})
    if release.get("coming_soon", False):
        return False, "coming soon"
    raw_date = release.get("date", "")
    parsed = parse_steam_release_date(raw_date, run_date.year)
    if not parsed:
        return False, f"unparseable release date: {raw_date or 'missing'}"
    start, end = release_window(run_date, days)
    if start <= parsed <= end:
        return True, f"released {parsed.isoformat()} within {start.isoformat()}..{end.isoformat()}"
    return False, f"released {parsed.isoformat()} outside {start.isoformat()}..{end.isoformat()}"


def is_indie_game(details: dict, tags: list[str]) -> tuple[bool, str, str]:
    genres = [g.get("description", "") for g in details.get("genres", [])]
    genre_lowers = normalize_names(genres)
    categories = normalize_names([c.get("description", "") for c in details.get("categories", [])])
    publishers = details.get("publishers", [])
    developers = details.get("developers", [])

    if any(keyword in g for g in genre_lowers for keyword in NON_GAME_KEYWORDS):
        return False, "high", "non-game software category"

    big_publisher = has_big_publisher(publishers)
    if big_publisher:
        return False, "high", f"large publisher: {big_publisher}"

    if has_big_publisher(developers):
        return False, "high", "large-studio developer"

    has_indie = "indie" in genre_lowers or any(tag.lower() == "indie" for tag in tags)
    tag_lowers = normalize_names(tags)
    if not has_indie and any(tag in tag_lowers for tag in LARGE_SERVICE_TAGS):
        return False, "high", "large-scale online service tags without Indie marker"

    is_free = details.get("is_free", False)
    price = details.get("price_overview", {}).get("initial", 0)
    release = details.get("release_date", {})
    coming_soon = release.get("coming_soon", False)

    if has_indie:
        return True, "high", "Steam marks it as Indie and no large publisher matched"
    if not is_free and 0 < price <= 3999 and not coming_soon:
        return True, "medium", "small publisher with indie-range price"
    if tags and not publishers:
        return True, "low", "tag-rich small game with no large publisher"
    return False, "low", "not clearly independent"


TAG_LABELS = [
    ("roguelike", "Roguelike"),
    ("roguelite", "Roguelite"),
    ("deckbuilder", "卡牌构筑"),
    ("deck builder", "卡牌构筑"),
    ("card", "卡牌"),
    ("survival", "生存"),
    ("crafting", "制作建造"),
    ("base building", "基地建设"),
    ("automation", "自动化"),
    ("factory", "工厂自动化"),
    ("farming", "农场经营"),
    ("life sim", "生活模拟"),
    ("cozy", "舒适治愈"),
    ("management", "经营管理"),
    ("simulation", "模拟"),
    ("city builder", "城市建造"),
    ("strategy", "策略"),
    ("turn-based", "回合制"),
    ("tower defense", "塔防"),
    ("puzzle", "解谜"),
    ("metroidvania", "银河恶魔城"),
    ("souls", "类魂"),
    ("bullet hell", "弹幕"),
    ("shooter", "射击"),
    ("fps", "第一人称射击"),
    ("rhythm", "节奏"),
    ("horror", "恐怖"),
    ("open world", "开放世界"),
    ("sandbox", "沙盒"),
    ("platformer", "平台跳跃"),
    ("co-op", "合作"),
    ("multiplayer", "多人联机"),
    ("party", "派对游戏"),
    ("idle", "放置"),
]

TREND_RULES = [
    (("roguelike", "roguelite", "deckbuilder", "card"), "Roguelike 与卡牌变体"),
    (("survival", "crafting", "base building", "open world"), "生存建造热"),
    (("cozy", "farming", "life sim", "relaxing"), "舒适生活模拟"),
    (("automation", "factory", "management", "simulation"), "自动化与经营模拟"),
    (("horror", "psychological"), "小体量恐怖叙事"),
    (("souls", "metroidvania", "bullet hell", "action"), "高强度动作变体"),
    (("party", "co-op", "multiplayer"), "多人社交玩法"),
    (("puzzle", "strategy", "turn-based", "tower defense"), "策略解谜混合"),
]


def analyze_game(game: dict) -> dict:
    haystack = " ".join(game.get("top_tags", []) + game.get("genres", [])).lower()
    labels: list[str] = []
    for needle, label in TAG_LABELS:
        if needle in haystack and label not in labels:
            labels.append(label)
    if not labels:
        labels = game.get("top_tags", [])[:4] or game.get("genres", [])[:4] or ["独立游戏"]
    labels = labels[:5]

    trend = "独立新品综合热"
    for needles, name in TREND_RULES:
        if any(needle in haystack for needle in needles):
            trend = name
            break

    formula = " + ".join(labels[:3]) if len(labels) >= 2 else labels[0]
    desc = game.get("short_description") or ""
    hook = desc.strip()
    if len(hook) > 92:
        hook = hook[:89].rstrip() + "..."
    if not hook:
        hook = f"以 {formula} 为核心卖点的本周热门独立游戏。"

    return {
        "gameplay_tags": labels,
        "hook": hook,
        "formula": formula,
        "trend_category": trend,
    }


def analyze_with_claude(games: list[dict], api_key: str) -> dict[str, dict]:
    if not api_key or not games:
        return {}
    print("[4/5] Asking Claude for gameplay analysis...")
    compact = [
        {
            "appid": g["appid"],
            "name": g["name"],
            "genres": g["genres"],
            "tags": g["top_tags"][:10],
            "description": g["short_description"],
            "developer": g["developer"],
            "publisher": g["publisher"],
        }
        for g in games
    ]
    prompt = (
        "你是独立游戏玩法趋势分析师。请只基于下面这些本周 Steam 热门独立游戏，"
        "为每个游戏输出玩法标签、核心 hook、玩法公式、趋势分类。"
        "返回严格 JSON，key 是 appid 字符串，不要 markdown。\n\n"
        "字段格式：gameplay_tags 为 3-5 个中文短标签；hook 为一句中文洞察；"
        "formula 格式为 A + B + C；trend_category 为中文趋势名。\n\n"
        f"{json.dumps(compact, ensure_ascii=False)}"
    )
    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 5000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = "".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        return json.loads(text)
    except Exception as exc:
        print(f"  Claude analysis failed, using rules instead: {exc}")
        return {}


def build_game(candidate: Candidate, details: dict, tags: list[str], confidence: str, reason: str) -> dict:
    developers = details.get("developers", [])
    publishers = details.get("publishers", [])
    price_info = details.get("price_overview", {})
    return {
        "appid": candidate.appid,
        "name": details.get("name") or candidate.name or f"App {candidate.appid}",
        "url": f"https://store.steampowered.com/app/{candidate.appid}/",
        "header_image": details.get("header_image", ""),
        "short_description": html.unescape(details.get("short_description", "")),
        "developer": ", ".join(developers),
        "publisher": ", ".join(publishers),
        "genres": [g.get("description", "") for g in details.get("genres", [])],
        "top_tags": tags,
        "release_date": details.get("release_date", {}).get("date", ""),
        "price": "Free" if details.get("is_free") else price_info.get("final_formatted", "N/A"),
        "rank": candidate.rank,
        "source": candidate.source,
        "heat_score": candidate.heat_score,
        "indie_confidence": confidence,
        "indie_reason": reason,
    }


def write_outputs(output: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "weekly.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_path = DATA_DIR / f"archive_{output['week_of']}.json"
    archive_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    weeks = sorted(
        path.stem.replace("archive_", "")
        for path in DATA_DIR.glob("archive_*.json")
        if path.name != "archive_index.json"
    )
    (DATA_DIR / "archive_index.json").write_text(json.dumps({"weeks": weeks}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    configure_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-ai", action="store_true", help="Skip Claude analysis")
    parser.add_argument("--anthropic-key", default=os.environ.get("ANTHROPIC_API_KEY", ""))
    parser.add_argument("--run-date", help="Override run date in YYYY-MM-DD format for testing")
    args = parser.parse_args()
    run_date = (
        datetime.strptime(args.run_date, "%Y-%m-%d").date()
        if args.run_date
        else datetime.now(timezone.utc).date()
    )
    window_start, window_end = release_window(run_date)

    print("=" * 70)
    print("Steam Indie Tracker - weekly independent game scan")
    print(datetime.now(timezone.utc).strftime("Run time: %Y-%m-%d %H:%M UTC"))
    print(f"Release window: {window_start.isoformat()} to {window_end.isoformat()}")
    print("=" * 70)

    candidates = fetch_hot_candidates()
    if not candidates:
        raise RuntimeError("No Steam candidates found; refusing to overwrite site data.")

    print("[3/5] Filtering for independent games and excluding large publishers...")
    games: list[dict] = []
    checked = 0
    for candidate in candidates:
        checked += 1
        if len(games) >= TARGET_GAMES:
            break
        print(f"  [{checked}/{len(candidates)}] {candidate.name or candidate.appid}")
        time.sleep(0.35)
        details = fetch_app_details(candidate.appid)
        if not details:
            print("    skip: no app details")
            continue
        recent, release_reason = is_recent_release(details, run_date)
        if not recent:
            print(f"    skip: {release_reason}")
            continue
        tags = fetch_steamspy_tags(candidate.appid)
        ok, confidence, reason = is_indie_game(details, tags)
        print(f"    indie={ok} ({confidence}) {reason}")
        if not ok:
            continue
        games.append(build_game(candidate, details, tags, confidence, reason))

    if not games:
        raise RuntimeError("No independent games passed filters; refusing to overwrite site data.")

    analyses = {}
    if not args.no_ai:
        analyses = analyze_with_claude(games, args.anthropic_key)
    else:
        print("[4/5] Generating rule-based gameplay analysis...")

    print("[5/5] Writing JSON outputs...")
    for game in games:
        game["analysis"] = analyses.get(str(game["appid"])) or analyze_game(game)

    trends: dict[str, int] = {}
    for game in games:
        trend = game["analysis"]["trend_category"]
        trends[trend] = trends.get(trend, 0) + 1

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "week_of": run_date.isoformat(),
        "release_window_start": window_start.isoformat(),
        "release_window_end": window_end.isoformat(),
        "total_checked": checked,
        "total_indie": len(games),
        "used_ai": not args.no_ai and bool(analyses),
        "scope": "this_week_hot_indie_only",
        "excluded": "AAA and large-publisher games",
        "trend_summary": sorted(trends.items(), key=lambda item: item[1], reverse=True),
        "games": games,
    }
    write_outputs(output)
    print(f"Done: wrote {len(games)} games for {output['week_of']}")


if __name__ == "__main__":
    main()
