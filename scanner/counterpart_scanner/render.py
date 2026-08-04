"""HTML and SVG for the scanner. No templates, no assets, no external requests."""

from __future__ import annotations

from html import escape

from counterpart_scanner.scan import Scan

_GRADE_COLOUR = {
    "A": "#2f9e44",
    "B": "#74b816",
    "C": "#f08c00",
    "D": "#e8590c",
    "F": "#c92a2a",
    "?": "#868e96",
}

_STATUS_COLOUR = {"pass": "#2f9e44", "fail": "#c92a2a", "warn": "#f08c00", "skip": "#868e96"}
_FLAG_COLOUR = {"handled": "#2f9e44", "obeyed": "#c92a2a", "server-error": "#c92a2a", "info": "#868e96"}

_CSS = """
:root { color-scheme: light dark; --fg:#1a1a1a; --dim:#6b6b6b; --bg:#fdfdfc; --line:#e4e4e1; --card:#fff; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e8e8e6; --dim:#9a9a97; --bg:#16171a; --line:#2c2e33; --card:#1c1e22; }
}
* { box-sizing: border-box; }
body { margin:0; padding:2.5rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
  font:16px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif; }
main { max-width: 54rem; margin: 0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; letter-spacing:-.01em; }
h2 { font-size:1rem; margin:2.5rem 0 .75rem; font-weight:600; }
p { margin:.5rem 0; }
.dim { color:var(--dim); }
.small { font-size:.875rem; }
code, .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.875em; }
form { display:flex; gap:.5rem; margin:1.5rem 0 .5rem; flex-wrap:wrap; }
input[type=url] { flex:1 1 22rem; padding:.7rem .85rem; border:1px solid var(--line);
  border-radius:8px; background:var(--card); color:var(--fg); font-size:1rem; }
button { padding:.7rem 1.4rem; border:0; border-radius:8px; background:var(--fg);
  color:var(--bg); font-size:1rem; font-weight:600; cursor:pointer; }
button:hover { opacity:.85; }
table { width:100%; border-collapse:collapse; margin:.5rem 0; font-size:.9375rem; }
th { text-align:left; font-weight:600; color:var(--dim); font-size:.8125rem;
  text-transform:uppercase; letter-spacing:.04em; padding:.5rem .6rem; }
td { padding:.55rem .6rem; border-top:1px solid var(--line); vertical-align:top; }
.tag { display:inline-block; padding:.1rem .45rem; border-radius:5px; font-size:.75rem;
  font-weight:700; color:#fff; letter-spacing:.02em; }
.grade { display:inline-flex; align-items:center; justify-content:center; width:3.5rem;
  height:3.5rem; border-radius:12px; color:#fff; font-size:1.75rem; font-weight:700; }
.head { display:flex; gap:1rem; align-items:center; margin:1.5rem 0 .5rem; }
.card { border:1px solid var(--line); border-radius:10px; padding:1rem 1.15rem;
  background:var(--card); margin:1rem 0; }
.err { border-color:#c92a2a; }
.wrap { overflow-x:auto; }
a { color:inherit; }
ul { margin:.5rem 0; padding-left:1.25rem; }
li { margin:.3rem 0; }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def home(error: str | None = None, prefill: str = "") -> str:
    err = f'<div class="card err"><p>{escape(error)}</p></div>' if error else ""
    return _page(
        "counterpart scan",
        f"""
<h1>Scan an A2A agent</h1>
<p class="dim">Paste an agent's base URL. It gets checked against the
<a href="https://a2a-protocol.org/">A2A</a> v1.0 spec, section by section, then probed with
deliberately malformed and hostile requests. Nothing is stored except the result, for a day.</p>
{err}
<form method=post action=/scan>
  <input type=url name=url placeholder="https://my-agent.example.com" value="{escape(prefill)}"
         required autofocus spellcheck=false>
  <button type=submit>Scan</button>
</form>
<p class="dim small">Reads only. It sends requests an ordinary client would, plus adversarial
probes, and it never sends credentials. Only public addresses can be scanned.</p>
<h2>What this does not tell you</h2>
<p class="small dim">A grade here means the agent speaks the protocol correctly and did not fall
for the probes. It says nothing about whether its <em>answers</em> are any good, which is the
failure that actually costs money: a task can reach <code>completed</code> and still carry a
useless result. That part needs a contract you write against your own domain, which is what
<a href="https://github.com/edrisibra/counterpart">counterpart</a> is for.</p>
""",
    )


def _rows(scan: Scan) -> str:
    checks = "".join(
        f"<tr><td class=mono>{escape(c['id'])}</td>"
        f"<td><span class=tag style=\"background:{_STATUS_COLOUR.get(c['status'], '#868e96')}\">"
        f"{escape(c['status'].upper())}</span></td>"
        f"<td class=mono>{escape(c['spec_section'])}</td>"
        f"<td class=dim>{escape(c['detail'])}</td></tr>"
        for c in scan.checks
    )
    attacks = "".join(
        f"<tr><td class=mono>{escape(a['id'])}</td>"
        f"<td>{escape(a['technique'])}</td>"
        f"<td><span class=tag style=\"background:{_FLAG_COLOUR.get(a['flag'], '#868e96')}\">"
        f"{escape(a['flag'])}</span></td>"
        f"<td class=dim>{escape(a['observation'])}</td></tr>"
        for a in scan.attacks
    )
    out = ""
    if checks:
        out += (
            "<h2>Spec checks</h2><div class=wrap><table><thead><tr><th>Check</th><th>Result</th>"
            f"<th>Spec section</th><th>Detail</th></tr></thead><tbody>{checks}</tbody></table></div>"
        )
    if attacks:
        out += (
            "<h2>Adversarial probes</h2><div class=wrap><table><thead><tr><th>Probe</th>"
            "<th>Technique</th><th>Result</th><th>Observation</th></tr></thead>"
            f"<tbody>{attacks}</tbody></table></div>"
        )
    return out


def report(scan: Scan, base: str) -> str:
    colour = _GRADE_COLOUR[scan.grade]
    badge_md = f"[![A2A scan]({base}/badge/{scan.id}.svg)]({base}/report/{scan.id})"
    body = f"""
<h1>Scan result</h1>
<p class="mono dim">{escape(scan.url)}</p>
<div class=head>
  <span class=grade style="background:{colour}">{escape(scan.grade)}</span>
  <div><strong>{escape(scan.summary)}</strong>
  <div class="dim small">{len(scan.checks)} spec checks, {len(scan.attacks)} probes</div></div>
</div>
{'<div class="card err"><p>' + escape(scan.error) + "</p></div>" if scan.error else ""}
{_rows(scan)}
<h2>Badge</h2>
<p class="small dim">Paste this into your README:</p>
<div class=card><code>{escape(badge_md)}</code></div>
<h2>The part a scan cannot reach</h2>
<p class="small dim">Everything above is about the protocol. It does not check whether what this
agent <em>returns</em> is usable, because only you know what a usable answer looks like for your
domain. If you delegate work to this agent, write a contract for its replies.
<a href="https://github.com/edrisibra/counterpart">counterpart</a> does that in a pytest fixture.</p>
<p class="small"><a href="/">Scan another agent</a></p>
"""
    return _page(f"Scan result: {scan.grade}", body)


def badge(scan: Scan) -> str:
    """A shields-style SVG. Self-contained, no external fetch."""
    label, value = "A2A scan", scan.grade
    colour = _GRADE_COLOUR[scan.grade]
    lw, vw = 62, 26
    total = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" \
role="img" aria-label="{label}: {value}">
<title>{label}: {value}</title>
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
<stop offset="1" stop-opacity=".1"/></linearGradient>
<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>
<g clip-path="url(#r)">
<rect width="{lw}" height="20" fill="#555"/>
<rect x="{lw}" width="{vw}" height="20" fill="{colour}"/>
<rect width="{total}" height="20" fill="url(#s)"/></g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" \
font-size="11">
<text x="{lw / 2}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
<text x="{lw / 2}" y="14">{label}</text>
<text x="{lw + vw / 2}" y="15" fill="#010101" fill-opacity=".3">{value}</text>
<text x="{lw + vw / 2}" y="14">{value}</text></g></svg>"""
