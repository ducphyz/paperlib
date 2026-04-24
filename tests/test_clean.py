from paperlib.pipeline.clean import clean_text


def test_clean_text_replaces_ligatures():
    assert clean_text("ﬁ ﬂ ﬀ ﬃ ﬄ") == "fi fl ff ffi ffl"


def test_clean_text_removes_ascii_control_chars_but_keeps_newline_and_tab():
    assert clean_text("a\x00b\x1fc\nx\td") == "abc\nx d"


def test_clean_text_normalizes_line_endings():
    assert clean_text("a\r\nb\rc") == "a\nb\nc"


def test_clean_text_collapses_excessive_newlines():
    assert clean_text("a\n\n\nb\n\n\n\nc") == "a\n\nb\n\nc"


def test_clean_text_collapses_spaces_and_tabs():
    assert clean_text("a   b\t\tc \t d") == "a b c d"


def test_clean_text_strips_whitespace_per_line():
    assert clean_text("  a  \n\t b\t ") == "a\nb"


def test_clean_text_is_idempotent():
    text = "  ﬁ\t\tfoo\r\n\r\n\r\nbar\x00  "
    assert clean_text(clean_text(text)) == clean_text(text)


def test_clean_text_none_returns_empty_string():
    assert clean_text(None) == ""
