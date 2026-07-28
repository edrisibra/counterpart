# counterpart

**Test your A2A agent against simulated counterparties — cooperative, broken, or hostile —
before you connect it to a real one.**

Multi-agent systems don't usually fail by crashing. They fail when one agent reports
`completed` and hands incomplete or corrupt work to the next one — no error, no stack trace,
just wrong output three hops downstream. counterpart lets you reproduce that in a test: spin
up a counterparty that misbehaves on purpose, point your agent at it, and assert on the
**result**, not just the protocol status code.

> **Status: v0, spec v1.0, API will change.** This is *pre-deployment testing* — it catches
> classes of failure before you ship. It is **not** production monitoring, and it is not an
> identity, trust, or runtime-governance layer. See [What it does and doesn't do](#what-it-does-and-doesnt-do).

## Quickstart

```bash
pip install counterpart        # or: uv add counterpart
```

That's the whole setup. No `conftest.py`, no ini options — installing the package registers
the pytest fixture and configures async tests for you.

The scary one first — a peer that **lies about success**:

```python
from pydantic import BaseModel
from counterpart import Contract


class Quote(BaseModel):
    price: float
    currency: str


async def test_my_agent_rejects_a_lying_peer(mock_agent):
    # Declare what a *correct* delegated result must look like.
    contract = (
        Contract("freight quote")
        .returns(Quote)                                        # must parse into this shape
        .require("price_is_number", lambda q: isinstance(q.price, (int, float)))
        .expect_status("completed")
    )

    # A counterparty that reports "completed" but returns garbage.
    peer = mock_agent(persona="false_success")
    async with peer.client() as client:
        task = await client.send_message("Quote 2 pallets LA->Dallas", contract=contract)

    assert task.status == "completed"     # the peer *claimed* success at the protocol level...
    assert task.contract_violated         # ...but the returned work does not hold up — caught.
```

`mock_agent` is a pytest fixture that ships with the package (no config). Wire that same
`contract` into your agent's delegation path and it rejects the garbage instead of forwarding
it downstream.

### One thing to know before you trust a contract

By default `.returns(Model)` uses pydantic's normal lax mode, so a peer sending
`{"price": "1420.00"}` — a **string** where your model says `float` — is coerced and
**passes**. Predicates then see a real float, so `isinstance(x, float)` cannot save you.

That is standard pydantic behaviour, but it is a trap here: you may think you are getting type
validation that you are not. If you need real type fidelity, ask for it:

```python
Contract("fare").returns(Fare, strict=True)   # a stringified number is now a failure
```

Lax stays the default on purpose — plenty of real services legitimately send numbers as
strings, and a contract that flags valid traffic gets switched off entirely, which is worse
than no contract. Use `strict=True` when you own both ends or the wire format is pinned.

## Persona gallery

Every persona is deterministic (no LLM), toggle-configurable, and drives a real A2A task
lifecycle. The **reliability tier** is where today's pain lives; the **security tier** is a
smaller, forward-looking set for teams integrating untrusted or third-party agents.

| Persona | What it simulates | What a correct agent does |
|---|---|---|
| `cooperative` | Accepts, works, completes with a well-formed result | Uses the result |
| `clarifier` | Flips to `input-required` once with a question, then completes | Answers, continues the task |
| **`false_success`** ⭐ | Reports `completed` but returns incomplete/corrupt output | **Detects it via a contract; does not forward garbage** |
| `resource_abuse` | Stalls or streams forever, never completing | Bounds its own time/spend and gives up |
| `flaky` | Drops the connection mid-exchange, then recovers | Retries transient failures |
| `over_sharing` | Asks for more context than the task needs | Refuses to leak unrelated context |

Security-tier personas (`prompt_injection`, `capability_lying`) are on the
[roadmap](docs/roadmap.md) as A2A's cross-boundary use grows.

Write your own by returning directives from a plain class — no DSL:

```python
from counterpart.core import Complete, Progress
from counterpart.personas import register

class HalfAnswer:
    def respond(self, turn, ctx):
        return [Progress("working"), Complete(result={"partial": True})]  # omits the real answer

register("half_answer", HalfAnswer)
```

## `wrap()` — expose your agent for testing in one line

```python
from counterpart import wrap

app = wrap(my_agent, name="quoting-agent", skills=["freight-quote"])  # an ASGI A2A server
# run it: uvicorn.run(app, ...)   — or drive it in-process with A2AClient(app=app)
```

## Worked examples

Four runnable scenarios in [`examples/`](examples), each in a domain where A2A is genuinely
warranted (the counterparty is operated by someone else) and each with a **different failure
shape** — because the shape of the failure determines the shape of the check:

| Example | Shape | What it shows |
|---|---|---|
| [`freight_procurement.py`](examples/freight_procurement.py) | N competing offers | A shipper agent collects carrier quotes and picks the cheapest valid one. The naive version books the "cheapest" carrier whose completed quote has `price: "call for rate"` and sends a non-numeric price to invoicing. |
| [`satellite_downlink.py`](examples/satellite_downlink.py) | units, time systems, reference frames | A mission-ops agent schedules a downlink pass with a ground-station network. 12 plans that all report `completed` while describing a different physical reality — a window in GPS time (18 s of leap seconds early), elevation in radians masquerading as degrees, west-positive longitude, an inertial frame where an earth-fixed one is needed, a month-old TLE, a negative link margin. This is the Mars Climate Orbiter failure class. |
| [`chaos_multihop.py`](examples/chaos_multihop.py) | 4 hops, real sockets, 20 concurrent users | Deliberately excessive: a delegation chain where each hop is a separate real HTTP server, auth passed through, corruption injected at the deepest hop. Corruption three hops away is still caught by the contract at hop 1. |
| [`prior_authorization.py`](examples/prior_authorization.py) | pipeline with cross-field consistency | A clinic's agent clears a procedure with a payer's eligibility and utilization-management agents. 25 modelled payer failures — all reporting `completed` — sourced from the X12 278 / FHIR Da Vinci PAS specs and practitioner forums, with each case labelled by how well it is attested. |

```bash
uv run python examples/prior_authorization.py
```

Every example measures two things, and the second matters more: every unusable answer is
caught, **and** legitimate counterparty variation is *not* flagged.

That second property is not theoretical. Researching the real X12 value sets for the prior-auth
example found three bugs in those contracts, and **every one was a false positive** — rejecting
a valid `A1` certification, rejecting a correctly-echoed member id, and mis-parsing a date so a
`MM/DD/YYYY` value silently *passed*. A later end-to-end chaos run found a fourth. None was a
missed catch; all four were the checker crying wolf. A contract that flags valid traffic gets
switched off in week two, and then you have no contract at all.

## `check` and `attack` a live agent

```bash
counterpart check  https://my-agent.example.com
counterpart attack https://my-agent.example.com --json   # for CI
```

`check` produces a scored conformance smoke report, every row citing the spec section it
verifies:

```
                   counterpart check — https://my-agent.example.com
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check                ┃ Result ┃ Spec §      ┃ Detail                       ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ agent_card_reachable │ PASS   │ 8.2         │ HTTP 200                     │
│ agent_card_valid     │ PASS   │ 4.4.1       │ name='...', 1 skill(s)       │
│ send_message         │ PASS   │ 9.4.1       │ got task                     │
│ streaming_honesty    │ PASS   │ 3.3.4       │ content-type: text/event-... │
│ method_not_found     │ PASS   │ 9.5         │ got error code -32601        │
│ task_not_found       │ PASS   │ 5.4         │ got error code -32001        │
└──────────────────────┴────────┴─────────────┴──────────────────────────────┘
Score: 9/9 checks passed — conformant
Smoke report only; run a2a-tck for the full matrix.
```

It's a fast dev-loop tool, not a certification suite — for the full conformance matrix, use
the official [a2a-tck](https://github.com/a2aproject/a2a-tck).

## What it does and doesn't do

**Does:** reproduce cross-boundary failure modes — a peer that lies about success, stalls,
drops, or over-asks — as deterministic pytest fixtures; verify the *result* of delegated work
against a contract, not just the protocol; smoke-check and probe a live A2A agent from CI.

**Doesn't:** monitor production (that's tracing/observability — counterpart is shift-left,
complementary); solve agent identity or trust infrastructure; issue certifications; mock LLM
APIs, MCP, or vector DBs. It complements ingress-focused tools like `agent-security-harness`
(which attacks *your* endpoint) by testing the other direction: the counterparty *your* agent
delegates to.

Honest scoping: A2A adoption is still early, and multi-agent architectures are often overkill.
counterpart is for the real case where you have genuine cross-boundary agents — and the core
engine is protocol-agnostic, so the contract-verification and persona machinery isn't locked
to A2A. See [docs/prior-art.md](docs/prior-art.md) for how this compares to what exists.

## Design

- [docs/spec-notes.md](docs/spec-notes.md) — how A2A v1.0 maps to the code (every claim cites
  the spec; verified against the pinned proto in [tests/data](tests/data)).
- [docs/prior-art.md](docs/prior-art.md) — what exists and the precise niche this fills.
- [docs/roadmap.md](docs/roadmap.md) — what's intentionally out of v0.

Architecture: a protocol-agnostic core (`counterpart.core` — lifecycle, contracts, personas)
with A2A as one adapter (`counterpart.adapters.a2a`). A test enforces that the core never
imports protocol code, so a second adapter is possible without touching the engine.

## License

Apache-2.0. See [LICENSE](LICENSE).
