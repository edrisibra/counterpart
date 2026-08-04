# counterpart scanner

A web app that scans a live A2A agent and grades it. Paste a URL, get a report and a badge.

This is not part of the `counterpart` package and is not published to PyPI. It imports the
library and wraps the `check` and `attack` engine in a form, a report page, and a JSON endpoint.

```bash
uv run uvicorn counterpart_scanner.app:app --port 8000
```

Then open <http://127.0.0.1:8000>.

## Routes

| Route | What it does |
| --- | --- |
| `GET /` | the form |
| `POST /scan` | validate the URL, scan, redirect to the report |
| `GET /report/{id}` | the report page |
| `GET /badge/{id}.svg` | an embeddable badge |
| `GET /api/scan?url=` | the same scan as JSON, for CI |
| `GET /healthz` | liveness |

## What it grades

Conformance carries the letter grade, and adversarial results can only lower it. An agent that
answers every spec check correctly but obeys an injected instruction gets marked down, because a
green badge on that agent would be a lie.

It does not grade the *content* of what an agent returns. A task can reach `completed` and carry a
useless result, and only the caller knows what usable means for their domain. That part needs a
contract, which is what the library is for.

## Security

The scanner fetches a URL supplied by an anonymous visitor, so admission control is the whole
security story. `guard.py` resolves the hostname and refuses unless every address is publicly
routable: loopback, private, link-local, reserved, multicast, carrier-grade NAT, IPv4-mapped IPv6,
cloud metadata addresses and names, and a list of service ports are all refused. Requests carry no
credentials, redirects are not followed, and responses are never echoed back to the visitor except
as escaped text in the report.

Two limits worth stating. Validation happens before httpx resolves the name again, so a DNS name
that answers publicly and then privately is not defeated by this; closing that needs a transport
pinned to the validated address. And results live in memory for a day, so a restart loses them.

## Before deploying this publicly

- Put it behind a proxy that terminates TLS and sets `x-forwarded-for`.
- The rate limit is per process and in memory. Behind more than one worker, move it to something
  shared.
- Scanning a third party's agent sends real traffic to it. Say so on the page, and consider
  requiring the operator to prove they control the agent before allowing repeat scans.
