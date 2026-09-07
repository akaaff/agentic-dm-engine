"""CLI entrypoint for the LLM-assisted campaign generator (Day 23).

Usage: `uv run python -m src.cli.generate_campaign --size short_arc --id my_generated_campaign`

Writes `data/campaigns/<id>.yaml` plus one `data/campaigns/encounters/<id>__scene_N.yaml`
per combat scene - see `src.llm.campaign_generator` for how generation and
validation work. Needs a live Ollama.
"""

from __future__ import annotations

import argparse

from src.engine.campaign import CampaignSize
from src.llm.campaign_generator import generate_campaign


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="Campaign id (filename stem to write).")
    parser.add_argument(
        "--size", required=True, choices=["one_shot", "short_arc", "full"], help="Campaign size."
    )
    args = parser.parse_args()

    size: CampaignSize = args.size
    campaign = generate_campaign(campaign_id=args.id, size=size)

    print(f"Generated {campaign.id!r}: {campaign.title}")
    print(f"  {campaign.description.strip()}")
    for scene in campaign.scenes:
        detail = ""
        if scene.type == "combat":
            detail = f" (encounter_ref={scene.encounter_ref})"
        elif scene.type == "skill_challenge" and scene.skill_challenge_def is not None:
            detail = (
                f" (skill={scene.skill_challenge_def.skill}, dc={scene.skill_challenge_def.dc})"
            )
        print(f"  [{scene.id}] {scene.type}{detail}")


if __name__ == "__main__":
    main()
