"""Money, quantity and GST arithmetic.

Rules used everywhere in the app:
  * Money is stored as integer paise.
  * Quantities are stored as integer thousandths ("milli") of the item's base
    unit (kg, litre, piece).  Finished goods are stored as milli-packets.
  * Decimal is used for every intermediate calculation; floats never touch money.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

ALIASES = {
    "litre": "l", "liter": "l", "ltr": "l", "lt": "l", "litres": "l",
    "piece": "pc", "pieces": "pc", "pcs": "pc", "nos": "pc", "packet": "pkt", "packets": "pkt",
    "kgs": "kg", "gm": "g", "gram": "g", "grams": "g",
}
# unit -> (dimension, size of the unit in the smallest unit of that dimension)
UNIT_FACTORS = {
    "kg": ("mass", 1000), "g": ("mass", 1),
    "l": ("vol", 1000), "ml": ("vol", 1),
    "pc": ("count", 1), "pkt": ("count", 1),
}
BASE_UNITS = ("kg", "l", "pc")


def D(x) -> Decimal:
    try:
        return Decimal(str(x).strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"Invalid number: {x!r}")


def rnd(x) -> int:
    """Round half-up to an integer."""
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def norm_unit(u: str) -> str:
    u = (u or "").strip().lower()
    return ALIASES.get(u, u)


def to_milli(qty, unit: str, base_unit: str) -> int:
    """Convert `qty` expressed in `unit` into milli-units of `base_unit`."""
    u, b = norm_unit(unit), norm_unit(base_unit)
    if u not in UNIT_FACTORS or b not in UNIT_FACTORS:
        raise ValueError(f"Unknown unit: {unit!r}")
    (du, fu), (db, fb) = UNIT_FACTORS[u], UNIT_FACTORS[b]
    if du != db:
        raise ValueError(f"Cannot convert {unit} to {base_unit}")
    return rnd(D(qty) * Decimal(fu) / Decimal(fb) * 1000)


def milli_to_str(m: int, places: int = 3) -> str:
    d = Decimal(m) / 1000
    s = f"{d:.{places}f}".rstrip("0").rstrip(".")
    return s or "0"


def rupees_to_paise(x) -> int:
    return rnd(D(x) * 100)


def paise_to_str(p: int) -> str:
    sign = "-" if p < 0 else ""
    p = abs(int(p))
    return f"{sign}{p // 100}.{p % 100:02d}"


def pct_bp(actual: int, planned: int):
    """Variance in basis points ((actual-planned)/planned, 1% = 100bp) or None."""
    if not planned:
        return None
    return rnd(Decimal(actual - planned) * 10000 / Decimal(planned))


def beyond_tolerance(actual: int, planned: int, tol_bp: int) -> bool:
    """Exact integer test for |actual-planned|/planned > tolerance (no rounding)."""
    if not planned:
        return actual != 0
    return abs(actual - planned) * 10000 > tol_bp * planned


def compute_tax(taxable_paise: int, rate_x100: int, intra_state: bool) -> dict:
    """GST on one invoice line.  rate_x100: 5% -> 500.

    Intra-state: CGST and SGST are each half the rate, rounded half-up to paise.
    Inter-state: IGST at the full rate.
    """
    if intra_state:
        half = rnd(Decimal(taxable_paise) * rate_x100 / 20000)  # taxable * (rate/2)%
        return {"cgst": half, "sgst": half, "igst": 0}
    return {"cgst": 0, "sgst": 0, "igst": rnd(Decimal(taxable_paise) * rate_x100 / Decimal(10000))}


def fy_of(date_iso: str) -> str:
    y, m = int(date_iso[:4]), int(date_iso[5:7])
    start = y if m >= 4 else y - 1
    return f"{start}-{str(start + 1)[2:]}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
         "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n):
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _below_1000(n):
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n:
        out.append(_below_100(n))
    return " ".join(out)


def rupees_in_words(paise: int) -> str:
    rupees, ps = divmod(abs(int(paise)), 100)
    parts = []
    for div, name in ((10**7, "Crore"), (10**5, "Lakh"), (1000, "Thousand")):
        if rupees >= div:
            parts.append(_below_100(rupees // div) + " " + name)
            rupees %= div
    if rupees:
        parts.append(_below_1000(rupees))
    words = " ".join(parts) or "Zero"
    out = f"Rupees {words}"
    if ps:
        out += f" and {_below_100(ps)} Paise"
    return out + " Only"


STATES = {
    "Andhra Pradesh": "37", "Karnataka": "29", "Kerala": "32", "Tamil Nadu": "33", "Telangana": "36",
    "Maharashtra": "27", "Gujarat": "24", "Delhi": "07", "Puducherry": "34", "Goa": "30",
    "West Bengal": "19", "Rajasthan": "08", "Uttar Pradesh": "09", "Odisha": "21", "Punjab": "03",
}
