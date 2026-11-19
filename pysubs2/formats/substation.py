# mypy: disable-error-code="override"

import logging
import re
import warnings
from typing import TYPE_CHECKING, Any, NotRequired, TextIO, TypedDict, Unpack, override

from ..common import SSA_ALIGNMENT, Alignment, Color
from ..ssaevent import SSAEvent
from ..ssastyle import SSAStyle
from ..time import TIMESTAMP, TIMESTAMP_SHORT, make_time, ms_to_times, timestamp_to_ms
from ..warnings import (
    CoordinateOverflowWarning,
    SubtitleAttributeWarning,
    TimestampOverflow,
    TimestampUnderflow,
    UnsupportedOverrideTagWarning,
)
from .base import FormatBase

if TYPE_CHECKING:
    from ..ssafile import SSAFile


logger = logging.getLogger(__name__)


def ass_to_ssa_alignment(i: int) -> int:
    warnings.warn("ass_to_ssa_alignment function is deprecated, please use the Alignment enum", DeprecationWarning)
    return SSA_ALIGNMENT[i-1]

def ssa_to_ass_alignment(i: int) -> int:
    warnings.warn("ssa_to_ass_alignment function is deprecated, please use the Alignment enum", DeprecationWarning)
    return SSA_ALIGNMENT.index(i) + 1

SECTION_HEADING = re.compile(
    r"^.{,3}"  # allow 3 chars at start of line for BOM
    r"\["  # open square bracket
    r"[^]]*[a-z][^]]*"  # inside square brackets, at least one lowercase letter (this guards vs. uuencoded font data)
    r"]"  # close square bracket
)

ATTACHMENT_FILE_HEADING = re.compile(r"(fontname|filename):\s+(?P<name>\S+)")

STYLE_FORMAT_LINE = {
    "ass": "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic,"
           " Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment,"
           " MarginL, MarginR, MarginV, Encoding",
    "ssa": "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, TertiaryColour, BackColour, Bold, Italic,"
           " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, AlphaLevel, Encoding"
}

STYLE_FIELDS = {
    "ass": ["fontname", "fontsize", "primarycolor", "secondarycolor", "outlinecolor", "backcolor", "bold", "italic",
            "underline", "strikeout", "scalex", "scaley", "spacing", "angle", "borderstyle", "outline", "shadow",
            "alignment", "marginl", "marginr", "marginv", "encoding"],
    "ssa": ["fontname", "fontsize", "primarycolor", "secondarycolor", "tertiarycolor", "backcolor", "bold", "italic",
            "borderstyle", "outline", "shadow", "alignment", "marginl", "marginr", "marginv", "alphalevel", "encoding"]
}

EVENT_FORMAT_LINE = {
    "ass": "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    "ssa": "Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
}

EVENT_FIELDS = {
    "ass": ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"],
    "ssa": ["marked", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
}

#: Largest timestamp allowed in SubStation, ie. 9:59:59.99.
MAX_REPRESENTABLE_TIME = make_time(h=10) - 10

def color_to_ass_rgba(c: Color) -> str:
    return f"&H{((c.a << 24) | (c.b << 16) | (c.g << 8) | c.r):08X}"

def color_to_ssa_rgb(c: Color) -> str:
    return f"{((c.b << 16) | (c.g << 8) | c.r)}"

def rgba_to_color(s: str) -> Color:
    if s.startswith("&"):
        # Example: "&HAABBCCDD" (this is not a typical "0xAABBCCDD" value; lint to replace with base=0 is misplaced)
        x = int(s[2:], base=16)  # noqa: FURB166
    else:
        x = int(s)
    r = x & 0xff
    g = (x >> 8) & 0xff
    b = (x >> 16) & 0xff
    a = (x >> 24) & 0xff
    return Color(r, g, b, a)

def is_valid_field_content(s: str) -> bool:
    """
    Returns True if string s can be stored in a SubStation field.

    Fields are written in CSV-like manner, thus commas and/or newlines
    are not acceptable in the string.

    """
    return "\n" not in s and "," not in s


def parse_tags(text: str, style: SSAStyle = SSAStyle.DEFAULT_STYLE,
               styles: dict[str, SSAStyle] | None = None,
               skip_empty_fragments: bool = False) -> list[tuple[str, SSAStyle]]:
    """
    Split text into fragments with computed SSAStyles.
    
    Returns list of tuples (fragment, style), where fragment is a part of text
    between two brace-delimited override sequences, and style is the computed
    styling of the fragment, ie. the original style modified by all override
    sequences before the fragment.
    
    Newline and non-breakable space overrides are left as-is.
    
    Supported override tags:
    
    - i, b, u, s
    - fn
    - r (with or without style name)
    
    """
    if styles is None:
        styles = {}
    
    fragments = SSAEvent.OVERRIDE_SEQUENCE.split(text)
    if len(fragments) == 1:
        if skip_empty_fragments and not text:
            return []
        else:
            return [(text, style)]
    
    def apply_overrides(all_overrides: str) -> SSAStyle:
        s = style.copy()
        for tag in re.findall(r"\\[ibusp][0-9]|\\r[a-zA-Z_0-9 ]*|\\fn[a-zA-Z_0-9 ]+", all_overrides):
            if tag == r"\r":
                s = style.copy() # reset to original line style
            elif tag.startswith(r"\r"):
                name = tag[2:]
                if name in styles:
                    # reset to named style
                    s = styles[name].copy()
            elif tag.startswith(r"\fn"):
                fontname = tag[3:]
                s.fontname = fontname
            else:
                if "i" in tag:
                    s.italic = "1" in tag
                elif "b" in tag:
                    s.bold = "1" in tag
                elif "u" in tag:
                    s.underline = "1" in tag
                elif "s" in tag:
                    s.strikeout = "1" in tag
                elif "p" in tag:
                    try:
                        scale = int(tag[2:])
                    except (ValueError, IndexError):
                        continue

                    s.drawing = scale > 0
        return s
    
    overrides = SSAEvent.OVERRIDE_SEQUENCE.findall(text)
    overrides_prefix_sum = ["".join(overrides[:i]) for i in range(len(overrides) + 1)]
    computed_styles = map(apply_overrides, overrides_prefix_sum)
    output = list(zip(fragments, computed_styles))
    if skip_empty_fragments:
        output = [(fragment, sty) for fragment, sty in output if fragment]
    return output


# ---------------------------------------------------------------------------
# Resolution scaling of override tags
# ---------------------------------------------------------------------------

#: An ASS coordinate number (signed integer or decimal), no exponents.
_NUMBER = r"-?\d+(?:\.\d+)?"

#: The coordinate-bearing override tags handled by :func:`scale_override_tags`.
COORDINATE_TAG = re.compile(
    r"\\(?P<name>pos|org|move|iclip|clip)\b\s*(?P<args>\([^)]*\))?",
    re.IGNORECASE,
)

#: Animated override tag which may contain other tags nested in its parentheses.
_ANIMATED_TAG = re.compile(r"\\t\s*\(", re.IGNORECASE)


def _format_number(x: float) -> str:
    """Format a scaled ASS number compactly, without exponent notation."""
    if x.is_integer():
        return str(int(x))
    return f"{x:f}".rstrip("0").rstrip(".")


def _parse_numbers(args: str, expected: frozenset[int]) -> list[float] | None:
    """Parse comma-separated numbers inside ``(...)``; return None when malformed."""
    values: list[float] = []
    for part in args.split(","):
        part = part.strip()
        if not re.fullmatch(_NUMBER, part):
            return None
        values.append(float(part))
    if len(values) not in expected:
        return None
    return values


def _animated_tag_spans(block: str) -> list[tuple[int, int]]:
    """Return char spans of complete ``\\t(...)`` expressions (parentheses matched)."""
    spans: list[tuple[int, int]] = []
    for match in _ANIMATED_TAG.finditer(block):
        depth = 0
        for i in range(match.end() - 1, len(block)):
            if block[i] == "(":
                depth += 1
            elif block[i] == ")":
                depth -= 1
                if depth == 0:
                    spans.append((match.start(), i + 1))
                    break
    return spans


def _check_overflow(tag_name: str, coordinates: list[float],
                    play_res_x: int, play_res_y: int) -> None:
    """Warn when a scaled coordinate does not fit inside the target canvas."""
    xs = coordinates[0::2]
    ys = coordinates[1::2]
    if any(x < 0 or x > play_res_x for x in xs) or any(y < 0 or y > play_res_y for y in ys):
        warnings.warn(
            f"Scaled \\{tag_name} coordinate {coordinates!r} is outside of target resolution "
            f"{play_res_x}x{play_res_y}; it was scaled but left unclamped",
            CoordinateOverflowWarning,
        )


def scale_override_tags(text: str, scale_x: float, scale_y: float,
                        play_res_x: int, play_res_y: int) -> str:
    """
    Scale coordinate-bearing SubStation override tags in event text.

    The ``\\pos``, ``\\move`` and ``\\org`` tags and the rectangular ``\\clip``
    tag are scaled by multiplying their x/y coordinates with ``scale_x`` and
    ``scale_y``. Text, timing and all other override tags (eg. ``\\fs``, ``\\b``,
    karaoke tags) are preserved unchanged.

    Tags that cannot be scaled are left as-is and an
    :class:`~pysubs2.warnings.UnsupportedOverrideTagWarning` is emitted:

    - inverse clips (``\\iclip``) and vector-drawing clips (``\\clip`` with
      drawing commands instead of four rectangle coordinates),
    - malformed coordinate tags (eg. unparseable numbers or wrong argument count),
    - coordinate tags nested inside an animated ``\\t(...)`` tag.

    Coordinates that end up outside of the target canvas (``play_res_x`` x
    ``play_res_y``) are still scaled, but a
    :class:`~pysubs2.warnings.CoordinateOverflowWarning` is emitted.

    Arguments:
        text: Event text containing override tags.
        scale_x: Horizontal scaling factor.
        scale_y: Vertical scaling factor (equal to ``scale_x`` for an
            aspect-ratio-preserving migration).
        play_res_x: Target script width, used for overflow checks.
        play_res_y: Target script height, used for overflow checks.

    Returns:
        The event text with coordinate tags rescaled. Note that numbers in
        handled tags are reformatted canonically (eg. ``24.0`` becomes ``24``).

    """
    def scale_block(match: re.Match[str]) -> str:
        block = match.group(0)
        animated_spans = _animated_tag_spans(block)
        pieces: list[str] = []
        pos = 0

        for tag in COORDINATE_TAG.finditer(block):
            pieces.append(block[pos:tag.start()])
            pos = tag.end()
            name = tag.group("name").lower()
            raw_name = tag.group("name")
            args = tag.group("args")

            if any(span_start < tag.start() < span_end for span_start, span_end in animated_spans):
                warnings.warn(
                    f"\\{name} tag nested inside an animated \\t(...) tag is not supported, "
                    "the tag was left unchanged",
                    UnsupportedOverrideTagWarning,
                )
                pieces.append(tag.group(0))
                continue

            if name == "iclip":
                warnings.warn(
                    r"\iclip tags cannot be resolution-scaled, the tag was left unchanged",
                    UnsupportedOverrideTagWarning,
                )
                pieces.append(tag.group(0))
                continue

            if args is None:
                warnings.warn(
                    f"Malformed \\{name} tag without arguments, the tag was left unchanged",
                    UnsupportedOverrideTagWarning,
                )
                pieces.append(tag.group(0))
                continue

            inner = args[1:-1]

            if name in ("pos", "org"):
                values = _parse_numbers(inner, frozenset({2}))
                if values is None:
                    warnings.warn(
                        f"Malformed \\{name} tag {tag.group(0)!r}, the tag was left unchanged",
                        UnsupportedOverrideTagWarning,
                    )
                    pieces.append(tag.group(0))
                    continue
                scaled = [values[0] * scale_x, values[1] * scale_y]
                _check_overflow(name, scaled, play_res_x, play_res_y)
                pieces.append(rf"\{raw_name}({_format_number(scaled[0])},{_format_number(scaled[1])})")
            elif name == "move":
                values = _parse_numbers(inner, frozenset({4, 6}))
                if values is None:
                    warnings.warn(
                        f"Malformed \\move tag {tag.group(0)!r}, the tag was left unchanged",
                        UnsupportedOverrideTagWarning,
                    )
                    pieces.append(tag.group(0))
                    continue
                scaled = [
                    values[0] * scale_x, values[1] * scale_y,
                    values[2] * scale_x, values[3] * scale_y,
                ]
                _check_overflow("move", scaled, play_res_x, play_res_y)
                rendered = rf"\{raw_name}({_format_number(scaled[0])},{_format_number(scaled[1])}," \
                           rf"{_format_number(scaled[2])},{_format_number(scaled[3])}"
                if len(values) == 6:
                    # t1/t2 are timestamps, not coordinates - keep them untouched
                    rendered += f",{inner.split(',')[4].strip()},{inner.split(',')[5].strip()}"
                rendered += ")"
                pieces.append(rendered)
            else:  # clip - rectangular variant only
                values = _parse_numbers(inner, frozenset({4}))
                if values is None:
                    warnings.warn(
                        r"Only rectangular \clip tags (4 coordinates) can be resolution-scaled, "
                        f"got {tag.group(0)!r}; the tag was left unchanged",
                        UnsupportedOverrideTagWarning,
                    )
                    pieces.append(tag.group(0))
                    continue
                scaled = [
                    values[0] * scale_x, values[1] * scale_y,
                    values[2] * scale_x, values[3] * scale_y,
                ]
                _check_overflow("clip", scaled, play_res_x, play_res_y)
                pieces.append(
                    rf"\{raw_name}(" + ",".join(_format_number(v) for v in scaled) + ")"
                )

        pieces.append(block[pos:])
        return "".join(pieces)

    return SSAEvent.OVERRIDE_SEQUENCE.sub(scale_block, text)


NOTICE = "Script generated by pysubs2\nhttps://pypi.python.org/pypi/pysubs2"

class SubstationFormat(FormatBase):
    """SubStation Alpha (ASS, SSA) subtitle format implementation"""

    class ReaderArgs(TypedDict):
        pass

    class WriterArgs(TypedDict):
        header_notice: NotRequired[str]

    @staticmethod
    def ms_to_timestamp(requested_ms: int) -> str:
        """Convert ms to 'H:MM:SS.cc'"""
        if requested_ms < 0:
            warnings.warn("Underflow in SubStation timestamp, clamping to zero", TimestampUnderflow)
            requested_ms = 0
        elif requested_ms > MAX_REPRESENTABLE_TIME:
            warnings.warn("Overflow in SubStation timestamp, clamping to MAX_REPRESENTABLE_TIME", TimestampOverflow)
            requested_ms = MAX_REPRESENTABLE_TIME

        # Aegisub does rounding, see https://github.com/Aegisub/Aegisub/blob/6f546951b4f004da16ce19ba638bf3eedefb9f31/libaegisub/include/libaegisub/ass/time.h#L32
        round_ms = ((requested_ms + 5) - (requested_ms + 5) % 10)
        h, m, s, ms = ms_to_times(round_ms)
        cs = ms // 10

        return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"

    @classmethod
    @override
    def guess_format(cls, text: str) -> str | None:
        """See :meth:`pysubs2.formats.FormatBase.guess_format()`"""
        if re.search(r"V4\+ Styles", text, re.IGNORECASE):
            return "ass"
        elif re.search(r"V4 Styles", text, re.IGNORECASE):
            return "ssa"
        else:
            return None

    @classmethod
    @override
    def from_file(cls, subs: "SSAFile", fp: TextIO, format_: str, **kwargs: Unpack[ReaderArgs]) -> None:
        """See :meth:`pysubs2.formats.FormatBase.from_file()`"""
        subs.info.clear()
        subs.aegisub_project.clear()
        subs.styles.clear()
        subs.fonts_opaque.clear()
        subs.graphics_opaque.clear()

        inside_info_section = False
        inside_aegisub_section = False
        inside_font_section = False
        inside_graphic_section = False
        current_attachment_name = None
        current_attachment_lines_buffer: list[str] = []
        current_attachment_is_font = None

        for lineno, line in enumerate(fp, 1):
            line = line.strip()

            if SECTION_HEADING.match(line):
                logger.debug("at line %d: section heading %s", lineno, line)
                inside_info_section = "Info" in line
                inside_aegisub_section = "Aegisub" in line
                inside_font_section = "Fonts" in line
                inside_graphic_section = "Graphics" in line
            elif inside_info_section or inside_aegisub_section:
                if line.startswith(";"):
                    continue  # skip comments
                try:
                    k, v = line.split(":", 1)
                    if inside_info_section:
                        subs.info[k] = v.strip()
                    elif inside_aegisub_section:
                        subs.aegisub_project[k] = v.strip()
                except ValueError:
                    pass
            elif inside_font_section or inside_graphic_section:
                m = ATTACHMENT_FILE_HEADING.match(line)
                current_attachment_is_font = inside_font_section

                if current_attachment_name and (m or not line):
                    # flush last font/picture on newline or new font/picture name
                    attachment_data = current_attachment_lines_buffer[:]
                    if inside_font_section:
                        subs.fonts_opaque[current_attachment_name] = attachment_data
                    elif inside_graphic_section:
                        subs.graphics_opaque[current_attachment_name] = attachment_data
                    else:
                        raise NotImplementedError("Bad attachment section, expected [Fonts] or [Graphics]")
                    logger.debug("at line %d: finished attachment definition %s", lineno, current_attachment_name)
                    current_attachment_lines_buffer.clear()
                    current_attachment_name = None

                if m:
                    # start new font/picture
                    attachment_name = m.group("name")
                    current_attachment_name = attachment_name
                elif line:
                    # add non-empty line to current buffer
                    current_attachment_lines_buffer.append(line)
            elif line.startswith("Style:"):
                _, rest = line.split(":", 1)
                buf = rest.strip().split(",")
                name, *raw_fields = buf
                field_dict = {f: cls._string_to_field(f, v, format_) for f, v in zip(STYLE_FIELDS[format_], raw_fields)}
                sty = SSAStyle(**field_dict)
                subs.styles[name] = sty
            elif line.startswith(("Dialogue:", "Comment:")):
                ev_type, rest = line.split(":", 1)
                raw_fields = rest.strip().split(",", len(EVENT_FIELDS[format_])-1)
                field_dict = {f: cls._string_to_field(f, v, format_) for f, v in zip(EVENT_FIELDS[format_], raw_fields)}
                field_dict["type"] = ev_type
                ev = SSAEvent(**field_dict)
                subs.events.append(ev)

        # cleanup fonts/pictures
        if current_attachment_name:
            # flush last font on EOF or new section w/o newline
            attachment_data = current_attachment_lines_buffer[:]

            if current_attachment_is_font:
                subs.fonts_opaque[current_attachment_name] = attachment_data
            else:
                subs.graphics_opaque[current_attachment_name] = attachment_data

            logger.debug("at EOF: finished attachment definition %s", current_attachment_name)
            current_attachment_lines_buffer.clear()
            current_attachment_name = None

    @staticmethod
    def _string_to_field(field: str, value: str, format_: str) -> Any:
        """Parse SSAEvent/SSAStyle field value"""
        # Per issue #45, we should handle the case where there is extra whitespace around the values.
        # Extra whitespace is removed in non-string fields where it would break the parser otherwise,
        # and in font name (where it doesn't really make sense). It is preserved in Dialogue string
        # fields like Text, Name and Effect (to avoid introducing unnecessary change to parser output).

        match field:
            case "start" | "end":
                value = value.strip()
                if value.startswith("-"):
                    # handle negative timestamps
                    value = value[1:]
                    sign = -1
                else:
                    sign = 1

                m = TIMESTAMP.match(value)
                if m is None:
                    m = TIMESTAMP_SHORT.match(value)
                    if m is None:
                        raise ValueError(f"Failed to parse {field} timestamp: {value!r}")

                return sign * timestamp_to_ms(m.groups())
            case "primarycolor" | "secondarycolor" | "tertiarycolor" | "outlinecolor" | "backcolor":
                value = value.strip()
                try:
                    return rgba_to_color(value)
                except ValueError:
                    warnings.warn(f"Failed to parse {field}, using default", SubtitleAttributeWarning)
                    return getattr(SSAStyle.DEFAULT_STYLE, field)
            case "bold" | "underline" | "italic" | "strikeout":
                return value != "0"
            case "layer":
                # layer is not an SSAStyle attribute
                try:
                    return int(value)
                except ValueError:
                    warnings.warn(f"Failed to parse {field}, using default", SubtitleAttributeWarning)
                    return 0
            case "borderstyle" | "encoding" | "marginl" | "marginr" | "marginv" | "alphalevel":
                try:
                    return int(value)
                except ValueError:
                    warnings.warn(f"Failed to parse {field}, using default", SubtitleAttributeWarning)
                    # TODO - this is not entirely correct, the default for margin should be 0 if we're parsing SSAEvent
                    return getattr(SSAStyle.DEFAULT_STYLE, field)
            case "fontsize" | "scalex" | "scaley" | "spacing" | "angle" | "outline" | "shadow":
                try:
                    return float(value)
                except ValueError:
                    warnings.warn(f"Failed to parse {field}, using default", SubtitleAttributeWarning)
                    return getattr(SSAStyle.DEFAULT_STYLE, field)
            case "marked":
                return value.endswith("1")
            case "alignment":
                try:
                    if format_ == "ass":
                        return Alignment(int(value))
                    else:
                        return Alignment.from_ssa_alignment(int(value))
                except ValueError:
                    warnings.warn("Failed to parse alignment, using default", SubtitleAttributeWarning)
                    return getattr(SSAStyle.DEFAULT_STYLE, field)
            case "fontname":
                return value.strip()
            case _:
                return value

    @classmethod
    @override
    def to_file(cls, subs: "SSAFile", fp: TextIO, format_: str, **kwargs: Unpack[WriterArgs]) -> None:
        """See :meth:`pysubs2.formats.FormatBase.to_file()`"""
        header_notice: str = kwargs.get("header_notice", NOTICE)

        print("[Script Info]", file=fp)
        for line in header_notice.splitlines(False):
            print(";", line, file=fp)

        subs.info["ScriptType"] = "v4.00+" if format_ == "ass" else "v4.00"
        for k, v in subs.info.items():
            print(k, v, sep=": ", file=fp)

        if subs.aegisub_project:
            print("\n[Aegisub Project Garbage]", file=fp)
            for k, v in subs.aegisub_project.items():
                print(k, v, sep=": ", file=fp)

        print("\n[V4+ Styles]" if format_ == "ass" else "\n[V4 Styles]", file=fp)
        print(STYLE_FORMAT_LINE[format_], file=fp)
        for name, sty in subs.styles.items():
            fields = [cls._field_to_string(f, getattr(sty, f), sty, format_) for f in STYLE_FIELDS[format_]]
            print(f"Style: {name}", *fields, sep=",", file=fp)

        if subs.fonts_opaque:
            print("\n[Fonts]", file=fp)
            for font_name, font_lines in sorted(subs.fonts_opaque.items()):
                print(f"fontname: {font_name}", file=fp)
                for line in font_lines:
                    print(line, file=fp)
                print(file=fp)

        if subs.graphics_opaque:
            print("\n[Graphics]", file=fp)
            for picture_name, picture_lines in sorted(subs.graphics_opaque.items()):
                print(f"filename: {picture_name}", file=fp)
                for line in picture_lines:
                    print(line, file=fp)
                print(file=fp)

        print("\n[Events]", file=fp)
        print(EVENT_FORMAT_LINE[format_], file=fp)
        for ev in subs.events:
            fields = [cls._field_to_string(f, getattr(ev, f), ev, format_) for f in EVENT_FIELDS[format_]]
            print(ev.type, end=": ", file=fp)
            print(*fields, sep=",", file=fp)

    @classmethod
    def _field_to_string(cls, field: str, value: Any, line: SSAEvent | SSAStyle, format_: str) -> str:
        """Format SSAEvent/SSAStyle field value"""
        match field:
            case "start" | "end":
                return cls.ms_to_timestamp(value)
            case "marked":
                return f"Marked={value:d}"
            case "alignment":
                if isinstance(value, Alignment):
                    alignment = value
                else:
                    warnings.warn("The 'alignment' attribute of SSAStyle should be an Alignment instance, using plain int is deprecated", DeprecationWarning)
                    alignment = Alignment(value)

                if format_ == "ssa":
                    return str(alignment.to_ssa_alignment())
                else:
                    return str(alignment.value)

        match value:
            case bool():
                return "-1" if value else "0"
            case int():
                return str(value)
            case float():
                return str(int(value) if value.is_integer() else value)
            case str():
                return value
            case Color():
                if format_ == "ass":
                    return color_to_ass_rgba(value)
                else:
                    return color_to_ssa_rgb(value)

        raise TypeError(f"Unexpected type when writing a SubStation field {field!r} for line {line!r}")
