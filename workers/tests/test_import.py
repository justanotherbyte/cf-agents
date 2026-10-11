def test_exports_the_names_workerd_imports() -> None:
    from workers import (  # noqa: F401
        DurableObject,
        WorkerEntrypoint,
        WorkflowEntrypoint,
        python_from_rpc,
        python_to_rpc,
    )
