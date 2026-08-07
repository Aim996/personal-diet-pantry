from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timezone
import ast
import json
import inspect
import multiprocessing
from pathlib import Path
import threading
from typing import Iterator

import pytest

from personal_diet_pantry import data_export, derived_file_leases, reports, self_check
from personal_diet_pantry.derived_file_leases import (
    DerivedFileLeaseManager,
    LeaseOwnerToken,
    manager_for,
)
from personal_diet_pantry.service import DietService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RACE_CANARY = "ERASE-PUBLISHER-RACE-CANARY-070"


def _dispatch(service: DietService, action: str, payload: dict) -> dict:
    return service.dispatch(
        {"domain": "system", "action": action, "payload": payload}
    )


def _paused_report_publisher(
    data_dir: str,
    ready,
    release,
    result_queue,
) -> None:
    original_write = reports.atomic_write_text

    def pause_publish(destination, contents, **kwargs):
        if RACE_CANARY not in contents:
            raise AssertionError("publisher did not read the canary fact")
        ready.set()
        if not release.wait(20):
            raise TimeoutError("publisher release was not signaled")
        return original_write(destination, contents, **kwargs)

    reports.atomic_write_text = pause_publish
    try:
        with DietService(
            source_root=PROJECT_ROOT,
            plugin_config={"dataDir": data_dir},
            env={},
        ) as service:
            path = reports.build_daily_report(
                service.connection,
                service.data_paths,
                service.settings,
                date(2026, 7, 30),
                templates_dir=service.templates_dir,
            )
            result_queue.put({"ok": True, "path": str(path)})
    except BaseException as error:
        result_queue.put({"ok": False, "error": repr(error)})


def _paused_delete_worker(
    data_dir: str,
    commit_handle: str,
    checkpoint: str,
    reached,
    release,
    result_queue,
) -> None:
    try:
        with DietService(
            source_root=PROJECT_ROOT,
            plugin_config={"dataDir": data_dir},
            env={},
        ) as service:
            def pause(observed: str) -> None:
                if observed == checkpoint:
                    reached.set()
                    if not release.wait(20):
                        raise TimeoutError("deletion release was not signaled")

            service.trusted_workflows._crash_probe = pause
            result_queue.put(
                _dispatch(
                    service,
                    "commit_delete_data",
                    {
                        "commit_handle": commit_handle,
                        "confirmed": True,
                        "operation_key": f"mp-delete-{checkpoint}",
                    },
                )
            )
    except BaseException as error:
        result_queue.put({"ok": False, "error": repr(error)})


def _post_erasure_export_publisher(
    data_dir: str,
    fact_read,
    result_queue,
) -> None:
    original_records = data_export._portable_records

    def observed_records(*args, **kwargs):
        fact_read.set()
        return original_records(*args, **kwargs)

    data_export._portable_records = observed_records
    try:
        with DietService(
            source_root=PROJECT_ROOT,
            plugin_config={"dataDir": data_dir},
            env={},
        ) as service:
            result_queue.put(_dispatch(service, "export_data", {"format": "json"}))
    except BaseException as error:
        result_queue.put({"ok": False, "error": repr(error)})


class _RecordingLeaseManager:
    def __init__(self, manager: DerivedFileLeaseManager, events: list[str]) -> None:
        self.manager = manager
        self.events = events

    @contextmanager
    def shared_publisher(
        self,
        *,
        owner: LeaseOwnerToken | None = None,
    ) -> Iterator[LeaseOwnerToken]:
        reentry = owner is not None
        self.events.append("shared_reenter" if reentry else "shared_enter")
        with self.manager.shared_publisher(owner=owner) as issued:
            try:
                yield issued
            finally:
                self.events.append(
                    "shared_reexit" if reentry else "shared_exit"
                )


def test_same_canonical_lock_path_shares_process_gate_and_rejects_foreign_owner(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "control" / "derived-files.lock"
    first = DerivedFileLeaseManager(lock)
    second = DerivedFileLeaseManager(lock.parent / "." / lock.name)
    other = DerivedFileLeaseManager(tmp_path / "other" / "derived-files.lock")
    assert first._state is second._state

    with first.shared_publisher() as owner:
        with second.shared_publisher(owner=owner) as reentered:
            assert reentered is owner
            assert first._state.os_shared_acquire_count == 1
        with pytest.raises(RuntimeError):
            other.shared_publisher(owner=owner).__enter__()
        with pytest.raises(RuntimeError):
            first.shared_publisher(
                owner=replace(owner, _nonce="0" * 64)
            ).__enter__()

    with pytest.raises(RuntimeError):
        second.shared_publisher(owner=owner).__enter__()


def test_waiting_exclusive_blocks_new_owner_but_not_active_owner_reentry(
    tmp_path: Path,
) -> None:
    manager = DerivedFileLeaseManager(
        tmp_path / "control" / "derived-files.lock"
    )
    writer_acquired = threading.Event()
    release_writer = threading.Event()
    newcomer_acquired = threading.Event()

    def writer() -> None:
        with manager.exclusive_erasure():
            writer_acquired.set()
            assert release_writer.wait(5)

    def newcomer() -> None:
        with manager.shared_publisher():
            newcomer_acquired.set()

    with manager.shared_publisher() as owner:
        writer_thread = threading.Thread(target=writer)
        writer_thread.start()
        assert manager.wait_for_waiting_writers(1, timeout=5)
        newcomer_thread = threading.Thread(target=newcomer)
        newcomer_thread.start()
        assert not newcomer_acquired.is_set()
        with manager.shared_publisher(owner=owner):
            assert not writer_acquired.is_set()

    assert writer_acquired.wait(5)
    assert not newcomer_acquired.is_set()
    release_writer.set()
    writer_thread.join(5)
    newcomer_thread.join(5)
    assert newcomer_acquired.is_set()


@pytest.mark.parametrize("fault_target", ["_unlock_handle", "_close_lock_handle"])
def test_shared_release_fault_wakes_writer_and_restores_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_target: str,
) -> None:
    manager = DerivedFileLeaseManager(
        tmp_path / "control" / "derived-files.lock"
    )
    shared = manager.shared_publisher()
    shared.__enter__()
    writer_acquired = threading.Event()
    release_writer = threading.Event()

    def writer() -> None:
        with manager.exclusive_erasure():
            writer_acquired.set()
            assert release_writer.wait(5)

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    assert manager.wait_for_waiting_writers(1, timeout=5)
    original = getattr(derived_file_leases, fault_target)
    injected = False

    def fail_once(handle: object) -> None:
        nonlocal injected
        if injected:
            original(handle)
            return
        injected = True
        if fault_target == "_close_lock_handle":
            original(handle)
        raise OSError(f"injected {fault_target}")

    monkeypatch.setattr(derived_file_leases, fault_target, fail_once)
    with pytest.raises(OSError, match=fault_target):
        shared.__exit__(None, None, None)
    awakened_without_manual_repair = writer_acquired.wait(1)
    if not awakened_without_manual_repair:
        with manager._state.condition:
            manager._state.condition.notify_all()
        assert writer_acquired.wait(5)
    release_writer.set()
    writer_thread.join(5)
    assert awakened_without_manual_repair
    with manager.shared_publisher():
        pass


@pytest.mark.parametrize("fault_target", ["_unlock_handle", "_close_lock_handle"])
def test_exclusive_release_fault_restores_gate_for_later_publisher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_target: str,
) -> None:
    manager = DerivedFileLeaseManager(
        tmp_path / "control" / "derived-files.lock"
    )
    exclusive = manager.exclusive_erasure()
    exclusive.__enter__()
    original = getattr(derived_file_leases, fault_target)
    injected = False

    def fail_once(handle: object) -> None:
        nonlocal injected
        if injected:
            original(handle)
            return
        injected = True
        if fault_target == "_close_lock_handle":
            original(handle)
        raise OSError(f"injected {fault_target}")

    monkeypatch.setattr(derived_file_leases, fault_target, fail_once)
    with pytest.raises(OSError, match=fault_target):
        exclusive.__exit__(None, None, None)
    assert manager._state.exclusive_active is False
    assert manager._state.exclusive_thread_id is None
    with manager.shared_publisher():
        pass


def test_delete_waits_for_active_publisher_then_quarantines_its_output(
    tmp_path: Path,
) -> None:
    data_dir = str(tmp_path / "publisher-first")
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": data_dir},
        env={},
    ) as service:
        recorded = service.dispatch(
            {
                "domain": "water",
                "action": "record",
                "payload": {
                    "amount": "300",
                    "unit": "ml",
                    "occurred_at": "2026-07-30T01:00:00Z",
                    "source_text": RACE_CANARY,
                },
            }
        )
        assert recorded["ok"] is True
        preview = _dispatch(
            service,
            "preview_delete_data",
            {"scope": "all_business"},
        )
        commit_handle = preview["data"]["workflow"]["commit_handle"]

    context = multiprocessing.get_context("spawn")
    publisher_ready = context.Event()
    release_publisher = context.Event()
    manifest_persisted = context.Event()
    release_delete = context.Event()
    publisher_results = context.Queue()
    delete_results = context.Queue()
    publisher = context.Process(
        target=_paused_report_publisher,
        args=(
            data_dir,
            publisher_ready,
            release_publisher,
            publisher_results,
        ),
    )
    delete = context.Process(
        target=_paused_delete_worker,
        args=(
            data_dir,
            commit_handle,
            "manifest_persisted",
            manifest_persisted,
            release_delete,
            delete_results,
        ),
    )
    publisher.start()
    try:
        assert publisher_ready.wait(20)
        delete.start()
        assert not manifest_persisted.wait(1)
        release_publisher.set()
        assert manifest_persisted.wait(20)
        release_delete.set()
        publisher.join(20)
        delete.join(20)
        assert publisher.exitcode == 0
        assert delete.exitcode == 0
        assert publisher_results.get(timeout=5)["ok"] is True
        assert delete_results.get(timeout=5)["ok"] is True
    finally:
        release_publisher.set()
        release_delete.set()
        for process in (publisher, delete):
            if process.pid is not None and process.is_alive():
                process.terminate()
            if process.pid is not None:
                process.join(5)

    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": data_dir},
        env={},
    ) as reopened:
        derived = [
            path
            for root in (
                reopened.data_paths.cache,
                reopened.data_paths.exports,
                reopened.data_paths.reports,
            )
            for path in root.rglob("*")
            if path.is_file()
        ]
        assert derived == []


def test_publisher_waiting_behind_delete_re_reads_post_erasure_facts(
    tmp_path: Path,
) -> None:
    data_dir = str(tmp_path / "delete-first")
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": data_dir},
        env={},
    ) as service:
        recorded = service.dispatch(
            {
                "domain": "water",
                "action": "record",
                "payload": {
                    "amount": "300",
                    "unit": "ml",
                    "occurred_at": "2026-07-30T01:00:00Z",
                    "source_text": RACE_CANARY,
                },
            }
        )
        assert recorded["ok"] is True
        preview = _dispatch(
            service,
            "preview_delete_data",
            {"scope": "all_business"},
        )
        commit_handle = preview["data"]["workflow"]["commit_handle"]

    context = multiprocessing.get_context("spawn")
    exclusive_acquired = context.Event()
    release_delete = context.Event()
    fact_read = context.Event()
    delete_results = context.Queue()
    publisher_results = context.Queue()
    delete = context.Process(
        target=_paused_delete_worker,
        args=(
            data_dir,
            commit_handle,
            "derived_exclusive_acquired",
            exclusive_acquired,
            release_delete,
            delete_results,
        ),
    )
    publisher = context.Process(
        target=_post_erasure_export_publisher,
        args=(data_dir, fact_read, publisher_results),
    )
    delete.start()
    try:
        assert exclusive_acquired.wait(10)
        publisher.start()
        assert not fact_read.wait(1)
        release_delete.set()
        assert fact_read.wait(20)
        delete.join(20)
        publisher.join(20)
        assert delete.exitcode == 0
        assert publisher.exitcode == 0
        assert delete_results.get(timeout=5)["ok"] is True
        assert publisher_results.get(timeout=5)["ok"] is True
    finally:
        release_delete.set()
        for process in (delete, publisher):
            if process.pid is not None and process.is_alive():
                process.terminate()
            if process.pid is not None:
                process.join(5)

    export_text = "\n".join(
        path.read_text(encoding="utf-8", errors="strict")
        for path in (Path(data_dir) / "exports").glob("*.json")
    )
    assert RACE_CANARY not in export_text


def test_every_derived_root_publisher_uses_shared_lease(
    service: DietService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = manager_for(service.data_paths)

    def first_select(events: list[str], label: str):
        seen = False

        def trace(statement: str) -> None:
            nonlocal seen
            if not seen and statement.lstrip().upper().startswith("SELECT"):
                seen = True
                events.append(label)

        return trace

    export_events: list[str] = []
    export_manager = _RecordingLeaseManager(manager, export_events)
    original_export_write = data_export.atomic_write_text

    def export_write(*args, **kwargs):
        export_events.append("durable_publish")
        return original_export_write(*args, **kwargs)

    monkeypatch.setattr(data_export, "atomic_write_text", export_write)
    service.connection.set_trace_callback(first_select(export_events, "fact_reads"))
    data_export.export_data(
        service.connection,
        service.data_paths,
        export_format="json",
        product_version="0.6.7",
        timezone_name=service.settings.profile.timezone,
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        lease_manager=export_manager,
    )
    service.connection.set_trace_callback(None)
    assert export_events[0] == "shared_enter"
    assert export_events.index("fact_reads") < export_events.index("durable_publish")
    assert export_events[-1] == "shared_exit"

    report_events: list[str] = []
    report_manager = _RecordingLeaseManager(manager, report_events)
    original_report_write = reports.atomic_write_text

    def report_write(*args, **kwargs):
        report_events.append("durable_publish")
        return original_report_write(*args, **kwargs)

    monkeypatch.setattr(reports, "atomic_write_text", report_write)
    service.connection.set_trace_callback(first_select(report_events, "fact_reads"))
    reports.build_daily_report(
        service.connection,
        service.data_paths,
        service.settings,
        date(2026, 7, 30),
        templates_dir=service.templates_dir,
        lease_manager=report_manager,
    )
    service.connection.set_trace_callback(None)
    assert report_events[0] == "shared_enter"
    assert report_events.index("fact_reads") < report_events.index("durable_publish")
    assert report_events[-1] == "shared_exit"

    self_check_events: list[str] = []
    self_check_manager = _RecordingLeaseManager(manager, self_check_events)
    original_health_write = self_check.atomic_write_text

    def nested_report_write(*args, **kwargs):
        self_check_events.append("nested_report_publish")
        return original_report_write(*args, **kwargs)

    def health_write(*args, **kwargs):
        self_check_events.append("health_publish")
        return original_health_write(*args, **kwargs)

    monkeypatch.setattr(reports, "atomic_write_text", nested_report_write)
    monkeypatch.setattr(self_check, "atomic_write_text", health_write)
    before_acquires = manager._state.os_shared_acquire_count
    before_releases = manager._state.os_shared_release_count
    service.connection.set_trace_callback(
        first_select(self_check_events, "check_reads")
    )
    self_check.run_self_check(
        service.connection,
        service.data_paths,
        service.migrations_dir,
        source_root=service.source_root,
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        write_report=True,
        lease_manager=self_check_manager,
    )
    service.connection.set_trace_callback(None)
    assert self_check_events[0] == "shared_enter"
    assert self_check_events.index("check_reads") < self_check_events.index(
        "shared_reenter"
    )
    assert self_check_events.count("shared_reenter") == 3
    assert self_check_events.count("shared_reexit") == 3
    assert self_check_events.count("nested_report_publish") == 3
    assert self_check_events.index("health_publish") > max(
        index
        for index, value in enumerate(self_check_events)
        if value == "nested_report_publish"
    )
    assert self_check_events[-1] == "shared_exit"
    assert manager._state.os_shared_acquire_count - before_acquires == 1
    assert manager._state.os_shared_release_count - before_releases == 1


def test_self_check_nested_report_reenters_while_exclusive_waits(
    service: DietService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = manager_for(service.data_paths)
    recording_manager = _RecordingLeaseManager(manager, [])
    outer_paused = threading.Event()
    writer_acquired = threading.Event()
    allow_writer_release = threading.Event()
    writer_released = threading.Event()
    unrelated_started = threading.Event()
    unrelated_fact_read = threading.Event()
    unrelated_published = threading.Event()
    health_published = threading.Event()
    unrelated_finished = threading.Event()
    order: list[str] = []
    errors: list[BaseException] = []
    order_lock = threading.Lock()
    nested_pause_used = False

    def record(label: str) -> None:
        with order_lock:
            order.append(label)

    original_nested_daily = self_check.build_daily_report
    writer_thread: threading.Thread
    unrelated_thread: threading.Thread

    def pause_before_first_nested(*args, **kwargs):
        nonlocal nested_pause_used
        if kwargs.get("lease_owner") is not None and not nested_pause_used:
            nested_pause_used = True
            record("outer_paused")
            outer_paused.set()
            writer_thread.start()
            if not manager.wait_for_waiting_writers(1, timeout=10):
                raise TimeoutError("exclusive writer did not queue")
            unrelated_thread.start()
            if not unrelated_started.wait(10):
                raise TimeoutError("unrelated report did not start")
            if unrelated_fact_read.wait(0.25):
                raise AssertionError(
                    "unrelated report read facts before nested reentry"
                )
        return original_nested_daily(*args, **kwargs)

    monkeypatch.setattr(
        self_check,
        "build_daily_report",
        pause_before_first_nested,
    )
    original_report_write = reports.atomic_write_text

    def record_report_publish(*args, **kwargs):
        thread_name = threading.current_thread().name
        if thread_name == "unrelated-report":
            record("unrelated_publish")
            unrelated_published.set()
        else:
            record("nested_publish")
        return original_report_write(*args, **kwargs)

    monkeypatch.setattr(reports, "atomic_write_text", record_report_publish)
    original_health_write = self_check.atomic_write_text

    def record_health_publish(*args, **kwargs):
        record("health_publish")
        health_published.set()
        return original_health_write(*args, **kwargs)

    monkeypatch.setattr(self_check, "atomic_write_text", record_health_publish)
    original_unlock = derived_file_leases._unlock_handle

    def observe_unlock(handle: object) -> None:
        original_unlock(handle)
        if threading.current_thread().name == "queued-exclusive":
            record("exclusive_release")
            writer_released.set()

    monkeypatch.setattr(derived_file_leases, "_unlock_handle", observe_unlock)

    def writer() -> None:
        try:
            with manager.exclusive_erasure():
                record("exclusive_acquire")
                writer_acquired.set()
                if not allow_writer_release.wait(10):
                    raise TimeoutError("exclusive writer was not released")
        except BaseException as error:
            errors.append(error)

    def run_unrelated_report() -> None:
        try:
            with DietService(
                source_root=PROJECT_ROOT,
                plugin_config={"dataDir": str(service.data_paths.root)},
                env={},
            ) as unrelated:
                first_read_seen = False

                def observe_unrelated_read(statement: str) -> None:
                    nonlocal first_read_seen
                    if first_read_seen or not statement.lstrip().upper().startswith(
                        "SELECT"
                    ):
                        return
                    first_read_seen = True
                    record("unrelated_fact_read")
                    unrelated_fact_read.set()

                unrelated.connection.set_trace_callback(observe_unrelated_read)
                unrelated_started.set()
                reports.build_daily_report(
                    unrelated.connection,
                    unrelated.data_paths,
                    unrelated.settings,
                    date(2026, 7, 30),
                    templates_dir=unrelated.templates_dir,
                    lease_manager=manager_for(unrelated.data_paths),
                )
                unrelated.connection.set_trace_callback(None)
        except BaseException as error:
            errors.append(error)
        finally:
            unrelated_finished.set()

    before_acquires = manager._state.os_shared_acquire_count
    before_releases = manager._state.os_shared_release_count
    writer_thread = threading.Thread(target=writer, name="queued-exclusive")
    unrelated_thread = threading.Thread(
        target=run_unrelated_report,
        name="unrelated-report",
    )
    try:
        self_check.run_self_check(
            service.connection,
            service.data_paths,
            service.migrations_dir,
            source_root=service.source_root,
            now=datetime(2026, 7, 30, tzinfo=timezone.utc),
            write_report=True,
            lease_manager=recording_manager,
        )
        assert outer_paused.is_set()
        assert errors == []
        assert recording_manager.events.count("shared_reenter") == 3
        assert recording_manager.events.count("shared_reexit") == 3
        assert order.count("nested_publish") == 3
        assert health_published.is_set()
        assert manager._state.os_shared_acquire_count - before_acquires == 1
        assert manager._state.os_shared_release_count - before_releases == 1

        assert writer_acquired.wait(10)
        assert not unrelated_fact_read.wait(0.25)
        allow_writer_release.set()
        assert writer_released.wait(10)
        assert unrelated_fact_read.wait(10)
        assert unrelated_published.wait(10)
        assert unrelated_finished.wait(10)
        writer_thread.join(5)
        unrelated_thread.join(5)
        assert not writer_thread.is_alive()
        assert not unrelated_thread.is_alive()
        assert errors == []
        assert max(
            index for index, label in enumerate(order) if label == "nested_publish"
        ) < order.index("health_publish")
        assert order.index("health_publish") < order.index("exclusive_acquire")
        assert order.index("exclusive_acquire") < order.index("exclusive_release")
        assert order.index("exclusive_release") < order.index(
            "unrelated_fact_read"
        )
        assert order.index("unrelated_fact_read") < order.index(
            "unrelated_publish"
        )
    finally:
        allow_writer_release.set()
        for thread in (writer_thread, unrelated_thread):
            if thread.ident is not None:
                thread.join(5)


def _derived_writer_violations(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    tainted_parameters = {name: set() for name in functions}
    tainted_returns: set[str] = set()
    violations: set[str] = set()
    unsafe_methods = {"write_text", "write_bytes", "touch", "unlink", "replace"}
    derived_roots = {"cache", "exports", "reports"}

    def call_leaf(call: ast.Call) -> str | None:
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None

    def assigned_names(target: ast.expr) -> set[str]:
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, (ast.Tuple, ast.List)):
            return {
                name
                for element in target.elts
                for name in assigned_names(element)
            }
        return set()

    changed = True
    while changed:
        changed = False
        for function_name, function in functions.items():
            tainted = set(tainted_parameters[function_name])

            def expression_is_tainted(expression: ast.AST | None) -> bool:
                if expression is None:
                    return False
                if isinstance(expression, ast.Name):
                    return expression.id in tainted
                if isinstance(expression, ast.Attribute):
                    if (
                        expression.attr in derived_roots
                        and isinstance(expression.value, ast.Name)
                        and expression.value.id == "data_paths"
                    ):
                        return True
                    return expression_is_tainted(expression.value)
                if isinstance(expression, ast.BinOp):
                    return expression_is_tainted(
                        expression.left
                    ) or expression_is_tainted(expression.right)
                if isinstance(expression, ast.Subscript):
                    return expression_is_tainted(expression.value)
                if isinstance(expression, ast.Call):
                    leaf = call_leaf(expression)
                    if leaf in {"Path", "joinpath"}:
                        return any(
                            expression_is_tainted(argument)
                            for argument in expression.args
                        ) or (
                            isinstance(expression.func, ast.Attribute)
                            and expression_is_tainted(expression.func.value)
                        )
                    if leaf == "NamedTemporaryFile":
                        return any(
                            keyword.arg == "dir"
                            and expression_is_tainted(keyword.value)
                            for keyword in expression.keywords
                        )
                    if leaf in tainted_returns:
                        return True
                return False

            def inspect_call(call: ast.Call) -> None:
                nonlocal changed
                leaf = call_leaf(call)
                if isinstance(call.func, ast.Name) and leaf in functions:
                    callee = functions[leaf]
                    parameters = [argument.arg for argument in callee.args.args]
                    for index, argument in enumerate(call.args):
                        if index < len(parameters) and expression_is_tainted(argument):
                            parameter = parameters[index]
                            if parameter not in tainted_parameters[leaf]:
                                tainted_parameters[leaf].add(parameter)
                                changed = True
                    for keyword in call.keywords:
                        if (
                            keyword.arg in parameters
                            and expression_is_tainted(keyword.value)
                            and keyword.arg not in tainted_parameters[leaf]
                        ):
                            tainted_parameters[leaf].add(keyword.arg)
                            changed = True
                unsafe = False
                if (
                    leaf in {"unlink", "remove", "rename", "replace"}
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in {"os", "shutil"}
                ):
                    path_arguments = (
                        call.args[-1:]
                        if leaf in {"rename", "replace"}
                        else call.args[:1]
                    )
                    unsafe = any(
                        expression_is_tainted(item) for item in path_arguments
                    )
                elif leaf in unsafe_methods and isinstance(call.func, ast.Attribute):
                    unsafe = expression_is_tainted(call.func.value)
                    if not unsafe and call.args:
                        unsafe = expression_is_tainted(call.args[0])
                elif leaf == "open" and call.args:
                    unsafe = expression_is_tainted(call.args[0])
                elif leaf == "NamedTemporaryFile":
                    unsafe = any(
                        keyword.arg == "dir"
                        and expression_is_tainted(keyword.value)
                        for keyword in call.keywords
                    )
                elif leaf == "ZipFile" and call.args:
                    mode = next(
                        (
                            keyword.value.value
                            for keyword in call.keywords
                            if keyword.arg == "mode"
                            and isinstance(keyword.value, ast.Constant)
                        ),
                        None,
                    )
                    if mode is None and len(call.args) > 1 and isinstance(
                        call.args[1], ast.Constant
                    ):
                        mode = call.args[1].value
                    unsafe = (
                        isinstance(mode, str)
                        and any(flag in mode for flag in "wax")
                        and expression_is_tainted(call.args[0])
                    )
                if unsafe:
                    violations.add(
                        f"{filename}:{getattr(call, 'lineno', 0)}:{leaf}"
                    )

            def inspect_statements(statements: list[ast.stmt]) -> None:
                nonlocal changed
                for statement in statements:
                    for call in (
                        node for node in ast.walk(statement) if isinstance(node, ast.Call)
                    ):
                        inspect_call(call)
                    if isinstance(statement, ast.Assign):
                        if expression_is_tainted(statement.value):
                            for target in statement.targets:
                                tainted.update(assigned_names(target))
                    elif isinstance(statement, ast.AnnAssign):
                        if expression_is_tainted(statement.value):
                            tainted.update(assigned_names(statement.target))
                    elif isinstance(statement, (ast.With, ast.AsyncWith)):
                        for item in statement.items:
                            if expression_is_tainted(item.context_expr) and item.optional_vars:
                                tainted.update(assigned_names(item.optional_vars))
                        inspect_statements(statement.body)
                    elif isinstance(statement, ast.Return):
                        if expression_is_tainted(statement.value):
                            if function_name not in tainted_returns:
                                tainted_returns.add(function_name)
                                changed = True
                    elif isinstance(statement, (ast.If, ast.For, ast.While)):
                        inspect_statements(statement.body)
                        inspect_statements(statement.orelse)
                    elif isinstance(statement, ast.Try):
                        inspect_statements(statement.body)
                        for handler in statement.handlers:
                            inspect_statements(handler.body)
                        inspect_statements(statement.orelse)
                        inspect_statements(statement.finalbody)

            inspect_statements(function.body)
    return sorted(violations)


def test_derived_writer_scanner_rejects_positive_canary() -> None:
    canary = """
from pathlib import Path
import os
import tempfile

def derived_child(root):
    alias = root
    return alias.joinpath("canary.bin")

def forbidden_writer(data_paths):
    target = derived_child(data_paths.exports)
    alias = target
    with tempfile.NamedTemporaryFile(dir=alias.parent) as handle:
        temporary = Path(handle.name)
        open(temporary, "wb").write(b"canary")
    os.replace(Path("source.bin"), alias)
    Path.write_bytes(alias, b"canary")
"""
    with pytest.raises(AssertionError):
        assert _derived_writer_violations(canary, "positive_canary.py") == []


def test_derived_root_writers_cross_the_common_publisher_helpers() -> None:
    package = PROJECT_ROOT / "python" / "personal_diet_pantry"
    violations = [
        violation
        for path in package.glob("*.py")
        if path.name != "file_io.py"
        for violation in _derived_writer_violations(
            path.read_text(encoding="utf-8"),
            path.name,
        )
    ]
    assert violations == []

    for function in (
        data_export.export_data,
        reports._build_report,
        self_check.run_self_check,
    ):
        assert "with manager.shared_publisher" in inspect.getsource(function)
