"""PDF export pipeline — convert TPLLM documents to PDF.

Supports multiple backends:
- weasyprint (high-fidelity HTML-to-PDF, recommended)
- pdfkit/wkhtmltopdf (alternative)
- reportlab (pure Python, no external deps)
- basic (HTML file output as fallback)

All export methods accept the HTML output from DocumentGenerator and
produce a styled PDF with consistent TPLLM branding.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from tpllm.artifacts.store import ArtifactStore


class PDFBackend(str, Enum):
    WEASYPRINT = "weasyprint"
    PDFKIT = "pdfkit"
    REPORTLAB = "reportlab"
    BASIC = "basic"  # saves HTML, no true PDF


@dataclass
class PDFResult:
    """Result of a PDF export operation."""
    path: str = ""
    pages: int = 0
    size_bytes: int = 0
    backend: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "pages": self.pages,
            "size_bytes": self.size_bytes,
            "backend": self.backend,
            "ok": self.ok,
            "error": self.error,
        }


# Base CSS applied to all PDF exports
PDF_BASE_CSS = """
@page {
    size: A4;
    margin: 2cm;
    @bottom-center {
        content: "TPLLM — AI Technical Program Manager | Page " counter(page) " of " counter(pages);
        font-size: 8pt;
        color: #94a3b8;
    }
}
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1e293b;
}
h1 { color: #0f172a; border-bottom: 2px solid #2563eb; padding-bottom: 8px; font-size: 18pt; }
h2 { color: #334155; margin-top: 20px; font-size: 14pt; }
h3 { color: #475569; font-size: 12pt; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; }
th { background: #f1f5f9; color: #334155; padding: 8px; text-align: left; border: 1px solid #e2e8f0; }
td { padding: 6px 8px; border: 1px solid #e2e8f0; }
tr:nth-child(even) { background: #f8fafc; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 9pt; font-weight: bold; }
.badge-green { background: #dcfce7; color: #166534; }
.badge-yellow { background: #fef9c3; color: #854d0e; }
.badge-red { background: #fee2e2; color: #991b1b; }
pre { background: #f8fafc; padding: 12px; border-radius: 4px; font-size: 9pt; overflow-x: auto; }
"""


class PDFExporter:
    """HTML-to-PDF conversion pipeline for TPLLM documents."""

    def __init__(self, store: ArtifactStore, output_dir: Path | None = None) -> None:
        self._store = store
        self._output_dir = output_dir or Path.cwd()
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._backend = self._detect_backend()

    @property
    def backend(self) -> PDFBackend:
        return self._backend

    def export_html_to_pdf(
        self,
        html: str,
        filename: str,
        css: str = "",
    ) -> PDFResult:
        """Convert HTML string to PDF.

        Args:
            html: Full HTML content (from DocumentGenerator).
            filename: Output filename (without path).
            css: Additional CSS (appended to base CSS).
        """
        output_path = self._output_dir / filename

        full_css = PDF_BASE_CSS + (css or "")
        full_html = self._wrap_html(html, full_css)

        if self._backend == PDFBackend.WEASYPRINT:
            return self._export_weasyprint(full_html, output_path)
        elif self._backend == PDFBackend.PDFKIT:
            return self._export_pdfkit(full_html, output_path)
        elif self._backend == PDFBackend.REPORTLAB:
            return self._export_reportlab(html, output_path)
        else:
            return self._export_basic(full_html, output_path)

    def export_program_report(
        self,
        program_id: str,
        filename: str = "",
    ) -> PDFResult:
        """Generate and export a full program report as PDF."""
        from tpllm.workflows.document_generator import DocumentGenerator

        gen = DocumentGenerator(self._store)
        html = gen.render_full_report(program_id, "html")

        if not filename:
            filename = f"tpllm-report-{program_id}.pdf"

        return self.export_html_to_pdf(html, filename)

    def export_program_brief(self, program_id: str, filename: str = "") -> PDFResult:
        """Export a program brief as PDF."""
        from tpllm.workflows.document_generator import DocumentGenerator

        gen = DocumentGenerator(self._store)
        html = gen.render_program_brief(program_id, "html")

        if not filename:
            filename = f"tpllm-brief-{program_id}.pdf"

        return self.export_html_to_pdf(html, filename)

    def export_raid_log(self, program_id: str, filename: str = "") -> PDFResult:
        """Export a RAID log as PDF."""
        from tpllm.workflows.document_generator import DocumentGenerator

        gen = DocumentGenerator(self._store)
        html = gen.render_raid_log(program_id, "html")

        if not filename:
            filename = f"tpllm-raid-{program_id}.pdf"

        return self.export_html_to_pdf(html, filename)

    def export_milestone_plan(self, program_id: str, filename: str = "") -> PDFResult:
        """Export milestone plan as PDF."""
        from tpllm.workflows.document_generator import DocumentGenerator

        gen = DocumentGenerator(self._store)
        html = gen.render_milestone_plan(program_id, "html")

        if not filename:
            filename = f"tpllm-milestones-{program_id}.pdf"

        return self.export_html_to_pdf(html, filename)

    def list_available_backends(self) -> list[dict[str, Any]]:
        """List PDF backends and their availability."""
        backends = []

        try:
            import weasyprint  # noqa: F401
            backends.append({"backend": "weasyprint", "available": True, "recommended": True})
        except ImportError:
            backends.append({"backend": "weasyprint", "available": False, "recommended": True,
                             "install": "pip install weasyprint"})

        try:
            import pdfkit  # noqa: F401
            backends.append({"backend": "pdfkit", "available": True})
        except ImportError:
            backends.append({"backend": "pdfkit", "available": False,
                             "install": "pip install pdfkit (requires wkhtmltopdf)"})

        try:
            import reportlab  # noqa: F401
            backends.append({"backend": "reportlab", "available": True})
        except ImportError:
            backends.append({"backend": "reportlab", "available": False,
                             "install": "pip install reportlab"})

        backends.append({"backend": "basic", "available": True,
                         "note": "Saves HTML file (no true PDF conversion)"})

        return backends

    def get_summary(self) -> str:
        """Get exporter status."""
        backends = self.list_available_backends()
        available = [b["backend"] for b in backends if b.get("available")]

        lines = [
            "## PDF Export",
            f"- Active backend: {self._backend.value}",
            f"- Available: {', '.join(available)}",
            f"- Output directory: {self._output_dir}",
        ]
        return "\n".join(lines)

    # ── Backend Implementations ───────────────────────────

    def _export_weasyprint(self, html: str, output_path: Path) -> PDFResult:
        """Export using WeasyPrint."""
        try:
            import weasyprint

            doc = weasyprint.HTML(string=html)
            doc.write_pdf(str(output_path))

            size = output_path.stat().st_size
            return PDFResult(
                path=str(output_path),
                size_bytes=size,
                backend="weasyprint",
            )
        except ImportError:
            return PDFResult(error="weasyprint not installed", backend="weasyprint")
        except Exception as e:
            return PDFResult(error=str(e), backend="weasyprint")

    def _export_pdfkit(self, html: str, output_path: Path) -> PDFResult:
        """Export using pdfkit (wkhtmltopdf)."""
        try:
            import pdfkit

            pdfkit.from_string(html, str(output_path), options={
                "page-size": "A4",
                "margin-top": "20mm",
                "margin-bottom": "20mm",
                "margin-left": "15mm",
                "margin-right": "15mm",
                "encoding": "UTF-8",
            })

            size = output_path.stat().st_size
            return PDFResult(
                path=str(output_path),
                size_bytes=size,
                backend="pdfkit",
            )
        except ImportError:
            return PDFResult(error="pdfkit not installed", backend="pdfkit")
        except Exception as e:
            return PDFResult(error=str(e), backend="pdfkit")

    def _export_reportlab(self, html: str, output_path: Path) -> PDFResult:
        """Export using ReportLab (renders text, not full HTML)."""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

            doc = SimpleDocTemplate(str(output_path), pagesize=A4)
            styles = getSampleStyleSheet()
            story = []

            # Simple HTML-to-reportlab conversion
            import re
            text = re.sub(r"<[^>]+>", "", html)
            paragraphs = text.split("\n")

            for p in paragraphs:
                p = p.strip()
                if p:
                    story.append(Paragraph(p, styles["Normal"]))
                    story.append(Spacer(1, 6))

            doc.build(story)

            size = output_path.stat().st_size
            return PDFResult(
                path=str(output_path),
                size_bytes=size,
                backend="reportlab",
            )
        except ImportError:
            return PDFResult(error="reportlab not installed", backend="reportlab")
        except Exception as e:
            return PDFResult(error=str(e), backend="reportlab")

    def _export_basic(self, html: str, output_path: Path) -> PDFResult:
        """Fallback: save HTML file (rename to .html)."""
        html_path = output_path.with_suffix(".html")
        html_path.write_text(html, encoding="utf-8")

        return PDFResult(
            path=str(html_path),
            size_bytes=html_path.stat().st_size,
            backend="basic",
        )

    # ── Utility ───────────────────────────────────────────

    @staticmethod
    def _wrap_html(content: str, css: str) -> str:
        """Wrap HTML content with a full document structure."""
        if "<html" in content.lower():
            # Already a full HTML document; inject CSS
            return content.replace("</head>", f"<style>{css}</style></head>")

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>TPLLM Report</title>
    <style>{css}</style>
</head>
<body>
{content}
</body>
</html>"""

    def _detect_backend(self) -> PDFBackend:
        """Auto-detect the best available PDF backend."""
        try:
            import weasyprint  # noqa: F401
            return PDFBackend.WEASYPRINT
        except ImportError:
            pass

        try:
            import pdfkit  # noqa: F401
            return PDFBackend.PDFKIT
        except ImportError:
            pass

        try:
            import reportlab  # noqa: F401
            return PDFBackend.REPORTLAB
        except ImportError:
            pass

        return PDFBackend.BASIC
