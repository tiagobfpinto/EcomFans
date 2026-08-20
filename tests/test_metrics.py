import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

import metrics
from db import AdMetricsSnapshot, User, db
from metrics import (
    MetricsParseError,
    _parse_integer,
    parse_csv_metrics,
    parse_markdown_metrics,
)
from tests.conftest import csrf_token


# Pin "today" so the "completed day only" rule is independent of the wall clock.
FIXED_TODAY = date(2026, 6, 16)


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    monkeypatch.setattr(metrics, "_today", lambda: FIXED_TODAY)


SAMPLE_MARKDOWN = """\
# Ads for 1 Campaign — Jun 8 a Jun 14, 2026

## Métricas por anúncio

| Anúncio | Gasto | CPM | CPC | CTR | Adds to Cart | Compras | Custo/Compra | ROAS | Freq. |
|---------|------:|----:|----:|----:|------:|------:|------:|----:|----:|
| **Video 311** | €5.408,12 | €42,89 | €1,25 | 3,43% | 673 | 243 | €22,26 | **1,56** | 1,14 |
| **Video 260** | €1.206,63 | €35,94 | €1,16 | 3,09% | 137 | 44 | €27,42 | 1,31 | 1,15 |
| **Video 295** | €444,12 | €37,35 | €1,21 | 3,09% | 62 | 16 | €27,76 | 1,22 | 1,04 |
| **Video 217** | €460,88 | €38,23 | €1,33 | 2,88% | 45 | 15 | €30,73 | 1,17 | 1,10 |
| **Video 344** | €352,24 | €60,01 | €1,60 | 3,75% | 50 | 12 | €29,35 | 1,14 | 1,23 |
| **Video 331** | €220,55 | €38,87 | €1,47 | 2,64% | 22 | 9 | €24,51 | 1,44 | 1,21 |
| **Video 327** | €185,54 | €43,67 | €1,18 | 3,69% | 24 | 5 | €37,11 | **0,94** | 1,04 |
| **TOTAL (75 ads)** | €8.692,32 | €41,94 | €1,27 | 3,30% | 1.062 | 365 | €23,81 | **1,46** | 1,33 |
"""

ENGLISH_WITHOUT_TOTAL = """\
| Ad | Amount spent | CPM | CPC | CTR | Adds to Cart | Purchases | Cost per Purchase | Purchase ROAS | Frequency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Ad One | €100.00 | €20.00 | €1.00 | 2.00% | 10 | 4 | €25.00 | 2.00 | 1.20 |
| Ad Two | €50.00 | €25.00 | €2.00 | 1.25% | 5 | 1 | €50.00 | 1.00 | 1.10 |
"""

# Real Meta Ads CSV export header, with a high-spend ad, a sparse ad with
# empty conversion cells, a zero-spend (never delivered) row, and the same ad
# name split across two ad sets to exercise aggregation.
META_CSV = (
    '"Reporting starts","Reporting ends","Ad name","Ad delivery",'
    '"Ad set name","Ad set budget","Ad set budget type","Amount spent (EUR)",'
    '"CPM (cost per 1,000 impressions) (EUR)","CPC (cost per link click) (EUR)",'
    '"CTR (link click-through rate)","Adds to cart",Results,"Result indicator",'
    '"Cost per results","Purchase ROAS (return on ad spend)",Frequency,'
    '"Cost per add to cart (EUR)","3-second video plays rate per impressions",'
    '"Video plays at 50%","Link clicks",Impressions,"Ad ID",Reach,'
    '"Results value","Result value indicator",Purchases\n'
    "2026-06-12,2026-06-14,Video 311,active,Korean Secret,Using campaign budget,"
    "0,2557.92,45.471708,1.275771,3.564254,326,110,"
    "actions:offsite_conversion.fb_pixel_purchase,23.25381818,1.470386,1.088192,"
    "7.84638,41.956873,2722,2005,56253,120247475885180596,51694,3761.13,"
    "action_values:offsite_conversion.fb_pixel_purchase,110\n"
    "2026-06-12,2026-06-14,Video 297,active,Korean Secret,Using campaign budget,"
    "0,4.39,65.522388,1.0975,5.970149,,,,,,1.030769,,43.283582,7,4,67,"
    "120246849180540596,65,,,\n"
    "2026-06-12,2026-06-14,Video 279,active,All Creatives,Using campaign budget,"
    "0,0,0,,,,,,,,0,,,,,0,120246098346390596,0,,,\n"
    "2026-06-12,2026-06-14,Split Ad,active,Set A,Using campaign budget,"
    "0,100,50,1,2,10,4,actions:offsite_conversion.fb_pixel_purchase,25,2,1.1,10,"
    "40,40,5,100,120000000000000001,90,200,"
    "action_values:offsite_conversion.fb_pixel_purchase,4\n"
    "2026-06-12,2026-06-14,Split Ad,active,Set B,Using campaign budget,"
    "0,300,50,1,2,30,12,actions:offsite_conversion.fb_pixel_purchase,25,2,1.1,10,"
    "40,40,15,300,120000000000000002,270,600,"
    "action_values:offsite_conversion.fb_pixel_purchase,12\n"
)

META_HEADER = META_CSV.split("\n", 1)[0]


def _meta_row(
    start,
    end,
    ad_name,
    spend,
    purchases,
    *,
    ad_set="Korean Secret",
    cpm="40",
    cpc="1",
    ctr="3",
    adds=0,
    roas="1.5",
    freq="1.1",
    ad_id="120000000000000000",
):
    """Build one Meta Ads CSV row (27 columns) with sensible blanks."""
    cols = [""] * 27
    cols[0], cols[1], cols[2] = start, end, ad_name
    cols[3], cols[4] = "active", ad_set
    cols[5], cols[6] = "0", "Using campaign budget"
    cols[7] = str(spend)
    cols[8], cols[9], cols[10] = cpm, cpc, ctr
    cols[11] = str(adds)
    cols[12] = str(purchases)
    cols[13] = "actions:offsite_conversion.fb_pixel_purchase"
    cols[15], cols[16] = roas, freq
    cols[22] = ad_id
    cols[26] = str(purchases)
    return ",".join(cols)


def _meta_csv(rows):
    return META_HEADER + "\n" + "\n".join(rows) + "\n"


# A "By day" breakdown: one row per ad per day across three completed days.
META_CSV_BY_DAY = _meta_csv(
    [
        _meta_row("2026-06-12", "2026-06-12", "Video 311", 1000, 40, ad_id="11"),
        _meta_row(
            "2026-06-12", "2026-06-12", "Video 295", 100, 4,
            ad_set="All Creatives", ad_id="22",
        ),
        _meta_row("2026-06-13", "2026-06-13", "Video 311", 1200, 50, ad_id="11"),
        _meta_row(
            "2026-06-13", "2026-06-13", "Video 295", 120, 5,
            ad_set="All Creatives", ad_id="22",
        ),
        _meta_row("2026-06-14", "2026-06-14", "Video 311", 1300, 55, ad_id="11"),
        _meta_row(
            "2026-06-14", "2026-06-14", "Video 295", 130, 6,
            ad_set="All Creatives", ad_id="22",
        ),
    ]
)


def _login(client, user_id, username="testuser"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = username


def _import_markdown(
    client,
    metric_date,
    markdown=SAMPLE_MARKDOWN,
    filename="metrics.md",
    replace_existing=False,
):
    token = csrf_token(client)
    data = {
        "_csrf_token": token,
        "metric_date": metric_date,
        "file": (io.BytesIO(markdown.encode("utf-8")), filename),
    }
    if replace_existing:
        data["replace_existing"] = "true"
    return client.post(
        "/metrics/import",
        data=data,
        content_type="multipart/form-data",
    )


def _import_csv(
    client,
    csv_text=META_CSV,
    filename="meta.csv",
    metric_date=None,
    replace_existing=False,
):
    token = csrf_token(client)
    data = {
        "_csrf_token": token,
        "file": (io.BytesIO(csv_text.encode("utf-8")), filename),
    }
    if metric_date is not None:
        data["metric_date"] = metric_date
    if replace_existing:
        data["replace_existing"] = "true"
    return client.post(
        "/metrics/import",
        data=data,
        content_type="multipart/form-data",
    )


class TestCsvParser:
    def test_single_range_export_yields_one_period(self):
        periods = parse_csv_metrics(META_CSV)
        assert len(periods) == 1

    def test_parses_meta_export_and_skips_zero_spend(self):
        parsed = parse_csv_metrics(META_CSV)[0]

        names = {ad["ad_name"] for ad in parsed.ads}
        assert names == {"Video 311", "Video 297", "Split Ad"}
        # Video 279 had zero spend and is dropped.
        assert "Video 279" not in names
        assert parsed.has_reported_total is False
        assert parsed.reported_ad_count == 3
        assert parsed.reporting_start == date(2026, 6, 12)
        assert parsed.reporting_end == date(2026, 6, 14)

    def test_detects_reporting_window_from_the_file(self):
        parsed = parse_csv_metrics(META_CSV)[0]
        assert parsed.reporting_end == date(2026, 6, 14)

    def test_aggregates_duplicate_ad_names_across_ad_sets(self):
        parsed = parse_csv_metrics(META_CSV)[0]
        split = next(ad for ad in parsed.ads if ad["ad_name"] == "Split Ad")
        # Set A (100/4) + Set B (300/12) combine into one creative row.
        assert split["spend"] == Decimal("400")
        assert split["purchases"] == 16
        assert split["adds_to_cart"] == 40
        assert split["cost_per_purchase"] == Decimal("25")

    def test_derives_cost_per_purchase_when_column_absent(self):
        parsed = parse_csv_metrics(META_CSV)[0]
        top = next(ad for ad in parsed.ads if ad["ad_name"] == "Video 311")
        # 2557.92 / 110 purchases.
        assert top["cost_per_purchase"].quantize(Decimal("0.01")) == Decimal("23.25")

    def test_empty_conversion_cells_become_zero(self):
        parsed = parse_csv_metrics(META_CSV)[0]
        sparse = next(ad for ad in parsed.ads if ad["ad_name"] == "Video 297")
        assert sparse["purchases"] == 0
        assert sparse["adds_to_cart"] == 0
        assert sparse["roas"] == Decimal("0")
        assert sparse["cost_per_purchase"] == Decimal("0")

    def test_summary_totals_match_active_rows(self):
        parsed = parse_csv_metrics(META_CSV)[0]
        assert parsed.summary["spend"] == Decimal("2962.31")
        assert parsed.summary["purchases"] == 126
        assert parsed.summary["adds_to_cart"] == 366

    def test_rejects_csv_without_required_columns(self):
        try:
            parse_csv_metrics("foo,bar\n1,2\n")
        except MetricsParseError as exc:
            assert "Meta Ads columns" in str(exc)
        else:
            raise AssertionError("Expected missing columns to be rejected")

    def test_rejects_csv_with_no_spending_ads(self):
        header, _, zero_row = META_CSV.partition("\n")
        # Keep only the zero-spend row.
        zero_only = header + "\n" + (
            "2026-06-12,2026-06-14,Video 279,active,All Creatives,"
            "Using campaign budget,0,0,0,,,,,,,,0,,,,,0,120246098346390596,0,,,\n"
        )
        try:
            parse_csv_metrics(zero_only)
        except MetricsParseError as exc:
            assert "any ads with spend" in str(exc)
        else:
            raise AssertionError("Expected a spend-less CSV to be rejected")


class TestCsvEndpoint:
    def test_import_csv_uses_reporting_end_as_metric_date(self, app, client, test_user):
        _login(client, test_user)
        response = _import_csv(client)
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["metric_date"] == "2026-06-14"
        assert payload["source_format"] == "csv"
        assert payload["reporting_start"] == "2026-06-12"
        assert payload["reporting_end"] == "2026-06-14"
        assert payload["detailed_ad_count"] == 3

        with app.app_context():
            snapshot = AdMetricsSnapshot.query.one()
            assert snapshot.metric_date == date(2026, 6, 14)
            assert snapshot.has_reported_total is False
            assert {metric.ad_name for metric in snapshot.metrics} == {
                "Video 311",
                "Video 297",
                "Split Ad",
            }

    def test_csv_metric_date_falls_back_to_form_without_reporting_window(
        self, app, client, test_user
    ):
        _login(client, test_user)
        no_dates = META_CSV.replace("2026-06-12,2026-06-14,", ",,")
        response = _import_csv(
            client, csv_text=no_dates, metric_date="2026-06-13"
        )
        assert response.status_code == 200
        assert response.get_json()["metric_date"] == "2026-06-13"

        with app.app_context():
            assert AdMetricsSnapshot.query.one().metric_date == date(2026, 6, 13)

    def test_csv_and_markdown_share_a_snapshot_date(self, app, client, test_user):
        _login(client, test_user)
        assert _import_csv(client).status_code == 200

        # A Markdown upload for the same date conflicts like any re-import.
        conflict = _import_markdown(client, "2026-06-14")
        assert conflict.status_code == 409

    def test_rejects_unsupported_extension(self, client, test_user):
        _login(client, test_user)
        response = _import_csv(client, filename="meta.txt")
        assert response.status_code == 400
        assert "supported" in response.get_json()["error"]

    def test_per_day_export_creates_one_snapshot_per_day(
        self, app, client, test_user
    ):
        _login(client, test_user)
        response = _import_csv(client, csv_text=META_CSV_BY_DAY)
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["days_imported"] == 3
        assert payload["imported_dates"] == [
            "2026-06-12",
            "2026-06-13",
            "2026-06-14",
        ]
        # Latest imported day drives the post-import view.
        assert payload["metric_date"] == "2026-06-14"
        assert payload["reporting_start"] == "2026-06-12"
        assert payload["reporting_end"] == "2026-06-14"
        assert payload["detailed_ad_count"] == 6

        with app.app_context():
            snapshots = AdMetricsSnapshot.query.order_by(
                AdMetricsSnapshot.metric_date
            ).all()
            assert [s.metric_date for s in snapshots] == [
                date(2026, 6, 12),
                date(2026, 6, 13),
                date(2026, 6, 14),
            ]
            # Each day keeps its own per-day numbers (not collapsed together).
            assert snapshots[0].total_spend == Decimal("1100.00")
            assert snapshots[2].total_purchases == 61

    def test_window_aggregates_creatives_across_days(self, client, test_user):
        _login(client, test_user)
        assert _import_csv(client, csv_text=META_CSV_BY_DAY).status_code == 200

        data = client.get(
            "/metrics/data?start_date=2026-06-12&end_date=2026-06-14"
        ).get_json()
        assert data["days_with_data"] == 3
        assert len(data["daily"]) == 3
        assert {ad["ad_name"] for ad in data["ads"]} == {"Video 311", "Video 295"}

        video_311 = next(
            ad for ad in data["ads"] if ad["ad_name"] == "Video 311"
        )
        # Spend and purchases sum across the three days (1000+1200+1300, 40+50+55).
        assert video_311["spend"] == 3500.0
        assert video_311["purchases"] == 145

    def test_per_day_export_skips_the_in_progress_day(
        self, app, client, test_user
    ):
        _login(client, test_user)
        rows = [
            _meta_row("2026-06-15", "2026-06-15", "Video 311", 900, 30, ad_id="11"),
            _meta_row("2026-06-16", "2026-06-16", "Video 311", 100, 2, ad_id="11"),
        ]
        response = _import_csv(client, csv_text=_meta_csv(rows))
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["imported_dates"] == ["2026-06-15"]
        assert payload["skipped_incomplete"] == ["2026-06-16"]

        with app.app_context():
            snapshot = AdMetricsSnapshot.query.one()
            assert snapshot.metric_date == date(2026, 6, 15)

    def test_csv_dated_today_is_rejected(self, app, client, test_user):
        _login(client, test_user)
        rows = [
            _meta_row("2026-06-16", "2026-06-16", "Video 311", 100, 2, ad_id="11"),
        ]
        response = _import_csv(client, csv_text=_meta_csv(rows))
        assert response.status_code == 400
        payload = response.get_json()
        assert "completed days" in payload["error"]
        assert payload["skipped_incomplete"] == ["2026-06-16"]

        with app.app_context():
            assert AdMetricsSnapshot.query.count() == 0

    def test_per_day_reimport_conflicts_then_replaces(self, app, client, test_user):
        _login(client, test_user)
        assert _import_csv(client, csv_text=META_CSV_BY_DAY).status_code == 200

        conflict = _import_csv(client, csv_text=META_CSV_BY_DAY)
        assert conflict.status_code == 409
        data = conflict.get_json()
        assert data["conflict"] is True
        assert data["conflict_dates"] == [
            "2026-06-12",
            "2026-06-13",
            "2026-06-14",
        ]

        confirmed = _import_csv(
            client, csv_text=META_CSV_BY_DAY, replace_existing=True
        )
        assert confirmed.status_code == 200
        assert confirmed.get_json()["replaced"] is True

        with app.app_context():
            assert AdMetricsSnapshot.query.count() == 3


class TestMetricsParser:
    def test_parses_portuguese_sample_and_reported_total(self):
        parsed = parse_markdown_metrics(SAMPLE_MARKDOWN)

        assert len(parsed.ads) == 7
        assert parsed.reported_ad_count == 75
        assert parsed.has_reported_total is True
        assert parsed.ads[0]["ad_name"] == "Video 311"
        assert parsed.ads[0]["spend"] == Decimal("5408.12")
        assert parsed.summary["spend"] == Decimal("8692.32")
        assert parsed.summary["adds_to_cart"] == 1062
        assert parsed.summary["roas"] == Decimal("1.46")

    def test_accepts_english_headers_and_derives_missing_total(self):
        parsed = parse_markdown_metrics(ENGLISH_WITHOUT_TOTAL)

        assert parsed.reported_ad_count == 2
        assert parsed.has_reported_total is False
        assert parsed.summary["spend"] == Decimal("150.00")
        assert parsed.summary["purchases"] == 5
        assert parsed.summary["cost_per_purchase"] == Decimal("30.00")
        assert parsed.summary["roas"] == Decimal("1.666666666666666666666666667")

    def test_duplicate_ads_are_rejected_case_insensitively(self):
        duplicate = ENGLISH_WITHOUT_TOTAL.replace("Ad Two", "ad one")
        try:
            parse_markdown_metrics(duplicate)
        except MetricsParseError as exc:
            assert "duplicate ad name" in " ".join(exc.details)
        else:
            raise AssertionError("Expected duplicate ad names to be rejected")

    def test_negative_or_invalid_rows_are_rejected(self):
        invalid = ENGLISH_WITHOUT_TOTAL.replace("€100.00", "-€100.00")
        try:
            parse_markdown_metrics(invalid)
        except MetricsParseError as exc:
            assert "Spend" in " ".join(exc.details)
        else:
            raise AssertionError("Expected invalid metrics to be rejected")

    def test_count_parser_accepts_meta_and_spreadsheet_formats(self):
        assert _parse_integer("1.062", "Adds to Cart") == 1062
        assert _parse_integer("1,062", "Adds to Cart") == 1062
        assert _parse_integer("22,00", "Adds to Cart") == 22
        assert _parse_integer("15.0", "Purchases") == 15
        assert _parse_integer("2\u202f431", "Purchases") == 2431
        assert _parse_integer("24\u200b", "Adds to Cart") == 24
        assert _parse_integer("—", "Purchases") == 0

    def test_invalid_count_error_includes_received_value(self):
        try:
            _parse_integer("12.5", "Purchases")
        except ValueError as exc:
            assert "received '12.5'" in str(exc)
        else:
            raise AssertionError("Expected fractional count to be rejected")


class TestMetricsEndpoints:
    def test_metrics_page_requires_authentication(self, client):
        response = client.get("/metrics")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_markdown_import_rejects_today(self, app, client, test_user):
        _login(client, test_user)
        response = _import_markdown(client, FIXED_TODAY.isoformat())
        assert response.status_code == 400
        assert "completed days" in response.get_json()["error"]
        with app.app_context():
            assert AdMetricsSnapshot.query.count() == 0

    def test_markdown_import_rejects_future_date(self, app, client, test_user):
        _login(client, test_user)
        future = (FIXED_TODAY + timedelta(days=3)).isoformat()
        response = _import_markdown(client, future)
        assert response.status_code == 400
        with app.app_context():
            assert AdMetricsSnapshot.query.count() == 0

    def test_metrics_page_caps_upload_date_at_yesterday(self, client, test_user):
        _login(client, test_user)
        html = client.get("/metrics").get_data(as_text=True)
        assert 'max="2026-06-15"' in html

    def test_import_and_replace_daily_snapshot(self, app, client, test_user):
        _login(client, test_user)
        first = _import_markdown(client, "2026-06-10")
        assert first.status_code == 200
        assert first.get_json()["replaced"] is False

        replacement = ENGLISH_WITHOUT_TOTAL.replace("Ad Two", "Replacement")
        conflict = _import_markdown(
            client,
            "2026-06-10",
            replacement,
            filename="replacement.md",
        )
        assert conflict.status_code == 409
        conflict_data = conflict.get_json()
        assert conflict_data["conflict"] is True
        assert conflict_data["existing"]["filename"] == "metrics.md"
        assert conflict_data["incoming"]["filename"] == "replacement.md"

        confirmed = _import_markdown(
            client,
            "2026-06-10",
            replacement,
            filename="replacement.md",
            replace_existing=True,
        )
        assert confirmed.status_code == 200
        assert confirmed.get_json()["replaced"] is True

        with app.app_context():
            snapshots = AdMetricsSnapshot.query.all()
            assert len(snapshots) == 1
            assert snapshots[0].metric_date == date(2026, 6, 10)
            assert {metric.ad_name for metric in snapshots[0].metrics} == {
                "Ad One",
                "Replacement",
            }

    def test_confirmed_reimport_with_same_ad_names_replaces_without_unique_violation(
        self, app, client, test_user
    ):
        _login(client, test_user)
        assert _import_markdown(client, "2026-06-10").status_code == 200

        conflict = _import_markdown(client, "2026-06-10")
        assert conflict.status_code == 409

        confirmed = _import_markdown(
            client,
            "2026-06-10",
            replace_existing=True,
        )
        assert confirmed.status_code == 200
        assert confirmed.get_json()["replaced"] is True

        with app.app_context():
            snapshot = AdMetricsSnapshot.query.one()
            assert len(snapshot.metrics) == 7
            assert {metric.ad_name for metric in snapshot.metrics} == {
                "Video 311",
                "Video 260",
                "Video 295",
                "Video 217",
                "Video 344",
                "Video 331",
                "Video 327",
            }

    def test_unconfirmed_conflict_does_not_modify_existing_snapshot(
        self, app, client, test_user
    ):
        _login(client, test_user)
        assert _import_markdown(client, "2026-06-10").status_code == 200

        replacement = ENGLISH_WITHOUT_TOTAL.replace("Ad Two", "Replacement")
        response = _import_markdown(client, "2026-06-10", replacement)
        assert response.status_code == 409

        with app.app_context():
            snapshot = AdMetricsSnapshot.query.one()
            assert snapshot.filename == "metrics.md"
            assert snapshot.reported_ad_count == 75
            assert len(snapshot.metrics) == 7
            assert snapshot.total_spend == Decimal("8692.32")

    def test_invalid_reimport_preserves_existing_snapshot(self, app, client, test_user):
        _login(client, test_user)
        assert _import_markdown(client, "2026-06-10").status_code == 200

        invalid = ENGLISH_WITHOUT_TOTAL.replace("Ad Two", "Ad One")
        response = _import_markdown(client, "2026-06-10", invalid)
        assert response.status_code == 400

        with app.app_context():
            snapshot = AdMetricsSnapshot.query.one()
            assert snapshot.reported_ad_count == 75
            assert len(snapshot.metrics) == 7
            assert snapshot.total_spend == Decimal("8692.32")

    def test_data_reports_missing_days_and_partial_coverage(self, client, test_user):
        _login(client, test_user)
        assert _import_markdown(client, "2026-06-10").status_code == 200
        assert _import_markdown(client, "2026-06-12").status_code == 200

        response = client.get(
            "/metrics/data?start_date=2026-06-10&end_date=2026-06-12"
        )
        assert response.status_code == 200
        data = response.get_json()

        assert data["days_with_data"] == 2
        assert data["missing_dates"] == ["2026-06-11"]
        assert data["coverage"] == {
            "detailed_ad_rows": 14,
            "reported_ad_rows": 150,
            "partial": True,
        }
        assert data["kpis"]["spend"] == 17384.64
        assert data["kpis"]["purchases"] == 730
        assert len(data["ads"]) == 7

    def test_zero_values_do_not_cause_division_errors(self, client, test_user):
        zero_markdown = ENGLISH_WITHOUT_TOTAL.replace(
            "| Ad One | €100.00 | €20.00 | €1.00 | 2.00% | 10 | 4 | €25.00 | 2.00 | 1.20 |",
            "| Ad One | €0.00 | €0.00 | €0.00 | 0.00% | 0 | 0 | €0.00 | 0.00 | 0.00 |",
        ).replace(
            "| Ad Two | €50.00 | €25.00 | €2.00 | 1.25% | 5 | 1 | €50.00 | 1.00 | 1.10 |",
            "| Ad Two | €0.00 | €0.00 | €0.00 | 0.00% | 0 | 0 | €0.00 | 0.00 | 0.00 |",
        )
        _login(client, test_user)
        assert _import_markdown(client, "2026-06-10", zero_markdown).status_code == 200

        data = client.get(
            "/metrics/data?start_date=2026-06-10&end_date=2026-06-10"
        ).get_json()
        assert data["kpis"]["cost_per_purchase"] == 0.0
        assert data["kpis"]["roas"] == 0.0
        assert data["kpis"]["ctr"] == 0.0

    def test_snapshots_are_isolated_by_user(self, app, client, test_user):
        _login(client, test_user)
        assert _import_markdown(client, "2026-06-10").status_code == 200

        with app.app_context():
            other = User(
                username="other",
                email="other@example.com",
                password_hash=generate_password_hash("Password1!"),
                next_credit_reset_at=date.today() + timedelta(days=30),
            )
            db.session.add(other)
            db.session.commit()
            other_id = other.id

        _login(client, other_id, "other")
        data = client.get(
            "/metrics/data?start_date=2026-06-10&end_date=2026-06-10"
        ).get_json()

        assert data["days_with_data"] == 0
        assert data["ads"] == []
        assert data["kpis"]["spend"] == 0.0

    def test_import_requires_csrf(self, client, test_user):
        _login(client, test_user)
        response = client.post(
            "/metrics/import",
            data={
                "metric_date": "2026-06-10",
                "file": (io.BytesIO(SAMPLE_MARKDOWN.encode("utf-8")), "metrics.md"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 400
        assert response.get_json()["error"] == "Invalid CSRF token."
