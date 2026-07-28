# counterpart

[![CI](https://github.com/edrisibra/counterpart/actions/workflows/ci.yml/badge.svg)](https://github.com/edrisibra/counterpart/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/counterpart.svg)](https://pypi.org/project/counterpart/)
[![Python](https://img.shields.io/pypi/pyversions/counterpart.svg)](https://pypi.org/project/counterpart/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

[A2A](https://a2a-protocol.org/) lets one AI agent hand work to another. counterpart mocks the
agent on the other end, including the ways it can go wrong:

```python
async def test_carrier_reports_success_with_no_price(mock_agent):
    peer = mock_agent("false_success")     # reports done, returns garbage

    task = await peer.ask("Quote 2 pallets LA to Dallas", contract={"price": float})

    assert task.status == "completed"   # the peer said it finished
    assert task.contract_violated      # what it sent back was unusable
```

```bash
pip install counterpart
```

Installing the package is the whole setup. The `mock_agent` fixture and the async
configuration come with it, so there is no `conftest.py` to write.

This is for you if your agent hands work to an agent somebody else runs and you cannot stand up
their side to test against. If your agents are three functions in the same process, you do not need
any of this.

The mock runs inside your test process, so there is no port to manage and nothing touches the
network. Six personas ship with it, all deterministic and none needing an LLM: ones that work, ones
that stall, ones that come back with junk. `mock_agent` is the pytest fixture, and `MockAgent` is
the same object without pytest, for when you want `.serve()` to give you a real address to point
another process at.

`{"price": float}` is shorthand for a contract that requires a `price` field which is a
number. When you want more, build one properly. Pass a pydantic model, add named checks, and
use `.client()` when a test needs several turns on one connection:

```python
contract = (
    Contract("freight quote")
    .returns(Quote, strict=True)                       # a real model, no coercion
    .require(price_positive=lambda q: q.price > 0)
)

peer = mock_agent("clarifier", question="Deliver by when?")
async with peer.client() as client:                    # one connection, several turns
    task = await client.send_message("Quote 2 pallets", contract=contract)
    task = await client.reply(task, "Friday")
```

## Why check the reply as well

Mocking the transport is the easy half. The reason counterpart also looks at content is that
`completed` only tells you the other agent stopped working. Every one of these arrives as a
completed task, and the protocol is satisfied by all of them:

```python
{"message": "Your quote is ready!"}          # reads fine, contains no price
{"price": "call for rate"}                   # a sentence where you needed a number
{"price": 1420.0, "account": "AC-99812"}     # right shape, somebody else's account
{}                                           # nothing at all
```

Your code then carries on with whatever it got. Nothing raised, nothing was logged, and the bad
value is now three functions downstream or in an invoice. That is what makes these expensive to
find: you are debugging backwards from a wrong number, days later, with no stack trace to start
from. One [study](https://arxiv.org/abs/2503.13657) annotated more than 1,600 execution traces from
seven multi-agent frameworks and sorted the failures into 14 modes under three headings. One of the
three headings is task verification.

A contract is just you writing down what you were expecting, so the test fails at the point the
peer lets you down instead of somewhere later.

## Personas

Every persona is deterministic, needs no LLM, and walks a real A2A task through its states.

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

## Testing the other direction

Everything above has your agent doing the calling. Sometimes you are the one being called, and
you want to know your agent answers properly when somebody else's agent asks it something. The
same mocks work as the caller.

`wrap()` turns any callable, sync or async, into an A2A server, and the mock sends it work:

```python
from counterpart import MockAgent, serve_asgi, wrap

with serve_asgi(wrap(my_agent, name="quoting-agent")) as url:
    peer = MockAgent("cooperative")
    task = await peer.send_task(url, "Quote 2 pallets", contract={"price": float})

assert not task.contract_violated      # your agent's own answer held up
```

The contract checks your agent this time, which is a quick way to catch your own service
returning a completed task with nothing useful in it. Run the server with uvicorn instead if you
want a long-lived one, or skip the socket with `A2AClient(app=app)`.

## Checking a live agent

```bash
counterpart check https://my-agent.example.com
counterpart attack https://my-agent.example.com --json
```

`check` gives you a scored report where every row cites the section of the spec it verifies. It is
a quick smoke test for the development loop, not a thorough audit. When you want to know whether
your server follows every rule in the protocol, use the official
[a2a-tck](https://github.com/a2aproject/a2a-tck). `attack` sends deliberately malformed and hostile
requests and reports what came back.

## Examples

Five runnable scenarios in [examples](examples), each in a domain where the counterparty genuinely
belongs to somebody else, and each built around a different kind of failure.

- [Freight procurement](examples/freight_procurement.py) picks the cheapest usable quote. The naive
  version books the carrier whose `price` is the string `"call for rate"` and sends that to
  invoicing.
- [Freight edge cases](examples/freight_edge_cases.py) has 22 ways a quote is unusable, and 14 that
  look wrong and are perfectly normal. Checking the 22 changes which carrier you book, and the right
  answer costs $592.50 more than the cheapest bid.
- [Prior authorization](examples/prior_authorization.py) is a clinic getting a procedure approved by
  an insurer before performing it, with money and patient safety attached. It models 25 replies that
  all report success. The standard's own published example of a *pending* decision reports success,
  with the only honest signal in an optional nested field.
- [Satellite downlink](examples/satellite_downlink.py) schedules a pass with a ground station. Twelve
  plans that all validate and each describe a different physical reality, such as an elevation in
  radians where the field says degrees. This is the class of mistake that cost NASA the Mars Climate
  Orbiter.
- [Chaos multihop](examples/chaos_multihop.py) is deliberately excessive. Four hops, each a separate
  HTTP server, auth passed along the chain, twenty concurrent users, and corruption injected at the
  deepest hop. It still gets caught at the top.

Each one checks two things, and the second matters more. Every unusable answer has to be caught, and
every legitimate answer has to be left alone.

That second half is where the real difficulty turned out to be. Going through the actual industry
value sets found four bugs in the contracts I had written, and every single one was a false positive.
My checks were rejecting good answers: an approval code I had not thought to accept, an id that came
back correct but padded with spaces, a date compared as text so that `07/31/2026` slipped past a
check it should have failed, and a currency of `"usd"` rejected because I had only thought of
`"USD"`. None of the four was a missed catch.

That is the trap with this kind of testing. Catching bad data is easy. A checker that also flags good
data gets switched off, and then you are back to having none, so every example tests for false
positives explicitly.

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

## What it does not do

This is testing you run before you deploy. It does not watch production, it does not solve
agent identity or trust, and it will not bound how deep a peer delegates. A contract only sees
the payload, not the clock, so if a correct answer arriving 400ms late is useless to you, that
timeout is yours to enforce. [limits_probe.py](examples/limits_probe.py) shows exactly where
the library goes quiet.

## Design

The core has no protocol code in it. A2A is one adapter, and a test enforces the boundary, so
the contract engine also works on a plain HTTP response or a function return.

- [docs/spec-notes.md](docs/spec-notes.md) maps A2A v1.0 to the code, citing the spec
  throughout. Wire types are checked against the protocol's own proto file, checked into the repo.
- [docs/prior-art.md](docs/prior-art.md) covers what already exists and where this fits.
- [docs/roadmap.md](docs/roadmap.md) lists what is out of scope and what is known to be missing.

## Status

Version 0.1.4, built against A2A spec v1.0. The API will change. Apache-2.0.

If you have hit this failure in something you built, I would like to hear about it. Open an
issue with the shape of the response that fooled you.
