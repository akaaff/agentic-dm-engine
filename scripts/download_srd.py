"""Vendor the D&D 5e SRD 5.1 JSON dataset into data/srd/.

Source: 5e-bits/5e-database (https://github.com/5e-bits/5e-database), the
dataset backing dnd5eapi.co. That repo's own compiled dataset is MIT-licensed;
the underlying game rules text/data (the SRD 5.1 itself) is separately
released by Wizards of the Coast under both the Open Gaming License 1.0a and
(as of 2023) Creative Commons CC-BY-4.0 - see data/srd/ATTRIBUTION.md, written
by this script, for the exact notice. Only the 2014/en subset is pulled: this
project targets 5e SRD 5.1 rules, not the 2024 revision, and not other
languages.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

BASE_URL = "https://raw.githubusercontent.com/5e-bits/5e-database/main/src/2014/en"
FILES = [
    "5e-SRD-Backgrounds.json",
    "5e-SRD-Classes.json",
    "5e-SRD-Subclasses.json",
    "5e-SRD-Conditions.json",
    "5e-SRD-Equipment.json",
    "5e-SRD-Equipment-Categories.json",
    "5e-SRD-Monsters.json",
    "5e-SRD-Races.json",
    "5e-SRD-Subraces.json",
    "5e-SRD-Spells.json",
    "5e-SRD-Skills.json",
]

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "srd"

ATTRIBUTION = """\
# SRD 5.1 attribution

This directory vendors a subset of the D&D 5th Edition System Reference
Document 5.1, downloaded from the 5e-bits/5e-database project
(https://github.com/5e-bits/5e-database), which compiles the SRD into JSON
for use by dnd5eapi.co.

- The compiled JSON dataset itself (5e-bits/5e-database) is MIT-licensed.
- The underlying SRD 5.1 game content is released by Wizards of the Coast
  under the Open Gaming License 1.0a, and separately (since January 2023)
  under Creative Commons CC-BY-4.0. This project relies on the CC-BY-4.0
  grant: "This work includes material taken from the System Reference
  Document 5.1 ('SRD 5.1') by Wizards of the Coast LLC and available at
  https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1
  is licensed under the Creative Commons Attribution 4.0 International
  License available at https://creativecommons.org/licenses/by/4.0/legalcode."

No other Wizards of the Coast content (published adventures, non-SRD
monsters/spells, setting material, etc.) is used anywhere in this project.
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "ATTRIBUTION.md").write_text(ATTRIBUTION, encoding="utf-8")

    with httpx.Client(timeout=30.0) as client:
        for filename in FILES:
            resp = client.get(f"{BASE_URL}/{filename}")
            resp.raise_for_status()
            data = resp.json()
            (OUT_DIR / filename).write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            count = len(data) if isinstance(data, list) else "?"
            print(f"{filename}: {count} entries")


if __name__ == "__main__":
    main()
