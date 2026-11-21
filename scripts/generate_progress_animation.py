#!/usr/bin/env python3
"""
scripts/generate_progress_animation.py
Generates a dynamic SVG progress animation for the GitHub profile showcase,
featuring an animated cyber-snake actively consuming a progress bar based on
live GitHub contribution telemetry.

Zero external dependencies (Python standard library only).
"""

import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


def fetch_contributions_graphql(username: str, token: str | None) -> int | None:
    """Tier 1: Query GitHub GraphQL API using GITHUB_TOKEN or GH_TOKEN."""
    if not token:
        return None
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
          }
        }
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"login": username}}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "ContributionProgressSnake/1.0",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            cal = (
                data.get("data", {})
                .get("user", {})
                .get("contributionsCollection", {})
                .get("contributionCalendar", {})
            )
            if "totalContributions" in cal:
                val = int(cal["totalContributions"])
                print(f"[INFO] Tier 1 GraphQL fetch successful: {val} contributions", file=sys.stderr)
                return val
    except Exception as e:
        print(f"[WARN] Tier 1 GraphQL fetch failed: {e}", file=sys.stderr)
    return None


def fetch_contributions_scrape(username: str) -> int | None:
    """Tier 2: Scrape public contributions count from user profile contributions page."""
    try:
        url = f"https://github.com/users/{username}/contributions"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")
            m = re.search(r"([0-9,]+)\s+contributions\s+in\s+(?:the last year|\d{4})", html)
            if m:
                val = int(m.group(1).replace(",", ""))
                print(f"[INFO] Tier 2 public scrape successful: {val} contributions", file=sys.stderr)
                return val
    except Exception as e:
        print(f"[WARN] Tier 2 public scrape failed: {e}", file=sys.stderr)
    return None


def fetch_contributions_microservice(username: str) -> int | None:
    """Tier 3: Query public contributions JSON microservice."""
    try:
        url = f"https://github-contributions-api.jogruber.de/v4/{username}?y=last"
        req = urllib.request.Request(url, headers={"User-Agent": "ContributionProgressSnake/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "total" in data and "lastYear" in data["total"]:
                val = int(data["total"]["lastYear"])
                print(f"[INFO] Tier 3 microservice fetch successful: {val} contributions", file=sys.stderr)
                return val
    except Exception as e:
        print(f"[WARN] Tier 3 microservice fetch failed: {e}", file=sys.stderr)
    return None


def get_contributions(username: str, token: str | None, mock: int | None = None) -> int:
    """Multi-tier contribution retriever with safe baseline fallback."""
    if mock is not None:
        print(f"[INFO] Using mock contributions count: {mock}", file=sys.stderr)
        return int(mock)

    # Tier 1: GraphQL
    val = fetch_contributions_graphql(username, token)
    if val is not None:
        return val

    # Tier 2: Public Scrape
    val = fetch_contributions_scrape(username)
    if val is not None:
        return val

    # Tier 3: Microservice
    val = fetch_contributions_microservice(username)
    if val is not None:
        return val

    # Tier 4: Safe baseline fallback
    fallback_baseline = 178
    print(f"[WARN] All live fetch tiers failed. Using Tier 4 baseline fallback ({fallback_baseline})", file=sys.stderr)
    return fallback_baseline


def calculate_metrics(total: int, goal: int) -> dict:
    pct = min(100.0, max(0.0, (total / goal) * 100.0)) if goal > 0 else 0.0

    if total < 50:
        level, rank_color = "LVL 1 // RECON INITIATE", "#8b949e"
    elif total < 100:
        level, rank_color = "LVL 2 // SYSTEM AUDITOR", "#38bdf8"
    elif total < 175:
        level, rank_color = "LVL 3 // HARDWARE ANALYST", "#e3b341"
    elif total < 250:
        level, rank_color = "LVL 4 // FORENSICS SPECIALIST", "#3fb950"
    else:
        level, rank_color = "LVL 5 // ROOT ARCHITECT", "#00ff66"

    remaining = max(0, goal - total)
    return {
        "total": total,
        "goal": goal,
        "pct": pct,
        "remaining": remaining,
        "level": level,
        "rank_color": rank_color,
    }


def generate_svg(metrics: dict, width: int = 900, height: int = 138) -> str:
    total = metrics["total"]
    goal = metrics["goal"]
    pct = metrics["pct"]
    remaining = metrics["remaining"]
    level = metrics["level"]
    rank_color = metrics["rank_color"]

    track_x = 60.0
    track_y = 74.0
    track_w = 780.0
    track_h = 22.0
    track_cy = track_y + track_h / 2.0  # 85.0

    eaten_w = track_w * (pct / 100.0)
    frontier_x = track_x + eaten_w

    num_pellets = 38
    spacing = track_w / (num_pellets - 1)

    total_dur = 5.0
    k_reach = 0.72
    k_wait = 0.88

    # Pellets generation
    earned_pellets = []
    locked_pellets = []

    for i in range(num_pellets):
        px = track_x + i * spacing
        py = track_cy
        if px <= frontier_x + 0.1:
            earned_pellets.append(
                f'      <g>\n'
                f'        <circle cx="{px:.1f}" cy="{py:.1f}" r="3.2" fill="#3fb950" opacity="0.95" />\n'
                f'        <circle cx="{px:.1f}" cy="{py:.1f}" r="1.3" fill="#ffffff" opacity="0.9" />\n'
                f'      </g>'
            )
        else:
            locked_pellets.append(
                f'      <g>\n'
                f'        <circle cx="{px:.1f}" cy="{py:.1f}" r="2.6" fill="#0d1117" stroke="#30363d" stroke-width="1" stroke-dasharray="1.5,1.5" />\n'
                f'        <circle cx="{px:.1f}" cy="{py:.1f}" r="0.8" fill="#484f58" />\n'
                f'      </g>'
            )

    earned_markup = "\n".join(earned_pellets)
    locked_markup = "\n".join(locked_pellets)

    # Milestone ticks
    tick_25 = track_x + track_w * 0.25
    tick_50 = track_x + track_w * 0.50
    tick_75 = track_x + track_w * 0.75

    val_25 = int(round(goal * 0.25))
    val_50 = int(round(goal * 0.50))
    val_75 = int(round(goal * 0.75))

    clip_w_start = track_w + 30.0
    clip_w_end = max(1.0, clip_w_start - eaten_w)

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%">
  <defs>
    <!-- Background Gradients -->
    <linearGradient id="cardBg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0a0e14" />
      <stop offset="50%" stop-color="#0d1117" />
      <stop offset="100%" stop-color="#080c10" />
    </linearGradient>
    <linearGradient id="chromeBg" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#161b22" />
      <stop offset="100%" stop-color="#0f141a" />
    </linearGradient>
    <linearGradient id="trackBg" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#080b0f" />
      <stop offset="100%" stop-color="#121820" />
    </linearGradient>

    <!-- Glowing Conduits & Skin -->
    <linearGradient id="conduitGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#006d32" />
      <stop offset="35%" stop-color="#26a641" />
      <stop offset="75%" stop-color="#39d353" />
      <stop offset="100%" stop-color="#38bdf8" />
    </linearGradient>
    <linearGradient id="snakeSkin" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#14532d" />
      <stop offset="35%" stop-color="#16a34a" />
      <stop offset="75%" stop-color="#22c55e" />
      <stop offset="100%" stop-color="#4ade80" />
    </linearGradient>

    <!-- Dynamic Eating Clip Paths -->
    <!-- 1. consumedClip: expands from track_x to frontier_x behind the snake -->
    <clipPath id="consumedClip">
      <rect x="{track_x}" y="{track_y}" width="0" height="{track_h}" rx="11">
        <animate attributeName="width"
          values="0; {eaten_w:.1f}; {eaten_w:.1f}; 0"
          keyTimes="0; {k_reach}; {k_wait}; 1"
          dur="{total_dur}s" repeatCount="indefinite"
          calcMode="spline" keySplines="0.4 0 0.2 1; 0 0 1 1; 0.4 0 0.2 1" />
      </rect>
    </clipPath>

    <!-- 2. pelletClip: shifts its left edge rightward as snake mouth consumes pellets -->
    <clipPath id="pelletClip">
      <rect x="{track_x}" y="{track_y - 10}" width="{clip_w_start:.1f}" height="{track_h + 20}">
        <animate attributeName="x"
          values="{track_x}; {frontier_x:.1f}; {frontier_x:.1f}; {track_x}"
          keyTimes="0; {k_reach}; {k_wait}; 1"
          dur="{total_dur}s" repeatCount="indefinite"
          calcMode="spline" keySplines="0.4 0 0.2 1; 0 0 1 1; 0.4 0 0.2 1" />
        <animate attributeName="width"
          values="{clip_w_start:.1f}; {clip_w_end:.1f}; {clip_w_end:.1f}; {clip_w_start:.1f}"
          keyTimes="0; {k_reach}; {k_wait}; 1"
          dur="{total_dur}s" repeatCount="indefinite"
          calcMode="spline" keySplines="0.4 0 0.2 1; 0 0 1 1; 0.4 0 0.2 1" />
      </rect>
    </clipPath>
  </defs>

  <style>
    .mono-title {{ font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; fill: #f0f6fc; }}
    .mono-telemetry {{ font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 9px; letter-spacing: 0.8px; fill: #7d8590; }}
    .stat-label {{ font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 8px; font-weight: 600; letter-spacing: 0.7px; fill: #8b949e; }}
    .stat-val {{ font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 9.5px; font-weight: 700; letter-spacing: 0.8px; fill: #f0f6fc; }}
    .scale-label {{ font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 8px; font-weight: 500; fill: #7d8590; }}
  </style>

  <!-- Container Base Card -->
  <rect width="{width}" height="{height}" rx="8" fill="url(#cardBg)" stroke="#21262d" stroke-width="1" />

  <!-- Terminal Chrome Header Bar -->
  <rect width="{width}" height="28" rx="8" fill="url(#chromeBg)" />
  <rect y="20" width="{width}" height="8" fill="url(#chromeBg)" />
  <line x1="0" y1="28" x2="{width}" y2="28" stroke="#21262d" stroke-width="1" />

  <circle cx="18" cy="14" r="3.5" fill="#3fb950">
    <animate attributeName="opacity" values="1; 0.35; 1" dur="2s" repeatCount="indefinite" />
  </circle>
  <text x="30" y="18" class="mono-title">DYNAMIC INVESTIGATION TELEMETRY // CONTRIBUTION PROGRESS</text>
  <text x="{width - 18}" y="18" text-anchor="end" class="mono-telemetry">
    TARGET: <tspan fill="#e3b341">{goal} COMMITS</tspan> | RECORD: <tspan fill="#3fb950">{total}</tspan> | QUOTA: <tspan fill="#38bdf8">{pct:.1f}%</tspan> | STATUS: <tspan fill="#00ff66">CONSUMING</tspan>
  </text>

  <!-- Telemetry Sub-row Readout Badges -->
  <g transform="translate(60, 36)">
    <!-- Badge 1: Activity -->
    <rect x="0" y="0" width="180" height="22" rx="4" fill="#161b22" stroke="#21262d" stroke-width="0.8" />
    <text x="10" y="15" class="stat-label">ANNUAL ACTIVITY:</text>
    <text x="110" y="15" class="stat-val" fill="#3fb950">{total} <tspan fill="#7d8590" font-weight="400">/ {goal}</tspan></text>

    <!-- Badge 2: Goal Fill -->
    <rect x="195" y="0" width="175" height="22" rx="4" fill="#161b22" stroke="#21262d" stroke-width="0.8" />
    <text x="205" y="15" class="stat-label">QUOTA FILL:</text>
    <text x="275" y="15" class="stat-val" fill="#00ff66">{pct:.1f}% <tspan fill="#7d8590" font-weight="400">({remaining} REM)</tspan></text>

    <!-- Badge 3: Clearance -->
    <rect x="385" y="0" width="230" height="22" rx="4" fill="#161b22" stroke="#21262d" stroke-width="0.8" />
    <text x="395" y="15" class="stat-label">CLEARANCE:</text>
    <text x="470" y="15" class="stat-val" fill="{rank_color}">{level}</text>

    <!-- Badge 4: Chomp Cadence -->
    <rect x="630" y="0" width="150" height="22" rx="4" fill="#161b22" stroke="#21262d" stroke-width="0.8" />
    <text x="640" y="15" class="stat-label">CADENCE:</text>
    <text x="705" y="15" class="stat-val" fill="#38bdf8">4.0 HZ // SMIL</text>
  </g>

  <!-- PROGRESS TRACK CONDUIT -->
  <g>
    <!-- Base Recessed Track -->
    <rect x="{track_x}" y="{track_y}" width="{track_w}" height="{track_h}" rx="11" fill="url(#trackBg)" stroke="#21262d" stroke-width="1.2" />

    <!-- Quarter Grid Lines -->
    <g stroke="#1b222c" stroke-width="1" stroke-dasharray="2,3">
      <line x1="{tick_25:.1f}" y1="{track_y}" x2="{tick_25:.1f}" y2="{track_y + track_h}" />
      <line x1="{tick_50:.1f}" y1="{track_y}" x2="{tick_50:.1f}" y2="{track_y + track_h}" />
      <line x1="{tick_75:.1f}" y1="{track_y}" x2="{tick_75:.1f}" y2="{track_y + track_h}" />
    </g>

    <!-- Center Track Circuit Line -->
    <line x1="{track_x + 8}" y1="{track_cy}" x2="{track_x + track_w - 8}" y2="{track_cy}" stroke="#21262d" stroke-width="1" stroke-dasharray="3,6" />

    <!-- Consumed Energy Conduit (clipped by consumedClip) -->
    <g clip-path="url(#consumedClip)">
      <rect x="{track_x}" y="{track_y}" width="{track_w}" height="{track_h}" rx="11" fill="url(#conduitGrad)" opacity="0.9" />
      <line x1="{track_x}" y1="{track_cy}" x2="{track_x + track_w}" y2="{track_cy}" stroke="#ffffff" stroke-width="0.8" stroke-dasharray="4,8" opacity="0.6" />
    </g>

    <!-- Locked Future Pellets (outside eaten frontier) -->
{locked_markup}

    <!-- Active Contribution Pellets (disappear into snake mouth via pelletClip) -->
    <g clip-path="url(#pelletClip)">
{earned_markup}
    </g>

    <!-- Frontier Milestone Beacon -->
    <g>
      <!-- Vertical Beacon Marker -->
      <line x1="{frontier_x:.1f}" y1="{track_y - 4}" x2="{frontier_x:.1f}" y2="{track_y + track_h + 4}" stroke="#38bdf8" stroke-width="1.2" stroke-dasharray="2,2" opacity="0.8" />
      <!-- Milestone Chevron Pointer -->
      <polygon points="{frontier_x - 3.5:.1f},{track_y - 6} {frontier_x + 3.5:.1f},{track_y - 6} {frontier_x:.1f},{track_y - 1}" fill="#38bdf8" />
      <!-- Frontier Rotating Radar Ring -->
      <circle cx="{frontier_x:.1f}" cy="{track_cy}" r="9" fill="none" stroke="#38bdf8" stroke-width="1" stroke-dasharray="2,2" opacity="0.7">
        <animateTransform attributeName="transform" type="rotate" from="0 {frontier_x:.1f} {track_cy}" to="360 {frontier_x:.1f} {track_cy}" dur="4s" repeatCount="indefinite" />
      </circle>
      <!-- Milestone Arrival Shockwave -->
      <circle cx="{frontier_x:.1f}" cy="{track_cy}" r="2" fill="none" stroke="#58a6ff" stroke-width="1.6" opacity="0">
        <animate attributeName="r" values="2; 2; 16; 22" keyTimes="0; {k_reach}; {(k_reach+0.05):.3f}; 1" dur="{total_dur}s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="0; 0; 0.9; 0" keyTimes="0; {k_reach}; {(k_reach+0.04):.3f}; 1" dur="{total_dur}s" repeatCount="indefinite" />
      </circle>
      <!-- Core Frontier Node -->
      <circle cx="{frontier_x:.1f}" cy="{track_cy}" r="3" fill="#38bdf8" />
      <circle cx="{frontier_x:.1f}" cy="{track_cy}" r="1.2" fill="#ffffff" />
    </g>
  </g>

  <!-- Scale Tick Labels Below Track -->
  <g transform="translate(0, {track_y + track_h + 15})">
    <text x="{track_x}" y="0" class="scale-label">0% [INIT]</text>
    <text x="{tick_25:.1f}" y="0" text-anchor="middle" class="scale-label">25% ({val_25})</text>
    <text x="{tick_50:.1f}" y="0" text-anchor="middle" class="scale-label">50% ({val_50})</text>
    <text x="{tick_75:.1f}" y="0" text-anchor="middle" class="scale-label">75% ({val_75})</text>
    <text x="{track_x + track_w}" y="0" text-anchor="end" class="scale-label">100% [{goal} COMMITS]</text>
  </g>

  <!-- ANIMATED CYBER-SNAKE EATER -->
  <g transform="translate({track_x}, {track_cy})">
    <animateTransform attributeName="transform" type="translate"
      values="{track_x},{track_cy}; {frontier_x:.1f},{track_cy}; {frontier_x:.1f},{track_cy}; {track_x},{track_cy}"
      keyTimes="0; {k_reach}; {k_wait}; 1"
      dur="{total_dur}s" repeatCount="indefinite"
      calcMode="spline" keySplines="0.4 0 0.2 1; 0 0 1 1; 0.4 0 0.2 1" />

    <!-- Undulating Trailing Segments -->
    <!-- Segment 4 (Tail) -->
    <circle cx="-34" cy="0" r="3.2" fill="#14532d" opacity="0.7">
      <animate attributeName="cy" values="0; 2.2; 0; -2.2; 0" dur="0.5s" repeatCount="indefinite" />
    </circle>
    <!-- Segment 3 -->
    <circle cx="-26" cy="0" r="4.6" fill="#166534" opacity="0.8">
      <animate attributeName="cy" values="0; -2.6; 0; 2.6; 0" dur="0.5s" repeatCount="indefinite" />
    </circle>
    <!-- Segment 2 -->
    <circle cx="-18" cy="0" r="5.8" fill="#15803d" opacity="0.9">
      <animate attributeName="cy" values="0; 2.8; 0; -2.8; 0" dur="0.5s" repeatCount="indefinite" />
    </circle>
    <!-- Segment 1 (Shoulder) -->
    <circle cx="-10" cy="0" r="7.0" fill="#22c55e" opacity="0.95">
      <animate attributeName="cy" values="0; -2.2; 0; 2.2; 0" dur="0.5s" repeatCount="indefinite" />
    </circle>

    <!-- Bio-luminescent Vertebrae Nodes -->
    <circle cx="-26" cy="0" r="1.1" fill="#38bdf8">
      <animate attributeName="cy" values="0; -2.6; 0; 2.6; 0" dur="0.5s" repeatCount="indefinite" />
    </circle>
    <circle cx="-18" cy="0" r="1.3" fill="#38bdf8">
      <animate attributeName="cy" values="0; 2.8; 0; -2.8; 0" dur="0.5s" repeatCount="indefinite" />
    </circle>
    <circle cx="-10" cy="0" r="1.5" fill="#38bdf8">
      <animate attributeName="cy" values="0; -2.2; 0; 2.2; 0" dur="0.5s" repeatCount="indefinite" />
    </circle>

    <!-- Cyber Head Base -->
    <ellipse cx="-2" cy="0" rx="7.5" ry="6.5" fill="url(#snakeSkin)" stroke="#052e16" stroke-width="0.8" />

    <!-- Cyber Sensor Eye -->
    <circle cx="2" cy="-4" r="2.2" fill="#38bdf8">
      <animate attributeName="opacity" values="0.8; 1; 0.8" dur="1s" repeatCount="indefinite" />
    </circle>
    <circle cx="2.5" cy="-4" r="1.0" fill="#ffffff" />

    <!-- Articulated Chomping Jaws (4 Hz = 0.25s bite cadence) -->
    <!-- Upper Jaw with Cyber Fangs -->
    <g>
      <animateTransform attributeName="transform" type="rotate" values="0 0 -2; -24 0 -2; 0 0 -2" dur="0.25s" repeatCount="indefinite" />
      <path d="M -4,-2 L 4,-8 L 12,-4 L 14,-1 L 0,-1 Z" fill="url(#snakeSkin)" stroke="#052e16" stroke-width="0.8" />
      <polygon points="8,-1 10,-1 9,2.5" fill="#f0f6fc" />
      <polygon points="4,-1 6,-1 5,2.0" fill="#f0f6fc" />
    </g>

    <!-- Lower Jaw with Cyber Fangs -->
    <g>
      <animateTransform attributeName="transform" type="rotate" values="0 0 2; 24 0 2; 0 0 2" dur="0.25s" repeatCount="indefinite" />
      <path d="M -4,2 L 3,8 L 11,4 L 13,1 L 0,1 Z" fill="url(#snakeSkin)" stroke="#052e16" stroke-width="0.8" />
      <polygon points="8,1 10,1 9,-2.5" fill="#f0f6fc" />
      <polygon points="4,1 6,1 5,-2.0" fill="#f0f6fc" />
    </g>

    <!-- Laser Sensor / Cyber Forked Sensor Tongue -->
    <path d="M 13,0 L 20,0 L 24,-2.5 M 20,0 L 24,2.5" fill="none" stroke="#38bdf8" stroke-width="1" stroke-linecap="round">
      <animate attributeName="opacity" values="0; 1; 1; 0" keyTimes="0; 0.3; 0.7; 1" dur="0.5s" repeatCount="indefinite" />
      <animateTransform attributeName="transform" type="translate" values="0,0; 3,0; 0,0" dur="0.5s" repeatCount="indefinite" />
    </path>

    <!-- Contact Spark Emitter at mouth bite apex -->
    <circle cx="12" cy="0" r="2.5" fill="#58a6ff" opacity="0">
      <animate attributeName="opacity" values="0; 0.9; 0" dur="0.25s" repeatCount="indefinite" />
      <animate attributeName="r" values="1; 3.5; 1" dur="0.25s" repeatCount="indefinite" />
    </circle>
  </g>
</svg>'''


def main():
    parser = argparse.ArgumentParser(
        description="Generate dynamic contribution progress snake animation SVG."
    )
    parser.add_argument(
        "--username",
        "--user",
        dest="username",
        default=os.environ.get("GITHUB_REPOSITORY_OWNER", "cidmoosa"),
        help="GitHub username (default: cidmoosa or GITHUB_REPOSITORY_OWNER)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
        help="GitHub personal access token (default: GITHUB_TOKEN or GH_TOKEN env var)",
    )
    parser.add_argument(
        "--goal",
        type=int,
        default=250,
        help="Annual contribution quota goal (default: 250)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="assets/progress-bar.svg",
        help="Output path for generated SVG (default: assets/progress-bar.svg)",
    )
    parser.add_argument(
        "--mock-contributions",
        "--contributions",
        dest="mock_contributions",
        type=int,
        default=None,
        help="Manually specify contribution count override for testing/offline use",
    )

    args = parser.parse_args()

    # Retrieve contributions via multi-tier fetching or mock
    total = get_contributions(args.username, args.token, args.mock_contributions)
    metrics = calculate_metrics(total, args.goal)

    svg_content = generate_svg(metrics)

    # Validate generated SVG with ElementTree before writing
    try:
        ET.fromstring(svg_content)
    except ET.ParseError as err:
        print(f"[FATAL] Generated SVG failed XML parse validation: {err}", file=sys.stderr)
        sys.exit(1)

    out_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg_content)

    print(
        f"Generated {args.output} for user '{args.username}': "
        f"{total}/{args.goal} commits ({metrics['pct']:.1f}%) | Level: {metrics['level']}"
    )


if __name__ == "__main__":
    main()
