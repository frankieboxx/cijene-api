"""
Dubrovnik deals report — finds products on sale exclusively in Dubrovnik.

Queries the database for products that have a special (promotional) price
in Dubrovnik stores but NOT in other stores of the same chain.
Sends a formatted HTML email with the results.

Usage:
    python -m scripts.deals [--date YYYY-MM-DD] [--skip-email] [--debug]
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import httpx
from mjml import mjml_to_html

from service.config import settings

logger = logging.getLogger("deals")

TARGET_CITY = "dubrovnik"


@dataclass
class Deal:
    """A single product deal found in Dubrovnik."""

    chain: str
    product_name: str
    brand: str
    category: str
    regular_price: Decimal
    special_price: Decimal
    unit_price: Decimal | None
    best_price_30: Decimal | None
    store_name: str
    store_address: str
    ean: str

    @property
    def discount_pct(self) -> float:
        if self.regular_price and self.regular_price > 0:
            return float(
                (self.regular_price - self.special_price)
                / self.regular_price
                * 100
            )
        return 0.0

    @property
    def savings(self) -> Decimal:
        return self.regular_price - self.special_price


@dataclass
class DealsReport:
    """Full deals report for a single day."""

    report_date: date
    city: str
    deals: list[Deal] = field(default_factory=list)

    @property
    def total_deals(self) -> int:
        return len(self.deals)

    @property
    def chains_with_deals(self) -> int:
        return len({d.chain for d in self.deals})

    @property
    def avg_discount(self) -> float:
        if not self.deals:
            return 0.0
        return sum(d.discount_pct for d in self.deals) / len(self.deals)

    @property
    def max_discount(self) -> float:
        if not self.deals:
            return 0.0
        return max(d.discount_pct for d in self.deals)

    def _fmt_price(self, price: Decimal | None) -> str:
        if price is None:
            return "—"
        return f"{price:.2f} €"

    def _fmt_pct(self, pct: float) -> str:
        return f"{pct:.0f}%"

    def to_subject(self) -> str:
        return (
            f"[Cijene] Dubrovnik akcije {self.report_date} — "
            f"{self.total_deals} proizvoda"
        )

    def _build_deal_rows(self) -> str:
        rows = []
        sorted_deals = sorted(self.deals, key=lambda d: d.discount_pct, reverse=True)

        for i, d in enumerate(sorted_deals):
            bg = "#f8fafc" if i % 2 == 0 else "#ffffff"

            # Color-code discount percentage
            if d.discount_pct >= 30:
                pct_color = "#dc2626"  # red for 30%+
            elif d.discount_pct >= 15:
                pct_color = "#ea580c"  # orange for 15-30%
            else:
                pct_color = "#16a34a"  # green for <15%

            rows.append(f"""
                <tr style="background-color: {bg};">
                  <td style="padding: 10px 12px; font-weight: 600; color: #1e293b; font-size: 13px;">
                    {d.product_name}<br/>
                    <span style="font-weight: 400; color: #94a3b8; font-size: 11px;">{d.brand} · {d.chain}</span>
                  </td>
                  <td style="padding: 10px 12px; text-align: right; color: #94a3b8; font-size: 13px; text-decoration: line-through;">
                    {self._fmt_price(d.regular_price)}
                  </td>
                  <td style="padding: 10px 12px; text-align: right; color: #dc2626; font-weight: 700; font-size: 14px;">
                    {self._fmt_price(d.special_price)}
                  </td>
                  <td style="padding: 10px 12px; text-align: right; color: {pct_color}; font-weight: 700; font-size: 13px;">
                    -{self._fmt_pct(d.discount_pct)}
                  </td>
                </tr>""")

        return "\n".join(rows)

    def _build_chain_summary(self) -> str:
        chain_counts: dict[str, int] = {}
        for d in self.deals:
            chain_counts[d.chain] = chain_counts.get(d.chain, 0) + 1

        items = []
        for chain, count in sorted(chain_counts.items(), key=lambda x: -x[1]):
            items.append(
                f'<span style="display: inline-block; background: #e2e8f0; '
                f'border-radius: 12px; padding: 4px 12px; margin: 2px 4px; '
                f'font-size: 12px; color: #334155;">'
                f"{chain}: {count}</span>"
            )
        return " ".join(items)

    def to_html(self) -> str:
        deal_rows = self._build_deal_rows()
        chain_summary = self._build_chain_summary()

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
    <mj-section background-color="#7c3aed" padding="28px 20px 20px" border-radius="0">
      <mj-column>
        <mj-text align="center" font-size="24px" font-weight="700" color="#ffffff" padding="0">
          &#127930; Dubrovnik Akcije
        </mj-text>
        <mj-text align="center" font-size="13px" color="#ddd6fe" padding="4px 0 0 0">
          Proizvodi na akciji samo u Dubrovniku — {self.report_date.strftime("%d.%m.%Y.")}
        </mj-text>
      </mj-column>
    </mj-section>

    <!-- Summary stats -->
    <mj-section padding="20px 10px 0">
      <mj-column padding="0 10px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">{self.total_deals}</div>
          <div class="stat-label">Akcija</div>
        </mj-text>
      </mj-column>
      <mj-column padding="0 10px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">{self.chains_with_deals}</div>
          <div class="stat-label">Lanaca</div>
        </mj-text>
      </mj-column>
      <mj-column padding="0 10px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">-{self._fmt_pct(self.avg_discount)}</div>
          <div class="stat-label">Prosj. popust</div>
        </mj-text>
      </mj-column>
    </mj-section>

    <!-- Chain breakdown -->
    <mj-section padding="12px 20px 0">
      <mj-column>
        <mj-text align="center" padding="12px 16px" background-color="#ffffff" border-radius="8px" font-size="13px">
          {chain_summary}
        </mj-text>
      </mj-column>
    </mj-section>

    <!-- Deals table -->
    <mj-section padding="20px 20px 0">
      <mj-column>
        <mj-text font-size="16px" font-weight="700" color="#0f172a" padding="0 0 12px 0">
          Akcije — sortirano po popustu
        </mj-text>
        <mj-table cellpadding="0" cellspacing="0" width="100%" container-background-color="#ffffff" border="none" padding="0">
          <tr style="background-color: #7c3aed; color: #ffffff;">
            <th style="padding: 12px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 8px 0 0 0;">Proizvod</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Redovna</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Akcija</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 0 8px 0 0;">Popust</th>
          </tr>
          {deal_rows}
        </mj-table>
      </mj-column>
    </mj-section>

    <!-- Footer -->
    <mj-section padding="30px 20px 20px">
      <mj-column>
        <mj-divider border-color="#e2e8f0" border-width="1px" padding="0 0 20px 0" />
        <mj-text align="center" font-size="12px" color="#94a3b8" padding="0">
          Cijene API &bull; Dubrovnik Deals &bull; {settings.base_url}
        </mj-text>
        <mj-text align="center" font-size="11px" color="#cbd5e1" padding="4px 0 0 0">
          Prikazani su proizvodi koji imaju akcijsku cijenu u Dubrovniku,
          a nemaju akcijsku cijenu u drugim gradovima istog lanca.
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
        lines: list[str] = []
        lines.append(
            f"Dubrovnik Akcije — {self.report_date} "
            f"({self.total_deals} proizvoda)"
        )
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"  Akcija:       {self.total_deals}")
        lines.append(f"  Lanaca:       {self.chains_with_deals}")
        lines.append(f"  Prosj. popust: -{self._fmt_pct(self.avg_discount)}")
        lines.append("")
        lines.append(
            f"  {'Proizvod':<35} {'Redovna':>8} {'Akcija':>8} {'Popust':>7}"
        )
        lines.append("  " + "-" * 60)

        for d in sorted(self.deals, key=lambda x: x.discount_pct, reverse=True):
            name = d.product_name[:32] + "..." if len(d.product_name) > 35 else d.product_name
            lines.append(
                f"  {name:<35} "
                f"{self._fmt_price(d.regular_price):>8} "
                f"{self._fmt_price(d.special_price):>8} "
                f"-{self._fmt_pct(d.discount_pct):>6}"
            )
            lines.append(f"    {d.brand} · {d.chain}")

        lines.append("")
        lines.append(f"— Cijene API | Dubrovnik Deals | {settings.base_url}")
        return "\n".join(lines)


DEALS_QUERY = """
WITH latest_dates AS (
    -- Latest loaded date per chain
    SELECT DISTINCT ON (chain_id) chain_id, price_date
    FROM chain_stats
    ORDER BY chain_id, price_date DESC
),
dubrovnik_specials AS (
    -- Products with special_price in Dubrovnik stores.
    -- Some chains (Tommy) sometimes put per-kg unit price into the MPC
    -- (regular_price) column.  When regular_price = unit_price AND a
    -- best_price_30 exists that is lower, we treat best_price_30 as
    -- the real per-package regular price.
    SELECT
        p.chain_product_id,
        c.code AS chain,
        cp.name AS product_name,
        COALESCE(cp.brand, '') AS brand,
        COALESCE(cp.category, '') AS category,
        pr.ean,
        CASE
            WHEN p.regular_price = p.unit_price
                 AND p.best_price_30 IS NOT NULL
                 AND p.best_price_30 < p.regular_price
            THEN p.best_price_30
            ELSE p.regular_price
        END AS regular_price,
        p.special_price,
        p.unit_price,
        p.best_price_30,
        s.code AS store_code,
        COALESCE(s.address, '') AS store_address,
        s.city AS store_city
    FROM latest_dates ld
    JOIN chains c ON c.id = ld.chain_id
    JOIN chain_products cp ON cp.chain_id = c.id
    LEFT JOIN products pr ON pr.id = cp.product_id
    JOIN prices p ON p.chain_product_id = cp.id
                 AND p.price_date = ld.price_date
    JOIN stores s ON s.id = p.store_id
    WHERE LOWER(s.city) LIKE '%dubrovnik%'
      AND p.special_price IS NOT NULL
      AND p.regular_price IS NOT NULL
      AND p.special_price < p.regular_price
),
non_dubrovnik_specials AS (
    -- Same chain_product_ids that ALSO have special_price outside Dubrovnik
    SELECT DISTINCT p.chain_product_id
    FROM latest_dates ld
    JOIN chains c ON c.id = ld.chain_id
    JOIN chain_products cp ON cp.chain_id = c.id
    JOIN prices p ON p.chain_product_id = cp.id
                 AND p.price_date = ld.price_date
    JOIN stores s ON s.id = p.store_id
    WHERE LOWER(s.city) NOT LIKE '%dubrovnik%'
      AND p.special_price IS NOT NULL
      AND p.special_price < p.regular_price
)
SELECT DISTINCT ON (ds.chain, ds.product_name)
    ds.chain,
    ds.product_name,
    ds.brand,
    ds.category,
    ds.ean,
    ds.regular_price,
    ds.special_price,
    ds.unit_price,
    ds.best_price_30,
    ds.store_code,
    ds.store_address
FROM dubrovnik_specials ds
WHERE ds.chain_product_id NOT IN (SELECT chain_product_id FROM non_dubrovnik_specials)
  AND ds.special_price < ds.regular_price  -- re-check after price correction
ORDER BY ds.chain, ds.product_name, ds.special_price ASC
"""


async def fetch_dubrovnik_deals(price_date: date | None = None) -> list[Deal]:
    """
    Fetch products on sale exclusively in Dubrovnik.

    Returns deals where a product has a special_price in a Dubrovnik store
    but does NOT have a special_price in any non-Dubrovnik store of the same chain.
    """
    db = settings.get_db()
    await db.connect()

    try:
        async with db._get_conn() as conn:
            rows = await conn.fetch(DEALS_QUERY)

            deals = []
            for row in rows:
                deals.append(
                    Deal(
                        chain=row["chain"],
                        product_name=row["product_name"],
                        brand=row["brand"],
                        category=row["category"],
                        regular_price=row["regular_price"],
                        special_price=row["special_price"],
                        unit_price=row["unit_price"],
                        best_price_30=row["best_price_30"],
                        store_name=row["store_code"],
                        store_address=row["store_address"],
                        ean=row["ean"] or "",
                    )
                )

            logger.info(
                f"Found {len(deals)} Dubrovnik-exclusive deals"
            )
            return deals
    finally:
        await db.close()


def send_deals_report(report: DealsReport) -> bool:
    """Send the Dubrovnik deals report via Mailgun."""
    api_key = settings.mailgun_api_key
    domain = settings.mailgun_domain
    recipients = settings.report_recipients

    if not api_key or not domain or not recipients:
        logger.warning("Email not configured. Skipping deals report.")
        return False

    recipient_list = [r.strip() for r in recipients.split(";") if r.strip()]
    if not recipient_list:
        logger.warning("No valid recipients configured.")
        return False

    subject = report.to_subject()
    html_body = report.to_html()
    text_body = report.to_text()

    logger.info(f"Sending Dubrovnik deals report to {', '.join(recipient_list)}")

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
        logger.info(f"Deals report email sent: {response.json()}")
        return True
    except Exception as e:
        logger.error(f"Failed to send deals report: {e}")
        return False


async def run_deals_report(
    report_date: date,
    skip_email: bool = False,
) -> DealsReport:
    """Run the full deals report pipeline."""
    logger.info(f"=== Dubrovnik Deals Report: {report_date} ===")

    deals = await fetch_dubrovnik_deals(report_date)

    report = DealsReport(
        report_date=report_date,
        city="Dubrovnik",
        deals=deals,
    )

    if not deals:
        logger.info("No Dubrovnik-exclusive deals found today.")

    if skip_email:
        logger.info("Email skipped (--skip-email)")
        print(report.to_text())
    else:
        logger.info("Sending deals report email...")
        sent = send_deals_report(report)
        if sent:
            logger.info("Deals report email sent successfully.")
        else:
            logger.warning("Deals report email was not sent. Printing to stdout:")
            print(report.to_text())

    logger.info("=== Dubrovnik Deals Report complete ===")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dubrovnik exclusive deals email report",
    )
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
        help="Report date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--skip-email",
        action="store_true",
        help="Skip sending email, print report to stdout",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s:%(name)s:%(levelname)s:%(message)s",
    )

    report_date = args.date or date.today()
    asyncio.run(run_deals_report(report_date, skip_email=args.skip_email))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
