"""A2A client role: send a task to an A2A agent and get back a verifiable result.

Talks to any A2A v1.0 JSON-RPC agent — a real one over the network (``base_url``) or an
in-process one over ASGI (``app``, no socket, for fast/deterministic tests). Returns a
:class:`TaskResult` that carries the peer's self-reported status next to the extracted
result, and can verify both against a :class:`~a2a_sandbox.core.contract.Contract`.

v0 routes JSON-RPC to ``{base}/`` and fetches the card from the well-known path; honouring
the card's declared interface URL for routing is the conformance checker's job (roadmap).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from a2a_sandbox.adapters.a2a.constants import (
    A2A_VERSION_HEADER,
    MEDIA_TYPE_JSON,
    PROTOCOL_VERSION,
    WELL_KNOWN_AGENT_CARD_PATH,
    A2AMethod,
)
from a2a_sandbox.adapters.a2a.types import (
    AgentCard,
    Artifact,
    JSONRPCRequest,
    JSONRPCSuccessResponse,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
)
from a2a_sandbox.core.contract import Contract, ContractReport


def _extract_result(task: Task) -> Any:
    """The delegated result to verify: the latest artifact's data, else its text.

    Prefers a structured ``data`` part over ``text`` regardless of part order (a contract's
    ``.returns(Model)`` wants the structured payload), then falls back to text.
    """
    if not task.artifacts:
        return None
    parts = task.artifacts[-1].parts
    for part in parts:
        if part.data is not None:
            return part.data
    for part in parts:
        if part.text is not None:
            return part.text
    return None


@dataclass
class TaskResult:
    """The outcome of sending a task: the raw task, the states seen, and any contract report."""

    task: Task
    states: list[str] = field(default_factory=list)  # friendly aliases, in order
    report: ContractReport[Any] | None = None

    @property
    def status(self) -> str:
        """The peer's final self-reported status, as a friendly alias (e.g. ``"completed"``)."""
        return self.task.status.state.alias

    @property
    def result(self) -> Any:
        return _extract_result(self.task)

    @property
    def artifacts(self) -> list[Artifact]:
        return self.task.artifacts or []

    @property
    def completed(self) -> bool:
        return self.task.status.state is TaskState.COMPLETED

    @property
    def contract_violated(self) -> bool:
        """True if a contract was supplied and the delegated result did not satisfy it."""
        return self.report is not None and self.report.contract_violated

    def reached_state(self, state: str) -> bool:
        return TaskState.coerce(state).alias in self.states


class A2AClient:
    """An async A2A JSON-RPC client. Use ``base_url`` for a real agent, ``app`` for ASGI."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        app: Any = None,
        timeout: float = 30.0,
        protocol_version: str = PROTOCOL_VERSION,
    ) -> None:
        if (base_url is None) == (app is None):
            raise ValueError("provide exactly one of base_url or app")
        self._protocol_version = protocol_version
        if app is not None:
            self._base = "http://mock.local"
            transport: httpx.AsyncBaseTransport = httpx.ASGITransport(app=app)
            self._http = httpx.AsyncClient(
                transport=transport, base_url=self._base, timeout=timeout
            )
        else:
            self._base = base_url.rstrip("/")  # type: ignore[union-attr]
            self._http = httpx.AsyncClient(base_url=self._base, timeout=timeout)

    async def __aenter__(self) -> A2AClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        return {A2A_VERSION_HEADER: self._protocol_version, "content-type": MEDIA_TYPE_JSON}

    async def resolve_card(self) -> AgentCard:
        """Fetch and validate the counterparty's Agent Card (spec section 8.2)."""
        resp = await self._http.get(WELL_KNOWN_AGENT_CARD_PATH, headers=self._headers())
        resp.raise_for_status()
        return AgentCard.from_wire(resp.content)

    def _build_message(
        self, text: str, *, data: Any, task_id: str | None, context_id: str | None
    ) -> Message:
        parts = [Part(text=text)] if text else []
        if data is not None:
            parts.append(Part(data=data))
        if not parts:
            parts = [Part(text="")]
        return Message(
            message_id=f"msg-{uuid.uuid4().hex}",
            role=Role.USER,
            parts=parts,
            task_id=task_id,
            context_id=context_id,
        )

    async def send_message(
        self,
        text: str,
        *,
        data: Any = None,
        task_id: str | None = None,
        context_id: str | None = None,
        stream: bool = False,
        contract: Contract[Any] | None = None,
    ) -> TaskResult:
        """Send a task (or a follow-up, if ``task_id`` is set) and return the result."""
        message = self._build_message(text, data=data, task_id=task_id, context_id=context_id)
        request = SendMessageRequest(
            message=message,
            configuration=SendMessageConfiguration(accepted_output_modes=["application/json"]),
        )
        if stream:
            result = await self._send_streaming(request)
        else:
            result = await self._send_blocking(request)
        if contract is not None:
            result.report = contract.verify(result=result.result, reported_status=result.status)
        return result

    async def reply(
        self, task_id: str, text: str, *, context_id: str | None = None, **kw: Any
    ) -> TaskResult:
        """Continue an interrupted task (e.g. answer an input-required question, spec 3.4.3)."""
        return await self.send_message(text, task_id=task_id, context_id=context_id, **kw)

    async def _send_blocking(self, request: SendMessageRequest) -> TaskResult:
        rpc = JSONRPCRequest(method=A2AMethod.SEND_MESSAGE.value, id=1, params=request.to_wire())
        resp = await self._http.post("/", content=rpc.to_wire_json(), headers=self._headers())
        resp.raise_for_status()
        body = resp.json()
        # JSON-RPC errors arrive over HTTP 200, so surface them instead of dropping them.
        if isinstance(body, dict) and body.get("error") is not None:
            err = body["error"]
            raise A2AProtocolError(
                f"peer returned JSON-RPC error {err.get('code')}: {err.get('message')}"
            )
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict) or "task" not in result:
            raise A2AProtocolError(f"expected a task result, got: {result!r}")
        task = Task.from_wire(result["task"])
        return TaskResult(task=task, states=[task.status.state.alias])

    async def _send_streaming(self, request: SendMessageRequest) -> TaskResult:
        rpc = JSONRPCRequest(
            method=A2AMethod.SEND_STREAMING_MESSAGE.value, id=1, params=request.to_wire()
        )
        task: Task | None = None
        states: list[str] = []
        # Accumulate artifacts by id so chunked updates (append=true) are reassembled
        # rather than duplicated (spec 4.2.2).
        artifacts: dict[str, Artifact] = {}
        async with self._http.stream(
            "POST", "/", content=rpc.to_wire_json(), headers=self._headers()
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                envelope = JSONRPCSuccessResponse.from_wire(line[len("data:") :].strip())
                event = StreamResponse.from_wire(envelope.result)
                if event.task is not None:
                    task = event.task
                    states.append(event.task.status.state.alias)
                elif event.status_update is not None:
                    task = _apply_status(task, event.status_update)
                    states.append(event.status_update.status.state.alias)
                elif event.artifact_update is not None:
                    _accumulate_artifact(artifacts, event.artifact_update)
        if task is None:
            raise A2AProtocolError("stream produced no task")
        if artifacts:
            task = task.model_copy(update={"artifacts": list(artifacts.values())})
        return TaskResult(task=task, states=states)


class A2AProtocolError(Exception):
    """The peer's response did not conform to what the A2A method promises."""


def _apply_status(task: Task | None, event: Any) -> Task:
    if task is None:
        raise A2AProtocolError("status update arrived before the initial task event")
    return task.model_copy(update={"status": event.status})


def _accumulate_artifact(artifacts: dict[str, Artifact], event: Any) -> None:
    """Merge a TaskArtifactUpdateEvent into the id-keyed accumulator (spec 4.2.2).

    ``append=true`` extends the parts of an already-seen artifact with the same id;
    otherwise the artifact replaces (or first establishes) that id.
    """
    art = event.artifact
    if event.append and art.artifact_id in artifacts:
        existing = artifacts[art.artifact_id]
        artifacts[art.artifact_id] = existing.model_copy(
            update={"parts": [*existing.parts, *art.parts]}
        )
    else:
        artifacts[art.artifact_id] = art


def collect_states(results: Sequence[TaskResult]) -> list[str]:
    """Small helper: flatten the states seen across several results (for assertions)."""
    return [s for r in results for s in r.states]
