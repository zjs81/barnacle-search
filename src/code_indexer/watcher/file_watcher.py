import logging
import platform
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from ..constants import SUPPORTED_EXTENSIONS, EXCLUDE_DIRS, DEBOUNCE_SECS

logger = logging.getLogger(__name__)


def _path_has_excluded_component(path: str, exclude_dirs: set) -> bool:
    """Return True if any component of path is in exclude_dirs."""
    parts = Path(path).parts
    return any(part in exclude_dirs for part in parts)


class DebounceEventHandler(FileSystemEventHandler):
    """
    Collects changed file paths and fires callback after DEBOUNCE_SECS of quiet.
    Only processes files with SUPPORTED_EXTENSIONS.
    Skips paths that are under EXCLUDE_DIRS.
    """

    def __init__(
        self,
        debounce_secs: float,
        callback: Callable[[str], None],
        project_path: str,
        repo_change_callback: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.debounce_secs = debounce_secs
        self.callback = callback
        self.project_path = project_path
        self.repo_change_callback = repo_change_callback
        self._timer: Optional[threading.Timer] = None
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._last_git_head = self._get_git_head()

    def _get_git_head(self) -> Optional[str]:
        try:
            proc = subprocess.run(
                ["git", "-C", self.project_path, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        head = proc.stdout.strip()
        return head or None

    def _consume_repo_change(self) -> bool:
        current_head = self._get_git_head()
        if current_head is None:
            self._last_git_head = None
            return False
        if self._last_git_head is None:
            self._last_git_head = current_head
            return False
        if current_head == self._last_git_head:
            return False
        self._last_git_head = current_head
        return True

    def on_any_event(self, event: FileSystemEvent):
        if event.is_directory:
            return

        src_path: str = event.src_path

        # Check extension
        suffix = Path(src_path).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            return

        # Check that no component of the path is an excluded directory
        if _path_has_excluded_component(src_path, EXCLUDE_DIRS):
            return

        with self._lock:
            self._pending.add(src_path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_secs, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self):
        with self._lock:
            pending = set(self._pending)
            self._pending.clear()
            self._timer = None

        if self.repo_change_callback is not None and self._consume_repo_change():
            try:
                self.repo_change_callback()
            except Exception:
                logger.exception("Error in repo change callback for '%s'", self.project_path)
            return

        for path in pending:
            try:
                self.callback(path)
            except Exception:
                logger.exception("Error in rebuild callback for path '%s'", path)


def _make_observer():
    """
    Pick the best available watchdog observer for the current platform.

    macOS priority:
      1. FSEventsObserver  — event-driven, directory-level (no per-file FDs,
                             so it works fine on repos with node_modules)
      2. PollingObserver   — fallback if FSEvents is somehow unavailable

    Other platforms:
      InotifyObserver / ReadDirectoryChangesW via the default Observer.
    """
    if platform.system() == "Darwin":
        try:
            from watchdog.observers.fsevents import FSEventsObserver
            return FSEventsObserver()
        except Exception:
            logger.warning("FSEventsObserver unavailable, falling back to PollingObserver")
            from watchdog.observers.polling import PollingObserver
            return PollingObserver(timeout=2)
    else:
        from watchdog.observers import Observer
        return Observer()


class FileWatcherService:
    def __init__(self):
        self._observer = None
        self._monitoring = False
        self._project_path: Optional[str] = None

    def start(
        self,
        project_path: str,
        rebuild_callback: Callable[[str], None],
        repo_change_callback: Optional[Callable[[], None]] = None,
    ):
        """
        Start watching project_path.

        On macOS uses FSEventsObserver (directory-level, no per-file FDs — avoids
        the 'too many open files' problem that KqueueObserver causes on large repos
        with node_modules). Falls back to PollingObserver if FSEvents is unavailable.
        If already watching, stops first.
        """
        if self._monitoring:
            logger.info("FileWatcherService: stopping existing watcher before restart")
            self.stop()

        observer = _make_observer()

        handler = DebounceEventHandler(
            debounce_secs=DEBOUNCE_SECS,
            callback=rebuild_callback,
            project_path=project_path,
            repo_change_callback=repo_change_callback,
        )

        observer.schedule(handler, path=project_path, recursive=True)
        observer.start()

        self._observer = observer
        self._project_path = project_path
        self._monitoring = True
        logger.info(
            "FileWatcherService: started watching '%s' with %s",
            project_path,
            type(observer).__name__,
        )

    def stop(self):
        """Stop watching. Safe to call even if not started."""
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5.0)
            except Exception:
                logger.exception("FileWatcherService: error stopping observer")
            finally:
                self._observer = None

        self._monitoring = False
        self._project_path = None
        logger.info("FileWatcherService: stopped")

    @property
    def is_monitoring(self) -> bool:
        return self._monitoring

    def get_status(self) -> dict:
        return {
            "monitoring": self._monitoring,
            "project_path": self._project_path,
            "observer_type": type(self._observer).__name__ if self._observer else None,
        }
