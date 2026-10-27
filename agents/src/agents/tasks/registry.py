"""Which Tasks capability an object's ``@task`` handles use.

``Tasks(target=obj)`` records itself here; the ``@task`` descriptor looks it
up when accessed on ``obj`` (``.design/scheduling_queue_tasks_api.md``
§2.11). Keys and values are weak: ``Tasks`` references its target, so a
strong value would keep the target alive. The target keeps its ``Tasks``
alive (``self.tasks``, or through its Lifecycle).
"""

import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tasks import Tasks

__all__ = ("register_target", "tasks_for")

_by_target: "weakref.WeakKeyDictionary[object, weakref.ref[Tasks]]" = (
    weakref.WeakKeyDictionary()
)


def register_target(target: object, tasks: "Tasks") -> None:
    """Record ``tasks`` as the capability for ``target``'s ``@task`` methods."""
    _by_target[target] = weakref.ref(tasks)


def tasks_for(target: object) -> "Tasks | None":
    """Return the Tasks capability created with ``target=target``, if any."""
    ref = _by_target.get(target)
    return ref() if ref is not None else None
