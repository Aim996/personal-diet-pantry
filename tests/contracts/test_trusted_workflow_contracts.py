from __future__ import annotations

from personal_diet_pantry import service as service_module
from personal_diet_pantry.service import DietService
from personal_diet_pantry.trusted_workflows import (
    Confirmation,
    DeleteDataCommand,
    ImportDataCommand,
    OperationReceipt,
    RequestContext,
    SelfCheckQuery,
    WorkflowPreview,
)


def test_delete_preview_and_commit_handlers_cross_trusted_workflow_seam(
    service: DietService,
    monkeypatch,
) -> None:
    observed: list[tuple[object, ...]] = []
    opaque_handle = "wfh_opaque_delete_preview"

    def recording_preview(command: object, request_context: object):
        observed.append((command, request_context))
        return WorkflowPreview(
            workflow_handle=opaque_handle,
            command="delete_data",
            summary={
                "preview": {
                    "scope": "all_business",
                    "date_start": None,
                    "date_end": None,
                    "affected_counts": {},
                    "backups_deleted": False,
                    "irreversible": True,
                },
                "requires_confirmation": True,
            },
            expires_at="2026-07-30T00:30:00Z",
        )

    def recording_commit(workflow_handle: object, confirmation: object):
        observed.append((workflow_handle, confirmation))
        return OperationReceipt(
            command="delete_data",
            effect_count=0,
            undo_policy="none",
            result={"deletion": {"scope": "all_business"}},
        )

    def direct_call_is_forbidden(*_args, **_kwargs):
        raise AssertionError("delete handler bypassed trusted workflow seam")

    monkeypatch.setattr(service.trusted_workflows, "preview", recording_preview)
    monkeypatch.setattr(service.trusted_workflows, "commit", recording_commit)
    monkeypatch.setattr(
        service_module.data_erasure, "build_plan", direct_call_is_forbidden
    )
    monkeypatch.setattr(
        service_module.data_erasure, "commit_plan", direct_call_is_forbidden
    )

    previewed = service.dispatch(
        {
            "domain": "system",
            "action": "preview_delete_data",
            "payload": {"scope": "all_business"},
        }
    )
    committed = service.dispatch(
        {
            "domain": "system",
            "action": "commit_delete_data",
            "payload": {
                "commit_handle": opaque_handle,
                "confirmed": True,
                "operation_key": "seam-delete",
            },
        }
    )

    assert previewed["ok"] is True
    assert committed["ok"] is True
    assert len(observed) == 2
    command, request_context = observed[0]
    workflow_handle, confirmation = observed[1]
    assert command == DeleteDataCommand(scope="all_business")
    assert isinstance(request_context, RequestContext)
    assert workflow_handle == opaque_handle
    assert confirmation == Confirmation(
        confirmed=True,
        operation_key="seam-delete",
    )


def test_trusted_workflow_module_has_four_public_operations(
    service: DietService,
) -> None:
    public = {
        name
        for name, value in vars(type(service.trusted_workflows)).items()
        if callable(value) and not name.startswith("_")
    }
    assert public == {"preview", "commit", "recover_startup", "inspect"}


def test_system_self_check_crosses_trusted_workflow_seam(
    service: DietService,
    monkeypatch,
) -> None:
    observed: list[object] = []
    original = service.trusted_workflows.inspect

    def recording_inspect(query: object):
        observed.append(query)
        return original(query)

    monkeypatch.setattr(
        service.trusted_workflows,
        "inspect",
        recording_inspect,
    )
    result = service.dispatch(
        {"domain": "system", "action": "self_check", "payload": {}}
    )

    assert result["ok"] is True
    assert len(observed) == 1
    assert isinstance(observed[0], SelfCheckQuery)


def test_validate_and_import_handlers_cross_trusted_workflow_seam(
    service: DietService,
    monkeypatch,
) -> None:
    observed: list[tuple[object, ...]] = []
    opaque_handle = "wfh_opaque_import_preview"

    def recording_preview(command: object, request_context: object):
        observed.append((command, request_context))
        return WorkflowPreview(
            workflow_handle=opaque_handle,
            command="import_data",
            summary={
                "validation": {
                    "valid": True,
                    "record_counts": {"meals": 1},
                }
            },
            expires_at="2026-07-30T00:30:00Z",
        )

    def recording_commit(workflow_handle: object, confirmation: object):
        observed.append((workflow_handle, confirmation))
        return OperationReceipt(
            command="import_data",
            effect_count=1,
            undo_policy="none",
            result={"import": {"record_counts": {"meals": 1}}},
        )

    def direct_call_is_forbidden(*_args, **_kwargs):
        raise AssertionError("import handler bypassed trusted workflow seam")

    monkeypatch.setattr(service.trusted_workflows, "preview", recording_preview)
    monkeypatch.setattr(service.trusted_workflows, "commit", recording_commit)
    monkeypatch.setattr(
        service_module.data_import,
        "load_and_validate",
        direct_call_is_forbidden,
    )
    monkeypatch.setattr(
        service_module.data_import,
        "commit_import",
        direct_call_is_forbidden,
    )

    validated = service.dispatch(
        {
            "domain": "system",
            "action": "validate_import",
            "payload": {"import_name": "portable.json"},
        }
    )
    committed = service.dispatch(
        {
            "domain": "system",
            "action": "import_data",
            "payload": {
                "commit_handle": opaque_handle,
                "confirmed": True,
                "operation_key": "seam-import",
            },
        }
    )

    assert validated["ok"] is True
    assert committed["ok"] is True
    assert len(observed) == 2
    command, request_context = observed[0]
    workflow_handle, confirmation = observed[1]
    assert command == ImportDataCommand(import_name="portable.json")
    assert isinstance(request_context, RequestContext)
    assert request_context.operation_key is None
    assert workflow_handle == opaque_handle
    assert confirmation == Confirmation(
        confirmed=True,
        operation_key="seam-import",
    )
