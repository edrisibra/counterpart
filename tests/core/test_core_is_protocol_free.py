"""Enforce the one hard architecture rule: counterpart.core imports no protocol code.

If A2A adoption stalls or a competing protocol wins, the engine must survive with a new
adapter. This test fails the moment someone imports an adapter (or a protocol dependency)
from within core/ — catching the drift before it becomes load-bearing.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
CORE_DIR = SRC / "counterpart" / "core"

# core/ may import its own submodules (counterpart.core.*) and third-party libs like
# pydantic, but never a sibling first-party subpackage (adapters/, personas/, ...) nor an
# external protocol/transport dependency.
FORBIDDEN_TOP_LEVEL = frozenset(
    {"a2a", "a2a_sdk", "fasta2a", "starlette", "httpx", "uvicorn", "fastapi", "grpc"}
)


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _classify(module: str) -> str | None:
    """Return a reason string if ``module`` is a forbidden import inside core/, else None."""
    if module.startswith("counterpart."):
        if not module.startswith("counterpart.core"):
            return f"first-party non-core package {module!r}"
        return None
    top = module.split(".")[0]
    if top in FORBIDDEN_TOP_LEVEL:
        return f"protocol/transport dependency {module!r}"
    return None


def test_core_has_no_protocol_imports() -> None:
    offenders: list[str] = []
    for path in CORE_DIR.rglob("*.py"):
        for module in _imported_modules(path.read_text()):
            reason = _classify(module)
            if reason is not None:
                offenders.append(f"{path.name} imports {reason}")
    assert not offenders, "core/ must stay protocol-agnostic, but found: " + "; ".join(offenders)


def test_core_dir_actually_scanned() -> None:
    # Guard against the test silently passing because it scanned nothing.
    assert (CORE_DIR / "contract.py").exists()
    assert list(CORE_DIR.rglob("*.py"))
