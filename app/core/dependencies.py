"""Compatibility aliases; transport dependencies live in :mod:`app.api`."""

from app.api.dependencies import CurrentUser, get_current_user

__all__ = ["CurrentUser", "get_current_user"]
