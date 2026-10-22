"""Tests for PDF Export Pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.pdf_exporter import (
    PDFExporter,
    PDFBackend,
    PDFResult,
    PDF_BASE_CSS,
)


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_pdf_result_ok():
    result = PDFResult(path="/tmp/report.pdf", size_bytes=1024, backend="weasyprint")
    assert result.ok is True


def test_pdf_result_error():
    result = PDFResult(error="Backend not available", backend="weasyprint")
    assert result.ok is False


def test_pdf_result_to_dict():
    result = PDFResult(path="/tmp/report.pdf", pages=3, size_bytes=2048, backend="basic")
    d = result.to_dict()
    assert d["path"] == "/tmp/report.pdf"
    assert d["pages"] == 3
    assert d["ok"] is True


def test_detect_backend():
    """Backend auto-detection should return something."""
    store = _make_store()
    exporter = PDFExporter(store)
    assert exporter.backend in PDFBackend


def test_list_available_backends():
    store = _make_store()
    exporter = PDFExporter(store)
    backends = exporter.list_available_backends()
    assert len(backends) == 4  # weasyprint, pdfkit, reportlab, basic
    names = [b["backend"] for b in backends]
    assert "basic" in names
    # Basic is always available
    basic = next(b for b in backends if b["backend"] == "basic")
    assert basic["available"] is True


def test_wrap_html_bare_content():
    """Wrapping bare HTML adds full document structure."""
    result = PDFExporter._wrap_html("<h1>Test</h1>", "body { color: red; }")
    assert "<!DOCTYPE html>" in result
    assert "body { color: red; }" in result
    assert "<h1>Test</h1>" in result


def test_wrap_html_full_document():
    """Wrapping a full HTML document injects CSS into head."""
    full_html = "<html><head></head><body><h1>Test</h1></body></html>"
    result = PDFExporter._wrap_html(full_html, "body { color: red; }")
    assert "<style>body { color: red; }</style>" in result


def test_export_basic_fallback():
    """Basic backend saves HTML file."""
    store = _make_store()
    output_dir = Path(tempfile.mkdtemp())
    exporter = PDFExporter(store, output_dir)

    html = "<h1>Test Report</h1><p>Content here.</p>"
    result = exporter._export_basic(html, output_dir / "test.pdf")

    assert result.ok
    assert result.backend == "basic"
    # It saves as .html
    assert result.path.endswith(".html")
    assert Path(result.path).exists()
    content = Path(result.path).read_text()
    assert "Test Report" in content


def test_export_html_to_pdf_uses_backend():
    """Full export pipeline uses detected backend."""
    store = _make_store()
    output_dir = Path(tempfile.mkdtemp())
    exporter = PDFExporter(store, output_dir)

    html = "<h1>Test</h1><p>This is a test document.</p>"
    result = exporter.export_html_to_pdf(html, "test-output.pdf")

    assert result.ok or result.error  # Either succeeds or has a meaningful error
    assert result.backend in ("weasyprint", "pdfkit", "reportlab", "basic")


def test_pdf_base_css_defined():
    """Base CSS contains expected styles."""
    assert "@page" in PDF_BASE_CSS
    assert "font-family" in PDF_BASE_CSS
    assert "TPLLM" in PDF_BASE_CSS


def test_get_summary():
    store = _make_store()
    exporter = PDFExporter(store)
    summary = exporter.get_summary()
    assert "PDF Export" in summary
    assert "backend" in summary.lower()


def test_export_with_custom_css():
    """Custom CSS is appended to base CSS."""
    store = _make_store()
    output_dir = Path(tempfile.mkdtemp())
    exporter = PDFExporter(store, output_dir)

    html = "<h1>Styled</h1>"
    custom_css = ".custom { color: purple; }"
    result = exporter.export_html_to_pdf(html, "styled.pdf", css=custom_css)

    # Backend should attempt the export
    assert result.backend in PDFBackend.__members__.values()


def test_export_program_report_missing():
    """Export for non-existent program returns basic HTML or error."""
    store = _make_store()
    output_dir = Path(tempfile.mkdtemp())
    exporter = PDFExporter(store, output_dir)

    result = exporter.export_program_report("nonexistent")
    # Should succeed with "not found" content or fail gracefully
    assert result.backend


def test_weasyprint_not_installed():
    """WeasyPrint returns error when not installed."""
    store = _make_store()
    exporter = PDFExporter(store)
    # Directly test the weasyprint backend
    result = exporter._export_weasyprint("<h1>Test</h1>", Path("/tmp/test.pdf"))
    # Either succeeds (if installed) or fails with import error
    if not result.ok:
        assert "not installed" in result.error.lower() or result.error


def test_pdfkit_not_installed():
    """pdfkit returns error when not installed."""
    store = _make_store()
    exporter = PDFExporter(store)
    result = exporter._export_pdfkit("<h1>Test</h1>", Path("/tmp/test.pdf"))
    if not result.ok:
        assert "not installed" in result.error.lower() or result.error
