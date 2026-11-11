#!/usr/bin/env python3
"""Generate a GitHub profile's contribution staircases using only Python's stdlib.

The public GitHub contributions HTML is the only data source. No token, API
credentials, third-party service, or Python dependency is needed. A parse or
network failure exits without replacing the existing SVGs.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser
from itertools import groupby
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


def metadata(username: str, days: list[dict]) -> str:
    return escape(json.dumps({
        "username": username,
        "source": f"https://github.com/users/{username}/contributions",
        "first_date": days[0]["date"],
        "last_date": days[-1]["date"],
        "total": sum(day["count"] for day in days),
        "days": len(days),
    }, separators=(",", ":")))


def activity_staircase(username: str, days: list[dict], *, mobile: bool, dark: bool) -> str:
    first, last = (date.fromisoformat(days[index]["date"]) for index in (0, -1))
    months = []
    for _, month_days in groupby(days, key=lambda day: day["date"][:7]):
        observed = list(month_days)
        months.append((date.fromisoformat(observed[-1]["date"]), sum(day["count"] for day in observed)))
    total = sum(count for _, count in months)
    width, height = (360, 115) if mobile else (720, 87)
    left, right, top, bottom, axis = (10, 350, 54, 93, 109) if mobile else (247, 706, 11, 61, 79)
    title_y, count_y, count_size = (17, 42, 23) if mobile else (30, 57, 26)
    ink, muted, rule, trail_start, mint, comet = (
        ("#e6edf3", "#9da7b3", "#30363d", "#4e8f7d", "#90dcc7", "#adf5df") if dark
        else ("#1f2328", "#57606a", "#d8dee4", "#78a394", "#337c67", "#367d69")
    )

    cumulative = 0
    distances = [0.0]
    readings = [(0, f"{first:%b %Y}")]
    path_commands = [f"M{left} {bottom}"]
    for end, count in months:
        cumulative += count
        x = left + (right - left) * (end - first).days / (last - first).days
        y = bottom - (bottom - top) * cumulative / max(total, 1)
        path_commands.append(f"H{x:.3f} V{y:.3f}")
        distances.append(x - left + bottom - y)
        readings.append((cumulative, f"{end:%b %Y}"))
    path = " ".join(path_commands)
    length = distances[-1]
    fractions = [distance / length for distance in distances] + [1.0]

    # Draw over 5.2 seconds, then leave the full chart visible until the next pass.
    cycle_seconds = 12
    draw_end = 5.2 / cycle_seconds
    fade_end = draw_end + 0.3 / cycle_seconds
    times = [draw_end * index / len(months) for index in range(len(months) + 1)] + [1.0]

    def decimals(values):
        return ";".join(f"{value:.6f}" for value in values)

    def animation(attribute, values, keys=times, *, discrete=False):
        values = ";".join(f"{value:.6f}" if isinstance(value, float) else str(value) for value in values)
        mode = ' calcMode="discrete"' if discrete else ""
        return (f'<animate attributeName="{attribute}" values="{values}" keyTimes="{decimals(keys)}"'
                f'{mode} dur="{cycle_seconds}s" repeatCount="indefinite"/>')

    def reading(count, period):
        return (f'<text x="10" y="{count_y}" fill="{ink}" font-size="{count_size}" '
                f'font-weight="600">{count:,}<tspan dx="8" font-size="11.5" '
                f'font-weight="400" fill="{muted}">{period}</tspan></text>')

    tails = []
    band = (22 if mobile else 29) / length / 4
    for index, (weight, opacity) in enumerate(((1.7, 0.20), (2.1, 0.36), (2.6, 0.54), (3.2, 0.78))):
        travel = animation("stroke-dashoffset", [band * (4 - index) - fraction for fraction in fractions])
        tails.append(f'<path class="line" d="{path}" pathLength="1" stroke="{comet}" stroke-width="{weight}" '
                     f'stroke-opacity="{opacity}" stroke-dasharray="{band:.6f} 1">{travel}</path>')
    tail = "\n      ".join(tails)
    appear = animation("opacity", [0, 1, 1, 0, 0], [0, 0.014, draw_end, fade_end, 1])
    at_end = animation("opacity", [0, 0, 1, 1], [0, draw_end, fade_end, 1])
    reveal = animation("stroke-dashoffset", [1 - fraction for fraction in fractions])
    full_reading = reading(total, "past year")
    monthly_readings = []
    for index, (count, period) in enumerate(readings[:-1]):
        start, end = times[index:index + 2]
        states, keys = (("hidden", "visible", "hidden", "hidden"), (0, start, end, 1)) if index else (
            ("visible", "hidden", "hidden"), (0, end, 1)
        )
        show = animation("visibility", states, keys, discrete=True)
        monthly_readings.append(f'<g visibility="hidden">{show}{reading(count, period)}</g>')
    show_total = animation("visibility", ["hidden", "visible", "visible"], [0, draw_end, 1], discrete=True)
    counter = "\n    ".join(monthly_readings)
    end_y = top if total else bottom
    marker = (f'<circle cx="{right}" cy="{end_y}" r="5.7" fill="{mint}" opacity=".2"/>'
              f'<circle cx="{right}" cy="{end_y}" r="3" fill="{mint}"/>')
    midpoint = (first + (last - first) / 2).replace(day=1)
    mid_x = left + (right - left) * (midpoint - first).days / (last - first).days
    counts = "; ".join(f"{end:%Y-%m}: {count}" for end, count in months)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">
  <title id="title">Cumulative public contributions over the past year</title>
  <desc id="description">{escape(username)}: {total} public contributions from {first} to {last}. The staircase rises at the end of each observed month. Monthly counts, including partial months: {counts}.</desc>
  <metadata>{metadata(username, days)}</metadata>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; font-variant-numeric: tabular-nums; }}
    .line {{ fill: none; stroke-linecap: round; stroke-linejoin: round; }}
    .still {{ display: none; }}
    @media (prefers-reduced-motion: reduce) {{
      .animated {{ display: none; }}
      .still {{ display: inline; }}
    }}
  </style>
  <defs><linearGradient id="mint"><stop offset="0" stop-color="{trail_start}"/><stop offset="1" stop-color="{mint}"/></linearGradient></defs>
  <text x="10" y="{title_y}" fill="{ink}" font-size="13.5" font-weight="600">Public contributions</text>
  <line x1="{left}" x2="{right}" y1="{(top + bottom) / 2}" y2="{(top + bottom) / 2}" stroke="{rule}" opacity=".65"/>
  <line x1="{left}" x2="{right}" y1="{bottom}" y2="{bottom}" stroke="{rule}"/>
  <path class="line" d="{path}" stroke="{mint}" stroke-width="1.2" opacity=".22"/>
  <g class="animated" aria-hidden="true">
    <path class="line" d="{path}" pathLength="1" stroke="url(#mint)" stroke-width="1.6" stroke-dasharray="1">{reveal}</path>
    <g opacity="0">
      {appear}
      {tail}
      <g>
        <animateMotion path="{path}" keyPoints="{decimals(fractions)}" keyTimes="{decimals(times)}" calcMode="linear" dur="{cycle_seconds}s" repeatCount="indefinite"/>
        <circle r="5.9" fill="{mint}" opacity=".24"/>
        <circle r="3.35" fill="{comet}"/>
        <circle r="1.7" fill="#edfff8"/>
      </g>
    </g>
    <g opacity="1">{at_end}{marker}</g>
    {counter}
    <g visibility="visible">{show_total}{full_reading}</g>
  </g>
  <g class="still" aria-hidden="true">
    <path class="line" d="{path}" stroke="url(#mint)" stroke-width="1.6"/>
    {marker}
    {full_reading}
  </g>
  <g fill="{muted}" font-size="11">
    <text x="{left}" y="{axis}">{first:%b %Y}</text>
    <text x="{mid_x:.2f}" y="{axis}" text-anchor="middle">{midpoint:%b %Y}</text>
    <text x="{right}" y="{axis}" text-anchor="end">{last:%b %Y}</text>
  </g>
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
        "activity.svg": activity_staircase(arguments.username, days, mobile=False, dark=False),
        "activity-dark.svg": activity_staircase(arguments.username, days, mobile=False, dark=True),
        "activity-mobile.svg": activity_staircase(arguments.username, days, mobile=True, dark=False),
        "activity-mobile-dark.svg": activity_staircase(arguments.username, days, mobile=True, dark=True),
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
