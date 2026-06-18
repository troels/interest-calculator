"""Data layer: parsing local files and (gently) fetching from remote sources.

All fetching is cache-first and rate-respectful. This package produces
``loan.models.Curve`` objects; downstream code never parses raw files itself.
"""
