# counterpart

[![CI](https://github.com/edrisibra/counterpart/actions/workflows/ci.yml/badge.svg)](https://github.com/edrisibra/counterpart/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/counterpart.svg)](https://pypi.org/project/counterpart/)
[![Python](https://img.shields.io/pypi/pyversions/counterpart.svg)](https://pypi.org/project/counterpart/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Test your agent against a peer that lies about finishing.

When your agent delegates work over [A2A](https://a2a-protocol.org/), a task reaching
`completed` tells you the other agent stopped working. It does not tell you the work is any
good. counterpart gives you counterparties that misbehave on purpose, and a way to check what
they actually returned.

```bash
pip install counterpart
```

```python
from pydantic import BaseModel
from counterpart import Contract


class Quote(BaseModel):
    price: float
    currency: str


async def test_agent_rejects_a_lying_peer(mock_agent):
    contract = (
        Contract("freight quote")
        .returns(Quote)
        .require("price_is_number", lambda q: isinstance(q.price, (int, float)))
        .expect_status("completed")
    )

    peer = mock_agent(persona="false_success")
    async with peer.client() as client:
        task = await client.send_message("Quote 2 pallets LA to Dallas", contract=contract)

    assert task.status == "completed"   # the peer said it was done
    assert task.contract_violated       # what it sent back was useless
```

Installing the package is the whole setup. The `mock_agent` fixture and the async
configuration come with it, so there is no `conftest.py` to write.

## Why this exists

Multi-agent systems rarely fail by crashing. They fail when one agent finishes successfully and
hands the next one something incomplete or wrong. Nothing raises, nothing gets logged, and you
find out days later from a bad invoice or a denied claim. A study of multi-agent failures puts
false success at 45 to 79 percent of them.

Conformance testing cannot see this, because the protocol behaved correctly. Evaluation
platforms cannot either, because they simulate a user talking to your agent rather than a peer
agent answering it. So the question nobody asks is the one that matters: the agent I delegated
to says it finished, but can I use what it gave me?

Here is a real example. HL7's prior authorization standard publishes a sample response for a
request that is still pending review. Its top level `outcome` field says `complete`. The
approved sample says `complete` too. The two differ only in an optional code buried four levels
deep, while the field that misleads you is the required one. Any client reading the obvious
status field will schedule surgery against an authorization that does not exist.

## Personas

Every persona is deterministic, needs no LLM, and drives a real A2A task lifecycle.

| Persona | What it does |
| --- | --- |
| `cooperative` | Works and completes with a well formed result |
| `clarifier` | Asks one question, waits for your answer, then completes |
| `false_success` | Reports `completed` and returns garbage |
| `resource_abuse` | Stalls or streams forever without finishing |
| `flaky` | Drops the connection, then recovers on retry |
| `over_sharing` | Asks for more context than the task needs |

Writing your own takes one class and one call. There is no DSL.

```python
from counterpart.core import Complete, Progress
from counterpart.personas import register

class HalfAnswer:
    def respond(self, turn, ctx):
        return [Progress("working"), Complete(result={"partial": True})]

register("half_answer", HalfAnswer)
```

## One thing to know about contracts

By default `returns()` uses pydantic's normal mode, so a peer sending `{"price": "1420.00"}`
gets coerced to a float and passes. Your predicates then see a real number, so an `isinstance`
check will not save you. That is ordinary pydantic behaviour, but it surprises people here,
because you might reasonably think you asked for type validation.

If you want the stricter reading, ask for it:

```python
Contract("fare").returns(Fare, strict=True)
```

Lax is the default on purpose. Plenty of real services send numbers as strings, and a contract
that rejects valid traffic gets switched off, which leaves you with nothing. Use `strict=True`
when you control both ends or the format is pinned.

## Exposing your own agent

`wrap()` turns any callable, sync or async, into an A2A server you can point tests at.

```python
from counterpart import wrap

app = wrap(my_agent, name="quoting-agent", skills=["freight-quote"])
```

Run it with uvicorn, or skip the socket entirely with `A2AClient(app=app)`.

## Checking a live agent

```bash
counterpart check https://my-agent.example.com
counterpart attack https://my-agent.example.com --json
```

`check` gives you a scored report where every row cites the spec section it verifies. It is a
quick smoke test for the development loop, not a certification suite. For the full conformance
matrix use the official [a2a-tck](https://github.com/a2aproject/a2a-tck). `attack` sends
adversarial probes and reports what came back.

## Examples

There are four runnable scenarios in [examples](examples), each in a domain where the
counterparty genuinely belongs to someone else, and each built around a different kind of
failure.

[Freight procurement](examples/freight_procurement.py) collects quotes from competing carriers
and picks the cheapest usable one. The naive version books the carrier whose completed quote
lists `price` as `"call for rate"`, and sends that string to invoicing.

[Prior authorization](examples/prior_authorization.py) clears a procedure with a health
insurer's eligibility and utilization management agents. It models 25 payer responses that all
report `completed`, drawn from the X12 278 and FHIR Da Vinci specs and from billing forums.
Each one is labelled by how well attested it is, and two are marked unattested because I could
not find a real case for them.

[Satellite downlink](examples/satellite_downlink.py) schedules a pass with a ground station
network. Twelve plans that all validate and all describe a different physical reality: a window
handed over in GPS time, elevation in radians where the field says degrees, an inertial frame
where you need an earth fixed one. This is the failure that cost NASA the Mars Climate Orbiter.

[Chaos multihop](examples/chaos_multihop.py) is deliberately excessive. Four hops, each a
separate HTTP server, auth passed along the chain, twenty concurrent users, and corruption
injected at the deepest hop. Corruption three hops away still gets caught at the top.

Every one of them measures two things, and the second matters more. Every unusable answer has
to be caught, and every legitimate variation has to be left alone. That second half is not
theoretical. Researching the real X12 value sets turned up three bugs in these contracts, and
all three were false positives: rejecting a valid `A1` certification, rejecting a member id
that came back correct but with different padding, and comparing dates as strings so that
`07/31/2026` quietly passed a check it should have failed. A later chaos run found a fourth.
None of them was a missed catch. A contract that flags good traffic gets deleted in week two.

## What it does not do

This is testing you run before you deploy. It does not watch production, it does not solve
agent identity or trust, and it will not bound how deep a peer delegates. A contract only sees
the payload, not the clock, so if a correct answer arriving 400ms late is useless to you, that
timeout is yours to enforce. [limits_probe.py](examples/limits_probe.py) shows exactly where
the library goes quiet.

If your agents are three functions in the same process, you do not need any of this.

## Design

The core has no protocol code in it. A2A is one adapter, and a test enforces the boundary, so
the contract engine also works on a plain HTTP response, an MCP style tool result, or a
function return.

- [docs/spec-notes.md](docs/spec-notes.md) maps A2A v1.0 to the code, citing the spec
  throughout. Wire types are checked against the vendored normative proto.
- [docs/prior-art.md](docs/prior-art.md) covers what already exists and where this fits.
- [docs/roadmap.md](docs/roadmap.md) lists what is out of scope and what is known to be missing.

## Status

Version 0.1.1, built against A2A spec v1.0. The API will change. Apache-2.0.

If you have hit this failure in something you built, I would like to hear about it. Open an
issue with the shape of the response that fooled you.
