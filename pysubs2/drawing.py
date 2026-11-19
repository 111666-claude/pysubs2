"""
Drawing path view for ASS vector drawings (the ``\\p`` override tag).

This module finds *drawing sections* in subtitle event text --- a ``{\\pN}``
override block (with N > 0) followed by drawing commands --- and parses the
``m`` (move), ``l`` (line) and ``b`` (cubic Bézier curve) commands into
ordered points and curves. The shapes can be moved and uniformly scaled,
and written back as valid ASS event text; plain text, newlines and
non-drawing override tags are preserved as-is.

Example::

    >>> from pysubs2.drawing import parse_drawing_sections, replace_drawing_sections
    >>> text = r"Arrow {\\p1}m 0 0 l 10 0 10 10{\\p0}!"
    >>> sections = parse_drawing_sections(text)
    >>> sections[0].bounds
    (0.0, 0.0, 10.0, 10.0)
    >>> sections[0].move(100, 50)
    >>> replace_drawing_sections(text, sections)
    'Arrow {\\\\p1}m 100 50 l 110 50 110 60{\\\\p0}!'

Malformed drawing data raises :exc:`pysubs2.exceptions.DrawingError`
with the position and excerpt of the offending text.

"""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .common import IntOrFloat
from .exceptions import DrawingError

__all__ = [
    "MAX_DRAWING_COMMANDS",
    "DrawingCommand",
    "DrawingSection",
    "parse_drawing_sections",
    "replace_drawing_sections",
]

#: Maximum number of drawing commands allowed in a single drawing section.
#: Sections with more commands are rejected with :exc:`DrawingError`
#: (this guards against pathological or corrupt input).
MAX_DRAWING_COMMANDS = 4096

_OVERRIDE_BLOCK = re.compile(r"{[^}]*}")
_P_TAG = re.compile(r"\\p(\d+)")
_COMMAND_LETTERS = ("m", "l", "b")
_SNIPPET_RADIUS = 20


@dataclass
class DrawingCommand:
    """
    One ASS drawing command: move (``m``), line (``l``) or cubic Bézier curve (``b``).

    A single command letter may be followed by multiple coordinate pairs
    (eg. ``l 0 0 10 0 10 10``); all of them are kept in :attr:`points`,
    in order. For ``b`` commands, the points come in groups of three
    (two control points and an end point per curve).

    Coordinates are in *drawing units*; divide by ``2 ** (scale - 1)``
    of the containing :class:`DrawingSection` to get script coordinates.

    Attributes:
        type: Command letter, one of ``"m"``, ``"l"``, ``"b"``
        points: Ordered (x, y) coordinate pairs of the command
    """
    type: str
    points: list[tuple[float, float]] = field(default_factory=list)

    def to_ass(self) -> str:
        """Serialize the command to ASS drawing syntax, eg. ``m 0 0 l 10 0``."""
        numbers = " ".join(_format_number(coordinate) for point in self.points for coordinate in point)
        return f"{self.type} {numbers}"


@dataclass
class DrawingSection:
    """
    A parsed ``\\p`` drawing section of a subtitle event text.

    Attributes:
        scale: The ``\\p`` scale (1 or more); drawing units are
            ``2 ** (scale - 1)`` times script coordinates
        commands: Ordered drawing commands of the section
        start: Character offset of the override block preceding the drawing
            content (typically the ``{\\pN}`` block) in the original text
        content_start: Character offset where the drawing commands start
            in the original text
        end: Character offset where the drawing commands end (exclusive)
            in the original text
    """
    scale: int
    commands: list[DrawingCommand]
    start: int
    content_start: int
    end: int

    @property
    def coordinate_scale(self) -> int:
        """Ratio between drawing units and script coordinates, ie. ``2 ** (scale - 1)``."""
        return 2 ** (self.scale - 1)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """
        Bounding box of the drawing in script coordinates.

        Returns ``(min_x, min_y, max_x, max_y)`` over all points of all
        commands (Bézier control points included).
        """
        xs = [x for command in self.commands for x, _ in command.points]
        ys = [y for command in self.commands for _, y in command.points]
        scale = self.coordinate_scale
        return min(xs) / scale, min(ys) / scale, max(xs) / scale, max(ys) / scale

    def move(self, dx: IntOrFloat = 0, dy: IntOrFloat = 0) -> None:
        """
        Move the drawing by the given offset, in script coordinates.
        """
        scale = self.coordinate_scale
        offset_x, offset_y = dx * scale, dy * scale
        for command in self.commands:
            command.points = [(x + offset_x, y + offset_y) for x, y in command.points]

    def scale_by(self, factor: float, origin: tuple[float, float] = (0.0, 0.0)) -> None:
        """
        Scale the drawing uniformly by ``factor`` around ``origin`` (script coordinates).
        """
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("Scale factor must be a positive finite number")
        scale = self.coordinate_scale
        origin_x, origin_y = origin[0] * scale, origin[1] * scale
        for command in self.commands:
            command.points = [
                (origin_x + (x - origin_x) * factor, origin_y + (y - origin_y) * factor)
                for x, y in command.points
            ]

    def to_ass(self) -> str:
        """Serialize the drawing commands to ASS drawing syntax (without the ``{\\pN}`` block)."""
        return " ".join(command.to_ass() for command in self.commands)


def parse_drawing_sections(text: str) -> list[DrawingSection]:
    """
    Find and parse ``\\p`` drawing sections in subtitle event text.

    A drawing section starts at an override block containing a ``\\pN`` tag
    with N > 0 and covers the following text, up to the next override block
    or the end of the string. (If drawing mode is left on across several
    override blocks, each text fragment becomes its own section.)

    Returns a list of :class:`DrawingSection` objects, in order of
    appearance. The sections carry their positions in the original text,
    so the text can be rebuilt with :func:`replace_drawing_sections`
    after moving or scaling them.

    Raises:
        DrawingError: If the drawing data is malformed (unknown command,
            missing coordinates, odd number of coordinates, non-finite
            number, empty path, too many commands or drawing commands
            after a mid-path ``\\p0`` switch).
    """
    blocks = list(_OVERRIDE_BLOCK.finditer(text))
    sections: list[DrawingSection] = []
    drawing_scale = 0
    saw_drawing_content = False

    for i, block in enumerate(blocks):
        p_tags = _P_TAG.findall(block.group(0))
        if p_tags:
            new_scale = int(p_tags[-1])  # the last \p tag in the block wins
            if new_scale == 0 and drawing_scale > 0:
                if not saw_drawing_content:
                    raise DrawingError(
                        "Empty drawing path (no drawing commands between \\pN and \\p0)",
                        position=block.start(), snippet=_snippet(text, block.start()))
                following_start = block.end()
                following_end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
                following = text[following_start:following_end]
                if following.strip() and _looks_like_drawing(following):
                    raise DrawingError(
                        "Drawing commands found after \\p0 (mid-path \\p0 switch); "
                        "did you forget to re-enable drawing mode with \\pN?",
                        position=following_start, snippet=_snippet(text, following_start))
                drawing_scale = 0
                saw_drawing_content = False
            elif new_scale > 0:
                drawing_scale = new_scale

        if drawing_scale > 0:
            content_start = block.end()
            content_end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
            content = text[content_start:content_end]
            if content.strip():
                commands = _parse_drawing_commands(content, text, content_start)
                saw_drawing_content = True
                sections.append(DrawingSection(
                    scale=drawing_scale, commands=commands,
                    start=block.start(), content_start=content_start, end=content_end))

    if drawing_scale > 0 and not saw_drawing_content:
        raise DrawingError(
            "Empty drawing path (no drawing commands after \\pN)",
            position=len(text), snippet=_snippet(text, len(text)))

    return sections


def replace_drawing_sections(text: str, sections: Iterable[DrawingSection]) -> str:
    """
    Write (possibly transformed) drawing sections back into event text.

    Replaces the drawing commands of each section (as located by its
    ``content_start``/``end`` offsets) with :meth:`DrawingSection.to_ass`.
    Everything else --- plain text, newlines, override tags including the
    ``{\\pN}`` blocks themselves --- is preserved as-is.

    The sections must come from :func:`parse_drawing_sections` called on
    the same text and must not overlap.
    """
    ordered = sorted(sections, key=lambda s: s.content_start)
    output: list[str] = []
    cursor = 0
    for section in ordered:
        if section.content_start < cursor or section.end > len(text):
            raise ValueError("Drawing sections overlap or do not belong to the given text")
        output.append(text[cursor:section.content_start])
        output.append(section.to_ass())
        cursor = section.end
    output.append(text[cursor:])
    return "".join(output)


def _parse_drawing_commands(fragment: str, full_text: str, offset: int) -> list[DrawingCommand]:
    """Parse one drawing content fragment into commands. `offset` is the fragment's position in `full_text`."""
    commands: list[DrawingCommand] = []
    current_type: str | None = None
    current_numbers: list[float] = []

    def flush(position: int) -> None:
        nonlocal current_type, current_numbers
        if current_type is None:
            return
        if not current_numbers:
            raise DrawingError(
                f"Drawing command {current_type!r} is missing coordinates",
                position=position, snippet=_snippet(full_text, position))
        if len(current_numbers) % 2 != 0:
            raise DrawingError(
                f"Odd number of coordinates ({len(current_numbers)}) for drawing command {current_type!r}",
                position=position, snippet=_snippet(full_text, position))
        points = [(current_numbers[j], current_numbers[j + 1]) for j in range(0, len(current_numbers), 2)]
        if current_type == "b" and len(points) % 3 != 0:
            raise DrawingError(
                f"Incomplete Bézier curve: 'b' command requires groups of 3 points, got {len(points)}",
                position=position, snippet=_snippet(full_text, position))
        commands.append(DrawingCommand(current_type, points))
        current_type = None
        current_numbers = []

    cursor = 0
    for token in fragment.split():
        token_position = offset + fragment.find(token, cursor)
        cursor = token_position - offset + len(token)
        if token in _COMMAND_LETTERS:
            flush(token_position)
            current_type = token
        else:
            if current_type is None:
                raise DrawingError(
                    f"Drawing data does not start with a command: {token!r}",
                    position=token_position, snippet=_snippet(full_text, token_position))
            try:
                value = float(token)
            except ValueError:
                raise DrawingError(
                    f"Invalid drawing command or coordinate: {token!r}",
                    position=token_position, snippet=_snippet(full_text, token_position)) from None
            if not math.isfinite(value):
                raise DrawingError(
                    f"Non-finite coordinate in drawing: {token!r}",
                    position=token_position, snippet=_snippet(full_text, token_position))
            current_numbers.append(value)
    flush(offset + len(fragment))

    if len(commands) > MAX_DRAWING_COMMANDS:
        raise DrawingError(
            f"Too many drawing commands ({len(commands)} > {MAX_DRAWING_COMMANDS})",
            position=offset, snippet=_snippet(full_text, offset))
    return commands


def _looks_like_drawing(fragment: str) -> bool:
    """True if the fragment parses as drawing commands (used for mid-path \\p0 detection)."""
    try:
        return bool(_parse_drawing_commands(fragment, fragment, 0))
    except DrawingError:
        return False


def _format_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def _snippet(text: str, position: int) -> str:
    return text[max(0, position - _SNIPPET_RADIUS):position + _SNIPPET_RADIUS]
