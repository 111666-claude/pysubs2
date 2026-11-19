from typing import Any

__all__ = [
    "DrawingError",
    "FormatAutodetectionError",
    "Pysubs2Error",
    "UnknownFPSError",
    "UnknownFileExtensionError",
    "UnknownFormatIdentifierError",
]


class Pysubs2Error(Exception):
    """Base class for pysubs2 exceptions."""


class UnknownFPSError(Pysubs2Error):
    """Framerate was not specified and couldn't be inferred otherwise."""


class UnknownFileExtensionError(Pysubs2Error):
    """
    File extension does not pertain to any known subtitle format.

    This exception is raised by `SSAFile.save()` when the ``format_`` parameter
    is not specified. It will try to guess the desired format from output filename
    and raise this exception when it fails.

    Attributes:
        ext (str): File extension
    """

    def __init__(self, ext: str) -> None:
        self.ext = ext
        msg = f"File extension {ext!r} does not match any supported subtitle format"
        super().__init__(msg)

    def __reduce__(self) -> Any:
        return self.__class__, (self.ext,)


class UnknownFormatIdentifierError(Pysubs2Error):
    """
    Unknown subtitle format identifier (ie. string like ``"srt"``).

    This exception is used when interpreting ``format_`` parameter fails,
    eg. in `SSAFile.save()`.

    Attributes:
        format_ (str): Format identifier
    """

    def __init__(self, format_: str) -> None:
        self.format_ = format_
        msg = f"Format identifier {format_!r} does not match any supported subtitle format"
        super().__init__(msg)

    def __reduce__(self) -> Any:
        return self.__class__, (self.format_,)


class FormatAutodetectionError(Pysubs2Error):
    """
    Subtitle format is ambiguous or unknown based on analysis of file fragment

    This exception is raised by `SSAFile.load()` and related methods
    when the ``format_`` parameter is not specified. It will try to guess
    the input format based on reading first few kilobytes of the input file
    and raise this exception if the format cannot be uniquely determined.

    Attributes:
        content (str): Analyzed subtitle file content
        formats (list[str]): Format identifiers for detected formats
    """
    def __init__(self, content: str, formats: list[str]) -> None:
        self.content = content
        self.formats = formats
        if not formats:
            msg = "No suitable formats"
        else:
            msg = f"Multiple suitable formats ({formats!r})"
        super().__init__(msg)

    def __reduce__(self) -> Any:
        return self.__class__, (self.content, self.formats)


class DrawingError(Pysubs2Error):
    """
    Error in ASS vector drawing data (``\\p`` drawing sections).

    This exception is raised by the :mod:`pysubs2.drawing` module when drawing
    commands in event text are malformed, eg. unknown commands, missing or
    odd number of coordinates, non-finite numbers, empty paths, excessive
    command counts or drawing data interrupted by a ``\\p0`` tag.

    Attributes:
        message (str): Description of the problem
        position (int | None): Character offset in the event text where the problem was detected
        snippet (str | None): Excerpt of the event text around the problem
    """

    def __init__(self, message: str, position: int | None = None, snippet: str | None = None) -> None:
        self.message = message
        self.position = position
        self.snippet = snippet
        msg = message
        if position is not None:
            msg += f" (at position {position}"
            if snippet is not None:
                msg += f", near {snippet!r}"
            msg += ")"
        elif snippet is not None:
            msg += f" (near {snippet!r})"
        super().__init__(msg)

    def __reduce__(self) -> Any:
        return self.__class__, (self.message, self.position, self.snippet)
