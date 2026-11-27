#!/usr/bin/env python3
"""Verification and unit test suite for profile metrics and hero SVG assets.

Zero external dependencies (Python 3.11+ standard library only).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import generate_profile_metrics as gpm  # noqa: E402

SVG_NS = "http://www.w3.org/2000/svg"
METRICS_REQUIRED_LABELS = (
    "ORIGINAL REPOS",
    "TOTAL STARS",
    "365D ACTIVITY",
    "ACCOUNT TENURE",
    "LANGUAGE DISTRIBUTION",
    "FOCUS",
)


def _local_tag(tag: str) -> str:
    """Strip XML namespace prefix from an ElementTree tag name."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def verify_base_svg(
    svg_path: Path,
    expected_viewbox: str | None = None,
    min_bytes: int = 500,
) -> tuple[list[str], ET.Element | None, str]:
    """Validate XML well-formedness, viewBox, and GitHub camo security rules."""
    errors: list[str] = []
    if not svg_path.exists():
        return [f"Missing SVG file: {svg_path}"], None, ""
    content = svg_path.read_text(encoding="utf-8")
    if len(content.encode("utf-8")) < min_bytes:
        errors.append(f"SVG file unexpectedly small: {svg_path}")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        return [f"XML parse error in {svg_path}: {exc}"], None, content

    if _local_tag(root.tag) != "svg":
        errors.append(f"Root tag in {svg_path} is '{root.tag}', expected <svg>")
    view_box = root.get("viewBox", "")
    parts = view_box.split()
    if len(parts) != 4:
        errors.append(f"Invalid viewBox '{view_box}' in {svg_path}")
    elif expected_viewbox and view_box != expected_viewbox:
        errors.append(f"Expected viewBox '{expected_viewbox}', got '{view_box}' in {svg_path}")

    errors.extend(_audit_svg_security(root, content, svg_path))
    return errors, root, content


def _audit_svg_security(root: ET.Element, content: str, svg_path: Path) -> list[str]:
    """Ensure zero <script>, zero on* handlers, and zero external font/image URLs."""
    errors: list[str] = []
    for elem in root.iter():
        tag = _local_tag(elem.tag).lower()
        if tag == "script":
            errors.append(f"Forbidden <script> tag found in {svg_path}")
        if tag == "image":
            errors.append(f"Forbidden <image> tag found in {svg_path}")
        for attr_name, attr_val in elem.attrib.items():
            if attr_name.lower().startswith("on"):
                errors.append(f"Forbidden event attribute '{attr_name}' on <{tag}> in {svg_path}")
            if "http://" in attr_val.lower() or "https://" in attr_val.lower():
                errors.append(f"External URL in '{attr_name}' attribute in {svg_path}")
        if tag == "style" and elem.text:
            lowered = elem.text.lower()
            if "@import" in lowered or "http://" in lowered or "https://" in lowered:
                errors.append(f"External resource reference in <style> in {svg_path}")
    if re.search(r"<script\b", content, re.IGNORECASE):
        errors.append(f"Raw <script> string found in {svg_path}")
    return errors


def verify_metrics_svg(svg_path: Path, theme_name: str) -> list[str]:
    """Validate structural, theme, and non-empty telemetry checks for metrics SVG."""
    errors, root, content = verify_base_svg(svg_path, expected_viewbox=gpm.DEFAULT_VIEWBOX)
    if root is None:
        return errors

    for label in METRICS_REQUIRED_LABELS:
        if label not in content:
            errors.append(f"Missing required section label '{label}' in {svg_path}")

    if "prefers-reduced-motion" not in content:
        errors.append(f"Missing prefers-reduced-motion rule in {svg_path}")

    clip_ids = [el.get("id") for el in root.iter() if _local_tag(el.tag) == "clipPath"]
    if "langBarClip" not in clip_ids:
        errors.append(f"Missing langBarClip <clipPath> in {svg_path}")

    kpi_vals = [
        (el.text or "").strip()
        for el in root.iter()
        if _local_tag(el.tag) == "tspan" and el.get("class") == "kpi-val"
    ]
    if len(kpi_vals) != 4 or any(not val for val in kpi_vals):
        errors.append(f"Expected 4 non-empty .kpi-val metrics in {svg_path}, got {kpi_vals}")

    expected_tokens = ("#0d1117", "#38bdf8") if theme_name == "dark" else ("#ffffff", "#0284c7")
    for token in expected_tokens:
        if token not in content.lower():
            errors.append(f"Missing expected {theme_name} palette token '{token}' in {svg_path}")

    errors.extend(_verify_bounds(root, svg_path))
    return errors


def _verify_bounds(root: ET.Element, svg_path: Path) -> list[str]:
    """Check that rendered shapes stay strictly inside the 900x220 canvas."""
    errors: list[str] = []
    for elem in root.iter():
        if _local_tag(elem.tag) != "rect":
            continue
        try:
            x = float(elem.get("x", "0"))
            y = float(elem.get("y", "0"))
            w = float(elem.get("width", "0"))
            h = float(elem.get("height", "0"))
        except ValueError:
            continue
        if x < 0 or y < 0 or x + w > 900.5 or y + h > 220.5:
            errors.append(f"Rect out of 900x220 bounds in {svg_path}: x={x}, y={y}, w={w}, h={h}")
    return errors


def verify_assets_directory(assets_dir: Path) -> list[str]:
    """Verify metrics SVGs and hero-*.svg files inside assets_dir."""
    errors: list[str] = []
    errors.extend(verify_metrics_svg(assets_dir / "metrics-dark.svg", "dark"))
    errors.extend(verify_metrics_svg(assets_dir / "metrics-light.svg", "light"))

    hero_files = sorted(assets_dir.glob("hero-*.svg"))
    if not hero_files:
        print(f"[INFO] No hero-*.svg files found in {assets_dir}; skipped hero SVG checks.")
    else:
        for hero_path in hero_files:
            hero_errors, _, _ = verify_base_svg(hero_path, expected_viewbox="0 0 900 180")
            if hero_errors:
                errors.extend(hero_errors)
            else:
                print(f"[PASS] Verified hero SVG: {hero_path}")

    return errors


class ProfileMetricsTests(unittest.TestCase):
    """Mocked unit tests covering GitHub parsing, fallbacks, escaping, and CLI."""

    def test_fork_filtering_and_stars(self) -> None:
        """Forked repositories must not affect repo count, stars, languages, or focus."""
        user = {"login": "tester", "public_repos": 3, "followers": 7, "created_at": "2024-01-01T00:00:00Z"}
        repos = [
            {"name": "orig-a", "fork": False, "stargazers_count": 4, "language": "Python", "size": 100, "topics": ["dfir"]},
            {"name": "fork-noise", "fork": True, "stargazers_count": 999, "language": "Rust", "size": 99999, "topics": ["rust"]},
            {"name": "orig-b", "fork": False, "stargazers_count": 3, "language": "Go", "size": 200, "topics": ["forensics"]},
        ]
        mock_langs = {
            "orig-a": {"Python": 8000},
            "fork-noise": {"Rust": 9000000},
            "orig-b": {"Go": 2000},
        }
        original = [r for r in repos if not r.get("fork")]
        lang_bytes, lang_src = gpm.aggregate_languages(original, "tester", None, mock_languages=mock_langs)
        metrics = gpm.build_metrics(
            "tester",
            user,
            repos,
            lang_bytes,
            lang_src,
            gpm.ContributionSummary(120, 20, 365, "mock"),
            gpm.datetime(2026, 11, 26, tzinfo=gpm.timezone.utc),
        )
        self.assertEqual(metrics.original_repos, 2)
        self.assertEqual(metrics.forks_excluded, 1)
        self.assertEqual(metrics.total_stars, 7)
        self.assertNotIn("Rust", [s.name for s in metrics.languages])
        self.assertEqual(metrics.languages[0].name, "Python")
        self.assertEqual(metrics.languages[0].percent, 80.0)

    def test_top5_plus_other_and_bar_sum(self) -> None:
        """Language summary caps at top 5 plus Other and bar widths sum to 844px."""
        raw = {"Python": 500, "Go": 200, "Kotlin": 100, "C++": 80, "Java": 60, "Lua": 40, "Shell": 20}
        shares = tuple(gpm.summarize_languages(raw, top_n=5))
        self.assertEqual(len(shares), 6)
        self.assertEqual(shares[-1].name, "Other")
        self.assertEqual(shares[-1].byte_count, 60)
        self.assertEqual(shares[-1].percent, 6.0)
        widths = gpm.compute_bar_widths(shares, 844.0)
        self.assertAlmostEqual(sum(widths), 844.0, places=2)

    def test_rate_limit_fallback_to_repo_metadata(self) -> None:
        """Rate limit on /languages falls back cleanly to repo size + primary language."""
        repos = [
            {"name": "alpha", "fork": False, "language": "Python", "size": 300},
            {"name": "beta", "fork": False, "language": "Go", "size": 100},
        ]
        with patch.object(gpm, "fetch_repo_languages", side_effect=gpm.RateLimitError("403")):
            lang_bytes, source = gpm.aggregate_languages(repos, "tester", None)
        self.assertEqual(source, "repo_size")
        self.assertEqual(lang_bytes["Python"], 300 * 1024)
        self.assertEqual(lang_bytes["Go"], 100 * 1024)

    def test_contributions_multitier_fallbacks(self) -> None:
        """Contributions resolve through GraphQL -> HTML scrape -> Jogruber -> unavailable."""
        gql_payload = {
            "data": {
                "user": {
                    "contributionsCollection": {
                        "contributionCalendar": {
                            "totalContributions": 42,
                            "weeks": [
                                {"contributionDays": [{"date": "2026-11-25", "contributionCount": 2}, {"date": "2026-11-26", "contributionCount": 0}]}
                            ],
                        }
                    }
                }
            }
        }
        with patch.object(gpm, "http_post_json", return_value=gql_payload):
            c1 = gpm.fetch_contributions("tester", "tok")
        self.assertEqual((c1.total_365d, c1.active_days, c1.source), (42, 1, "graphql"))

        sample_html = (
            "<h2>191 contributions in the last year</h2>"
            '<table><td data-level="2" data-date="2026-11-24"></td>'
            '<td data-date="2026-11-25" data-level="0"></td>'
            '<td data-date="2026-11-26" data-level="4"></td></table>'
        )
        with (
            patch.object(gpm, "fetch_contributions_graphql", return_value=None),
            patch.object(gpm, "http_get_text", return_value=sample_html),
        ):
            c2 = gpm.fetch_contributions("tester", None)
        self.assertEqual((c2.total_365d, c2.active_days, c2.source), (191, 2, "html"))

        jg_payload = {"total": {"lastYear": 88}, "contributions": [{"count": 0}, {"count": 3}, {"count": 1}]}
        with (
            patch.object(gpm, "fetch_contributions_graphql", return_value=None),
            patch.object(gpm, "fetch_contributions_html", return_value=None),
            patch.object(gpm, "http_get_json", return_value=(jg_payload, {})),
        ):
            c3 = gpm.fetch_contributions("tester", None)
        self.assertEqual((c3.total_365d, c3.active_days, c3.source), (88, 2, "jogruber"))

        with (
            patch.object(gpm, "fetch_contributions_graphql", return_value=None),
            patch.object(gpm, "fetch_contributions_html", return_value=None),
            patch.object(gpm, "fetch_contributions_jogruber", return_value=None),
        ):
            c4 = gpm.fetch_contributions("tester", None)
        self.assertEqual((c4.total_365d, c4.active_days, c4.source), (None, None, "unavailable"))

    def test_xml_escaping_and_xss_safety(self) -> None:
        """Special XML characters and script payloads in inputs are safely escaped."""
        malicious_user = 'cid<script>"onload=alert(1)'
        user = {"login": malicious_user, "public_repos": 1, "followers": 0, "created_at": "2025-06-15T00:00:00Z"}
        repos = [{"name": "r&d", "fork": False, "stargazers_count": 1, "language": "C++ & RF <Test>", "topics": []}]
        metrics = gpm.build_metrics(
            malicious_user,
            user,
            repos,
            {"C++ & RF <Test>": 1024},
            "bytes",
            gpm.ContributionSummary(10, 4, 365, "mock"),
            gpm.datetime(2026, 11, 26, tzinfo=gpm.timezone.utc),
        )
        dark_svg = gpm.render_metrics_svg(metrics, gpm.DARK_THEME)
        root = ET.fromstring(dark_svg)
        self.assertEqual(len([el for el in root.iter() if _local_tag(el.tag) == "script"]), 0)
        for el in root.iter():
            for attr in el.attrib:
                self.assertFalse(attr.lower().startswith("on"))

    def test_mock_json_cli_end_to_end(self) -> None:
        """End-to-end CLI invocation with --mock-json produces valid dark & light SVGs."""
        fixture = {
            "user": {"login": "mock-dev", "public_repos": 2, "followers": 5, "created_at": "2024-12-01T04:17:04Z"},
            "repos": [
                {"name": "forensics-go", "fork": False, "stargazers_count": 10, "language": "Go", "size": 500, "topics": ["dfir", "ntfs"]},
                {"name": "esp32-rf", "fork": False, "stargazers_count": 4, "language": "C++", "size": 200, "topics": ["esp32", "nrf24l01"]},
            ],
            "languages_by_repo": {"forensics-go": {"Go": 60000, "Python": 20000}, "esp32-rf": {"C++": 20000}},
            "contributions": {"total_365d": 150, "active_days": 28},
            "generated_at": "2026-11-26T10:00:00Z",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            mock_path = tmp_dir / "mock.json"
            out_dir = tmp_dir / "out"
            mock_path.write_text(json.dumps(fixture), encoding="utf-8")
            rc = gpm.main(["--username", "mock-dev", "--mock-json", str(mock_path), "--out-dir", str(out_dir)])
            self.assertEqual(rc, 0)
            self.assertEqual(verify_metrics_svg(out_dir / "metrics-dark.svg", "dark"), [])
            self.assertEqual(verify_metrics_svg(out_dir / "metrics-light.svg", "light"), [])
            dark_text = (out_dir / "metrics-dark.svg").read_text(encoding="utf-8")
            self.assertIn("150", dark_text)
            self.assertIn("28 active days", dark_text)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for SVG verification and unit testing."""
    parser = argparse.ArgumentParser(description="Verify profile SVGs and run unit tests.")
    parser.add_argument("--assets-dir", default="assets", help="Directory containing generated SVG assets")
    parser.add_argument("--skip-tests", action="store_true", help="Only verify SVG files without running unit tests")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run asset verification and mocked unit test suite."""
    args = parse_args(argv)
    assets_dir = Path(args.assets_dir)
    errors = verify_assets_directory(assets_dir)
    if errors:
        for err in errors:
            print(f"[FAIL] {err}", file=sys.stderr)
        return 1
    print(f"[PASS] Verified metrics-dark.svg and metrics-light.svg in {assets_dir}")

    if not args.skip_tests:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProfileMetricsTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
