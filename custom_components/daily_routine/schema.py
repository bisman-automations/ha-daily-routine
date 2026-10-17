"""Schema validation library. Home Assistant 2026.10+ validates with probatio; older versions use voluptuous."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import probatio as vol
else:
    try:
        import probatio as vol
    except ImportError:  # Home Assistant before 2026.10
        import voluptuous as vol

__all__ = ["vol"]
