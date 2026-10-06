from __future__ import annotations

from app.remote.notifications import (
    NOTIFY_ERROR,
    NOTIFY_INFO,
    NOTIFY_SUCCESS,
    NotificationEvent,
    NotificationHub,
    NotificationTarget,
)


def _recording_target(
    name: str = "test",
    *,
    ready: bool = True,
    severities: frozenset[str] | None = None,
    fail_with: Exception | None = None,
    sink: list[NotificationEvent] | None = None,
) -> NotificationTarget:
    sink = sink if sink is not None else []

    async def _send(event: NotificationEvent) -> None:
        if fail_with is not None:
            raise fail_with
        sink.append(event)

    return NotificationTarget(
        name=name,
        send=_send,
        is_ready=lambda: ready,
        severities=severities
        if severities is not None
        else frozenset({NOTIFY_INFO, NOTIFY_SUCCESS, NOTIFY_ERROR}),
    )


class TestNotificationHubFanOut:
    async def test_delivers_to_every_registered_target(self) -> None:
        first: list[NotificationEvent] = []
        second: list[NotificationEvent] = []
        hub = NotificationHub()
        hub.register(_recording_target("a", sink=first))
        hub.register(_recording_target("b", sink=second))

        reached = await hub.notify(NotificationEvent(title="导入完成"))

        assert reached == ["a", "b"]
        assert [e.title for e in first] == ["导入完成"]
        assert [e.title for e in second] == ["导入完成"]

    async def test_unready_target_is_skipped_and_not_reported(self) -> None:
        """A revoked binding must stop deliveries without raising."""
        hub = NotificationHub()
        hub.register(_recording_target("bound", ready=False))

        reached = await hub.notify(NotificationEvent(title="导入完成"))

        assert reached == []

    async def test_severity_filter_keeps_a_target_opted_out(self) -> None:
        """Binding a bot must not imply wanting every severity."""
        errors_only: list[NotificationEvent] = []
        hub = NotificationHub()
        hub.register(
            _recording_target("errors", severities=frozenset({NOTIFY_ERROR}), sink=errors_only)
        )

        assert await hub.notify(NotificationEvent(title="导入完成", severity=NOTIFY_INFO)) == []
        assert len(errors_only) == 0
        assert await hub.notify(
            NotificationEvent(title="导入失败", severity=NOTIFY_ERROR)
        ) == ["errors"]

    async def test_one_failing_target_does_not_starve_the_others(self) -> None:
        """Delivery is best-effort: a broken bot must not break the import."""
        good: list[NotificationEvent] = []
        hub = NotificationHub()
        hub.register(_recording_target("broken", fail_with=RuntimeError("webhook down")))
        hub.register(_recording_target("good", sink=good))

        reached = await hub.notify(NotificationEvent(title="导入完成"))

        assert reached == ["good"]
        assert [e.title for e in good] == ["导入完成"]

    async def test_notify_never_raises_even_when_every_target_fails(self) -> None:
        hub = NotificationHub()
        hub.register(_recording_target("broken", fail_with=RuntimeError("down")))

        assert await hub.notify(NotificationEvent(title="导入完成")) == []

    async def test_a_raising_readiness_probe_reads_as_not_ready(self) -> None:
        def _explode() -> bool:
            raise RuntimeError("keychain locked")

        hub = NotificationHub()
        hub.register(
            NotificationTarget(name="flaky", send=_recording_target("x").send, is_ready=_explode)
        )

        assert hub.any_ready() is False
        assert await hub.notify(NotificationEvent(title="导入完成")) == []

    async def test_any_ready_is_false_when_nothing_is_bound(self) -> None:
        assert NotificationHub().any_ready() is False

    def test_registering_the_same_name_replaces_the_previous_target(self) -> None:
        hub = NotificationHub()
        hub.register(_recording_target("a"))
        hub.register(_recording_target("a"))

        assert hub.names() == ["a"]

    def test_unregister_removes_a_target(self) -> None:
        hub = NotificationHub()
        hub.register(_recording_target("a"))

        hub.unregister("a")
        hub.unregister("a")  # idempotent

        assert hub.names() == []

    async def test_event_carries_body_severity_and_dedupe_key(self) -> None:
        seen: list[NotificationEvent] = []
        hub = NotificationHub()
        hub.register(_recording_target("a", sink=seen))

        await hub.notify(
            NotificationEvent(
                title="文档导入完成",
                body="季度报告.pdf",
                severity=NOTIFY_SUCCESS,
                dedupe_key="import-done:job-1",
            )
        )

        event = seen[0]
        assert event.title == "文档导入完成"
        assert event.body == "季度报告.pdf"
        assert event.severity == NOTIFY_SUCCESS
        assert event.dedupe_key == "import-done:job-1"
