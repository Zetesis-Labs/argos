from pathlib import Path

from argos.devtools.spec_check import compare, scan_test_file


def test_only_first_docstring_line_anchors_a_test() -> None:
    """S01.9 solo la primera línea del docstring ancla un test."""
    source = '''
# S01.1 no cuenta desde un comentario.
def test_without_docstring():
    pass

def test_reference_on_second_line():
    """Descripción.
    S01.2 tampoco cuenta aquí.
    """

def test_anchored():
    """S01.3 esta referencia sí cuenta."""
'''

    scan = scan_test_file(source, path=Path("tests/test_example.py"))

    assert scan.references == {"S01.3"}
    assert scan.tests_without_reference == [
        "tests/test_example.py::test_reference_on_second_line",
        "tests/test_example.py::test_without_docstring",
    ]


def test_unanchored_tests_make_report_fail() -> None:
    """S01.9 los tests sin anclaje y las referencias desconocidas hacen fallar el informe."""
    report = compare(
        {"S01.1"},
        {"S99.1"},
        tests_without_reference=["tests/test_example.py::test_missing"],
    )

    assert not report.ok
    assert report.cases_without_test == ["S01.1"]
    assert report.unknown_references == ["S99.1"]
    assert report.tests_without_reference == ["tests/test_example.py::test_missing"]
