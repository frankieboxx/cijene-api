"""
Email report module for crawl & import pipeline.

Sends a beautifully formatted MJML-based HTML email via Mailgun
after each crawl+import run.
"""

import logging
from dataclasses import dataclass, field
from datetime import date

import httpx
from mjml import mjml_to_html

from service.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ChainReport:
    """Report for a single chain's crawl + import results."""

    chain: str
    stores: int = 0
    products: int = 0
    prices: int = 0
    new_prices: int = 0
    crawl_time: float = 0.0
    error: str | None = None


@dataclass
class PipelineReport:
    """Full pipeline report for a single run."""

    run_date: date
    chains: list[ChainReport] = field(default_factory=list)
    crawl_total_seconds: float = 0.0
    import_total_seconds: float = 0.0
    import_error: str | None = None

    @property
    def total_stores(self) -> int:
        return sum(c.stores for c in self.chains)

    @property
    def total_products(self) -> int:
        return sum(c.products for c in self.chains)

    @property
    def total_prices(self) -> int:
        return sum(c.prices for c in self.chains)

    @property
    def total_new_prices(self) -> int:
        return sum(c.new_prices for c in self.chains)

    @property
    def has_errors(self) -> bool:
        return self.import_error is not None or any(
            c.error is not None for c in self.chains
        )

    def _fmt_number(self, n: int) -> str:
        """Format number with dot as thousands separator (Croatian style)."""
        return f"{n:,}".replace(",", ".")

    def to_subject(self) -> str:
        status = "GRESKE" if self.has_errors else "OK"
        return (
            f"[Cijene] {self.run_date} — "
            f"{self.total_stores} trgovina, "
            f"{self._fmt_number(self.total_new_prices)} novih cijena [{status}]"
        )

    def _build_chain_rows(self) -> str:
        """Build MJML table rows for each chain."""
        rows = []
        for i, c in enumerate(sorted(self.chains, key=lambda x: x.chain)):
            bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
            if c.error:
                rows.append(f"""
                <tr style="background-color: #fef2f2;">
                  <td style="padding: 10px 12px; font-weight: 600; color: #dc2626;">{c.chain}</td>
                  <td colspan="4" style="padding: 10px 12px; color: #dc2626; font-size: 13px;">
                    Greska: {c.error[:80]}
                  </td>
                </tr>""")
            else:
                rows.append(f"""
                <tr style="background-color: {bg};">
                  <td style="padding: 10px 12px; font-weight: 600; color: #1e293b;">{c.chain}</td>
                  <td style="padding: 10px 12px; text-align: right; color: #475569;">{c.stores}</td>
                  <td style="padding: 10px 12px; text-align: right; color: #475569;">{self._fmt_number(c.products)}</td>
                  <td style="padding: 10px 12px; text-align: right; color: #475569;">{self._fmt_number(c.prices)}</td>
                  <td style="padding: 10px 12px; text-align: right; color: #475569;">{self._fmt_number(c.new_prices)}</td>
                </tr>""")
        return "\n".join(rows)

    def _build_errors_section(self) -> str:
        """Build MJML error section if there are errors."""
        if not self.has_errors:
            return ""

        error_items = []
        if self.import_error:
            error_items.append(
                f'<li style="margin-bottom: 6px;">Import: {self.import_error}</li>'
            )
        for c in self.chains:
            if c.error:
                error_items.append(
                    f'<li style="margin-bottom: 6px;">{c.chain}: {c.error}</li>'
                )

        errors_html = "\n".join(error_items)

        return f"""
        <mj-section padding="0 20px">
          <mj-column>
            <mj-text padding="16px 20px" background-color="#fef2f2" border-radius="8px" font-size="14px" color="#991b1b" line-height="1.6">
              <p style="margin: 0 0 8px 0; font-weight: 700; font-size: 15px;">Greske</p>
              <ul style="margin: 0; padding-left: 20px;">
                {errors_html}
              </ul>
            </mj-text>
          </mj-column>
        </mj-section>
        """

    def to_html(self) -> str:
        """Render the report as a beautiful HTML email using MJML."""
        status_color = "#dc2626" if self.has_errors else "#16a34a"
        status_bg = "#fef2f2" if self.has_errors else "#f0fdf4"
        status_text = "GRESKE" if self.has_errors else "USPJESNO"
        status_icon = "&#10060;" if self.has_errors else "&#9989;"

        chain_rows = self._build_chain_rows()
        errors_section = self._build_errors_section()

        active_chains = sum(1 for c in self.chains if c.stores > 0)
        total_chains = len(self.chains)

        mjml_template = f"""
<mjml>
  <mj-head>
    <mj-attributes>
      <mj-all font-family="'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif" />
      <mj-text font-size="14px" color="#334155" line-height="1.5" />
    </mj-attributes>
    <mj-style>
      .stat-value {{ font-size: 28px; font-weight: 700; color: #0f172a; line-height: 1.2; }}
      .stat-label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }}
    </mj-style>
  </mj-head>
  <mj-body background-color="#f1f5f9">

    <!-- Header -->
    <mj-section background-color="#0f172a" padding="28px 20px 20px" border-radius="0">
      <mj-column>
        <mj-text align="center" font-size="24px" font-weight="700" color="#ffffff" padding="0">
          Cijene API
        </mj-text>
        <mj-text align="center" font-size="13px" color="#94a3b8" padding="4px 0 0 0">
          Dnevni izvjestaj crawlera — {self.run_date.strftime("%d.%m.%Y.")}
        </mj-text>
      </mj-column>
    </mj-section>

    <!-- Status badge -->
    <mj-section padding="20px 20px 0">
      <mj-column>
        <mj-text align="center" padding="14px 20px" background-color="{status_bg}" border-radius="8px" font-size="15px" font-weight="600" color="{status_color}">
          {status_icon} &nbsp; Status: {status_text}
        </mj-text>
      </mj-column>
    </mj-section>

    <!-- Summary stats -->
    <mj-section padding="20px 10px 0">
      <mj-column padding="0 10px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">{active_chains}/{total_chains}</div>
          <div class="stat-label">Lanaca</div>
        </mj-text>
      </mj-column>
      <mj-column padding="0 10px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">{self.total_stores}</div>
          <div class="stat-label">Trgovina</div>
        </mj-text>
      </mj-column>
      <mj-column padding="0 10px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">{self._fmt_number(self.total_new_prices)}</div>
          <div class="stat-label">Novih cijena</div>
        </mj-text>
      </mj-column>
    </mj-section>

    <mj-section padding="10px 10px 0">
      <mj-column padding="0 10px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">{self._fmt_number(self.total_products)}</div>
          <div class="stat-label">Proizvoda</div>
        </mj-text>
      </mj-column>
      <mj-column padding="0 10px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">{self._fmt_number(self.total_prices)}</div>
          <div class="stat-label">Ukupno cijena</div>
        </mj-text>
      </mj-column>
      <mj-column padding="0 10px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">{self.crawl_total_seconds:.0f}s + {self.import_total_seconds:.0f}s</div>
          <div class="stat-label">Crawl + Import</div>
        </mj-text>
      </mj-column>
    </mj-section>

    <!-- Chain details table -->
    <mj-section padding="20px 20px 0">
      <mj-column>
        <mj-text font-size="16px" font-weight="700" color="#0f172a" padding="0 0 12px 0">
          Detalji po lancu
        </mj-text>
        <mj-table cellpadding="0" cellspacing="0" width="100%" container-background-color="#ffffff" border="none" padding="0">
          <tr style="background-color: #0f172a; color: #ffffff;">
            <th style="padding: 12px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 8px 0 0 0;">Lanac</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Trgovine</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Proizvodi</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Cijene</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 0 8px 0 0;">Nove</th>
          </tr>
          {chain_rows}
          <tr style="background-color: #0f172a; color: #ffffff; font-weight: 600;">
            <td style="padding: 10px 12px; border-radius: 0 0 0 8px;">Ukupno</td>
            <td style="padding: 10px 12px; text-align: right;">{self.total_stores}</td>
            <td style="padding: 10px 12px; text-align: right;">{self._fmt_number(self.total_products)}</td>
            <td style="padding: 10px 12px; text-align: right;">{self._fmt_number(self.total_prices)}</td>
            <td style="padding: 10px 12px; text-align: right; border-radius: 0 0 8px 0;">{self._fmt_number(self.total_new_prices)}</td>
          </tr>
        </mj-table>
      </mj-column>
    </mj-section>

    {errors_section}

    <!-- Footer -->
    <mj-section padding="30px 20px 20px">
      <mj-column>
        <mj-divider border-color="#e2e8f0" border-width="1px" padding="0 0 20px 0" />
        <mj-text align="center" font-size="12px" color="#94a3b8" padding="0">
          Cijene API Crawler &bull; {settings.base_url}
        </mj-text>
        <mj-text align="center" font-size="11px" color="#cbd5e1" padding="4px 0 0 0">
          Ovaj email je automatski generiran nakon svakog dnevnog crawla.
        </mj-text>
      </mj-column>
    </mj-section>

  </mj-body>
</mjml>
"""
        result = mjml_to_html(mjml_template)
        if result.errors:
            for err in result.errors:
                logger.warning(f"MJML warning: {err}")
        return result.html

    def to_text(self) -> str:
        """Render the report as plain-text fallback."""
        lines: list[str] = []
        status = "GRESKE" if self.has_errors else "OK"
        lines.append(f"Cijene API — Dnevni izvjestaj ({self.run_date}) [{status}]")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"  Datum:        {self.run_date}")
        lines.append(f"  Lanaca:       {len(self.chains)}")
        lines.append(f"  Trgovina:     {self.total_stores}")
        lines.append(f"  Proizvoda:    {self._fmt_number(self.total_products)}")
        lines.append(f"  Cijena:       {self._fmt_number(self.total_prices)}")
        lines.append(f"  Novih cijena: {self._fmt_number(self.total_new_prices)}")
        lines.append(f"  Crawl:        {self.crawl_total_seconds:.0f}s")
        lines.append(f"  Import:       {self.import_total_seconds:.0f}s")
        lines.append("")
        lines.append(f"  {'Lanac':<12} {'Trg':>4} {'Proiz':>8} {'Cijene':>8} {'Nove':>8}")
        lines.append("  " + "-" * 44)
        for c in sorted(self.chains, key=lambda x: x.chain):
            if c.error:
                lines.append(f"  {c.chain:<12} GRESKA: {c.error}")
            else:
                lines.append(
                    f"  {c.chain:<12} {c.stores:>4} "
                    f"{self._fmt_number(c.products):>8} "
                    f"{self._fmt_number(c.prices):>8} "
                    f"{self._fmt_number(c.new_prices):>8}"
                )
        if self.has_errors:
            lines.append("")
            lines.append("GRESKE:")
            if self.import_error:
                lines.append(f"  Import: {self.import_error}")
            for c in self.chains:
                if c.error:
                    lines.append(f"  {c.chain}: {c.error}")
        lines.append("")
        lines.append(f"— Cijene API Crawler | {settings.base_url}")
        return "\n".join(lines)


def send_report(report: PipelineReport) -> bool:
    """
    Send the pipeline report via Mailgun (HTML + plain-text).

    Returns True if email was sent successfully.
    """
    api_key = settings.mailgun_api_key
    domain = settings.mailgun_domain
    recipients = settings.report_recipients

    if not api_key or not domain or not recipients:
        logger.warning(
            "Email not configured (MAILGUN_API_KEY, MAILGUN_DOMAIN, "
            "REPORT_RECIPIENTS). Skipping report email."
        )
        return False

    recipient_list = [r.strip() for r in recipients.split(";") if r.strip()]
    if not recipient_list:
        logger.warning("No valid recipients configured. Skipping report email.")
        return False

    subject = report.to_subject()
    html_body = report.to_html()
    text_body = report.to_text()

    logger.info(f"Sending report email to {', '.join(recipient_list)}")

    try:
        response = httpx.post(
            f"https://api.eu.mailgun.net/v3/{domain}/messages",
            auth=("api", api_key),
            data={
                "from": f"Cijene API <noreply@{domain}>",
                "to": recipient_list,
                "subject": subject,
                "text": text_body,
                "html": html_body,
            },
            timeout=30,
        )
        response.raise_for_status()
        logger.info(f"Report email sent successfully: {response.json()}")
        return True
    except Exception as e:
        logger.error(f"Failed to send report email: {e}")
        return False
