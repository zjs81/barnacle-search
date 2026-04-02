from watchdog.events import FileModifiedEvent

from code_indexer.watcher.file_watcher import DebounceEventHandler


def test_fire_runs_repo_change_callback_when_git_head_changes(tmp_path, monkeypatch):
    repo_changes: list[str] = []
    rebuilt: list[str] = []
    heads = iter(["head-a", "head-b"])

    monkeypatch.setattr(
        DebounceEventHandler,
        "_get_git_head",
        lambda self: next(heads),
    )

    handler = DebounceEventHandler(
        debounce_secs=0.01,
        callback=rebuilt.append,
        project_path=str(tmp_path),
        repo_change_callback=lambda: repo_changes.append("changed"),
    )
    handler._pending.add(str(tmp_path / "file.py"))

    handler._fire()

    assert repo_changes == ["changed"]
    assert rebuilt == []


def test_fire_runs_file_callbacks_when_git_head_is_unchanged(tmp_path, monkeypatch):
    rebuilt: list[str] = []

    monkeypatch.setattr(
        DebounceEventHandler,
        "_get_git_head",
        lambda self: "same-head",
    )

    handler = DebounceEventHandler(
        debounce_secs=0.01,
        callback=rebuilt.append,
        project_path=str(tmp_path),
        repo_change_callback=lambda: None,
    )
    path = str(tmp_path / "file.py")
    handler._pending.add(path)

    handler._fire()

    assert rebuilt == [path]


def test_on_any_event_only_tracks_supported_non_excluded_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        DebounceEventHandler,
        "_get_git_head",
        lambda self: None,
    )

    handler = DebounceEventHandler(
        debounce_secs=0.01,
        callback=lambda path: None,
        project_path=str(tmp_path),
    )

    handler.on_any_event(FileModifiedEvent(str(tmp_path / "src" / "main.py")))
    handler.on_any_event(FileModifiedEvent(str(tmp_path / ".git" / "HEAD")))
    handler.on_any_event(FileModifiedEvent(str(tmp_path / "notes.txt")))

    assert handler._pending == {str(tmp_path / "src" / "main.py")}
    if handler._timer is not None:
        handler._timer.cancel()
