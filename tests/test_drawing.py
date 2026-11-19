import os.path

import pytest

import pysubs2
from pysubs2.drawing import (
    MAX_DRAWING_COMMANDS,
    parse_drawing_sections,
    replace_drawing_sections,
)
from pysubs2.exceptions import DrawingError

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
MIXED_FIXTURE = os.path.join(DATA_DIR, "drawing_mixed.ass")

#: Drawing text of the first event of the mixed fixture (multi-segment Bézier mixed with text).
FIXTURE_DRAWING_BOUNDS = (10.0, 10.0, 90.0, 90.0)


def test_parse_simple_drawing() -> None:
    text = r"{\p1}m 0 0 l 100 0 100 100 0 100{\p0}"
    sections = parse_drawing_sections(text)

    assert len(sections) == 1
    section = sections[0]
    assert section.scale == 1
    assert section.start == 0
    assert section.content_start == len(r"{\p1}")
    assert section.end == len(text) - len(r"{\p0}")
    assert text[section.content_start:section.end] == "m 0 0 l 100 0 100 100 0 100"

    assert [command.type for command in section.commands] == ["m", "l"]
    assert section.commands[0].points == [(0.0, 0.0)]
    assert section.commands[1].points == [(100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    assert section.bounds == (0.0, 0.0, 100.0, 100.0)


def test_no_drawing() -> None:
    assert parse_drawing_sections("Hello, world!") == []
    assert parse_drawing_sections(r"{\b1}bold{\b0} and \N a newline") == []
    assert parse_drawing_sections(r"{\pos(10,20)\p0}not a drawing") == []


def test_bounds_are_in_script_coordinates() -> None:
    # with \p2, drawing units are 2x script coordinates
    sections = parse_drawing_sections(r"{\p2}m 0 0 l 20 40{\p0}")
    assert sections[0].scale == 2
    assert sections[0].coordinate_scale == 2
    assert sections[0].bounds == (0.0, 0.0, 10.0, 20.0)


def test_multiple_sections() -> None:
    text = r"{\p1}m 0 0 l 1 1{\p0}text{\p1}m 5 5 l 6 6{\p0}"
    sections = parse_drawing_sections(text)
    assert len(sections) == 2
    assert sections[0].bounds == (0.0, 0.0, 1.0, 1.0)
    assert sections[1].bounds == (5.0, 5.0, 6.0, 6.0)


def test_mixed_fixture_bounds() -> None:
    subs = pysubs2.load(MIXED_FIXTURE)
    event = subs[0]
    sections = parse_drawing_sections(event.text)

    assert len(sections) == 1
    section = sections[0]
    assert [command.type for command in section.commands] == ["m", "l", "b", "b", "l"]
    # multi-segment Bézier: each b command is a group of 3 points
    assert len(section.commands[2].points) == 3
    assert len(section.commands[3].points) == 3
    assert section.bounds == FIXTURE_DRAWING_BOUNDS
    # section positions point at the drawing content in the original text
    assert event.text[section.start:section.content_start] == r"{\an8\pos(320,240)\p1}"
    assert event.text[section.end:] == r"{\p0} done\Nsecond {\b1}line{\b0}"

    # the second event has no drawings
    assert parse_drawing_sections(subs[1].text) == []


def test_writeback_without_changes_is_identity() -> None:
    subs = pysubs2.load(MIXED_FIXTURE)
    text = subs[0].text
    sections = parse_drawing_sections(text)
    assert replace_drawing_sections(text, sections) == text


def test_move_and_scale_roundtrip() -> None:
    subs = pysubs2.load(MIXED_FIXTURE)
    text = subs[0].text

    sections = parse_drawing_sections(text)
    for section in sections:
        section.move(5, -10)
        section.scale_by(2.0, origin=(10.0, 10.0))
    new_text = replace_drawing_sections(text, sections)

    # non-drawing content (plain text, newlines, non-drawing override tags) is preserved
    assert new_text != text
    assert new_text.endswith(r"{\p0} done\Nsecond {\b1}line{\b0}")
    assert new_text.startswith(r"Arrow: {\an8\pos(320,240)\p1}")

    # bounds: (10,10,90,90) -> move -> (15,0,95,80) -> scale 2x around (10,10) -> (20,-10,180,150)
    new_sections = parse_drawing_sections(new_text)
    assert len(new_sections) == 1
    assert new_sections[0].bounds == (20.0, -10.0, 180.0, 150.0)

    # inverse transform restores the original text exactly
    for section in new_sections:
        section.scale_by(0.5, origin=(10.0, 10.0))
        section.move(-5, 10)
    assert replace_drawing_sections(new_text, new_sections) == text


def test_ass_file_roundtrip_with_transformed_drawing() -> None:
    subs = pysubs2.load(MIXED_FIXTURE)
    sections = parse_drawing_sections(subs[0].text)
    sections[0].move(100, 200)
    subs[0].text = replace_drawing_sections(subs[0].text, sections)

    # the file still saves as valid ASS and reloads with the transformed drawing
    ass_output = subs.to_string("ass")
    subs2 = pysubs2.SSAFile.from_string(ass_output)
    assert subs2.equals(subs)
    new_sections = parse_drawing_sections(subs2[0].text)
    assert new_sections[0].bounds == (110.0, 210.0, 190.0, 290.0)
    assert subs2[1].text == "plain text only"


def test_fractional_coordinates() -> None:
    text = r"{\p1}m 0.5 -1.25 l 2.5 3{\p0}"
    sections = parse_drawing_sections(text)
    min_x, min_y, max_x, max_y = sections[0].bounds
    assert (min_x, min_y, max_x, max_y) == (0.5, -1.25, 2.5, 3.0)
    assert replace_drawing_sections(text, sections) == text


def test_error_invalid_command() -> None:
    with pytest.raises(DrawingError) as excinfo:
        parse_drawing_sections(r"{\p1}m 0 0 x 1 1{\p0}")
    assert "Invalid drawing command" in str(excinfo.value)
    assert excinfo.value.position is not None


def test_error_missing_coordinates() -> None:
    with pytest.raises(DrawingError, match="missing coordinates"):
        parse_drawing_sections(r"{\p1}m l 1 1{\p0}")


def test_error_incomplete_bezier() -> None:
    with pytest.raises(DrawingError, match="Bézier"):
        parse_drawing_sections(r"{\p1}b 0 0 1 1{\p0}")


def test_error_odd_coordinate_count() -> None:
    with pytest.raises(DrawingError, match="Odd number of coordinates"):
        parse_drawing_sections(r"{\p1}l 1 2 3{\p0}")


def test_error_non_finite_number() -> None:
    with pytest.raises(DrawingError, match="Non-finite"):
        parse_drawing_sections(r"{\p1}m nan 0{\p0}")
    with pytest.raises(DrawingError, match="Non-finite"):
        parse_drawing_sections(r"{\p1}m 1e999 0{\p0}")


def test_error_empty_path() -> None:
    with pytest.raises(DrawingError, match="Empty drawing path"):
        parse_drawing_sections(r"{\p1}{\p0}")
    with pytest.raises(DrawingError, match="Empty drawing path"):
        parse_drawing_sections(r"text {\p1}")
    with pytest.raises(DrawingError, match="Empty drawing path"):
        parse_drawing_sections(r"{\p1}   {\p0}")


def test_error_too_many_commands() -> None:
    text = "{\\p1}" + "m 0 0 " * (MAX_DRAWING_COMMANDS + 1) + "{\\p0}"
    with pytest.raises(DrawingError, match="Too many drawing commands"):
        parse_drawing_sections(text)


def test_error_mid_path_p0_switch() -> None:
    with pytest.raises(DrawingError) as excinfo:
        parse_drawing_sections(r"{\p1}m 0 0 l 1 1{\p0}m 2 2 l 3 3")
    assert r"\p0" in str(excinfo.value)
    assert excinfo.value.position is not None


def test_error_context_contains_position_and_snippet() -> None:
    with pytest.raises(DrawingError) as excinfo:
        parse_drawing_sections(r"prefix {\p1}m 0 0 zzz 1 1{\p0} suffix")
    error = excinfo.value
    assert error.position is not None
    assert error.snippet is not None
    assert "zzz" in error.snippet
    assert f"at position {error.position}" in str(error)
