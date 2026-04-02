import asyncio
import time

from code_indexer import server


class _DummyWatcher:
    def get_status(self):
        return {"running": True}

    def stop(self):
        return None

    def start(self, *_args, **_kwargs):
        return None


class _DummyDeep:
    def is_built(self):
        return False

    def get_stats(self):
        return {}


class _DummyShallow:
    def get_stats(self):
        return {}


async def _wait_for(condition, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def test_build_deep_index_returns_started_and_rejects_duplicate(monkeypatch):
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_run_deep_index_build(**_kwargs):
            server._transition_build_state(server.BuildStatus.RUNNING)
            server._set_build_progress(
                "parsing",
                1,
                4,
                message="Parsing files for the deep index.",
            )
            started.set()
            await release.wait()
            server._transition_build_state(
                server.BuildStatus.COMPLETED,
                finished_at=time.time(),
            )
            server._set_build_progress(
                "parsing",
                4,
                4,
                message="Deep index build completed.",
            )

        monkeypatch.setattr(server, "_run_deep_index_build", fake_run_deep_index_build)
        monkeypatch.setattr(server, "_watcher", _DummyWatcher())
        server._state.update(
            {
                "project_path": "/tmp/project",
                "cache_dir": "/tmp/cache",
                "shallow": _DummyShallow(),
                "deep": _DummyDeep(),
                "vector": object(),
            }
        )
        server._reset_build_state(project_path="/tmp/project")

        first = await server.build_deep_index(force_rebuild=False)
        assert first["status"] == "started"
        assert "get_index_status" in first["message"]

        await _wait_for(started.is_set)

        second = await server.build_deep_index(force_rebuild=True)
        assert second["status"] == "already_in_progress"
        assert second["indexing"]["in_progress"] is True
        assert second["indexing"]["phase"] == "parsing"
        assert second["indexing"]["percent_done"] == 25.0

        status = server.get_index_status()
        assert status["indexing"]["in_progress"] is True
        assert status["indexing"]["eta_seconds"] is not None

        release.set()
        await _wait_for(lambda: server._build_state["status"] == "completed")

    asyncio.run(scenario())


def test_get_indexing_status_ignores_stale_runtime_task():
    server._reset_build_state(project_path="/tmp/project")
    server._build_task = type(
        "_DoneTask",
        (),
        {"done": lambda self: True, "cancel": lambda self: None},
    )()
    server._transition_build_state(
        server.BuildStatus.COMPLETED,
        started_at=time.time() - 2,
        finished_at=time.time(),
        phase="embedding",
        phase_started_at=time.time() - 1,
        completed=8,
        total=8,
        percent_done=100.0,
        eta_seconds=0,
        message="Deep index build completed.",
        result={"files_parsed": 3},
    )

    status = server._get_indexing_status()
    assert status["in_progress"] is False
    assert status["status"] == "completed"


def test_get_indexing_status_reports_completed_progress():
    server._reset_build_state(project_path="/tmp/project")
    server._transition_build_state(
        server.BuildStatus.COMPLETED,
        started_at=time.time() - 2,
        finished_at=time.time(),
        phase="embedding",
        phase_started_at=time.time() - 1,
        completed=8,
        total=8,
        percent_done=100.0,
        eta_seconds=0,
        message="Deep index build completed.",
        result={"files_parsed": 3},
    )

    status = server._get_indexing_status()
    assert status["in_progress"] is False
    assert status["percent_done"] == 100.0
    assert status["eta_seconds"] == 0
    assert status["result"] == {"files_parsed": 3}


def test_reset_build_state_cancels_runtime_tasks():
    cancelled = {"build": False, "background": False}

    class _PendingTask:
        def done(self):
            return False

        def cancel(self):
            cancelled["build"] = True

    class _BackgroundTask:
        def done(self):
            return False

        def cancel(self):
            cancelled["background"] = True

    server._build_task = _PendingTask()
    server._background_tasks = {_BackgroundTask()}

    server._reset_build_state(project_path="/tmp/project")

    assert cancelled["build"] is True
    assert cancelled["background"] is True
    assert server._background_tasks == set()
