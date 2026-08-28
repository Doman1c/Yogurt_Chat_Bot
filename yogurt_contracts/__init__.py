"""Yogurt public cross-runtime domain contracts Python reference binding.

This package is intentionally not a canonical wire ABI. Future transports map
the normative domain semantics to their own compatibility rules.
"""

from .foundation.visibility import ContractVisibility

__all__ = ["ContractVisibility"]
