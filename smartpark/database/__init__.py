# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Database package for SmartPark Guardian AI."""

from smartpark.database.db import DatabaseManager, DatabaseUnavailableError

__all__ = ["DatabaseManager", "DatabaseUnavailableError"]
