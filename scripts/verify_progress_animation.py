#!/usr/bin/env python3
"""
scripts/verify_progress_animation.py
Automated verification test suite for dynamic progress animation SVG asset and generator.
Zero external dependencies (Python standard library only).
"""

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"


def verify_svg_file(file_path: str, expected_total: int | None = None, expected_pct: float | None = None) -> list[str]:
    """Verify an SVG file against all quality and sanitization criteria."""
    errors = []

    # 1. Existence and non-empty check (> 1000 bytes)
    if not os.path.exists(file_path):
        errors.append(f"File not found: {file_path}")
        return errors

    file_size = os.path.getsize(file_path)
    if file_size < 1000:
        errors.append(f"File size too small ({file_size} bytes < 1000 bytes): {file_path}")

    # 2. XML well-formedness
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
    except ET.ParseError as pe:
        errors.append(f"XML parse error in {file_path}: {pe}")
        return errors

    # 3. Root tag is <svg> with valid xmlns and viewBox
    tag = root.tag
    if tag != f"{{{SVG_NS}}}svg" and tag != "svg":
        errors.append(f"Root tag is '{tag}', expected '<svg>'")

    view_box = root.get("viewBox")
    if not view_box or len(view_box.split()) != 4:
        errors.append(f"Invalid or missing viewBox attribute: {view_box}")

    # 4. Zero <script> tags anywhere in the document (GitHub README markdown sanitization)
    for elem in root.iter():
        elem_tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if elem_tag.lower() == "script":
            errors.append(f"Illegal <script> element detected at line {getattr(elem, '__line__', 'unknown')}")

        # Check for inline event handler attributes like onload, onclick, onerror
        for attr in elem.attrib:
            if attr.lower().startswith("on"):
                errors.append(f"Illegal inline event handler attribute '{attr}' on <{elem_tag}>")

    # 5. Verify presence of essential components (eating clip paths, cyber snake, chrome header)
    clip_paths = [elem.get("id") for elem in root.iter() if (elem.tag.split("}")[-1] == "clipPath")]
    if "consumedClip" not in clip_paths:
        errors.append("Missing required 'consumedClip' clipPath definition")
    if "pelletClip" not in clip_paths:
        errors.append("Missing required 'pelletClip' clipPath definition")

    # Read raw content for textual / metric checks
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    if "DYNAMIC INVESTIGATION TELEMETRY" not in content:
        errors.append("Missing telemetry header text in SVG")

    if expected_total is not None:
        total_str = str(expected_total)
        if total_str not in content:
            errors.append(f"Expected contribution total '{total_str}' not found in SVG content")

    if expected_pct is not None:
        pct_str = f"{expected_pct:.1f}%"
        if pct_str not in content:
            errors.append(f"Expected progress percentage '{pct_str}' not found in SVG content")

    return errors


def run_generator_test_matrix() -> list[str]:
    """Execute generator script across parameter matrix and boundary conditions."""
    failures = []
    generator_script = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "generate_progress_animation.py")
    )

    if not os.path.exists(generator_script):
        return [f"Generator script not found: {generator_script}"]

    test_cases = [
        {"desc": "Zero contributions (0%)", "mock": 0, "goal": 250, "expected_pct": 0.0},
        {"desc": "Half goal contributions (50%)", "mock": 125, "goal": 250, "expected_pct": 50.0},
        {"desc": "Full goal contributions (100%)", "mock": 250, "goal": 250, "expected_pct": 100.0},
        {"desc": "Exceeded goal clamped to 100%", "mock": 400, "goal": 250, "expected_pct": 100.0},
        {"desc": "Custom goal (500) with 178 contributions", "mock": 178, "goal": 500, "expected_pct": 35.6},
        {"desc": "Baseline fallback count (178)", "mock": 178, "goal": 250, "expected_pct": 71.2},
    ]

    for tc in test_cases:
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            cmd = [
                sys.executable,
                generator_script,
                "--mock-contributions",
                str(tc["mock"]),
                "--goal",
                str(tc["goal"]),
                "--output",
                tmp_path,
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                failures.append(f"Test '{tc['desc']}' failed with exit code {res.returncode}: {res.stderr.strip()}")
                continue

            errs = verify_svg_file(
                tmp_path,
                expected_total=tc["mock"],
                expected_pct=tc["expected_pct"],
            )
            if errs:
                failures.append(f"Test '{tc['desc']}' validation errors: {'; '.join(errs)}")
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    return failures


def main():
    parser = argparse.ArgumentParser(description="Verify dynamic progress animation SVG asset and generator.")
    parser.add_argument(
        "--file",
        "-f",
        default="assets/progress-bar.svg",
        help="Path to SVG file to verify (default: assets/progress-bar.svg)",
    )
    parser.add_argument(
        "--run-generator-tests",
        action="store_true",
        default=True,
        help="Execute parameterized generator test matrix (default: True)",
    )

    args = parser.parse_args()

    print(f"=== Running Dynamic Progress Animation Verification Suite ===")
    all_passed = True

    # Check 1: Primary Target File
    print(f"\n[Test 1] Verifying primary asset: {args.file}")
    file_errors = verify_svg_file(args.file)
    if file_errors:
        all_passed = False
        print(f"  FAILED with {len(file_errors)} error(s):")
        for err in file_errors:
            print(f"    - {err}")
    else:
        file_size = os.path.getsize(args.file)
        print(f"  PASSED: Valid XML, viewBox present, zero <script> tags, size {file_size:,} bytes")

    # Check 2: Generator Test Matrix
    if args.run_generator_tests:
        print(f"\n[Test 2] Running parameterized generator test matrix...")
        matrix_errors = run_generator_test_matrix()
        if matrix_errors:
            all_passed = False
            print(f"  FAILED with {len(matrix_errors)} matrix failure(s):")
            for err in matrix_errors:
                print(f"    - {err}")
        else:
            print("  PASSED: All boundary & scaling test cases passed successfully.")

    print("\n" + "=" * 60)
    if all_passed:
        print("ALL VERIFICATION CHECKS PASSED (exit code 0)")
        sys.exit(0)
    else:
        print("VERIFICATION FAILED (exit code 1)")
        sys.exit(1)


if __name__ == "__main__":
    main()
