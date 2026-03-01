"""
Daily price comparison script.

Connects to both the Atrium DB (expenses/purchases) and the Cijene-API DB
(grocery chain prices in Dubrovnik), matches purchased products with cheaper
alternatives across chains, and sends an email report.

Matching strategy:
1. Metro items: exact match by product code (sifra → chain_products.code)
2. All items: fuzzy name matching via rapidfuzz across all Dubrovnik chains

Usage:
    uv run python -m scripts.price_compare [--skip-email] [--debug]
"""

import argparse
import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import asyncpg
import httpx
from mjml import mjml_to_html
from rapidfuzz import fuzz

from service.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CITY = "Dubrovnik"
FUZZY_THRESHOLD = 72  # minimum score to consider a fuzzy match
MIN_SAVINGS_EUR = 0.05  # minimum savings to report (€)
MIN_SAVINGS_PCT = 5  # minimum savings percentage to report

# Metro chain_id in cijene-api DB
METRO_CHAIN_ID = 7


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class PurchasedItem:
    """An item from the Atrium troskovi_detalji table."""

    id: int
    trosak_id: int
    opis: str  # product name
    kolicina: float
    jedinicna_cijena: float  # unit price paid
    jedinica_mjere: str
    ukupno: float  # total paid
    sifra: str  # product code (Metro internal code)
    dobavljac: str  # supplier name from parent troskovi record
    datum: date  # invoice date


@dataclass
class ParsedQuantity:
    """Extracted quantity and unit from a product name."""

    amount: float  # quantity in base unit (grams, mL, kom)
    unit: str  # normalized unit: 'g', 'ml', 'kom'
    unit_type: str  # 'weight', 'volume', 'piece'
    original: str  # original text that was parsed

    @property
    def kg(self) -> float | None:
        return self.amount / 1000 if self.unit_type == "weight" else None

    @property
    def liters(self) -> float | None:
        return self.amount / 1000 if self.unit_type == "volume" else None


@dataclass
class ChainPrice:
    """A price for a product in a specific chain/store in Dubrovnik."""

    chain: str  # chain code (tommy, konzum, etc.)
    chain_product_code: str
    product_name: str
    regular_price: float
    special_price: float | None
    unit_price: float | None  # per-unit price from DB (€/kg or €/L)
    best_price: float  # effective best price
    parsed_qty: ParsedQuantity | None = None  # extracted from name
    normalized_unit_price: float | None = None  # calculated €/kg or €/L
    unit_type: str = ""  # 'weight', 'volume', 'piece'

    @property
    def display_price(self) -> str:
        if self.special_price and self.special_price < self.regular_price:
            return f"~~{self.regular_price:.2f}~~ **{self.special_price:.2f}€**"
        return f"{self.regular_price:.2f}€"


@dataclass
class PriceMatch:
    """A matched purchased item with cheaper alternatives."""

    purchased: PurchasedItem
    purchased_qty: ParsedQuantity | None  # extracted from purchased item name
    purchased_norm_price: float | None  # normalized €/kg or €/L
    match_type: str  # "exact_code" or "fuzzy_name"
    fuzzy_score: int | None  # only for fuzzy matches
    metro_price: ChainPrice | None  # current Metro price for same product
    alternatives: list[ChainPrice] = field(default_factory=list)

    @property
    def best_alternative(self) -> ChainPrice | None:
        if not self.alternatives:
            return None
        # Compare by normalized unit price when available
        def sort_key(a: ChainPrice) -> float:
            if a.normalized_unit_price is not None:
                return a.normalized_unit_price
            return a.best_price
        return min(self.alternatives, key=sort_key)

    @property
    def savings_per_unit(self) -> float:
        """Savings per standard unit (€/kg or €/L) vs what was paid."""
        best = self.best_alternative
        if not best:
            return 0.0
        # Use normalized unit prices if available
        if self.purchased_norm_price and best.normalized_unit_price:
            return self.purchased_norm_price - best.normalized_unit_price
        # Fallback to raw price comparison
        return self.purchased.jedinicna_cijena - best.best_price

    @property
    def savings_pct(self) -> float:
        ref_price = self.purchased_norm_price or self.purchased.jedinicna_cijena
        if ref_price <= 0:
            return 0.0
        return (self.savings_per_unit / ref_price) * 100

    @property
    def comparison_unit(self) -> str:
        """Human-readable unit for comparison (€/kg, €/L, €/kom)."""
        if self.purchased_qty:
            if self.purchased_qty.unit_type == "weight":
                return "€/kg"
            elif self.purchased_qty.unit_type == "volume":
                return "€/L"
        return "€/kom"


@dataclass
class ComparisonReport:
    """Full comparison report."""

    run_date: date
    total_purchased_items: int
    matched_items: int
    matches_with_savings: list[PriceMatch] = field(default_factory=list)
    unmatched_items: list[PurchasedItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_potential_savings(self) -> float:
        """Total savings estimate. For unit-priced items this is per-unit savings."""
        total = 0.0
        for m in self.matches_with_savings:
            if m.purchased_norm_price and m.purchased_qty and m.purchased_qty.unit_type in ("weight", "volume"):
                # normalized savings (€/kg or €/L difference)
                total += m.savings_per_unit
            else:
                total += m.savings_per_unit * m.purchased.kolicina
        return total


# ---------------------------------------------------------------------------
# Text normalization for fuzzy matching
# ---------------------------------------------------------------------------


# Unit conversion to base units (grams for weight, mL for volume)
UNIT_TO_BASE = {
    "g": (1.0, "g", "weight"),
    "gr": (1.0, "g", "weight"),
    "kg": (1000.0, "g", "weight"),
    "dag": (10.0, "g", "weight"),
    "ml": (1.0, "ml", "volume"),
    "cl": (10.0, "ml", "volume"),
    "dl": (100.0, "ml", "volume"),
    "l": (1000.0, "ml", "volume"),
    "kom": (1.0, "kom", "piece"),
}

# Atrium unit codes (UOM) to our unit types
ATRIUM_UNIT_MAP = {
    "KGM": ("g", "weight", 1000.0),  # per kg
    "GRM": ("g", "weight", 1.0),
    "LTR": ("ml", "volume", 1000.0),  # per L
    "MLT": ("ml", "volume", 1.0),
    "H87": ("kom", "piece", 1.0),  # per piece
    "C62": ("kom", "piece", 1.0),
}

# Regex patterns for extracting quantity from product names
# Order matters: more specific patterns first
QTY_PATTERNS = [
    # "50X10G" → 50 * 10g = 500g
    re.compile(
        r"(\d+)\s*[xX*]\s*(\d+(?:[.,]\d+)?)\s*(g|gr|kg|dag|ml|cl|dl|l|kom)\b",
        re.IGNORECASE,
    ),
    # "500G", "1.5KG", "0,75L"
    re.compile(
        r"(\d+(?:[.,]\d+)?)\s*(g|gr|kg|dag|ml|cl|dl|l|kom)\b",
        re.IGNORECASE,
    ),
]


def extract_quantity(name: str) -> ParsedQuantity | None:
    """
    Extract quantity and unit from a product name.

    Examples:
        "1KG ARO LIMUNSKA KISELINA" → 1000g (weight)
        "LIMUNSKA KISELINA 20g DR OETKER" → 20g (weight)
        "50X10G HOT MIX" → 500g (weight)
        "0,75L VINO" → 750ml (volume)
    """
    for pattern in QTY_PATTERNS:
        m = pattern.search(name)
        if not m:
            continue

        groups = m.groups()
        if len(groups) == 3 and "x" in name[m.start() : m.end()].lower():
            # Multiplied: NxMunit
            count = float(groups[0])
            amount_str = groups[1].replace(",", ".")
            amount = float(amount_str) * count
            unit_str = groups[2].lower()
        elif len(groups) >= 2:
            amount_str = groups[-2].replace(",", ".")
            amount = float(amount_str)
            unit_str = groups[-1].lower()
        else:
            continue

        conversion = UNIT_TO_BASE.get(unit_str)
        if not conversion:
            continue

        factor, base_unit, unit_type = conversion
        base_amount = amount * factor

        if base_amount <= 0:
            continue

        return ParsedQuantity(
            amount=base_amount,
            unit=base_unit,
            unit_type=unit_type,
            original=m.group(0),
        )

    return None


def calc_normalized_price(
    price: float, qty: ParsedQuantity | None
) -> float | None:
    """
    Calculate price per standard unit (per kg for weight, per L for volume).

    Returns None if quantity is not available or unit type is 'piece'.
    """
    if not qty or qty.amount <= 0:
        return None
    if qty.unit_type == "weight":
        # Price per kg = price / (amount_in_grams / 1000)
        return price / (qty.amount / 1000)
    elif qty.unit_type == "volume":
        # Price per L = price / (amount_in_ml / 1000)
        return price / (qty.amount / 1000)
    return None


def calc_purchased_norm_price(item: "PurchasedItem") -> tuple[ParsedQuantity | None, float | None]:
    """
    Calculate normalized unit price for a purchased item.

    Uses Atrium's unit of measure (KGM, LTR, H87) when available,
    otherwise falls back to extracting quantity from the product name.
    """
    # Try Atrium UOM first
    uom = item.jedinica_mjere.strip().upper()
    if uom in ATRIUM_UNIT_MAP:
        base_unit, unit_type, amount_per_uom = ATRIUM_UNIT_MAP[uom]
        qty = ParsedQuantity(
            amount=amount_per_uom * item.kolicina,
            unit=base_unit,
            unit_type=unit_type,
            original=f"{item.kolicina} {uom}",
        )
        if unit_type == "weight":
            # jedinicna_cijena is per KGM → that IS the €/kg price
            return qty, item.jedinicna_cijena
        elif unit_type == "volume":
            return qty, item.jedinicna_cijena
        else:
            # piece: try to extract from name for better comparison
            name_qty = extract_quantity(item.opis)
            if name_qty and name_qty.unit_type in ("weight", "volume"):
                norm = calc_normalized_price(item.jedinicna_cijena, name_qty)
                return name_qty, norm
            return qty, None

    # Fallback: extract from product name
    name_qty = extract_quantity(item.opis)
    if name_qty:
        norm = calc_normalized_price(item.jedinicna_cijena, name_qty)
        return name_qty, norm

    return None, None


def normalize_name(name: str) -> str:
    """
    Normalize product name for fuzzy comparison.

    Strips diacritics, lowercases, removes quantity prefixes like '500G',
    and collapses whitespace.
    """
    # Remove diacritics
    nfkd = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in nfkd if not unicodedata.combining(c))
    text = text.lower()
    # Remove common weight/quantity prefixes (e.g. "500G", "1KG", "50X10G")
    text = re.sub(r"\b\d+[xX]\d+[gG]\b", "", text)
    text = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:g|gr|kg|dag|l|ml|cl|dl|kom|m)\b", "", text)
    # Remove percentages
    text = re.sub(r"\b\d+(?:[.,]\d+)?\s*%\s*(?:m\.?m\.?)?", "", text)
    # Remove extra whitespace and punctuation
    text = re.sub(r"[.,;:!/\\()\[\]\"']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Database queries
# ---------------------------------------------------------------------------


async def fetch_recent_purchases(
    conn: asyncpg.Connection,
    days: int = 30,
) -> list[PurchasedItem]:
    """Fetch recent purchase items from Atrium DB."""
    cutoff = datetime.now() - timedelta(days=days)

    rows = await conn.fetch(
        """
        SELECT
            td.id, td.trosak_id, td.opis, td.kolicina,
            td.jedinicna_cijena, td.jedinica_mjere, td.ukupno, td.sifra,
            t.opis as dobavljac, t.datum
        FROM troskovi_detalji td
        JOIN troskovi t ON t.id = td.trosak_id
        WHERE t.datum >= $1
        ORDER BY t.datum DESC
        """,
        cutoff,
    )

    items = []
    for r in rows:
        items.append(
            PurchasedItem(
                id=r["id"],
                trosak_id=r["trosak_id"],
                opis=r["opis"] or "",
                kolicina=float(r["kolicina"] or 0),
                jedinicna_cijena=float(r["jedinicna_cijena"] or 0),
                jedinica_mjere=r["jedinica_mjere"] or "",
                ukupno=float(r["ukupno"] or 0),
                sifra=r["sifra"] or "",
                dobavljac=r["dobavljac"] or "",
                datum=r["datum"].date() if r["datum"] else date.today(),
            )
        )
    return items


async def fetch_dubrovnik_prices(
    conn: asyncpg.Connection,
) -> list[ChainPrice]:
    """Fetch latest prices for all products in Dubrovnik stores."""

    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (cp.id)
            c.code as chain,
            cp.code as product_code,
            cp.name as product_name,
            p.regular_price,
            p.special_price,
            p.unit_price
        FROM prices p
        JOIN chain_products cp ON cp.id = p.chain_product_id
        JOIN stores s ON s.id = p.store_id
        JOIN chains c ON c.id = s.chain_id
        WHERE s.city = $1
        ORDER BY cp.id, p.price_date DESC
        """,
        CITY,
    )

    prices = []
    for r in rows:
        regular = float(r["regular_price"] or 0)
        special = float(r["special_price"]) if r["special_price"] else None
        db_unit_price = float(r["unit_price"]) if r["unit_price"] else None
        best = special if (special and special < regular) else regular
        product_name = r["product_name"] or ""

        # Extract quantity from product name
        parsed_qty = extract_quantity(product_name)

        # Calculate normalized unit price (€/kg or €/L)
        # Prefer DB unit_price if available, otherwise calculate from name qty
        norm_price = None
        unit_type = ""
        if db_unit_price and db_unit_price > 0:
            # DB unit_price is typically €/kg or €/L
            norm_price = db_unit_price
            if parsed_qty:
                unit_type = parsed_qty.unit_type
            else:
                unit_type = "weight"  # assume weight if unknown
        elif parsed_qty:
            norm_price = calc_normalized_price(best, parsed_qty)
            unit_type = parsed_qty.unit_type

        prices.append(
            ChainPrice(
                chain=r["chain"],
                chain_product_code=r["product_code"],
                product_name=product_name,
                regular_price=regular,
                special_price=special,
                unit_price=db_unit_price,
                best_price=best,
                parsed_qty=parsed_qty,
                normalized_unit_price=norm_price,
                unit_type=unit_type,
            )
        )
    return prices


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------


def match_by_metro_code(
    item: PurchasedItem,
    prices_by_code: dict[str, list[ChainPrice]],
) -> PriceMatch | None:
    """Try to match by Metro product code (exact)."""
    if not item.sifra:
        return None

    metro_prices = prices_by_code.get(item.sifra, [])
    if not metro_prices:
        return None

    # Found the same product in Metro — now find it in other chains by fuzzy name
    metro_price = metro_prices[0]  # should be unique per code
    return PriceMatch(
        purchased=item,
        purchased_qty=None,  # will be set later
        purchased_norm_price=None,  # will be set later
        match_type="exact_code",
        fuzzy_score=None,
        metro_price=metro_price,
    )


def find_fuzzy_alternatives(
    item_name: str,
    all_prices: list[ChainPrice],
    normalized_cache: dict[int, str],
    exclude_chain: str | None = None,
    required_unit_type: str | None = None,
) -> list[tuple[ChainPrice, int]]:
    """
    Find fuzzy matches across all chains for a product name.

    If required_unit_type is set (weight/volume/piece), only returns
    alternatives with a compatible unit type.
    """
    norm_name = normalize_name(item_name)
    if len(norm_name) < 3:
        return []

    matches = []
    for i, price in enumerate(all_prices):
        if exclude_chain and price.chain == exclude_chain:
            continue

        # Filter by unit type compatibility when required
        if required_unit_type and required_unit_type in ("weight", "volume"):
            if price.unit_type != required_unit_type:
                continue

        norm_price_name = normalized_cache.get(i)
        if norm_price_name is None:
            norm_price_name = normalize_name(price.product_name)
            normalized_cache[i] = norm_price_name

        if len(norm_price_name) < 3:
            continue

        score = fuzz.token_sort_ratio(norm_name, norm_price_name)
        if score >= FUZZY_THRESHOLD:
            matches.append((price, score))

    # Sort by score descending, take top matches per chain
    matches.sort(key=lambda x: (-x[1], x[0].best_price))
    return matches


def build_matches(
    purchases: list[PurchasedItem],
    all_prices: list[ChainPrice],
) -> ComparisonReport:
    """Build the full comparison report."""
    report = ComparisonReport(
        run_date=date.today(),
        total_purchased_items=len(purchases),
        matched_items=0,
    )

    # Index Metro prices by product code for exact matching
    metro_by_code: dict[str, list[ChainPrice]] = {}
    for p in all_prices:
        if p.chain == "metro":
            metro_by_code.setdefault(p.chain_product_code, []).append(p)

    # Pre-compute normalized names cache
    normalized_cache: dict[int, str] = {}

    for item in purchases:
        if item.jedinicna_cijena <= 0:
            continue

        # Calculate normalized price for purchased item
        purchased_qty, purchased_norm_price = calc_purchased_norm_price(item)
        required_unit = purchased_qty.unit_type if purchased_qty else None

        # Step 1: try exact Metro code match
        match = match_by_metro_code(item, metro_by_code)

        if match:
            match.purchased_qty = purchased_qty
            match.purchased_norm_price = purchased_norm_price
            # Find alternatives across ALL chains (including non-Metro)
            fuzzy_alts = find_fuzzy_alternatives(
                item.opis, all_prices, normalized_cache,
                required_unit_type=required_unit,
            )
            # Keep best per chain — compare by normalized unit price
            seen_chains: dict[str, ChainPrice] = {}
            for alt, score in fuzzy_alts:
                key = alt.chain
                if key not in seen_chains:
                    seen_chains[key] = alt
                else:
                    # Prefer lower normalized unit price
                    existing = seen_chains[key]
                    new_price = alt.normalized_unit_price or alt.best_price
                    old_price = existing.normalized_unit_price or existing.best_price
                    if new_price < old_price:
                        seen_chains[key] = alt
            match.alternatives = list(seen_chains.values())
        else:
            # Step 2: fuzzy match across all chains
            fuzzy_alts = find_fuzzy_alternatives(
                item.opis, all_prices, normalized_cache,
                required_unit_type=required_unit,
            )
            if fuzzy_alts:
                best_score = fuzzy_alts[0][1]
                # Keep best per chain — compare by normalized unit price
                seen_chains: dict[str, ChainPrice] = {}
                for alt, score in fuzzy_alts:
                    key = alt.chain
                    if key not in seen_chains:
                        seen_chains[key] = alt
                    else:
                        existing = seen_chains[key]
                        new_price = alt.normalized_unit_price or alt.best_price
                        old_price = existing.normalized_unit_price or existing.best_price
                        if new_price < old_price:
                            seen_chains[key] = alt
                match = PriceMatch(
                    purchased=item,
                    purchased_qty=purchased_qty,
                    purchased_norm_price=purchased_norm_price,
                    match_type="fuzzy_name",
                    fuzzy_score=best_score,
                    metro_price=None,
                    alternatives=list(seen_chains.values()),
                )

        if match and match.alternatives:
            report.matched_items += 1
            best_alt = match.best_alternative
            if (
                best_alt
                and match.savings_per_unit >= MIN_SAVINGS_EUR
                and match.savings_pct >= MIN_SAVINGS_PCT
            ):
                report.matches_with_savings.append(match)
        elif match is None:
            report.unmatched_items.append(item)

    # Sort by savings potential (biggest first) — use normalized savings
    report.matches_with_savings.sort(
        key=lambda m: abs(m.savings_per_unit) * (m.purchased.kolicina if m.purchased_qty and m.purchased_qty.unit_type == "piece" else 1),
        reverse=True,
    )

    return report


# ---------------------------------------------------------------------------
# Email template (MJML)
# ---------------------------------------------------------------------------


def _fmt(n: float) -> str:
    """Format price in Croatian style."""
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(n: float) -> str:
    return f"{n:.0f}%"


def build_html(report: ComparisonReport) -> str:
    """Build MJML email and convert to HTML."""

    savings_rows = []
    for i, m in enumerate(report.matches_with_savings[:50]):  # limit to 50
        best = m.best_alternative
        if not best:
            continue

        bg = "#f8f9fa" if i % 2 == 0 else "#ffffff"
        match_icon = "🎯" if m.match_type == "exact_code" else "🔍"
        score_text = f" ({m.fuzzy_score}%)" if m.fuzzy_score else ""

        savings_color = "#28a745" if m.savings_pct >= 20 else "#ffc107"

        # Show unit price info
        unit_label = m.comparison_unit
        paid_display = f"{_fmt(m.purchased.jedinicna_cijena)}€"
        if m.purchased_norm_price and m.purchased_qty and m.purchased_qty.unit_type in ("weight", "volume"):
            paid_display += f"<br><span style='color:#888;font-size:11px'>{_fmt(m.purchased_norm_price)} {unit_label}</span>"

        alt_display = f"{_fmt(best.best_price)}€"
        if best.normalized_unit_price and best.unit_type in ("weight", "volume"):
            alt_display += f"<br><span style='color:#888;font-size:11px'>{_fmt(best.normalized_unit_price)} {unit_label}</span>"

        qty_info = ""
        if m.purchased_qty and m.purchased_qty.original:
            qty_info = f" · {m.purchased_qty.original}"
        alt_qty = ""
        if best.parsed_qty and best.parsed_qty.original:
            alt_qty = f" ({best.parsed_qty.original})"

        savings_rows.append(f"""
            <tr style="background:{bg}">
              <td style="padding:8px;border-bottom:1px solid #eee;font-size:13px">
                {match_icon} <strong>{m.purchased.opis}</strong>{score_text}
                <br><span style="color:#888;font-size:11px">{m.purchased.dobavljac}{qty_info} · {m.purchased.datum}</span>
              </td>
              <td style="padding:8px;border-bottom:1px solid #eee;font-size:13px;text-align:right">
                {paid_display}
              </td>
              <td style="padding:8px;border-bottom:1px solid #eee;font-size:13px">
                <strong style="color:#1a73e8">{best.chain.upper()}</strong><br>
                <span style="font-size:11px">{best.product_name[:50]}{alt_qty}</span>
              </td>
              <td style="padding:8px;border-bottom:1px solid #eee;font-size:13px;text-align:right">
                <strong>{alt_display}</strong>
              </td>
              <td style="padding:8px;border-bottom:1px solid #eee;font-size:13px;text-align:right">
                <span style="color:{savings_color};font-weight:bold">
                  -{_fmt(m.savings_per_unit)} {unit_label} ({_fmt_pct(m.savings_pct)})
                </span>
              </td>
            </tr>
        """)

    savings_table = "\n".join(savings_rows) if savings_rows else """
        <tr><td colspan="5" style="padding:20px;text-align:center;color:#888">
          Nema pronađenih ušteda za vaše nedavne nabavke 👍
        </td></tr>
    """

    total_savings = report.total_potential_savings

    mjml_template = f"""
    <mjml>
      <mj-head>
        <mj-attributes>
          <mj-all font-family="Segoe UI, Roboto, sans-serif" />
          <mj-text font-size="14px" color="#333" />
        </mj-attributes>
      </mj-head>
      <mj-body background-color="#f4f4f4">

        <!-- Header -->
        <mj-section background-color="#1a237e" padding="20px">
          <mj-column>
            <mj-text align="center" color="#fff" font-size="22px" font-weight="bold">
              💰 Usporedba cijena — Dubrovnik
            </mj-text>
            <mj-text align="center" color="#b3bbff" font-size="13px">
              {report.run_date.strftime('%d.%m.%Y.')} · Automatska analiza nabavki
            </mj-text>
          </mj-column>
        </mj-section>

        <!-- Summary cards -->
        <mj-section background-color="#ffffff" padding="15px 20px">
          <mj-column width="25%">
            <mj-text align="center" font-size="24px" font-weight="bold" color="#1a237e">
              {report.total_purchased_items}
            </mj-text>
            <mj-text align="center" font-size="11px" color="#888">
              STAVKI PREGLEDANO
            </mj-text>
          </mj-column>
          <mj-column width="25%">
            <mj-text align="center" font-size="24px" font-weight="bold" color="#1a73e8">
              {report.matched_items}
            </mj-text>
            <mj-text align="center" font-size="11px" color="#888">
              PRONAĐENO U LANCIMA
            </mj-text>
          </mj-column>
          <mj-column width="25%">
            <mj-text align="center" font-size="24px" font-weight="bold" color="#28a745">
              {len(report.matches_with_savings)}
            </mj-text>
            <mj-text align="center" font-size="11px" color="#888">
              JEFTINIJIH OPCIJA
            </mj-text>
          </mj-column>
          <mj-column width="25%">
            <mj-text align="center" font-size="24px" font-weight="bold" color="#e65100">
              {_fmt(total_savings)}€
            </mj-text>
            <mj-text align="center" font-size="11px" color="#888">
              POTENCIJALNA UŠTEDA
            </mj-text>
          </mj-column>
        </mj-section>

        <mj-section background-color="#e8f5e9" padding="10px 20px">
          <mj-column>
            <mj-text align="center" font-size="13px" color="#2e7d32">
              🎯 = točan match po šifri &nbsp;&nbsp; 🔍 = fuzzy match po nazivu
            </mj-text>
          </mj-column>
        </mj-section>

        <!-- Savings table -->
        <mj-section background-color="#ffffff" padding="0 10px">
          <mj-column>
            <mj-table>
              <tr style="background:#1a237e;color:#fff">
                <th style="padding:10px;text-align:left;font-size:12px">PROIZVOD</th>
                <th style="padding:10px;text-align:right;font-size:12px">PLAĆENO</th>
                <th style="padding:10px;text-align:left;font-size:12px">JEFTINIJI LANAC</th>
                <th style="padding:10px;text-align:right;font-size:12px">CIJENA</th>
                <th style="padding:10px;text-align:right;font-size:12px">UŠTEDA</th>
              </tr>
              {savings_table}
            </mj-table>
          </mj-column>
        </mj-section>

        <!-- Footer -->
        <mj-section background-color="#f4f4f4" padding="15px">
          <mj-column>
            <mj-text align="center" font-size="11px" color="#999">
              Cijene iz baze cijene-api ({CITY}) · Nabavke zadnjih 30 dana iz Atrium ERP-a
              <br>Automatski generirano · Ne odgovaraj na ovaj email
            </mj-text>
          </mj-column>
        </mj-section>

      </mj-body>
    </mjml>
    """

    result = mjml_to_html(mjml_template)
    return result.html


def build_text(report: ComparisonReport) -> str:
    """Build plain-text version of the report."""
    lines = [
        f"USPOREDBA CIJENA — DUBROVNIK ({report.run_date})",
        "=" * 50,
        f"Pregledano stavki: {report.total_purchased_items}",
        f"Pronađeno u lancima: {report.matched_items}",
        f"Jeftinijih opcija: {len(report.matches_with_savings)}",
        f"Potencijalna ušteda: {_fmt(report.total_potential_savings)}€",
        "",
        "DETALJI UŠTEDA:",
        "-" * 50,
    ]

    for m in report.matches_with_savings[:50]:
        best = m.best_alternative
        if not best:
            continue
        match_type = "ŠIFRA" if m.match_type == "exact_code" else f"FUZZY({m.fuzzy_score}%)"
        unit_label = m.comparison_unit

        paid_info = f"{_fmt(m.purchased.jedinicna_cijena)}€"
        if m.purchased_norm_price and m.purchased_qty and m.purchased_qty.unit_type in ("weight", "volume"):
            paid_info += f" ({_fmt(m.purchased_norm_price)} {unit_label})"

        alt_info = f"{_fmt(best.best_price)}€"
        if best.normalized_unit_price and best.unit_type in ("weight", "volume"):
            alt_info += f" ({_fmt(best.normalized_unit_price)} {unit_label})"

        qty_note = ""
        if m.purchased_qty:
            qty_note = f" [{m.purchased_qty.original}]"
        alt_qty_note = ""
        if best.parsed_qty:
            alt_qty_note = f" [{best.parsed_qty.original}]"

        lines.append(
            f"\n[{match_type}] {m.purchased.opis}{qty_note}"
            f"\n  Plaćeno: {paid_info} ({m.purchased.dobavljac})"
            f"\n  Jeftinije: {best.chain.upper()} — {best.product_name}{alt_qty_note} — {alt_info}"
            f"\n  Ušteda: -{_fmt(m.savings_per_unit)} {unit_label} ({_fmt_pct(m.savings_pct)})"
        )

    if not report.matches_with_savings:
        lines.append("Nema pronađenih ušteda za vaše nedavne nabavke.")

    return "\n".join(lines)


def build_subject(report: ComparisonReport) -> str:
    if report.matches_with_savings:
        return (
            f"[Cijene] Ušteda {_fmt(report.total_potential_savings)}€ — "
            f"{len(report.matches_with_savings)} jeftinijih opcija"
        )
    return f"[Cijene] Usporedba cijena {report.run_date} — bez ušteda"


# ---------------------------------------------------------------------------
# Email sending (reuses Mailgun config from report.py)
# ---------------------------------------------------------------------------


def send_comparison_email(report: ComparisonReport) -> bool:
    """Send the comparison report via Mailgun."""
    api_key = settings.mailgun_api_key
    domain = settings.mailgun_domain
    recipients = settings.report_recipients

    if not api_key or not domain or not recipients:
        logger.warning("Email not configured. Skipping comparison email.")
        return False

    recipient_list = [r.strip() for r in recipients.split(";") if r.strip()]
    if not recipient_list:
        logger.warning("No valid recipients. Skipping comparison email.")
        return False

    subject = build_subject(report)
    html_body = build_html(report)
    text_body = build_text(report)

    logger.info(f"Sending comparison email to {', '.join(recipient_list)}")

    try:
        response = httpx.post(
            f"https://api.eu.mailgun.net/v3/{domain}/messages",
            auth=("api", api_key),
            data={
                "from": f"Cijene Usporedba <noreply@{domain}>",
                "to": recipient_list,
                "subject": subject,
                "text": text_body,
                "html": html_body,
            },
            timeout=30,
        )
        response.raise_for_status()
        logger.info(f"Comparison email sent: {response.json()}")
        return True
    except Exception as e:
        logger.error(f"Failed to send comparison email: {e}")
        return False


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def run_comparison(skip_email: bool = False) -> ComparisonReport:
    """Run the full price comparison pipeline."""

    atrium_dsn = settings.atrium_database_url
    cijene_dsn = settings.db_dsn

    if not atrium_dsn:
        raise ValueError("ATRIUM_DATABASE_URL not configured")
    if not cijene_dsn:
        raise ValueError("DB_DSN not configured")

    logger.info("Connecting to Atrium DB...")
    atrium_conn = await asyncpg.connect(atrium_dsn)

    logger.info("Connecting to Cijene-API DB...")
    cijene_conn = await asyncpg.connect(cijene_dsn)

    try:
        # 1. Fetch recent purchases from Atrium
        logger.info("Fetching recent purchases from Atrium...")
        purchases = await fetch_recent_purchases(atrium_conn, days=30)
        logger.info(f"Found {len(purchases)} purchase items (last 30 days)")

        # 2. Fetch all Dubrovnik prices from cijene-api
        logger.info(f"Fetching {CITY} prices from cijene-api DB...")
        all_prices = await fetch_dubrovnik_prices(cijene_conn)
        logger.info(f"Found {len(all_prices)} product prices in {CITY}")

        # 3. Build comparison report
        logger.info("Matching products and finding savings...")
        report = build_matches(purchases, all_prices)
        logger.info(
            f"Results: {report.matched_items} matched, "
            f"{len(report.matches_with_savings)} with savings, "
            f"potential savings: {_fmt(report.total_potential_savings)}€"
        )

        # 4. Send email
        if not skip_email:
            send_comparison_email(report)
        else:
            logger.info("Skipping email (--skip-email)")
            # Print report to console instead
            print(build_text(report))

        return report

    finally:
        await atrium_conn.close()
        await cijene_conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Compare Atrium purchase prices with Dubrovnik chain prices"
    )
    parser.add_argument(
        "--skip-email", action="store_true", help="Skip sending email, print to console"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    asyncio.run(run_comparison(skip_email=args.skip_email))


if __name__ == "__main__":
    main()
