"""src/llm/providers.py's pure, non-network helpers - contains_cjk (issue
#16) is the only one worth hand-computed offline coverage; chat/chat_
structured/chat_english_only all make real HTTP calls and are covered by
tests/llm's live-Ollama checks instead (this project's established "LLM/
image-gen code gets live verification, not mocking" stance)."""

from src.llm.providers import contains_cjk


def test_contains_cjk_false_for_plain_english() -> None:
    assert contains_cjk("The goblin slinks forward, blade drawn.") is False


def test_contains_cjk_true_for_chinese_characters() -> None:
    assert contains_cjk("its步伐轻巧而敏捷") is True


def test_contains_cjk_true_for_chinese_punctuation() -> None:
    # A sentence that's otherwise pure ASCII but uses a full-width comma/
    # period - the exact shape of text the live-observed bug produced.
    assert contains_cjk("It moves closer，then stops。") is True


def test_contains_cjk_false_for_ordinary_non_ascii_typography() -> None:
    # Deliberately narrow (CJK ranges only, not "any non-ASCII") so
    # legitimate English prose - curly quotes, an em dash, an accented
    # name - doesn't trigger a needless retry.
    assert contains_cjk("“Watch out!” René shouted — then charged.") is False
