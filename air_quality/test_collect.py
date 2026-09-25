"""All fixtures are invented in memory; no real source requests or local data."""
import io
import unittest
from unittest.mock import patch
import zipfile

import pandas as pd

from air_quality import collect as c


HEADER = ["state code", "county code", "date", "aqi", "category", "number of sites reporting", "county name"]


def fixture(rows):
    return pd.DataFrame(rows, columns=HEADER, dtype="string")


def row(date="2024-01-01", aqi="50", category="Good", county="001", state="01", sites="2", name="Example County"):
    return [state, county, date, aqi, category, sites, name]


def zipped(entries):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries:
            archive.writestr(name, body)
    return target.getvalue()


class Semantics(unittest.TestCase):
    def test_schema_resolves_case_and_whitespace_only(self):
        data, headers = c.parse_csv(b" State Code ,County   Code,DATE,AQI,County Name\n01,001,2024-01-01,0,\n")
        self.assertEqual(headers["county code"], "County   Code")
        self.assertEqual(data.iloc[0]["county name"], "")
        self.assertEqual(data.iloc[0]["state code"], "01")

    def test_header_ambiguity_and_required_fields(self):
        for body in [b"State Code, STATE CODE ,County Code,Date,AQI\n", b"State Code,County Code,AQI\n", b"\n"]:
            with self.assertRaises(ValueError):
                c.parse_csv(body)

    def test_malformed_width_and_reserved_null(self):
        for body in [b"State Code,County Code,Date,AQI\n01,001,2024-01-01\n", b"State Code,County Code,Date,AQI\n01,001,2024-01-01,\\N\n"]:
            with self.assertRaises(ValueError):
                c.parse_csv(body)

    def test_empty_source_and_codes_remain_strings(self):
        good, bad, stats = c.normalize(fixture([row(name="", county="1", state="1")]), 2024)
        self.assertEqual(len(bad), 0)
        self.assertEqual(good.iloc[0].aqs_county_key, "01001")
        self.assertEqual(good.iloc[0].source_county_code, "1")
        self.assertEqual(good.iloc[0].source_county_name, "")
        self.assertEqual(good.iloc[0].calendar_partition, "calibration_2024")
        self.assertFalse(good.iloc[0].historical_available_asof_verified)

    def test_invalid_code_is_quarantined_not_coerced(self):
        good, bad, _ = c.normalize(fixture([row(state="01.0"), row(county="1001", date="2024-01-02"), row(state="", date="2024-01-03")]), 2024)
        self.assertTrue(good.empty)
        self.assertEqual(len(bad), 3)
        self.assertTrue(bad.aqs_county_key.isna().all())

    def test_aqi_zero_and_beyond_five_hundred(self):
        good, bad, _ = c.normalize(fixture([row(aqi="0"), row(date="2024-01-02", aqi="750", category="Hazardous")]), 2024)
        self.assertEqual(good.aqi.tolist(), [0, 750])
        self.assertEqual(good.category_validation.tolist(), ["matches_current_band", "matches_current_band"])
        self.assertTrue(bad.empty)

    def test_invalid_fraction_infinity_and_empty_aqi(self):
        values = ["-1", "1.5", "inf", "NaN", "", "1.00000000000000001"]
        good, bad, _ = c.normalize(fixture([row(date=f"2024-01-{i+1:02}", aqi=v) for i, v in enumerate(values)]), 2024)
        self.assertTrue(good.empty)
        self.assertEqual(len(bad), len(values))
        self.assertTrue(bad.aqi.isna().all())

    def test_category_boundaries(self):
        values = [0, 50, 51, 100, 101, 150, 151, 200, 201, 300, 301, 700]
        labels = ["Good", "Good", "Moderate", "Moderate", "Unhealthy for Sensitive Groups", "Unhealthy for Sensitive Groups", "Unhealthy", "Unhealthy", "Very Unhealthy", "Very Unhealthy", "Hazardous", "Hazardous"]
        good, _, _ = c.normalize(fixture([row(date=f"2024-01-{i+1:02}", aqi=str(v), category=labels[i]) for i, v in enumerate(values)]), 2024)
        self.assertEqual(good.category_from_aqi_current_band.tolist(), labels)
        self.assertTrue(good.category_validation.eq("matches_current_band").all())

    def test_mismatch_and_unknown_category_retained(self):
        good, _, stats = c.normalize(fixture([row(category="Moderate"), row(date="2024-01-02", category="unexpected"), row(date="2024-01-03", category="")]), 2024)
        self.assertEqual(good.category_validation.tolist(), ["mismatch_current_band", "unrecognized", "missing"])
        self.assertEqual(stats["canonical_rows"], 3)

    def test_reporting_sites_independent_of_aqi(self):
        good, _, _ = c.normalize(fixture([row(sites=""), row(date="2024-01-02", sites="-1"), row(date="2024-01-03", sites="0")]), 2024)
        self.assertEqual(good.reporting_sites_validation.tolist(), ["missing", "invalid", "zero_reporters_inconsistent_with_aqi"])
        self.assertEqual(good.iloc[2].number_of_sites_reporting, 0)

    def test_exact_duplicates_removed_and_conflicts_all_quarantined(self):
        good, bad, stats = c.normalize(fixture([row(), row(), row(date="2024-01-02", aqi="51"), row(date="2024-01-02", aqi="52")]), 2024)
        self.assertEqual(len(good), 1)
        self.assertEqual(len(bad), 2)
        self.assertEqual(stats["deduplicated_rows"], 3)
        self.assertTrue(bad.exclusion_reason.str.contains("conflicting_county_date").all())

    def test_optional_source_difference_is_conflict(self):
        good, bad, _ = c.normalize(fixture([row(name="A"), row(name="B")]), 2024)
        self.assertTrue(good.empty)
        self.assertEqual(len(bad), 2)

    def test_invalid_aqi_also_quarantines_other_member_of_key(self):
        good, bad, _ = c.normalize(fixture([row(), row(aqi="")]), 2024)
        self.assertTrue(good.empty)
        self.assertEqual(len(bad), 2)

    def test_date_granularity_and_source_year(self):
        good, bad, _ = c.normalize(fixture([row(date="2024-02-29"), row(date="2023-12-31"), row(date="2024-01-01T00:00:00Z"), row(date="2024-02-30")]), 2024)
        self.assertEqual(good.observation_date.tolist(), ["2024-02-29"])
        self.assertEqual(len(bad), 3)
        self.assertIn("source_year_date_mismatch", bad.exclusion_reason.tolist())

    def test_partitions_and_missing_days(self):
        for year, label in [(2023, "fit_pre_2024"), (2024, "calibration_2024"), (2025, "holdout_2025_plus")]:
            good, _, _ = c.normalize(fixture([row(date=f"{year}-01-01"), row(date=f"{year}-01-03")]), year)
            self.assertEqual(len(good), 2)
            self.assertTrue(good.calendar_partition.eq(label).all())

    def test_zero_codes_unresolved_and_mismatch_partition_uses_actual_date(self):
        good, bad, _ = c.normalize(fixture([row(state="00", county="000"), row(date="2025-01-01")]), 2024)
        self.assertTrue(good.empty)
        mismatch = bad.loc[bad.source_date.eq("2025-01-01")].iloc[0]
        self.assertEqual(mismatch.calendar_partition, "holdout_2025_plus")
        invalid = c.normalize(fixture([row(date="not-a-date")]), 2024)[1]
        self.assertTrue(pd.isna(invalid.iloc[0].calendar_partition))

    def test_absent_optional_fields(self):
        frame = fixture([row()]).drop(columns=["category", "number of sites reporting"])
        good, _, _ = c.normalize(frame, 2024)
        self.assertEqual(good.iloc[0].category_validation, "column_absent")
        self.assertTrue(pd.isna(good.iloc[0].number_of_sites_reporting))

    def test_empty_file_retains_schema(self):
        good, bad, stats = c.normalize(fixture([]), 2024)
        self.assertEqual(stats["canonical_rows"], 0)
        self.assertIn("aqs_county_key", good)
        self.assertIn("exclusion_reason", bad)


class ArchiveAndTransport(unittest.TestCase):
    def test_exact_zip_member(self):
        body = b"invented,csv\n"
        self.assertEqual(c.extract_csv(zipped([("daily_aqi_by_county_2024.csv", body)]), 2024), body)

    def test_zip_traversal_extra_wrong_year_and_duplicate(self):
        for entries in [[("../daily_aqi_by_county_2024.csv", b"x")], [("daily_aqi_by_county_2023.csv", b"x")], [("daily_aqi_by_county_2024.csv", b"x"), ("extra.txt", b"x")], [("daily_aqi_by_county_2024.csv", b"x")] * 2]:
            with self.assertRaises(ValueError):
                c.extract_csv(zipped(entries), 2024)

    def test_decompression_limit(self):
        with patch.object(c, "CSV_BYTES", 10):
            with self.assertRaises(ValueError):
                c.extract_csv(zipped([("daily_aqi_by_county_2024.csv", b"x" * 11)]), 2024)

    def test_only_allowlisted_epa_source(self):
        c.Budget.allowed(c.BASE + "daily_aqi_by_county_2024.zip")
        for url in ["https://evil.example/daily_aqi_by_county_2024.zip", c.BASE + "daily_aqi_by_county_2024.zip?token=bad", c.BASE + "other.zip", "http://aqs.epa.gov/aqsweb/airdata/daily_aqi_by_county_2024.zip"]:
            with self.assertRaises(ValueError):
                c.Budget.allowed(url)

    def test_budget_stops_before_next_request(self):
        budget = c.Budget()
        budget.requests = c.MAX_REQUESTS
        with self.assertRaises(RuntimeError):
            budget.check()
        budget.requests = 0
        budget.bytes = c.MAX_BYTES
        with self.assertRaises(RuntimeError):
            budget.check()
        budget.bytes = 0
        budget.deadline = 0
        with self.assertRaises(TimeoutError):
            budget.check()

    def test_hard_deadline_has_distinct_exception(self):
        with self.assertRaises(c.HardWallTimeout):
            c.timeout_handler()

    def test_retry_after_is_sanitized_without_retry(self):
        self.assertEqual(c.retry_after("120")["retry_after_seconds"], 120)
        self.assertEqual(c.retry_after("bad\nsecret"), {"retry_after_seconds": None, "retry_after_utc": None})
        self.assertIsNotNone(c.retry_after("Fri, 25 Sep 2026 21:00:00 GMT")["retry_after_utc"])

    def test_real_io_guard_before_network_or_write(self):
        with patch.object(c, "require_github_hosted_runner", side_effect=RuntimeError("guard")), patch.object(c.requests, "Session") as session:
            with self.assertRaisesRegex(RuntimeError, "guard"):
                c.Budget().fetch(2024)
            session.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "guard"):
                c.write_bytes("not_written", b"synthetic")


if __name__ == "__main__":
    unittest.main()
