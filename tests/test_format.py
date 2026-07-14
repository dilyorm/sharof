from sharof.bot import TG_LIMIT, chunks, plain


def test_plain_strips_markdown_telegram_would_show_literally():
    out = plain(
        "## Folders\n"
        "1. **Folders**:\n"
        "   - `data` (last modified: 7/10/2026)\n"
        "* `pfx2.py`\n"
        "Use ```code``` here\n"
    )
    for mark in ("**", "##", "`", "```"):
        assert mark not in out
    assert "data (last modified: 7/10/2026)" in out
    assert "- pfx2.py" in out  # markdown bullet becomes a plain dash


def test_plain_keeps_ordinary_text_alone():
    assert plain("kaggle/rogii: 7 files, 6 folders") == "kaggle/rogii: 7 files, 6 folders"


def test_chunks_splits_long_replies_on_line_boundaries():
    text = "\n".join(f"line {i}" for i in range(2000))
    parts = chunks(text)
    assert len(parts) > 1
    assert all(len(p) <= TG_LIMIT for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_chunks_hard_splits_a_single_monster_line():
    parts = chunks("x" * (TG_LIMIT * 2 + 5))
    assert all(len(p) <= TG_LIMIT for p in parts)
    assert sum(len(p) for p in parts) == TG_LIMIT * 2 + 5


def test_chunks_never_returns_empty():
    assert chunks("   ") == ["(empty)"]
