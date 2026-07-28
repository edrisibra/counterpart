# DRAFT — launch blog post

Status: draft, not published. This is the hook for launch: a specific, checkable finding about
a real standard, with the library as the punchline rather than the headline.

Verify every claim below still holds on the day you publish — the FHIR IG is a moving target
and the whole point of this post is that it is precisely correct.

---

## "Complete" does not mean approved: a landmine in HL7's prior-auth standard

I have been building a testing library for AI agents that talk to each other, and I went
looking for a realistic example of the failure I care about most: **an agent that reports
success while handing back work you cannot use.**

I did not expect to find the cleanest example of it sitting in an official healthcare standard.

### The setup

HL7's Da Vinci **Prior Authorization Support** (PAS) implementation guide is the FHIR standard
for a provider asking a payer to authorise a procedure. The payer's answer comes back as a
`ClaimResponse`, which has a top-level field called `outcome`.

`outcome` is bound to a small value set — `queued | complete | error | partial` — and it is
**required** (cardinality `1..1`). If you are writing a client, that field looks exactly like
the one you should branch on.

### The problem

Here is the IG's own published example of a **pended** response — a request that has *not* been
decided, that is sitting in clinical review:

```json
{
  "resourceType": "ClaimResponse",
  "outcome": "complete",
  "item": [{
    "adjudication": [{
      "extension": [{
        "url": ".../extension-reviewAction",
        "extension": [{
          "url": "number",
          "valueCodeableConcept": {
            "coding": [{
              "system": "https://codesystem.x12.org/005010/306",
              "code": "A4",
              "display": "Pending"
            }]
          }
        }]
      }]
    }]
  }]
}
```

`outcome` is `"complete"`. There is no `preAuthRef` — no authorization number. The only thing
in the entire payload that tells you this was *not* approved is `reviewActionCode: "A4"`,
buried four levels deep in an extension.

Now look at the IG's **approved** example. It is also `outcome: "complete"`. It also has no
`preAuthRef`. The two responses — "approved" and "still pending" — **differ only in that
review action code.**

So:

| | Says approval state? | Cardinality |
|---|---|---|
| `outcome` | No | **Required** (1..1) |
| `reviewActionCode` | **Yes** | Optional (0..1) |

The field that carries the truth is optional. The field that misleads you is mandatory.

A client that reads `outcome` — the obvious, required, top-level status field — cannot
distinguish an approval from a pend. It will conclude the authorization came through, and the
procedure gets scheduled on an authorization that does not exist.

I checked whether this was an old erratum since corrected. It is present in STU 2.1 and still
in the current CI build.

### This is not a hypothetical failure

The X12 world has the same trap from a different direction. A first `278` response is routinely
an *interim acknowledgement*, not a decision — Blue Cross NC returns `HCR01=A4` (pended) within
24 hours and sends the actual determination later, in a separate unsolicited transaction. Texas
Medicaid goes further and returns `A4` *"for all approved transactions"* — there, `A4` is a
receipt that your request arrived.

Same code. Opposite meanings. Depending on the payer.

And the money is real: Premier reports that **10.4% of denied claims had been pre-approved via
prior authorization** — up from 3.2% the year before — at $57.23 per claim just to rework.
Authorization is not immunity from denial.

### Why I care, beyond healthcare

I have been working on agent-to-agent systems, where the same shape appears everywhere. In the
A2A protocol, a task reaching state `completed` means **the agent finished its work**. It does
not mean the work is correct, complete, or usable. Those are different claims, and only one of
them is on the wire.

This is the failure mode that ruins multi-agent systems, and it is much worse than a crash. A
crash gives you a stack trace and a place to look. This gives you nothing: every agent in the
chain reports success, no error is logged anywhere, and you find out weeks later from a denied
claim or a customer who was billed the wrong amount. An academic study of multi-agent failures
puts **false success at 45–79%** of them.

Conformance testing cannot see it. Conformance asks "did the protocol behave?" — and the
protocol behaved perfectly. Evaluation platforms cannot see it either; they simulate a *user*
talking to your agent and score its reasoning. Neither one asks the question that matters: *the
agent I delegated to said it was done — is what it gave me actually usable?*

### What I built

[a2a-sandbox](https://github.com/edrisibra/a2a-sandbox) does two things.

It gives you counterparty agents that **misbehave on purpose**, so you can test against a peer
that lies about finishing:

```python
peer = mock_agent(persona="false_success")   # reports completed, returns garbage
```

And it lets you declare what a *usable* answer looks like, then verify the answer you actually
got:

```python
contract = (Contract("prior authorization")
    .returns(AuthDetermination)
    .require("certified", is_approval)           # reads the review action code, not `outcome`
    .require("has_auth_number", lambda a: bool(a.authorization_number))
    .expect_status("completed"))

report = contract.verify(result=payload, reported_status=task.status)
assert report.contract_violated    # the peer claimed success; the work does not hold up
```

The report records **what the peer claimed** right next to **whether it held up** — which is
exactly the pairing that makes silent partial completion visible instead of invisible.

The repo has a runnable version of the scenario above: a clinic's agent clearing a procedure
with a payer's eligibility and utilization-management agents, against 25 modelled payer
failures — every one of which reports `completed`. A naive agent schedules on 24 of them.

### The thing that surprised me most

Building the checks was easy. Building checks that **don't cry wolf** was the hard part.

Researching the real X12 value sets found three bugs in the contracts I had written, and every
single one was a **false positive** — my checks rejecting *valid* answers:

- I only accepted the literal string `"APPROVED"`, so I rejected `A1` and `"Certified in
  total"`, which are the actual X12 certification values.
- I compared dates as strings, so a payer sending `07/31/2026` silently **passed** a window
  check that it should have failed. That one failed *open*, which is the dangerous direction.
- I compared member IDs with `==`, so a payer echoing ` w123456789 ` — the correct member, with
  different case and padding — got **rejected as the wrong patient**.

That is the real lesson, and it generalises well beyond healthcare: **a checker that flags
legitimate variation gets switched off in week two**, and then you have no checker at all. Both
examples in the repo now test explicitly for false positives, not just for catches.

### Honestly

This is v0 and the API will change. It is pre-deployment testing — it does not monitor
production, and it does not solve agent identity or trust. And if your "agents" are three
functions in one process, you do not need any of this.

But if you have an agent that depends on an agent someone else operates, it is worth asking
what your code does when that agent says `completed` and means something else.

---

## Notes for the author (delete before publishing)

- Re-verify the PAS examples on the day of publishing; link the exact raw JSON URLs.
- The three-false-positives section is the most credible part of this post — it is a real
  finding against my own code. Do not cut it to save length.
- Do not oversell adoption. No claims about users; there are none yet.
- Suggested title alternatives:
  - "'Complete' does not mean approved: a landmine in HL7's prior-auth standard"
  - "The field that says success is required. The field that says the truth is optional."
  - "Your agent said it's done. Prove it."
