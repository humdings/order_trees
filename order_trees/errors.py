"""Errors raised by file-mirrored mappings."""


class OrderTreesError(Exception):
    """Base error for this package."""


class CodecError(OrderTreesError):
    """The codec could not read or write a document."""


class DocumentError(OrderTreesError):
    """A file that should hold a JSON object held something else."""
