#!/usr/bin/env python3
"""Classify dependency licenses for the AGPL-3.0 policy.

Reads a pip-licenses JSON list or a license-checker JSON object. Denied licenses
exit 1. UNKNOWN or missing licenses only warn. Stdlib only, so CI and local runs
need no extra dependency.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Licenses that cannot be combined with AGPL-3.0 distribution or that impose
# commercial or source-availability terms. Matched case-insensitively per term.
DENIED_TERMS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bSSPL\b",
        r"Server Side Public License",
        r"\bBUSL\b",
        r"Business Source License",
        r"Commons Clause",
        r"Elastic License|\bELv2\b|Elastic-2\.0",
        r"\bProprietary\b",
        r"\bCommercial\b",
        r"\bUNLICENSED\b",
        r"GPL-2\.0-only",
        r"(?<!L)GPLv2(?!\+| or later)",
    )
)
# Alternatives joined by OR: the consumer may pick one, so the expression is denied
# only when every alternative is denied. Conjunctions (AND, ";") deny on any term.
OR_SPLIT = re.compile(r"\s+OR\s+|\s*/\s*|\s*\|\s*", re.IGNORECASE)
AND_SPLIT = re.compile(r"\s+AND\s+|\s*;\s*", re.IGNORECASE)
UNKNOWN_VALUES = {"", "UNKNOWN", "NOASSERTION", "NONE"}
# Longer values are license texts pasted into metadata. Matching terms inside prose
# would flag ordinary words such as "commercial", so these only warn for review.
MAX_LICENSE_LABEL_LENGTH = 100


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    license: str


def load_packages(data: object) -> list[Package]:
    """Normalize pip-licenses (list) or license-checker (object) output."""
    if isinstance(data, list):
        return [
            Package(
                name=str(entry.get("Name", "")),
                version=str(entry.get("Version", "")),
                license=str(entry.get("License", "")),
            )
            for entry in data
        ]
    if isinstance(data, dict):
        return [_from_license_checker(key, value) for key, value in data.items()]
    raise ValueError("unsupported license report format")


def _from_license_checker(key: str, value: dict) -> Package:
    name, _, version = key.rpartition("@")
    licenses = value.get("licenses", "")
    if isinstance(licenses, list):
        licenses = " OR ".join(str(item) for item in licenses)
    return Package(name=name or key, version=version, license=str(licenses))


def strip_parentheses(expression: str) -> str:
    return expression.replace("(", " ").replace(")", " ").strip()


def is_unknown(expression: str) -> bool:
    return strip_parentheses(expression).upper() in UNKNOWN_VALUES


def is_license_text(expression: str) -> bool:
    return len(expression) > MAX_LICENSE_LABEL_LENGTH or "\n" in expression


def is_denied(expression: str) -> bool:
    if is_license_text(expression):
        return False
    alternatives = [
        alt for alt in OR_SPLIT.split(strip_parentheses(expression)) if alt.strip()
    ]
    if not alternatives:
        return False
    return all(_alternative_is_denied(alt) for alt in alternatives)


def _alternative_is_denied(alternative: str) -> bool:
    parts = [part for part in AND_SPLIT.split(alternative) if part.strip()]
    return any(any(term.search(part) for term in DENIED_TERMS) for part in parts)


def classify(packages: list[Package]) -> tuple[list[Package], list[Package]]:
    denied = [pkg for pkg in packages if is_denied(pkg.license)]
    unknown = [
        pkg
        for pkg in packages
        if is_unknown(pkg.license) or is_license_text(pkg.license)
    ]
    return denied, unknown


def _label(license_name: str) -> str:
    if is_license_text(license_name):
        return f"<license text, {len(license_name)} chars>"
    return license_name


def render_summary(
    packages: list[Package], denied: list[Package], unknown: list[Package]
) -> str:
    lines = [f"Packages: {len(packages)}", "", "Licenses:"]
    counts = Counter(pkg.license or "UNKNOWN" for pkg in packages)
    lines += [
        f"  {count:4d}  {_label(license_name)}"
        for license_name, count in counts.most_common()
    ]
    lines += ["", f"WARN unknown license: {len(unknown)}"]
    lines += [f"  {pkg.name}@{pkg.version}" for pkg in unknown]
    lines += ["", f"DENIED: {len(denied)}"]
    lines += [f"  {pkg.name}@{pkg.version}: {_label(pkg.license)}" for pkg in denied]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "report", type=Path, help="pip-licenses or license-checker JSON file"
    )
    args = parser.parse_args(argv)

    packages = load_packages(json.loads(args.report.read_text(encoding="utf-8")))
    denied, unknown = classify(packages)
    print(render_summary(packages, denied, unknown))
    if denied:
        print("license-policy: denied licenses found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
