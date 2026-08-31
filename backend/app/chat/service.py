from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from app.api.errors import DomainError
from app.chat.citations import URLStreamSanitizer, parse_citations
from app.chat.prompts import build_rag_prompt
from app.core.llm import ChatRequest, LLMMessage, LLMProvider
from app.core.retrieval import HybridRetriever
from app.imports.events import EventEnvelope, EventType, ImportEventBroker
from app.schemas.chat import ChatStreamRequest, CitationRef
from app.schemas.retrieval import RetrievalHit
from app.storage.models import MessageRecord
from app.storage.repositories import ConversationStore

_GAP_ANSWER = "当前文档未覆盖该问题，无法基于现有资料作答。"


class ChatService:
    def __init__(
        self,
        *,
        retriever: HybridRetriever,
        llm: LLMProvider,
        conversation_store: ConversationStore,
        event_broker: ImportEventBroker,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.conversation_store = conversation_store
        self.event_broker = event_broker
        self.tasks: set[asyncio.Task[None]] = set()
        self.task_errors: list[BaseException] = []
        self._started_request_ids: set[str] = set()
        self._last_sequences: dict[str, int] = {}
        self._fallback_terminals: dict[str, EventEnvelope] = {}
        self._fallback_ready: dict[str, asyncio.Event] = {}
        self._start_lock = asyncio.Lock()
        self._stopped = False

    async def stream(self, request: ChatStreamRequest) -> AsyncIterator[EventEnvelope]:
        key = str(request.request_id)
        await self._ensure_producer(key, request)
        subscription = self.event_broker.subscribe(key, 0)
        next_event = asyncio.create_task(anext(subscription))
        cursor = 0
        try:
            while True:
                fallback = self._fallback_terminals.get(key)
                if fallback is not None and cursor >= fallback.sequence - 1:
                    next_event.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await next_event
                    await asyncio.sleep(0)
                    yield fallback
                    return

                if fallback is None:
                    fallback_ready = asyncio.create_task(
                        self._fallback_ready[key].wait()
                    )
                    completed, _ = await asyncio.wait(
                        {next_event, fallback_ready},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if next_event not in completed:
                        continue
                    fallback_ready.cancel()
                    with suppress(asyncio.CancelledError):
                        await fallback_ready

                try:
                    event = await next_event
                except StopAsyncIteration:
                    return
                cursor = event.sequence
                yield event.model_copy(update={"request_id": request.request_id})
                if event.type in {"done", "error"}:
                    return
                next_event = asyncio.create_task(anext(subscription))
        finally:
            if not next_event.done():
                next_event.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await next_event
            with suppress(RuntimeError):
                await subscription.aclose()

    async def wait_for_idle(self) -> None:
        while self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    async def stop(self) -> None:
        self._stopped = True
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _ensure_producer(self, key: str, request: ChatStreamRequest) -> None:
        async with self._start_lock:
            if key in self._started_request_ids:
                return
            if self._stopped:
                raise RuntimeError("chat service is stopped")
            self._started_request_ids.add(key)
            self._fallback_ready[key] = asyncio.Event()
            task = asyncio.create_task(self._produce(key, request))
            self.tasks.add(task)
            task.add_done_callback(self._producer_finished)

    def _producer_finished(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self.task_errors.append(error)

    async def _produce(self, key: str, request: ChatStreamRequest) -> None:
        answer_parts: list[str] = []
        user_persisted = False
        assistant_persistence_attempted = False
        try:
            user_message = self.conversation_store.add_message(
                MessageRecord(
                    session_id=request.session_id,
                    role="user",
                    content=request.message,
                    generation_status="completed",
                )
            )
            user_persisted = True
            history = self.conversation_store.list_messages(request.session_id)[-20:]
            result = await self.retriever.search(
                request.message, request.repository_ids, top_k=5
            )
            sources = _source_map(result.hits)
            await self._publish(
                key,
                "citations",
                {
                    "citations": [
                        source.model_dump(by_alias=True) for source in sources.values()
                    ]
                },
            )
            if not sources:
                assistant_persistence_attempted = True
                assistant = self._persist_assistant(
                    request.session_id, _GAP_ANSWER, [], "completed"
                )
                await self._publish(key, "delta", {"content": _GAP_ANSWER})
                await self._publish(key, "done", {"messageId": assistant.id})
                return

            chat_request = ChatRequest(
                messages=_history_with_prompt(
                    history, user_message.id, request.message, result.hits
                )
            )
            sanitizer = URLStreamSanitizer()
            async for delta in self.llm.stream_chat(chat_request):
                sanitized_delta = sanitizer.feed(delta.content)
                if sanitized_delta:
                    answer_parts.append(sanitized_delta)
                    await self._publish(key, "delta", {"content": sanitized_delta})
            final_delta = sanitizer.finish()
            if final_delta:
                answer_parts.append(final_delta)
                await self._publish(key, "delta", {"content": final_delta})

            answer = "".join(answer_parts)
            citations = parse_citations(answer, sources)
            assistant_persistence_attempted = True
            assistant = self._persist_assistant(
                request.session_id, answer, citations, "completed"
            )
            await self._publish(key, "done", {"messageId": assistant.id})
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - producer owns the operation error boundary
            details = _error_details(error)
            if user_persisted and not assistant_persistence_attempted:
                try:
                    self._persist_assistant(
                        request.session_id,
                        "".join(answer_parts),
                        [],
                        "error",
                    )
                except Exception:  # noqa: BLE001, S110 - terminal must survive storage failure
                    pass
            try:
                await self._publish(key, "error", details)
            except Exception:
                fallback = EventEnvelope(
                    request_id=request.request_id,
                    type="error",
                    sequence=self._last_sequences.get(key, 0) + 1,
                    payload=details,
                )
                self._fallback_terminals[key] = fallback
                self._fallback_ready[key].set()
                raise

    def _persist_assistant(
        self,
        session_id: str,
        content: str,
        citations: list[CitationRef],
        generation_status: str,
    ) -> MessageRecord:
        return self.conversation_store.add_message(
            MessageRecord(
                session_id=session_id,
                role="assistant",
                content=content,
                citations_json=json.dumps(
                    [citation.model_dump(by_alias=True) for citation in citations],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                generation_status=generation_status,
            )
        )

    async def _publish(
        self, key: str, event_type: EventType, payload: dict[str, Any]
    ) -> None:
        event = await self.event_broker.publish(key, event_type, payload)
        self._last_sequences[key] = event.sequence


def _source_map(hits: list[RetrievalHit]) -> dict[str, CitationRef]:
    return {
        f"S{index}": CitationRef(
            source_id=f"S{index}",
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            title=hit.document_title,
            section_path=hit.section_path,
            page_number=hit.page_number,
            excerpt=hit.text,
            source_url=hit.source_url,
        )
        for index, hit in enumerate(hits[:5], start=1)
    }


def _history_with_prompt(
    history: list[MessageRecord],
    current_message_id: str,
    query: str,
    hits: list[RetrievalHit],
) -> list[LLMMessage]:
    prompt = build_rag_prompt(query, hits)
    messages = [LLMMessage(role=message.role, content=message.content) for message in history]
    for index in range(len(history) - 1, -1, -1):
        if history[index].id == current_message_id:
            messages[index] = LLMMessage(role="user", content=prompt)
            break
    else:
        messages.append(LLMMessage(role="user", content=prompt))
    return messages[-20:]


def _error_details(error: Exception) -> dict[str, Any]:
    if isinstance(error, DomainError):
        return {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        }
    return {
        "code": "CHAT_GENERATION_FAILED",
        "message": "回答生成失败，请稍后重试",
        "retryable": True,
    }
