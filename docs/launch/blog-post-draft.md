# DRAFT: launch blog post

Not published. Check the numbers against the example before posting, and run
`uv run python examples/freight_edge_cases.py` so the output you quote is the output people get.

---

## My agent booked a carrier that wasn't allowed to haul freight

I was building an agent that shops for freight. It asks a handful of carriers what they'd charge
to move a couple of pallets, then books the cheapest one. Five quotes came back. It picked the
cheapest, at $1,050.

That carrier had no operating authority. No MC number, which is the registration a carrier needs
to legally move freight for hire in the US. If the load had gone missing, the loss would have
been mine, and my insurer would have had a reasonable opinion about why.

Here's the thing that bothered me. Nothing went wrong.

The request succeeded. The response was valid JSON. It parsed cleanly into the shape I expected.
It had a carrier name, a price, a transit time, an expiry date. The agent on the other end
reported its task as `completed`, because from its point of view it had finished the work it was
asked to do. Every layer I had was satisfied.

My code was the only thing that cared whether the carrier was allowed to drive, and my code
wasn't looking.

## Why not five REST APIs

Fair question, and freight already has an answer. Machine to machine procurement has existed there
for decades as EDI, Electronic Data Interchange, and its truckload workhorse is transaction set 204,
the load tender.

A 204 is not a rate request. The standards body, ASC X12, describes it as an offer of a shipment to
a carrier, and the message carries the shipper's own rate. The carrier's reply, a 990, is four
segments long and its real payload is one code: A for accepted, D for declined. There is no price
in it anywhere.

Two pallets from Los Angeles to Dallas ships LTL, less than truckload, which never touches 204 and
990. So I went to the carrier APIs instead. Estes is REST with an OAuth 2.0 bearer token plus a
separate API key. Old Dominion is still SOAP, with the username and password inside the request
body. XPO is OAuth 2.0 against its own token endpoint.

Three carriers, two wire formats, three auth models. Each also keeps its own proprietary
accessorial codes, which are the surcharges for things like a liftgate or a residential delivery.
With five carriers I write five auth flows and five code mappings. At thirty I write thirty of each,
which is why almost nobody does it and almost everybody buys a rating engine.

A2A, the Agent2Agent protocol, lands on exactly that. Its own introduction says it is for agents
"built using different frameworks, languages, or by different vendors" to work together "without
needing access to each other's internal state, memory, or tools." The nine task states are defined
in the protocol, so nobody reconciles my word for queued against theirs. Optional features are
declared as fields instead of discovered by trial.

The part that only matters across company lines is the opacity. Inside my own organisation I can
read the other team's schema and ask them to change it. I can do neither with a carrier, and I do
not control when they ship.

Two things I should not overstate. The spec lists three ways to find an agent's card and only
recommends the well known path, so discovery is closer to a convention than a guarantee. And one
interface lets me reach thirty carriers, which is not the same as being able to price with them.
LTL rates are account specific. Old Dominion's own guide says the credentials grant access to data
for your account, and Estes quotes only for registered users, so each carrier still wants a pricing
agreement and a credit application first.

So the protocol removes the plumbing and leaves the commercial work. That is also why the content
problem gets worse rather than better. The per partner integration was the thing that used to make
me read a carrier's schema line by line, and that is where I would have noticed one of them quoting
without fuel. Thirty carriers behind one interface, and nobody reads anything.

While I'm admitting things: operating authority is checkable. The FMCSA publishes it and I could
have called it. The check existed and calling it was my job. Nothing in my stack told me I hadn't.

## The bug isn't in the protocol

I'm using [A2A](https://a2a-protocol.org/), the protocol for agents to hand work to each other.
When a task reaches `completed`, that means the other agent stopped working. It does not mean the
result is any good. Those are two different claims and only one of them is on the wire.

Once I started looking for this, it was everywhere in the quotes I was getting back:

One carrier came in cheapest because the quote excluded the fuel surcharge, which in freight is a
percentage added on top and is not optional. Its $1,300 would have invoiced at $1,642.50, about a
quarter more than the number I compared against.

Another quoted my shipment at freight class 50 when it's class 70. Class comes from density, and
a carrier that reclassifies your pallet on the dock bills you the difference. The cheap number
was cheap because it described a different, lighter shipment.

Another said three days transit and did not say which kind of days it meant. Transit is normally
quoted in business days, so from a Thursday pickup that is the following Tuesday. I had been
planning around Sunday. Nothing in the payload said which.

None of those were malformed. Every one of them was a completed task with a valid payload and a
real number in it. If you sort by price and book the winner, you lose money on all three, and you
find out weeks later from an invoice that doesn't match anything you agreed to.

## Why the tools I had didn't help

I checked whether I was missing something obvious.

There's an official test kit for A2A that checks whether a server follows the protocol's rules.
It didn't help, because the carrier's server followed them. That is the whole point of that kit and
it did its job. It will happily tell you a server is correct while the server hands you a quote
from a carrier that isn't allowed to drive.

Evaluation platforms didn't help either. They simulate a user talking to your agent and score how
well it reasons. Useful, different problem. Nobody was checking whether the thing the *other*
agent sent back was usable.

Mock servers got closest, and there are a couple of good ones. But they replay canned responses,
so they answer "does my code handle a response" and not "should my code have accepted this
response".

The question I actually had was: the agent I delegated to says it finished, can I use what it gave
me? Nothing I could find asked it.

## What I built

[counterpart](https://github.com/edrisibra/counterpart) does two things.

It stands in for the agents your agent calls, and it lets you say what a usable answer looks like
before you accept one.

```python
async def test_agent_rejects_a_bad_quote(mock_agent):
    peer = mock_agent("false_success")     # reports done, sends back junk

    task = await peer.ask("Quote 2 pallets LA to Dallas", contract={"price": float})

    assert task.status == "completed"   # it said it finished
    assert task.contract_violated      # and there was no price in it
```

For the real freight case the contract is longer, because the things that cost money are specific:

```python
contract = (
    Contract("LTL freight quote")
    .returns(Quote)
    .require(has_operating_authority=lambda q: bool(q.mc_number) and q.mc_number.isdigit())
    .require(insurance_valid=lambda q: date.fromisoformat(q.insurance_expires) >= today)
    .require(fuel_surcharge_accounted=lambda q: q.fuel_surcharge_usd is not None)
    .require(freight_class_matches=lambda q: q.quoted_freight_class == load.freight_class)
    .require(transit_basis_stated=lambda q: q.transit_day_basis in {"business", "calendar"})
)
```

That's not clever code. It's the list of things I now know to check, written down where a test can
run it, instead of living in my head and being forgotten at 6pm.

The repo has this as a runnable example with 22 ways a quote can be unusable. The result I like
best is the selection one, because it isn't about validation at all:

```
  $1,050.00  Gray Route          no operating authority
  $1,180.00  Dockline            excludes the fuel surcharge
  $1,240.00  Sunbelt LTL         wrong freight class, rebilled on the dock
  $1,642.50  Ridgeline Freight   usable
  $2,180.00  Meridian Carriers   usable

  without checks: books Gray Route at $1,050.00
  with checks:    books Ridgeline Freight at $1,642.50
```

The correct answer is to pay $592.50 more. That's the part I'd want someone to take away. This isn't
a linter that makes your tests stricter, it's the difference between two bookings.

## The kinds of wrong

Three examples don't show a shape. Across three unrelated domains I wrote 59 payloads a peer could
return while reporting success, and they collapse into a handful of kinds. A contract, in this
library, is just the list of things I said a usable answer has to satisfy. These six are the ones
furthest apart, picked so no two fail for the same reason.

### A number with no frame attached

A ground station returns `max_elevation_deg: 0.728` next to `angle_unit: "rad"`. Read as radians
that is 41.7 degrees and a good overhead pass. Read as degrees, the antenna is aimed at the
horizon. This is the class of mistake that destroyed the Mars Climate Orbiter, and it is catchable
here only because the unit rides on the wire as its own field, which the schema chose to do.

### A quote that contradicts its own arithmetic

`total_usd: 1400.00` over line items of 1,180.00, 342.50 and 120.00, which sum to 1,642.50. A
checker can reject that without knowing anything about what I asked for.

### A price for the opposite trip

The same quote with `origin_zip: "75201"` and `dest_zip: "90021"`. I asked for Los Angeles to
Dallas and got a real, well formed price for Dallas to Los Angeles.

### An input that expired

A pass plan arrives with a `tle_epoch` 31 days old. A TLE, or two line element set, is the orbital
state the schedule is computed from, and mine is good for about three days. The prediction was
computed this morning from a month old orbit.

### A refusal that reads as an answer

An eligibility check comes back `coverage_active: False` with `reject_reason_code: "72"`. Code 72
means the insurer could not identify the member, so it never answered the question at all. Read as
"not covered", the clinic tells a patient their insurance won't pay. The other five here cost me
money. This one costs a patient.

### Success in the field a client is guaranteed to read

The standard's own published example of a pending decision reports success at the top level, with
the only honest signal in an optional nested field. The field a client must read says complete. The
field carrying the truth is one a client is allowed to skip.

## What contracts can't catch

These four cannot be fixed by writing better contracts.

Cross peer disagreement. I asked five peers the same question and two of them lied. All five passed
their own contract, because a contract is per peer, and agreement between peers is not something a
per peer check can see. Quorum, and median of N which means taking the middle answer of several,
sit a layer above this. I write those by hand.

The clock. A contract only ever sees the payload. A correct answer that arrives in 402 milliseconds
when I needed 300 is still a miss, and whether that is too slow depends on my business rather than
on the response, so the response cannot tell me.

Delegation depth. Nothing bounds how far a peer delegates onward, so a four hop chain can do work I
never authorised and hand me back something clean.

Capability claims that behaviour does not back. An agent card can advertise a skill the agent
cannot actually perform, and a contract sees a result rather than the card it came with. That
mismatch is conformance ground, and a2a-tck is the suite built for it. Its RFC 2119 levelling keys
the optional tests to whatever the card declared.

## The part I got wrong

I expected the hard bit to be catching bad data. It wasn't. It was not crying wolf.

I went and read the actual industry code lists to make the examples realistic, and doing that
found four bugs in the checks I had already written. Every single one was a false positive. My
checks were rejecting good answers.

I only accepted one spelling of an approval code, so I rejected the real code that the standard
actually uses. I compared dates as text, so `07/31/2026` slipped past a check it should have
failed, which is the dangerous direction to be wrong in. I compared an id with `==`, so a
correct id that came back with a space around it was treated as the wrong customer. And I rejected
`currency: "usd"` because I'd only thought of `"USD"`.

Not one of the four was a missed catch. All four were the checker being wrong about good input.

That matters more than it sounds. A checker that flags valid traffic gets switched off in week
two, and then you have nothing. So both examples in the repo now test the opposite direction
explicitly: fourteen things that look wrong and are completely normal, like an all in rate with no
breakdown, or a billed weight higher than the scale weight because the pallet is bulky, and all
fourteen have to pass.

## What it doesn't do

It's testing you run before you ship. It doesn't watch production, so if you want to know what
your agents are doing right now you want tracing, not this.

And if your agents are three functions in the same process, you don't need any of this. Call the
functions.

## If you've hit this

I'd like to know what the payload looked like. The failure modes in the examples are only as good
as the real cases behind them, and I'd rather model things that actually happened to somebody
than things I imagined. There's an issue tracker.

`pip install counterpart`, Apache 2.0, version 0.1.7, the API will change.

---

## Notes to self, delete before posting

- Run the freight example and paste its real output. Don't retype the numbers.
- The four false positives section is the most credible thing here because it's an admission
  against interest. Keep it even if the post runs long.
- Don't claim any users. There aren't any.
- Verified by me against primary sources: the 59 payload count (counted in the repo), the Linux
  Foundation April 2026 adoption numbers, the arXiv 2503.13657 citation, and the $592.50 and
  $1,642.50 figures (run the example).
- NOT verified by me, came from a research pass: the EDI 204 and 990 descriptions, and the Estes,
  Old Dominion and XPO auth and wire format details. Spot check those against the carriers' own
  developer docs before publishing, because a freight reader will know instantly if one is stale.
- Two numbers in earlier versions of this post were wrong and got caught late: a "45 to 79 percent"
  statistic that its supposed source does not contain, and "adoption is thin" which the Linux
  Foundation release contradicts outright. Assume there is a third.
- Title options, all plain: "My agent booked a carrier that wasn't allowed to haul freight" /
  "The cheapest quote came from an unlicensed carrier" / "Finished and correct are different
  things".
