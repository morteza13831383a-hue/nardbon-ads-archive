"""Cross-platform advisory file lock (fcntl on POSIX, msvcrt on Windows)."""
import os

try:
    import fcntl
    LOCK_EX = fcntl.LOCK_EX
    LOCK_NB = fcntl.LOCK_NB

    def flock(handle, flags):
        fcntl.flock(handle, flags)
except ImportError:
    import msvcrt
    LOCK_EX = 1
    LOCK_NB = 2

    def flock(handle, flags):
        mode = msvcrt.LK_NBLCK if (flags & LOCK_NB) else msvcrt.LK_LOCK
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), mode, 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc
