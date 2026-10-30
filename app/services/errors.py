"""Domain error types shared by the services and the HTTP layer.

Every class subclasses the builtin exception that the services raised
before (ValueError / LookupError), so existing callers and tests keep
working unchanged. The API layer uses these precise types to map
failures to HTTP statuses without parsing message strings.
"""

from __future__ import annotations


class CatalogEntityNotFoundError(ValueError):
    """A catalog entity addressed by id (path parameter) does not exist.

    Maps to 404 in the API.
    """


class AssetNotFoundError(LookupError):
    """A media asset addressed by asset_id does not exist.

    Maps to 404 in the API.
    """


class SlotConflictError(ValueError):
    """The (bucket_name, object_key) slot is already registered or is
    being reserved by a concurrent upload.

    Maps to 409 in the API.
    """


class MediaStateError(ValueError):
    """A media operation conflicts with the current state of an asset or
    storage object (e.g. object not 'available', object still referenced
    by other assets).

    Maps to 409 in the API.
    """


class MediaValidationError(ValueError):
    """Invalid media payload: wrong MIME type, unsupported format,
    non-positive bitrate, mismatched metadata, empty content, etc.

    Maps to 422 in the API.
    """


class InvalidReferenceError(ValueError):
    """A reference passed in the request body points to an entity that
    does not exist (e.g. album_id / artist_id in a create payload).

    Maps to 422 in the API (unified contract for body references).
    """


class DuplicateLinkError(ValueError):
    """A link record (e.g. track-artist with the same role) already
    exists.

    Maps to 409 in the API.
    """
