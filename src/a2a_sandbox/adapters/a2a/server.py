"""A2A MockAgent server: a persona-driven, spec-v1.0 A2A agent over JSON-RPC + SSE.

This is the A2A adapter's server role. It renders a protocol-agnostic persona (a
``core.Behaviour`` emitting directives) onto the A2A task lifecycle: it serves an Agent
Card at the well-known path, accepts JSON-RPC 2.0 requests, walks tasks through
``submitted → working → input-required → completed/failed/canceled``, returns artifacts,
and streams status/artifact events over SSE.

v0 implements: ``SendMessage`` (blocking + returnImmediately), ``SendStreamingMessage``
(SSE), ``GetTask``, ``CancelTask``. ``SubscribeToTask`` and the push-config methods return
``UnsupportedOperationError`` for now (roadmap). All wire shapes come from
``a2a_sandbox.adapters.a2a.types``, verified against the normative proto.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from a2a_sandbox.adapters.a2a.constants import (
    A2A_VERSION_HEADER,
    MEDIA_TYPE_JSON,
    MEDIA_TYPE_SSE,
    WELL_KNOWN_AGENT_CARD_PATH,
    A2AErrorCode,
    A2AMethod,
)
from a2a_sandbox.adapters.a2a.types import (
    AgentCard,
    Artifact,
    JSONRPCError,
    JSONRPCErrorResponse,
    JSONRPCRequest,
    JSONRPCSuccessResponse,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a_sandbox.core.behaviour import (
    Behaviour,
    Complete,
    Deliver,
    Directive,
    Drop,
    EmitRawStatus,
    Fail,
    NeedInput,
    Progress,
    SessionContext,
    Turn,
    Wait,
)
from a2a_sandbox.core.lifecycle import Lifecycle, LifecycleSpec

# The A2A task lifecycle expressed for the protocol-agnostic core (spec section 4.1.3).
A2A_LIFECYCLE = LifecycleSpec(
    states=frozenset(s.value for s in TaskState),
    initial=TaskState.SUBMITTED.value,
    terminal=frozenset(
        s.value
        for s in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED)
    ),
    interrupted=frozenset(s.value for s in (TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED)),
)


class DropConnection(Exception):
    """Raised by the Drop directive to sever the exchange mid-flight."""


@dataclass
class _TaskRecord:
    """Server-side state for one task: its wire Task, lifecycle, and persona session."""

    task: Task
    lifecycle: Lifecycle
    behaviour: Behaviour
    ctx: SessionContext = field(default_factory=SessionContext)
    turn_index: int = 0


BehaviourFactory = Callable[[], Behaviour]


def _artifact_from_result(result: Any, name: str | None) -> Artifact:
    part = Part(text=result) if isinstance(result, str) else Part(data=result)
    return Artifact(artifact_id=f"artifact-{uuid.uuid4().hex[:8]}", name=name, parts=[part])


def _turn_from_message(message: Message, index: int) -> Turn:
    text = " ".join(p.text for p in message.parts if p.text is not None)
    data = next((p.data for p in message.parts if p.data is not None), None)
    return Turn(text=text, data=data, index=index, raw=message)


class A2AServer:
    """Renders a persona onto the A2A protocol. Build the ASGI app with :meth:`build_app`."""

    def __init__(self, behaviour_factory: BehaviourFactory, *, card: AgentCard) -> None:
        self._factory = behaviour_factory
        self.card = card
        self._tasks: dict[str, _TaskRecord] = {}
        self._bg: set[asyncio.Task[None]] = set()
        # Observability: every inbound JSON-RPC request, for assertions in tests.
        self.received_requests: list[JSONRPCRequest] = []

    # -- app wiring --------------------------------------------------------

    def build_app(self) -> Starlette:
        return Starlette(
            routes=[
                Route(WELL_KNOWN_AGENT_CARD_PATH, self._serve_card, methods=["GET"]),
                Route("/", self._handle_rpc, methods=["POST"]),
            ]
        )

    async def _serve_card(self, request: Request) -> Response:
        return JSONResponse(self.card.to_wire())

    async def _handle_rpc(self, request: Request) -> Response:
        try:
            body = await request.body()
            rpc = JSONRPCRequest.from_wire(body)
        except Exception:
            return self._error(None, A2AErrorCode.JSON_PARSE_ERROR)
        self.received_requests.append(rpc)

        handler = {
            A2AMethod.SEND_MESSAGE.value: self._send_message,
            A2AMethod.SEND_STREAMING_MESSAGE.value: self._send_streaming_message,
            A2AMethod.GET_TASK.value: self._get_task,
            A2AMethod.CANCEL_TASK.value: self._cancel_task,
        }.get(rpc.method)
        if handler is None:
            code = (
                A2AErrorCode.UNSUPPORTED_OPERATION
                if rpc.method in {m.value for m in A2AMethod}
                else A2AErrorCode.METHOD_NOT_FOUND
            )
            return self._error(rpc.id, code)
        return await handler(rpc)

    # -- method handlers ---------------------------------------------------

    async def _send_message(self, rpc: JSONRPCRequest) -> Response:
        try:
            params = SendMessageRequest.model_validate(rpc.params or {})
        except Exception:
            return self._error(rpc.id, A2AErrorCode.INVALID_PARAMS)

        resolved = self._resolve_task(params.message)
        if isinstance(resolved, A2AErrorCode):
            return self._error(rpc.id, resolved)
        record = resolved

        turn = _turn_from_message(params.message, record.turn_index)
        record.turn_index += 1
        directives = await self._respond(record, turn)

        non_blocking = bool(params.configuration and params.configuration.return_immediately)
        if non_blocking:
            self._spawn(self._run_directives(record, directives))
            return self._ok(rpc.id, {"task": record.task.to_wire()})

        # Blocking (spec 3.2.2): process the turn's directives to completion, then return the
        # task in whatever state they left it (terminal, interrupted, or still working). A
        # persona scopes its own per-turn directives, so it stops itself at the right state.
        try:
            async for _ in self._apply_all(record, directives):
                pass
        except DropConnection:
            return self._error(rpc.id, A2AErrorCode.INTERNAL_ERROR, "connection dropped")
        return self._ok(rpc.id, {"task": record.task.to_wire()})

    async def _send_streaming_message(self, rpc: JSONRPCRequest) -> Response:
        if not (self.card.capabilities.streaming or False):
            return self._error(rpc.id, A2AErrorCode.UNSUPPORTED_OPERATION)
        try:
            params = SendMessageRequest.model_validate(rpc.params or {})
        except Exception:
            return self._error(rpc.id, A2AErrorCode.INVALID_PARAMS)
        resolved = self._resolve_task(params.message)
        if isinstance(resolved, A2AErrorCode):
            return self._error(rpc.id, resolved)
        record = resolved

        turn = _turn_from_message(params.message, record.turn_index)
        record.turn_index += 1
        directives = await self._respond(record, turn)

        async def event_stream() -> AsyncIterator[str]:
            # Spec 3.1.2: a task-lifecycle stream begins with the Task object.
            yield self._sse(rpc.id, StreamResponse(task=record.task))
            try:
                async for event in self._apply_all(record, directives):
                    yield self._sse(rpc.id, event)
            except DropConnection:
                return  # sever the stream mid-flight

        return StreamingResponse(event_stream(), media_type=MEDIA_TYPE_SSE)

    async def _get_task(self, rpc: JSONRPCRequest) -> Response:
        task_id = (rpc.params or {}).get("id")
        record = self._tasks.get(task_id) if isinstance(task_id, str) else None
        if record is None:
            return self._error(rpc.id, A2AErrorCode.TASK_NOT_FOUND)
        return self._ok(rpc.id, {"task": record.task.to_wire()})

    async def _cancel_task(self, rpc: JSONRPCRequest) -> Response:
        task_id = (rpc.params or {}).get("id")
        record = self._tasks.get(task_id) if isinstance(task_id, str) else None
        if record is None:
            return self._error(rpc.id, A2AErrorCode.TASK_NOT_FOUND)
        if record.lifecycle.is_terminal:
            return self._error(rpc.id, A2AErrorCode.TASK_NOT_CANCELABLE)
        self._set_state(record, TaskState.CANCELED)
        return self._ok(rpc.id, {"task": record.task.to_wire()})

    # -- task + persona plumbing ------------------------------------------

    async def _respond(self, record: _TaskRecord, turn: Turn) -> list[Directive]:
        """Call the persona, awaiting it if it's an async behaviour (e.g. a wrapped agent)."""
        result = record.behaviour.respond(turn, record.ctx)
        if inspect.isawaitable(result):
            result = await result
        return list(result)

    def _resolve_task(self, message: Message) -> _TaskRecord | A2AErrorCode:
        """Look up an existing task (follow-up) or create a new one (server-generated ids).

        Returns the record, or an error code the caller renders as a JSON-RPC error.
        """
        if message.task_id is not None:
            record = self._tasks.get(message.task_id)
            if record is None:
                return A2AErrorCode.TASK_NOT_FOUND  # spec 3.4.2
            if record.lifecycle.is_terminal:
                return A2AErrorCode.UNSUPPORTED_OPERATION  # spec 3.1.1: terminal task
            return record
        return self._new_task(message.context_id)

    def _new_task(self, context_id: str | None) -> _TaskRecord:
        task_id = f"task-{uuid.uuid4().hex}"
        ctx_id = context_id or f"ctx-{uuid.uuid4().hex}"  # spec 3.4.1: server includes contextId
        lifecycle = Lifecycle(A2A_LIFECYCLE)
        task = Task(
            id=task_id,
            context_id=ctx_id,
            status=TaskStatus(state=TaskState.SUBMITTED),
        )
        record = _TaskRecord(task=task, lifecycle=lifecycle, behaviour=self._factory())
        self._tasks[task_id] = record
        return record

    async def _run_directives(self, record: _TaskRecord, directives: Iterable[Directive]) -> None:
        try:
            async for _ in self._apply_all(record, directives):
                pass
        except DropConnection:
            pass

    async def _apply_all(
        self, record: _TaskRecord, directives: Iterable[Directive]
    ) -> AsyncIterator[StreamResponse]:
        for directive in directives:
            async for event in self._apply(record, directive):
                yield event

    async def _apply(self, record: _TaskRecord, d: Directive) -> AsyncIterator[StreamResponse]:
        if isinstance(d, Wait):
            await asyncio.sleep(d.seconds)
            return
        if isinstance(d, Drop):
            raise DropConnection
        if isinstance(d, Progress):
            self._set_state(record, TaskState.WORKING, agent_text=d.message or None)
            yield self._status_event(record)
            return
        if isinstance(d, NeedInput):
            self._set_state(record, TaskState.INPUT_REQUIRED, agent_text=d.question)
            yield self._status_event(record)
            return
        if isinstance(d, Deliver):
            self._add_artifact(record, d.result, d.name)
            yield self._artifact_event(record)
            return
        if isinstance(d, Complete):
            if d.result is not None:
                self._add_artifact(record, d.result, d.name)
                yield self._artifact_event(record)
            self._set_state(record, TaskState.COMPLETED)
            yield self._status_event(record)
            return
        if isinstance(d, Fail):
            self._set_state(record, TaskState.FAILED, agent_text=d.reason or None)
            yield self._status_event(record)
            return
        if isinstance(d, EmitRawStatus):
            # v0: only valid states; an arbitrary/illegal raw wire status is a roadmap item.
            state = TaskState.coerce(d.status)
            self._set_state(record, state, agent_text=d.message, force=d.force)
            yield self._status_event(record)
            return
        raise NotImplementedError(f"unhandled directive: {type(d).__name__}")

    def _set_state(
        self,
        record: _TaskRecord,
        state: TaskState,
        *,
        agent_text: str | None = None,
        force: bool = False,
    ) -> None:
        if record.lifecycle.state != state.value or force:
            record.lifecycle.transition_to(state.value, force=force)
        message = None
        if agent_text is not None:
            message = Message(
                message_id=f"msg-{uuid.uuid4().hex[:8]}",
                role=Role.AGENT,
                parts=[Part(text=agent_text)],
                task_id=record.task.id,
                context_id=record.task.context_id,
            )
        record.task.status = TaskStatus(state=state, message=message)

    def _add_artifact(self, record: _TaskRecord, result: Any, name: str | None) -> None:
        artifact = _artifact_from_result(result, name)
        record.task.artifacts = [*(record.task.artifacts or []), artifact]

    def _status_event(self, record: _TaskRecord) -> StreamResponse:
        return StreamResponse(
            status_update=TaskStatusUpdateEvent(
                task_id=record.task.id,
                context_id=record.task.context_id or "",
                status=record.task.status,
            )
        )

    def _artifact_event(self, record: _TaskRecord) -> StreamResponse:
        from a2a_sandbox.adapters.a2a.types import TaskArtifactUpdateEvent

        latest = (record.task.artifacts or [])[-1]
        return StreamResponse(
            artifact_update=TaskArtifactUpdateEvent(
                task_id=record.task.id,
                context_id=record.task.context_id or "",
                artifact=latest,
                last_chunk=True,
            )
        )

    # -- background task tracking -----------------------------------------

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # -- response helpers --------------------------------------------------

    def _ok(self, rpc_id: Any, result: Any) -> Response:
        body = JSONRPCSuccessResponse(id=rpc_id, result=result).to_wire()
        return JSONResponse(body, media_type=MEDIA_TYPE_JSON)

    def _error(self, rpc_id: Any, code: A2AErrorCode, message: str | None = None) -> Response:
        from a2a_sandbox.adapters.a2a.constants import ERROR_NAMES, STANDARD_ERROR_MESSAGES

        text = message or STANDARD_ERROR_MESSAGES.get(code) or ERROR_NAMES[code]
        body = JSONRPCErrorResponse(
            id=rpc_id, error=JSONRPCError(code=int(code), message=text)
        ).to_wire()
        return JSONResponse(body, media_type=MEDIA_TYPE_JSON)

    def _sse(self, rpc_id: Any, event: StreamResponse) -> str:
        # Spec 9.4.2: each SSE data field is a full JSON-RPC response whose result is a
        # StreamResponse.
        envelope = JSONRPCSuccessResponse(id=rpc_id, result=event.to_wire()).to_wire()
        return f"data: {json.dumps(envelope)}\n\n"

    def version_header(self, request: Request) -> str | None:
        return request.headers.get(A2A_VERSION_HEADER)
