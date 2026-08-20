import csv
import io
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, session
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from auth import login_required
from db import AdMetric, AdMetricsSnapshot, db


metrics_bp = Blueprint("metrics", __name__)
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_DATE_RANGE_DAYS = 3660
ZERO = Decimal("0")

METRIC_FIELDS = (
    "ad_name",
    "spend",
    "cpm",
    "cpc",
    "ctr",
    "adds_to_cart",
    "purchases",
    "cost_per_purchase",
    "roas",
    "frequency",
)

HEADER_ALIASES = {
    "ad_name": {
        "ad",
        "ad name",
        "advertisement",
        "anuncio",
        "nome do anuncio",
    },
    "spend": {"spend", "amount spent", "gasto", "valor gasto"},
    "cpm": {"cpm", "cost per 1000 impressions", "cost per thousand impressions"},
    "cpc": {"cpc", "cost per click", "cost per link click"},
    "ctr": {"ctr", "click through rate", "link ctr"},
    "adds_to_cart": {
        "adds to cart",
        "add to cart",
        "cart additions",
        "adicoes ao carrinho",
    },
    "purchases": {"purchases", "purchase", "compras", "compra"},
    "cost_per_purchase": {
        "cost per purchase",
        "cost purchase",
        "custo compra",
        "custo por compra",
    },
    "roas": {"roas", "purchase roas", "return on ad spend"},
    "frequency": {"frequency", "freq", "frequencia"},
}

# Meta Ads CSV exports use long, parenthesised column titles
# (e.g. "Amount spent (EUR)", "CPM (cost per 1,000 impressions) (EUR)").
# After normalisation we match each column by a stable leading prefix rather
# than the full title, which keeps the mapping resilient to the currency or
# qualifier text Meta appends. "cost_per_purchase" is intentionally absent: the
# export has no such column, so it is derived from spend / purchases.
CSV_COLUMN_PREFIXES = {
    "ad_name": ("ad name",),
    "spend": ("amount spent",),
    "cpm": ("cpm",),
    "cpc": ("cpc",),
    "ctr": ("ctr",),
    "adds_to_cart": ("adds to cart",),
    "purchases": ("purchases",),
    "roas": ("purchase roas",),
    "frequency": ("frequency",),
}

CSV_REPORTING_PREFIXES = {
    "reporting_start": ("reporting starts",),
    "reporting_end": ("reporting ends",),
}


class MetricsParseError(ValueError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or []


@dataclass(frozen=True)
class ParsedMetrics:
    ads: list[dict]
    summary: dict
    reported_ad_count: int
    has_reported_total: bool
    reporting_start: date | None = None
    reporting_end: date | None = None


def _normalize_label(value):
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = normalized.casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


NORMALIZED_HEADER_ALIASES = {
    field: {_normalize_label(alias) for alias in aliases}
    for field, aliases in HEADER_ALIASES.items()
}


def _strip_markdown(value):
    value = value.strip()
    value = re.sub(r"(\*\*|__|~~|`)", "", value)
    return value.strip()


def _split_table_row(line):
    stripped = line.strip()
    if not stripped or "|" not in stripped:
        return []
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator_row(cells):
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells
    )


def _canonical_headers(cells):
    headers = {}
    for index, cell in enumerate(cells):
        normalized = _normalize_label(_strip_markdown(cell))
        for field, aliases in NORMALIZED_HEADER_ALIASES.items():
            if normalized in aliases:
                if field in headers:
                    return {}
                headers[field] = index
                break
    return headers


def _parse_decimal(value, field_name):
    cleaned = _strip_markdown(value)
    cleaned = "".join(
        character for character in cleaned
        if not character.isspace() and unicodedata.category(character) != "Cf"
    )
    cleaned = cleaned.replace("€", "").replace("EUR", "").replace("eur", "")
    cleaned = cleaned.replace("%", "")

    if not cleaned:
        raise ValueError(f"{field_name} is empty.")
    if cleaned in {"-", "--", "—", "–"}:
        return ZERO

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")

    try:
        result = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} is not a valid number.") from exc

    if not result.is_finite() or result < 0:
        raise ValueError(f"{field_name} must be a non-negative number.")
    return result


def _parse_integer(value, field_name):
    original = _strip_markdown(value)
    cleaned = "".join(
        character for character in original
        if not character.isspace() and unicodedata.category(character) != "Cf"
    )
    if not cleaned:
        raise ValueError(f"{field_name} is empty.")
    if cleaned in {"-", "--", "—", "–"}:
        return 0

    normalized_digits = []
    for character in cleaned:
        try:
            normalized_digits.append(str(unicodedata.decimal(character)))
        except (TypeError, ValueError):
            normalized_digits.append(character)
    cleaned = "".join(normalized_digits)

    if re.fullmatch(r"\d+", cleaned):
        return int(cleaned)

    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", cleaned):
        return int(cleaned.replace(".", "").replace(",", ""))

    decimal_candidate = cleaned
    if "," in decimal_candidate and "." in decimal_candidate:
        if decimal_candidate.rfind(",") > decimal_candidate.rfind("."):
            decimal_candidate = decimal_candidate.replace(".", "").replace(",", ".")
        else:
            decimal_candidate = decimal_candidate.replace(",", "")
    elif "," in decimal_candidate:
        decimal_candidate = decimal_candidate.replace(",", ".")

    try:
        numeric_value = Decimal(decimal_candidate)
    except InvalidOperation as exc:
        raise ValueError(
            f"{field_name} must be a non-negative whole number; "
            f"received {original!r}."
        ) from exc

    if (
        not numeric_value.is_finite()
        or numeric_value < 0
        or numeric_value != numeric_value.to_integral_value()
    ):
        raise ValueError(
            f"{field_name} must be a non-negative whole number; "
            f"received {original!r}."
        )
    return int(numeric_value)


def _safe_divide(numerator, denominator):
    if not denominator:
        return ZERO
    return numerator / denominator


def _aggregate_rows(rows):
    spend = sum((row["spend"] for row in rows), ZERO)
    adds_to_cart = sum(row["adds_to_cart"] for row in rows)
    purchases = sum(row["purchases"] for row in rows)
    revenue = sum((row["spend"] * row["roas"] for row in rows), ZERO)

    impressions = ZERO
    clicks = ZERO
    frequency_weight = ZERO
    for row in rows:
        row_impressions = (
            row["spend"] * Decimal("1000") / row["cpm"]
            if row["cpm"] > 0
            else ZERO
        )
        row_clicks = (
            row["spend"] / row["cpc"] if row["cpc"] > 0 else ZERO
        )
        impressions += row_impressions
        clicks += row_clicks
        frequency_weight += row["frequency"] * row_impressions

    return {
        "spend": spend,
        "cpm": _safe_divide(spend * Decimal("1000"), impressions),
        "cpc": _safe_divide(spend, clicks),
        "ctr": _safe_divide(clicks * Decimal("100"), impressions),
        "adds_to_cart": adds_to_cart,
        "purchases": purchases,
        "cost_per_purchase": _safe_divide(spend, Decimal(purchases)),
        "roas": _safe_divide(revenue, spend),
        "frequency": _safe_divide(frequency_weight, impressions),
    }


def _parse_data_row(cells, headers, line_number):
    if len(cells) <= max(headers.values()):
        raise ValueError("the row has fewer columns than the header.")

    ad_name = _strip_markdown(cells[headers["ad_name"]])
    if not ad_name:
        raise ValueError("Ad name is empty.")
    if len(ad_name) > 255:
        raise ValueError("Ad name must be 255 characters or fewer.")

    return {
        "ad_name": ad_name,
        "spend": _parse_decimal(cells[headers["spend"]], "Spend"),
        "cpm": _parse_decimal(cells[headers["cpm"]], "CPM"),
        "cpc": _parse_decimal(cells[headers["cpc"]], "CPC"),
        "ctr": _parse_decimal(cells[headers["ctr"]], "CTR"),
        "adds_to_cart": _parse_integer(
            cells[headers["adds_to_cart"]], "Adds to Cart"
        ),
        "purchases": _parse_integer(
            cells[headers["purchases"]], "Purchases"
        ),
        "cost_per_purchase": _parse_decimal(
            cells[headers["cost_per_purchase"]], "Cost per Purchase"
        ),
        "roas": _parse_decimal(cells[headers["roas"]], "ROAS"),
        "frequency": _parse_decimal(
            cells[headers["frequency"]], "Frequency"
        ),
        "_line_number": line_number,
    }


def parse_markdown_metrics(markdown_text):
    lines = markdown_text.splitlines()
    header_index = None
    headers = {}

    for index, line in enumerate(lines):
        cells = _split_table_row(line)
        if not cells:
            continue
        candidate = _canonical_headers(cells)
        if set(candidate) == set(METRIC_FIELDS):
            headers = candidate
            header_index = index
            break

    if header_index is None:
        raise MetricsParseError(
            "Could not find a metrics table with all required columns.",
            ["Required columns: Ad, Spend, CPM, CPC, CTR, Adds to Cart, "
             "Purchases, Cost per Purchase, ROAS, Frequency."],
        )

    separator_index = header_index + 1
    if separator_index >= len(lines) or not _is_separator_row(
        _split_table_row(lines[separator_index])
    ):
        raise MetricsParseError(
            "The metrics table header must be followed by a Markdown separator row."
        )

    ads = []
    total_row = None
    errors = []
    seen_ads = set()

    for index in range(separator_index + 1, len(lines)):
        cells = _split_table_row(lines[index])
        if not cells:
            break
        if _is_separator_row(cells):
            continue
        try:
            row = _parse_data_row(cells, headers, index + 1)
            normalized_name = row["ad_name"].casefold()
            if re.match(r"^total(?:\s|\(|$)", normalized_name):
                if total_row is not None:
                    raise ValueError("only one TOTAL row is allowed.")
                total_row = row
                continue
            if normalized_name in seen_ads:
                raise ValueError(f'duplicate ad name "{row["ad_name"]}".')
            seen_ads.add(normalized_name)
            ads.append(row)
        except ValueError as exc:
            errors.append(f"Line {index + 1}: {exc}")

    if errors:
        raise MetricsParseError(
            "The Markdown contains invalid metric rows.", errors
        )
    if not ads:
        raise MetricsParseError("The metrics table does not contain any ads.")

    for row in ads:
        row.pop("_line_number", None)

    if total_row:
        total_name = total_row.pop("ad_name")
        total_row.pop("_line_number", None)
        count_match = re.search(r"\(([\d.,]+)\s*(?:ads?|an[uú]ncios?)?\)", total_name, re.I)
        reported_ad_count = (
            _parse_integer(count_match.group(1), "Reported ad count")
            if count_match
            else len(ads)
        )
        if reported_ad_count < len(ads):
            raise MetricsParseError(
                "The TOTAL row reports fewer ads than the table contains."
            )
        summary = total_row
        has_reported_total = True
    else:
        reported_ad_count = len(ads)
        summary = _aggregate_rows(ads)
        has_reported_total = False

    return ParsedMetrics(
        ads=ads,
        summary=summary,
        reported_ad_count=reported_ad_count,
        has_reported_total=has_reported_total,
    )


def _csv_decimal(value, field_name):
    if value is None or not value.strip():
        return ZERO
    return _parse_decimal(value, field_name)


def _csv_integer(value, field_name):
    if value is None or not value.strip():
        return 0
    return _parse_integer(value, field_name)


def _safe_iso_date(raw_value):
    try:
        return date.fromisoformat((raw_value or "").strip())
    except ValueError:
        return None


def _csv_header_map(cells):
    """Map column indexes for a candidate Meta Ads CSV header row.

    Each column title is normalised then matched against a known leading
    prefix. The first column that matches a field wins, so trailing duplicate
    columns are ignored.
    """
    mapping = {}
    prefixes = {**CSV_COLUMN_PREFIXES, **CSV_REPORTING_PREFIXES}
    for index, cell in enumerate(cells):
        normalized = _normalize_label(_strip_markdown(cell))
        if not normalized:
            continue
        for field, field_prefixes in prefixes.items():
            if field in mapping:
                continue
            if any(
                normalized == prefix or normalized.startswith(prefix + " ")
                for prefix in field_prefixes
            ):
                mapping[field] = index
                break
    return mapping


def _parse_csv_data_row(cells, mapping):
    """Parse one Meta Ads CSV row.

    Returns ``None`` for rows that should be ignored (too short, no ad name, or
    no spend) and raises ``ValueError`` for genuinely malformed numbers.
    """
    last_required = max(mapping[field] for field in CSV_COLUMN_PREFIXES)
    if len(cells) <= last_required:
        return None

    ad_name = _strip_markdown(cells[mapping["ad_name"]])
    if not ad_name:
        return None

    spend = _csv_decimal(cells[mapping["spend"]], "Spend")
    if spend <= ZERO:
        # Inactive / never-delivered ads add only noise to the breakdown.
        return None

    purchases = _csv_integer(cells[mapping["purchases"]], "Purchases")
    return {
        "ad_name": ad_name[:255],
        "spend": spend,
        "cpm": _csv_decimal(cells[mapping["cpm"]], "CPM"),
        "cpc": _csv_decimal(cells[mapping["cpc"]], "CPC"),
        "ctr": _csv_decimal(cells[mapping["ctr"]], "CTR"),
        "adds_to_cart": _csv_integer(
            cells[mapping["adds_to_cart"]], "Adds to Cart"
        ),
        "purchases": purchases,
        "cost_per_purchase": _safe_divide(spend, Decimal(purchases)),
        "roas": _csv_decimal(cells[mapping["roas"]], "ROAS"),
        "frequency": _csv_decimal(cells[mapping["frequency"]], "Frequency"),
    }


def _build_period_metrics(rows_by_name, reporting_start, reporting_end):
    """Combine a period's rows (grouped by ad name) into one ParsedMetrics."""
    ads = []
    for group in rows_by_name.values():
        if len(group) == 1:
            ads.append(group[0])
        else:
            ads.append({"ad_name": group[0]["ad_name"], **_aggregate_rows(group)})

    return ParsedMetrics(
        ads=ads,
        summary=_aggregate_rows(ads),
        reported_ad_count=len(ads),
        has_reported_total=False,
        reporting_start=reporting_start,
        reporting_end=reporting_end,
    )


def parse_csv_metrics(csv_text):
    """Parse a Meta Ads Manager CSV export into one ParsedMetrics per period.

    Meta exports come in two shapes, and rows are grouped by their per-row
    ``(Reporting starts, Reporting ends)`` pair so both are handled the same way:

    * a single aggregated range (every row shares one reporting period) yields
      one ``ParsedMetrics`` keyed to the range's end date;
    * a "By day" breakdown (one row per ad per day) yields one ``ParsedMetrics``
      per day.

    Within each period the same creative can appear once per ad set, so rows are
    grouped by ad name and combined with the weighted aggregation used elsewhere,
    giving one row per creative that fits the ``(snapshot_id, ad_name)``
    uniqueness constraint. The list is returned ordered by reporting period.
    """
    rows = list(csv.reader(io.StringIO(csv_text)))

    mapping = None
    header_index = None
    for index, cells in enumerate(rows):
        candidate = _csv_header_map(cells)
        if all(field in candidate for field in CSV_COLUMN_PREFIXES):
            mapping = candidate
            header_index = index
            break

    if mapping is None:
        raise MetricsParseError(
            "Could not find the Meta Ads columns in this CSV.",
            [
                "Required columns: Ad name, Amount spent, CPM, CPC, CTR, "
                "Adds to cart, Purchases, Purchase ROAS, Frequency."
            ],
        )

    periods = {}
    period_order = []
    errors = []

    for index in range(header_index + 1, len(rows)):
        cells = rows[index]
        if not any(cell.strip() for cell in cells):
            continue

        period_start = None
        period_end = None
        if "reporting_start" in mapping and len(cells) > mapping["reporting_start"]:
            period_start = _safe_iso_date(cells[mapping["reporting_start"]])
        if "reporting_end" in mapping and len(cells) > mapping["reporting_end"]:
            period_end = _safe_iso_date(cells[mapping["reporting_end"]])

        try:
            row = _parse_csv_data_row(cells, mapping)
        except ValueError as exc:
            errors.append(f"Row {index + 1}: {exc}")
            continue
        if row is None:
            continue

        key = (period_start, period_end)
        if key not in periods:
            periods[key] = {}
            period_order.append(key)
        periods[key].setdefault(row["ad_name"].casefold(), []).append(row)

    if errors:
        raise MetricsParseError("The CSV contains invalid metric rows.", errors)
    if not periods:
        raise MetricsParseError(
            "The CSV does not contain any ads with spend."
        )

    results = [
        _build_period_metrics(periods[key], key[0], key[1])
        for key in period_order
    ]
    results.sort(
        key=lambda parsed: (
            parsed.reporting_end or date.min,
            parsed.reporting_start or date.min,
        )
    )
    return results


def _parse_iso_date(raw_value, field_name):
    try:
        return date.fromisoformat((raw_value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format.") from exc


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _snapshot_summary(snapshot):
    return {
        "spend": _decimal(snapshot.total_spend),
        "cpm": _decimal(snapshot.total_cpm),
        "cpc": _decimal(snapshot.total_cpc),
        "ctr": _decimal(snapshot.total_ctr),
        "adds_to_cart": snapshot.total_adds_to_cart,
        "purchases": snapshot.total_purchases,
        "cost_per_purchase": _decimal(snapshot.total_cost_per_purchase),
        "roas": _decimal(snapshot.total_roas),
        "frequency": _decimal(snapshot.total_frequency),
    }


def _serialize_metrics(metrics):
    return {
        "spend": float(metrics["spend"]),
        "cpm": float(metrics["cpm"]),
        "cpc": float(metrics["cpc"]),
        "ctr": float(metrics["ctr"]),
        "adds_to_cart": int(metrics["adds_to_cart"]),
        "purchases": int(metrics["purchases"]),
        "cost_per_purchase": float(metrics["cost_per_purchase"]),
        "roas": float(metrics["roas"]),
        "frequency": float(metrics["frequency"]),
    }


def _latest_import(user_id):
    return (
        AdMetricsSnapshot.query
        .filter_by(user_id=user_id)
        .order_by(AdMetricsSnapshot.imported_at.desc())
        .first()
    )


def _today():
    """Indirection so the "completed day only" rule is easy to pin in tests."""
    return date.today()


def _upsert_snapshot(user_id, metric_date, parsed, filename):
    """Insert or replace a single day's snapshot. Does not commit.

    The caller owns the surrounding transaction so several days from one
    per-day export can be written atomically.
    """
    summary = parsed.summary
    snapshot = AdMetricsSnapshot.query.filter_by(
        user_id=user_id, metric_date=metric_date
    ).first()
    if snapshot is None:
        snapshot = AdMetricsSnapshot(user_id=user_id, metric_date=metric_date)
        db.session.add(snapshot)
    else:
        # Flush the explicit delete before inserting rows with the same
        # (snapshot_id, ad_name) unique keys. The transaction rollback restores
        # the previous rows if any later insert fails.
        db.session.execute(
            delete(AdMetric).where(AdMetric.snapshot_id == snapshot.id)
        )
        db.session.flush()

    snapshot.filename = filename
    snapshot.reported_ad_count = parsed.reported_ad_count
    snapshot.has_reported_total = parsed.has_reported_total
    snapshot.total_spend = summary["spend"]
    snapshot.total_cpm = summary["cpm"]
    snapshot.total_cpc = summary["cpc"]
    snapshot.total_ctr = summary["ctr"]
    snapshot.total_adds_to_cart = summary["adds_to_cart"]
    snapshot.total_purchases = summary["purchases"]
    snapshot.total_cost_per_purchase = summary["cost_per_purchase"]
    snapshot.total_roas = summary["roas"]
    snapshot.total_frequency = summary["frequency"]
    snapshot.imported_at = datetime.now(timezone.utc)

    db.session.add_all(
        [
            AdMetric(
                snapshot=snapshot,
                ad_name=row["ad_name"],
                spend=row["spend"],
                cpm=row["cpm"],
                cpc=row["cpc"],
                ctr=row["ctr"],
                adds_to_cart=row["adds_to_cart"],
                purchases=row["purchases"],
                cost_per_purchase=row["cost_per_purchase"],
                roas=row["roas"],
                frequency=row["frequency"],
            )
            for row in parsed.ads
        ]
    )
    return snapshot


@metrics_bp.route("/metrics")
@login_required
def metrics_page():
    user_id = session["user_id"]
    latest_snapshot = (
        AdMetricsSnapshot.query
        .filter_by(user_id=user_id)
        .order_by(AdMetricsSnapshot.metric_date.desc())
        .first()
    )
    latest_import = _latest_import(user_id)
    # The current day is never complete, so the upload picker only offers
    # finished days (yesterday and earlier).
    last_complete_day = _today() - timedelta(days=1)
    return render_template(
        "metrics.html",
        latest_date=(
            latest_snapshot.metric_date.isoformat() if latest_snapshot else ""
        ),
        default_upload_date=last_complete_day.isoformat(),
        max_upload_date=last_complete_day.isoformat(),
        latest_import=latest_import,
    )


@metrics_bp.route("/metrics/import", methods=["POST"])
@login_required
def import_metrics():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "Choose a Markdown or CSV file to upload."}), 400
    suffix = Path(upload.filename).suffix.casefold()
    if suffix not in {".md", ".csv"}:
        return jsonify(
            {"error": "Only .md (Markdown) and .csv files are supported."}
        ), 400

    raw_bytes = upload.stream.read(MAX_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        return jsonify({"error": "The file must be 2 MB or smaller."}), 413
    if not raw_bytes:
        return jsonify({"error": "The uploaded file is empty."}), 400

    try:
        file_text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"error": "The file must be UTF-8 encoded."}), 400

    # Markdown carries no date of its own, so it relies on the form field. A
    # Meta CSV is authoritative about its own reporting window: each period's
    # "Reporting ends" date is used, falling back to the form field if absent.
    # day_groups maps metric_date -> ParsedMetrics for that day/period.
    day_groups = {}
    form_date = None
    try:
        if suffix == ".csv":
            for parsed in parse_csv_metrics(file_text):
                metric_date = parsed.reporting_end
                if metric_date is None:
                    if form_date is None:
                        form_date = _parse_iso_date(
                            request.form.get("metric_date"), "Metric date"
                        )
                    metric_date = form_date
                # A per-day export holds distinct dates; on the off chance two
                # rows share a key the later one wins.
                day_groups[metric_date] = parsed
        else:
            metric_date = _parse_iso_date(
                request.form.get("metric_date"), "Metric date"
            )
            day_groups[metric_date] = parse_markdown_metrics(file_text)
    except MetricsParseError as exc:
        return jsonify({"error": str(exc), "details": exc.details}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # The current day is never complete, so reject (single file) or skip
    # (per-day export) anything dated today or later.
    today = _today()
    skipped_incomplete = sorted(
        metric_date for metric_date in day_groups if metric_date >= today
    )
    import_dates = sorted(
        metric_date for metric_date in day_groups if metric_date < today
    )

    if not import_dates:
        return jsonify(
            {
                "error": (
                    "Metrics can only be imported for completed days. "
                    "Today is still in progress, so choose an earlier date."
                ),
                "skipped_incomplete": [d.isoformat() for d in skipped_incomplete],
            }
        ), 400

    user_id = session["user_id"]
    filename = Path(upload.filename).name[:255]
    replace_existing = (
        request.form.get("replace_existing", "").strip().casefold()
        in {"1", "true", "yes", "on"}
    )

    existing_by_date = {
        snapshot.metric_date: snapshot
        for snapshot in AdMetricsSnapshot.query.filter(
            AdMetricsSnapshot.user_id == user_id,
            AdMetricsSnapshot.metric_date.in_(import_dates),
        ).all()
    }
    conflict_dates = sorted(existing_by_date)

    if conflict_dates and not replace_existing:
        if len(import_dates) == 1:
            metric_date = import_dates[0]
            snapshot = existing_by_date[metric_date]
            parsed = day_groups[metric_date]
            return jsonify(
                {
                    "error": (
                        "Metrics already exist for this date. "
                        "Confirm replacement to continue."
                    ),
                    "conflict": True,
                    "metric_date": metric_date.isoformat(),
                    "existing": {
                        "filename": snapshot.filename,
                        "imported_at": snapshot.imported_at.isoformat(),
                        "detailed_ad_count": len(snapshot.metrics),
                        "reported_ad_count": snapshot.reported_ad_count,
                    },
                    "incoming": {
                        "filename": filename,
                        "detailed_ad_count": len(parsed.ads),
                        "reported_ad_count": parsed.reported_ad_count,
                    },
                }
            ), 409
        return jsonify(
            {
                "error": (
                    f"{len(conflict_dates)} of {len(import_dates)} days in this "
                    "file already have metrics. Confirm replacement to continue."
                ),
                "conflict": True,
                "conflict_dates": [d.isoformat() for d in conflict_dates],
                "import_dates": [d.isoformat() for d in import_dates],
            }
        ), 409

    try:
        for metric_date in import_dates:
            _upsert_snapshot(
                user_id, metric_date, day_groups[metric_date], filename
            )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        logger.exception(
            "Metrics import conflict for user_id=%s dates=%s",
            user_id,
            import_dates,
        )
        return jsonify(
            {
                "error": (
                    "Metrics for these dates changed during import. "
                    "Reload the page and try again."
                ),
                "conflict": True,
                "conflict_dates": [d.isoformat() for d in conflict_dates],
            }
        ), 409
    except Exception:
        db.session.rollback()
        logger.exception(
            "Failed to import ad metrics for user_id=%s dates=%s",
            user_id,
            import_dates,
        )
        return jsonify({"error": "Failed to save the imported metrics."}), 500

    detailed_ad_count = sum(len(day_groups[d].ads) for d in import_dates)
    reported_ad_count = sum(day_groups[d].reported_ad_count for d in import_dates)
    reporting_ends = [
        day_groups[d].reporting_end
        for d in import_dates
        if day_groups[d].reporting_end
    ]
    reporting_starts = [
        day_groups[d].reporting_start
        for d in import_dates
        if day_groups[d].reporting_start
    ]
    return jsonify(
        {
            "ok": True,
            "replaced": bool(conflict_dates),
            "metric_date": import_dates[-1].isoformat(),
            "imported_dates": [d.isoformat() for d in import_dates],
            "replaced_dates": [d.isoformat() for d in conflict_dates],
            "skipped_incomplete": [d.isoformat() for d in skipped_incomplete],
            "days_imported": len(import_dates),
            "detailed_ad_count": detailed_ad_count,
            "reported_ad_count": reported_ad_count,
            "partial_coverage": reported_ad_count > detailed_ad_count,
            "source_format": "csv" if suffix == ".csv" else "markdown",
            "reporting_start": (
                min(reporting_starts).isoformat() if reporting_starts else None
            ),
            "reporting_end": (
                max(reporting_ends).isoformat() if reporting_ends else None
            ),
        }
    )


@metrics_bp.route("/metrics/data")
@login_required
def metrics_data():
    try:
        start_date = _parse_iso_date(
            request.args.get("start_date"), "Start date"
        )
        end_date = _parse_iso_date(request.args.get("end_date"), "End date")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if start_date > end_date:
        return jsonify({"error": "Start date cannot be after end date."}), 400
    range_days = (end_date - start_date).days + 1
    if range_days > MAX_DATE_RANGE_DAYS:
        return jsonify(
            {"error": f"Date range cannot exceed {MAX_DATE_RANGE_DAYS} days."}
        ), 400

    user_id = session["user_id"]
    snapshots = (
        AdMetricsSnapshot.query
        .options(selectinload(AdMetricsSnapshot.metrics))
        .filter(
            AdMetricsSnapshot.user_id == user_id,
            AdMetricsSnapshot.metric_date >= start_date,
            AdMetricsSnapshot.metric_date <= end_date,
        )
        .order_by(AdMetricsSnapshot.metric_date.asc())
        .all()
    )

    available_dates = {snapshot.metric_date for snapshot in snapshots}
    missing_dates = [
        (start_date + timedelta(days=offset)).isoformat()
        for offset in range(range_days)
        if start_date + timedelta(days=offset) not in available_dates
    ]

    summary_rows = [_snapshot_summary(snapshot) for snapshot in snapshots]
    kpis = _aggregate_rows(summary_rows) if summary_rows else _aggregate_rows([])

    daily = []
    detailed_rows = 0
    reported_ads = 0
    grouped_ads = {}
    for snapshot in snapshots:
        snapshot_summary = _snapshot_summary(snapshot)
        daily.append(
            {
                "date": snapshot.metric_date.isoformat(),
                **_serialize_metrics(snapshot_summary),
            }
        )
        detailed_rows += len(snapshot.metrics)
        reported_ads += max(snapshot.reported_ad_count, len(snapshot.metrics))

        for metric in snapshot.metrics:
            grouped_ads.setdefault(metric.ad_name.casefold(), {
                "ad_name": metric.ad_name,
                "rows": [],
            })["rows"].append({
                "spend": _decimal(metric.spend),
                "cpm": _decimal(metric.cpm),
                "cpc": _decimal(metric.cpc),
                "ctr": _decimal(metric.ctr),
                "adds_to_cart": metric.adds_to_cart,
                "purchases": metric.purchases,
                "cost_per_purchase": _decimal(metric.cost_per_purchase),
                "roas": _decimal(metric.roas),
                "frequency": _decimal(metric.frequency),
            })

    ads = []
    for grouped in grouped_ads.values():
        ads.append({
            "ad_name": grouped["ad_name"],
            **_serialize_metrics(_aggregate_rows(grouped["rows"])),
        })
    ads.sort(key=lambda item: item["spend"], reverse=True)

    latest_import = _latest_import(user_id)
    latest_import_payload = None
    if latest_import:
        latest_import_payload = {
            "filename": latest_import.filename,
            "metric_date": latest_import.metric_date.isoformat(),
            "imported_at": latest_import.imported_at.isoformat(),
            "detailed_ad_count": len(latest_import.metrics),
            "reported_ad_count": latest_import.reported_ad_count,
        }

    return jsonify(
        {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days_requested": range_days,
            "days_with_data": len(snapshots),
            "missing_dates": missing_dates,
            "kpis": _serialize_metrics(kpis),
            "daily": daily,
            "ads": ads,
            "coverage": {
                "detailed_ad_rows": detailed_rows,
                "reported_ad_rows": reported_ads,
                "partial": reported_ads > detailed_rows,
            },
            "latest_import": latest_import_payload,
            "frequency_is_estimated": True,
        }
    )
