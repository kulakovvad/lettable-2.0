"""Internal consistency checks that decide whether parser labels are trusted as ground truth."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from core.models import Label

LISTINGS_DIR = Path("Source/02 - Live Listings/Listings Data/v2")
AREA_TOL = 0.01
LISTING_AREA_TOL = 0.02
LISTING_RENT_TOL = 0.01


@lru_cache(maxsize=1)
def _listings(listings_dir: str = str(LISTINGS_DIR)) -> dict[str, dict[str, str]]:
    base = Path(listings_dir)
    brochures, listings = base / "brochures.csv", base / "listings.csv"
    if not brochures.exists() or not listings.exists():
        return {}
    with open(listings, newline="") as fh:
        by_id = {r["listing_id"]: r for r in csv.DictReader(fh)}
    out: dict[str, dict[str, str]] = {}
    with open(brochures, newline="") as fh:
        for r in csv.DictReader(fh):
            doc_id = r["file"].split("_", 1)[0]
            if r["listing_id"] in by_id:
                out[doc_id] = by_id[r["listing_id"]]
    return out


def _num(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def listing_warnings(doc_id: str, labels: list[Label],
                     listing: dict[str, str] | None = None) -> list[str]:
    """Disagreements with the scraped listing. Informational: scraped sizes are often wrong."""
    listing = listing if listing is not None else _listings().get(doc_id)
    if not listing:
        return []
    prop = {lb.field: lb.value for lb in labels if lb.unit_id is None}
    total_rent, total_area = prop.get("total_rent"), prop.get("total_area_sqft")
    out: list[str] = []
    size = _num(listing.get("size_sqft"))
    if size and total_area and abs(size - int(total_area)) > LISTING_AREA_TOL * size:
        out.append(f"listing size_sqft {size:.0f} != total_area_sqft {total_area} (±2%)")
    price = _num(listing.get("price_gbp"))
    if (listing.get("price_basis") == "pa" and price and total_rent is not None
            and abs(price - int(total_rent)) > LISTING_RENT_TOL * price):
        out.append(f"listing price_gbp {price:.0f} pa != total_rent {total_rent} (±1%)")
    return out


def _listing_rent_matches(doc_id: str, rent: int, listing: dict[str, str] | None) -> bool:
    listing = listing if listing is not None else _listings().get(doc_id)
    if not listing or listing.get("price_basis") != "pa":
        return False
    price = _num(listing.get("price_gbp"))
    return bool(price) and abs(price - rent) <= LISTING_RENT_TOL * price


def reasons(doc_id: str, labels: list[Label], listing: dict[str, str] | None = None) -> list[str]:
    """Why the labels of one document cannot be trusted; empty list means agreed.

    Only the brochure's own totals decide; a single-unit table without a rent total falls back
    to the listing's annual price.
    """
    if not labels:
        return ["no tenancy table parsed"]
    prop = {lb.field: lb.value for lb in labels if lb.unit_id is None}
    units = {lb.unit_id for lb in labels if lb.unit_id is not None}
    rents = {lb.unit_id: lb.value for lb in labels if lb.field == "passing_rent"}
    areas = [lb.value for lb in labels if lb.field == "area_sqft"]
    out: list[str] = []

    if not units:
        out.append("no unit rows parsed")
    missing_rent = sorted(u for u in units if u not in rents)
    if missing_rent:
        out.append(f"passing_rent missing for {', '.join(missing_rent)}")

    total_rent = prop.get("total_rent")
    unit_rent = sum(int(v) for v in rents.values())
    if total_rent is None:
        if not (len(units) == 1 and rents and _listing_rent_matches(doc_id, unit_rent, listing)):
            out.append("total_rent missing in Total row")
    elif abs(unit_rent - int(total_rent)) > len(rents):
        # Pence are rounded per unit, so allow at most one pound of drift per unit.
        out.append(f"sum passing_rent {unit_rent} != total_rent {total_rent}")

    total_area = prop.get("total_area_sqft")
    unit_area = sum(int(v) for v in areas)
    if total_area is None:
        if areas:
            out.append("total_area_sqft missing in Total row")
    elif abs(unit_area - int(total_area)) > AREA_TOL * int(total_area):
        out.append(f"sum area_sqft {unit_area} != total_area_sqft {total_area} (±1%)")
    return out


def check(doc_id: str, labels: list[Label]) -> list[Label]:
    """Copies of the labels with status 'agreed' if every internal check passes, else 'disputed'."""
    status = "disputed" if reasons(doc_id, labels) else "agreed"
    return [lb.model_copy(update={"status": status}) for lb in labels]


def check_status(labels: list[Label], doc_id: str) -> tuple[str, list[str]]:
    why = reasons(doc_id, labels)
    return ("disputed" if why else "agreed"), why
