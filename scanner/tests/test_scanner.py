"""Tests for the scanner. The guard tests matter most: that is the security boundary."""

from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from counterpart_scanner.app import app
from counterpart_scanner.guard import UrlRejected, admit
from counterpart_scanner.scan import Scan


# --- admission control -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1",
        "https://127.0.0.1:8443",
        "http://[::1]",
        "http://0.0.0.0",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://100.100.100.200/",  # Alibaba metadata
        "http://10.0.0.5",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://[fd00::1]",  # IPv6 unique local
        "http://[::ffff:127.0.0.1]",  # IPv4-mapped loopback
        "http://100.64.0.1",  # carrier-grade NAT
        "http://something.internal",
        "http://box.local",
        "file:///etc/passwd",
        "gopher://example.com",
        "ftp://example.com",
        "http://user:pass@example.com",
        "",
        "   ",
        "http://",
    ],
)
def test_dangerous_urls_are_refused(url: str) -> None:
    with pytest.raises(UrlRejected):
        admit(url)


@pytest.mark.parametrize("port", [22, 3306, 5432, 6379, 27017])
def test_service_ports_are_refused(port: int) -> None:
    with pytest.raises(UrlRejected):
        admit(f"http://example.com:{port}")


def test_long_url_is_refused() -> None:
    with pytest.raises(UrlRejected):
        admit("https://example.com/" + "a" * 3000)


def test_a_public_host_is_admitted() -> None:
    target = admit("example.com")
    assert target.url == "https://example.com"
    assert target.port == 443
    assert target.addresses


def test_url_is_rebuilt_not_echoed() -> None:
    """A crafted URL must not survive intact into the fetch."""
    target = admit("https://EXAMPLE.com:443/agent/")
    assert target.host == "example.com"
    assert "EXAMPLE" not in target.url
    assert not target.url.endswith("/")


# --- grading -----------------------------------------------------------------------


def _scan(passed: int, failed: int = 0, concerning: int = 0) -> Scan:
    s = Scan(id=f"t{concerning}{passed}{failed}", url="https://a.example", created_at=time.time())
    s.checks = [{"id": f"p{i}", "status": "pass", "spec_section": "1", "detail": ""} for i in range(passed)]
    s.checks += [{"id": f"f{i}", "status": "fail", "spec_section": "1", "detail": ""} for i in range(failed)]
    s.attacks = [
        {"id": f"a{i}", "technique": "t", "flag": "obeyed", "observation": ""} for i in range(concerning)
    ]
    return s


def test_all_passing_is_an_a() -> None:
    assert _scan(passed=10).grade == "A"


def test_a_single_failure_drops_the_grade() -> None:
    assert _scan(passed=9, failed=1).grade == "B"


def test_an_obeyed_probe_downgrades_a_perfect_conformance_score() -> None:
    """A green badge on an agent that obeyed an injection would be a lie."""
    assert _scan(passed=10).grade == "A"
    assert _scan(passed=10, concerning=1).grade == "B"


def test_an_errored_scan_has_no_grade() -> None:
    s = _scan(passed=0)
    s.error = "unreachable"
    assert s.grade == "?"


# --- routes ------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_home_renders(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Scan an A2A agent" in r.text


def test_security_headers_are_set(client: TestClient) -> None:
    r = client.get("/")
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_posting_a_private_url_is_refused_without_scanning(client: TestClient) -> None:
    r = client.post("/scan", data={"url": "http://169.254.169.254/"})
    assert r.status_code == 400
    assert "not scannable" in r.text


def test_unknown_report_is_a_404(client: TestClient) -> None:
    assert client.get("/report/nope").status_code == 404
    assert client.get("/badge/nope.svg").status_code == 404


def test_api_rejects_a_private_url(client: TestClient) -> None:
    r = client.get("/api/scan", params={"url": "http://127.0.0.1"})
    assert r.status_code == 400
    assert "error" in r.json()


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"ok": True}


def test_report_and_badge_render_for_a_stored_scan(client: TestClient) -> None:
    from counterpart_scanner.app import _store

    s = _scan(passed=8, failed=1)
    _store.put(s)
    page = client.get(f"/report/{s.id}")
    assert page.status_code == 200
    assert "Scan result" in page.text
    assert "https://a.example" in page.text

    svg = client.get(f"/badge/{s.id}.svg")
    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in svg.text


def test_report_escapes_the_url(client: TestClient) -> None:
    """A URL is visitor input and lands in HTML, so it must be escaped."""
    from counterpart_scanner.app import _store

    s = _scan(passed=1)
    s.url = 'https://x.example/<script>alert("x")</script>'
    _store.put(s)
    page = client.get(f"/report/{s.id}")
    assert "<script>alert" not in page.text
    assert "&lt;script&gt;" in page.text
