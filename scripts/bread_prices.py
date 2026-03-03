"""
Bread price tracker — monitors bread prices across all Croatian retail chains.

Tracks cheapest bread per chain, price changes vs previous day, and active
promotions (special_price) for bread products. Sends a formatted HTML email
with the results.

Usage:
    python -m scripts.bread_prices [--date YYYY-MM-DD] [--skip-email] [--debug]
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

logger = logging.getLogger("bread_prices")


@dataclass
class BreadProduct:
    """A single bread product with pricing info."""

    chain: str
    product_name: str
    brand: str
    category: str
    regular_price: Decimal
    special_price: Decimal | None
    unit_price: Decimal | None
    best_price_30: Decimal | None

    @property
    def effective_price(self) -> Decimal:
        """Current effective price (special if available, otherwise regular)."""
        if self.special_price and self.special_price < self.regular_price:
            return self.special_price
        return self.regular_price

    @property
    def is_on_sale(self) -> bool:
        return (
            self.special_price is not None
            and self.special_price < self.regular_price
        )

    @property
    def discount_pct(self) -> float:
        if self.is_on_sale and self.regular_price > 0:
            return float(
                (self.regular_price - self.special_price)
                / self.regular_price
                * 100
            )
        return 0.0


@dataclass
class PriceChange:
    """A bread product whose price changed from the previous day."""

    chain: str
    product_name: str
    brand: str
    old_price: Decimal
    new_price: Decimal

    @property
    def change_pct(self) -> float:
        if self.old_price > 0:
            return float(
                (self.new_price - self.old_price) / self.old_price * 100
            )
        return 0.0

    @property
    def change_amount(self) -> Decimal:
        return self.new_price - self.old_price


@dataclass
class BreadReport:
    """Full bread price report for a single day."""

    report_date: date
    cheapest_per_chain: list[BreadProduct] = field(default_factory=list)
    deals: list[BreadProduct] = field(default_factory=list)
    price_changes: list[PriceChange] = field(default_factory=list)

    @property
    def total_products(self) -> int:
        return len(self.cheapest_per_chain)

    @property
    def total_deals(self) -> int:
        return len(self.deals)

    @property
    def total_changes(self) -> int:
        return len(self.price_changes)

    @property
    def increases(self) -> list[PriceChange]:
        return [c for c in self.price_changes if c.change_amount > 0]

    @property
    def decreases(self) -> list[PriceChange]:
        return [c for c in self.price_changes if c.change_amount < 0]

    @property
    def cheapest_overall(self) -> BreadProduct | None:
        if not self.cheapest_per_chain:
            return None
        return min(self.cheapest_per_chain, key=lambda p: p.effective_price)

    def _fmt_price(self, price: Decimal | None) -> str:
        if price is None:
            return "—"
        return f"{price:.2f} €"

    def _fmt_pct(self, pct: float) -> str:
        return f"{pct:.1f}%"

    def _fmt_change(self, pct: float) -> str:
        sign = "+" if pct > 0 else ""
        return f"{sign}{pct:.1f}%"

    def to_subject(self) -> str:
        cheapest = self.cheapest_overall
        cheapest_str = ""
        if cheapest:
            cheapest_str = (
                f" — najjeftiniji: {self._fmt_price(cheapest.effective_price)} "
                f"({cheapest.chain})"
            )
        return (
            f"[Cijene] Kruh Dubrovnik {self.report_date} — "
            f"{self.total_deals} akcija, "
            f"{self.total_changes} promjena{cheapest_str}"
        )

    def _build_cheapest_rows(self) -> str:
        """Build MJML table rows for cheapest bread per chain."""
        rows = []
        sorted_products = sorted(
            self.cheapest_per_chain, key=lambda p: p.effective_price
        )
        for i, p in enumerate(sorted_products):
            bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
            price_display = self._fmt_price(p.effective_price)
            unit_display = self._fmt_price(p.unit_price)

            sale_badge = ""
            if p.is_on_sale:
                sale_badge = (
                    '<span style="background: #dc2626; color: white; '
                    'font-size: 10px; padding: 2px 6px; border-radius: 4px; '
                    f'margin-left: 6px;">-{self._fmt_pct(p.discount_pct)}</span>'
                )

            rows.append(f"""
                <tr style="background-color: {bg};">
                  <td style="padding: 10px 12px; font-weight: 600; color: #1e293b; font-size: 13px;">
                    {p.chain}
                  </td>
                  <td style="padding: 10px 12px; color: #334155; font-size: 13px;">
                    {p.product_name}{sale_badge}<br/>
                    <span style="color: #94a3b8; font-size: 11px;">{p.brand}</span>
                  </td>
                  <td style="padding: 10px 12px; text-align: right; color: #0f172a; font-weight: 700; font-size: 14px;">
                    {price_display}
                  </td>
                  <td style="padding: 10px 12px; text-align: right; color: #64748b; font-size: 12px;">
                    {unit_display}/kg
                  </td>
                </tr>""")
        return "\n".join(rows)

    def _build_deals_rows(self) -> str:
        """Build MJML table rows for bread on sale."""
        rows = []
        sorted_deals = sorted(self.deals, key=lambda d: d.discount_pct, reverse=True)
        for i, d in enumerate(sorted_deals):
            bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
            if d.discount_pct >= 30:
                pct_color = "#dc2626"
            elif d.discount_pct >= 15:
                pct_color = "#ea580c"
            else:
                pct_color = "#16a34a"

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

    def _build_changes_rows(self) -> str:
        """Build MJML table rows for price changes."""
        rows = []
        sorted_changes = sorted(
            self.price_changes, key=lambda c: c.change_pct, reverse=True
        )
        for i, c in enumerate(sorted_changes):
            bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
            if c.change_amount > 0:
                change_color = "#dc2626"
                arrow = "&#9650;"  # ▲
            else:
                change_color = "#16a34a"
                arrow = "&#9660;"  # ▼

            rows.append(f"""
                <tr style="background-color: {bg};">
                  <td style="padding: 10px 12px; font-weight: 600; color: #1e293b; font-size: 13px;">
                    {c.product_name}<br/>
                    <span style="font-weight: 400; color: #94a3b8; font-size: 11px;">{c.brand} · {c.chain}</span>
                  </td>
                  <td style="padding: 10px 12px; text-align: right; color: #64748b; font-size: 13px;">
                    {self._fmt_price(c.old_price)}
                  </td>
                  <td style="padding: 10px 12px; text-align: right; color: {change_color}; font-weight: 700; font-size: 14px;">
                    {self._fmt_price(c.new_price)}
                  </td>
                  <td style="padding: 10px 12px; text-align: right; color: {change_color}; font-weight: 700; font-size: 13px;">
                    {arrow} {self._fmt_change(c.change_pct)}
                  </td>
                </tr>""")
        return "\n".join(rows)

    def to_html(self) -> str:
        """Generate MJML-based HTML email for the bread price report."""
        cheapest_rows = self._build_cheapest_rows()
        deals_rows = self._build_deals_rows()
        changes_rows = self._build_changes_rows()

        cheapest = self.cheapest_overall
        cheapest_price = self._fmt_price(cheapest.effective_price) if cheapest else "—"
        cheapest_chain = cheapest.chain if cheapest else "—"

        # Deals section (only if there are deals)
        deals_section = ""
        if self.deals:
            deals_section = f"""
    <mj-section padding="20px 20px 0">
      <mj-column>
        <mj-text font-size="16px" font-weight="700" color="#0f172a" padding="0 0 12px 0">
          &#127942; Akcije na kruh
        </mj-text>
        <mj-table cellpadding="0" cellspacing="0" width="100%" container-background-color="#ffffff" border="none" padding="0">
          <tr style="background-color: #dc2626; color: #ffffff;">
            <th style="padding: 12px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 8px 0 0 0;">Proizvod</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Redovna</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Akcija</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 0 8px 0 0;">Popust</th>
          </tr>
          {deals_rows}
        </mj-table>
      </mj-column>
    </mj-section>"""

        # Changes section (only if there are changes)
        changes_section = ""
        if self.price_changes:
            changes_section = f"""
    <mj-section padding="20px 20px 0">
      <mj-column>
        <mj-text font-size="16px" font-weight="700" color="#0f172a" padding="0 0 12px 0">
          &#128200; Promjene cijena kruha
        </mj-text>
        <mj-table cellpadding="0" cellspacing="0" width="100%" container-background-color="#ffffff" border="none" padding="0">
          <tr style="background-color: #475569; color: #ffffff;">
            <th style="padding: 12px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 8px 0 0 0;">Proizvod</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Jučer</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Danas</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 0 8px 0 0;">Promjena</th>
          </tr>
          {changes_rows}
        </mj-table>
      </mj-column>
    </mj-section>"""

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
    <mj-section background-color="#b45309" padding="28px 20px 20px" border-radius="0">
      <mj-column>
        <mj-text align="center" font-size="24px" font-weight="700" color="#ffffff" padding="0">
          &#127838; Praćenje cijena kruha — Dubrovnik
        </mj-text>
        <mj-text align="center" font-size="13px" color="#fde68a" padding="4px 0 0 0">
          Dnevni izvještaj — {self.report_date.strftime("%d.%m.%Y.")}
        </mj-text>
      </mj-column>
    </mj-section>

    <!-- Summary stats -->
    <mj-section padding="20px 10px 0">
      <mj-column padding="0 6px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">{cheapest_price}</div>
          <div class="stat-label">Najjeftiniji ({cheapest_chain})</div>
        </mj-text>
      </mj-column>
      <mj-column padding="0 6px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value">{self.total_deals}</div>
          <div class="stat-label">Akcija</div>
        </mj-text>
      </mj-column>
      <mj-column padding="0 6px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value" style="color: #dc2626;">{len(self.increases)}</div>
          <div class="stat-label">Poskupljenja</div>
        </mj-text>
      </mj-column>
      <mj-column padding="0 6px">
        <mj-text align="center" padding="20px 12px" background-color="#ffffff" border-radius="8px">
          <div class="stat-value" style="color: #16a34a;">{len(self.decreases)}</div>
          <div class="stat-label">Pojeftinjenja</div>
        </mj-text>
      </mj-column>
    </mj-section>

    <!-- Cheapest bread per chain -->
    <mj-section padding="20px 20px 0">
      <mj-column>
        <mj-text font-size="16px" font-weight="700" color="#0f172a" padding="0 0 12px 0">
          &#127968; Najjeftiniji kruh po lancu u Dubrovniku
        </mj-text>
        <mj-table cellpadding="0" cellspacing="0" width="100%" container-background-color="#ffffff" border="none" padding="0">
          <tr style="background-color: #b45309; color: #ffffff;">
            <th style="padding: 12px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 8px 0 0 0;">Lanac</th>
            <th style="padding: 12px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Proizvod</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Cijena</th>
            <th style="padding: 12px; text-align: right; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 0 8px 0 0;">€/kg</th>
          </tr>
          {cheapest_rows}
        </mj-table>
      </mj-column>
    </mj-section>

    {deals_section}

    {changes_section}

    <!-- Footer -->
    <mj-section padding="30px 20px 20px">
      <mj-column>
        <mj-divider border-color="#e2e8f0" border-width="1px" padding="0 0 20px 0" />
        <mj-text align="center" font-size="12px" color="#94a3b8" padding="0">
          Cijene API &bull; Praćenje cijena kruha &bull; Dubrovnik &bull; {settings.base_url}
        </mj-text>
        <mj-text align="center" font-size="11px" color="#cbd5e1" padding="4px 0 0 0">
          Prate se proizvodi koji u nazivu sadrže "kruh" u trgovinama na području Dubrovnika.
          Najjeftiniji kruh po lancu prikazuje minimalnu cijenu iz dubrovačkih trgovina tog lanca.
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
        """Generate plain-text version of the report."""
        lines: list[str] = []
        lines.append(
            f"Praćenje cijena kruha (Dubrovnik) — {self.report_date}"
        )
        lines.append("=" * 60)
        lines.append("")

        cheapest = self.cheapest_overall
        if cheapest:
            lines.append(
                f"  Najjeftiniji: {self._fmt_price(cheapest.effective_price)} "
                f"({cheapest.chain} — {cheapest.product_name})"
            )
        lines.append(f"  Akcija:       {self.total_deals}")
        lines.append(f"  Poskupljenja: {len(self.increases)}")
        lines.append(f"  Pojeftinjenja: {len(self.decreases)}")
        lines.append("")

        # Cheapest per chain
        lines.append("NAJJEFTINIJI KRUH PO LANCU U DUBROVNIKU")
        lines.append("-" * 60)
        lines.append(
            f"  {'Lanac':<12} {'Proizvod':<30} {'Cijena':>8} {'€/kg':>8}"
        )
        lines.append("  " + "-" * 56)
        for p in sorted(self.cheapest_per_chain, key=lambda x: x.effective_price):
            name = (
                p.product_name[:27] + "..."
                if len(p.product_name) > 30
                else p.product_name
            )
            sale = " *AKCIJA*" if p.is_on_sale else ""
            lines.append(
                f"  {p.chain:<12} {name:<30} "
                f"{self._fmt_price(p.effective_price):>8} "
                f"{self._fmt_price(p.unit_price):>8}{sale}"
            )

        if self.deals:
            lines.append("")
            lines.append("AKCIJE NA KRUH")
            lines.append("-" * 60)
            lines.append(
                f"  {'Proizvod':<35} {'Redovna':>8} {'Akcija':>8} {'Popust':>7}"
            )
            lines.append("  " + "-" * 56)
            for d in sorted(self.deals, key=lambda x: x.discount_pct, reverse=True):
                name = (
                    d.product_name[:32] + "..."
                    if len(d.product_name) > 35
                    else d.product_name
                )
                lines.append(
                    f"  {name:<35} "
                    f"{self._fmt_price(d.regular_price):>8} "
                    f"{self._fmt_price(d.special_price):>8} "
                    f"-{self._fmt_pct(d.discount_pct):>6}"
                )
                lines.append(f"    {d.brand} · {d.chain}")

        if self.price_changes:
            lines.append("")
            lines.append("PROMJENE CIJENA KRUHA")
            lines.append("-" * 60)
            lines.append(
                f"  {'Proizvod':<35} {'Jučer':>8} {'Danas':>8} {'Promj.':>7}"
            )
            lines.append("  " + "-" * 56)
            for c in sorted(
                self.price_changes, key=lambda x: x.change_pct, reverse=True
            ):
                name = (
                    c.product_name[:32] + "..."
                    if len(c.product_name) > 35
                    else c.product_name
                )
                lines.append(
                    f"  {name:<35} "
                    f"{self._fmt_price(c.old_price):>8} "
                    f"{self._fmt_price(c.new_price):>8} "
                    f"{self._fmt_change(c.change_pct):>7}"
                )
                lines.append(f"    {c.brand} · {c.chain}")

        lines.append("")
        lines.append(f"— Cijene API | Praćenje kruha | Dubrovnik | {settings.base_url}")
        return "\n".join(lines)


# --- SQL Queries ---

CHEAPEST_BREAD_QUERY = """
WITH latest_dates AS (
    SELECT DISTINCT ON (chain_id) chain_id, price_date
    FROM chain_stats
    ORDER BY chain_id, price_date DESC
)
SELECT DISTINCT ON (c.code)
    c.code AS chain,
    cp.name AS product_name,
    COALESCE(cp.brand, '') AS brand,
    COALESCE(cp.category, '') AS category,
    p.regular_price,
    p.special_price,
    p.unit_price,
    p.best_price_30
FROM latest_dates ld
JOIN chains c ON c.id = ld.chain_id
JOIN chain_products cp ON cp.chain_id = c.id
JOIN prices p ON p.chain_product_id = cp.id
             AND p.price_date = ld.price_date
JOIN stores s ON s.id = p.store_id
WHERE LOWER(s.city) LIKE '%dubrovnik%'
  AND LOWER(cp.name) ~ '\\mkruh\\M'
  AND LOWER(cp.name) NOT LIKE '%vrećic%'
  AND LOWER(cp.name) NOT LIKE '%mješavin%za%'
  AND LOWER(cp.name) NOT LIKE '%brašno%za%'
  AND LOWER(cp.name) NOT LIKE '%ekstrakt%'
  AND LOWER(cp.name) NOT LIKE '%knedle od kruha%'
  AND LOWER(cp.name) NOT LIKE '%čips od kruha%'
  AND p.regular_price IS NOT NULL
  AND p.regular_price > 0.30
ORDER BY c.code,
    CASE
        WHEN p.special_price IS NOT NULL AND p.special_price < p.regular_price
        THEN p.special_price
        ELSE p.regular_price
    END ASC
"""

BREAD_DEALS_QUERY = """
WITH latest_dates AS (
    SELECT DISTINCT ON (chain_id) chain_id, price_date
    FROM chain_stats
    ORDER BY chain_id, price_date DESC
)
SELECT DISTINCT ON (c.code, cp.name)
    c.code AS chain,
    cp.name AS product_name,
    COALESCE(cp.brand, '') AS brand,
    COALESCE(cp.category, '') AS category,
    p.regular_price,
    p.special_price,
    p.unit_price,
    p.best_price_30
FROM latest_dates ld
JOIN chains c ON c.id = ld.chain_id
JOIN chain_products cp ON cp.chain_id = c.id
JOIN prices p ON p.chain_product_id = cp.id
             AND p.price_date = ld.price_date
JOIN stores s ON s.id = p.store_id
WHERE LOWER(s.city) LIKE '%dubrovnik%'
  AND LOWER(cp.name) ~ '\\mkruh\\M'
  AND LOWER(cp.name) NOT LIKE '%vrećic%'
  AND LOWER(cp.name) NOT LIKE '%mješavin%za%'
  AND LOWER(cp.name) NOT LIKE '%brašno%za%'
  AND LOWER(cp.name) NOT LIKE '%ekstrakt%'
  AND LOWER(cp.name) NOT LIKE '%knedle od kruha%'
  AND LOWER(cp.name) NOT LIKE '%čips od kruha%'
  AND p.special_price IS NOT NULL
  AND p.regular_price IS NOT NULL
  AND p.special_price < p.regular_price
  AND p.regular_price > 0.30
ORDER BY c.code, cp.name, p.special_price ASC
"""

BREAD_PRICE_CHANGES_QUERY = """
WITH latest_dates AS (
    SELECT DISTINCT ON (chain_id) chain_id, price_date
    FROM chain_stats
    ORDER BY chain_id, price_date DESC
),
prev_dates AS (
    SELECT DISTINCT ON (cs.chain_id) cs.chain_id, cs.price_date
    FROM chain_stats cs
    JOIN latest_dates ld ON ld.chain_id = cs.chain_id
    WHERE cs.price_date < ld.price_date
    ORDER BY cs.chain_id, cs.price_date DESC
),
today AS (
    SELECT
        c.code AS chain,
        cp.id AS cp_id,
        cp.name AS product_name,
        COALESCE(cp.brand, '') AS brand,
        MIN(p.regular_price) AS min_price
    FROM latest_dates ld
    JOIN chains c ON c.id = ld.chain_id
    JOIN chain_products cp ON cp.chain_id = c.id
    JOIN prices p ON p.chain_product_id = cp.id
                 AND p.price_date = ld.price_date
    JOIN stores s ON s.id = p.store_id
    WHERE LOWER(s.city) LIKE '%dubrovnik%'
      AND LOWER(cp.name) ~ '\\mkruh\\M'
      AND LOWER(cp.name) NOT LIKE '%vrećic%'
      AND LOWER(cp.name) NOT LIKE '%mješavin%za%'
      AND LOWER(cp.name) NOT LIKE '%brašno%za%'
      AND LOWER(cp.name) NOT LIKE '%ekstrakt%'
      AND LOWER(cp.name) NOT LIKE '%knedle od kruha%'
      AND LOWER(cp.name) NOT LIKE '%čips od kruha%'
      AND p.regular_price IS NOT NULL
      AND p.regular_price > 0.30
    GROUP BY c.code, cp.id, cp.name, cp.brand
),
yesterday AS (
    SELECT
        c.code AS chain,
        cp.id AS cp_id,
        MIN(p.regular_price) AS min_price
    FROM prev_dates pd
    JOIN chains c ON c.id = pd.chain_id
    JOIN chain_products cp ON cp.chain_id = c.id
    JOIN prices p ON p.chain_product_id = cp.id
                 AND p.price_date = pd.price_date
    JOIN stores s ON s.id = p.store_id
    WHERE LOWER(s.city) LIKE '%dubrovnik%'
      AND LOWER(cp.name) ~ '\\mkruh\\M'
      AND LOWER(cp.name) NOT LIKE '%vrećic%'
      AND LOWER(cp.name) NOT LIKE '%mješavin%za%'
      AND LOWER(cp.name) NOT LIKE '%brašno%za%'
      AND LOWER(cp.name) NOT LIKE '%ekstrakt%'
      AND LOWER(cp.name) NOT LIKE '%knedle od kruha%'
      AND LOWER(cp.name) NOT LIKE '%čips od kruha%'
      AND p.regular_price IS NOT NULL
      AND p.regular_price > 0.30
    GROUP BY c.code, cp.id
)
SELECT
    t.chain,
    t.product_name,
    t.brand,
    y.min_price AS old_price,
    t.min_price AS new_price
FROM today t
JOIN yesterday y ON y.cp_id = t.cp_id
WHERE t.min_price != y.min_price
ORDER BY ABS(t.min_price - y.min_price) DESC
LIMIT 50
"""


async def fetch_bread_data() -> tuple[
    list[BreadProduct], list[BreadProduct], list[PriceChange]
]:
    """
    Fetch bread price data from the database.

    Returns:
        Tuple of (cheapest_per_chain, deals, price_changes).
    """
    db = settings.get_db()
    await db.connect()

    try:
        async with db._get_conn() as conn:
            # Cheapest bread per chain
            cheapest_rows = await conn.fetch(CHEAPEST_BREAD_QUERY)
            cheapest = [
                BreadProduct(
                    chain=row["chain"],
                    product_name=row["product_name"],
                    brand=row["brand"],
                    category=row["category"],
                    regular_price=row["regular_price"],
                    special_price=row["special_price"],
                    unit_price=row["unit_price"],
                    best_price_30=row["best_price_30"],
                )
                for row in cheapest_rows
            ]
            logger.info(f"Found cheapest bread in {len(cheapest)} chains")

            # Bread deals
            deals_rows = await conn.fetch(BREAD_DEALS_QUERY)
            deals = [
                BreadProduct(
                    chain=row["chain"],
                    product_name=row["product_name"],
                    brand=row["brand"],
                    category=row["category"],
                    regular_price=row["regular_price"],
                    special_price=row["special_price"],
                    unit_price=row["unit_price"],
                    best_price_30=row["best_price_30"],
                )
                for row in deals_rows
            ]
            logger.info(f"Found {len(deals)} bread deals")

            # Price changes
            changes_rows = await conn.fetch(BREAD_PRICE_CHANGES_QUERY)
            changes = [
                PriceChange(
                    chain=row["chain"],
                    product_name=row["product_name"],
                    brand=row["brand"],
                    old_price=row["old_price"],
                    new_price=row["new_price"],
                )
                for row in changes_rows
            ]
            logger.info(f"Found {len(changes)} bread price changes")

            return cheapest, deals, changes
    finally:
        await db.close()


def send_bread_report(report: BreadReport) -> bool:
    """Send the bread price report via Mailgun."""
    api_key = settings.mailgun_api_key
    domain = settings.mailgun_domain
    recipients = settings.report_recipients

    if not api_key or not domain or not recipients:
        logger.warning("Email not configured. Skipping bread report.")
        return False

    recipient_list = [r.strip() for r in recipients.split(";") if r.strip()]
    if not recipient_list:
        logger.warning("No valid recipients configured.")
        return False

    subject = report.to_subject()
    html_body = report.to_html()
    text_body = report.to_text()

    logger.info(f"Sending bread price report to {', '.join(recipient_list)}")

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
        logger.info(f"Bread report email sent: {response.json()}")
        return True
    except Exception as e:
        logger.error(f"Failed to send bread report: {e}")
        return False


async def run_bread_report(
    report_date: date,
    skip_email: bool = False,
) -> BreadReport:
    """Run the full bread price report pipeline."""
    logger.info(f"=== Bread Price Report: {report_date} ===")

    cheapest, deals, changes = await fetch_bread_data()

    report = BreadReport(
        report_date=report_date,
        cheapest_per_chain=cheapest,
        deals=deals,
        price_changes=changes,
    )

    if not cheapest:
        logger.warning("No bread products found in the database.")

    if skip_email:
        logger.info("Email skipped (--skip-email)")
        print(report.to_text())
    else:
        logger.info("Sending bread price report email...")
        sent = send_bread_report(report)
        if sent:
            logger.info("Bread report email sent successfully.")
        else:
            logger.warning("Bread report email was not sent. Printing to stdout:")
            print(report.to_text())

    logger.info("=== Bread Price Report complete ===")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bread price tracking email report",
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
    asyncio.run(run_bread_report(report_date, skip_email=args.skip_email))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
