"""Backoffice domain package."""

from src.domains.backoffice import models
from src.domains.backoffice.router import router

__all__ = ["models", "router"]
