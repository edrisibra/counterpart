# DRAFT — launch posts

Not published. Prerequisites before any of this goes out:

1. Repo is **public** and `pip install a2a-sandbox` works (a Show HN pointing at a private repo
   gets flagged immediately).
2. The **name question is settled** — confirm "A2A"/"Agent2Agent" trademark rules permit
   `a2a-sandbox` as a package name, or rename first. Renaming after publishing is the one
   genuinely painful mistake available here.
3. You have a free evening. You are expected to answer comments for several hours; a Show HN
   where the author vanishes does badly.

Timing: Tuesday–Thursday, roughly 8–10am US Eastern. One shot per project — you don't get a
do-over next week.

---

## Show HN

**Title** (keep under 80 chars; leads with the finding, not the tool):

```
Show HN: HL7's prior-auth standard says "complete" when a request is only pended
```

Alternatives, roughly in order of preference:

```
Show HN: A2A-sandbox – test your agent against a peer that lies about finishing
Show HN: The field that says success is required; the field with the truth is optional
Show HN: Your agent said it's done. Prove it.
```

**URL:** the GitHub repo (not the blog post — HN prefers the artifact; put the blog link in the
comment).

**First comment** (post this yourself, immediately after submitting):

> I have been building a testing library for agents that talk to each other over A2A, and went
> looking for a realistic example of the failure I care about: an agent reporting success while
> returning work you can't use. I found the cleanest example in HL7's own prior-authorization
> standard.
>
> Their published *pended* example returns `outcome: "complete"` with no authorization number.
> Their *approved* example is also `outcome: "complete"` with no authorization number. The two
> differ only in an optional, deeply-nested `reviewActionCode` (A1 certified vs A4 pended) —
> while `outcome`, the field that misleads you, is required (1..1). A client that branches on
> the obvious top-level status field cannot tell an approval from a pend. Still present in the
> current build, not an old erratum.
>
> X12 has the same trap: a first 278 response is often an interim ack, not a decision. Blue
> Cross NC returns A4 (pended) within 24h and sends the real determination later as a separate
> unsolicited transaction; Texas Medicaid returns A4 "for all approved transactions". Same code,
> opposite meaning, depending on payer.
>
> The general shape is what interests me. In A2A, a task reaching `completed` means the agent
> finished — not that the work is right. Conformance testing can't see the difference (the
> protocol behaved fine) and eval platforms can't either (they simulate a user and score
> reasoning). Nobody checks whether the artifact the *peer* handed back is usable.
>
> So the library does two things: counterparty agents that misbehave on purpose (so you can
> test against a peer that lies), and contracts that verify the returned content against what
> you asked for. The repo has a runnable scenario with 25 modelled payer failures, all of which
> report `completed`; a naive agent schedules on 24 of them.
>
> The part that surprised me: writing checks was easy, writing checks that don't cry wolf was
> not. Researching the real X12 value sets found three bugs in my own contracts and all three
> were false positives — I rejected `A1` and "Certified in total" (real certification values),
> string-compared dates so `07/31/2026` silently *passed* a window check, and `==`-compared
> member IDs so a correctly-echoed ` w123456789 ` was rejected as the wrong patient. A checker
> that flags valid answers gets switched off in week two, which is worse than having none.
>
> v0, MIT, API will change. It's pre-deployment testing — it doesn't monitor production and
> doesn't solve agent identity. If your agents are three functions in one process you don't
> need it. Happy to be told the abstraction is wrong.

**Comment-thread prep** — the questions you will get, and honest answers ready:

- *"Isn't this just Pydantic validation?"* — Largely yes, and say so. The structural check *is*
  `model_validate`. What's added: pairing it with the peer's reported status so the discrepancy
  is recorded, a typed failure category, and — the actual work — a spec-verified A2A server that
  walks the real task lifecycle so you have something to test the contract against.
- *"Isn't A2A dead / who uses A2A?"* — Concede it directly. Adoption is thin; a probe found
  0/50 agents advertising A2A actually answered a valid request. That's why the core is
  protocol-agnostic: the contract engine works on any delegated result — plain HTTP, an MCP tool
  call, a function return.
- *"Doesn't a2a-tck already do this?"* — No, and be precise: a2a-tck tests *your server* for
  protocol conformance. This tests the *counterparty you delegate to*, at the content layer.
  Different direction, different layer. Link them; don't disparage them.
- *"Why not just use DeepEval / LangSmith?"* — They simulate a user and score your agent's
  reasoning. They don't simulate a protocol-speaking peer that returns a malformed artifact.
- *"How is this different from a mock server?"* — `aimock` and `mokksy` do stateless fixture
  matching. These are stateful personas walking a real task lifecycle, paired with content
  verification. Credit them.

**Rules of engagement:** no marketing voice, concede every fair criticism immediately, never
argue with a downvote. If someone says the idea is wrong, ask what they'd do instead — that
answer is worth more than the upvotes.

---

## r/LLMDevs and r/AI_Agents

Problem-first; these subs remove "check out my project" posts.

**Title:** `The worst multi-agent bug isn't a crash — it's an agent reporting "completed" with garbage`

**Body:** open with the HL7 finding, then the general shape (protocol `completed` ≠ work
correct), then the three false positives I found in my own checks, then one link at the end.
Roughly 70% problem, 30% tool.

---

## The A2A project's own discussions

The most useful post of all of these, and the lowest risk. Frame it as a question, not an
announcement:

> Is counterparty/negative testing in scope for the project's tooling? a2a-tck covers
> server-side conformance well; I've been working on the other direction — simulated
> counterparty agents that misbehave (pended-as-approved, silent code downgrade, never
> decides) plus content verification of the returned artifact. Built it out of need; happy to
> contribute the pieces upstream if useful, or keep it as an ecosystem tool. Which would you
> prefer?

That question gets you one of three answers, all valuable: they want it, they'd absorb it into
a2a-tck, or nobody replies. Sequence this **before** the Show HN if you can — the answer changes
how you position the launch.
