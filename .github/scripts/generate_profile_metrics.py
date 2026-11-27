#!/usr/bin/env python3
"""Self-hosted GitHub profile metrics generator (zero external dependencies).

Fetches live user, non-forked repository, language byte, and 365-day
contribution telemetry from GitHub APIs and renders dark/light architectural
SVG summary cards into the configured output directory.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_VIEWBOX = "0 0 900 220"
USER_AGENT = "cid-moosa-profile-metrics/1.0"


@dataclass(frozen=True)
class ThemePalette:
    """Color tokens for an architectural metrics card theme."""

    name: str
    canvas: str
    surface: str
    hairline: str
    primary: str
    secondary: str
    muted: str
    accent: str
    track_bg: str
    bar_colors: tuple[str, ...]


DARK_THEME = ThemePalette(
    name="dark",
    canvas="#0d1117",
    surface="#161b22",
    hairline="#30363d",
    primary="#f0f6fc",
    secondary="#8b949e",
    muted="#6e7681",
    accent="#38bdf8",
    track_bg="#21262d",
    bar_colors=("#38bdf8", "#e6edf3", "#94a3b8", "#0284c7", "#64748b", "#30363d"),
)

LIGHT_THEME = ThemePalette(
    name="light",
    canvas="#ffffff",
    surface="#f6f8fa",
    hairline="#d0d7de",
    primary="#1f2328",
    secondary="#59636e",
    muted="#8c959f",
    accent="#0284c7",
    track_bg="#e6ebf1",
    bar_colors=("#0284c7", "#1f2328", "#59636e", "#38bdf8", "#8c959f", "#d0d7de"),
)

FOCUS_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "Systems & Forensics",
        ("go", "shell"),
        (
            "forensic",
            "dfir",
            "grub",
            "bootloader",
            "ntfs",
            "fat32",
            "disk-imaging",
            "memory-forensics",
            "kernel",
            "rtl8188eus",
            "kali",
            "linux",
            "driver",
            "polkit",
            "qemu",
            "reverse-engineering",
            "prodiscover",
        ),
    ),
    (
        "Android & USB / RF",
        ("kotlin",),
        (
            "android",
            "jetpack-compose",
            "usb-otg",
            "infrared",
            "ir-blaster",
            "wardrive",
            "p2p",
            "walkie-talkie",
            "telephony",
            "room",
            "material3",
            "tiqiaa",
        ),
    ),
    (
        "Embedded & Wireless",
        ("c++", "c", "lua"),
        (
            "esp32",
            "esp32-s3",
            "nrf24",
            "nrf24l01",
            "rf-transceiver",
            "platformio",
            "arduino",
            "wps",
            "wifi-scanner",
            "captive-portal",
            "ws2812",
            "firmware",
            "serial",
            "magiclantern",
        ),
    ),
    (
        "Tooling & Automation",
        ("python", "java"),
        (
            "cli",
            "tui",
            "automation",
            "installer",
            "package manager",
            "downloader",
            "daemon",
            "lab-record",
            "ai-agent",
            "ooxml",
            "docx",
            "pdf-generation",
            "gcode",
            "drawbot",
            "flasher",
            "yt-dlp",
            "voice assistant",
        ),
    ),
    (
        "Web & Graphics",
        ("javascript", "typescript", "html", "css"),
        (
            "webassembly",
            "wasm",
            "webgpu",
            "threejs",
            "react",
            "vite",
            "webgl",
            "browser-games",
            "webui",
        ),
    ),
)


class RateLimitError(RuntimeError):
    """Raised when the GitHub API rate-limits an endpoint."""


@dataclass(frozen=True)
class LanguageShare:
    """Single segment in the top languages breakdown."""

    name: str
    byte_count: int
    percent: float


@dataclass(frozen=True)
class ContributionSummary:
    """Trailing 365-day contribution and active cadence summary."""

    total_365d: int | None
    active_days: int | None
    total_days: int
    source: str


@dataclass(frozen=True)
class FocusArea:
    """Derived engineering focus domain across non-forked repos."""

    label: str
    repo_count: int


@dataclass(frozen=True)
class ProfileMetrics:
    """Normalized telemetry rendered into the profile metrics SVGs."""

    username: str
    public_repos: int
    original_repos: int
    forks_excluded: int
    total_stars: int
    followers: int
    tenure_value: str
    tenure_subtext: str
    contributions: ContributionSummary
    languages: tuple[LanguageShare, ...]
    total_language_bytes: int
    language_source: str
    focus_areas: tuple[FocusArea, ...]
    synced_date: str


def esc(value: object) -> str:
    """Escape text for safe XML attribute and element interpolation."""
    return html.escape(str(value), quote=True)


def resolve_token(explicit_token: str | None, allow_gh_cli: bool = True) -> str | None:
    """Resolve a GitHub token from CLI argument, env vars, or local gh CLI."""
    if explicit_token and explicit_token.strip():
        return explicit_token.strip()
    for env_key in ("GITHUB_TOKEN", "GH_TOKEN"):
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            return env_val
    if not allow_gh_cli:
        return None
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = proc.stdout.strip()
    return token if proc.returncode == 0 and token else None


def build_headers(token: str | None, accept: str = "application/vnd.github+json") -> dict[str, str]:
    """Construct standard GitHub HTTP request headers."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _check_http_error(exc: urllib.error.HTTPError) -> None:
    """Convert HTTP 403/429 rate-limit responses into RateLimitError."""
    remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
    if exc.code in (403, 429) or remaining == "0":
        raise RateLimitError(f"GitHub API rate limit hit (HTTP {exc.code})") from exc
    raise RuntimeError(f"GitHub HTTP {exc.code}: {exc.reason}") from exc


def http_get_json(url: str, headers: dict[str, str], timeout: int = 15) -> tuple[Any, dict[str, str]]:
    """Perform a GET request and parse the JSON response plus headers."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return json.loads(raw), resp_headers
    except urllib.error.HTTPError as exc:
        _check_http_error(exc)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc
    raise AssertionError("unreachable")


def http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 15) -> Any:
    """Perform a JSON POST request and return parsed JSON."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        _check_http_error(exc)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"POST request failed for {url}: {exc}") from exc


def http_get_text(url: str, headers: dict[str, str], timeout: int = 15) -> str:
    """Perform a GET request and return decoded UTF-8 response text."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Text GET failed for {url}: {exc}") from exc


def fetch_user_profile(username: str, token: str | None) -> dict[str, Any]:
    """Fetch user profile metadata from /users/{username}."""
    safe_user = urllib.parse.quote(username)
    data, _ = http_get_json(f"https://api.github.com/users/{safe_user}", build_headers(token))
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected user profile payload type")
    return data


def fetch_all_repos(username: str, token: str | None) -> list[dict[str, Any]]:
    """Fetch all public repositories for username using paginated REST calls."""
    safe_user = urllib.parse.quote(username)
    headers = build_headers(token)
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        url = f"https://api.github.com/users/{safe_user}/repos?per_page=100&sort=updated&page={page}"
        batch, resp_headers = http_get_json(url, headers)
        if not isinstance(batch, list):
            raise RuntimeError("Unexpected repository list payload type")
        if not batch:
            break
        repos.extend(item for item in batch if isinstance(item, dict))
        link = resp_headers.get("link", "")
        if len(batch) < 100 and 'rel="next"' not in link:
            break
        page += 1
    return repos


def fetch_repo_languages(repo: dict[str, Any], username: str, token: str | None) -> dict[str, int]:
    """Fetch per-language byte counts for a single repository."""
    lang_url = repo.get("languages_url")
    if not isinstance(lang_url, str) or not lang_url.startswith("https://"):
        safe_user = urllib.parse.quote(username)
        safe_name = urllib.parse.quote(str(repo.get("name", "")))
        lang_url = f"https://api.github.com/repos/{safe_user}/{safe_name}/languages"
    try:
        payload, _ = http_get_json(lang_url, build_headers(token))
    except RuntimeError as exc:
        if isinstance(exc, RateLimitError):
            raise
        if "HTTP 404" in str(exc):
            return {}
        raise
    if not isinstance(payload, dict):
        return {}
    return {str(k): int(v) for k, v in payload.items() if int(v) > 0}


def aggregate_languages_from_metadata(repos: list[dict[str, Any]]) -> dict[str, int]:
    """Fallback language weighting using repository primary language and size."""
    has_positive_size = any(int(r.get("size") or 0) > 0 for r in repos)
    totals: Counter[str] = Counter()
    for repo in repos:
        lang = repo.get("language")
        if not isinstance(lang, str) or not lang.strip():
            continue
        size_kb = int(repo.get("size") or 0)
        weight = max(size_kb, 0) * 1024 if has_positive_size else 1
        if weight > 0:
            totals[lang.strip()] += weight
    return dict(totals)


def aggregate_languages(
    repos: list[dict[str, Any]],
    username: str,
    token: str | None,
    mock_languages: dict[str, dict[str, int]] | None = None,
) -> tuple[dict[str, int], str]:
    """Aggregate language bytes across non-forked repos with rate-limit fallback."""
    if mock_languages is not None:
        return _aggregate_mock_languages(repos, mock_languages)
    totals: Counter[str] = Counter()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetch_repo_languages, repo, username, token) for repo in repos]
            for fut in as_completed(futures):
                totals.update(fut.result())
        if totals:
            return dict(totals), "bytes"
    except (RateLimitError, RuntimeError) as exc:
        print(f"[WARN] Falling back to repo size/language metadata: {exc}", file=sys.stderr)
    return aggregate_languages_from_metadata(repos), "repo_size"


def _aggregate_mock_languages(
    repos: list[dict[str, Any]],
    mock_languages: dict[str, dict[str, int]],
) -> tuple[dict[str, int], str]:
    """Aggregate mock /languages mappings for non-forked repositories."""
    totals: Counter[str] = Counter()
    matched = False
    for repo in repos:
        name = str(repo.get("name", ""))
        entry = mock_languages.get(name)
        if isinstance(entry, dict) and entry:
            matched = True
            for lang, count in entry.items():
                if int(count) > 0:
                    totals[str(lang)] += int(count)
    if matched and totals:
        return dict(totals), "bytes"
    return aggregate_languages_from_metadata(repos), "repo_size"


class ContributionHTMLParser(HTMLParser):
    """HTML parser extracting contribution total and active day counts."""

    def __init__(self) -> None:
        super().__init__()
        self.active_days = 0
        self.total_days = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "td":
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if "data-date" in attr_map and "data-level" in attr_map:
            self.total_days += 1
            try:
                if int(attr_map["data-level"]) > 0:
                    self.active_days += 1
            except ValueError:
                pass

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def extract_total(self) -> int | None:
        """Parse total trailing-year contributions from collected text."""
        joined = " ".join(self._chunks)
        match = re.search(r"([0-9,]+)\s+contributions?\s+in\s+(?:the last year|\d{4})", joined)
        if not match:
            return None
        return int(match.group(1).replace(",", ""))


def fetch_contributions_graphql(username: str, token: str | None) -> ContributionSummary | None:
    """Tier 1: Query GitHub GraphQL contributionsCollection when token exists."""
    if not token:
        return None
    query = (
        "query($login: String!) { user(login: $login) { contributionsCollection { "
        "contributionCalendar { totalContributions weeks { contributionDays { "
        "date contributionCount } } } } } }"
    )
    headers = build_headers(token)
    headers["Content-Type"] = "application/json"
    try:
        data = http_post_json(
            "https://api.github.com/graphql",
            {"query": query, "variables": {"login": username}},
            headers,
        )
        cal = data.get("data", {}).get("user", {}).get("contributionsCollection", {}).get("contributionCalendar")
        if not isinstance(cal, dict) or "totalContributions" not in cal:
            return None
        days = [d for w in cal.get("weeks", []) for d in w.get("contributionDays", [])]
        active = sum(1 for d in days if int(d.get("contributionCount", 0)) > 0)
        return ContributionSummary(
            total_365d=int(cal["totalContributions"]),
            active_days=active,
            total_days=len(days) or 365,
            source="graphql",
        )
    except (RuntimeError, ValueError, TypeError, AttributeError) as exc:
        print(f"[WARN] GraphQL contributions failed: {exc}", file=sys.stderr)
        return None


def fetch_contributions_html(username: str) -> ContributionSummary | None:
    """Tier 2: Scrape public GitHub contributions calendar HTML."""
    safe_user = urllib.parse.quote(username)
    url = f"https://github.com/users/{safe_user}/contributions"
    try:
        body = http_get_text(url, {"User-Agent": "Mozilla/5.0"})
        parser = ContributionHTMLParser()
        parser.feed(body)
        total = parser.extract_total()
        if total is None:
            return None
        active = parser.active_days if parser.total_days > 0 else None
        return ContributionSummary(
            total_365d=total,
            active_days=active,
            total_days=parser.total_days or 365,
            source="html",
        )
    except (RuntimeError, ValueError) as exc:
        print(f"[WARN] HTML contributions scrape failed: {exc}", file=sys.stderr)
        return None


def fetch_contributions_jogruber(username: str) -> ContributionSummary | None:
    """Tier 3: Query github-contributions-api.jogruber.de JSON fallback."""
    safe_user = urllib.parse.quote(username)
    url = f"https://github-contributions-api.jogruber.de/v4/{safe_user}?y=last"
    try:
        data, _ = http_get_json(url, {"User-Agent": USER_AGENT})
        if not isinstance(data, dict):
            return None
        total_raw = data.get("total", {}).get("lastYear")
        if total_raw is None:
            return None
        entries = data.get("contributions", [])
        active = sum(1 for item in entries if int(item.get("count", 0)) > 0) if isinstance(entries, list) else None
        return ContributionSummary(
            total_365d=int(total_raw),
            active_days=active,
            total_days=len(entries) or 365,
            source="jogruber",
        )
    except (RuntimeError, ValueError, TypeError, AttributeError) as exc:
        print(f"[WARN] Jogruber contributions API failed: {exc}", file=sys.stderr)
        return None


def fetch_contributions(username: str, token: str | None) -> ContributionSummary:
    """Resolve trailing 365d contributions via GraphQL -> HTML -> Jogruber fallback."""
    for fetcher in (
        lambda: fetch_contributions_graphql(username, token),
        lambda: fetch_contributions_html(username),
        lambda: fetch_contributions_jogruber(username),
    ):
        result = fetcher()
        if result is not None:
            return result
    return ContributionSummary(total_365d=None, active_days=None, total_days=365, source="unavailable")


def compute_account_tenure(created_at: str | None, now: datetime) -> tuple[str, str]:
    """Compute human-readable account tenure and creation year subtext."""
    if not created_at:
        return ("—", "unknown")
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return ("—", "unknown")
    months = (now.year - created.year) * 12 + (now.month - created.month)
    if now.day < created.day:
        months -= 1
    months = max(months, 0)
    if months == 0:
        days = max((now.date() - created.date()).days, 0)
        return (f"{days}d", f"est. {created.year}")
    years, rem_months = divmod(months, 12)
    if years == 0:
        return (f"{rem_months}m", f"est. {created.year}")
    label = f"{years}y {rem_months}m" if rem_months else f"{years}y"
    return (label, f"est. {created.year}")


def summarize_languages(lang_bytes: dict[str, int], top_n: int = 5) -> list[LanguageShare]:
    """Produce top-N languages plus Other with accurate percentage shares."""
    filtered = [(name, val) for name, val in lang_bytes.items() if val > 0]
    if not filtered:
        return []
    filtered.sort(key=lambda item: (-item[1], item[0].lower()))
    total = sum(val for _, val in filtered)
    top_items = filtered[:top_n]
    shares = [
        LanguageShare(name=name, byte_count=val, percent=round(val / total * 100.0, 1))
        for name, val in top_items
    ]
    other_bytes = sum(val for _, val in filtered[top_n:])
    if other_bytes > 0:
        shares.append(
            LanguageShare(name="Other", byte_count=other_bytes, percent=round(other_bytes / total * 100.0, 1))
        )
    return shares


def _repo_corpus(repo: dict[str, Any]) -> tuple[str, str]:
    """Extract normalized primary language and searchable text for a repo."""
    lang = str(repo.get("language") or "").strip().lower()
    topics = repo.get("topics") or []
    topic_str = " ".join(str(t) for t in topics if t)
    parts = [str(repo.get("name") or ""), str(repo.get("description") or ""), topic_str, lang]
    return lang, " ".join(parts).lower()


def derive_focus_areas(repos: list[dict[str, Any]], max_areas: int = 4) -> list[FocusArea]:
    """Derive engineering focus domains dynamically from repo metadata."""
    counts: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    for repo in repos:
        lang, corpus = _repo_corpus(repo)
        for label, lang_markers, keywords in FOCUS_RULES:
            kw_hits = sum(1 for kw in keywords if kw in corpus)
            lang_hit = 1 if lang in lang_markers else 0
            if kw_hits > 0 or (lang_hit > 0 and kw_hits == 0 and lang in ("kotlin", "c++")):
                counts[label] += 1
                hits[label] += kw_hits + lang_hit
    if counts:
        ordered = sorted(counts.keys(), key=lambda lbl: (-counts[lbl], -hits[lbl], lbl))
        return [FocusArea(label=lbl, repo_count=counts[lbl]) for lbl in ordered[:max_areas]]
    lang_counts = Counter(str(r["language"]).strip() for r in repos if r.get("language"))
    if lang_counts:
        return [FocusArea(label=f"{lang} Engineering", repo_count=cnt) for lang, cnt in lang_counts.most_common(max_areas)]
    return [FocusArea(label="Open Source Software", repo_count=len(repos))]


def build_metrics(
    username: str,
    user: dict[str, Any],
    repos: list[dict[str, Any]],
    lang_bytes: dict[str, int],
    lang_source: str,
    contribs: ContributionSummary,
    now: datetime,
) -> ProfileMetrics:
    """Assemble normalized ProfileMetrics from non-forked GitHub records."""
    original = [r for r in repos if not r.get("fork")]
    forks_excluded = len(repos) - len(original)
    public_repos = int(user.get("public_repos") or len(repos))
    total_stars = sum(int(r.get("stargazers_count") or 0) for r in original)
    followers = int(user.get("followers") or 0)
    tenure_val, tenure_sub = compute_account_tenure(user.get("created_at"), now)
    shares = tuple(summarize_languages(lang_bytes, top_n=5))
    focus = tuple(derive_focus_areas(original, max_areas=4))
    return ProfileMetrics(
        username=username,
        public_repos=public_repos,
        original_repos=len(original),
        forks_excluded=forks_excluded,
        total_stars=total_stars,
        followers=followers,
        tenure_value=tenure_val,
        tenure_subtext=tenure_sub,
        contributions=contribs,
        languages=shares,
        total_language_bytes=sum(lang_bytes.values()),
        language_source=lang_source,
        focus_areas=focus,
        synced_date=now.strftime("%Y-%m-%d"),
    )


def format_code_volume(total_bytes: int, lang_source: str) -> str:
    """Format code volume header label for the language breakdown band."""
    if total_bytes <= 0:
        return "NO LANGUAGE DATA"
    if lang_source == "repo_size":
        return f"{total_bytes / 1_000_000:.2f} MB EST &#183; REPO SIZE"
    if total_bytes >= 1_000_000:
        return f"{total_bytes / 1_000_000:.2f} MB CODE &#183; NON-FORKED"
    return f"{total_bytes / 1_000:.1f} KB CODE &#183; NON-FORKED"


def compute_bar_widths(shares: tuple[LanguageShare, ...], total_width: float = 844.0) -> list[float]:
    """Compute pixel widths for language bar segments summing exactly to total_width."""
    total_bytes = sum(s.byte_count for s in shares)
    if not shares or total_bytes <= 0:
        return []
    raw = [s.byte_count / total_bytes * total_width for s in shares]
    widths = [max(round(w, 2), 1.0) for w in raw]
    diff = round(total_width - sum(widths), 2)
    largest_idx = max(range(len(widths)), key=widths.__getitem__)
    widths[largest_idx] = round(max(widths[largest_idx] + diff, 1.0), 2)
    return widths


def render_svg_style(theme: ThemePalette) -> str:
    """Render theme-scoped CSS typography and reduced-motion reveal rules."""
    sans = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    mono = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    s = f"#metrics-{theme.name}"
    return (
        "<style>\n"
        f"    {s} .hdr {{ font-family: {mono}; font-size: 11px; font-weight: 600; letter-spacing: 1.5px; fill: {theme.secondary}; }}\n"
        f"    {s} .meta {{ font-family: {mono}; font-size: 10px; font-weight: 500; letter-spacing: 1px; fill: {theme.muted}; }}\n"
        f"    {s} .kpi-lbl {{ font-family: {mono}; font-size: 10px; font-weight: 600; letter-spacing: 1.2px; fill: {theme.muted}; }}\n"
        f"    {s} .kpi-val {{ font-family: {sans}; font-size: 24px; font-weight: 700; fill: {theme.primary}; }}\n"
        f"    {s} .kpi-sub {{ font-family: {mono}; font-size: 11px; font-weight: 500; fill: {theme.secondary}; }}\n"
        f"    {s} .kpi-acc {{ font-family: {mono}; font-size: 11px; font-weight: 600; fill: {theme.accent}; }}\n"
        f"    {s} .leg-name {{ font-family: {sans}; font-size: 11px; font-weight: 600; fill: {theme.primary}; }}\n"
        f"    {s} .leg-pct {{ font-family: {mono}; font-size: 10.5px; font-weight: 500; fill: {theme.secondary}; }}\n"
        f"    {s} .tag-txt {{ font-family: {sans}; font-size: 11px; font-weight: 500; fill: {theme.primary}; }}\n"
        f"    {s} .bar-reveal {{ transform-origin: 28px 142px; animation: barReveal-{theme.name} 0.85s cubic-bezier(0.22, 1, 0.36, 1); }}\n"
        f"    {s} .pulse-dot {{ animation: softPulse-{theme.name} 3.2s ease-in-out infinite; }}\n"
        f"    @keyframes barReveal-{theme.name} {{ from {{ transform: scaleX(0.08); opacity: 0.35; }} to {{ transform: scaleX(1); opacity: 1; }} }}\n"
        f"    @keyframes softPulse-{theme.name} {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.45; }} }}\n"
        f"    @media (prefers-reduced-motion: reduce) {{ {s} .bar-reveal, {s} .pulse-dot {{ animation: none; transform: none; opacity: 1; }} }}\n"
        "  </style>"
    )


def render_header_band(metrics: ProfileMetrics, theme: ThemePalette) -> str:
    """Render top architectural header bar with handle and sync metadata."""
    handle = esc(metrics.username)
    sync = esc(metrics.synced_date)
    return (
        f'  <circle class="pulse-dot" cx="31" cy="20" r="3" fill="{theme.accent}"/>\n'
        f'  <text class="hdr" x="42" y="24">TELEMETRY // @{handle}</text>\n'
        f'  <text class="meta" x="872" y="24" text-anchor="end">NON-FORKED REPOS &#183; SYNCED {sync}</text>\n'
        f'  <line x1="1" y1="40" x2="899" y2="40" stroke="{theme.hairline}" stroke-width="1"/>'
    )


def render_kpi_band(metrics: ProfileMetrics, theme: ThemePalette) -> str:
    """Render 4-column KPI strip covering repos, stars, activity, and tenure."""
    repo_sub = f"of {metrics.public_repos} public" if metrics.forks_excluded == 0 else f"{metrics.forks_excluded} forks excl."
    contrib_val = str(metrics.contributions.total_365d) if metrics.contributions.total_365d is not None else "—"
    if metrics.contributions.active_days is not None:
        contrib_sub = f"{metrics.contributions.active_days} active days"
    else:
        contrib_sub = "calendar unavailable"
    cells = (
        (28, "ORIGINAL REPOS", str(metrics.original_repos), repo_sub, "kpi-sub"),
        (253, "TOTAL STARS", str(metrics.total_stars), f"{metrics.followers} followers", "kpi-sub"),
        (478, "365D ACTIVITY", contrib_val, contrib_sub, "kpi-acc"),
        (703, "ACCOUNT TENURE", metrics.tenure_value, metrics.tenure_subtext, "kpi-sub"),
    )
    parts = [
        f'  <line x1="{dx}" y1="40" x2="{dx}" y2="112" stroke="{theme.hairline}" stroke-width="1"/>'
        for dx in (225, 450, 675)
    ]
    for x, label, val, sub, sub_cls in cells:
        parts.append(
            f'  <text class="kpi-lbl" x="{x}" y="62">{esc(label)}</text>\n'
            f'  <text x="{x}" y="92"><tspan class="kpi-val">{esc(val)}</tspan>'
            f'<tspan class="{sub_cls}" dx="8">{esc(sub)}</tspan></text>'
        )
    parts.append(f'  <line x1="1" y1="112" x2="899" y2="112" stroke="{theme.hairline}" stroke-width="1"/>')
    return "\n".join(parts)


def render_language_band(metrics: ProfileMetrics, theme: ThemePalette) -> str:
    """Render proportional language bar and legend row."""
    vol_label = format_code_volume(metrics.total_language_bytes, metrics.language_source)
    lines = [
        f'  <text class="kpi-lbl" x="28" y="130">LANGUAGE DISTRIBUTION</text>',
        f'  <text class="meta" x="872" y="130" text-anchor="end">{vol_label}</text>',
        f'  <rect x="28" y="138" width="844" height="8" rx="4" fill="{theme.track_bg}"/>',
    ]
    widths = compute_bar_widths(metrics.languages, 844.0)
    if widths:
        rects = []
        cursor = 28.0
        for idx, (share, seg_w) in enumerate(zip(metrics.languages, widths)):
            color = theme.bar_colors[min(idx, len(theme.bar_colors) - 1)]
            rects.append(
                f'      <rect x="{cursor:.2f}" y="138" width="{seg_w:.2f}" height="8" '
                f'fill="{color}" stroke="{theme.surface}" stroke-width="1.2"/>'
            )
            cursor += seg_w
        lines.append('  <g clip-path="url(#langBarClip)">\n    <g class="bar-reveal">\n' + "\n".join(rects) + "\n    </g>\n  </g>")
    lines.extend(_render_language_legend(metrics.languages, theme))
    lines.append(f'  <line x1="1" y1="176" x2="899" y2="176" stroke="{theme.hairline}" stroke-width="1"/>')
    return "\n".join(lines)


def _render_language_legend(shares: tuple[LanguageShare, ...], theme: ThemePalette) -> list[str]:
    """Render inline language swatches and percentages beneath the bar."""
    if not shares:
        return [f'  <text class="meta" x="28" y="162">No language bytes recorded across non-forked repositories.</text>']
    step = min(142.0, 844.0 / len(shares))
    items: list[str] = []
    for idx, share in enumerate(shares):
        x = 28.0 + idx * step
        color = theme.bar_colors[min(idx, len(theme.bar_colors) - 1)]
        label_xml = esc(share.name) if len(share.name) <= 12 else f"{esc(share.name[:11])}&#8230;"
        items.append(
            f'  <rect x="{x:.1f}" y="154" width="8" height="8" rx="2" fill="{color}"/>\n'
            f'  <text x="{x + 13:.1f}" y="162"><tspan class="leg-name">{label_xml}</tspan>'
            f'<tspan class="leg-pct" dx="4">{share.percent:.1f}%</tspan></text>'
        )
    return items


def render_focus_band(metrics: ProfileMetrics, theme: ThemePalette) -> str:
    """Render bottom row of dynamically derived Core Focus Area tags."""
    parts = [f'  <text class="kpi-lbl" x="28" y="201">FOCUS</text>']
    cursor_x = 84
    for area in metrics.focus_areas:
        pill_w = max(96, round(len(area.label) * 6.6 + 34))
        if cursor_x + pill_w > 872:
            break
        parts.append(
            f'  <rect x="{cursor_x}" y="186" width="{pill_w}" height="22" rx="5" '
            f'fill="{theme.canvas}" stroke="{theme.hairline}" stroke-width="1"/>\n'
            f'  <circle cx="{cursor_x + 11}" cy="197" r="2.2" fill="{theme.accent}"/>\n'
            f'  <text class="tag-txt" x="{cursor_x + 20}" y="201">{esc(area.label)}</text>'
        )
        cursor_x += pill_w + 10
    return "\n".join(parts)


def render_metrics_svg(metrics: ProfileMetrics, theme: ThemePalette) -> str:
    """Render a complete, GitHub-safe architectural SVG card for the given theme."""
    svg = (
        f'<svg id="metrics-{esc(theme.name)}" xmlns="http://www.w3.org/2000/svg" viewBox="{DEFAULT_VIEWBOX}" width="100%" '
        f'role="img" aria-label="GitHub profile metrics for {esc(metrics.username)}">\n'
        f"  <title>{esc(metrics.username)} GitHub Telemetry ({esc(theme.name)})</title>\n"
        f"  <defs>\n"
        f'    <clipPath id="langBarClip"><rect x="28" y="138" width="844" height="8" rx="4"/></clipPath>\n'
        f"  </defs>\n"
        f"  {render_svg_style(theme)}\n"
        f'  <rect x="0" y="0" width="900" height="220" fill="{theme.canvas}"/>\n'
        f'  <rect x="1" y="1" width="898" height="218" rx="10" fill="{theme.surface}" '
        f'stroke="{theme.hairline}" stroke-width="1"/>\n'
        f"{render_header_band(metrics, theme)}\n"
        f"{render_kpi_band(metrics, theme)}\n"
        f"{render_language_band(metrics, theme)}\n"
        f"{render_focus_band(metrics, theme)}\n"
        "</svg>\n"
    )
    ET.fromstring(svg)
    return svg


def load_mock_data(
    mock_path: Path,
    username: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, int]] | None, ContributionSummary | None, datetime]:
    """Load deterministic mock GitHub payloads from a JSON file."""
    data = json.loads(mock_path.read_text(encoding="utf-8"))
    user = data.get("user") or {"login": username, "public_repos": len(data.get("repos", [])), "followers": 0}
    repos = data.get("repos") or []
    mock_langs = data.get("languages_by_repo")
    contrib_raw = data.get("contributions")
    contribs: ContributionSummary | None = None
    if isinstance(contrib_raw, int):
        contribs = ContributionSummary(total_365d=contrib_raw, active_days=None, total_days=365, source="mock")
    elif isinstance(contrib_raw, dict):
        contribs = ContributionSummary(
            total_365d=contrib_raw.get("total_365d"),
            active_days=contrib_raw.get("active_days"),
            total_days=int(contrib_raw.get("total_days") or 365),
            source="mock",
        )
    gen_at = data.get("generated_at")
    now = datetime.fromisoformat(gen_at.replace("Z", "+00:00")) if isinstance(gen_at, str) else datetime.now(timezone.utc)
    return user, repos, mock_langs, contribs, now


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for profile metrics SVG generation."""
    default_user = os.environ.get("GITHUB_REPOSITORY_OWNER") or "cid-moosa"
    parser = argparse.ArgumentParser(description="Generate dark and light profile metrics SVGs.")
    parser.add_argument("--username", default=default_user, help="GitHub username (default: GITHUB_REPOSITORY_OWNER or cid-moosa)")
    parser.add_argument("--token", default=None, help="GitHub API token (defaults to GITHUB_TOKEN / gh auth token)")
    parser.add_argument("--out-dir", default="assets", help="Output directory for metrics-dark.svg and metrics-light.svg")
    parser.add_argument("--mock-json", default=None, help="Path to mock JSON fixture for deterministic offline generation")
    return parser.parse_args(argv)


def generate_metrics(args: argparse.Namespace) -> tuple[ProfileMetrics, Path, Path]:
    """Fetch or load profile data and write metrics-dark.svg and metrics-light.svg."""
    if args.mock_json:
        user, repos, mock_langs, contribs, now = load_mock_data(Path(args.mock_json), args.username)
        token = None
    else:
        token = resolve_token(args.token, allow_gh_cli=True)
        user = fetch_user_profile(args.username, token)
        repos = fetch_all_repos(args.username, token)
        mock_langs = None
        contribs = None
        now = datetime.now(timezone.utc)

    original = [r for r in repos if not r.get("fork")]
    lang_bytes, lang_source = aggregate_languages(original, args.username, token, mock_languages=mock_langs)
    if contribs is None:
        contribs = fetch_contributions(args.username, token)
    metrics = build_metrics(args.username, user, repos, lang_bytes, lang_source, contribs, now)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dark_path = out_dir / "metrics-dark.svg"
    light_path = out_dir / "metrics-light.svg"
    dark_path.write_text(render_metrics_svg(metrics, DARK_THEME), encoding="utf-8")
    light_path.write_text(render_metrics_svg(metrics, LIGHT_THEME), encoding="utf-8")
    return metrics, dark_path, light_path


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    metrics, dark_path, light_path = generate_metrics(args)
    print(
        f"[OK] {metrics.username}: {metrics.original_repos} original repos, "
        f"{metrics.total_stars} stars, {metrics.contributions.total_365d} contribs "
        f"({metrics.contributions.source}), langs={metrics.language_source} -> "
        f"{dark_path}, {light_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
