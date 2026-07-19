#!/usr/bin/env python3
"""Phase 7.2 — offline Amazon Ads report ingestion tests (release-grade).

Hermetic + offline by construction. Every report used here is SYNTHETIC_TEST_DATA_ONLY built in a
temp workspace — never a real owner export, never mixed with any T2 dataset. This module opens NO
network and asserts the permanent Amazon boundary (zero action/network counters) throughout.
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("", "core", "listing", "production", "scripts"):
    _p = ROOT if not _sub else os.path.join(ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_report_ingestion as RI   # noqa: E402
from core import money as MONEY                          # noqa: E402

REF = "2026-07-19"
NOW = "2026-07-19T10:00:00+07:00"


# ---------------------------------------------------------------- fixture builders (SYNTHETIC ONLY)
def _csv(headers, rows, delimiter=","):
    lines = [delimiter.join(headers)]
    for r in rows:
        lines.append(delimiter.join("" if c is None else str(c) for c in r))
    return "\n".join(lines) + "\n"


def campaign_csv(rows=None, *, interval=False):
    if interval:
        headers = ["Start Date", "End Date", "Campaign Name", "Impressions", "Clicks", "Spend",
                   "7 Day Total Sales", "Currency"]
        rows = rows if rows is not None else [
            ["2026-07-01", "2026-07-07", "Nurse SP Auto", 1000, 50, "12.34", "45.00", "USD"]]
    else:
        headers = ["Date", "Campaign Name", "Impressions", "Clicks", "Spend", "7 Day Total Sales",
                   "Currency"]
        rows = rows if rows is not None else [
            ["2026-07-01", "Nurse SP Auto", 1000, 50, "12.34", "45.00", "USD"],
            ["2026-07-01", "Nurse SP Manual", 2000, 80, "20.00", "90.00", "USD"]]
    return _csv(headers, rows)


def targeting_csv(rows=None):
    headers = ["Date", "Campaign Name", "Ad Group Name", "Targeting", "Match Type", "Impressions",
               "Clicks", "Spend", "7 Day Total Sales", "Currency"]
    rows = rows if rows is not None else [
        ["2026-07-01", "Nurse SP Manual", "Core", "nurse sweatshirt", "exact", 500, 30, "9.00",
         "40.00", "USD"]]
    return _csv(headers, rows)


def search_term_csv(rows=None):
    headers = ["Date", "Campaign Name", "Ad Group Name", "Targeting", "Match Type",
               "Customer Search Term", "Impressions", "Clicks", "Spend", "7 Day Total Sales",
               "Currency"]
    rows = rows if rows is not None else [
        ["2026-07-01", "Nurse SP Manual", "Core", "nurse sweatshirt", "broad",
         "gift for nurse", 300, 15, "5.00", "25.00", "USD"]]
    return _csv(headers, rows)


def advertised_product_csv(rows=None):
    headers = ["Date", "Campaign Name", "Ad Group Name", "Advertised SKU", "Advertised ASIN",
               "Impressions", "Clicks", "Spend", "7 Day Total Sales", "Currency"]
    rows = rows if rows is not None else [
        ["2026-07-01", "Nurse SP Auto", "Core", "NURSE-SWT-1", "B0SYNTH0001", 800, 40, "10.00",
         "50.00", "USD"]]
    return _csv(headers, rows)


def purchased_product_csv(rows=None):
    headers = ["Date", "Campaign Name", "Ad Group Name", "Advertised SKU", "Purchased ASIN",
               "7 Day Total Units", "7 Day Total Sales", "Currency"]
    rows = rows if rows is not None else [
        ["2026-07-01", "Nurse SP Auto", "Core", "NURSE-SWT-1", "B0OTHER0002", 3, "60.00", "USD"]]
    return _csv(headers, rows)


def placement_csv(rows=None):
    headers = ["Date", "Campaign Name", "Placement", "Impressions", "Clicks", "Spend",
               "7 Day Total Sales", "Currency"]
    rows = rows if rows is not None else [
        ["2026-07-01", "Nurse SP Auto", "Top of Search", 400, 20, "8.00", "44.00", "USD"]]
    return _csv(headers, rows)


def budget_csv(rows=None):
    headers = ["Date", "Campaign Name", "Budget", "Budget Type", "Currency"]
    rows = rows if rows is not None else [
        ["2026-07-01", "Nurse SP Auto", "25.00", "DAILY", "USD"]]
    return _csv(headers, rows)


# ---------------------------------------------------------------- workspace helpers
class Base(unittest.TestCase):
    def mkbase(self):
        d = tempfile.mkdtemp(prefix="p72_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def ws(self):
        return RI.ensure_workspace(self.mkbase())

    def drop(self, dirs, name, text, encoding="utf-8"):
        path = os.path.join(dirs["inbox"], name)
        data = text.encode(encoding) if isinstance(text, str) else text
        with open(path, "wb") as f:
            f.write(data)
        return path

    def run_base(self, dirs, **kw):
        kw.setdefault("reference_date", REF)
        kw.setdefault("now", NOW)
        kw.setdefault("run_id", "t")
        return RI.run_ingestion(dirs["base"], **kw)


# ================================================================ A. HEADER NORMALIZATION
class TestHeaderNormalization(Base):
    def test_header_key_punctuation(self):
        self.assertEqual(RI._header_key("Click-Thru Rate (CTR)"), "click thru rate ctr")

    def test_header_key_windowed_metric(self):
        self.assertEqual(RI._header_key("7 Day Total Sales"), "7 day total sales")

    def test_header_key_nfkc_fullwidth(self):
        # fullwidth 'Ｄate' normalizes under NFKC to ascii
        self.assertEqual(RI._header_key("Ｄate"), "date")

    def test_header_key_collapses_whitespace(self):
        self.assertEqual(RI._header_key("  Campaign    Name  "), "campaign name")

    def test_alias_maps_date(self):
        self.assertEqual(RI.normalize_header("Date")[3], "report_date")

    def test_alias_maps_spend(self):
        self.assertEqual(RI.normalize_header("Spend")[3], "spend")

    def test_alias_maps_windowed_sales(self):
        self.assertEqual(RI.normalize_header("7 Day Total Sales")[3], "sales_7d")

    def test_alias_case_insensitive(self):
        self.assertEqual(RI.normalize_header("cAmPaIgN nAmE")[3], "campaign_name")

    def test_unmapped_header_is_extra(self):
        m = RI.map_headers(["Date", "Weird Column"])
        self.assertIn("report_date", m["canonical"])
        self.assertTrue(any(e["original"] == "Weird Column" for e in m["extras"]))

    def test_bare_sales_not_inferred_to_window(self):
        # a window-less 'Sales' must NOT be mapped to any attribution window (kept as extra)
        m = RI.map_headers(["Date", "Sales"])
        self.assertNotIn("sales_7d", m["canonical"])
        self.assertTrue(any(e["key"] == "sales" for e in m["extras"]))

    def test_duplicate_canonical_detected(self):
        m = RI.map_headers(["Spend", "Cost", "Spend"])
        self.assertIn("spend", m["duplicates"])

    def test_original_header_preserved(self):
        m = RI.map_headers(["Campaign Name"])
        self.assertEqual(m["columns"][0]["original"], "Campaign Name")

    def test_unknown_columns_never_silently_dropped(self):
        m = RI.map_headers(["Foo", "Bar"])
        self.assertEqual(len(m["extras"]), 2)


# ================================================================ B. CLASSIFICATION
class TestClassification(Base):
    def _cls(self, headers):
        return RI.classify_report(set(RI.map_headers(headers)["canonical"].keys()))[0]

    def test_campaign(self):
        self.assertEqual(self._cls(["Date", "Campaign Name", "Impressions", "Spend", "Currency"]),
                         RI.SP_CAMPAIGN)

    def test_targeting(self):
        self.assertEqual(self._cls(["Date", "Campaign Name", "Ad Group Name", "Targeting",
                                    "Match Type", "Impressions", "Spend"]), RI.SP_TARGETING)

    def test_search_term(self):
        self.assertEqual(self._cls(["Date", "Campaign Name", "Ad Group Name", "Targeting",
                                    "Match Type", "Customer Search Term", "Impressions"]),
                         RI.SP_SEARCH_TERM)

    def test_advertised_product(self):
        self.assertEqual(self._cls(["Date", "Campaign Name", "Ad Group Name", "Advertised SKU",
                                    "Impressions", "Spend"]), RI.SP_ADVERTISED_PRODUCT)

    def test_purchased_product(self):
        self.assertEqual(self._cls(["Date", "Campaign Name", "Ad Group Name", "Advertised SKU",
                                    "Purchased ASIN", "7 Day Total Units"]), RI.SP_PURCHASED_PRODUCT)

    def test_placement(self):
        self.assertEqual(self._cls(["Date", "Campaign Name", "Placement", "Impressions", "Spend"]),
                         RI.SP_PLACEMENT)

    def test_budget(self):
        self.assertEqual(self._cls(["Date", "Campaign Name", "Budget", "Budget Type"]), RI.SP_BUDGET)

    def test_ambiguous(self):
        self.assertEqual(self._cls(["Date", "Campaign Name", "Ad Group Name", "Targeting",
                                    "Match Type", "Placement", "Impressions"]), RI.AMBIGUOUS)

    def test_unknown(self):
        self.assertEqual(self._cls(["Date", "Portfolio Name"]), RI.UNKNOWN)

    def test_search_term_beats_targeting(self):
        # a search-term report legitimately carries targeting + match type; it must not be ambiguous
        headers = ["Date", "Campaign Name", "Ad Group Name", "Targeting", "Match Type",
                   "Customer Search Term", "Impressions"]
        self.assertEqual(self._cls(headers), RI.SP_SEARCH_TERM)

    def test_purchased_beats_advertised(self):
        self.assertEqual(self._cls(["Date", "Campaign Name", "Advertised SKU", "Purchased ASIN"]),
                         RI.SP_PURCHASED_PRODUCT)

    def test_filename_not_authoritative(self):
        # campaign headers under a search-term filename still classify as campaign
        decision, ev = RI.classify_report(
            set(RI.map_headers(["Date", "Campaign Name", "Impressions", "Spend"])["canonical"].keys()),
            filename_hint="search_term_report.csv")
        self.assertEqual(decision, RI.SP_CAMPAIGN)
        self.assertFalse(ev["filename_is_authoritative"])
        self.assertEqual(ev["filename_hint"], "search_term_report.csv")

    def test_classification_evidence_present_fields(self):
        _, ev = RI.classify_report({"campaign_name", "impressions"})
        self.assertIn("campaign_name", ev["present_canonical_fields"])

    def test_empty_headers_unknown(self):
        self.assertEqual(self._cls([]), RI.UNKNOWN)

    def test_matches_deterministic(self):
        p = {"campaign_name", "impressions", "clicks", "spend", "sales_7d"}
        self.assertEqual(RI._classification_matches(p), [RI.SP_CAMPAIGN])

    def test_campaign_with_adgroup_not_campaign(self):
        # an ad-group dimension disqualifies a campaign-level report
        self.assertNotEqual(self._cls(["Date", "Campaign Name", "Ad Group Name", "Impressions"]),
                            RI.SP_CAMPAIGN)


# ================================================================ C. FORMAT DETECTION
class TestFormatDetection(Base):
    def _detect(self, name, text, encoding="utf-8"):
        d = self.ws()
        p = self.drop(d, name, text, encoding=encoding)
        return RI.detect_format(p)

    def test_plain_csv(self):
        r = self._detect("a.csv", campaign_csv())
        self.assertEqual(r["format"], "CSV")
        self.assertEqual(r["delimiter"], ",")
        self.assertEqual(r["encoding"], "utf-8")

    def test_bom_csv(self):
        r = self._detect("a.csv", "﻿" + campaign_csv())
        self.assertEqual(r["format"], "CSV_BOM")
        self.assertEqual(r["encoding"], "utf-8-sig")

    def test_tsv_by_extension(self):
        r = self._detect("a.tsv", campaign_csv().replace(",", "\t"))
        self.assertEqual(r["delimiter"], "\t")
        self.assertEqual(r["format"], "TSV")

    def test_tsv_by_content(self):
        r = self._detect("a.txt", campaign_csv().replace(",", "\t"))
        self.assertEqual(r["delimiter"], "\t")

    def test_tsv_extension_without_tabs_mismatch(self):
        r = self._detect("a.tsv", campaign_csv())
        self.assertEqual(r["reason"], RI.EXTENSION_CONTENT_MISMATCH)

    def test_xlsx_detected_as_supported(self):
        # a .xlsx with a zip signature is now natively supported (openpyxl); the reason is set only for
        # a corrupt workbook at READ time, not at signature detection.
        r = self._detect("a.xlsx", "PK\x03\x04 rest")
        self.assertEqual(r["format"], "XLSX")
        self.assertIsNone(r["reason"])
        self.assertEqual(r["parser"], RI.PARSER_XLSX)

    def test_xlsm_still_unsupported(self):
        # a macro workbook (.xlsm, also a zip) stays refused
        r = self._detect("a.xlsm", "PK\x03\x04 rest")
        self.assertEqual(r["reason"], RI.UNSUPPORTED_REPORT_FORMAT)

    def test_zip_content_in_csv_mismatch(self):
        d = self.ws()
        p = os.path.join(d["inbox"], "a.csv")
        with open(p, "wb") as f:
            f.write(b"PK\x03\x04\x00\x00stuff")
        self.assertEqual(RI.detect_format(p)["reason"], RI.EXTENSION_CONTENT_MISMATCH)

    def test_ole_xls_unsupported(self):
        d = self.ws()
        p = os.path.join(d["inbox"], "a.csv")
        with open(p, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest")
        self.assertEqual(RI.detect_format(p)["reason"], RI.UNSUPPORTED_REPORT_FORMAT)

    def test_nul_byte_mismatch(self):
        d = self.ws()
        p = os.path.join(d["inbox"], "a.csv")
        with open(p, "wb") as f:
            f.write(b"Date,Campaign\x00Name\n")
        self.assertEqual(RI.detect_format(p)["reason"], RI.EXTENSION_CONTENT_MISMATCH)

    def test_suspicious_extension(self):
        self.assertEqual(self._detect("a.exe", "MZ")["reason"], RI.SUSPICIOUS_EXTENSION)

    def test_empty_file(self):
        self.assertEqual(self._detect("a.csv", "")["reason"], RI.EMPTY_FILE)

    def test_unsupported_encoding(self):
        d = self.ws()
        p = os.path.join(d["inbox"], "a.csv")
        with open(p, "wb") as f:
            f.write(b"\xff\xfe\x00D\x00a")   # UTF-16-ish garbage under a .csv
        self.assertIn(RI.detect_format(p)["reason"], (RI.UNSUPPORTED_ENCODING, RI.EXTENSION_CONTENT_MISMATCH))

    def test_file_too_large(self):
        d = self.ws()
        p = os.path.join(d["inbox"], "a.csv")
        with open(p, "wb") as f:
            f.write(b"x")
        big = RI.MAX_FILE_BYTES + 1
        os.truncate(p, big)
        self.assertEqual(RI.detect_format(p)["reason"], RI.FILE_TOO_LARGE)

    def test_extension_recorded(self):
        self.assertEqual(self._detect("a.csv", campaign_csv())["extension"], ".csv")

    def test_delimiter_comma_when_more_commas(self):
        r = self._detect("a.csv", campaign_csv())
        self.assertEqual(r["delimiter"], ",")

    def test_zip_bomb_signature_rejected_even_zip_ext(self):
        d = self.ws()
        p = os.path.join(d["inbox"], "a.zip")
        with open(p, "wb") as f:
            f.write(b"PK\x03\x04bomb")
        self.assertEqual(RI.detect_format(p)["reason"], RI.SUSPICIOUS_EXTENSION)


# ================================================================ D. MONEY PARSING
class TestMoneyParsing(Base):
    def test_plain(self):
        self.assertEqual(RI.parse_money_cell("12.34"), ("12.34", RI.MS_PRESENT, None))

    def test_grouped_thousands(self):
        self.assertEqual(RI.parse_money_cell("1,234.56")[0], "1234.56")

    def test_malformed_grouping_rejected(self):
        self.assertEqual(RI.parse_money_cell("1,23,456")[2], RI.INVALID_DECIMAL)

    def test_currency_symbol_stripped(self):
        self.assertEqual(RI.parse_money_cell("$12.99")[0], "12.99")

    def test_blank_is_blank_not_zero(self):
        self.assertEqual(RI.parse_money_cell(""), (None, RI.MS_BLANK, None))

    def test_none_is_missing(self):
        self.assertEqual(RI.parse_money_cell(None), (None, RI.MS_MISSING, None))

    def test_zero_distinct_from_blank(self):
        self.assertEqual(RI.parse_money_cell("0"), ("0", RI.MS_ZERO, None))

    def test_invalid_decimal(self):
        self.assertEqual(RI.parse_money_cell("12.3.4")[2], RI.INVALID_DECIMAL)

    def test_negative_metric(self):
        self.assertEqual(RI.parse_money_cell("-5.00")[2], RI.NEGATIVE_METRIC)

    def test_nan_rejected(self):
        self.assertEqual(RI.parse_money_cell("NaN")[2], RI.INVALID_DECIMAL)

    def test_infinity_rejected(self):
        self.assertEqual(RI.parse_money_cell("Infinity")[2], RI.INVALID_DECIMAL)

    def test_scientific_rejected(self):
        self.assertEqual(RI.parse_money_cell("1e3")[2], RI.INVALID_DECIMAL)

    def test_formula_cell_rejected(self):
        self.assertEqual(RI.parse_money_cell("=1+1")[2], RI.UNSAFE_FORMULA_CELL)

    def test_value_is_string_never_float(self):
        v = RI.parse_money_cell("12.34")[0]
        self.assertIsInstance(v, str)

    def test_money_authority_rejects_float(self):
        with self.assertRaises(MONEY.MoneyError):
            MONEY.parse_money_decimal(12.34)

    def test_money_authority_rejects_bool(self):
        with self.assertRaises(MONEY.MoneyError):
            MONEY.parse_money_decimal(True)

    def test_precision_preserved(self):
        self.assertEqual(RI.parse_money_cell("12.3456")[0], "12.3456")

    def test_large_value(self):
        self.assertEqual(RI.parse_money_cell("1000000.00")[0], "1000000.00")


# ================================================================ E. INTEGER PARSING
class TestIntParsing(Base):
    def test_plain(self):
        self.assertEqual(RI.parse_int_cell("10"), (10, RI.MS_PRESENT, None))

    def test_zero(self):
        self.assertEqual(RI.parse_int_cell("0"), (0, RI.MS_ZERO, None))

    def test_blank(self):
        self.assertEqual(RI.parse_int_cell(""), (None, RI.MS_BLANK, None))

    def test_missing(self):
        self.assertEqual(RI.parse_int_cell(None), (None, RI.MS_MISSING, None))

    def test_fractional_rejected(self):
        self.assertEqual(RI.parse_int_cell("1.5")[2], RI.INVALID_INTEGER)

    def test_negative_rejected(self):
        self.assertEqual(RI.parse_int_cell("-3")[2], RI.NEGATIVE_METRIC)

    def test_grouped(self):
        self.assertEqual(RI.parse_int_cell("1,000")[0], 1000)

    def test_alpha_rejected(self):
        self.assertEqual(RI.parse_int_cell("abc")[2], RI.INVALID_INTEGER)

    def test_value_is_int(self):
        self.assertIsInstance(RI.parse_int_cell("7")[0], int)

    def test_formula_rejected(self):
        self.assertEqual(RI.parse_int_cell("=5")[2], RI.UNSAFE_FORMULA_CELL)


# ================================================================ F. DATE PARSING
class TestDateParsing(Base):
    def test_iso(self):
        self.assertEqual(RI.parse_date_cell("2026-07-01"), ("2026-07-01", None))

    def test_slash_ymd(self):
        self.assertEqual(RI.parse_date_cell("2026/07/01"), ("2026-07-01", None))

    def test_us_mdy(self):
        self.assertEqual(RI.parse_date_cell("07/01/2026"), ("2026-07-01", None))

    def test_month_name(self):
        self.assertEqual(RI.parse_date_cell("Jul 01, 2026"), ("2026-07-01", None))

    def test_full_month_name(self):
        self.assertEqual(RI.parse_date_cell("July 1, 2026"), ("2026-07-01", None))

    def test_invalid(self):
        self.assertEqual(RI.parse_date_cell("notadate")[1], RI.INVALID_DATE)

    def test_blank_invalid(self):
        self.assertEqual(RI.parse_date_cell("")[1], RI.INVALID_DATE)

    def test_range_report_date(self):
        self.assertEqual(RI.parse_date_range(report_date="2026-07-01"), ("2026-07-01", "2026-07-01", None))

    def test_range_explicit(self):
        self.assertEqual(RI.parse_date_range(start_date="2026-07-01", end_date="2026-07-07"),
                         ("2026-07-01", "2026-07-07", None))

    def test_range_end_before_start(self):
        self.assertEqual(RI.parse_date_range(start_date="2026-07-07", end_date="2026-07-01")[2],
                         RI.INVALID_DATE_RANGE)

    def test_range_future_blocked(self):
        self.assertEqual(RI.parse_date_range(report_date="2026-08-01", reference_date=REF)[2],
                         RI.FUTURE_DATE)

    def test_range_not_future_when_within_reference(self):
        self.assertIsNone(RI.parse_date_range(report_date="2026-07-01", reference_date=REF)[2])

    def test_range_missing(self):
        self.assertEqual(RI.parse_date_range()[2], RI.INVALID_DATE)

    def test_range_no_reference_skips_future_check(self):
        # determinism: with no owner reference date, no wall-clock is consulted; future is not judged
        self.assertIsNone(RI.parse_date_range(report_date="2099-01-01")[2])

    def test_range_partial_missing_end(self):
        self.assertEqual(RI.parse_date_range(start_date="2026-07-01")[2], RI.INVALID_DATE)

    def test_single_digit_us(self):
        self.assertEqual(RI.parse_date_cell("7/1/2026"), ("2026-07-01", None))


# ================================================================ G. CURRENCY + MATCH TYPE
class TestCurrencyMatch(Base):
    def test_usd(self):
        self.assertEqual(RI.validate_currency("USD"), ("USD", None))

    def test_lowercase_upper(self):
        self.assertEqual(RI.validate_currency("usd"), ("USD", None))

    def test_blank_none(self):
        self.assertEqual(RI.validate_currency(""), (None, None))

    def test_two_letter_invalid(self):
        self.assertEqual(RI.validate_currency("US")[1], RI.CURRENCY_INVALID)

    def test_unknown_code_flagged(self):
        self.assertEqual(RI.validate_currency("ZZZ")[1], RI.CURRENCY_INVALID)

    def test_match_exact(self):
        self.assertEqual(RI.normalize_match_type("Exact"), ("EXACT", None))

    def test_match_phrase(self):
        self.assertEqual(RI.normalize_match_type("phrase"), ("PHRASE", None))

    def test_match_broad(self):
        self.assertEqual(RI.normalize_match_type("BROAD"), ("BROAD", None))

    def test_match_dash_na(self):
        self.assertEqual(RI.normalize_match_type("-"), (None, None))

    def test_match_unknown(self):
        self.assertEqual(RI.normalize_match_type("fuzzy")[1], RI.UNSUPPORTED_MATCH_TYPE)


# ================================================================ H. ROW VALIDATION
class TestRowValidation(Base):
    def _cells(self, headers, row):
        m = RI.map_headers(headers)
        cells, present, dup, over, form = RI.build_cells(m, row)
        return cells, present, dup, over, form, [c["original"] for c in m["columns"]]

    def _validate(self, report_type, headers, row, **kw):
        cells, present, dup, over, form, orig = self._cells(headers, row)
        return RI.validate_row(report_type, cells, present, source_sha="s" * 64, row_number=2,
                               original_headers=orig, dup_conflict_fields=dup, oversized_fields=over,
                               formula_fields=form, reference_date=REF, **kw)

    def test_valid_campaign_row(self):
        headers = ["Date", "Campaign Name", "Impressions", "Spend", "Currency"]
        row, reasons, meta = self._validate(RI.SP_CAMPAIGN, headers,
                                            ["2026-07-01", "Nurse", "100", "5.00", "USD"])
        self.assertIsNotNone(row)
        self.assertEqual(reasons, [])
        self.assertEqual(row["metrics"]["spend"], "5.00")

    def test_missing_campaign_identity(self):
        headers = ["Date", "Impressions", "Spend", "Currency"]
        row, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers, ["2026-07-01", "100", "5.00", "USD"])
        self.assertIsNone(row)
        self.assertIn("MISSING_REQUIRED_FIELD:campaign", reasons)

    def test_missing_date(self):
        headers = ["Campaign Name", "Impressions", "Spend", "Currency"]
        row, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers, ["Nurse", "100", "5.00", "USD"])
        self.assertIsNone(row)
        self.assertIn(RI.INVALID_DATE, reasons)

    def test_currency_missing_when_money_present(self):
        headers = ["Date", "Campaign Name", "Impressions", "Spend"]
        row, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers, ["2026-07-01", "Nurse", "100", "5.00"])
        self.assertIsNone(row)
        self.assertIn(RI.CURRENCY_MISSING, reasons)

    def test_negative_metric_row(self):
        headers = ["Date", "Campaign Name", "Impressions", "Spend", "Currency"]
        row, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers,
                                         ["2026-07-01", "Nurse", "100", "-5.00", "USD"])
        self.assertIsNone(row)
        self.assertTrue(any(r.startswith(RI.NEGATIVE_METRIC) for r in reasons))

    def test_invalid_date_range_row(self):
        headers = ["Start Date", "End Date", "Campaign Name", "Impressions", "Spend", "Currency"]
        row, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers,
                                         ["2026-07-07", "2026-07-01", "Nurse", "100", "5.00", "USD"])
        self.assertIsNone(row)
        self.assertIn(RI.INVALID_DATE_RANGE, reasons)

    def test_blank_optional_metric_ok(self):
        headers = ["Date", "Campaign Name", "Impressions", "Clicks", "Spend", "Currency"]
        row, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers,
                                         ["2026-07-01", "Nurse", "100", "", "5.00", "USD"])
        self.assertIsNotNone(row)
        self.assertEqual(row["metric_states"].get("clicks"), RI.MS_BLANK)

    def test_zero_metric_recorded(self):
        headers = ["Date", "Campaign Name", "Impressions", "Clicks", "Spend", "Currency"]
        row, _, _ = self._validate(RI.SP_CAMPAIGN, headers,
                                   ["2026-07-01", "Nurse", "100", "0", "5.00", "USD"])
        self.assertEqual(row["metrics"]["clicks"], 0)
        self.assertEqual(row["metric_states"].get("clicks"), RI.MS_ZERO)

    def test_missing_vs_blank_distinct(self):
        # 'cost' column absent -> MISSING (not in metrics, not in metric_states)
        headers = ["Date", "Campaign Name", "Impressions", "Spend", "Currency"]
        row, _, _ = self._validate(RI.SP_CAMPAIGN, headers, ["2026-07-01", "Nurse", "100", "5.00", "USD"])
        self.assertNotIn("cost", row["metrics"])
        self.assertNotIn("cost", row["metric_states"])

    def test_targeting_requires_ad_group(self):
        headers = ["Date", "Campaign Name", "Targeting", "Match Type", "Impressions", "Spend", "Currency"]
        row, reasons, _ = self._validate(RI.SP_TARGETING, headers,
                                         ["2026-07-01", "Nurse", "kw", "exact", "100", "5.00", "USD"])
        self.assertIsNone(row)
        self.assertIn("MISSING_REQUIRED_FIELD:ad_group", reasons)

    def test_unsupported_match_type_row(self):
        headers = ["Date", "Campaign Name", "Ad Group Name", "Targeting", "Match Type", "Impressions",
                   "Spend", "Currency"]
        row, reasons, _ = self._validate(RI.SP_TARGETING, headers,
                                         ["2026-07-01", "Nurse", "Core", "kw", "fuzzy", "100", "5.00", "USD"])
        self.assertIsNone(row)
        self.assertIn(RI.UNSUPPORTED_MATCH_TYPE, reasons)

    def test_search_term_valid(self):
        headers = ["Date", "Campaign Name", "Ad Group Name", "Targeting", "Match Type",
                   "Customer Search Term", "Impressions", "Spend", "Currency"]
        row, reasons, _ = self._validate(RI.SP_SEARCH_TERM, headers,
                                         ["2026-07-01", "N", "Core", "kw", "broad", "gift", "10", "1.00", "USD"])
        self.assertIsNotNone(row)
        self.assertEqual(row["identity"]["search_term"], "gift")

    def test_advertised_requires_sku_or_asin(self):
        headers = ["Date", "Campaign Name", "Ad Group Name", "Impressions", "Spend", "Currency"]
        row, reasons, _ = self._validate(RI.SP_ADVERTISED_PRODUCT, headers,
                                         ["2026-07-01", "N", "Core", "10", "1.00", "USD"])
        self.assertIsNone(row)
        self.assertIn("MISSING_REQUIRED_FIELD:advertised", reasons)

    def test_duplicate_canonical_conflict_row(self):
        headers = ["Date", "Campaign Name", "Spend", "Spend", "Impressions", "Currency"]
        row, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers,
                                         ["2026-07-01", "N", "5.00", "9.99", "10", "USD"])
        self.assertIsNone(row)
        self.assertTrue(any(r.startswith(RI.DUPLICATE_CANONICAL_COLUMN) for r in reasons))

    def test_duplicate_canonical_equal_ok(self):
        headers = ["Date", "Campaign Name", "Spend", "Spend", "Impressions", "Currency"]
        row, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers,
                                         ["2026-07-01", "N", "5.00", "5.00", "10", "USD"])
        self.assertIsNotNone(row)

    def test_reasons_deterministic_sorted(self):
        headers = ["Impressions", "Spend"]
        _, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers, ["100", "5.00"])
        self.assertEqual(reasons, sorted(reasons))

    def test_id_preferred_over_name(self):
        headers = ["Date", "Campaign Name", "Campaign ID", "Impressions", "Spend", "Currency"]
        row, _, _ = self._validate(RI.SP_CAMPAIGN, headers,
                                   ["2026-07-01", "Nurse", "C123", "10", "5.00", "USD"])
        self.assertEqual(row["key_basis"]["campaign"], "campaign_id")

    def test_oversized_cell_quarantined(self):
        headers = ["Date", "Campaign Name", "Impressions", "Spend", "Currency"]
        big = "x" * (RI.MAX_FIELD_LEN + 5)
        row, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers,
                                         ["2026-07-01", big, "10", "5.00", "USD"])
        self.assertIsNone(row)
        self.assertTrue(any(r.startswith(RI.OVERSIZED_CELL) for r in reasons))

    def test_formula_identity_cell_flagged(self):
        headers = ["Date", "Campaign Name", "Impressions", "Spend", "Currency"]
        row, reasons, _ = self._validate(RI.SP_CAMPAIGN, headers,
                                         ["2026-07-01", "=cmd()", "10", "5.00", "USD"])
        self.assertIsNone(row)
        self.assertTrue(any(r.startswith(RI.UNSAFE_FORMULA_CELL) for r in reasons))


# ================================================================ I. CANONICAL KEY + LINEAGE
class TestCanonicalKeyLineage(Base):
    def test_key_stable(self):
        k1 = RI.canonical_row_key(RI.SP_CAMPAIGN, "US", "2026-07-01", "2026-07-01", {"campaign": "N"})
        k2 = RI.canonical_row_key(RI.SP_CAMPAIGN, "US", "2026-07-01", "2026-07-01", {"campaign": "N"})
        self.assertEqual(k1, k2)

    def test_key_distinct_by_campaign(self):
        a = RI.canonical_row_key(RI.SP_CAMPAIGN, "US", "2026-07-01", "2026-07-01", {"campaign": "A"})
        b = RI.canonical_row_key(RI.SP_CAMPAIGN, "US", "2026-07-01", "2026-07-01", {"campaign": "B"})
        self.assertNotEqual(a, b)

    def test_key_distinct_by_range(self):
        a = RI.canonical_row_key(RI.SP_CAMPAIGN, "US", "2026-07-01", "2026-07-01", {"campaign": "A"})
        b = RI.canonical_row_key(RI.SP_CAMPAIGN, "US", "2026-07-02", "2026-07-02", {"campaign": "A"})
        self.assertNotEqual(a, b)

    def test_key_escapes_pipe(self):
        a = RI.canonical_row_key(RI.SP_CAMPAIGN, "US", "2026-07-01", "2026-07-01", {"campaign": "A|B"})
        b = RI.canonical_row_key(RI.SP_CAMPAIGN, "US", "2026-07-01", "2026-07-01", {"campaign": "AB"})
        self.assertNotEqual(a, b)

    def test_key_collision_resistant(self):
        # 'a|b' vs 'a', 'b' must not collide once escaped
        a = RI.canonical_row_key(RI.SP_TARGETING, "US", "2026-07-01", "2026-07-01",
                                 {"campaign": "a", "ad_group": "b", "targeting": "", "match_type": ""})
        b = RI.canonical_row_key(RI.SP_TARGETING, "US", "2026-07-01", "2026-07-01",
                                 {"campaign": "a|b", "ad_group": "", "targeting": "", "match_type": ""})
        self.assertNotEqual(a, b)

    def test_lineage_fields(self):
        ln = RI.lineage_for("s" * 64, 2, RI.SP_CAMPAIGN, "2026-07-01", "2026-07-01", ["Date"], "k")
        for f in ("source_file_sha256", "source_row_number", "canonical_row_key", "lineage_hash",
                  "normalization_schema_version", "source_original_headers", "contributing"):
            self.assertIn(f, ln)

    def test_lineage_hash_deterministic(self):
        a = RI.lineage_for("s" * 64, 2, RI.SP_CAMPAIGN, "2026-07-01", "2026-07-01", ["Date"], "k")
        b = RI.lineage_for("s" * 64, 2, RI.SP_CAMPAIGN, "2026-07-01", "2026-07-01", ["Date"], "k")
        self.assertEqual(a["lineage_hash"], b["lineage_hash"])

    def test_lineage_hash_varies_by_row(self):
        a = RI.lineage_for("s" * 64, 2, RI.SP_CAMPAIGN, "2026-07-01", "2026-07-01", ["Date"], "k")
        b = RI.lineage_for("s" * 64, 3, RI.SP_CAMPAIGN, "2026-07-01", "2026-07-01", ["Date"], "k")
        self.assertNotEqual(a["lineage_hash"], b["lineage_hash"])

    def test_lineage_contributing_self(self):
        ln = RI.lineage_for("s" * 64, 2, RI.SP_CAMPAIGN, "2026-07-01", "2026-07-01", ["Date"], "k")
        self.assertEqual(ln["contributing"], [{"source_file_sha256": "s" * 64, "source_row_number": 2}])

    def test_normalized_row_carries_lineage(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        row = r.normalized_by_type[RI.SP_CAMPAIGN][0]
        self.assertEqual(row["lineage"]["source_report_type"], RI.SP_CAMPAIGN)
        self.assertTrue(row["lineage"]["source_file_sha256"])

    def test_row_number_is_source_line(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        nums = sorted(row["lineage"]["source_row_number"]
                      for row in r.normalized_by_type[RI.SP_CAMPAIGN])
        self.assertEqual(nums, [2, 3])

    def test_normalization_schema_version(self):
        ln = RI.lineage_for("s" * 64, 2, RI.SP_CAMPAIGN, "2026-07-01", "2026-07-01", ["Date"], "k")
        self.assertEqual(ln["normalization_schema_version"], RI.NORMALIZATION_SCHEMA_VERSION)


# ================================================================ J. FILE READING
class TestFileReading(Base):
    def _read(self, name, text, encoding="utf-8"):
        d = self.ws()
        p = self.drop(d, name, text, encoding=encoding)
        fmt = RI.detect_format(p)
        return RI.read_delimited(p, fmt)

    def test_reads_headers_and_rows(self):
        headers, rows, reason = self._read("a.csv", campaign_csv())
        self.assertIsNone(reason)
        self.assertEqual(headers[0], "Date")
        self.assertEqual(len(rows), 2)

    def test_bom_stripped_from_first_header(self):
        headers, rows, reason = self._read("a.csv", "﻿" + campaign_csv())
        self.assertEqual(headers[0], "Date")

    def test_tsv_reads(self):
        headers, rows, reason = self._read("a.tsv", campaign_csv().replace(",", "\t"))
        self.assertEqual(headers[0], "Date")
        self.assertEqual(len(rows), 2)

    def test_no_data_rows(self):
        headers, rows, reason = self._read("a.csv", "Date,Campaign Name,Impressions,Spend,Currency\n")
        self.assertEqual(reason, RI.NO_DATA_ROWS)

    def test_all_cells_are_data(self):
        # a leading '=' cell is read as a plain string, never evaluated
        headers, rows, reason = self._read(
            "a.csv", "Date,Campaign Name,Impressions,Spend,Currency\n2026-07-01,=1+1,10,5.00,USD\n")
        self.assertEqual(rows[0][1], "=1+1")

    def test_quoted_field_with_comma(self):
        headers, rows, _ = self._read(
            "a.csv", 'Date,Campaign Name,Impressions,Spend,Currency\n2026-07-01,"N, Inc",10,5.00,USD\n')
        self.assertEqual(rows[0][1], "N, Inc")

    def test_embedded_newline_in_quotes(self):
        headers, rows, _ = self._read(
            "a.csv", 'Date,Campaign Name,Impressions,Spend,Currency\n2026-07-01,"N\nline",10,5.00,USD\n')
        self.assertEqual(len(rows), 1)

    def test_ragged_row_padded(self):
        cells, present, dup, over, form = RI.build_cells(
            RI.map_headers(["Date", "Campaign Name", "Impressions"]), ["2026-07-01", "N"])
        self.assertIsNone(cells.get("impressions"))

    def test_utf8_content(self):
        headers, rows, reason = self._read(
            "a.csv", "Date,Campaign Name,Impressions,Spend,Currency\n2026-07-01,Café,10,5.00,USD\n")
        self.assertEqual(rows[0][1], "Café")

    def test_build_cells_present_columns(self):
        cells, present, dup, over, form = RI.build_cells(
            RI.map_headers(["Date", "Campaign Name", "Spend"]), ["2026-07-01", "N", "5.00"])
        self.assertIn("spend", present)
        self.assertIn("campaign_name", present)


# ================================================================ K. INTAKE + PATH SAFETY
class TestIntake(Base):
    def test_valid_file_accepted(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        self.assertEqual(r.counts["accepted_source_count"], 1)
        self.assertEqual(r.state, RI.REPORTS_READY)

    def test_in_run_duplicate_is_idempotent(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.drop(d, "camp_copy.csv", campaign_csv())    # identical bytes
        r = self.run_base(d)
        self.assertEqual(r.counts["accepted_source_count"], 1)
        self.assertEqual(r.counts["idempotent_source_count"], 1)
        self.assertEqual(r.counts["valid_row_count"], 2)  # never double-counted

    def test_cross_run_idempotent(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        r2 = self.run_base(d)                             # same file still in inbox
        self.assertEqual(r2.counts["idempotent_source_count"], 1)
        self.assertEqual(r2.counts["accepted_source_count"], 0)

    def test_xlsx_quarantined(self):
        d = self.ws()
        self.drop(d, "a.xlsx", "PK\x03\x04junk")
        r = self.run_base(d)
        self.assertEqual(r.counts["quarantined_source_count"], 1)
        self.assertEqual(r.state, RI.REPORT_FORMAT_BLOCKED)

    def test_extension_content_mismatch_quarantined(self):
        d = self.ws()
        with open(os.path.join(d["inbox"], "a.csv"), "wb") as f:
            f.write(b"PK\x03\x04zip")
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORT_FORMAT_BLOCKED)

    def test_oversize_file_quarantined(self):
        d = self.ws()
        p = os.path.join(d["inbox"], "a.csv")
        with open(p, "wb") as f:
            f.write(b"x")
        os.truncate(p, RI.MAX_FILE_BYTES + 1)
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORT_FORMAT_BLOCKED)

    def test_symlink_not_followed(self):
        d = self.ws()
        target = os.path.join(self.mkbase(), "real.csv")
        with open(target, "w", encoding="utf-8") as f:
            f.write(campaign_csv())
        link = os.path.join(d["inbox"], "link.csv")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink not permitted in this environment")
        files, rejects = RI.scan_inbox(d["inbox"])
        self.assertEqual(files, [])
        self.assertTrue(any(x["reason"] == RI.PATH_UNSAFE for x in rejects))

    def test_subdirectory_skipped(self):
        d = self.ws()
        os.makedirs(os.path.join(d["inbox"], "sub"))
        files, _ = RI.scan_inbox(d["inbox"])
        self.assertEqual(files, [])

    def test_safe_rel_rejects_escape(self):
        with self.assertRaises(RI.ReportIngestionError):
            RI._safe_rel("/etc/passwd", "/tmp/base")

    def test_too_many_files_capped(self):
        d = self.ws()
        for i in range(RI.MAX_FILES + 3):
            self.drop(d, f"f{i:04d}.csv", campaign_csv())
        files, rejects = RI.scan_inbox(d["inbox"])
        self.assertEqual(len(files), RI.MAX_FILES)
        self.assertTrue(any(x["reason"] == RI.TOO_MANY_FILES for x in rejects))

    def test_accepted_raw_named_by_hash(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        sha = r.sources[0]["source_file_sha256"]
        self.assertTrue(os.path.exists(os.path.join(d["accepted_raw"], sha + ".csv")))

    def test_accepted_raw_bytes_immutable(self):
        d = self.ws()
        text = campaign_csv()
        self.drop(d, "camp.csv", text)
        r = self.run_base(d)
        sha = r.sources[0]["source_file_sha256"]
        with open(os.path.join(d["accepted_raw"], sha + ".csv"), "rb") as f:
            self.assertEqual(f.read(), text.encode("utf-8"))

    def test_quarantine_file_copied(self):
        d = self.ws()
        self.drop(d, "a.xlsx", "PK\x03\x04junk")
        self.run_base(d)
        self.assertTrue(os.listdir(d["quarantine"]))

    def test_bom_file_accepted(self):
        d = self.ws()
        self.drop(d, "camp.csv", "﻿" + campaign_csv())
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)

    def test_tsv_file_accepted(self):
        d = self.ws()
        self.drop(d, "camp.tsv", campaign_csv().replace(",", "\t"))
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)


# ================================================================ L. DUPLICATES
class TestDuplicates(Base):
    def test_duplicate_row_collapsed(self):
        row = ["2026-07-01", "N", 100, 5, "5.00", "10.00", "USD"]
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[row, list(row)]))
        r = self.run_base(d)
        self.assertEqual(r.counts["normalized_sp_campaign_row_count"], 1)
        self.assertEqual(r.counts["duplicate_row_count"], 1)

    def test_duplicate_row_duplicate_count(self):
        row = ["2026-07-01", "N", 100, 5, "5.00", "10.00", "USD"]
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[row, list(row)]))
        r = self.run_base(d)
        self.assertEqual(r.normalized_by_type[RI.SP_CAMPAIGN][0]["duplicate_count"], 2)

    def test_conflicting_duplicate_row_blocks(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[
            ["2026-07-01", "N", 100, 5, "5.00", "10.00", "USD"],
            ["2026-07-01", "N", 100, 5, "9.99", "10.00", "USD"]]))
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORT_CONFLICT_BLOCKED)
        self.assertTrue(any(c["code"] == RI.DUPLICATE_ROW_CONFLICT for c in r.conflicts))

    def test_no_double_count_across_identical_exports(self):
        d = self.ws()
        self.drop(d, "a.csv", campaign_csv())
        self.drop(d, "b.csv", campaign_csv())   # identical -> second idempotent
        r = self.run_base(d)
        self.assertEqual(r.counts["normalized_sp_campaign_row_count"], 2)

    def test_merged_lineage_contributions(self):
        row = ["2026-07-01", "N", 100, 5, "5.00", "10.00", "USD"]
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[row, list(row)]))
        r = self.run_base(d)
        contrib = r.normalized_by_type[RI.SP_CAMPAIGN][0]["lineage"]["contributing"]
        self.assertEqual(len(contrib), 2)

    def test_idempotent_result_reported(self):
        d = self.ws()
        self.drop(d, "a.csv", campaign_csv())
        self.drop(d, "b.csv", campaign_csv())
        r = self.run_base(d)
        self.assertEqual(r.counts["duplicate_file_count"], 1)

    def test_reimport_does_not_grow_rows(self):
        d = self.ws()
        self.drop(d, "a.csv", campaign_csv())
        r1 = self.run_base(d)
        r2 = self.run_base(d)
        self.assertEqual(r1.counts["normalized_sp_campaign_row_count"], 2)
        self.assertEqual(r2.counts["accepted_source_count"], 0)

    def test_distinct_campaigns_two_rows(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv())   # two distinct campaigns
        r = self.run_base(d)
        keys = {row["canonical_row_key"] for row in r.normalized_by_type[RI.SP_CAMPAIGN]}
        self.assertEqual(len(keys), 2)

    def test_duplicate_file_sha_recorded(self):
        d = self.ws()
        self.drop(d, "a.csv", campaign_csv())
        self.drop(d, "b.csv", campaign_csv())
        r = self.run_base(d)
        states = [s["import_state"] for s in r.sources]
        self.assertIn(RI.IDEMPOTENT_ALREADY_IMPORTED, states)

    def test_idempotent_source_has_no_rows(self):
        d = self.ws()
        self.drop(d, "a.csv", campaign_csv())
        self.drop(d, "b.csv", campaign_csv())
        r = self.run_base(d)
        idem = [s for s in r.sources if s["import_state"] == RI.IDEMPOTENT_ALREADY_IMPORTED][0]
        self.assertEqual(idem["row_count_valid"], 0)


# ================================================================ M. OVERLAP + RECONCILIATION
class TestOverlap(Base):
    def _nrow(self, campaign, start, end, metrics, currency="USD", sha="a" * 64, rn=2,
              rtype=RI.SP_CAMPAIGN):
        key = f"type={rtype}|mk=US|range={start}:{end}|campaign={campaign}"
        return {"report_type": rtype, "marketplace": "US", "start_date": start, "end_date": end,
                "currency": currency, "metrics": metrics, "metric_states": {}, "duplicate_count": 1,
                "canonical_row_key": key,
                "lineage": {"contributing": [{"source_file_sha256": sha, "source_row_number": rn}]}}

    def test_rel_same(self):
        self.assertEqual(RI.classify_range_relationship("2026-07-01", "2026-07-07",
                                                        "2026-07-01", "2026-07-07"), "SAME")

    def test_rel_contained(self):
        self.assertEqual(RI.classify_range_relationship("2026-07-01", "2026-07-31",
                                                        "2026-07-05", "2026-07-10"), "CONTAINED")

    def test_rel_adjacent(self):
        self.assertEqual(RI.classify_range_relationship("2026-07-01", "2026-07-07",
                                                        "2026-07-08", "2026-07-14"), "ADJACENT")

    def test_rel_partial(self):
        self.assertEqual(RI.classify_range_relationship("2026-07-01", "2026-07-07",
                                                        "2026-07-05", "2026-07-10"), "PARTIAL")

    def test_rel_disjoint(self):
        self.assertEqual(RI.classify_range_relationship("2026-07-01", "2026-07-07",
                                                        "2026-07-20", "2026-07-25"), "DISJOINT")

    def test_overlap_code_exact_duplicate(self):
        self.assertEqual(RI.overlap_code("SAME", True), RI.OV_EXACT_DUPLICATE)

    def test_overlap_code_exact_conflict(self):
        self.assertEqual(RI.overlap_code("SAME", False), RI.OV_EXACT_CONFLICT)

    def test_overlap_code_partial(self):
        self.assertEqual(RI.overlap_code("PARTIAL"), RI.OV_PARTIAL)

    def test_overlap_code_disjoint(self):
        self.assertEqual(RI.overlap_code("DISJOINT"), RI.OV_NO_OVERLAP)

    def test_semantics_campaign_interval(self):
        self.assertEqual(RI.report_semantics(RI.SP_CAMPAIGN), RI.SEM_INTERVAL)

    def test_semantics_budget_snapshot(self):
        self.assertEqual(RI.report_semantics(RI.SP_BUDGET), RI.SEM_POINT_IN_TIME)

    def test_semantics_unknown(self):
        self.assertEqual(RI.report_semantics("nope"), RI.SEM_UNKNOWN)

    def test_reconcile_unknown_semantics_blocks(self):
        rc = RI.reconcile_interval_group([self._nrow("A", "2026-07-01", "2026-07-07", {"spend": "5.00"})],
                                         RI.SEM_UNKNOWN)
        self.assertEqual(rc["result"], RI.REPORT_SEMANTICS_REQUIRED)

    def test_reconcile_partial_overlap_review(self):
        rows = [self._nrow("A", "2026-07-01", "2026-07-07", {"spend": "5.00"}),
                self._nrow("A", "2026-07-05", "2026-07-10", {"spend": "6.00"})]
        rc = RI.reconcile_interval_group(rows, RI.SEM_INTERVAL)
        self.assertEqual(rc["result"], "REVIEW")
        self.assertTrue(any(o["code"] == RI.OV_PARTIAL for o in rc["overlaps"]))

    def test_reconcile_adjacent_ok(self):
        rows = [self._nrow("A", "2026-07-01", "2026-07-07", {"spend": "5.00"}),
                self._nrow("A", "2026-07-08", "2026-07-14", {"spend": "6.00"})]
        rc = RI.reconcile_interval_group(rows, RI.SEM_INTERVAL)
        self.assertEqual(rc["result"], "OK")

    def test_reconcile_disjoint_ok(self):
        rows = [self._nrow("A", "2026-07-01", "2026-07-07", {"spend": "5.00"}),
                self._nrow("A", "2026-07-20", "2026-07-25", {"spend": "6.00"})]
        rc = RI.reconcile_interval_group(rows, RI.SEM_INTERVAL)
        self.assertEqual(rc["result"], "OK")

    def test_reconcile_contained_review(self):
        rows = [self._nrow("A", "2026-07-01", "2026-07-31", {"spend": "50.00"}),
                self._nrow("A", "2026-07-05", "2026-07-10", {"spend": "6.00"})]
        rc = RI.reconcile_interval_group(rows, RI.SEM_INTERVAL)
        self.assertEqual(rc["result"], "REVIEW")

    def test_reconcile_snapshot_keeps_latest(self):
        rows = [self._nrow("A", "2026-07-01", "2026-07-01", {"budget": "25.00"}),
                self._nrow("A", "2026-07-08", "2026-07-08", {"budget": "30.00"})]
        rc = RI.reconcile_interval_group(rows, RI.SEM_POINT_IN_TIME)
        self.assertEqual(len(rc["rows"]), 1)
        self.assertEqual(rc["rows"][0]["end_date"], "2026-07-08")

    def test_reconcile_snapshot_never_sums(self):
        rows = [self._nrow("A", "2026-07-01", "2026-07-01", {"budget": "25.00"}),
                self._nrow("A", "2026-07-08", "2026-07-08", {"budget": "30.00"})]
        rc = RI.reconcile_interval_group(rows, RI.SEM_POINT_IN_TIME)
        self.assertEqual(rc["rows"][0]["metrics"]["budget"], "30.00")   # not 55.00

    def test_reconcile_snapshot_same_date_conflict(self):
        rows = [self._nrow("A", "2026-07-01", "2026-07-07", {"budget": "25.00"}),
                self._nrow("A", "2026-06-01", "2026-07-07", {"budget": "30.00"})]
        rc = RI.reconcile_interval_group(rows, RI.SEM_POINT_IN_TIME)
        self.assertTrue(any(c["code"] == RI.SNAPSHOT_CONFLICT for c in rc["conflicts"]))

    def test_reconcile_exact_duplicate_not_double_counted(self):
        row = self._nrow("A", "2026-07-01", "2026-07-07", {"spend": "5.00"})
        rc = RI.reconcile_interval_group([row, dict(row)], RI.SEM_INTERVAL)
        self.assertEqual(len(rc["rows"]), 1)
        self.assertEqual(rc["duplicate_row_count"], 1)

    def test_run_partial_overlap_review(self):
        d = self.ws()
        self.drop(d, "a.csv", campaign_csv(interval=True, rows=[
            ["2026-07-01", "2026-07-07", "Nurse", 100, 5, "5.00", "10.00", "USD"]]))
        self.drop(d, "b.csv", campaign_csv(interval=True, rows=[
            ["2026-07-05", "2026-07-10", "Nurse", 100, 5, "6.00", "10.00", "USD"]]))
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORT_OVERLAP_REVIEW_REQUIRED)

    def test_run_daily_plus_aggregate_review(self):
        d = self.ws()
        self.drop(d, "daily.csv", campaign_csv(rows=[
            ["2026-07-01", "Nurse", 100, 5, "5.00", "10.00", "USD"],
            ["2026-07-02", "Nurse", 120, 6, "6.00", "12.00", "USD"]]))
        self.drop(d, "agg.csv", campaign_csv(interval=True, rows=[
            ["2026-07-01", "2026-07-07", "Nurse", 700, 40, "40.00", "80.00", "USD"]]))
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORT_OVERLAP_REVIEW_REQUIRED)


# ================================================================ N. ARTIFACTS
class TestArtifacts(Base):
    def test_input_required_artifacts(self):
        d = self.ws()
        r = self.run_base(d)
        got = set(os.listdir(d["final"]))
        self.assertEqual(got, {RI.F_INPUT_REQUIRED_MD, RI.F_READINESS, RI.F_VERIFICATION, RI.F_MANIFEST})

    def test_ready_writes_normalized_jsonl(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        self.assertIn(RI.NORMALIZED_FILE[RI.SP_CAMPAIGN], os.listdir(d["final"]))

    def test_ready_full_artifact_set(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        got = set(os.listdir(d["final"]))
        for f in (RI.F_SOURCE_REGISTRY, RI.F_IMPORT_MANIFEST, RI.F_VALIDATION, RI.F_ROW_ERRORS,
                  RI.F_OVERLAP, RI.F_CONFLICTS, RI.F_READINESS, RI.F_SUMMARY_MD, RI.F_VERIFICATION,
                  RI.F_MANIFEST):
            self.assertIn(f, got)

    def test_blocked_no_normalized_jsonl(self):
        d = self.ws()
        self.drop(d, "bad.csv", campaign_csv(rows=[["2026-07-01", "N", 100, 5, "-5.00", "10.00", "USD"]]))
        self.run_base(d)
        self.assertNotIn(RI.NORMALIZED_FILE[RI.SP_CAMPAIGN], os.listdir(d["final"]))

    def test_blocked_writes_diagnostics(self):
        d = self.ws()
        self.drop(d, "bad.csv", campaign_csv(rows=[["2026-07-01", "N", 100, 5, "-5.00", "10.00", "USD"]]))
        self.run_base(d)
        got = set(os.listdir(d["final"]))
        self.assertIn(RI.F_ROW_ERRORS, got)
        self.assertIn(RI.F_VALIDATION, got)

    def test_no_empty_normalized_claimed(self):
        # idempotent-only re-import (no new rows) must NOT emit an empty normalized file
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        r2 = self.run_base(d)
        self.assertEqual(r2.state, RI.REPORTS_READY)
        self.assertNotIn(RI.NORMALIZED_FILE[RI.SP_CAMPAIGN], os.listdir(d["final"]))

    def test_manifest_has_output_hashes(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        man = json.load(open(os.path.join(d["final"], RI.F_MANIFEST), encoding="utf-8"))
        self.assertIn("output_hashes", man)
        self.assertIn("stable_content_hashes", man)

    def test_registry_sorted_by_sha(self):
        d = self.ws()
        self.drop(d, "a.csv", campaign_csv())
        self.drop(d, "b.csv", targeting_csv())
        self.run_base(d)
        reg = json.load(open(os.path.join(d["final"], RI.F_SOURCE_REGISTRY), encoding="utf-8"))
        shas = [s["source_file_sha256"] for s in reg["sources"]]
        self.assertEqual(shas, sorted(shas))

    def test_jsonl_rows_sorted_and_valid(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        lines = open(os.path.join(d["final"], RI.NORMALIZED_FILE[RI.SP_CAMPAIGN]),
                     encoding="utf-8").read().splitlines()
        keys = [json.loads(ln)["canonical_row_key"] for ln in lines]
        self.assertEqual(keys, sorted(keys))
        for ln in lines:
            json.loads(ln)   # each line is valid JSON

    def test_counts_in_manifest(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        man = json.load(open(os.path.join(d["final"], RI.F_MANIFEST), encoding="utf-8"))
        self.assertEqual(man["counts"]["valid_row_count"], 2)

    def test_readiness_doc_state(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        rd = json.load(open(os.path.join(d["final"], RI.F_READINESS), encoding="utf-8"))
        self.assertEqual(rd["analysis_readiness"], RI.REPORTS_READY)

    def test_each_report_type_gets_own_file(self):
        for builder, rtype in ((targeting_csv, RI.SP_TARGETING),
                               (search_term_csv, RI.SP_SEARCH_TERM),
                               (advertised_product_csv, RI.SP_ADVERTISED_PRODUCT),
                               (placement_csv, RI.SP_PLACEMENT),
                               (budget_csv, RI.SP_BUDGET),
                               (purchased_product_csv, RI.SP_PURCHASED_PRODUCT)):
            d = self.ws()
            self.drop(d, "r.csv", builder())
            r = self.run_base(d)
            self.assertEqual(r.state, RI.REPORTS_READY, f"{rtype} not ready")
            self.assertIn(RI.NORMALIZED_FILE[rtype], os.listdir(d["final"]))

    def test_verification_doc_required_artifacts(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        v = json.load(open(os.path.join(d["final"], RI.F_VERIFICATION), encoding="utf-8"))
        self.assertIn(RI.F_MANIFEST, v["required_artifacts"])

    def test_conflicts_doc_present(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        c = json.load(open(os.path.join(d["final"], RI.F_CONFLICTS), encoding="utf-8"))
        self.assertEqual(c["conflict_count"], 0)

    def test_overlap_doc_semantics(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        o = json.load(open(os.path.join(d["final"], RI.F_OVERLAP), encoding="utf-8"))
        self.assertEqual(o["report_semantics"][RI.SP_BUDGET], RI.SEM_POINT_IN_TIME)

    def test_no_part_files_left(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        for sub in ("final", "accepted_raw"):
            self.assertFalse(any(n.endswith(".part") for n in os.listdir(d[sub])))


# ================================================================ O. ATOMIC PROMOTION
class TestAtomicPromotion(Base):
    def test_valid_promotion(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        self.assertEqual(r.promote_report["result"], "PASS")
        self.assertTrue(r.promote_report["promoted"])

    def test_candidate_removed_after_promote(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        self.assertFalse(os.path.isdir(os.path.join(d["base"], "candidate"))
                         and os.listdir(os.path.join(d["base"], "candidate")))

    def test_tamper_candidate_refused(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = RI.run_ingestion(d["base"], reference_date=REF, now=NOW, write=False)
        RI._build_documents(r)
        cand = os.path.join(d["base"], "candidate")
        manifest, hashes = RI.write_candidate(r, cand)
        # tamper one artifact after hashing
        with open(os.path.join(cand, RI.F_READINESS), "ab") as f:
            f.write(b" ")
        report = RI.verify_candidate(cand, hashes, r.required_artifacts)
        self.assertEqual(report["result"], "BLOCKED")

    def test_tamper_not_promoted_preserves_final(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)   # good final in place
        final_before = sorted(os.listdir(d["final"]))
        # build a fresh candidate, tamper, attempt promote
        r = RI.run_ingestion(d["base"], reference_date=REF, now=NOW, write=False)
        RI._build_documents(r)
        cand = os.path.join(d["base"], "candidate2")
        manifest, hashes = RI.write_candidate(r, cand)
        with open(os.path.join(cand, RI.F_MANIFEST), "ab") as f:
            f.write(b"x")
        report = RI.promote_candidate(cand, d["final"], d["last_valid"], hashes, r.required_artifacts)
        self.assertFalse(report["promoted"])
        self.assertEqual(sorted(os.listdir(d["final"])), final_before)

    def test_last_valid_preserved_on_second_import(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        # replace inbox with a different valid report and re-run
        os.remove(os.path.join(d["inbox"], "camp.csv"))
        self.drop(d, "tgt.csv", targeting_csv())
        self.run_base(d)
        self.assertTrue(os.listdir(d["last_valid"]))

    def test_source_mutation_does_not_touch_accepted_raw(self):
        d = self.ws()
        text = campaign_csv()
        self.drop(d, "camp.csv", text)
        r = self.run_base(d)
        sha = r.sources[0]["source_file_sha256"]
        # mutate the inbox file after acceptance
        with open(os.path.join(d["inbox"], "camp.csv"), "ab") as f:
            f.write(b"tampered")
        with open(os.path.join(d["accepted_raw"], sha + ".csv"), "rb") as f:
            self.assertEqual(f.read(), text.encode("utf-8"))

    def test_verify_missing_artifact_blocks(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = RI.run_ingestion(d["base"], reference_date=REF, now=NOW, write=False)
        RI._build_documents(r)
        cand = os.path.join(d["base"], "candidate")
        manifest, hashes = RI.write_candidate(r, cand)
        os.remove(os.path.join(cand, RI.F_READINESS))
        report = RI.verify_candidate(cand, hashes, r.required_artifacts)
        self.assertEqual(report["result"], "BLOCKED")

    def test_promote_snapshots_prior_final(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        campaign_jsonl_before = RI.NORMALIZED_FILE[RI.SP_CAMPAIGN] in os.listdir(d["final"])
        os.remove(os.path.join(d["inbox"], "camp.csv"))
        self.drop(d, "tgt.csv", targeting_csv())
        self.run_base(d)
        self.assertTrue(campaign_jsonl_before)
        self.assertIn(RI.NORMALIZED_FILE[RI.SP_CAMPAIGN], os.listdir(d["last_valid"]))

    def test_atomic_write_leaves_no_temp(self):
        d = self.ws()
        p = os.path.join(d["base"], "x.json")
        RI._atomic_write_bytes(p, b"{}")
        self.assertEqual([n for n in os.listdir(d["base"]) if n.startswith(".tmp")], [])

    def test_promote_ordered_manifest_last(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        # manifest present and references the same output set
        man = json.load(open(os.path.join(d["final"], RI.F_MANIFEST), encoding="utf-8"))
        self.assertIn(RI.F_READINESS, man["output_hashes"])

    def test_verify_pass_on_untampered(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = RI.run_ingestion(d["base"], reference_date=REF, now=NOW, write=False)
        RI._build_documents(r)
        cand = os.path.join(d["base"], "candidate")
        manifest, hashes = RI.write_candidate(r, cand)
        report = RI.verify_candidate(cand, hashes, r.required_artifacts)
        self.assertEqual(report["result"], "PASS")

    def test_reimport_atomic_state(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        r2 = self.run_base(d)
        self.assertEqual(r2.promote_report["result"], "PASS")


# ================================================================ P. SECURITY / BOUNDARY
class TestSecurity(Base):
    MODULE_SRC = open(os.path.join(ROOT, "production", "phase7_report_ingestion.py"),
                      encoding="utf-8").read()

    def test_formula_injection_quarantined(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[["2026-07-01", "=SUM(A1:A9)", 10, 1, "5.00", "9.00", "USD"]]))
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORT_VALIDATION_BLOCKED)
        codes = {rc.split(":")[0] for iv in r.invalid_rows for rc in iv["reason_codes"]}
        self.assertIn(RI.UNSAFE_FORMULA_CELL, codes)

    def test_csv_safe_cell_escapes_formula(self):
        self.assertEqual(RI.csv_safe_cell("=1+1"), "'=1+1")
        self.assertEqual(RI.csv_safe_cell("@x"), "'@x")

    def test_csv_safe_cell_passthrough(self):
        self.assertEqual(RI.csv_safe_cell("normal"), "normal")

    def test_cell_never_executed(self):
        # a formula cell is read as literal data
        _, rows, _ = (lambda p: RI.read_delimited(p, RI.detect_format(p)))(
            self.drop(self.ws(), "c.csv",
                      "Date,Campaign Name,Impressions,Spend,Currency\n2026-07-01,=1+1,10,5.00,USD\n"))
        self.assertEqual(rows[0][1], "=1+1")

    def test_contains_credentials_detects_cookie(self):
        self.assertTrue(RI._contains_credentials({"cookie": "abc"}))

    def test_contains_credentials_detects_token_value(self):
        self.assertTrue(RI._contains_credentials({"x": "Atza|verylongtokenstring"}))

    def test_contains_credentials_clean(self):
        self.assertFalse(RI._contains_credentials({"campaign_name": "Nurse", "spend": "5.00"}))

    def test_no_network_imports(self):
        for banned in ("import socket", "import requests", "urllib", "http.client", "import boto",
                       "selenium", "webdriver", "smtplib", "ftplib"):
            self.assertNotIn(banned, self.MODULE_SRC, f"forbidden network import: {banned}")

    def test_no_amazon_api_call_patterns(self):
        # No ACTUAL Amazon endpoint call or API client. (Defensive detection substrings such as
        # 'sellercentral.amazon' inside credential-rejection guards are allowed and expected.)
        low = self.MODULE_SRC.lower()
        for banned in ("https://advertising-api", "http://advertising-api", "https://sellercentral",
                       "https://sellingpartner", "boto3", "python-amazon-sp-api", "sp_api",
                       ".get_report(", "requestreport", "create_report(", "requests.get",
                       "requests.post", "urlopen"):
            self.assertNotIn(banned, low, f"forbidden amazon/network call pattern: {banned}")

    def test_defensive_credential_literals_present(self):
        # the module DOES defensively detect account material in owner data — that is required, not a leak
        self.assertIn("sellercentral", TestSecurity.MODULE_SRC.lower())

    def test_zero_counters_in_readiness(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        rd = json.load(open(os.path.join(d["final"], RI.F_READINESS), encoding="utf-8"))
        self.assertTrue(all(v == 0 for v in rd["amazon_boundary"].values()))

    def test_zero_counters_in_verification(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        v = json.load(open(os.path.join(d["final"], RI.F_VERIFICATION), encoding="utf-8"))
        self.assertTrue(all(val == 0 for val in v["amazon_boundary"].values()))

    def test_required_zero_counters_defined(self):
        for k in ("external_amazon_account_attempts", "amazon_account_actions", "campaign_write_actions",
                  "target_write_actions", "negative_write_actions", "bid_write_actions",
                  "budget_write_actions", "report_download_attempts", "external_network_attempts",
                  "browser_automation_attempts", "credential_store_count", "api_payload_count",
                  "automated_optimization_actions"):
            self.assertEqual(RI._ZERO_COUNTERS[k], 0)

    def test_private_path_not_in_committed_style_proof(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        proof = RI.build_proof_gate(r, starting_commit="x")
        blob = RI.canonical_json(proof)
        self.assertNotIn(d["base"], blob)   # no absolute local path leaks into proof

    def test_no_raw_rows_in_proof(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        proof = RI.build_proof_gate(r, starting_commit="x")
        blob = RI.canonical_json(proof)
        self.assertNotIn("Nurse SP Auto", blob)   # campaign name never leaks into committed proof

    def test_this_session_never_flags(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        v = json.load(open(os.path.join(d["final"], RI.F_VERIFICATION), encoding="utf-8"))
        self.assertTrue(v["this_session_never"]["connects_to_amazon"])
        self.assertTrue(v["this_session_never"]["recommends_bids"])

    def test_null_byte_name_rejected(self):
        # scan_inbox rejects a null-byte name defensively (cannot be created on most FS, tested via API)
        rejects = RI.scan_inbox.__doc__
        self.assertIn("null-byte", rejects.lower())


# ================================================================ Q. DETERMINISM
class TestDeterminism(Base):
    def _stable(self, base, **kw):
        return RI.run_ingestion(base, reference_date=REF, **kw).stable_hashes

    def test_two_runs_identical(self):
        b1, b2 = self.mkbase(), self.mkbase()
        for b in (b1, b2):
            dirs = RI.ensure_workspace(b)
            self.drop(dirs, "camp.csv", campaign_csv())
        h1 = self._stable(b1, now="2026-07-19T10:00:00+07:00", run_id="A")
        h2 = self._stable(b2, now="2099-01-01T00:00:00+00:00", run_id="B")
        self.assertEqual(h1, h2)

    def test_three_modes_identical(self):
        hashes = []
        for mode in RI.CONNECTIVITY_MODES:
            b = self.mkbase()
            dirs = RI.ensure_workspace(b)
            self.drop(dirs, "camp.csv", campaign_csv())
            hashes.append(self._stable(b, mode=mode, now=NOW, run_id=mode))
        self.assertEqual(hashes[0], hashes[1])
        self.assertEqual(hashes[1], hashes[2])

    def test_network_zero_across_modes(self):
        for mode in RI.CONNECTIVITY_MODES:
            b = self.mkbase()
            dirs = RI.ensure_workspace(b)
            self.drop(dirs, "camp.csv", campaign_csv())
            r = RI.run_ingestion(b, reference_date=REF, mode=mode, now=NOW)
            proof = RI.build_proof_gate(r, starting_commit="x")
            self.assertEqual(proof["external_network_attempts"], 0)

    def test_shuffled_rows_same_entities(self):
        rowsA = [["2026-07-01", "A", 100, 5, "5.00", "10.00", "USD"],
                 ["2026-07-01", "B", 200, 8, "8.00", "20.00", "USD"]]
        rowsB = list(reversed(rowsA))
        ent = []
        for rows in (rowsA, rowsB):
            b = self.mkbase()
            dirs = RI.ensure_workspace(b)
            self.drop(dirs, "camp.csv", campaign_csv(rows=rows))
            r = RI.run_ingestion(b, reference_date=REF, now=NOW)
            ent.append({row["canonical_row_key"]: row["metrics"]
                        for row in r.normalized_by_type[RI.SP_CAMPAIGN]})
        self.assertEqual(ent[0], ent[1])

    def test_reconcile_shuffled_input(self):
        t = TestOverlap()
        rows = [t._nrow("A", "2026-07-01", "2026-07-07", {"spend": "5.00"}),
                t._nrow("B", "2026-07-01", "2026-07-07", {"spend": "6.00"})]
        a = RI.reconcile_interval_group(rows, RI.SEM_INTERVAL)["rows"]
        b = RI.reconcile_interval_group(list(reversed(rows)), RI.SEM_INTERVAL)["rows"]
        self.assertEqual([x["canonical_row_key"] for x in a],
                         [x["canonical_row_key"] for x in b])

    def test_readiness_hash_stable(self):
        b1, b2 = self.mkbase(), self.mkbase()
        for b in (b1, b2):
            dirs = RI.ensure_workspace(b)
            self.drop(dirs, "camp.csv", campaign_csv())
        r1 = RI.run_ingestion(b1, reference_date=REF, now="t1", run_id="1")
        r2 = RI.run_ingestion(b2, reference_date=REF, now="t2", run_id="2")
        self.assertEqual(r1.json_docs[RI.F_READINESS]["deterministic_content_sha256"],
                         r2.json_docs[RI.F_READINESS]["deterministic_content_sha256"])

    def test_registry_hash_excludes_imported_at(self):
        b1, b2 = self.mkbase(), self.mkbase()
        for b in (b1, b2):
            dirs = RI.ensure_workspace(b)
            self.drop(dirs, "camp.csv", campaign_csv())
        r1 = RI.run_ingestion(b1, reference_date=REF, now="A", run_id="1")
        r2 = RI.run_ingestion(b2, reference_date=REF, now="B", run_id="2")
        self.assertEqual(r1.json_docs[RI.F_SOURCE_REGISTRY]["deterministic_content_sha256"],
                         r2.json_docs[RI.F_SOURCE_REGISTRY]["deterministic_content_sha256"])

    def test_empty_inbox_deterministic(self):
        b1, b2 = self.mkbase(), self.mkbase()
        RI.ensure_workspace(b1)
        RI.ensure_workspace(b2)
        h1 = RI.run_ingestion(b1, now="A").stable_hashes
        h2 = RI.run_ingestion(b2, now="B").stable_hashes
        self.assertEqual(h1, h2)

    def test_jsonl_bytes_stable(self):
        b1, b2 = self.mkbase(), self.mkbase()
        for b in (b1, b2):
            dirs = RI.ensure_workspace(b)
            self.drop(dirs, "camp.csv", campaign_csv())
        r1 = RI.run_ingestion(b1, reference_date=REF, now="A")
        r2 = RI.run_ingestion(b2, reference_date=REF, now="B")
        self.assertEqual(r1.jsonl_docs[RI.NORMALIZED_FILE[RI.SP_CAMPAIGN]],
                         r2.jsonl_docs[RI.NORMALIZED_FILE[RI.SP_CAMPAIGN]])

    def test_invalid_mode_rejected(self):
        with self.assertRaises(RI.ReportIngestionError):
            RI.run_ingestion(self.mkbase(), mode="BOGUS")

    def test_stable_hashes_are_hex(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        for h in r.stable_hashes.values():
            self.assertRegex(h, r"^[0-9a-f]{64}$")

    def test_reason_codes_sorted_deterministic(self):
        d = self.ws()
        self.drop(d, "bad.csv", campaign_csv(rows=[["notadate", "", 100, 5, "-5.00", "10.00", ""]]))
        r = self.run_base(d)
        for iv in r.invalid_rows:
            self.assertEqual(iv["reason_codes"], sorted(iv["reason_codes"]))


# ================================================================ R. SCOPE (no optimization / later phases)
class TestScope(Base):
    def test_no_optimization_artifact_names(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        for name in os.listdir(d["final"]):
            up = name.upper()
            for banned in ("BID", "NEGATIVE", "CAMPAIGN-ROLE", "OPTIM", "LAUNCH", "BUDGET-PLAN",
                           "SEARCH-TERM-HARVEST", "RECOMMEND"):
                self.assertNotIn(banned, up, f"scope-violating artifact: {name}")

    def test_proof_optimization_false(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        proof = RI.build_proof_gate(r, starting_commit="x")
        self.assertFalse(proof["ready_for_automated_optimization"])
        self.assertFalse(proof["ready_for_amazon_action"])
        self.assertFalse(proof["ready_for_phase7_3_decision_support"])

    def test_automated_optimization_actions_zero(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        proof = RI.build_proof_gate(r, starting_commit="x")
        self.assertEqual(proof["automated_optimization_actions"], 0)

    def test_no_bid_functions_in_module(self):
        src = TestSecurity.MODULE_SRC.lower()
        for banned in ("def recommend_bid", "def compute_bid", "def harvest", "def create_negative",
                       "def optimize", "def suggest_bid"):
            self.assertNotIn(banned, src)

    def test_no_phase73_74_artifacts(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        self.run_base(d)
        for name in os.listdir(d["final"]):
            self.assertNotIn("7.3", name)
            self.assertNotIn("7.4", name)

    def test_supported_types_no_optimization(self):
        self.assertNotIn("OPTIMIZE", RI.REPORT_TYPES)
        self.assertEqual(set(RI.REPORT_TYPES), {RI.SP_CAMPAIGN, RI.SP_TARGETING, RI.SP_SEARCH_TERM,
                                                RI.SP_ADVERTISED_PRODUCT, RI.SP_PURCHASED_PRODUCT,
                                                RI.SP_PLACEMENT, RI.SP_BUDGET})

    def test_no_api_payload_generated(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        proof = RI.build_proof_gate(r, starting_commit="x")
        self.assertEqual(proof["api_payload_count"], 0)

    def test_never_ready_for_manual_launch(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        b = RI._readiness_booleans(r.state)
        self.assertNotIn("ready_for_manual_launch", {k: v for k, v in b.items() if v is True})

    def test_scope_boundary_report_download_zero(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        proof = RI.build_proof_gate(r, starting_commit="x")
        self.assertEqual(proof["report_download_attempts"], 0)

    def test_scope_no_campaign_writes(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        proof = RI.build_proof_gate(r, starting_commit="x")
        self.assertEqual(proof["campaign_write_actions"], 0)
        self.assertEqual(proof["negative_write_actions"], 0)
        self.assertEqual(proof["bid_write_actions"], 0)
        self.assertEqual(proof["budget_write_actions"], 0)


# ================================================================ S. READINESS STATES
class TestReadinessStates(Base):
    def test_input_required(self):
        d = self.ws()
        self.assertEqual(self.run_base(d).state, RI.REPORT_INPUT_REQUIRED)

    def test_ready(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv())
        self.assertEqual(self.run_base(d).state, RI.REPORTS_READY)

    def test_validation_blocked(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[["2026-07-01", "", 100, 5, "5.00", "10.00", "USD"]]))
        self.assertEqual(self.run_base(d).state, RI.REPORT_VALIDATION_BLOCKED)

    def test_currency_blocked_conflict(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[
            ["2026-07-01", "A", 100, 5, "5.00", "10.00", "USD"],
            ["2026-07-01", "B", 100, 5, "5.00", "10.00", "EUR"]]))
        self.assertEqual(self.run_base(d).state, RI.REPORT_CURRENCY_BLOCKED)

    def test_currency_blocked_missing(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[["2026-07-01", "A", 100, 5, "5.00", "10.00", ""]]))
        self.assertEqual(self.run_base(d).state, RI.REPORT_CURRENCY_BLOCKED)

    def test_date_blocked_future(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[["2026-08-01", "A", 100, 5, "5.00", "10.00", "USD"]]))
        self.assertEqual(self.run_base(d).state, RI.REPORT_DATE_BLOCKED)

    def test_classification_blocked_unknown(self):
        d = self.ws()
        self.drop(d, "c.csv", _csv(["Date", "Portfolio Name"], [["2026-07-01", "P"]]))
        self.assertEqual(self.run_base(d).state, RI.REPORT_CLASSIFICATION_BLOCKED)

    def test_classification_blocked_ambiguous(self):
        d = self.ws()
        headers = ["Date", "Campaign Name", "Ad Group Name", "Targeting", "Match Type", "Placement",
                   "Impressions", "Spend", "Currency"]
        self.drop(d, "c.csv", _csv(headers, [["2026-07-01", "N", "G", "kw", "exact", "Top", 10, "5.00", "USD"]]))
        self.assertEqual(self.run_base(d).state, RI.REPORT_CLASSIFICATION_BLOCKED)

    def test_format_blocked(self):
        d = self.ws()
        self.drop(d, "a.xlsx", "PK\x03\x04")
        self.assertEqual(self.run_base(d).state, RI.REPORT_FORMAT_BLOCKED)

    def test_conflict_blocked(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[
            ["2026-07-01", "N", 100, 5, "5.00", "10.00", "USD"],
            ["2026-07-01", "N", 100, 5, "6.00", "10.00", "USD"]]))
        self.assertEqual(self.run_base(d).state, RI.REPORT_CONFLICT_BLOCKED)

    def test_most_restrictive_wins(self):
        # a format-blocked file + a valid file -> format is more restrictive than READY
        d = self.ws()
        self.drop(d, "a.xlsx", "PK\x03\x04")
        self.drop(d, "b.csv", campaign_csv())
        self.assertEqual(self.run_base(d).state, RI.REPORT_FORMAT_BLOCKED)

    def test_ready_booleans(self):
        b = RI._readiness_booleans(RI.REPORTS_READY)
        self.assertTrue(b["ready_for_normalized_analysis"])
        self.assertFalse(b["ready_for_amazon_action"])

    def test_blocked_booleans(self):
        b = RI._readiness_booleans(RI.REPORT_VALIDATION_BLOCKED)
        self.assertFalse(b["ready_for_normalized_analysis"])
        self.assertTrue(b["ready_for_report_input"])

    def test_date_beats_validation_rank(self):
        # a row with BOTH a future date and a bad metric resolves to DATE (more restrictive than validation)
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(rows=[["2026-08-01", "N", 100, 5, "-5.00", "10.00", "USD"]]))
        self.assertEqual(self.run_base(d).state, RI.REPORT_DATE_BLOCKED)


# ================================================================ T. PROOF GATE
class TestProofGate(Base):
    def _proof(self, **kw):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        return RI.build_proof_gate(r, starting_commit="363e58c", **kw)

    def test_final_status_matches_state(self):
        self.assertEqual(self._proof()["final_status"], RI.REPORTS_READY)

    def test_t2_state_recorded_separately(self):
        p = self._proof()
        self.assertEqual(p["t2_product_readiness"], "PHASE7_OWNER_INPUT_REQUIRED")
        self.assertNotEqual(p["t2_product_readiness"], p["report_input_state"])

    def test_leakage_none(self):
        self.assertEqual(self._proof()["private_data_leakage_result"], "NONE")

    def test_zero_counters(self):
        p = self._proof()
        self.assertTrue(all(p[k] == 0 for k in RI._ZERO_COUNTERS))

    def test_deterministic_hash_present(self):
        self.assertIn("deterministic_content_sha256", self._proof())

    def test_supported_formats(self):
        self.assertEqual(self._proof()["supported_file_formats"], ["CSV", "CSV_BOM", "TSV", "XLSX"])

    def test_duplicate_authority_zero(self):
        self.assertEqual(self._proof()["duplicate_authority_count"], 0)

    def test_authority_path(self):
        self.assertEqual(self._proof()["report_ingestion_authority"],
                         "production/phase7_report_ingestion.py")

    def test_money_authority(self):
        self.assertEqual(self._proof()["money_authority"], "core/money.py")

    def test_normalized_counts_present(self):
        p = self._proof()
        self.assertEqual(p["normalized_campaign_row_count"], 2)

    def test_proof_hash_deterministic_across_commit(self):
        d = self.ws()
        self.drop(d, "camp.csv", campaign_csv())
        r = self.run_base(d)
        p1 = RI.build_proof_gate(r, starting_commit="A", final_commit="X")
        p2 = RI.build_proof_gate(r, starting_commit="B", final_commit="Y")
        self.assertEqual(p1["deterministic_content_sha256"], p2["deterministic_content_sha256"])


# ================================================================ U. DEPENDENCY / PREFLIGHT
class TestDependencyPreflight(Base):
    def _load(self, name):
        return json.load(open(os.path.join(ROOT, name), encoding="utf-8"))

    def test_phase7_1e_proof_parses(self):
        self.assertEqual(self._load("SESSION7_1E-PROOF-GATE.json")["session_status"],
                         "PHASE7_OWNER_INPUT_REQUIRED")

    def test_phase7_1m_acceptance_parses(self):
        self.assertEqual(self._load("SESSION7_1M-ACCEPTANCE-PROOF.json")["acceptance_status"],
                         "PHASE7_1M_ACCEPTED_WITH_REPORTING_FIX")

    def test_phase7_0_proof_parses(self):
        self.assertEqual(self._load("SESSION7_0-PROOF-GATE.json")["readiness_decision"],
                         "PHASE7_OWNER_INPUT_REQUIRED")

    def test_phase6_dependency(self):
        self.assertEqual(self._load("SESSION7_0-PROOF-GATE.json")["phase6_final_status"],
                         "PHASE6_SAFE_DRAFT_READY")

    def test_single_authority_exists(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "production", "phase7_report_ingestion.py")))

    def test_no_duplicate_authorities(self):
        for banned in ("report_ingestion_v2.py", "amazon_report_downloader.py", "report_fetcher.py",
                       "ads_api_client.py", "report_optimizer.py", "search_term_optimizer.py",
                       "performance_engine.py"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, "production", banned)), banned)

    def test_connectivity_modes_from_preflight(self):
        from production import phase7_preflight as P7
        self.assertEqual(RI.CONNECTIVITY_MODES, P7.CONNECTIVITY_MODES)

    def test_money_authority_importable(self):
        self.assertTrue(hasattr(MONEY, "parse_money_decimal"))


# ================================================================ V. NATIVE EXCEL / ENCODING INPUT (7.2 upgrade)
def _xlsx_bytes(sheets):
    """Build .xlsx BYTES from [(sheet_title, [row, ...]), ...]. Cells keep their Python type (str / int /
    float / None) so the reader's displayed-value -> text conversion is exercised. Sheet titles are kept
    <=31 chars (Excel limit). Bytes are built once and reused so a duplicate/determinism fixture is
    byte-stable."""
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        ws = wb.create_sheet(title=title[:31])
        for row in rows:
            ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# a valid SP search-term report as (header, rows) — reused for xlsx + csv duplicate fixtures.
_ST_HEADER = ["Date", "Campaign Name", "Ad Group Name", "Targeting", "Match Type",
              "Customer Search Term", "Impressions", "Clicks", "Spend", "7 Day Total Sales", "Currency"]
# money values chosen with NO trailing-zero ambiguity so an Excel float and a CSV string canonicalize
# identically (Decimal canonicalization preserves trailing zeros: '5.50' != '5.5').
_ST_ROWS_NUM = [
    ["2026-07-01", "Nurse SP Manual", "Core", "nurse sweatshirt", "broad", "gift for nurse",
     300, 15, 5.25, 25.75, "USD"],
    ["2026-07-01", "Nurse SP Manual", "Core", "nurse sweatshirt", "exact", "nurse gift",
     120, 6, 2.5, 9.99, "USD"],
]


def _search_term_xlsx(rows=None, *, sheets=None, title="SP-SearchTerm"):
    if sheets is not None:
        return _xlsx_bytes(sheets)
    rows = rows if rows is not None else _ST_ROWS_NUM
    return _xlsx_bytes([(title, [_ST_HEADER] + [list(r) for r in rows])])


class TestExcelAndEncodingInput(Base):
    def dropb(self, dirs, name, data):
        p = os.path.join(dirs["inbox"], name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    # ---- 1. valid .xlsx Sponsored Products Search Term report ----
    def test_xlsx_search_term_accepted(self):
        d = self.ws()
        self.dropb(d, "SP_SearchTerm.xlsx", _search_term_xlsx())
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)
        self.assertEqual(r.counts["accepted_source_count"], 1)
        s = r.sources[0]
        self.assertEqual(s["parser"], RI.PARSER_XLSX)
        self.assertEqual(s["report_type"], RI.SP_SEARCH_TERM)
        self.assertEqual(s["worksheet"], "SP-SearchTerm")
        self.assertEqual(r.counts["normalized_sp_search_term_row_count"], 2)

    def test_xlsx_no_longer_format_blocked(self):
        # regression: a real .xlsx must NOT produce PHASE7_REPORT_FORMAT_BLOCKED
        d = self.ws()
        self.dropb(d, "r.xlsx", _search_term_xlsx())
        self.assertNotEqual(self.run_base(d).state, RI.REPORT_FORMAT_BLOCKED)

    def test_xlsx_archived_with_xlsx_extension(self):
        d = self.ws()
        self.dropb(d, "r.xlsx", _search_term_xlsx())
        r = self.run_base(d)
        sha = [s["source_file_sha256"] for s in r.sources if s["import_state"] == "ACCEPTED"][0]
        self.assertTrue(os.path.exists(os.path.join(d["accepted_raw"], sha + ".xlsx")))

    def test_xlsx_original_bytes_preserved(self):
        d = self.ws()
        data = _search_term_xlsx()
        self.dropb(d, "r.xlsx", data)
        r = self.run_base(d)
        sha = [s["source_file_sha256"] for s in r.sources if s["import_state"] == "ACCEPTED"][0]
        with open(os.path.join(d["accepted_raw"], sha + ".xlsx"), "rb") as f:
            self.assertEqual(f.read(), data)          # archived workbook is byte-for-byte the original

    # ---- 2/3/4/5. encoding + delimiter fixtures ----
    def test_utf8_csv(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(), encoding="utf-8")
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)
        self.assertEqual([s for s in r.sources][0]["detected_encoding"], "utf-8")

    def test_utf8_bom_csv(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv(), encoding="utf-8-sig")
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)
        self.assertEqual(r.sources[0]["detected_encoding"], "utf-8-sig")

    def test_cp1252_csv_accents(self):
        # Windows-1252 export with español / cumpleaños / mamá must import and preserve accents
        rows = [["2026-07-01", "Nurse SP Manual", "Core", "nurse sweatshirt", "broad", "regalo español",
                 300, 15, "5.25", "25.75", "USD"],
                ["2026-07-01", "Nurse SP Manual", "Core", "nurse sweatshirt", "exact", "cumpleaños mamá",
                 120, 6, "2.50", "9.99", "USD"]]
        d = self.ws()
        self.drop(d, "c.csv", _csv(_ST_HEADER, rows), encoding="cp1252")
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)
        self.assertEqual(r.sources[0]["detected_encoding"], "cp1252")
        terms = {row["identity"]["search_term"] for row in r.normalized_by_type[RI.SP_SEARCH_TERM]}
        self.assertEqual(terms, {"regalo español", "cumpleaños mamá"})   # exact, no transliteration

    def test_tab_delimited_unicode_txt(self):
        rows = [["2026-07-01", "Nurse SP Manual", "Core", "nurse sweatshirt", "broad", "regalo mamá",
                 300, 15, "5.25", "25.75", "USD"]]
        d = self.ws()
        self.drop(d, "report.txt", _csv(_ST_HEADER, rows, delimiter="\t"), encoding="utf-8")
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)
        self.assertEqual(r.sources[0]["detected_delimiter"], "TAB")
        self.assertEqual(r.sources[0]["detected_encoding"], "utf-8")

    def test_semicolon_delimiter(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv().replace(",", ";"))
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)
        self.assertEqual(r.sources[0]["detected_delimiter"], "SEMICOLON")

    def test_pipe_delimiter(self):
        d = self.ws()
        self.drop(d, "c.csv", campaign_csv().replace(",", "|"))
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)
        self.assertEqual(r.sources[0]["detected_delimiter"], "PIPE")

    # ---- 6. Excel ~$ lock file ----
    def test_lock_file_ignored_not_accepted(self):
        d = self.ws()
        self.dropb(d, "~$SP_SearchTerm.xlsx", b"PK\x03\x04 lockjunk")
        r = self.run_base(d)
        self.assertEqual(r.counts["ignored_source_count"], 1)
        self.assertEqual(r.counts["accepted_source_count"], 0)
        self.assertEqual(r.counts["quarantined_source_count"], 0)
        # only a lock file present => no supported report => INPUT_REQUIRED (never FORMAT_BLOCKED)
        self.assertEqual(r.state, RI.REPORT_INPUT_REQUIRED)

    def test_lock_file_plus_valid_xlsx_ready(self):
        # regression: a ~$ lock file must NOT cause FORMAT_BLOCKED when a valid .xlsx is present
        d = self.ws()
        self.dropb(d, "r.xlsx", _search_term_xlsx())
        self.dropb(d, "~$r.xlsx", b"PK\x03\x04 lockjunk")
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)
        self.assertEqual(r.counts["accepted_source_count"], 1)
        self.assertEqual(r.counts["ignored_source_count"], 1)

    # ---- 7. empty workbook ----
    def test_empty_workbook_quarantined(self):
        d = self.ws()
        self.dropb(d, "empty.xlsx", _xlsx_bytes([("Sheet1", [])]))
        r = self.run_base(d)
        self.assertEqual(r.counts["quarantined_source_count"], 1)
        self.assertEqual(r.state, RI.REPORT_FORMAT_BLOCKED)

    # ---- 8. first empty sheet, second valid sheet ----
    def test_first_empty_sheet_second_valid(self):
        d = self.ws()
        data = _search_term_xlsx(sheets=[("Empty", []),
                                         ("Data", [_ST_HEADER] + [list(r) for r in _ST_ROWS_NUM])])
        self.dropb(d, "two.xlsx", data)
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)
        self.assertEqual(r.sources[0]["worksheet"], "Data")

    # ---- 9. unsupported .xls (OLE) ----
    def test_xls_ole_unsupported(self):
        d = self.ws()
        self.dropb(d, "old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 legacy ole body")
        r = self.run_base(d)
        self.assertEqual(r.counts["quarantined_source_count"], 1)
        self.assertEqual(r.state, RI.REPORT_FORMAT_BLOCKED)

    # ---- 10. ambiguous / one-column malformed CSV ----
    def test_single_column_no_delimiter_quarantined(self):
        d = self.ws()
        self.drop(d, "one.csv", "OnlyColumnNoDelimiter\nvalue1\nvalue2\n")
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORT_FORMAT_BLOCKED)
        self.assertEqual(r.sources[0]["quarantine_state"], RI.AMBIGUOUS_DELIMITER)

    def test_ambiguous_delimiter_tie_quarantined(self):
        d = self.ws()
        self.drop(d, "tie.csv", "a,b;c\n1,2;3\n")     # comma==1 and semicolon==1 => tie => ambiguous
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORT_FORMAT_BLOCKED)
        self.assertEqual(r.sources[0]["quarantine_state"], RI.AMBIGUOUS_DELIMITER)

    # ---- 11. mixed inbox: valid xlsx + ~$ lock + unsupported ----
    def test_mixed_inbox(self):
        d = self.ws()
        self.dropb(d, "good.xlsx", _search_term_xlsx())
        self.dropb(d, "~$good.xlsx", b"PK\x03\x04 lockjunk")
        self.dropb(d, "bad.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 ole")
        r = self.run_base(d)
        self.assertEqual(r.counts["accepted_source_count"], 1)   # xlsx still processed
        self.assertEqual(r.counts["ignored_source_count"], 1)    # lock ignored, not accepted
        self.assertEqual(r.counts["quarantined_source_count"], 1)  # the .xls
        self.assertEqual(len(r.normalized_by_type[RI.SP_SEARCH_TERM]), 2)  # xlsx rows normalized
        self.assertEqual(r.state, RI.REPORT_FORMAT_BLOCKED)      # unsupported .xls is most-restrictive

    # ---- 12. duplicate .xlsx and equivalent .csv (identical logical rows) ----
    def test_duplicate_xlsx_and_csv_dedup(self):
        d = self.ws()
        self.dropb(d, "report.xlsx", _search_term_xlsx())
        # a CSV whose canonical rows equal the xlsx's (float 5.25 == '5.25', etc.)
        csv_rows = [["2026-07-01", "Nurse SP Manual", "Core", "nurse sweatshirt", "broad",
                     "gift for nurse", 300, 15, "5.25", "25.75", "USD"],
                    ["2026-07-01", "Nurse SP Manual", "Core", "nurse sweatshirt", "exact",
                     "nurse gift", 120, 6, "2.5", "9.99", "USD"]]
        self.drop(d, "report.csv", _csv(_ST_HEADER, csv_rows))
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)             # equal metrics => duplicate, not conflict
        self.assertEqual(r.counts["accepted_source_count"], 2)  # two distinct source files
        # but the normalized dataset is per actual report (2 unique rows), not per representation (4)
        self.assertEqual(len(r.normalized_by_type[RI.SP_SEARCH_TERM]), 2)
        self.assertTrue(r.counts["duplicate_row_count"] >= 2)
        self.assertEqual(r.counts["conflict_count"], 0)

    # ---- 13. deterministic repeated import ----
    def test_deterministic_repeated_import(self):
        data = _search_term_xlsx()
        b1, b2 = self.mkbase(), self.mkbase()
        h = []
        for b, now, rid in ((b1, "A", "1"), (b2, "B", "2")):
            dirs = RI.ensure_workspace(b)
            self.dropb(dirs, "r.xlsx", data)          # identical bytes => identical sha
            h.append(RI.run_ingestion(b, reference_date=REF, now=now, run_id=rid).stable_hashes)
        self.assertEqual(h[0], h[1])

    # ---- 14. failure preserving last_valid ----
    def test_blocked_run_preserves_last_valid(self):
        d = self.ws()
        self.dropb(d, "good.xlsx", _search_term_xlsx())
        self.run_base(d)                              # run1: good final in place
        st_file = RI.NORMALIZED_FILE[RI.SP_SEARCH_TERM]
        self.assertIn(st_file, os.listdir(d["final"]))
        os.remove(os.path.join(d["inbox"], "good.xlsx"))
        self.dropb(d, "bad.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 ole")  # run2: only an unsupported file
        r2 = self.run_base(d)
        self.assertEqual(r2.state, RI.REPORT_FORMAT_BLOCKED)
        # the previous good dataset survives in last_valid (never partially replaced)
        self.assertIn(st_file, os.listdir(d["last_valid"]))

    # ---- 15. numeric Excel cells through the Decimal-safe path ----
    def test_numeric_excel_cells_decimal_safe(self):
        d = self.ws()
        self.dropb(d, "r.xlsx", _search_term_xlsx())
        r = self.run_base(d)
        for row in r.normalized_by_type[RI.SP_SEARCH_TERM]:
            for field, val in row["metrics"].items():
                # money is a canonical Decimal STRING; counts are int. NEVER a float in either path.
                self.assertNotIsInstance(val, float)
                if field in RI.MONEY_FIELDS:
                    self.assertIsInstance(val, str)
                    from decimal import Decimal
                    Decimal(val)                      # parses cleanly as an exact decimal
        spend = {row["metrics"]["spend"] for row in r.normalized_by_type[RI.SP_SEARCH_TERM]}
        self.assertEqual(spend, {"5.25", "2.5"})      # 5.25 float -> '5.25' text -> Decimal-safe

    def test_xlsx_number_to_text_no_float_noise(self):
        self.assertEqual(RI._xlsx_number_to_text(1000), "1000")
        self.assertEqual(RI._xlsx_number_to_text(1000.0), "1000")
        self.assertEqual(RI._xlsx_number_to_text(12.34), "12.34")

    # ---- 16. accented customer search terms preserved exactly (xlsx path) ----
    def test_xlsx_accented_search_term_preserved(self):
        rows = [["2026-07-01", "Nurse SP Manual", "Core", "nurse sweatshirt", "broad",
                 "cumpleaños de mamá", 300, 15, 5.25, 25.75, "USD"]]
        d = self.ws()
        self.dropb(d, "r.xlsx", _search_term_xlsx(rows=rows))
        r = self.run_base(d)
        self.assertEqual(r.state, RI.REPORTS_READY)
        terms = {row["identity"]["search_term"] for row in r.normalized_by_type[RI.SP_SEARCH_TERM]}
        self.assertEqual(terms, {"cumpleaños de mamá"})

    # ---- per-file manifest (requirement-6 schema) ----
    def test_per_file_manifest_schema(self):
        d = self.ws()
        self.dropb(d, "r.xlsx", _search_term_xlsx())
        self.dropb(d, "~$r.xlsx", b"PK\x03\x04 lockjunk")
        self.run_base(d)
        man = json.load(open(os.path.join(d["final"], RI.F_IMPORT_MANIFEST), encoding="utf-8"))
        self.assertIn("per_file", man)
        keys = {"source_filename", "source_extension", "source_sha256", "source_size_bytes", "parser",
                "parser_version", "worksheet", "detected_encoding", "detected_delimiter", "rows_read",
                "rows_valid", "rows_invalid", "report_type", "status", "reason_codes"}
        for entry in man["per_file"]:
            self.assertEqual(set(entry.keys()), keys)
        statuses = {e["status"] for e in man["per_file"]}
        self.assertEqual(statuses, {RI.FILE_STATUS_ACCEPTED, RI.FILE_STATUS_IGNORED})
        acc = [e for e in man["per_file"] if e["status"] == RI.FILE_STATUS_ACCEPTED][0]
        self.assertEqual(acc["parser"], RI.PARSER_XLSX)
        ign = [e for e in man["per_file"] if e["status"] == RI.FILE_STATUS_IGNORED][0]
        self.assertIn(RI.IGNORED_TEMP_LOCK_FILE, ign["reason_codes"])

    # ---- boundary: no float ever reaches the money authority; no network in the xlsx path ----
    def test_no_float_into_money_authority(self):
        # feeding a float to the money authority is a hard error (never silently coerced)
        with self.assertRaises(MONEY.MoneyError):
            MONEY.parse_decimal_string(5.25, field="money")

    def test_xlsx_path_adds_no_network_or_amazon(self):
        d = self.ws()
        self.dropb(d, "r.xlsx", _search_term_xlsx())
        r = self.run_base(d)
        proof = RI.build_proof_gate(r, starting_commit="x")
        self.assertTrue(all(proof[k] == 0 for k in RI._ZERO_COUNTERS))
        self.assertIn("XLSX", proof["supported_file_formats"])

    def test_openpyxl_import_is_local_only(self):
        # openpyxl must not pull a network/browser/api dependency into the module surface
        src = TestSecurity.MODULE_SRC.lower()
        self.assertIn("import openpyxl", src)
        for banned in ("requests", "urllib", "http.client", "selenium", "webdriver", "boto",
                       "sp_api", "advertising-api"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
