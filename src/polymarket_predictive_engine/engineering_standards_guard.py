"""Static S2/S4 guard inventory used by the required PR gate.

The scanner is intentionally syntax based and offline.  It does not decide that a
direct write is safe merely because similar code was previously reviewed: every
call site and call count must match the review manifest.  The same rule applies to
HTTP/websocket ingestion sites and their recorded-reality fixture contracts.
"""

from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


WRITE_METHODS = {"write_text", "write_bytes"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "request"}
WRITE_CLASSIFICATIONS = {
    "append_only",
    "archive_staging",
    "atomic_helper",
    "atomic_staging",
    "exclusive_lock",
    "restore_target",
}


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _dotted(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    return ""


def _literal_string(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _call_mode(call: ast.Call, primitive: str) -> str:
    if primitive == "os.open":
        return "flags"
    positional = 1 if primitive in {"open", "gzip.open", "tarfile.open"} else 0
    if len(call.args) > positional:
        mode = _literal_string(call.args[positional])
        if mode:
            return mode
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode = _literal_string(keyword.value)
            if mode:
                return mode
    return ""


def _is_write_open(call: ast.Call, primitive: str) -> tuple[bool, str]:
    mode = _call_mode(call, primitive)
    if primitive == "os.open":
        flags = ast.unparse(call.args[1]) if len(call.args) > 1 else ""
        return any(flag in flags for flag in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_EXCL")), mode
    if not mode:
        return False, mode
    return any(flag in mode for flag in ("w", "a", "x", "+")), mode


class _SiteVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path.replace("\\", "/")
        self.scope: list[str] = []
        self.direct_writes: Counter[str] = Counter()
        self.external_ingestion: Counter[str] = Counter()

    @property
    def qualname(self) -> str:
        return ".".join(self.scope) if self.scope else "<module>"

    def _scoped(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast API
        self._scoped(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast API
        self._scoped(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast API
        self._scoped(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast API
        dotted = _dotted(node.func)
        primitive = ""
        mode = ""
        if dotted.rsplit(".", 1)[-1] in WRITE_METHODS:
            primitive = dotted.rsplit(".", 1)[-1]
        elif dotted in {"open", "gzip.open", "tarfile.open", "os.open"} or dotted.endswith(".open"):
            candidate = dotted if dotted in {"open", "gzip.open", "tarfile.open", "os.open"} else "path.open"
            is_write, mode = _is_write_open(node, candidate)
            if is_write:
                primitive = candidate
        if primitive:
            suffix = f":{mode}" if mode else ""
            self.direct_writes[f"{self.relative_path}:{self.qualname}:{primitive}{suffix}"] += 1

        external = ""
        if dotted.startswith("requests.") and dotted.rsplit(".", 1)[-1] in HTTP_METHODS:
            external = dotted
        elif dotted == "urllib.request.urlopen":
            external = dotted
        elif dotted.endswith(".connect") and dotted.split(".", 1)[0] in {"websocket", "websockets"}:
            external = dotted
        else:
            base, _, method = dotted.rpartition(".")
            if method in HTTP_METHODS and "session" in base.lower():
                external = f"session.{method}"
        if external:
            self.external_ingestion[f"{self.relative_path}:{self.qualname}:{external}"] += 1
        self.generic_visit(node)


def _scan(package_root: Path) -> tuple[Counter[str], Counter[str]]:
    writes: Counter[str] = Counter()
    external: Counter[str] = Counter()
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        visitor = _SiteVisitor(relative)
        visitor.visit(tree)
        writes.update(visitor.direct_writes)
        external.update(visitor.external_ingestion)
    return writes, external


def scan_direct_writes(package_root: str | Path) -> Counter[str]:
    return _scan(Path(package_root))[0]


def scan_external_ingestion(package_root: str | Path) -> Counter[str]:
    return _scan(Path(package_root))[1]


def load_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("engineering guard manifest must be a JSON object")
    return payload


def _expected_counts(entries: Mapping[str, Any]) -> Counter[str]:
    return Counter({site: int(metadata.get("count", 0)) for site, metadata in entries.items()})


def _counter_errors(label: str, actual: Counter[str], expected: Counter[str]) -> list[str]:
    errors: list[str] = []
    for site in sorted(actual.keys() | expected.keys()):
        if actual[site] != expected[site]:
            errors.append(f"{label} site mismatch: {site}: actual={actual[site]} expected={expected[site]}")
    return errors


def audit_s2(package_root: str | Path, manifest: Mapping[str, Any]) -> list[str]:
    entries = manifest.get("s2_direct_writes")
    if not isinstance(entries, Mapping):
        return ["manifest.s2_direct_writes is missing or malformed"]
    errors = _counter_errors("S2", scan_direct_writes(package_root), _expected_counts(entries))
    required = {"count", "classification", "artifact", "cadence", "interleaving"}
    for site, raw in entries.items():
        metadata = raw if isinstance(raw, Mapping) else {}
        missing = sorted(required - metadata.keys())
        if missing:
            errors.append(f"S2 manifest metadata missing for {site}: {missing}")
        if metadata.get("classification") not in WRITE_CLASSIFICATIONS:
            errors.append(f"S2 manifest classification invalid for {site}: {metadata.get('classification')}")
        for field in ("artifact", "cadence", "interleaving"):
            if not str(metadata.get(field) or "").strip():
                errors.append(f"S2 manifest {field} empty for {site}")
    return errors


def _test_node_exists(root: Path, node_id: str) -> bool:
    file_name, separator, test_name = node_id.partition("::")
    if not separator or not test_name:
        return False
    test_path = root / file_name
    if not test_path.exists():
        return False
    tree = ast.parse(test_path.read_text(encoding="utf-8-sig"), filename=str(test_path))
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name for node in tree.body)


def audit_s4(
    package_root: str | Path,
    manifest: Mapping[str, Any],
    *,
    repository_root: str | Path,
) -> list[str]:
    entries = manifest.get("s4_external_ingestion")
    if not isinstance(entries, Mapping):
        return ["manifest.s4_external_ingestion is missing or malformed"]
    surfaces = manifest.get("s4_recorded_surfaces")
    if not isinstance(surfaces, Mapping):
        return ["manifest.s4_recorded_surfaces is missing or malformed"]
    errors = _counter_errors("S4", scan_external_ingestion(package_root), _expected_counts(entries))
    root = Path(repository_root)
    fixture_root = root / "tests" / "fixtures" / "recorded"
    readme = (fixture_root / "README.md").read_text(encoding="utf-8")
    used_surfaces: set[str] = set()
    for site, raw in entries.items():
        metadata = raw if isinstance(raw, Mapping) else {}
        missing = sorted({"count", "surface"} - metadata.keys())
        if missing:
            errors.append(f"S4 manifest metadata missing for {site}: {missing}")
        surface = str(metadata.get("surface") or "")
        if not surface:
            errors.append(f"S4 manifest surface empty for {site}")
        elif surface not in surfaces:
            errors.append(f"S4 manifest surface unknown for {site}: {surface}")
        else:
            used_surfaces.add(surface)
    unused = sorted(set(surfaces) - used_surfaces)
    if unused:
        errors.append(f"S4 recorded surfaces are stale/unreferenced: {unused}")

    required = {"fixtures", "replay_test", "fail_safe", "endpoint"}
    for surface in sorted(used_surfaces):
        raw = surfaces.get(surface)
        metadata = raw if isinstance(raw, Mapping) else {}
        missing = sorted(required - metadata.keys())
        if missing:
            errors.append(f"S4 surface metadata missing for {surface}: {missing}")
        fixtures = metadata.get("fixtures")
        if not isinstance(fixtures, list) or not fixtures:
            errors.append(f"S4 surface fixtures empty for {surface}")
        else:
            for fixture in fixtures:
                fixture_path = fixture_root / str(fixture)
                if not fixture_path.is_file():
                    errors.append(f"S4 recorded fixture missing for {surface}: {fixture}")
                if f"`{fixture}`" not in readme:
                    errors.append(f"S4 fixture provenance missing from README for {surface}: {fixture}")
        replay_test = str(metadata.get("replay_test") or "")
        if not _test_node_exists(root, replay_test):
            errors.append(f"S4 replay test missing for {surface}: {replay_test}")
        for field in ("endpoint", "fail_safe"):
            if not str(metadata.get(field) or "").strip():
                errors.append(f"S4 surface {field} empty for {surface}")
    return errors


def inventory(package_root: str | Path) -> dict[str, dict[str, int]]:
    writes, external = _scan(Path(package_root))
    return {
        "s2_direct_writes": dict(sorted(writes.items())),
        "s4_external_ingestion": dict(sorted(external.items())),
    }


def format_errors(errors: Iterable[str]) -> str:
    return "\n".join(f"- {error}" for error in errors)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Inventory S2/S4 engineering-standard call sites")
    parser.add_argument("package_root")
    arguments = parser.parse_args()
    print(json.dumps(inventory(arguments.package_root), indent=2, sort_keys=True))
