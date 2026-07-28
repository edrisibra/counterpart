# Contributing to a2a-sandbox

Thanks for looking. This is a young project (v0), so the most valuable contributions right now
are **failure modes you have actually hit** — a real counterparty that lied about finishing, a
payload that broke your agent, a check you had to hand-roll.

## The fastest useful contribution

Open an issue titled with the failure, not the fix. For example: *"payer returned an approval
with no authorization number"*. Include the shape of the payload if you can share it (redact
freely — the shape matters, the data does not). Failure modes drawn from real systems are what
make the persona library worth anything; invented ones make it worse.

## Development setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/edrisibra/a2a-sandbox
cd a2a-sandbox
uv sync
uv run pytest
```

Before opening a PR, all four of these must pass — CI runs exactly the same commands on 3.11
and 3.12:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Project rules that are not negotiable

1. **`src/a2a_sandbox/core/` contains no protocol code.** No A2A types, no imports from
   `adapters/`, no HTTP/ASGI dependencies. A2A is one adapter; the engine has to survive a
   different protocol winning. `tests/core/test_core_is_protocol_free.py` enforces this and
   will fail your build.
2. **Spec claims cite the spec.** Anything asserting A2A behaviour must reference the section
   in [`docs/spec-notes.md`](docs/spec-notes.md), which in turn cites the specification. The
   normative wire definition is vendored with a checksum in
   [`tests/data/`](tests/data/README.md). Do not implement protocol behaviour from memory.
3. **New failure modes must be labelled by how well they are attested.** The examples
   distinguish `very common` / `common` / `long-tail` / `unattested`. If you cannot point to a
   source, mark it `unattested` — that is an honest and acceptable label. Inventing a dramatic
   failure that no real system produces makes the library less useful, not more.
4. **Every check needs a false-positive test.** A contract rule that rejects legitimate
   variation is worse than no rule, because users switch the whole thing off. Every rule that
   catches a bad response needs a sibling test proving it accepts the good variations —
   including the awkward ones (different case, boundary dates, optional fields omitted).

## Adding a persona

A persona is a plain class with one method. No DSL, no registration ceremony beyond one call:

```python
from a2a_sandbox.core import Complete, Progress
from a2a_sandbox.personas import register

class HalfAnswer:
    """Reports success but omits the field the caller actually needs."""

    def respond(self, turn, ctx):
        return [Progress("working"), Complete(result={"partial": True})]

register("half_answer", HalfAnswer)
```

Personas must be **deterministic** — same inputs, same directives out. No LLM calls, no
randomness, no wall-clock dependence. Tests and CI runs have to be reproducible.

## Commit sign-off (DCO)

Contributions are accepted under the [Developer Certificate of
Origin](https://developercertificate.org/). Sign off each commit:

```bash
git commit -s -m "your message"
```

That appends a `Signed-off-by:` line, certifying you wrote the patch or have the right to
submit it under the project's MIT licence.

## Licence

By contributing you agree your contribution is licensed under the
[MIT Licence](LICENSE), the same as the project.
