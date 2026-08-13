#!/usr/bin/env python3
"""Verify that every Remotion package in each bundled template is pinned alike."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ("shortform", "longform")


def remotion_versions(package: dict[str, object]) -> dict[str, str]:
    dependencies = package.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError("package.json has no dependencies object")
    return {
        name: version
        for name, version in dependencies.items()
        if (name == "remotion" or name.startswith("@remotion/"))
        and isinstance(version, str)
    }


def check_template(template: str) -> list[str]:
    package_path = ROOT / "assets" / template / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    versions = remotion_versions(package)
    errors: list[str] = []

    if not versions:
        return [f"{package_path}: no Remotion dependencies found"]

    expected = versions.get("remotion")
    if expected is None:
        return [f"{package_path}: missing remotion dependency"]

    for name, version in versions.items():
        if version.startswith(("^", "~", ">", "<", "*")):
            errors.append(f"{package_path}: {name} is not exactly pinned: {version}")
        elif version != expected:
            errors.append(
                f"{package_path}: {name}={version}, expected {expected}"
            )
    return errors


def main() -> int:
    errors = [error for template in TEMPLATES for error in check_template(template)]
    if errors:
        print("Remotion dependency check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Remotion dependency versions are exactly pinned and consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
