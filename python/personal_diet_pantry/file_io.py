"""Durable, no-follow I/O for files owned by the configured data root."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Iterator

from .models import DataPaths


def _flush_probe(boundary: str) -> None:
    """Fault-injection seam; production deliberately performs no action."""

    del boundary


def atomic_write_text(
    destination: Path,
    contents: str,
    *,
    encoding: str = "utf-8",
    data_paths: DataPaths | None = None,
) -> None:
    """Flush text before replacing the destination in one bound parent."""

    atomic_write_bytes(
        destination,
        contents.encode(encoding),
        data_paths=data_paths,
    )


def atomic_write_bytes(
    destination: Path,
    contents: bytes,
    *,
    data_paths: DataPaths | None = None,
) -> None:
    """Flush bytes before replacing the destination in one bound parent."""

    target = Path(destination)
    payload = bytes(contents)
    if data_paths is None:
        _atomic_write_unowned(target, payload)
        return
    durable_mkdir(target.parent, data_paths=data_paths)
    if os.name == "nt":
        _atomic_write_windows(target, payload, data_paths=data_paths)
    else:
        _atomic_write_posix(target, payload, data_paths=data_paths)


def sha256_regular_file(path: Path, *, data_paths: DataPaths) -> str:
    """Hash one regular file through a no-follow handle under bound parents."""

    target = Path(path)
    if os.name == "nt":
        with _bound_windows_parent(target, data_paths) as bound:
            handle = _open_windows_regular(bound.path, delete_access=False)
            try:
                return _hash_windows_handle(handle)
            finally:
                _close_windows_handle(handle)
    with _bound_posix_parent(target, data_paths) as bound:
        descriptor = _open_posix_regular(bound.descriptor, bound.name)
        try:
            return _hash_posix_descriptor(descriptor)
        finally:
            os.close(descriptor)


def fsync_regular_file(path: Path) -> None:
    """Open the final component without following it and flush regular bytes."""

    target = Path(path)
    if os.name == "nt":
        handle = _open_windows_regular(target, delete_access=False)
        try:
            _flush_windows_handle(handle)
        finally:
            _close_windows_handle(handle)
        return
    descriptor = os.open(
        target,
        os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        _require_posix_regular(descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(path: Path) -> None:
    """Open the final directory without following it and flush its entries."""

    target = Path(path)
    if os.name == "nt":
        handle = _open_windows_directory(target)
        try:
            _flush_windows_handle(handle)
        finally:
            _close_windows_handle(handle)
        return
    descriptor = os.open(
        target,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("not a directory")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_mkdir(target: Path, *, data_paths: DataPaths) -> None:
    """Create owned components while every parent remains handle-bound."""

    destination = Path(target)
    root, relative = _owned_relative(destination, data_paths)
    if not relative.parts:
        return
    if os.name == "nt":
        _durable_mkdir_windows(root, relative.parts)
    else:
        _durable_mkdir_posix(root, relative.parts)


def durable_replace(
    source: Path,
    destination: Path,
    *,
    data_paths: DataPaths,
    expected_sha256: str | None = None,
) -> None:
    """Replace from bound source/destination parents without following links."""

    src = Path(source)
    dst = Path(destination)
    if os.name == "nt":
        _durable_replace_windows(
            src,
            dst,
            data_paths=data_paths,
            expected_sha256=expected_sha256,
        )
    else:
        _durable_replace_posix(
            src,
            dst,
            data_paths=data_paths,
            expected_sha256=expected_sha256,
        )


def durable_unlink(target: Path, *, data_paths: DataPaths) -> None:
    """Remove the exact regular-file object opened under a bound parent."""

    path = Path(target)
    if os.name == "nt":
        _durable_unlink_windows(path, data_paths=data_paths)
    else:
        _durable_unlink_posix(path, data_paths=data_paths)


def durable_rmdir(target: Path, *, data_paths: DataPaths) -> None:
    """Remove the exact empty directory opened under a bound parent."""

    path = Path(target)
    if os.name == "nt":
        _durable_rmdir_windows(path, data_paths=data_paths)
    else:
        _durable_rmdir_posix(path, data_paths=data_paths)


def _owned_relative(target: Path, data_paths: DataPaths) -> tuple[Path, Path]:
    raw = Path(target)
    if ".." in raw.parts:
        raise OSError("owned path contains parent traversal")
    root = Path(data_paths.root).absolute()
    absolute = raw.absolute()
    try:
        relative = absolute.relative_to(root)
    except ValueError as error:
        raise OSError("path escapes the configured data root") from error
    return root, relative


def _atomic_write_unowned(target: Path, contents: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{secrets.token_hex(12)}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        fsync_regular_file(target)
        fsync_directory(target.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class _PosixBoundParent:
    def __init__(self, descriptor: int, name: str) -> None:
        self.descriptor = descriptor
        self.name = name


@contextmanager
def _bound_posix_parent(
    target: Path,
    data_paths: DataPaths,
) -> Iterator[_PosixBoundParent]:
    root, relative = _owned_relative(target, data_paths)
    if not relative.parts:
        raise OSError("operation target must be below the data root")
    descriptors: list[int] = []
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        current = os.open(root, flags)
        descriptors.append(current)
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise OSError("configured data root is not a directory")
        for part in relative.parts[:-1]:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
            if not stat.S_ISDIR(os.fstat(current).st_mode):
                raise OSError("owned path component is not a directory")
        yield _PosixBoundParent(current, relative.parts[-1])
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _open_posix_regular(parent_descriptor: int, name: str) -> int:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_descriptor,
    )
    try:
        _require_posix_regular(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _require_posix_regular(descriptor: int) -> os.stat_result:
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode):
        raise OSError("opened object is not a regular file")
    return details


def _hash_posix_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _atomic_write_posix(
    target: Path,
    payload: bytes,
    *,
    data_paths: DataPaths,
) -> None:
    with _bound_posix_parent(target, data_paths) as bound:
        temporary_name = f".{bound.name}.{secrets.token_hex(12)}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=bound.descriptor,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
            os.fsync(descriptor)
            os.replace(
                temporary_name,
                bound.name,
                src_dir_fd=bound.descriptor,
                dst_dir_fd=bound.descriptor,
            )
            os.fsync(descriptor)
            os.fsync(bound.descriptor)
        except BaseException:
            try:
                os.unlink(temporary_name, dir_fd=bound.descriptor)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(descriptor)


def _durable_mkdir_posix(root: Path, parts: tuple[str, ...]) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        current = os.open(root, flags)
        descriptors.append(current)
        for part in parts:
            created = False
            try:
                child = os.open(part, flags, dir_fd=current)
            except FileNotFoundError:
                os.mkdir(part, dir_fd=current)
                created = True
                child = os.open(part, flags, dir_fd=current)
            descriptors.append(child)
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                raise OSError("owned path component is not a directory")
            if created:
                _flush_probe("mkdir_new_directory_flush")
                os.fsync(child)
                _flush_probe("mkdir_parent_flush")
                os.fsync(current)
            current = child
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _durable_replace_posix(
    source: Path,
    destination: Path,
    *,
    data_paths: DataPaths,
    expected_sha256: str | None,
) -> None:
    with _bound_posix_parent(source, data_paths) as src, _bound_posix_parent(
        destination, data_paths
    ) as dst:
        descriptor = _open_posix_regular(src.descriptor, src.name)
        try:
            if os.fstat(descriptor).st_dev != os.fstat(dst.descriptor).st_dev:
                raise OSError("durable replacement requires one volume")
            if (
                expected_sha256 is not None
                and _hash_posix_descriptor(descriptor) != expected_sha256
            ):
                raise OSError("durable replacement source hash changed")
            _flush_probe("replace_source_file_flush")
            os.fsync(descriptor)
            current = os.stat(src.name, dir_fd=src.descriptor, follow_symlinks=False)
            opened = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise OSError("durable replacement source identity changed")
            os.replace(
                src.name,
                dst.name,
                src_dir_fd=src.descriptor,
                dst_dir_fd=dst.descriptor,
            )
            _flush_probe("replace_destination_file_flush")
            os.fsync(descriptor)
            _flush_probe("replace_source_parent_flush")
            os.fsync(src.descriptor)
            src_parent = os.fstat(src.descriptor)
            dst_parent = os.fstat(dst.descriptor)
            if (src_parent.st_dev, src_parent.st_ino) != (
                dst_parent.st_dev,
                dst_parent.st_ino,
            ):
                _flush_probe("replace_destination_parent_flush")
                os.fsync(dst.descriptor)
        finally:
            os.close(descriptor)


def _durable_unlink_posix(target: Path, *, data_paths: DataPaths) -> None:
    with _bound_posix_parent(target, data_paths) as bound:
        descriptor = _open_posix_regular(bound.descriptor, bound.name)
        try:
            _flush_probe("unlink_target_file_flush")
            os.fsync(descriptor)
            current = os.stat(
                bound.name,
                dir_fd=bound.descriptor,
                follow_symlinks=False,
            )
            opened = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise OSError("durable unlink target identity changed")
            os.unlink(bound.name, dir_fd=bound.descriptor)
        finally:
            os.close(descriptor)
        _flush_probe("unlink_parent_flush")
        os.fsync(bound.descriptor)


def _durable_rmdir_posix(target: Path, *, data_paths: DataPaths) -> None:
    with _bound_posix_parent(target, data_paths) as bound:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(bound.name, flags, dir_fd=bound.descriptor)
        try:
            if os.listdir(descriptor):
                raise OSError("durable_rmdir requires an empty directory")
            _flush_probe("rmdir_quarantine_directory_flush")
            os.fsync(descriptor)
            opened = os.fstat(descriptor)
            current = os.stat(
                bound.name,
                dir_fd=bound.descriptor,
                follow_symlinks=False,
            )
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise OSError("durable rmdir target identity changed")
            os.rmdir(bound.name, dir_fd=bound.descriptor)
        finally:
            os.close(descriptor)
        _flush_probe("rmdir_parent_flush")
        os.fsync(bound.descriptor)


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _OPEN_EXISTING = 3
    _CREATE_NEW = 1
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_BEGIN = 0
    _SYNCHRONIZE = 0x00100000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _FILE_CREATE = 2
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_RENAME_INFO_CLASS = 3
    _FILE_RENAME_INFORMATION_CLASS = 10
    _FILE_DISPOSITION_INFO_CLASS = 4
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _FILE_RENAME_INFO(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _FILE_DISPOSITION_INFO(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [
            ("Status", ctypes.c_void_p),
            ("Information", ctypes.c_size_t),
        ]

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    _kernel32.SetFilePointerEx.restype = wintypes.BOOL
    _ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_OBJECT_ATTRIBUTES),
        ctypes.POINTER(_IO_STATUS_BLOCK),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    _ntdll.NtCreateFile.restype = ctypes.c_long
    _ntdll.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_IO_STATUS_BLOCK),
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.c_int,
    ]
    _ntdll.NtSetInformationFile.restype = ctypes.c_long
    _ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    _ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG

    class _WindowsBoundParent:
        def __init__(self, handles: list[int], path: Path, name: str) -> None:
            self.handles = handles
            self.path = path
            self.name = name

        @property
        def parent_handle(self) -> int:
            return self.handles[-1]

    def _windows_path(path: Path) -> str:
        value = str(Path(path).absolute())
        if not value.startswith("\\\\?\\"):
            return "\\\\?\\" + value
        return value

    def _canonical_windows_path(value: str | Path) -> str:
        text = str(value)
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        return os.path.normcase(os.path.normpath(os.path.abspath(text)))

    def _windows_information(handle: int) -> _BY_HANDLE_FILE_INFORMATION:
        information = _BY_HANDLE_FILE_INFORMATION()
        if not _kernel32.GetFileInformationByHandle(
            handle, ctypes.byref(information)
        ):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed")
        return information

    def _windows_final_path(handle: int) -> str:
        required = _kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not required:
            raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
        buffer = ctypes.create_unicode_buffer(required + 1)
        written = _kernel32.GetFinalPathNameByHandleW(
            handle, buffer, len(buffer), 0
        )
        if not written or written >= len(buffer):
            raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
        return _canonical_windows_path(buffer.value)

    def _open_windows_handle(
        path: Path,
        *,
        desired_access: int,
        disposition: int = _OPEN_EXISTING,
        directory: bool,
    ) -> int:
        flags = _FILE_FLAG_OPEN_REPARSE_POINT
        if directory:
            flags |= _FILE_FLAG_BACKUP_SEMANTICS
        handle = _kernel32.CreateFileW(
            _windows_path(path),
            desired_access,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            disposition,
            flags,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed")
        return int(handle)

    def _open_windows_relative(
        parent_handle: int,
        name: str,
        *,
        desired_access: int,
        disposition: int,
        directory: bool,
    ) -> int:
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
        ):
            raise OSError("handle-relative name must be one leaf")
        name_buffer = ctypes.create_unicode_buffer(name)
        encoded_length = len(name.encode("utf-16-le"))
        unicode_name = _UNICODE_STRING(
            encoded_length,
            encoded_length + ctypes.sizeof(ctypes.c_wchar),
            ctypes.cast(name_buffer, wintypes.LPWSTR),
        )
        attributes = _OBJECT_ATTRIBUTES(
            ctypes.sizeof(_OBJECT_ATTRIBUTES),
            wintypes.HANDLE(parent_handle),
            ctypes.pointer(unicode_name),
            _OBJ_CASE_INSENSITIVE,
            None,
            None,
        )
        handle = wintypes.HANDLE()
        io_status = _IO_STATUS_BLOCK()
        create_options = _FILE_SYNCHRONOUS_IO_NONALERT
        create_options |= (
            _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
        )
        status = _ntdll.NtCreateFile(
            ctypes.byref(handle),
            desired_access | _SYNCHRONIZE,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            disposition,
            create_options,
            None,
            0,
        )
        if status < 0:
            error_code = int(_ntdll.RtlNtStatusToDosError(status))
            raise OSError(error_code, "NtCreateFile failed")
        return int(handle.value)

    def _validate_windows_handle(
        handle: int,
        expected: Path,
        *,
        directory: bool,
    ) -> _BY_HANDLE_FILE_INFORMATION:
        information = _windows_information(handle)
        attributes = information.dwFileAttributes
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError("owned path traverses a reparse point")
        is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
        if is_directory != directory:
            raise OSError("opened owned object has the wrong type")
        if _windows_final_path(handle) != _canonical_windows_path(expected):
            raise OSError("opened owned object resolved to an unexpected path")
        return information

    def _open_windows_directory(path: Path) -> int:
        handle = _open_windows_handle(
            path,
            desired_access=_GENERIC_READ | _GENERIC_WRITE,
            directory=True,
        )
        try:
            _validate_windows_handle(handle, path, directory=True)
        except BaseException:
            _close_windows_handle(handle)
            raise
        return handle

    def _open_windows_regular(path: Path, *, delete_access: bool) -> int:
        desired = _GENERIC_READ | _GENERIC_WRITE
        if delete_access:
            desired |= _DELETE
        handle = _open_windows_handle(
            path,
            desired_access=desired,
            directory=False,
        )
        try:
            _validate_windows_handle(handle, path, directory=False)
        except BaseException:
            _close_windows_handle(handle)
            raise
        return handle

    @contextmanager
    def _bound_windows_parent(
        target: Path,
        data_paths: DataPaths,
    ) -> Iterator[_WindowsBoundParent]:
        root, relative = _owned_relative(target, data_paths)
        if not relative.parts:
            raise OSError("operation target must be below the data root")
        handles: list[int] = []
        current = root
        try:
            root_handle = _open_windows_directory(root)
            handles.append(root_handle)
            for part in relative.parts[:-1]:
                current /= part
                handle = _open_windows_directory(current)
                handles.append(handle)
            yield _WindowsBoundParent(handles, current / relative.parts[-1], relative.parts[-1])
        finally:
            primary_error = sys.exc_info()[1]
            try:
                _close_windows_handles(handles)
            except BaseException:
                if primary_error is None:
                    raise

    def _flush_windows_handle(handle: int) -> None:
        if not _kernel32.FlushFileBuffers(handle):
            raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")

    def _close_windows_handle(handle: int) -> None:
        if not _kernel32.CloseHandle(handle):
            raise OSError(ctypes.get_last_error(), "CloseHandle failed")

    def _close_windows_handles(handles: list[int]) -> None:
        first_error: BaseException | None = None
        for handle in reversed(handles):
            try:
                _close_windows_handle(handle)
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _set_windows_pointer(handle: int, position: int) -> None:
        if not _kernel32.SetFilePointerEx(
            handle, position, None, _FILE_BEGIN
        ):
            raise OSError(ctypes.get_last_error(), "SetFilePointerEx failed")

    def _hash_windows_handle(handle: int) -> str:
        digest = hashlib.sha256()
        _set_windows_pointer(handle, 0)
        buffer = ctypes.create_string_buffer(1024 * 1024)
        while True:
            read = wintypes.DWORD()
            if not _kernel32.ReadFile(
                handle, buffer, len(buffer), ctypes.byref(read), None
            ):
                raise OSError(ctypes.get_last_error(), "ReadFile failed")
            if not read.value:
                break
            digest.update(buffer.raw[: read.value])
        _set_windows_pointer(handle, 0)
        return digest.hexdigest()

    def _write_windows_handle(handle: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            chunk = payload[offset : offset + 1024 * 1024]
            written = wintypes.DWORD()
            buffer = ctypes.create_string_buffer(chunk)
            if not _kernel32.WriteFile(
                handle, buffer, len(chunk), ctypes.byref(written), None
            ):
                raise OSError(ctypes.get_last_error(), "WriteFile failed")
            if not written.value:
                raise OSError("WriteFile made no progress")
            offset += written.value

    def _rename_windows_handle(
        handle: int,
        destination_parent_handle: int,
        destination_name: str,
    ) -> None:
        if (
            not destination_name
            or destination_name in {".", ".."}
            or "/" in destination_name
            or "\\" in destination_name
        ):
            raise OSError("handle-relative rename requires one leaf")
        encoded = destination_name.encode("utf-16-le")
        size = ctypes.sizeof(_FILE_RENAME_INFO) + len(encoded)
        buffer = ctypes.create_string_buffer(size)
        information = _FILE_RENAME_INFO.from_buffer(buffer)
        information.ReplaceIfExists = True
        information.RootDirectory = destination_parent_handle
        information.FileNameLength = len(encoded)
        ctypes.memmove(
            ctypes.addressof(buffer) + _FILE_RENAME_INFO.FileName.offset,
            encoded,
            len(encoded),
        )
        io_status = _IO_STATUS_BLOCK()
        status = _ntdll.NtSetInformationFile(
            wintypes.HANDLE(handle),
            ctypes.byref(io_status),
            buffer,
            size,
            _FILE_RENAME_INFORMATION_CLASS,
        )
        if status < 0:
            error_code = int(_ntdll.RtlNtStatusToDosError(status))
            raise OSError(error_code, "handle-bound rename failed")

    def _delete_windows_handle(handle: int) -> None:
        information = _FILE_DISPOSITION_INFO(True)
        if not _kernel32.SetFileInformationByHandle(
            handle,
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise OSError(ctypes.get_last_error(), "handle-bound delete failed")

    def _atomic_write_windows(
        target: Path,
        payload: bytes,
        *,
        data_paths: DataPaths,
    ) -> None:
        with _bound_windows_parent(target, data_paths) as bound:
            temporary_name = f".{bound.name}.{secrets.token_hex(12)}.tmp"
            temporary = bound.path.parent / temporary_name
            handle = _open_windows_relative(
                bound.parent_handle,
                temporary_name,
                desired_access=_GENERIC_READ | _GENERIC_WRITE | _DELETE,
                disposition=_FILE_CREATE,
                directory=False,
            )
            renamed = False
            try:
                _validate_windows_handle(handle, temporary, directory=False)
                _write_windows_handle(handle, payload)
                _flush_windows_handle(handle)
                _rename_windows_handle(
                    handle,
                    bound.parent_handle,
                    bound.name,
                )
                renamed = True
                if _windows_final_path(handle) != _canonical_windows_path(target):
                    raise OSError("atomic destination binding changed")
                _flush_windows_handle(handle)
                _flush_windows_handle(bound.parent_handle)
            finally:
                primary_error = sys.exc_info()[1]
                cleanup_error: BaseException | None = None
                if not renamed:
                    try:
                        _delete_windows_handle(handle)
                    except BaseException as error:
                        cleanup_error = error
                try:
                    _close_windows_handle(handle)
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
                if primary_error is None and cleanup_error is not None:
                    raise cleanup_error

    def _durable_mkdir_windows(root: Path, parts: tuple[str, ...]) -> None:
        handles: list[int] = []
        current = root
        try:
            parent_handle = _open_windows_directory(root)
            handles.append(parent_handle)
            for part in parts:
                current /= part
                created = False
                try:
                    handle = _open_windows_directory(current)
                except OSError as error:
                    if error.errno not in (2, 3):
                        raise
                    os.mkdir(_windows_path(current))
                    created = True
                    handle = _open_windows_directory(current)
                handles.append(handle)
                if created:
                    _flush_probe("mkdir_new_directory_flush")
                    _flush_windows_handle(handle)
                    _flush_probe("mkdir_parent_flush")
                    _flush_windows_handle(parent_handle)
                parent_handle = handle
        finally:
            primary_error = sys.exc_info()[1]
            try:
                _close_windows_handles(handles)
            except BaseException:
                if primary_error is None:
                    raise

    def _durable_replace_windows(
        source: Path,
        destination: Path,
        *,
        data_paths: DataPaths,
        expected_sha256: str | None,
    ) -> None:
        with _bound_windows_parent(source, data_paths) as src, _bound_windows_parent(
            destination, data_paths
        ) as dst:
            handle = _open_windows_regular(src.path, delete_access=True)
            try:
                source_info = _windows_information(handle)
                destination_info = _windows_information(dst.parent_handle)
                if source_info.dwVolumeSerialNumber != destination_info.dwVolumeSerialNumber:
                    raise OSError("durable replacement requires one volume")
                if (
                    expected_sha256 is not None
                    and _hash_windows_handle(handle) != expected_sha256
                ):
                    raise OSError("durable replacement source hash changed")
                _flush_probe("replace_source_file_flush")
                _flush_windows_handle(handle)
                _rename_windows_handle(
                    handle,
                    dst.parent_handle,
                    dst.name,
                )
                if _windows_final_path(handle) != _canonical_windows_path(dst.path):
                    raise OSError("durable replacement destination binding changed")
                _flush_probe("replace_destination_file_flush")
                _flush_windows_handle(handle)
                _flush_probe("replace_source_parent_flush")
                _flush_windows_handle(src.parent_handle)
                source_parent_info = _windows_information(src.parent_handle)
                destination_parent_info = _windows_information(
                    dst.parent_handle
                )
                if (
                    source_parent_info.dwVolumeSerialNumber,
                    source_parent_info.nFileIndexHigh,
                    source_parent_info.nFileIndexLow,
                ) != (
                    destination_parent_info.dwVolumeSerialNumber,
                    destination_parent_info.nFileIndexHigh,
                    destination_parent_info.nFileIndexLow,
                ):
                    _flush_probe("replace_destination_parent_flush")
                    _flush_windows_handle(dst.parent_handle)
            finally:
                _close_windows_handle(handle)

    def _durable_unlink_windows(target: Path, *, data_paths: DataPaths) -> None:
        with _bound_windows_parent(target, data_paths) as bound:
            handle = _open_windows_regular(bound.path, delete_access=True)
            try:
                _flush_probe("unlink_target_file_flush")
                _flush_windows_handle(handle)
                _delete_windows_handle(handle)
            finally:
                _close_windows_handle(handle)
            _flush_probe("unlink_parent_flush")
            _flush_windows_handle(bound.parent_handle)

    def _durable_rmdir_windows(target: Path, *, data_paths: DataPaths) -> None:
        with _bound_windows_parent(target, data_paths) as bound:
            handle = _open_windows_handle(
                bound.path,
                desired_access=_GENERIC_READ | _GENERIC_WRITE | _DELETE,
                directory=True,
            )
            try:
                _validate_windows_handle(handle, bound.path, directory=True)
                _flush_probe("rmdir_quarantine_directory_flush")
                _flush_windows_handle(handle)
                _delete_windows_handle(handle)
            finally:
                _close_windows_handle(handle)
            _flush_probe("rmdir_parent_flush")
            _flush_windows_handle(bound.parent_handle)

else:
    def _atomic_write_windows(
        target: Path, payload: bytes, *, data_paths: DataPaths
    ) -> None:
        raise AssertionError((target, payload, data_paths))

    def _durable_mkdir_windows(root: Path, parts: tuple[str, ...]) -> None:
        raise AssertionError((root, parts))

    def _durable_replace_windows(
        source: Path,
        destination: Path,
        *,
        data_paths: DataPaths,
        expected_sha256: str | None,
    ) -> None:
        raise AssertionError((source, destination, data_paths, expected_sha256))

    def _durable_unlink_windows(target: Path, *, data_paths: DataPaths) -> None:
        raise AssertionError((target, data_paths))

    def _durable_rmdir_windows(target: Path, *, data_paths: DataPaths) -> None:
        raise AssertionError((target, data_paths))
