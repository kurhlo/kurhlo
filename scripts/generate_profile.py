#!/usr/bin/env python3
"""Generate a GitHub profile's activity cards using only Python's stdlib.

The public GitHub contributions HTML is the only data source. No token, API
credentials, third-party service, or Python dependency is needed. A parse or
network failure exits without replacing the existing SVGs.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


class CalendarParser(HTMLParser):
    """Read GitHub's dated calendar cells and their contribution tooltips."""

    def __init__(self) -> None:
        super().__init__()
        self.days: dict[str, dict] = {}
        self.tooltip: str | None = None
        self.tooltip_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple]) -> None:
        attributes = dict(attrs)
        if tag == "td" and attributes.get("data-date"):
            cell_id = attributes.get("id")
            if not cell_id or "data-level" not in attributes:
                raise ValueError("A contribution cell is missing its ID or level")
            self.days[cell_id] = {
                "date": attributes["data-date"],
                "level": int(attributes["data-level"]),
            }
        if tag == "tool-tip" and attributes.get("for", "").startswith(
            "contribution-day-component-"
        ):
            self.tooltip = attributes["for"]
            self.tooltip_parts = []

    def handle_data(self, text: str) -> None:
        if self.tooltip:
            self.tooltip_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tool-tip" and self.tooltip:
            if self.tooltip in self.days:
                text = " ".join(self.tooltip_parts)
                count = re.search(r"\b([\d,]+)\s+contributions?\b", text, re.I)
                if count:
                    self.days[self.tooltip]["count"] = int(
                        count.group(1).replace(",", "")
                    )
                elif re.search(r"\bNo contributions\b", text, re.I):
                    self.days[self.tooltip]["count"] = 0
            self.tooltip = None
            self.tooltip_parts = []


def parse_calendar(html: str) -> list[dict]:
    parser = CalendarParser()
    parser.feed(html)
    days = sorted(parser.days.values(), key=lambda day: day["date"])
    # GitHub includes the first partial week, so the rolling calendar can
    # contain a few more than 365 days. Reject partial or unexpected responses.
    if not 350 <= len(days) <= 378:
        raise ValueError(f"Expected a full yearly calendar, received {len(days)} days")
    for index, day in enumerate(days):
        current = date.fromisoformat(day["date"])
        if day.get("count") is None or day["count"] < 0:
            raise ValueError(f"Contribution count missing for {current}")
        if day["level"] not in range(5):
            raise ValueError(f"Invalid contribution level for {current}")
        if index and current != date.fromisoformat(days[index - 1]["date"]) + timedelta(days=1):
            raise ValueError("The contribution calendar contains missing or duplicate dates")
    return days


def fetch_calendar(username: str) -> str:
    url = f"https://github.com/users/{username}/contributions"
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; GitHubProfileVisuals/1.0)",
            "Accept": "text/html",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("Could not fetch the contribution calendar")


def calendar_geometry(days: list[dict]) -> tuple[date, int, list[int]]:
    first = date.fromisoformat(days[0]["date"])
    sunday = first - timedelta(days=(first.weekday() + 1) % 7)
    last = date.fromisoformat(days[-1]["date"])
    columns = (last - sunday).days // 7 + 1
    weekly = [0] * columns
    for day in days:
        column = (date.fromisoformat(day["date"]) - sunday).days // 7
        weekly[column] += day["count"]
    return sunday, columns, weekly


def metadata(username: str, days: list[dict]) -> str:
    return escape(json.dumps({
        "username": username,
        "source": f"https://github.com/users/{username}/contributions",
        "first_date": days[0]["date"],
        "last_date": days[-1]["date"],
        "total": sum(day["count"] for day in days),
        "days": len(days),
    }, separators=(",", ":")))


def activity_svg(username: str, days: list[dict]) -> str:
    _, _, weekly = calendar_geometry(days)
    total, peak = sum(weekly), max(max(weekly), 1)
    left, right, top, bottom = 220, 694, 34, 133
    points = [
        (left + index / max(1, len(weekly) - 1) * (right - left),
         bottom - value / peak * (bottom - top))
        for index, value in enumerate(weekly)
    ]
    line = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in points)
    area = f"{line} L{right},{bottom} L{left},{bottom} Z"
    first_label = date.fromisoformat(days[0]["date"]).strftime("%Y/%m")
    last_label = date.fromisoformat(days[-1]["date"]).strftime("%Y/%m")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="720" height="180" viewBox="0 0 720 180" role="img" aria-labelledby="title description">
  <title id="title">{escape(username)}'s public contribution activity</title>
  <desc id="description">{total} public contributions from {days[0]["date"]} to {days[-1]["date"]}. Weekly contribution totals: {', '.join(str(n) for n in weekly)}. Highest weekly total: {max(weekly)}.</desc>
  <metadata>{metadata(username, days)}</metadata>
  <style>text{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}.muted{{fill:#a4c4ba;font-size:11px}}</style>
  <rect width="720" height="180" rx="6" fill="#0e181e"/>
  <text class="profile-name" x="24" y="43" fill="#7dcab4" font-size="19" font-weight="500">{escape(username)}</text>
  <text class="total" x="24" y="86" fill="#c4e9df" font-size="34" font-weight="500">{total}</text>
  <text class="summary" x="24" y="112" fill="#9fbbb3" font-size="12">contributions in the last year</text>
  <text class="muted" x="24" y="154">Public contributions</text>
  <path d="{area}" fill="#387e6e" opacity="0.55"/>
  <path d="{line}" fill="none" stroke="#7bcbb4" stroke-width="1.6" stroke-linejoin="round"/>
  <path d="M{left},{bottom} H{right}" fill="none" stroke="#416359"/>
  <text class="muted" x="{right}" y="23" text-anchor="end">weekly · max {max(weekly)}</text>
  <text class="muted" x="{left}" y="155">{first_label}</text>
  <text class="muted" x="{right}" y="155" text-anchor="end">{last_label}</text>
</svg>
'''


def activity_mobile_svg(username: str, days: list[dict]) -> str:
    _, _, weekly = calendar_geometry(days)
    total, peak = sum(weekly), max(max(weekly), 1)
    left, right, top, bottom = 20, 340, 92, 202
    points = [
        (left + index / max(1, len(weekly) - 1) * (right - left),
         bottom - value / peak * (bottom - top))
        for index, value in enumerate(weekly)
    ]
    line = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in points)
    area = f"{line} L{right},{bottom} L{left},{bottom} Z"
    first_label = date.fromisoformat(days[0]["date"]).strftime("%Y/%m")
    last_label = date.fromisoformat(days[-1]["date"]).strftime("%Y/%m")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="360" height="248" viewBox="0 0 360 248" role="img" aria-labelledby="title description">
  <title id="title">{escape(username)}'s public contribution activity</title>
  <desc id="description">{total} public contributions from {days[0]["date"]} to {days[-1]["date"]}. Weekly contribution totals: {', '.join(str(n) for n in weekly)}. Highest weekly total: {max(weekly)}.</desc>
  <metadata>{metadata(username, days)}</metadata>
  <style>text{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}.muted{{fill:#a4c4ba;font-size:12px}}</style>
  <rect width="360" height="248" rx="6" fill="#0e181e"/>
  <text x="20" y="35" fill="#7dcab4" font-size="18" font-weight="500">{escape(username)}</text>
  <text x="340" y="38" text-anchor="end" fill="#c4e9df" font-size="30" font-weight="500">{total}</text>
  <text x="20" y="58" fill="#9fbbb3" font-size="12">contributions in the last year</text>
  <text class="muted" x="{right}" y="83" text-anchor="end">weekly · max {max(weekly)}</text>
  <path d="{area}" fill="#387e6e" opacity="0.55"/>
  <path d="{line}" fill="none" stroke="#7bcbb4" stroke-width="1.6" stroke-linejoin="round"/>
  <path d="M{left},{bottom} H{right}" fill="none" stroke="#416359"/>
  <text class="muted" x="{left}" y="225">{first_label}</text>
  <text class="muted" x="{right}" y="225" text-anchor="end">{last_label}</text>
</svg>
'''


def main() -> None:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--username", default="kurhlo")
    argument_parser.add_argument("--calendar-html", type=Path,
                                 help="Read a saved public calendar response instead of fetching it")
    argument_parser.add_argument("--output-dir", type=Path,
                                 default=Path(__file__).resolve().parents[1] / "assets")
    arguments = argument_parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", arguments.username):
        argument_parser.error("Invalid GitHub username")
    html = (arguments.calendar_html.read_text(encoding="utf-8")
            if arguments.calendar_html else fetch_calendar(arguments.username))
    days = parse_calendar(html)
    images = {
        "activity.svg": activity_svg(arguments.username, days),
        "activity-mobile.svg": activity_mobile_svg(arguments.username, days),
    }
    # Validate all documents before writing any file. This retains the
    # previous known-good images when GitHub changes its HTML unexpectedly.
    for svg in images.values():
        ET.fromstring(svg)
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    for name, svg in images.items():
        target = arguments.output_dir / name
        temporary = target.with_suffix(".svg.tmp")
        temporary.write_text(svg, encoding="utf-8")
        temporary.replace(target)
    print(json.dumps({
        "username": arguments.username,
        "total": sum(day["count"] for day in days),
        "days": len(days),
        "first_date": days[0]["date"],
        "last_date": days[-1]["date"],
        "files": [str(arguments.output_dir / name) for name in images],
    }))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, HTTPError, URLError, TimeoutError) as error:
        print(f"Profile visuals were not updated: {error}", file=sys.stderr)
        raise SystemExit(1)
