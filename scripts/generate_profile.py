#!/usr/bin/env python3
"""Generate a GitHub profile's contribution timelines using only Python's stdlib.

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


def activity_timeline(username: str, days: list[dict], *, mobile: bool, dark: bool) -> str:
    sunday, columns, weekly = calendar_geometry(days)
    total = sum(weekly)
    peak = max(max(weekly), 1)
    width, height = (360, 64) if mobile else (720, 66)
    left, right = 10, width - 10
    center_y = 35
    spacing = (right - left) / max(1, columns - 1)
    max_radius = min(4.6, spacing * 0.39)
    ink, muted, line, empty, mint = (
        ("#e6edf3", "#9da7b3", "#30363d", "#52616b", "#7bcbb4") if dark
        else ("#1f2328", "#57606a", "#d8dee4", "#9ba8a6", "#387e68")
    )
    first, last = (date.fromisoformat(days[index]["date"]) for index in (0, -1))
    first_active = next((index for index, count in enumerate(weekly) if count), 0)
    dots, pulses, weekly_description = [], [], []
    for index, count in enumerate(weekly):
        x = left + index * spacing
        radius = max(1.15, max_radius * (count / peak) ** 0.5) if count else 0.9
        dots.append(f'<circle cx="{x:.2f}" cy="{center_y}" r="{radius:.2f}" '
                    f'fill="{mint if count else empty}"/>')
        if count:
            delay = (index - first_active) * 85
            pulses.append(f'<circle class="pulse" cx="{x:.2f}" cy="{center_y}" '
                          f'r="{radius + 0.35:.2f}" style="animation-delay:{delay}ms"/>')
        week_start = max(sunday + timedelta(weeks=index), first)
        weekly_description.append(f"{week_start}: {count}")
    marks = "\n    ".join(dots + pulses)
    counts = "; ".join(weekly_description)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">
  <title id="title">Public contributions over the past year</title>
  <desc id="description">{escape(username)}: {total} public contributions from {first} to {last}. Larger dots indicate more contributions. Weekly counts by start date, including partial weeks: {counts}.</desc>
  <metadata>{metadata(username, days)}</metadata>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .pulse {{
      fill: none;
      stroke: {mint};
      stroke-width: 0.8;
      opacity: 0;
      transform-box: fill-box;
      transform-origin: center;
      animation: week-pulse 10s linear infinite;
    }}
    @keyframes week-pulse {{
      0% {{ opacity: 0; transform: scale(1); }}
      1.4% {{ opacity: 0.64; }}
      6%, 100% {{ opacity: 0; transform: scale(1.8); }}
    }}
    @media (prefers-reduced-motion: reduce) {{ .pulse {{ animation: none; }} }}
  </style>
  <text x="{left}" y="16" fill="{ink}" font-size="13" font-weight="600">Public contributions</text>
  <text x="{right}" y="16" text-anchor="end" fill="{muted}" font-size="12"><tspan fill="{ink}" font-weight="600">{total:,}</tspan> in the past year</text>
  <g aria-hidden="true">
    <line x1="{left}" x2="{right}" y1="{center_y}" y2="{center_y}" stroke="{line}" stroke-width="0.8"/>
    {marks}
  </g>
  <text x="{left}" y="56" fill="{muted}" font-size="11">{first:%b %Y}</text>
  <text x="{right}" y="56" text-anchor="end" fill="{muted}" font-size="11">{last:%b %Y}</text>
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
        "activity.svg": activity_timeline(arguments.username, days, mobile=False, dark=False),
        "activity-dark.svg": activity_timeline(arguments.username, days, mobile=False, dark=True),
        "activity-mobile.svg": activity_timeline(arguments.username, days, mobile=True, dark=False),
        "activity-mobile-dark.svg": activity_timeline(arguments.username, days, mobile=True, dark=True),
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
