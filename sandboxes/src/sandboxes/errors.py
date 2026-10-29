from typing import Literal

__all__ = (
    "BackupErrorCode",
    "BackupOperation",
    "FileOperation",
    "S3MountErrorCode",
    "S3MountOperation",
    "SandboxAbortError",
    "SandboxBackupError",
    "SandboxError",
    "SandboxFileError",
    "SandboxProtocolError",
    "SandboxS3MountError",
)

type FileOperation = Literal[
    "read_file",
    "write_file",
    "stat",
    "lstat",
    "read_directory",
    "mkdir",
    "rename",
    "remove",
    "backup",
    "restore",
]
type S3MountOperation = Literal["mount", "inspect", "unmount"]
type S3MountErrorCode = Literal[
    "S3_MOUNT_BUSY", "S3_MOUNT_CONFLICT", "S3_MOUNT_FAILED", "S3_MOUNT_INCOMPATIBLE"
]
type BackupOperation = Literal["backup", "restore", "delete"]
type BackupErrorCode = Literal[
    "BACKUP_NOT_FOUND", "BACKUP_INTEGRITY", "BACKUP_TRANSFER"
]

# Linux errno values, which the shim sends. Python's errno module holds the
# values of the platform it runs on, and Pyodide's differ from Linux's.
_ERRNO_NAMES = {
    1: "EPERM", 2: "ENOENT", 3: "ESRCH", 4: "EINTR", 5: "EIO", 6: "ENXIO",
    7: "E2BIG", 8: "ENOEXEC", 9: "EBADF", 10: "ECHILD", 11: "EAGAIN",
    12: "ENOMEM", 13: "EACCES", 14: "EFAULT", 16: "EBUSY", 17: "EEXIST",
    18: "EXDEV", 19: "ENODEV", 20: "ENOTDIR", 21: "EISDIR", 22: "EINVAL",
    23: "ENFILE", 24: "EMFILE", 25: "ENOTTY", 26: "ETXTBSY", 27: "EFBIG",
    28: "ENOSPC", 29: "ESPIPE", 30: "EROFS", 31: "EMLINK", 32: "EPIPE",
    33: "EDOM", 34: "ERANGE", 35: "EDEADLK", 36: "ENAMETOOLONG", 37: "ENOLCK",
    38: "ENOSYS", 39: "ENOTEMPTY", 40: "ELOOP", 42: "ENOMSG", 43: "EIDRM",
    60: "ENOSTR", 61: "ENODATA", 62: "ETIME", 63: "ENOSR", 67: "ENOLINK",
    71: "EPROTO", 72: "EMULTIHOP", 74: "EBADMSG", 75: "EOVERFLOW", 84: "EILSEQ",
    88: "ENOTSOCK", 89: "EDESTADDRREQ", 90: "EMSGSIZE", 91: "EPROTOTYPE",
    92: "ENOPROTOOPT", 93: "EPROTONOSUPPORT", 95: "EOPNOTSUPP",
    97: "EAFNOSUPPORT", 98: "EADDRINUSE", 99: "EADDRNOTAVAIL", 100: "ENETDOWN",
    101: "ENETUNREACH", 102: "ENETRESET", 103: "ECONNABORTED", 104: "ECONNRESET",
    105: "ENOBUFS", 106: "EISCONN", 107: "ENOTCONN", 110: "ETIMEDOUT",
    111: "ECONNREFUSED", 113: "EHOSTUNREACH", 114: "EALREADY", 115: "EINPROGRESS",
    116: "ESTALE", 122: "EDQUOT", 125: "ECANCELED",
}  # fmt: skip


class SandboxError(Exception):
    """The base class of every error this package raises."""


class SandboxAbortError(SandboxError):
    """The caller set the ``abort`` event before the operation finished."""

    def __init__(self) -> None:
        super().__init__("the operation was aborted")


class SandboxFileError(SandboxError):
    """
    Linux rejected a file operation in the container.

    Attributes
    ----------
    code
        The errno name, such as ``"ENOENT"``, or ``"UNKNOWN"``.
    operation
        The method that failed.
    path
        The path the method was called with.
    destination
        The destination of a ``rename``, otherwise ``None``.
    detail
        The shim's description of the failure.
    """

    def __init__(
        self,
        code: str,
        operation: FileOperation,
        path: str,
        detail: str,
        *,
        destination: str | None = None,
    ) -> None:
        subject = f"'{path}'" if destination is None else f"'{path}' to '{destination}'"
        super().__init__(f"{operation} {subject}: {detail}")
        self.code = code
        self.operation: FileOperation = operation
        self.path = path
        self.destination = destination
        self.detail = detail

    @classmethod
    def from_errno(
        cls,
        errno: int,
        detail: str,
        operation: FileOperation,
        path: str,
        *,
        destination: str | None = None,
    ) -> "SandboxFileError":
        code = _ERRNO_NAMES.get(errno, "UNKNOWN")
        return cls(code, operation, path, detail or code, destination=destination)


class SandboxProtocolError(SandboxError):
    """
    The package and ``sandbox-shim`` could not complete their exchange.

    This usually means the image's shim and the package version don't match.
    """

    code = "SANDBOX_PROTOCOL_ERROR"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class SandboxS3MountError(SandboxError):
    """
    An S3 mount operation failed in the container.

    Attributes
    ----------
    code
        ``S3_MOUNT_BUSY``, ``S3_MOUNT_CONFLICT``, ``S3_MOUNT_FAILED`` or
        ``S3_MOUNT_INCOMPATIBLE``.
    operation
        ``"mount"``, ``"inspect"`` or ``"unmount"``.
    path
        The mount path.
    detail
        The shim's description of the failure.
    """

    def __init__(
        self,
        code: S3MountErrorCode,
        operation: S3MountOperation,
        path: str,
        detail: str,
    ) -> None:
        super().__init__(f"{operation} '{path}': {detail}")
        self.code: S3MountErrorCode = code
        self.operation: S3MountOperation = operation
        self.path = path
        self.detail = detail


class SandboxBackupError(SandboxError):
    """
    A directory backup is missing, changed, or failed to transfer.

    Attributes
    ----------
    code
        ``BACKUP_NOT_FOUND``, ``BACKUP_INTEGRITY`` or ``BACKUP_TRANSFER``.
    operation
        ``"backup"``, ``"restore"`` or ``"delete"``.
    path
        The directory being saved or restored.
    detail
        A description of the failure.
    """

    def __init__(
        self, code: BackupErrorCode, operation: BackupOperation, path: str, detail: str
    ) -> None:
        super().__init__(f"{operation} '{path}': {detail}")
        self.code: BackupErrorCode = code
        self.operation: BackupOperation = operation
        self.path = path
        self.detail = detail
