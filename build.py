#!/usr/bin/env python3
"""Build the Field Drip site.

Reads data/site.txt, data/schools.csv, data/deals.csv, data/reports.csv,
pages/about.md and the Markdown posts in posts/, checks all of it, and writes
the finished site to _site/. GitHub Actions runs this on every commit.

Preview on your own computer:
    pip install -r requirements.txt
    python build.py
    python -m http.server --directory _site 8000
Then open http://localhost:8000
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import sys
from email.utils import format_datetime
from pathlib import Path

try:
    import markdown
except ImportError:
    sys.exit("The markdown package is missing. Run: pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
POSTS_DIR = ROOT / "posts"
PAGES_DIR = ROOT / "pages"
STATIC = ROOT / "static"

LADDER = ["rumored", "reported", "agreed", "official", "in effect"]
STATUSES = LADDER + ["ended"]
PENDING = ("rumored", "reported", "agreed", "official")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DASH_RE = re.compile("[\u2013\u2014]")
ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
THUMB_STYLES = ["field", "green", "ink", "gold"]

SCHOOL_COLUMNS = ["id", "name", "conference"]
DEAL_COLUMNS = ["id", "school", "brand", "sub_brand", "start", "end", "status", "value", "note", "updated"]
REPORT_COLUMNS = ["deal", "outlet", "date", "terms", "url"]

ERRORS: list[str] = []
WARNINGS: list[str] = []
WAITING: list[str] = []


# ---------------------------------------------------------------- helpers

def error(where: str, msg: str) -> None:
    ERRORS.append(f"{where}: {msg}")


def warn(where: str, msg: str) -> None:
    WARNINGS.append(f"{where}: {msg}")


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def slugify(text: str) -> str:
    text = text.lower().replace("&", " and ").replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "item"


def split_list(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def truthy(value: str) -> bool:
    return (value or "").strip().lower() in ("true", "yes", "1", "on")


def long_date(day: dt.date) -> str:
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def join_names(names: list[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    if len(names) == 3:
        return f"{names[0]}, {names[1]}, and {names[2]}"
    return f"{names[0]}, {names[1]}, and {len(names) - 2} more"


def dash_check(where: str, text: str, first_line: int = 1) -> None:
    for offset, line in enumerate(text.splitlines()):
        if DASH_RE.search(line):
            error(f"{where} line {first_line + offset}",
                  "has an em or en dash. Use a comma, colon, period, or parentheses instead.")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


class When:
    """A date known to the year (2026), the month (2026-07), or the day (2026-07-01)."""

    def __init__(self, raw: str = "", where: str = "") -> None:
        self.raw = (raw or "").strip()
        self.parts: tuple[int, ...] = ()
        if not self.raw:
            return
        match = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", self.raw)
        if not match:
            error(where, f"'{self.raw}' is not a date. Write it as 2026, 2026-07, or 2026-07-01.")
            self.raw = ""
            return
        parts = tuple(int(p) for p in match.groups() if p)
        try:
            dt.date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
        except ValueError:
            error(where, f"'{self.raw}' is not a real date.")
            self.raw = ""
            return
        self.parts = parts

    def __bool__(self) -> bool:
        return bool(self.parts)

    @property
    def exact(self) -> bool:
        return len(self.parts) == 3

    def first(self) -> dt.date | None:
        if not self.parts:
            return None
        y = self.parts[0]
        m = self.parts[1] if len(self.parts) > 1 else 1
        d = self.parts[2] if len(self.parts) > 2 else 1
        return dt.date(y, m, d)

    def last(self) -> dt.date | None:
        if not self.parts:
            return None
        y = self.parts[0]
        if len(self.parts) == 1:
            return dt.date(y, 12, 31)
        m = self.parts[1]
        if len(self.parts) == 2:
            following = dt.date(y + (m == 12), m % 12 + 1, 1)
            return following - dt.timedelta(days=1)
        return dt.date(y, m, self.parts[2])

    def label(self) -> str:
        if not self.parts:
            return ""
        if len(self.parts) == 1:
            return str(self.parts[0])
        if len(self.parts) == 2:
            return f"{MONTHS[self.parts[1] - 1]} {self.parts[0]}"
        return f"{MONTHS[self.parts[1] - 1]} {self.parts[2]}, {self.parts[0]}"

    def key_first(self) -> str:
        day = self.first()
        return day.isoformat() if day else ""

    def key_last(self) -> str:
        day = self.last()
        return day.isoformat() if day else ""


# ---------------------------------------------------------------- the data model

class School:
    def __init__(self, sid: str, name: str, conference: str, where: str) -> None:
        self.id = sid
        self.name = name
        self.conference = conference
        self.conf_slug = slugify(conference)
        self.where = where
        self.deals: list[Deal] = []
        self.current: Deal | None = None
        self.pending: Deal | None = None
        self.moves: list[Move] = []
        self.board_moves: list[Move] = []
        self.posts: list[Post] = []

    @property
    def url(self) -> str:
        return f"schools/{self.id}/"

    @property
    def conf_url(self) -> str:
        return f"conferences/{self.conf_slug}/"

    def tag_brands(self) -> list[str]:
        brands: list[str] = []
        for move in self.board_moves:
            brands += [move.from_brand, move.to_brand]
        if not brands and self.current:
            brands.append(self.current.brand)
        if self.pending and self.pending.brand not in brands:
            brands.append(self.pending.brand)
        return list(dict.fromkeys(brands))

    def reports(self) -> list[Report]:
        found: list[Report] = []
        for deal in sorted(self.deals, key=lambda d: d.start.key_first() or "0000", reverse=True):
            found += deal.reports
        return found


class Deal:
    def __init__(self, row: dict) -> None:
        where = row["_where"]
        self.where = where
        self.id = row["id"]
        self.school_id = row["school"]
        self.brand = row["brand"]
        self.sub_brand = row.get("sub_brand", "")
        # Jordan is a Nike brand, but the site lists it as its own outfitter.
        if self.sub_brand.strip().lower() == "jordan":
            self.brand, self.sub_brand = "Jordan", ""
        self.status = row["status"].lower()
        self.start = When(row.get("start", ""), where)
        self.end = When(row.get("end", ""), where)
        self.value = row.get("value", "")
        self.note = row.get("note", "")
        self.updated = When(row.get("updated", ""), where)
        self.reports: list[Report] = []
        self.school: School | None = None
        self.published = True

    @property
    def brand_label(self) -> str:
        return f"{self.brand} ({self.sub_brand})" if self.sub_brand else self.brand

    @property
    def rank(self) -> int:
        return LADDER.index(self.status) + 1 if self.status in LADDER else 0

    @property
    def status_label(self) -> str:
        return self.status.capitalize()

    def span_label(self) -> str:
        if self.start and self.end:
            return f"{self.start.label()} to {self.end.label()}"
        if self.start:
            return f"From {self.start.label()}"
        if self.end:
            return f"Until {self.end.label()}"
        return "Dates not reported"


class Report:
    def __init__(self, row: dict) -> None:
        self.deal_id = row["deal"]
        self.outlet = row["outlet"]
        self.date = When(row.get("date", ""), row["_where"])
        self.terms = row.get("terms", "")
        self.url = row.get("url", "")
        self.deal: Deal | None = None


class Move:
    def __init__(self, school: School, prev: Deal, deal: Deal) -> None:
        self.school = school
        self.prev = prev
        self.deal = deal

    @property
    def from_brand(self) -> str:
        return self.prev.brand

    @property
    def to_brand(self) -> str:
        return self.deal.brand


class Post:
    def __init__(self) -> None:
        self.path: Path | None = None
        self.slug = ""
        self.title = ""
        self.dek = ""
        self.date = dt.date.today()
        self.author = ""
        self.kicker = ""
        self.school_ids: list[str] = []
        self.schools: list[School] = []
        self.extra_brands: list[str] = []
        self.extra_confs: list[str] = []
        self.brands: list[str] = []
        self.confs: list[str] = []
        self.lead = False
        self.thumb = ""
        self.thumb_style = ""
        self.body_html = ""
        self.words = 0

    @property
    def url(self) -> str:
        return f"stories/{self.slug}/"

    @property
    def read_minutes(self) -> int:
        return max(1, round(self.words / 230))


class Site:
    def __init__(self) -> None:
        self.config: dict[str, str] = {}
        self.schools: dict[str, School] = {}
        self.deals: dict[str, Deal] = {}
        self.posts: list[Post] = []
        self.board: list[Move] = []
        self.brands: list[str] = []
        self.conferences: list[str] = []
        self.all_school_ids: set[str] = set()
        self.hidden_schools: list[School] = []
        self.about_title = "About"
        self.about_description = ""
        self.about_html = ""

    def brand_slug(self, brand: str) -> str:
        return slugify(brand)


# ---------------------------------------------------------------- reading files

def read_kv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        error(rel(path), "file is missing")
        return values
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            error(f"{rel(path)} line {number}", "settings look like 'key: value'")
            continue
        key, value = stripped.split(":", 1)
        values[key.strip().lower()] = value.strip()
        if DASH_RE.search(value):
            dash_check(rel(path), line, number)
    return values


def read_csv(name: str, columns: list[str], required: list[str]) -> list[dict]:
    path = DATA / name
    if not path.exists():
        error(f"data/{name}", "file is missing")
        return []
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = [cell.strip().lower() for cell in next(reader, [])]
        missing = [c for c in columns if c not in header]
        if missing:
            error(f"data/{name} line 1", f"missing column(s): {', '.join(missing)}")
            return []
        for raw in reader:
            where = f"data/{name} line {reader.line_num}"
            if not any(cell.strip() for cell in raw):
                continue
            if raw[0].strip().startswith("#"):
                continue
            if len(raw) != len(header):
                error(where, f"has {len(raw)} values but the header has {len(header)}. "
                             "If a value contains a comma, wrap it in double quotes.")
                continue
            row = {h: cell.strip() for h, cell in zip(header, raw)}
            row["_where"] = where
            for column in required:
                if not row.get(column):
                    error(where, f"'{column}' is empty")
            for column, cell in row.items():
                if column != "_where" and DASH_RE.search(cell):
                    error(where, f"'{column}' has an em or en dash. Use a comma, colon, period, or parentheses instead.")
            rows.append(row)
    return rows


def read_markdown_file(path: Path) -> tuple[dict[str, str], str, int]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    meta: dict[str, str] = {}
    body_start = 0
    if lines and lines[0].strip() == "---":
        closed = False
        for index in range(1, len(lines)):
            line = lines[index]
            if line.strip() == "---":
                body_start = index + 1
                closed = True
                break
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if ":" not in line:
                error(f"{rel(path)} line {index + 1}", "lines at the top look like 'key: value'")
                continue
            key, value = line.split(":", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            meta[key.strip().lower()] = value
        if not closed:
            error(rel(path), "the settings at the top start with --- but never close with ---")
    dash_check(rel(path), "\n".join(lines), 1)
    return meta, "\n".join(lines[body_start:]), body_start + 1


def render_markdown(body: str, base_path: str) -> str:
    # Links written as ~/path stay as they are here; Ctx.fix turns them into real links for each page.
    return markdown.markdown(
        body,
        extensions=["extra", "sane_lists", "smarty"],
        extension_configs={"smarty": {"smart_dashes": False}},
        output_format="html",
    )


# ---------------------------------------------------------------- loading and checking

def load(today: dt.date, base_path: str) -> Site:
    site = Site()
    site.config = read_kv(DATA / "site.txt")
    site.config.setdefault("title", "Field Drip")
    site.config.setdefault("tagline", "")
    site.config.setdefault("description", "")
    site.config.setdefault("author", "")

    for row in read_csv("schools.csv", SCHOOL_COLUMNS, SCHOOL_COLUMNS):
        sid = row["id"]
        if sid and not ID_RE.fullmatch(sid):
            error(row["_where"], f"school id '{sid}' should be lowercase words joined by hyphens, like ole-miss")
        if sid in site.schools:
            error(row["_where"], f"school id '{sid}' is listed twice")
            continue
        site.schools[sid] = School(sid, row["name"], row["conference"], row["_where"])

    for row in read_csv("deals.csv", DEAL_COLUMNS, ["id", "school", "brand", "status"]):
        deal = Deal(row)
        if deal.id and not ID_RE.fullmatch(deal.id):
            error(deal.where, f"deal id '{deal.id}' should be lowercase words joined by hyphens, like ole-miss-adidas")
        if deal.id in site.deals:
            error(deal.where, f"deal id '{deal.id}' is used twice")
            continue
        if deal.status not in STATUSES:
            error(deal.where, f"status '{row['status']}' should be one of: {', '.join(STATUSES)}")
        school = site.schools.get(deal.school_id)
        if school is None:
            error(deal.where, f"school '{deal.school_id}' is not in data/schools.csv")
            continue
        if deal.start and deal.end and deal.end.last() < deal.start.first():
            error(deal.where, "end date is before the start date")
        deal.school = school
        school.deals.append(deal)
        site.deals[deal.id] = deal

    for row in read_csv("reports.csv", REPORT_COLUMNS, ["deal", "outlet"]):
        report = Report(row)
        deal = site.deals.get(report.deal_id)
        if deal is None:
            error(row["_where"], f"deal '{report.deal_id}' is not in data/deals.csv")
            continue
        if report.url and not re.match(r"https?://", report.url):
            error(row["_where"], "url should start with https://")
        report.deal = deal
        deal.reports.append(report)

    for school in site.schools.values():
        if not school.deals:
            WAITING.append(f"{school.name}: no deal on file yet ({school.where})")

    # No source, no publish: a current or upcoming deal stays off the site until it has a source.
    for deal in site.deals.values():
        if deal.status != "ended" and not deal.reports:
            deal.published = False
            WAITING.append(f"{deal.school.name}: {deal.brand} ({deal.where}, deal id {deal.id})")

    months = 18
    try:
        months = int(site.config.get("board_months") or 18)
    except ValueError:
        error("data/site.txt", "board_months should be a number, like 18")
    cutoff = today - dt.timedelta(days=round(months * 30.44))

    for school in site.schools.values():
        school.deals = [d for d in school.deals if d.published]
        school.deals.sort(key=lambda d: (d.start.key_first() or "0000", STATUSES.index(d.status) if d.status in STATUSES else 9))
        live = [d for d in school.deals if d.status == "in effect"]
        if len(live) > 1:
            error(live[-1].where, f"{school.name} has more than one deal marked 'in effect'. Mark the old one 'ended'.")
        school.current = live[-1] if live else None
        upcoming = [d for d in school.deals if d.status in PENDING]
        if upcoming:
            school.pending = max(upcoming, key=lambda d: (d.rank, -(d.start.first().toordinal() if d.start else 0)))
        settled: list[Deal] = []
        for deal in school.deals:
            if deal.status in PENDING:
                prev = school.current or (settled[-1] if settled else None)
            else:
                prev = settled[-1] if settled else None
            if prev is not None and deal.status != "ended" and prev.brand.lower() != deal.brand.lower():
                move = Move(school, prev, deal)
                school.moves.append(move)
                recent = deal.start and deal.start.first() >= cutoff
                if deal.status in PENDING or (deal.status == "in effect" and recent):
                    school.board_moves.append(move)
            if deal.status in ("ended", "in effect"):
                settled.append(deal)
        for deal in school.deals:
            if deal.status in PENDING and deal.start and deal.start.last() < today:
                warn(deal.where, f"{school.name} {deal.brand} started {deal.start.label()} but is still '{deal.status}'. Update the status?")
            if deal.status == "in effect" and deal.end and deal.end.last() < today:
                warn(deal.where, f"{school.name} {deal.brand} ended {deal.end.label()} but is still 'in effect'. Mark it 'ended'?")
        site.board += school.board_moves

    site.board.sort(key=lambda m: (m.school.name.lower()))
    site.board.sort(key=lambda m: m.deal.rank)
    site.board.sort(key=lambda m: m.deal.start.key_first(), reverse=True)

    site.all_school_ids = set(site.schools)
    site.hidden_schools = [s for s in site.schools.values() if not (s.current or s.pending)]
    site.schools = {sid: s for sid, s in site.schools.items() if s.current or s.pending}

    brand_names: dict[str, str] = {}
    for school in site.schools.values():
        for deal in school.deals:
            brand_names.setdefault(deal.brand.lower(), deal.brand)
    site.brands = sorted(brand_names.values(), key=str.lower)
    site.conferences = sorted({s.conference for s in site.schools.values()}, key=str.lower)

    load_posts(site, base_path)
    load_about(site, base_path)
    return site


def load_posts(site: Site, base_path: str) -> None:
    if not POSTS_DIR.exists():
        return
    slugs: dict[str, Path] = {}
    for index, path in enumerate(sorted(POSTS_DIR.glob("*.md"))):
        meta, body, _ = read_markdown_file(path)
        if truthy(meta.get("draft", "")):
            continue
        post = Post()
        post.path = path
        where = rel(path)
        post.title = meta.get("title", "")
        if not post.title:
            error(where, "needs a 'title:' line at the top")
        when = When(meta.get("date", ""), where)
        if not when.exact:
            error(where, "needs a 'date:' line at the top, like date: 2026-10-03")
        else:
            post.date = when.first()
        post.slug = slugify(meta.get("slug") or re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem))
        if post.slug in slugs:
            error(where, f"has the same web address as {rel(slugs[post.slug])}. Rename one of the files.")
        slugs[post.slug] = path
        post.dek = meta.get("dek", "")
        post.author = meta.get("author") or site.config.get("author", "")
        post.kicker = meta.get("kicker", "")
        post.lead = truthy(meta.get("lead", ""))
        post.thumb = meta.get("thumb", "")
        style = meta.get("thumb_style", "").lower()
        if style and style not in THUMB_STYLES:
            error(where, f"thumb_style should be one of: {', '.join(THUMB_STYLES)}")
            style = ""
        post.thumb_style = style or THUMB_STYLES[index % len(THUMB_STYLES)]
        for sid in split_list(meta.get("schools", "")):
            school = site.schools.get(sid)
            if school is None and sid in site.all_school_ids:
                warn(where, f"school '{sid}' has no sourced deal yet, so its tag is left off this post")
                continue
            if school is None:
                error(where, f"school '{sid}' is not in data/schools.csv (use the id, like ole-miss)")
                continue
            post.school_ids.append(sid)
            post.schools.append(school)
        post.extra_brands = split_list(meta.get("brands", ""))
        post.extra_confs = split_list(meta.get("conferences", ""))
        brands: list[str] = []
        confs: list[str] = []
        for school in post.schools:
            brands += school.tag_brands()
            confs.append(school.conference)
        post.brands = list(dict.fromkeys(brands + post.extra_brands))
        post.confs = list(dict.fromkeys(confs + post.extra_confs))
        post.words = len(re.findall(r"[A-Za-z0-9']+", body))
        post.body_html = render_markdown(body, base_path)
        site.posts.append(post)
        for school in post.schools:
            school.posts.append(post)
    site.posts.sort(key=lambda p: (p.date, p.lead), reverse=True)


def load_about(site: Site, base_path: str) -> None:
    path = PAGES_DIR / "about.md"
    if not path.exists():
        site.about_html = "<p>Field Drip tracks who outfits college football.</p>"
        return
    meta, body, _ = read_markdown_file(path)
    site.about_title = meta.get("title", "About")
    site.about_description = meta.get("description", "")
    site.about_html = render_markdown(body, base_path)


# ---------------------------------------------------------------- rendering context

class Ctx:
    def __init__(self, site: Site, base_path: str, site_url: str, today: dt.date, version: str,
                 relative: bool = False) -> None:
        self.site = site
        self.base_path = base_path
        self.site_url = site_url.rstrip("/")
        self.today = today
        self.version = version
        self.relative = relative
        self.current = ""
        self.pages: list[str] = []

    def u(self, path: str = "") -> str:
        if not self.relative:
            return self.base_path + path
        # Relative mode: links climb out of the current page's folder and name index.html outright,
        # so the site opens straight from a folder with no web server.
        target = path + "index.html" if path == "" or path.endswith("/") else path
        return "../" * self.current.count("/") + target

    def fix(self, text: str) -> str:
        def swap(match: re.Match) -> str:
            attr, path = match.group(1), match.group(2)
            tail = ""
            for mark in ("#", "?"):
                if mark in path:
                    path, rest = path.split(mark, 1)
                    tail = mark + rest
                    break
            return f'{attr}="{self.u(path)}{tail}"'
        return re.sub(r'(href|src)="~/([^"]*)"', swap, text)

    def absolute(self, path: str = "") -> str:
        if self.site_url:
            return f"{self.site_url}/{path}"
        return self.u(path)

    @property
    def title(self) -> str:
        return self.site.config.get("title", "Field Drip")


ARROW = ('<svg class="arrow" width="26" height="14" viewBox="0 0 26 14" aria-hidden="true">'
         '<path d="M1 7h22M18 2l5 5-5 5" fill="none" stroke="currentColor" stroke-width="2" '
         'stroke-linecap="round" stroke-linejoin="round"></path></svg>')
BELL = ('<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" '
        'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>'
        '<path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"></path></svg>')


def wordmark(title: str, extra_class: str = "") -> str:
    first, _, rest = title.partition(" ")
    tail = f' <span class="drip">{esc(rest)}</span>' if rest else ""
    return f'<span class="wordmark {extra_class}">{esc(first)}{tail}</span>'


def has_tip_line(ctx: Ctx) -> bool:
    return bool(ctx.site.config.get("tips_form_url") or ctx.site.config.get("tips_email"))


def tip_actions(ctx: Ctx, light: bool = False) -> str:
    form = ctx.site.config.get("tips_form_url", "")
    email = ctx.site.config.get("tips_email", "")
    bits = []
    if form:
        bits.append(f'<a class="btn btn-gold" href="{esc(form)}" rel="noopener">Send a tip</a>')
    if email:
        bits.append(f'<a class="btn {"btn-green" if light else "btn-ghost"}" href="mailto:{esc(email)}">Email {esc(email)}</a>')
    return "".join(bits)


def layout(ctx: Ctx, *, path: str, title: str, description: str, body: str,
           active: str = "", og_type: str = "website", extra_head: str = "") -> str:
    full_title = ctx.title if not title else f"{ctx.title} | {title}"
    canonical = ctx.absolute(path)
    nav_items = [
        ("board", "Deal watch", f"{ctx.u('')}#board"),
        ("tracker", "Tracker", ctx.u("tracker/")),
        ("stories", "Stories", ctx.u("stories/")),
        ("conferences", "Conferences", ctx.u("conferences/")),
        ("brands", "Brands", ctx.u("brands/")),
        ("about", "About", ctx.u("about/")),
    ]
    current_attr = ' aria-current="page"'
    nav = "".join(
        f'<a href="{href}"{current_attr if key == active else ""}>{label}</a>'
        for key, label, href in nav_items
    )
    tagline = ctx.site.config.get("tagline", "")
    tip_footer = (f'<a href="{ctx.u("tips/")}">Send a tip</a>' if has_tip_line(ctx) else "")
    tip_button = (f'<a class="btn btn-ink" href="{ctx.u("tips/")}">Send a tip</a>' if has_tip_line(ctx) else "")
    year = ctx.today.year
    fonts = "".join(
        f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family={family}&amp;display=swap">'
        for family in (
            "Big+Shoulders+Display:wght@700;800;900",
            "Libre+Franklin:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400",
            "IBM+Plex+Mono:wght@400;500;600",
        )
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:site_name" content="{esc(ctx.title)}">
<meta property="og:title" content="{esc(title or ctx.title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:type" content="{og_type}">
<meta property="og:url" content="{esc(canonical)}">
<meta name="theme-color" content="#0F2E1E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
{fonts}
<link rel="stylesheet" href="{ctx.u('static/style.css')}?v={ctx.version}">
<link rel="alternate" type="application/rss+xml" title="{esc(ctx.title)} stories" href="{ctx.u('feed.xml')}">
{extra_head}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="utility"><div class="wrap utility-in"><span>Updated {long_date(ctx.today)}</span><span>FBS apparel deals, tracked and sourced</span></div></div>
<header class="masthead">
<div class="wrap masthead-in">
<a class="brand-link" href="{ctx.u('')}">{wordmark(ctx.title)}<span class="tagline">{esc(tagline)}</span></a>
{tip_button}
</div>
<nav class="nav" aria-label="Main"><div class="wrap nav-in">{nav}</div></nav>
</header>
<main id="main">
{body}
</main>
<footer class="footer">
<div class="wrap footer-in">
<div class="footer-about">
{wordmark(ctx.title, "wordmark-sm")}
<p>Independent and not affiliated with any school, conference, or apparel company. Every deal links to its sources, and every status follows the same ladder: rumored, reported, agreed, official, in effect.</p>
</div>
<nav class="footer-nav" aria-label="Footer">
<a href="{ctx.u('about/')}#status">How we label status</a>
<a href="{ctx.u('about/')}#corrections">Sources and corrections</a>
<a href="{ctx.u('about/')}">About</a>
{tip_footer}<a href="{ctx.u('feed.xml')}">RSS feed</a>
</nav>
</div>
<div class="footer-base"><div class="wrap">&copy; {year} {esc(ctx.title)}</div></div>
</footer>
<script src="{ctx.u('static/site.js')}?v={ctx.version}" defer></script>
</body>
</html>
"""


# ---------------------------------------------------------------- shared pieces

def ladder_html(status: str) -> str:
    rank = LADDER.index(status) + 1 if status in LADDER else 0
    steps = []
    for number, step in enumerate(LADDER, 1):
        state = "on" if number <= rank else "off"
        current = ' aria-current="step"' if number == rank else ""
        steps.append(f'<li class="{state}"{current}><span class="bar"></span><span class="lbl">{esc(step.capitalize())}</span></li>')
    label = f"Status: {status.capitalize()}, step {rank} of 5" if rank else "Status: ended"
    return f'<ol class="ladder" aria-label="{esc(label)}">{"".join(steps)}</ol>'


def meter_html(deal: Deal) -> str:
    bars = "".join(f'<i class="{"on" if n <= deal.rank else ""}"></i>' for n in range(1, 6))
    return f'<span class="meter" role="img" aria-label="Status step {deal.rank} of 5: {esc(deal.status_label)}">{bars}</span>'


def external(url: str, text: str) -> str:
    if not url:
        return esc(text)
    return f'<a href="{esc(url)}" rel="noopener noreferrer" target="_blank">{esc(text)}</a>'


def deal_card_html(ctx: Ctx, school: School, heading: str = "h2") -> str:
    current, pending = school.current, school.pending
    if pending:
        focus = pending
        status = pending.status
        now_brand = current.brand_label if current else "Not on file"
        now_when = f"Since {current.start.label()}" if current and current.start else ""
        next_when = f"From {pending.start.label()}" if pending.start else "Start date not reported"
        swap = (f'<div class="dc-swap"><div><span class="label">Now</span><span class="dc-brand">{esc(now_brand)}</span>'
                f'<span class="dc-when">{esc(now_when)}</span></div>{ARROW}'
                f'<div><span class="label">Next</span><span class="dc-brand dc-next">{esc(pending.brand_label)}</span>'
                f'<span class="dc-when">{esc(next_when)}</span></div></div>')
    elif current:
        focus = current
        status = "in effect"
        swap = (f'<div class="dc-swap dc-single"><div><span class="label">Outfitter</span>'
                f'<span class="dc-brand">{esc(current.brand_label)}</span>'
                f'<span class="dc-when">{esc(current.span_label())}</span></div></div>')
    else:
        return (f'<aside class="deal-card" aria-label="{esc(school.name)} deal file"><span class="dc-kicker">Deal file</span>'
                f'<{heading} class="dc-school"><a href="{ctx.u(school.url)}">{esc(school.name)}</a></{heading}>'
                f'<p class="dc-note">No deal on file yet.</p></aside>')

    terms = [r for r in focus.reports if r.terms]
    rows = "".join(
        f'<div class="term"><span class="outlet">{external(r.url, r.outlet)}</span><span class="terms">{esc(r.terms)}</span></div>'
        for r in terms
    )
    if not rows and focus.value:
        rows = f'<div class="term"><span class="outlet">Reported value</span><span class="terms">{esc(focus.value)}</span></div>'
    disagree = ""
    if len(terms) >= 2 and focus.status in PENDING:
        disagree = '<p class="dc-note">Reports differ. This updates when the school or brand confirms the terms.</p>'
    terms_block = (f'<div class="dc-terms"><span class="label">Reported terms</span>{rows}{disagree}</div>' if rows else "")
    note = f'<p class="dc-note">{esc(focus.note)}</p>' if focus.note else ""
    sources = sum(1 for r in school.reports() if r.url)
    checked = f"<span>Checked {esc(focus.updated.label())}</span>" if focus.updated else "<span></span>"
    source_link = (f'<a href="{ctx.u(school.url)}#sources">{sources} source{"s" if sources != 1 else ""}</a>' if sources else "")
    rank = LADDER.index(status) + 1
    return f"""<aside class="deal-card" aria-label="{esc(school.name)} deal file">
<div class="dc-top"><span class="dc-kicker">Deal file</span><a class="box-tag" href="{ctx.u(school.conf_url)}">{esc(school.conference)}</a></div>
<{heading} class="dc-school"><a href="{ctx.u(school.url)}">{esc(school.name)}</a></{heading}>
{swap}
<div class="dc-status"><div class="dc-status-head"><span class="label">Status</span><span class="dc-status-word">{esc(status.capitalize())} &middot; step {rank} of 5</span></div>{ladder_html(status)}</div>
{terms_block}
{note}
<div class="dc-foot">{checked}{source_link}</div>
</aside>"""


def tag_links(ctx: Ctx, post: Post) -> str:
    items = [f'<li><a href="{ctx.u(s.url)}">{esc(s.name)}</a></li>' for s in post.schools]
    items += [f'<li><a href="{ctx.u("conferences/" + slugify(c) + "/")}">{esc(c)}</a></li>' for c in post.confs]
    items += [f'<li><a href="{ctx.u("brands/" + slugify(b) + "/")}">{esc(b)}</a></li>' for b in post.brands]
    return f'<ul class="tags" aria-label="Tags">{"".join(items)}</ul>' if items else ""


def thumb_text(post: Post) -> str:
    raw = post.thumb or f"{MONTHS[post.date.month - 1]} {post.date.day} / {post.date.year}"
    return "<br>".join(esc(part.strip()) for part in raw.split("/"))


def story_card(ctx: Ctx, post: Post, heading: str = "h3") -> str:
    meta = f"{long_date(post.date)} &middot; {post.read_minutes} min read"
    kicker = f'<span class="kicker">{esc(post.kicker)}</span>' if post.kicker else ""
    dek = f'<p class="dek">{esc(post.dek)}</p>' if post.dek else ""
    return f"""<article class="story" data-item data-brands="{esc('|'.join(post.brands))}" data-confs="{esc('|'.join(post.confs))}">
<a class="story-thumb t-{post.thumb_style}" href="{ctx.u(post.url)}" tabindex="-1" aria-hidden="true"><span>{thumb_text(post)}</span></a>
<div class="story-body">
{kicker}
<{heading}><a href="{ctx.u(post.url)}">{esc(post.title)}</a></{heading}>
{dek}
{tag_links(ctx, post)}
<p class="meta-line">{meta}</p>
</div>
</article>"""


def move_row(ctx: Ctx, move: Move) -> str:
    school, deal = move.school, move.deal
    note_html = f'<p class="row-note">{esc(deal.note)}</p>' if deal.note else ""
    start = deal.start.label() or "Not reported"
    return f"""<li class="row" data-item data-brands="{esc(move.from_brand + '|' + move.to_brand)}" data-confs="{esc(school.conference)}" data-sort-start="{deal.start.key_first()}" data-sort-rank="{deal.rank}" data-sort-school="{esc(school.name)}">
<div class="row-school"><div class="row-name-line"><a class="row-name" href="{ctx.u(school.url)}">{esc(school.name)}</a><a class="box-tag" href="{ctx.u(school.conf_url)}">{esc(school.conference)}</a></div>{note_html}</div>
<div class="row-move"><span class="from">{esc(move.prev.brand_label)}</span>{ARROW}<span class="to">{esc(deal.brand_label)}</span></div>
<div class="row-start"><span class="label">Starts</span><span class="val">{esc(start)}</span></div>
<div class="row-status"><span class="status-word">{esc(deal.status_label)}</span>{meter_html(deal)}</div>
</li>"""


def chip_buttons(key: str, values: list[str], all_label: str) -> str:
    buttons = [f'<button type="button" class="chip" data-filter-key="{key}" data-filter-value="" aria-pressed="true">{esc(all_label)}</button>']
    buttons += [f'<button type="button" class="chip" data-filter-key="{key}" data-filter-value="{esc(v)}" aria-pressed="false">{esc(v)}</button>'
                for v in values]
    return "".join(buttons)


def ranked(values: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts, key=lambda v: (-counts[v], v.lower()))


def filter_bar(brands: list[str], confs: list[str], with_sort: bool) -> str:
    rows = [
        f'<div class="filter-row"><span class="label">Brand</span><div class="chip-set">{chip_buttons("brands", brands, "All brands")}</div></div>',
        f'<div class="filter-row"><span class="label">Conference</span><div class="chip-set">{chip_buttons("confs", confs, "All conferences")}</div></div>',
    ]
    if with_sort:
        sorts = [("start", "Start date", "true"), ("status", "Status", "false"), ("school", "School", "false")]
        buttons = "".join(f'<button type="button" class="chip chip-sort" data-sort-key="{k}" aria-pressed="{p}">{label}</button>' for k, label, p in sorts)
        rows.append(f'<div class="filter-row"><span class="label">Sort by</span><div class="chip-set">{buttons}</div></div>')
    return f'<div class="filters">{"".join(rows)}</div>'


def school_finder(ctx: Ctx) -> str:
    options = "".join(f'<option value="{esc(s.name)}" data-url="{ctx.u(s.url)}"></option>'
                      for s in sorted(ctx.site.schools.values(), key=lambda s: s.name.lower()))
    return (f'<div class="field"><label class="field-label" for="school-finder">Find a school</label>'
            f'<input class="input" id="school-finder" type="search" list="school-list" autocomplete="off" '
            f'placeholder="Try {esc(next(iter(sorted(s.name for s in ctx.site.schools.values())), "a school name"))}" '
            f'data-school-finder data-fallback="{ctx.u("tracker/")}">'
            f'<datalist id="school-list">{options}</datalist></div>')


def page_head(kicker: str, title: str, lede_html: str = "", crumbs: str = "") -> str:
    lede = f'<p class="lede">{lede_html}</p>' if lede_html else ""
    crumb = f'<p class="crumbs">{crumbs}</p>' if crumbs else ""
    return f"""<div class="page-head"><div class="wrap">{crumb}<span class="kicker">{esc(kicker)}</span>
<h1 class="page-title">{esc(title)}</h1>{lede}</div></div>"""


def scoreboard_data(moves: list[Move]) -> list[dict]:
    rows: dict[str, dict] = {}
    for move in sorted(moves, key=lambda m: m.deal.start.key_first()):
        rows.setdefault(move.to_brand, {"brand": move.to_brand, "gained": [], "lost": []})["gained"].append(move.school.name)
        rows.setdefault(move.from_brand, {"brand": move.from_brand, "gained": [], "lost": []})["lost"].append(move.school.name)
    for row in rows.values():
        row["net"] = len(row["gained"]) - len(row["lost"])
    return sorted(rows.values(), key=lambda r: (-r["net"], r["brand"].lower()))


def net_label(net: int) -> str:
    if net > 0:
        return f"+{net}"
    if net < 0:
        return f"−{abs(net)}"
    return "0"


# ---------------------------------------------------------------- pages

def render_home(ctx: Ctx) -> str:
    site = ctx.site
    parts: list[str] = []

    lead = next((p for p in site.posts if p.lead), site.posts[0] if site.posts else None)
    if lead:
        lead_school = next((s for s in lead.schools if s.pending or s.board_moves), lead.schools[0] if lead.schools else None)
        card = deal_card_html(ctx, lead_school, heading="h2") if lead_school else ""
        kicker_bits = " &middot; ".join(esc(c) for c in lead.confs)
        author = f"<span>By {esc(lead.author)}</span>" if lead.author else ""
        parts.append(f"""<section class="hero" aria-labelledby="lead-title">
<div class="wrap hero-in{' hero-solo' if not card else ''}">
<div class="hero-story">
<p class="hero-kicker"><span class="flag">{esc(lead.kicker or 'Latest')}</span><span>{kicker_bits}</span></p>
<h1 id="lead-title" class="hero-title"><a href="{ctx.u(lead.url)}">{esc(lead.title)}</a></h1>
<p class="hero-dek">{esc(lead.dek)}</p>
<p class="meta-line">{author}<span>{long_date(lead.date)}</span><span>{lead.read_minutes} min read</span></p>
<div class="hero-actions"><a class="btn btn-gold" href="{ctx.u(lead.url)}">Read the story</a><a class="btn btn-ghost" href="#board">See the deal watch</a></div>
</div>
{card}
</div>
</section>""")
    else:
        parts.append(f'<h1 class="visually-hidden">{esc(ctx.title)}</h1>')

    board = site.board
    home_posts = site.posts[:6]
    countdown = ""
    upcoming = [m for m in board if m.deal.status in PENDING and m.deal.start.exact and m.deal.start.first() > ctx.today]
    if upcoming:
        soonest = min(m.deal.start.first() for m in upcoming)
        names = [m.school.name for m in upcoming if m.deal.start.first() == soonest]
        days = (soonest - ctx.today).days
        verb = "switches brands" if len(names) == 1 else "swap brands" if len(names) == 2 else "switch brands"
        countdown = f"""<div class="countdown" data-countdown="{soonest.isoformat()}">
<span class="countdown-num" data-countdown-num>{days}</span>
<span class="countdown-text"><strong><span data-countdown-unit>{"day" if days == 1 else "days"}</span> until {esc(join_names(names))} {verb}</strong><span class="countdown-date">{long_date(soonest)}</span></span>
</div>"""

    brands = ranked([b for m in board for b in (m.from_brand, m.to_brand)] + [b for p in home_posts for b in p.brands])
    confs = ranked([m.school.conference for m in board] + [c for p in home_posts for c in p.confs])
    rows = "".join(move_row(ctx, m) for m in board)
    board_count = f"Showing {len(board)} of {len(board)} moves"
    parts.append(f"""<div data-filter-scope>
<section id="board" class="section" aria-labelledby="board-title">
<div class="wrap stack">
<div class="section-head">
<div class="intro"><span class="kicker">The board</span><h2 id="board-title" class="h2">Deal watch</h2>
<p>Every switch on the record, where it stands, and when it takes effect. Filter by brand or conference and the stories below follow along.</p></div>
{countdown}
</div>
{filter_bar(brands, confs, with_sort=True)}
<div class="filter-list" data-filter-list data-noun="moves">
<div class="list-meta"><span data-count>{board_count}</span><button type="button" class="text-button" data-filter-clear hidden>Clear filters</button></div>
<ul class="rows" data-sortable>{rows}</ul>
<p class="empty" data-empty hidden>No moves match those filters yet.</p>
</div>
<a class="link-arrow" href="{ctx.u('tracker/')}">Open the full tracker &rarr;</a>
</div>
</section>
<section id="stories" class="section section-tight" aria-labelledby="stories-title">
<div class="wrap stack">
<div class="section-head"><div class="intro"><span class="kicker">On the record</span><h2 id="stories-title" class="h2">Latest stories</h2></div>
<a class="link-arrow" href="{ctx.u('stories/')}">All stories &rarr;</a></div>
<div class="filter-list" data-filter-list data-noun="stories">
<div class="story-grid">{"".join(story_card(ctx, p) for p in home_posts)}</div>
<p class="empty" data-empty hidden>No stories with those tags yet.</p>
</div>
</div>
</section>
</div>""")

    score = scoreboard_data(board)
    score_html = "".join(f"""<div class="score">
<span class="score-brand"><a href="{ctx.u('brands/' + slugify(r['brand']) + '/')}">{esc(r['brand'])}</a></span>
<span class="score-net">{net_label(r['net'])}</span>
<span class="score-list">Gained: {esc(', '.join(r['gained']) or 'None')}</span>
<span class="score-list">Lost: {esc(', '.join(r['lost']) or 'None')}</span>
</div>""" for r in score)
    conf_chips = "".join(f'<a class="chip" href="{ctx.u("conferences/" + slugify(c) + "/")}">{esc(c)}</a>' for c in site.conferences)
    brand_chips = "".join(f'<a class="chip" href="{ctx.u("brands/" + slugify(b) + "/")}">{esc(b)}</a>' for b in site.brands)
    n_moves = len(board)
    parts.append(f"""<section id="browse" class="section" aria-label="Scoreboard and tracker">
<div class="wrap split">
<div class="scoreboard">
<div class="intro"><span class="kicker">Scoreboard</span><h2 class="h2 h2-sm">Who&rsquo;s winning the switches</h2>
<p>Net schools gained across the {n_moves} move{'s' if n_moves != 1 else ''} on the board, counting reported and agreed deals.</p></div>
<div class="score-grid">{score_html}</div>
</div>
<div class="browse" id="tracker">
<div class="intro"><span class="kicker">The tracker</span><h2 class="h2 h2-sm">Browse every program</h2>
<p>Every school in one sortable table, plus a page for each conference and brand with its deals and stories.</p></div>
{school_finder(ctx)}
<div class="field"><span class="label">By conference</span><div class="chip-set">{conf_chips}</div></div>
<div class="field"><span class="label">By brand</span><div class="chip-set">{brand_chips}</div></div>
<a class="btn btn-green" href="{ctx.u('tracker/')}">Open the full tracker</a>
</div>
</div>
</section>""")

    if has_tip_line(ctx):
        parts.append(f"""<section id="tips" class="alerts" aria-labelledby="tips-title">
<div class="wrap alerts-in">
<div><h2 id="tips-title" class="h2 h2-sm">Know about a deal before it&rsquo;s announced?</h2><p>Send what you&rsquo;ve heard, with a link if you have one. Every tip gets checked against a source before it goes on the board.</p></div>
<div class="alerts-actions">{tip_actions(ctx)}</div>
</div>
</section>""")

    description = site.config.get("description", "")
    return layout(ctx, path="", title="", description=description, body="\n".join(parts), active="")


def render_stories_index(ctx: Ctx) -> str:
    posts = ctx.site.posts
    brands = ranked([b for p in posts for b in p.brands])
    confs = ranked([c for p in posts for c in p.confs])
    body = f"""{page_head("Stories", "Every story", "News, history, and explainers on who outfits college football. Filter by brand or conference.")}
<div data-filter-scope>
<section class="section section-tight"><div class="wrap stack">
{filter_bar(brands, confs, with_sort=False)}
<div class="filter-list" data-filter-list data-noun="stories">
<div class="list-meta"><span data-count>Showing {len(posts)} of {len(posts)} stories</span><button type="button" class="text-button" data-filter-clear hidden>Clear filters</button></div>
<div class="story-grid">{"".join(story_card(ctx, p, heading="h2") for p in posts)}</div>
<p class="empty" data-empty hidden>No stories with those tags yet.</p>
</div>
</div></section>
</div>"""
    return layout(ctx, path="stories/", title="Stories", description="Every Field Drip story on college football apparel deals.",
                  body=body, active="stories")


def render_post(ctx: Ctx, post: Post) -> str:
    cards = "".join(deal_card_html(ctx, s, heading="h2") for s in post.schools)
    author = f"<span>By {esc(post.author)}</span>" if post.author else ""
    kicker = f'<span class="kicker">{esc(post.kicker)}</span>' if post.kicker else ""
    others = [p for p in ctx.site.posts if p is not post][:3]
    more = ""
    if others:
        more = f"""<section class="section section-tight more" aria-labelledby="more-title"><div class="wrap stack">
<h2 id="more-title" class="h2 h2-sm">More stories</h2>
<div class="story-grid">{"".join(story_card(ctx, p) for p in others)}</div></div></section>"""
    schema = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": post.title,
        "description": post.dek,
        "datePublished": post.date.isoformat(),
        "author": {"@type": "Organization", "name": post.author} if post.author else None,
        "publisher": {"@type": "Organization", "name": ctx.title},
        "mainEntityOfPage": ctx.absolute(post.url),
    }
    schema = {k: v for k, v in schema.items() if v}
    head = f'<script type="application/ld+json">{json.dumps(schema)}</script>'
    aside = f'<div class="layout-side">{cards}<div class="side-tags"><span class="label">Tagged</span>{tag_links(ctx, post)}</div></div>'
    body = f"""<article class="post">
<header class="post-head"><div class="wrap">
{kicker}
<h1 class="post-title">{esc(post.title)}</h1>
{f'<p class="post-dek">{esc(post.dek)}</p>' if post.dek else ''}
<p class="meta-line">{author}<span>{long_date(post.date)}</span><span>{post.read_minutes} min read</span></p>
</div></header>
<div class="wrap layout">
<div class="layout-main prose">{ctx.fix(post.body_html)}</div>
{aside}
</div>
</article>
{more}"""
    return layout(ctx, path=post.url, title=post.title, description=post.dek or post.title, body=body,
                  active="stories", og_type="article", extra_head=head)


def tracker_row(ctx: Ctx, school: School) -> str:
    current, pending = school.current, school.pending
    brand = current.brand_label if current else "Not on file"
    ends = current.end.label() if current and current.end else ("Not reported" if current else "")
    next_html = (f'<span class="to">{esc(pending.brand_label)}</span><span class="sub">{esc(pending.start.label() or "Start not reported")}</span>'
                 if pending else '<span class="sub">None</span>')
    if pending:
        status_html = f'<span class="status-word">{esc(pending.status_label)}</span>{meter_html(pending)}'
        rank = pending.rank
    elif current:
        status_html = '<span class="status-word">In effect</span>'
        rank = 5
    else:
        status_html = '<span class="sub">Not on file</span>'
        rank = 0
    brands = [b for b in (current.brand if current else "", pending.brand if pending else "") if b]
    search = " ".join([school.name, school.conference] + brands)
    return f"""<tr data-search="{esc(search)}" data-conf="{esc(school.conference)}" data-brand="{esc('|'.join(brands))}"
 data-sort-school="{esc(school.name.lower())}" data-sort-conf="{esc(school.conference.lower())}" data-sort-brand="{esc(brand.lower() if current else '')}"
 data-sort-ends="{current.end.key_last() if current else ''}" data-sort-next="{esc(pending.brand.lower() if pending else '')}" data-sort-status="{rank}">
<td class="school"><a href="{ctx.u(school.url)}">{esc(school.name)}</a></td>
<td><a href="{ctx.u(school.conf_url)}">{esc(school.conference)}</a></td>
<td class="strong">{esc(brand)}</td>
<td class="mono">{esc(ends)}</td>
<td class="next">{next_html}</td>
<td class="status">{status_html}</td>
</tr>"""


def render_tracker(ctx: Ctx) -> str:
    schools = sorted(ctx.site.schools.values(), key=lambda s: s.name.lower())
    n = len(schools)
    conf_options = "".join(f'<option value="{esc(c)}">{esc(c)}</option>' for c in ctx.site.conferences)
    brand_options = "".join(f'<option value="{esc(b)}">{esc(b)}</option>' for b in ctx.site.brands)
    columns = [("school", "School", "text"), ("conf", "Conference", "text"), ("brand", "Outfitter now", "text"),
               ("ends", "Deal ends", "text"), ("next", "Next", "text"), ("status", "Status", "number")]
    sorted_attr = ' aria-sort="ascending"'
    heads = "".join(
        f'<th scope="col" data-col="{key}" data-type="{kind}"{sorted_attr if key == "school" else ""}>'
        f'<button type="button">{label}</button></th>'
        for key, label, kind in columns
    )
    body = f"""{page_head("The tracker", "Every program", f"{n} program{'s' if n != 1 else ''} tracked so far. Sort any column, or filter by conference and brand. Each school links to its full deal history and sources.")}
<section class="section section-tight"><div class="wrap stack" data-tracker>
<div class="controls">
<div class="field control"><label class="field-label" for="tracker-search">Search</label><input class="input" id="tracker-search" type="search" placeholder="School, conference, or brand" data-tracker-search></div>
<div class="field control"><label class="field-label" for="tracker-conf">Conference</label><select class="select" id="tracker-conf" data-tracker-filter="conf"><option value="">All conferences</option>{conf_options}</select></div>
<div class="field control"><label class="field-label" for="tracker-brand">Brand</label><select class="select" id="tracker-brand" data-tracker-filter="brand"><option value="">All brands</option>{brand_options}</select></div>
</div>
<div class="list-meta"><span data-tracker-count>Showing {n} of {n} programs</span></div>
<div class="table-wrap"><table class="data-table">
<caption class="visually-hidden">College football apparel deals by school</caption>
<thead><tr>{heads}</tr></thead>
<tbody>{"".join(tracker_row(ctx, s) for s in schools)}</tbody>
</table></div>
<p class="empty" data-tracker-empty hidden>No programs match. Try a different search or filter.</p>
<p class="fine">Brand shown is the football program's outfitter. Jordan Brand is part of Nike but is listed on its own. Status follows <a href="{ctx.u('about/')}#status">the ladder</a>.</p>
</div></section>"""
    return layout(ctx, path="tracker/", title="Tracker", description="Every college football program's apparel outfitter, deal end date, and next move, in one sortable table.",
                  body=body, active="tracker")


def timeline_html(school: School) -> str:
    items = []
    for deal in sorted(school.deals, key=lambda d: d.start.key_first() or "0000", reverse=True):
        kind = "s-pending" if deal.status in PENDING else "s-live" if deal.status == "in effect" else ""
        value = f'<p class="tl-note">{esc(deal.value)}</p>' if deal.value else ""
        note = f'<p class="tl-note">{esc(deal.note)}</p>' if deal.note else ""
        items.append(f"""<li><span class="tl-years">{esc(deal.span_label())}</span>
<div><span class="tl-brand">{esc(deal.brand_label)} <span class="status-pill {kind}">{esc(deal.status_label)}</span></span>{value}{note}</div></li>""")
    return f'<ol class="timeline">{"".join(items)}</ol>'


def sources_html(school: School) -> str:
    reports = school.reports()
    if not reports:
        return '<p class="fine">No sources listed yet.</p>'
    items = []
    for report in reports:
        date = f' &middot; {esc(report.date.label())}' if report.date else ""
        deal = report.deal
        items.append(f"""<li><span><span class="src-outlet">{external(report.url, report.outlet)}</span><span class="src-deal">{esc(deal.brand_label)}, {esc(deal.status_label.lower())}{date}</span></span>
<span class="src-terms">{esc(report.terms)}</span></li>""")
    return f'<ul class="sources">{"".join(items)}</ul>'


def story_grid_or_empty(ctx: Ctx, posts: list[Post], empty: str) -> str:
    if not posts:
        return f'<p class="fine">{esc(empty)}</p>'
    return f'<div class="story-grid">{"".join(story_card(ctx, p) for p in posts)}</div>'


def render_school(ctx: Ctx, school: School) -> str:
    current, pending = school.current, school.pending
    bits = [f'<a href="{ctx.u(school.conf_url)}">{esc(school.conference)}</a>']
    if current:
        through = f", deal through {current.end.label()}" if current.end else ""
        bits.append(f"Outfitter: {esc(current.brand_label)}{esc(through)}")
    if pending:
        when = f" {pending.start.label()}" if pending.start else ""
        bits.append(f"Moving to {esc(pending.brand_label)}{esc(when)} ({esc(pending.status_label.lower())})")
    crumbs = f'<a href="{ctx.u("tracker/")}">Tracker</a> / {esc(school.name)}'
    body = f"""{page_head("School", school.name, " &middot; ".join(bits), crumbs)}
<div class="wrap layout">
<div class="layout-main">
<section aria-labelledby="history-title"><h2 id="history-title" class="h3-section">Deal history</h2>{timeline_html(school)}</section>
<section id="sources" aria-labelledby="sources-title"><h2 id="sources-title" class="h3-section">Sources</h2>{sources_html(school)}</section>
<section aria-labelledby="school-stories"><h2 id="school-stories" class="h3-section">Stories</h2>{story_grid_or_empty(ctx, school.posts, "No stories about this school yet.")}</section>
</div>
<div class="layout-side">{deal_card_html(ctx, school, heading="h2")}</div>
</div>"""
    desc = f"{school.name} apparel deal history: current outfitter, deal end date, and any reported switch, with sources."
    return layout(ctx, path=school.url, title=school.name, description=desc, body=body, active="tracker")


def mini_table(ctx: Ctx, schools: list[School]) -> str:
    rows = []
    for school in sorted(schools, key=lambda s: s.name.lower()):
        current, pending = school.current, school.pending
        nxt = f"{pending.brand_label} ({pending.status_label.lower()})" if pending else "None"
        rows.append(f"""<tr><td class="school"><a href="{ctx.u(school.url)}">{esc(school.name)}</a></td>
<td class="strong">{esc(current.brand_label if current else 'Not on file')}</td>
<td class="mono">{esc(current.end.label() if current and current.end else 'Not reported')}</td>
<td>{esc(nxt)}</td></tr>""")
    return f"""<div class="table-wrap"><table class="data-table compact"><thead><tr>
<th scope="col"><span class="th">School</span></th><th scope="col"><span class="th">Outfitter now</span></th><th scope="col"><span class="th">Deal ends</span></th><th scope="col"><span class="th">Next</span></th>
</tr></thead><tbody>{"".join(rows)}</tbody></table></div>"""


def mix_line(schools: list[School]) -> str:
    counts: dict[str, int] = {}
    for school in schools:
        key = school.current.brand if school.current else "Not on file"
        counts[key] = counts.get(key, 0) + 1
    return " &middot; ".join(f"{esc(b)} {n}" for b, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def render_conference(ctx: Ctx, conference: str) -> str:
    schools = [s for s in ctx.site.schools.values() if s.conference == conference]
    moves = [m for m in ctx.site.board if m.school.conference == conference]
    posts = [p for p in ctx.site.posts if conference in p.confs]
    move_list = (f'<ul class="rows">{"".join(move_row(ctx, m) for m in moves)}</ul>' if moves
                 else '<p class="fine">No moves on the board for this conference.</p>')
    slug = slugify(conference)
    crumbs = f'<a href="{ctx.u("conferences/")}">Conferences</a> / {esc(conference)}'
    body = f"""{page_head("Conference", conference, f"{len(schools)} program{'s' if len(schools) != 1 else ''} tracked &middot; {mix_line(schools)}", crumbs)}
<div class="wrap stack section section-tight">
<section aria-labelledby="c-schools"><h2 id="c-schools" class="h3-section">Programs</h2>{mini_table(ctx, schools)}</section>
<section aria-labelledby="c-moves"><h2 id="c-moves" class="h3-section">On the board</h2>{move_list}</section>
<section aria-labelledby="c-stories"><h2 id="c-stories" class="h3-section">Stories</h2>{story_grid_or_empty(ctx, posts, "No stories tagged with this conference yet.")}</section>
</div>"""
    return layout(ctx, path=f"conferences/{slug}/", title=conference, description=f"{conference} apparel deals: who outfits each program, when deals end, and who is switching.",
                  body=body, active="conferences")


def render_brand(ctx: Ctx, brand: str) -> str:
    site = ctx.site
    now = [s for s in site.schools.values() if s.current and s.current.brand == brand]
    coming = [m for m in site.board if m.to_brand == brand and m.deal.status in PENDING]
    leaving = [m for m in site.board if m.from_brand == brand and m.deal.status in PENDING]
    past = sorted({s.name for s in site.schools.values() for d in s.deals if d.brand == brand and d.status == "ended"})
    posts = [p for p in site.posts if brand in p.brands]

    def move_items(moves: list[Move], empty: str) -> str:
        if not moves:
            return f'<p class="fine">{esc(empty)}</p>'
        return f'<ul class="rows">{"".join(move_row(ctx, m) for m in moves)}</ul>'

    past_html = (f'<p class="fine">{esc(", ".join(past))}</p>' if past else '<p class="fine">None on file.</p>')
    slug = slugify(brand)
    crumbs = f'<a href="{ctx.u("brands/")}">Brands</a> / {esc(brand)}'
    lede = f"{len(now)} program{'s' if len(now) != 1 else ''} now &middot; {len(coming)} incoming &middot; {len(leaving)} leaving"
    if brand == "Jordan":
        lede += " &middot; A Nike brand, listed here when a football program wears Jordan"
    body = f"""{page_head("Brand", brand, lede, crumbs)}
<div class="wrap stack section section-tight">
<section aria-labelledby="b-now"><h2 id="b-now" class="h3-section">Outfitting now</h2>{mini_table(ctx, now) if now else '<p class="fine">No programs on file.</p>'}</section>
<section aria-labelledby="b-coming"><h2 id="b-coming" class="h3-section">Coming to {esc(brand)}</h2>{move_items(coming, "No incoming programs on the board.")}</section>
<section aria-labelledby="b-leaving"><h2 id="b-leaving" class="h3-section">Leaving {esc(brand)}</h2>{move_items(leaving, "No programs leaving on the board.")}</section>
<section aria-labelledby="b-past"><h2 id="b-past" class="h3-section">Past partners</h2>{past_html}</section>
<section aria-labelledby="b-stories"><h2 id="b-stories" class="h3-section">Stories</h2>{story_grid_or_empty(ctx, posts, "No stories tagged with this brand yet.")}</section>
</div>"""
    return layout(ctx, path=f"brands/{slug}/", title=brand, description=f"Which college football programs {brand} outfits, who is coming, and who is leaving.",
                  body=body, active="brands")


def render_index_tiles(ctx: Ctx, kind: str) -> str:
    site = ctx.site
    tiles = []
    if kind == "conferences":
        for conference in site.conferences:
            schools = [s for s in site.schools.values() if s.conference == conference]
            tiles.append(f'<a class="tile" href="{ctx.u("conferences/" + slugify(conference) + "/")}"><span class="tile-name">{esc(conference)}</span>'
                         f'<span class="tile-meta">{len(schools)} tracked &middot; {mix_line(schools)}</span></a>')
        head = page_head("Conferences", "By conference", "Pick a conference to see every program's outfitter, deal dates, and moves.")
        title, desc = "Conferences", "College football apparel deals by conference."
    else:
        for brand in site.brands:
            now = sum(1 for s in site.schools.values() if s.current and s.current.brand == brand)
            coming = sum(1 for m in site.board if m.to_brand == brand and m.deal.status in PENDING)
            tiles.append(f'<a class="tile" href="{ctx.u("brands/" + slugify(brand) + "/")}"><span class="tile-name">{esc(brand)}</span>'
                         f'<span class="tile-meta">{now} now &middot; {coming} incoming</span></a>')
        head = page_head("Brands", "By brand", "Who each brand outfits today, who is coming, and who is leaving.")
        title, desc = "Brands", "College football apparel deals by brand."
    body = f'{head}<div class="wrap section section-tight"><div class="card-grid">{"".join(tiles)}</div></div>'
    return layout(ctx, path=f"{kind}/", title=title, description=desc, body=body, active=kind)


def render_schools_index(ctx: Ctx) -> str:
    groups = []
    for conference in ctx.site.conferences:
        schools = sorted((s for s in ctx.site.schools.values() if s.conference == conference), key=lambda s: s.name.lower())
        links = "".join(f'<a class="chip" href="{ctx.u(s.url)}">{esc(s.name)}</a>' for s in schools)
        groups.append(f'<section class="field"><h2 class="h3-section">{esc(conference)}</h2><div class="chip-set">{links}</div></section>')
    head = page_head("Schools", "Every school", "Jump to any program&rsquo;s deal history.")
    body = f'{head}<div class="wrap stack section section-tight">{"".join(groups)}</div>'
    return layout(ctx, path="schools/", title="Schools", description="Every tracked college football program.", body=body, active="tracker")


def render_about(ctx: Ctx) -> str:
    email = ctx.site.config.get("contact_email", "")
    contact = f'<p>Email <a href="mailto:{esc(email)}">{esc(email)}</a>.</p>' if email else ""
    body = f"""{page_head("About", ctx.site.about_title)}
<div class="wrap layout"><div class="layout-main prose">{ctx.fix(ctx.site.about_html)}{contact}</div>
<div class="layout-side"><div class="deal-card"><span class="dc-kicker">The ladder</span>{ladder_html("reported")}
<p class="dc-note">Every deal sits on one rung. This one is at Reported.</p></div></div></div>"""
    return layout(ctx, path="about/", title="About", description=ctx.site.about_description or "About Field Drip.", body=body, active="about")


def render_tips(ctx: Ctx) -> str:
    body = f"""{page_head("Tips", "Send a tip", "Heard about a deal, an extension, or a uniform switch? Send it here.")}
<div class="wrap layout"><div class="layout-main prose">
<p>Tips are how the board finds out first. Tell us the school, the brand, and what you heard, and include a link to a story, release, or public record if you have one.</p>
<p>Nothing goes on the site from a tip alone. Every deal still needs a published source, and a tip with a link gets checked fastest. We don&rsquo;t publish your name or contact details.</p>
<div class="hero-actions">{tip_actions(ctx, light=True)}</div>
</div></div>"""
    return layout(ctx, path="tips/", title="Send a tip", description="Send Field Drip a tip about a college football apparel deal.", body=body, active="")


def render_404(ctx: Ctx) -> str:
    body = f"""{page_head("Not found", "Wrong field", "That page isn't here. It may have moved, or the address has a typo.")}
<div class="wrap section section-tight"><a class="btn btn-green" href="{ctx.u('')}">Back to the home page</a></div>"""
    return layout(ctx, path="404.html", title="Page not found", description="Page not found.", body=body)


def render_feed(ctx: Ctx) -> str:
    items = []
    for post in ctx.site.posts[:30]:
        published = dt.datetime.combine(post.date, dt.time(12, 0), tzinfo=dt.timezone.utc)
        link = ctx.absolute(post.url)
        items.append(f"""<item><title>{esc(post.title)}</title><link>{esc(link)}</link><guid>{esc(link)}</guid>
<pubDate>{format_datetime(published)}</pubDate><description>{esc(post.dek)}</description></item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>{esc(ctx.title)}</title><link>{esc(ctx.absolute(''))}</link>
<description>{esc(ctx.site.config.get('description', ''))}</description><language>en-us</language>
{"".join(items)}
</channel></rss>
"""


def render_sitemap(ctx: Ctx) -> str:
    urls = "".join(f"<url><loc>{esc(ctx.absolute(p))}</loc></url>" for p in ctx.pages if p != "404.html")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n'


# ---------------------------------------------------------------- writing it all out

def write_page(ctx: Ctx, out: Path, path: str, content: str) -> None:
    target = out / path
    if path == "" or path.endswith("/"):
        target = target / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    ctx.pages.append(path)


def static_version() -> str:
    digest = hashlib.sha1()
    for path in sorted(STATIC.glob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:8]


def normalize_base(base: str) -> str:
    base = (base or "/").strip()
    if not base.startswith("/"):
        base = "/" + base
    if not base.endswith("/"):
        base += "/"
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Field Drip site into _site/.")
    parser.add_argument("--base-path", default="/", help="URL path the site lives under, like /field-drip/")
    parser.add_argument("--site-url", default="", help="Full address of the site, used in the feed and sitemap")
    parser.add_argument("--out", default="_site", help="Output folder")
    parser.add_argument("--today", default="", help="Pretend today is this date (YYYY-MM-DD), for testing")
    parser.add_argument("--check", action="store_true", help="Only check the data and posts; write nothing")
    parser.add_argument("--relative", action="store_true",
                        help="Write relative links so the site opens straight from a folder, no web server needed")
    args = parser.parse_args()

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    base_path = normalize_base(args.base_path)
    site = load(today, base_path)

    for line in WARNINGS:
        print(f"WARNING  {line}")
    if WAITING:
        print(f"\n{len(WAITING)} waiting for a source. They stay off the site until data/reports.csv has a row for each:")
        for line in WAITING:
            print(f"  {line}")
    if ERRORS:
        print(f"\nThe build stopped. Fix {'this' if len(ERRORS) == 1 else 'these ' + str(len(ERRORS))} and commit again:\n")
        for line in ERRORS:
            print(f"  {line}")
        print("\nThe live site has not changed.")
        return 1
    if args.check:
        print(f"\nEverything checks out: {len(site.schools)} schools on the site, {len(site.hidden_schools)} waiting for a source, "
              f"{len(site.posts)} posts.")
        return 0

    ctx = Ctx(site, base_path, args.site_url, today, static_version(), relative=args.relative)
    out = (ROOT / args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(STATIC, out / "static")

    def emit(path: str, render, *extra) -> None:
        ctx.current = path
        write_page(ctx, out, path, render(ctx, *extra))

    emit("", render_home)
    emit("stories/", render_stories_index)
    for post in site.posts:
        emit(post.url, render_post, post)
    emit("tracker/", render_tracker)
    emit("schools/", render_schools_index)
    for school in site.schools.values():
        emit(school.url, render_school, school)
    emit("conferences/", render_index_tiles, "conferences")
    for conference in site.conferences:
        emit(f"conferences/{slugify(conference)}/", render_conference, conference)
    emit("brands/", render_index_tiles, "brands")
    for brand in site.brands:
        emit(f"brands/{slugify(brand)}/", render_brand, brand)
    emit("about/", render_about)
    if has_tip_line(ctx):
        emit("tips/", render_tips)
    emit("404.html", render_404)
    ctx.current = ""
    (out / "feed.xml").write_text(render_feed(ctx), encoding="utf-8")
    (out / "sitemap.xml").write_text(render_sitemap(ctx), encoding="utf-8")
    sitemap = f"Sitemap: {ctx.absolute('sitemap.xml')}\n" if ctx.site_url else ""
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n{sitemap}", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    print(f"\nBuilt {len(ctx.pages)} pages: {len(site.schools)} schools on the site, {len(site.hidden_schools)} waiting for a source, "
          f"{len(site.posts)} posts, {len(site.board)} moves on the board.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
