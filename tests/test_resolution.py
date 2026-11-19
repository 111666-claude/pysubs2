"""
Tests for resolution migration (SSAFile.scale_resolution) and
coordinate override-tag scaling (pysubs2.formats.substation.scale_override_tags).

The fixed fixture tests/data/resolution_migration_720p.ass is a 1280x720 script
with two styles - a bottom-centered "Dialogue" style and a top-left "Sign"
(corner badge) style. The canonical migration target is 1920x1080, a uniform
1.5x scale.
"""
import re
import warnings
from pathlib import Path

import pytest

from pysubs2 import SSAFile
from pysubs2.exceptions import UnknownPlayResError
from pysubs2.formats.substation import scale_override_tags
from pysubs2.warnings import CoordinateOverflowWarning, UnsupportedOverrideTagWarning

DATA_DIR = Path(__file__).parent / "data"
FIXTURE_PATH = DATA_DIR / "resolution_migration_720p.ass"

SRC_X, SRC_Y = 1280, 720
DST_X, DST_Y = 1920, 1080
SCALE = 1.5


def load_720p() -> SSAFile:
    return SSAFile.load(FIXTURE_PATH)


def extract_tag(text: str, name: str) -> str:
    match = re.search(r"\\" + name + r"\([^)]*\)", text)
    assert match is not None, f"\\{name} not found in {text!r}"
    return match.group(0)


# ---------------------------------------------------------------------------
# SSAFile.scale_resolution: happy path
# ---------------------------------------------------------------------------

def test_playres_updated_and_copy_returned() -> None:
    subs = load_720p()
    migrated = subs.scale_resolution(DST_X, DST_Y)

    assert migrated is not subs
    assert migrated.info["PlayResX"] == str(DST_X)
    assert migrated.info["PlayResY"] == str(DST_Y)
    # original is untouched
    assert subs.info["PlayResX"] == str(SRC_X)
    assert subs.info["PlayResY"] == str(SRC_Y)
    assert subs.styles["Dialogue"].fontsize == 40.0


def test_styles_scaled_for_dialogue_and_sign() -> None:
    subs = load_720p()
    migrated = subs.scale_resolution(DST_X, DST_Y)

    dialogue = migrated.styles["Dialogue"]
    assert dialogue.fontsize == pytest.approx(60.0)
    assert dialogue.outline == pytest.approx(3.0)
    assert dialogue.shadow == pytest.approx(4.5)
    assert dialogue.spacing == pytest.approx(1.5)
    assert (dialogue.marginl, dialogue.marginr, dialogue.marginv) == (30, 30, 45)

    sign = migrated.styles["Sign"]
    assert sign.fontsize == pytest.approx(90.0)
    assert sign.outline == pytest.approx(6.0)
    assert sign.shadow == pytest.approx(0.0)
    assert sign.spacing == pytest.approx(3.0)
    assert (sign.marginl, sign.marginr, sign.marginv) == (15, 15, 15)
    # non-pixel fields are preserved
    assert sign.alignment == subs.styles["Sign"].alignment
    assert sign.bold and sign.italic
    assert sign.primarycolor == subs.styles["Sign"].primarycolor


def test_event_margins_scaled() -> None:
    subs = load_720p()
    migrated = subs.scale_resolution(DST_X, DST_Y)

    margin_event = next(e for e in migrated.events if e.text == "Dialogue with event margins")
    assert (margin_event.marginl, margin_event.marginr, margin_event.marginv) == (60, 75, 90)
    # zero margins stay zero
    assert migrated[0].marginl == 0 and migrated[0].marginr == 0 and migrated[0].marginv == 0


def test_relative_position_preserved() -> None:
    # positions in both canvases must describe the same relative point
    subs = load_720p()
    migrated = subs.scale_resolution(DST_X, DST_Y)

    pos_event = next(e for e in migrated.events if "Positioned dialogue" in e.text)
    match = re.fullmatch(r"\{\\pos\((-?[\d.]+),(-?[\d.]+)\)\}(.*)", pos_event.text)
    assert match is not None
    x, y = float(match.group(1)), float(match.group(2))
    assert x / DST_X == pytest.approx(640 / SRC_X)
    assert y / DST_Y == pytest.approx(690 / SRC_Y)
    assert match.group(3) == "Positioned dialogue"

    move_event = next(e for e in migrated.events if e.text.startswith(r"{\move(150,150"))
    assert extract_tag(move_event.text, "move") == r"\move(150,150,450,300)"

    timed_move_event = next(e for e in migrated.events if "Moving sign with timing" in e.text)
    # t1/t2 timestamps (500, 1000 ms) must not be scaled, unlike coordinates
    assert extract_tag(timed_move_event.text, "move") == r"\move(0,0,960,540,500,1000)"

    org_event = next(e for e in migrated.events if "Origin line" in e.text)
    assert extract_tag(org_event.text, "org") == r"\org(960,540)"

    clip_event = next(e for e in migrated.events if "Clipped sign" in e.text)
    assert extract_tag(clip_event.text, "clip") == r"\clip(15,30,150,300)"


def test_text_timing_and_unrelated_override_tags_preserved() -> None:
    subs = load_720p()
    migrated = subs.scale_resolution(DST_X, DST_Y)

    for old, new in zip(subs.events, migrated.events):
        assert old.start == new.start
        assert old.end == new.end
        assert old.style == new.style
        assert old.type == new.type
        assert old.plaintext == new.plaintext

    unrelated = next(e for e in migrated.events if e.text.startswith(r"{\fs80"))
    assert r"\fs80" in unrelated.text
    assert r"\b1" in unrelated.text
    assert r"\fad(100,200)" in unrelated.text
    assert r"\an7" in unrelated.text
    assert r"\k40" in unrelated.text

    # drawing commands are not coordinate tags and must stay byte-identical
    drawing = next(e for e in migrated.events if r"{\p1}" in e.text)
    assert drawing.text == r"{\p1}m 0 0 l 100 0 100 100 0 100{\p0}"


def test_comments_are_migrated_as_well() -> None:
    migrated = load_720p().scale_resolution(DST_X, DST_Y)
    comment = next(e for e in migrated.events if e.is_comment)
    assert extract_tag(comment.text, "pos") == r"\pos(150,150)"


def test_write_back_and_reread() -> None:
    subs = load_720p()
    migrated = subs.scale_resolution(DST_X, DST_Y)

    ass_text = migrated.to_string("ass")
    reread = SSAFile.from_string(ass_text)
    assert reread.equals(migrated)
    assert reread.info["PlayResX"] == str(DST_X)
    assert reread.info["PlayResY"] == str(DST_Y)
    assert reread.styles["Dialogue"].fontsize == pytest.approx(60.0)
    assert reread.styles["Sign"].marginl == 15

    # SSA round-trip also keeps the new PlayRes and scaled values
    ssa_text = migrated.to_string("ssa")
    reread_ssa = SSAFile.from_string(ssa_text)
    assert reread_ssa.info["PlayResX"] == str(DST_X)
    assert reread_ssa.styles["Dialogue"].fontsize == pytest.approx(60.0)
    assert reread_ssa[1].text.startswith(r"{\pos(960,1035)}")


def test_other_scale_factors_and_rounding() -> None:
    # 1280x720 -> 640x360 is a uniform 0.5x downscale
    subs = load_720p()
    migrated = subs.scale_resolution(640, 360)
    assert migrated.styles["Dialogue"].fontsize == pytest.approx(20.0)
    assert (migrated.styles["Dialogue"].marginl,
            migrated.styles["Dialogue"].marginv) == (10, 15)

    # half-pixel margins are rounded to the nearest integer
    margin_event = next(e for e in migrated.events if e.text == "Dialogue with event margins")
    assert (margin_event.marginl, margin_event.marginr, margin_event.marginv) == (20, 25, 30)

    # integer target with fractional scale factor: 1280 -> 960 is 0.75x
    fractional = subs.scale_resolution(960, 540)
    assert fractional.styles["Dialogue"].marginl == 15  # 20 * 0.75
    pos_event = next(e for e in fractional.events if "Positioned dialogue" in e.text)
    assert extract_tag(pos_event.text, "pos") == r"\pos(480,517.5)"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_missing_playres_raises() -> None:
    subs = SSAFile()
    subs.info.pop("PlayResX", None)
    subs.info.pop("PlayResY", None)
    with pytest.raises(UnknownPlayResError):
        subs.scale_resolution(1920, 1080)


def test_invalid_playres_values_raise() -> None:
    subs = SSAFile()
    subs.info["PlayResX"] = "nonsense"
    subs.info["PlayResY"] = "720"
    with pytest.raises(UnknownPlayResError):
        subs.scale_resolution(1920, 1080)

    subs.info["PlayResX"] = "0"
    subs.info["PlayResY"] = "720"
    with pytest.raises(UnknownPlayResError):
        subs.scale_resolution(1920, 1080)

    subs.info["PlayResX"] = "-1280"
    subs.info["PlayResY"] = "720"
    with pytest.raises(UnknownPlayResError):
        subs.scale_resolution(1920, 1080)

    # only one dimension present
    subs.info.clear()
    subs.info["PlayResX"] = "1280"
    with pytest.raises(UnknownPlayResError):
        subs.scale_resolution(1920, 1080)


def test_playres_set_programmatically_as_int() -> None:
    subs = SSAFile()
    subs.info["PlayResX"] = 1280  # type: ignore[assignment]
    subs.info["PlayResY"] = 720  # type: ignore[assignment]
    migrated = subs.scale_resolution(1920, 1080)
    assert migrated.info["PlayResX"] == "1920"
    assert migrated.info["PlayResY"] == "1080"


def test_non_proportional_target_raises() -> None:
    subs = load_720p()
    with pytest.raises(ValueError, match="aspect ratio"):
        subs.scale_resolution(1920, 800)
    # original resolution must remain unchanged after failure
    assert subs.info["PlayResX"] == "1280"
    assert subs.styles["Dialogue"].fontsize == 40.0


def test_zero_negative_and_wrong_typed_targets() -> None:
    subs = load_720p()
    for bad_x, bad_y in [(0, 1080), (1920, 0), (-1920, -1080)]:
        with pytest.raises(ValueError, match="positive"):
            subs.scale_resolution(bad_x, bad_y)

    with pytest.raises(TypeError):
        subs.scale_resolution(1920.0, 1080.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        subs.scale_resolution("1920", "1080")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        subs.scale_resolution(True, 1080)  # type: ignore[arg-type]


def test_unsupported_and_malformed_tags_warn_and_remain() -> None:
    subs = load_720p()
    with pytest.warns(UnsupportedOverrideTagWarning) as record:
        migrated = subs.scale_resolution(DST_X, DST_Y)

    texts = [e.text for e in migrated.events]
    assert any(text.startswith(r"{\iclip(1,2,3,4)}") for text in texts)
    assert any(r"{\clip(m 0 0 l 100 0 100 100)}" in text for text in texts)
    assert any(text.startswith(r"{\t(\clip(1,2,3,4))}") for text in texts)
    assert any(text.startswith(r"{\pos(1,)}") for text in texts)
    assert len(record) >= 4


def test_overflow_coordinates_warn_but_are_scaled() -> None:
    subs = load_720p()
    with pytest.warns(CoordinateOverflowWarning):
        migrated = subs.scale_resolution(DST_X, DST_Y)
    overflow_event = next(e for e in migrated.events if "Overflowing position" in e.text)
    # 1300 * 1.5 = 1950 (> 1920), -10 * 1.5 = -15 (< 0); scaled but not clamped
    assert extract_tag(overflow_event.text, "pos") == r"\pos(1950,-15)"


# ---------------------------------------------------------------------------
# Repeated / chained migration
# ---------------------------------------------------------------------------

def test_repeated_migration_is_noop() -> None:
    subs = load_720p()
    once = subs.scale_resolution(DST_X, DST_Y)
    # unsupported tags re-warn on every pass, but a 1.0x migration must not change data
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        twice = once.scale_resolution(DST_X, DST_Y)
    assert twice.equals(once)
    assert twice.info["PlayResX"] == str(DST_X)
    assert twice.styles["Dialogue"].fontsize == once.styles["Dialogue"].fontsize
    assert twice[1].text == once[1].text


def test_chained_migration_composes() -> None:
    subs = load_720p()
    via_360p = subs.scale_resolution(640, 360).scale_resolution(DST_X, DST_Y)
    direct = subs.scale_resolution(DST_X, DST_Y)

    assert via_360p.info["PlayResX"] == direct.info["PlayResX"]
    assert via_360p.styles["Dialogue"].fontsize == pytest.approx(
        direct.styles["Dialogue"].fontsize
    )
    assert via_360p.styles["Sign"].marginv == direct.styles["Sign"].marginv
    via_pos = next(e for e in via_360p.events if "Positioned dialogue" in e.text)
    direct_pos = next(e for e in direct.events if "Positioned dialogue" in e.text)
    assert extract_tag(via_pos.text, "pos") == extract_tag(direct_pos.text, "pos")


# ---------------------------------------------------------------------------
# scale_override_tags unit tests
# ---------------------------------------------------------------------------

def test_scale_override_tags_all_supported_tags() -> None:
    text = r"{\pos(10,20)\org(30,40)\move(1,2,3,4)\clip(5,6,7,8)}x"
    out = scale_override_tags(text, 2, 2, 1920, 1080)
    assert out == r"{\pos(20,40)\org(60,80)\move(2,4,6,8)\clip(10,12,14,16)}x"


def test_scale_override_tags_separate_axes() -> None:
    out = scale_override_tags(r"{\pos(100,100)}", 2, 3, 1920, 1080)
    assert out == r"{\pos(200,300)}"


def test_scale_override_tags_decimal_formatting() -> None:
    out = scale_override_tags(r"{\pos(1,1)\move(1.5,2.5,3,4)}", 1.5, 1.5, 1920, 1080)
    assert r"\pos(1.5,1.5)" in out
    assert r"\move(2.25,3.75,4.5,6)" in out


def test_scale_override_tags_without_tags_unchanged() -> None:
    text = r"plain\Ntext {\b1}bold{\b0}"
    assert scale_override_tags(text, 2, 2, 1920, 1080) == text


def test_scale_override_tags_preserves_casing_and_other_tags() -> None:
    out = scale_override_tags(r"{\POS(10,20)\fs40\c&HFF&}", 2, 2, 1920, 1080)
    assert out == r"{\POS(20,40)\fs40\c&HFF&}"


def test_scale_override_tags_multiple_blocks() -> None:
    out = scale_override_tags(r"{\pos(1,1)}a{\pos(2,2)}b", 2, 2, 1920, 1080)
    assert out == r"{\pos(2,2)}a{\pos(4,4)}b"


def test_scale_override_tags_same_resolution_canonicalizes_numbers() -> None:
    # factor 1.0 rewrites handled tags canonically but changes nothing visually
    out = scale_override_tags(r"{\pos(10.0,20)}", 1.0, 1.0, 1280, 720)
    assert out == r"{\pos(10,20)}"


def test_scale_override_tags_negative_coordinates() -> None:
    with pytest.warns(CoordinateOverflowWarning):
        out = scale_override_tags(r"{\pos(-10,-20)}", 2, 2, 1920, 1080)
    assert out == r"{\pos(-20,-40)}"


def test_scale_override_tags_move_timing_untouched() -> None:
    out = scale_override_tags(r"{\move(10,10,20,20,250,750)}", 2, 2, 1920, 1080)
    assert out == r"{\move(20,20,40,40,250,750)}"


def test_scale_override_tags_unsupported_cases() -> None:
    unchanged_cases = [
        r"{\iclip(1,2,3,4)}",
        r"{\clip(m 0 0 l 1 1)}",
        r"{\t(\clip(1,2,3,4))}",
        r"{\pos(1,)}",
        r"{\pos(1,2,3)}",
        r"{\move(1,2,3)}",
        r"{\pos}",
    ]
    for text in unchanged_cases:
        with pytest.warns(UnsupportedOverrideTagWarning):
            assert scale_override_tags(text, 2, 2, 1920, 1080) == text


def test_scale_override_tags_ignores_lookalike_tag_names() -> None:
    # tags whose name merely starts with a handled name are unrelated tags
    for text in [r"{\movep(1,2)}", r"{\clip1(1,2,3,4)}", r"{\pos10,20}"]:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert scale_override_tags(text, 2, 2, 1920, 1080) == text


def test_scale_override_tags_overflow_detection() -> None:
    # in-canvas: no warning
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        scale_override_tags(r"{\pos(1920,1080)}", 1, 1, 1920, 1080)
    assert not [w for w in record if issubclass(w.category, CoordinateOverflowWarning)]

    with pytest.warns(CoordinateOverflowWarning):
        scale_override_tags(r"{\clip(0,0,2000,10)}", 1, 1, 1920, 1080)
