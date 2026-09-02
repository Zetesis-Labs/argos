"""Anclaje spec ↔ tests: cada caso Sxx.n tiene test y cada test cita un caso real."""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CASE_AT_START_RE = re.compile(r"^(S\d{2}\.\d+)\b")
HEADING_RE = re.compile(r"^##\s+(S\d{2}\.\d+)\b", re.MULTILINE)


@dataclass(frozen=True)
class TestScan:
    references: set[str]
    tests_without_reference: list[str]


@dataclass(frozen=True)
class Report:
    cases_without_test: list[str]
    unknown_references: list[str]
    tests_without_reference: list[str]

    @property
    def ok(self) -> bool:
        return not (
            self.cases_without_test or self.unknown_references or self.tests_without_reference
        )


def cases_in_spec(text: str) -> set[str]:
    return set(HEADING_RE.findall(text))


def scan_test_file(text: str, *, path: Path) -> TestScan:
    tree = ast.parse(text, filename=str(path))
    references: set[str] = set()
    tests_without_reference: list[str] = []
    test_nodes = (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )
    for node in test_nodes:
        docstring = ast.get_docstring(node, clean=False)
        first_line = docstring.splitlines()[0] if docstring else ""
        match = CASE_AT_START_RE.match(first_line)
        if match:
            references.add(match.group(1))
        else:
            tests_without_reference.append(f"{path.as_posix()}::{node.name}")
    return TestScan(
        references=references,
        tests_without_reference=sorted(tests_without_reference),
    )


def compare(
    spec_cases: set[str],
    test_references: set[str],
    *,
    tests_without_reference: list[str] | None = None,
) -> Report:
    return Report(
        cases_without_test=sorted(spec_cases - test_references, key=case_sort_key),
        unknown_references=sorted(test_references - spec_cases, key=case_sort_key),
        tests_without_reference=sorted(tests_without_reference or []),
    )


def case_sort_key(case: str) -> tuple[int, int]:
    spec, number = case[1:].split(".")
    return (int(spec), int(number))


def collect_spec_cases(specs_dir: Path) -> set[str]:
    cases: set[str] = set()
    for path in sorted(specs_dir.glob("S[0-9][0-9]-*.md")):
        cases |= cases_in_spec(path.read_text(encoding="utf-8"))
    return cases


def collect_test_scan(tests_dir: Path) -> TestScan:
    references: set[str] = set()
    tests_without_reference: list[str] = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        scan = scan_test_file(path.read_text(encoding="utf-8"), path=path)
        references |= scan.references
        tests_without_reference.extend(scan.tests_without_reference)
    return TestScan(
        references=references,
        tests_without_reference=sorted(tests_without_reference),
    )


def render(report: Report) -> str:
    if report.ok:
        return "spec-check: todos los casos tienen test y todas las referencias existen\n"
    lines: list[str] = []
    if report.cases_without_test:
        lines.append("Casos sin test:")
        lines.extend(f"  {case}" for case in report.cases_without_test)
    if report.unknown_references:
        lines.append("Tests que citan casos inexistentes:")
        lines.extend(f"  {case}" for case in report.unknown_references)
    if report.tests_without_reference:
        lines.append("Tests sin caso al inicio del docstring:")
        lines.extend(f"  {test}" for test in report.tests_without_reference)
    return "\n".join(lines) + "\n"


def main() -> None:
    root = Path.cwd()
    test_scan = collect_test_scan(root / "tests")
    report = compare(
        collect_spec_cases(root / "specs"),
        test_scan.references,
        tests_without_reference=test_scan.tests_without_reference,
    )
    sys.stdout.write(render(report))
    sys.exit(0 if report.ok else 1)
