__all__ = [
    "CoordinateOverflowWarning",
    "PossibleMissedSubtitleWarning",
    "Pysubs2Warning",
    "SubtitleAttributeWarning",
    "TimestampOverflow",
    "TimestampUnderflow",
    "UnsupportedOverrideTagWarning",
]


class Pysubs2Warning(UserWarning):
    """Base class for pysubs2 warnings."""


class PossibleMissedSubtitleWarning(Pysubs2Warning):
    """The parser suspects that a subtitle was skipped due to being too malformed"""


class SubtitleAttributeWarning(Pysubs2Warning):
    """Generic warning related to a subtitle event attribute"""


class TimestampOverflow(SubtitleAttributeWarning):
    """During saving, a timestamp was greater than what the output format allows, it was clamped to maximum value"""


class TimestampUnderflow(SubtitleAttributeWarning):
    """During saving, a timestamp negative, it was clamped to zero"""


class CoordinateOverflowWarning(SubtitleAttributeWarning):
    """
    During resolution scaling, a coordinate ended up outside of the target resolution

    The coordinate was still scaled and written, it just does not fit within
    the target ``PlayResX``/``PlayResY`` canvas (this can be legitimate, eg. for
    off-screen positioning or content that is intentionally partly out of view).
    """


class UnsupportedOverrideTagWarning(SubtitleAttributeWarning):
    """
    An override tag could not be resolution-scaled and was left unchanged

    This covers vector drawing clips (``\\clip`` with drawing commands, ``\\iclip``),
    malformed ``\\pos``/``\\move``/``\\org``/``\\clip`` tags and tags nested inside
    other tags (eg. a ``\\clip`` inside an animated ``\\t(...)`` tag).
    """
