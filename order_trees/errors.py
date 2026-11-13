"""Errors raised by file-mirrored mappings."""


class OrderTreesError(Exception):
    """Base error for this package."""


class CodecError(OrderTreesError):
    """The codec could not read or write a document."""


class DocumentError(OrderTreesError):
    """A file that should hold a JSON object held something else."""


class EmptyFileError(DocumentError):
    """The path exists and has no bytes. That is not an empty document."""


class Conflict(OrderTreesError):
    """A batch commit lost to a change made outside the batch."""


class Gone(KeyError):
    """The backing path is not a file. The mapping has no document."""
