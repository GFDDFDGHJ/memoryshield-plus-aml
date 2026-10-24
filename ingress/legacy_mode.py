"""Atomic request leases for an explicitly selected legacy-key backend.

The reader supplies one small, atomically replaced configuration value. This
module never reads files, credentials, request bodies, memories or models. A
lease pins a valid mode until its request finishes, including exception paths.
It does not infer a track from the content or silently mix active backends.
"""
from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Callable, Iterator


class LegacyModeUnavailable(RuntimeError):
    """The mode reader failed or supplied no valid canonical mode."""


class LegacyModeBusy(RuntimeError):
    """A requested mode change must wait for current request leases to finish."""


class LegacyModeController:
    """Pin ``textual`` or ``coding`` across each complete proxied request.

    Construction defaults to Textual and does not call the reader. Each new
    request and status refresh reads the current setting under the same lock.
    Switching is applied only while idle; existing leases retain their mode.
    Invalid configuration is rejected for new requests, while status returns
    a validity flag without exposing the reader's value, path or exception.
    """

    _MODES = frozenset({'textual', 'coding'})

    def __init__(self, reader: Callable[[], str], initial: str = 'textual'):
        if not callable(reader):
            raise TypeError('reader must be callable')
        if not isinstance(initial, str) or initial not in self._MODES:
            raise ValueError('initial mode must be textual or coding')
        self._reader = reader
        self._current = initial
        self._active = 0
        self._lock = threading.RLock()

    def _requested(self) -> str | None:
        """Called with the lock held; never propagate private reader details."""
        try:
            value = self._reader()
            if not isinstance(value, str):
                return None
            value = value.strip()
            return value if value in self._MODES else None
        except Exception:
            return None

    @contextmanager
    def request(self) -> Iterator[str]:
        """Hold a logical request lease without locking during upstream I/O."""
        with self._lock:
            requested = self._requested()
            if requested is None:
                raise LegacyModeUnavailable('Legacy mode configuration is unavailable') from None
            if requested != self._current:
                if self._active:
                    raise LegacyModeBusy('Legacy mode change is waiting for active requests') from None
                self._current = requested
            selected = self._current
            self._active += 1
        try:
            yield selected
        finally:
            with self._lock:
                self._active -= 1

    def status(self) -> dict:
        """Refresh an idle mode, or report a pending change without applying it."""
        with self._lock:
            requested = self._requested()
            valid = requested is not None
            if valid and self._active == 0:
                self._current = requested
            return {
                'legacy_key_target': self._current,
                'requested_legacy_key_target': requested,
                'legacy_active_requests': self._active,
                'legacy_config_valid': valid,
                'legacy_switch_pending': valid and requested != self._current,
            }
