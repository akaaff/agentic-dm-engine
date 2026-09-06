"""Episode-level transcript judge (Day 15). Unlike every other node in this
package, this isn't wired into build_graph()'s per-action StateGraph -
scoring a whole transcript only makes sense once an episode (an autoplay run
or a live session) has finished, so cli/play.py's autoplay driver calls
judge_transcript() directly, once, at the end.
"""

from __future__ import annotations

from pydantic import BaseModel

from src.llm.providers import chat_structured, load_prompt


class JudgeResult(BaseModel):
    narrative_quality: int
    mechanical_consistency: int
    overall_score: int
    strengths: list[str]
    weaknesses: list[str]
    summary: str


def judge_transcript(narration_log: list[str]) -> JudgeResult:
    transcript = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(narration_log) if line)
    prompt = load_prompt("judge_rubric").format(transcript=transcript)
    return chat_structured(
        messages=[{"role": "user", "content": prompt}],
        schema=JudgeResult,
        temperature=0.2,
    )
