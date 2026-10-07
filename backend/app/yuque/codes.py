"""Yuque provider constants shared by the gateways and the capability record.

Kept in its own leaf module because both sides need the same code: the
gateway raises it when no usable browser is installed, and
``ProviderCapabilities`` advertises it so a client can offer the install action
without hard-coding a vendor-specific string. Putting it in either gateway
would make the other import a cycle.
"""
from __future__ import annotations

YUQUE_BROWSER_UNAVAILABLE_CODE = "YUQUE_BROWSER_UNAVAILABLE"
