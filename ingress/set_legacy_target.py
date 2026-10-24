"""Switch the original credential's local target only between completed evaluations.

This operator CLI does not read credentials or memories. Its health URL and
configuration location are fixed; only the testable Python API accepts fakes.
The idle health snapshots supplement the required operator assertion, rather
than locking out new public requests between the snapshots.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Callable

import httpx


HEALTH_URL = 'http://127.0.0.1:8282/__gateway_health'
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / 'legacy_target.json'
EXPECTED_SYSTEM = 'MemoryShield+ track gateway'
EXPECTED_VERSION = 'v1.1.0'
_TARGETS = frozenset({'textual', 'coding'})


class LegacyTargetSwitchError(RuntimeError):
    """A sanitized operator error; written configurations are never rolled back."""

    def __init__(self, message: str, *, configuration_written: bool = False):
        super().__init__(message)
        self.configuration_written = configuration_written


def fetch_gateway_health() -> dict:
    """Read only the fixed local gateway, without environment proxies or redirects."""
    try:
        with httpx.Client(trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            response = client.get(HEALTH_URL)
            if response.status_code != 200:
                raise ValueError('Unexpected local health status')
            payload = response.json()
    except Exception:
        raise LegacyTargetSwitchError('Local gateway health is unavailable.') from None
    if type(payload) is not dict:
        raise LegacyTargetSwitchError('Local gateway health is invalid.')
    return payload


def _health_is_ready(payload, *, allow_invalid: bool, expected_target=None) -> bool:
    if type(payload) is not dict:
        return False
    if (payload.get('status') != 'ok'
            or payload.get('system') != EXPECTED_SYSTEM
            or payload.get('version') != EXPECTED_VERSION):
        return False
    for key in ('legacy_active_requests', 'active_memory_requests'):
        if type(payload.get(key)) is not int or payload[key] != 0:
            return False
    if payload.get('legacy_switch_pending') is not False:
        return False
    current = payload.get('legacy_key_target')
    if type(current) is not str or current not in _TARGETS:
        return False
    valid = payload.get('legacy_config_valid')
    if valid is False:
        # A missing/bad local file must be repairable while no request is active.
        return allow_invalid and payload.get('requested_legacy_key_target') is None
    if valid is not True:
        return False
    requested = payload.get('requested_legacy_key_target')
    if requested != current:
        return False
    return expected_target is None or current == expected_target


def _read_health(fetch: Callable[[], dict]):
    try:
        return fetch()
    except Exception:
        # Fakes and HTTP transports may carry response data in their errors.
        raise LegacyTargetSwitchError('Local gateway health could not be read.') from None


def _atomic_write(config_path: Path, target: str) -> None:
    temporary = None
    descriptor = None
    try:
        descriptor, name = tempfile.mkstemp(prefix='.legacy-target-', suffix='.tmp',
                                           dir=config_path.parent)
        temporary = Path(name)
        writer = os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n')
        descriptor = None
        with writer:
            writer.write(json.dumps({'legacy_key_target': target}, ensure_ascii=True) + '\n')
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, config_path)
        temporary = None
    except Exception:
        raise LegacyTargetSwitchError('Local target configuration could not be written.') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # No private content is stored, but retain a clear failure result.
                raise LegacyTargetSwitchError('Temporary target configuration cleanup failed.') from None


def set_legacy_target(target: str, *, evaluation_idle: bool = False,
                      config_path=None, fetch_health=None) -> dict:
    """Atomically select a target and confirm it using two bounded health reads.

    ``config_path`` and ``fetch_health`` are injection seams for offline tests.
    A failed post-write verification leaves the written file in place: another
    request may already be using it, so a blind rollback would be another switch.
    An existing lock is refused rather than considered stale automatically.
    """
    if type(target) is not str or target not in _TARGETS:
        raise LegacyTargetSwitchError('Target must be textual or coding.')
    if evaluation_idle is not True:
        raise LegacyTargetSwitchError('Confirm all evaluations have ended with --evaluation-idle.')
    destination = DEFAULT_CONFIG_PATH if config_path is None else Path(config_path)
    fetch = fetch_gateway_health if fetch_health is None else fetch_health
    lock_path = destination.with_name(destination.stem + '.switch.lock')
    lock_descriptor = None
    written = False
    try:
        try:
            lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise LegacyTargetSwitchError('Another target switch is locked; inspect it before retrying.') from None
        except OSError:
            raise LegacyTargetSwitchError('The local target switch lock could not be created.') from None

        before = _read_health(fetch)
        if not _health_is_ready(before, allow_invalid=True):
            raise LegacyTargetSwitchError('Gateway is incompatible, busy, or has a pending target switch.')
        _atomic_write(destination, target)
        written = True
        try:
            after = _read_health(fetch)
        except LegacyTargetSwitchError:
            raise LegacyTargetSwitchError(
                'Configuration was written, but the gateway target could not be confirmed; inspect local health before evaluating.',
                configuration_written=True) from None
        if not _health_is_ready(after, allow_invalid=False, expected_target=target):
            raise LegacyTargetSwitchError(
                'Configuration was written, but the gateway target is not confirmed idle; inspect local health before evaluating.',
                configuration_written=True)
        # Whitelist return fields; never return or print arbitrary health fields.
        return {'status': 'ok', 'legacy_key_target': target,
                'legacy_config_valid': True, 'legacy_active_requests': 0,
                'active_memory_requests': 0, 'legacy_switch_pending': False}
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)
            try:
                lock_path.unlink()
            except OSError:
                raise LegacyTargetSwitchError(
                    'The local target switch lock could not be removed; inspect it before retrying.',
                    configuration_written=written) from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Select the original key target only after every Smoke/Full evaluation has ended.')
    parser.add_argument('target', choices=sorted(_TARGETS))
    parser.add_argument('--evaluation-idle', action='store_true', required=True,
                        help='Required assertion: all evaluations have finished; do not switch during a run.')
    arguments = parser.parse_args(argv)
    try:
        result = set_legacy_target(arguments.target, evaluation_idle=arguments.evaluation_idle)
    except LegacyTargetSwitchError as error:
        print(str(error), file=sys.stderr)
        return 1
    print('Original key target confirmed: ' + result['legacy_key_target'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
