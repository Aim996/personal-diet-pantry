"""Writer-preferring, cross-process leases for generated artifacts."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Iterator

from .models import DataPaths


@dataclass(frozen=True)
class LeaseOwnerToken:
    """Opaque, process-local proof that this call chain owns shared."""

    _canonical_lock_path: str
    _pid: int
    _nonce: str


@dataclass
class _ActiveOwner:
    depth: int
    thread_id: int
    handle: object


class _ProcessLeaseState:
    def __init__(self, canonical_lock_path: str) -> None:
        self.canonical_lock_path = canonical_lock_path
        self.condition = threading.Condition(threading.RLock())
        self.active_owners: dict[str, _ActiveOwner] = {}
        self.waiting_writers = 0
        self.exclusive_active = False
        self.exclusive_thread_id: int | None = None
        self.os_shared_acquire_count = 0
        self.os_shared_release_count = 0


_REGISTRY_LOCK = threading.Lock()
_REGISTRY: dict[str, _ProcessLeaseState] = {}


class DerivedFileLeaseManager:
    def __init__(self, lock_path: Path) -> None:
        self._lock_path = _canonical_lock_file(Path(lock_path))
        key = os.path.normcase(os.path.normpath(str(self._lock_path)))
        with _REGISTRY_LOCK:
            self._state = _REGISTRY.setdefault(key, _ProcessLeaseState(key))

    @contextmanager
    def shared_publisher(
        self,
        *,
        owner: LeaseOwnerToken | None = None,
    ) -> Iterator[LeaseOwnerToken]:
        state = self._state
        token: LeaseOwnerToken
        acquired_new = False
        with state.condition:
            if owner is not None:
                active = _active_owner(state, owner)
                active.depth += 1
                token = owner
            else:
                while state.exclusive_active or state.waiting_writers:
                    state.condition.wait()
                handle = _open_lock_handle(self._lock_path)
                try:
                    _lock_handle(handle, exclusive=False)
                except BaseException:
                    _close_lock_handle(handle)
                    raise
                token = LeaseOwnerToken(
                    state.canonical_lock_path,
                    os.getpid(),
                    secrets.token_hex(32),
                )
                state.active_owners[token._nonce] = _ActiveOwner(
                    depth=1,
                    thread_id=threading.get_ident(),
                    handle=handle,
                )
                state.os_shared_acquire_count += 1
                acquired_new = True
        try:
            yield token
        finally:
            with state.condition:
                active = _active_owner(state, token)
                active.depth -= 1
                if active.depth == 0:
                    del state.active_owners[token._nonce]
                    release_error: BaseException | None = None
                    try:
                        _unlock_handle(active.handle)
                    except BaseException as error:
                        release_error = error
                    try:
                        _close_lock_handle(active.handle)
                    except BaseException as error:
                        if release_error is None:
                            release_error = error
                    state.os_shared_release_count += 1
                    state.condition.notify_all()
                    if release_error is not None:
                        raise release_error
                elif acquired_new:
                    raise RuntimeError("derived-file lease depth is inconsistent")

    @contextmanager
    def exclusive_erasure(self) -> Iterator[None]:
        state = self._state
        thread_id = threading.get_ident()
        handle: object | None = None
        with state.condition:
            if any(
                active.thread_id == thread_id
                for active in state.active_owners.values()
            ):
                raise RuntimeError("shared-to-exclusive lease upgrade is forbidden")
            state.waiting_writers += 1
            try:
                while state.exclusive_active or state.active_owners:
                    state.condition.wait()
                handle = _open_lock_handle(self._lock_path)
                _lock_handle(handle, exclusive=True)
                state.exclusive_active = True
                state.exclusive_thread_id = thread_id
            except BaseException:
                if handle is not None:
                    _close_lock_handle(handle)
                raise
            finally:
                state.waiting_writers -= 1
                state.condition.notify_all()
        try:
            yield
        finally:
            assert handle is not None
            with state.condition:
                release_error: BaseException | None = None
                try:
                    _unlock_handle(handle)
                except BaseException as error:
                    release_error = error
                try:
                    _close_lock_handle(handle)
                except BaseException as error:
                    if release_error is None:
                        release_error = error
                state.exclusive_active = False
                state.exclusive_thread_id = None
                state.condition.notify_all()
                if release_error is not None:
                    raise release_error

    def wait_for_waiting_writers(self, count: int, *, timeout: float) -> bool:
        """Wait for a process-local writer count; useful for deterministic hooks."""

        deadline = time.monotonic() + timeout
        with self._state.condition:
            while self._state.waiting_writers < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._state.condition.wait(remaining)
            return True


def manager_for(data_paths: DataPaths) -> DerivedFileLeaseManager:
    """Return a manager sharing the process gate for this data directory."""

    return DerivedFileLeaseManager(data_paths.control / "derived-files.lock")


def _active_owner(
    state: _ProcessLeaseState,
    token: LeaseOwnerToken,
) -> _ActiveOwner:
    if (
        not isinstance(token, LeaseOwnerToken)
        or token._canonical_lock_path != state.canonical_lock_path
        or token._pid != os.getpid()
    ):
        raise RuntimeError("derived-file lease owner is invalid")
    active = state.active_owners.get(token._nonce)
    if active is None:
        raise RuntimeError("derived-file lease owner is stale")
    return active


def _canonical_lock_file(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.exists() and _is_reparse(current):
            raise RuntimeError("derived-file lock path traverses a link")
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if _is_reparse(absolute.parent):
        raise RuntimeError("derived-file lock parent is a link")
    return absolute


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & 0x400)


def _open_lock_handle(path: Path) -> object:
    return open(path, "a+b", buffering=0)


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    ]
    _kernel32.LockFileEx.restype = wintypes.BOOL
    _kernel32.UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    ]
    _kernel32.UnlockFileEx.restype = wintypes.BOOL

    def _lock_handle(handle: object, *, exclusive: bool) -> None:
        raw = msvcrt.get_osfhandle(handle.fileno())  # type: ignore[attr-defined]
        overlapped = _OVERLAPPED()
        flags = 0x00000002 if exclusive else 0
        if not _kernel32.LockFileEx(raw, flags, 0, 1, 0, ctypes.byref(overlapped)):
            raise OSError(ctypes.get_last_error(), "LockFileEx failed")

    def _unlock_handle(handle: object) -> None:
        raw = msvcrt.get_osfhandle(handle.fileno())  # type: ignore[attr-defined]
        overlapped = _OVERLAPPED()
        if not _kernel32.UnlockFileEx(raw, 0, 1, 0, ctypes.byref(overlapped)):
            raise OSError(ctypes.get_last_error(), "UnlockFileEx failed")

else:
    import fcntl

    def _lock_handle(handle: object, *, exclusive: bool) -> None:
        fcntl.flock(
            handle.fileno(),  # type: ignore[attr-defined]
            fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
        )

    def _unlock_handle(handle: object) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _close_lock_handle(handle: object) -> None:
    handle.close()  # type: ignore[attr-defined]
