from __future__ import annotations


async def test_markdown_import_then_cited_question(test_app, staged_markdown):
    repo = await test_app.create_repository("SwiftUI")
    preview = await test_app.inspect_staged(staged_markdown)
    job = await test_app.create_import(preview, repo.id)
    await test_app.wait_for_import(job.id)
    session = await test_app.create_session([repo.id])
    events = await test_app.ask(session.id, "@State 有什么作用？")
    assert any(event.type == "citations" for event in events)
    assert "[S1]" in "".join(event.payload.get("content", "") for event in events)
