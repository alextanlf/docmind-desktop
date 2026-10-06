"""Provider-declared notification delivery.

A credential channel can opt into "when this happens, tell me" without the
business module knowing anything about the target platform: the channel
declares a :class:`NotificationTarget` and the registry fans events out to
every registered target.

Why this lives beside the credential specs rather than in a ``notify.py``:
binding a Feishu bot webhook is a provider concern, so the provider owns the
delivery mechanics. Business code publishes a *platform-neutral* event
(:class:`NotificationEvent`) and never branches on a provider name.

Delivery is best-effort by contract. A notification failure must never fail the
operation that triggered it, so :func:`NotificationHub.notify` swallows
transport errors and only reports which targets were reached.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Severity of a notification. Kept as a plain string on the wire so a new
# channel can consume events without this module knowing its vocabulary.
NOTIFY_INFO = "info"
NOTIFY_SUCCESS = "success"
NOTIFY_ERROR = "error"


@dataclass(frozen=True)
class NotificationEvent:
    """A platform-neutral thing worth telling the user about.

    ``title`` is a short headline; ``body`` is one or two lines of detail. Both
    are already user-facing prose, so they must not embed provider or document
    internals that only make sense inside the app.
    """

    title: str
    body: str = ""
    severity: str = NOTIFY_INFO
    # Deduplication key. Two identical failures in one run should not produce
    # two messages, so callers pass a stable id (e.g. job id) rather than
    # relying on text equality.
    dedupe_key: str | None = None


@dataclass(frozen=True)
class NotificationTarget:
    """One destination plus the transport that reaches it.

    ``send`` is an async callable that either delivers the event or raises. It
    is supplied by the provider package, which is the only layer that knows
    the platform's payload shape.
    """

    name: str
    send: Callable[[NotificationEvent], Awaitable[None]]
    # Channels with a secret (a webhook URL, a token) must be re-checked
    # immediately before delivery, because the user can revoke the binding at
    # any time; a target whose credential went away is skipped silently.
    is_ready: Callable[[], bool] = lambda: True
    severities: frozenset[str] = field(
        default_factory=lambda: frozenset({NOTIFY_INFO, NOTIFY_SUCCESS, NOTIFY_ERROR})
    )


class NotificationHub:
    """Fan-out registry of notification targets.

    Holds no platform knowledge itself. Targets register themselves from their
    provider's ``register_*`` function, and business code only ever calls
    :meth:`notify`.
    """

    def __init__(self) -> None:
        self._targets: dict[str, NotificationTarget] = {}

    def register(self, target: NotificationTarget) -> None:
        self._targets[target.name] = target

    def unregister(self, name: str) -> None:
        self._targets.pop(name, None)

    def names(self) -> list[str]:
        return sorted(self._targets)

    def any_ready(self) -> bool:
        """Whether at least one target is currently deliverable.

        Callers use this to decide whether an event is worth publishing at all
        instead of paying a publish cost nobody can receive.
        """
        return any(self._safe_ready(target) for target in self._targets.values())

    async def notify(self, event: NotificationEvent) -> list[str]:
        """Deliver ``event`` to every willing target; return the reached names.

        Never raises: a broken notification target must not break the import or
        sync that produced the event.
        """
        reached: list[str] = []
        for name in self.names():
            target = self._targets[name]
            if event.severity not in target.severities:
                continue
            if not self._safe_ready(target):
                continue
            try:
                await target.send(event)
            except Exception:
                logger.warning(
                    "notification target %s failed for %r", name, event.title, exc_info=True
                )
                continue
            reached.append(name)
        return reached

    @staticmethod
    def _safe_ready(target: NotificationTarget) -> bool:
        try:
            return bool(target.is_ready())
        except Exception:  # noqa: BLE001 - a failing probe means "not ready"
            return False
