"""Exception types shared across modules (kept import-light)."""


class UserFacingError(RuntimeError):
    """An expected failure whose message is safe to show to end users."""
