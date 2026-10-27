"""Database package for SmartPark Guardian AI."""

from smartpark.database.db import DatabaseManager, DatabaseUnavailableError

__all__ = ["DatabaseManager", "DatabaseUnavailableError"]
