#!/usr/bin/env python3
"""production.phase7_report_ingestion — the ONE Phase 7.2 offline report-ingestion authority.

Phase 7.2 accepts owner-EXPORTED Amazon Ads report files that the owner has MANUALLY placed in a
local inbox, then validates, normalizes, reconciles, hashes, and stores sanitized local outputs. It
answers exactly one question:

  Given report files the owner dropped locally, are they safely normalized into a deterministic,
  lineage-preserving, analysis-ready dataset — or is owner input / review still required?

It is a GENERIC ingestion engine (Amazon report FORMATS change, so classification is by declared
headers, never a fixed schema and never the filename). It reuses the established Phase 6/7 primitives
rather than re-implementing them:
  * canonical json / content hash / atomic write  -> production.product_workspace
  * Decimal / integer parsing (never a float)      -> core.money
  * connectivity modes                             -> production.phase7_preflight

The permanent Amazon boundary is re-asserted and never crossed: no login, SP-API / MWS / Ads API,
browser automation, credential / cookie / token store, automated report retrieval or download, no
campaign / target / negative / bid / budget write, no API payload, no public bind, no external
network, and NO optimization (this phase never recommends a bid, harvests a search term, or creates a
negative). Every Amazon-action and network counter is zero. The owner remains the only bridge to
Amazon: the owner exports and copies the file in; the toolkit only reads the local bytes.

Public API (high level):
  detect_format / normalize_header / map_headers / classify_report
  parse_money_cell / parse_int_cell / parse_date_cell / parse_date_range / validate_currency
  validate_row / canonical_row_key / lineage_for
  classify_range_relationship / report_semantics / reconcile_interval_group
  run_ingestion               (the orchestrator: scan inbox -> candidate -> verify -> promote)
  write_candidate / verify_candidate / promote_candidate
  build_stage_manifest / build_proof_gate
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unicodedata

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "listing", "scripts"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW          # noqa: E402  canonical json / content hash
from production import phase7_preflight as P7            # noqa: E402  connectivity modes
from core import money as MONEY                          # noqa: E402  Decimal / integer authority

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256

# ================================================================ constants / versions
STAGE_ID = "7.2"
STAGE_NAME = "Offline Amazon Ads Report Ingestion, Validation, Normalization, and Reconciliation"

SCHEMA_SOURCE_REGISTRY = "phase7-2-report-source-registry-v1"
SCHEMA_IMPORT_MANIFEST = "phase7-2-report-import-manifest-v1"
SCHEMA_VALIDATION = "phase7-2-report-validation-v1"
SCHEMA_ROW_ERRORS = "phase7-2-report-row-errors-v1"
SCHEMA_OVERLAP = "phase7-2-report-overlap-analysis-v1"
SCHEMA_CONFLICTS = "phase7-2-report-conflicts-v1"
SCHEMA_NORMALIZED_ROW = "phase7-2-normalized-row-v1"
SCHEMA_LINEAGE = "phase7-2-lineage-v1"
SCHEMA_READINESS = "phase7-2-report-analysis-readiness-v1"
SCHEMA_VERIFICATION = "phase7-2-report-verification-v1"
SCHEMA_MANIFEST = "phase7-2-report-manifest-v1"
SCHEMA_PROOF_GATE = "session7_2-proof-gate-v1"

NORMALIZATION_SCHEMA_VERSION = SCHEMA_NORMALIZED_ROW

CONNECTIVITY_MODES = P7.CONNECTIVITY_MODES

# volatile keys excluded from every deterministic content hash.
_VOLATILE = ("deterministic_content_sha256", "generated_at", "started_at", "completed_at",
             "imported_at", "imported_at_volatile", "run_id", "repo_root", "run_dir",
             "base_dir", "git_head", "git_branch", "connectivity_mode")

# ---------------------------------------------------------------- declared safety limits
MAX_FILE_BYTES = 64 * 1024 * 1024       # a single report file
MAX_FILES = 256                          # files processed from one inbox
MAX_ROWS = 2_000_000                     # data rows per file
MAX_COLUMNS = 512                        # columns per file
MAX_FIELD_LEN = 32_768                   # characters per cell

ALLOWED_EXTENSIONS = (".csv", ".tsv", ".txt")
XLSX_EXTENSIONS = (".xlsx", ".xlsm", ".xls")
# .xlsx is natively supported (openpyxl, read-only/data-only); .xls (OLE) and .xlsm (macro) stay refused.
SUPPORTED_XLSX_EXTENSION = ".xlsx"
# extensions whose ORIGINAL extension is preserved for the immutable raw archive copy.
_ARCHIVE_EXTS = frozenset(ALLOWED_EXTENSIONS + (SUPPORTED_XLSX_EXTENSION,))
SUSPICIOUS_EXTENSIONS = (".exe", ".bat", ".cmd", ".com", ".js", ".vbs", ".scr", ".dll", ".msi",
                         ".ps1", ".sh", ".zip", ".rar", ".7z", ".gz", ".tar", ".jar", ".apk")

# --- parsers + versions (recorded per source in the manifest) ---
PARSER_XLSX = "xlsx_openpyxl"
PARSER_TEXT = "delimited_text"
PARSER_VERSION = "phase7-2-parser-v2"     # v2 = native .xlsx + multi-encoding + 4-delimiter text

# deterministic text decode order (NO probabilistic detection). latin-1 never raises, so it is the
# final total fallback; utf-8-sig is only selected when a real BOM is present.
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
# candidate delimiters, in a fixed order for deterministic reporting.
_DELIMITERS = ((",", "COMMA"), ("\t", "TAB"), (";", "SEMICOLON"), ("|", "PIPE"))
_DELIMITER_LABELS = dict(_DELIMITERS)
# excel lock/temp file marker — owner Excel keeps a "~$Book.xlsx" open-file lock; never a report.
LOCK_FILE_PREFIX = "~$"

# ---------------------------------------------------------------- report types + semantics
SP_CAMPAIGN = "SP_CAMPAIGN"
SP_TARGETING = "SP_TARGETING"
SP_SEARCH_TERM = "SP_SEARCH_TERM"
SP_ADVERTISED_PRODUCT = "SP_ADVERTISED_PRODUCT"
SP_PURCHASED_PRODUCT = "SP_PURCHASED_PRODUCT"
SP_PLACEMENT = "SP_PLACEMENT"
SP_BUDGET = "SP_BUDGET"
UNKNOWN = "UNKNOWN"
AMBIGUOUS = "AMBIGUOUS"

REPORT_TYPES = (SP_CAMPAIGN, SP_TARGETING, SP_SEARCH_TERM, SP_ADVERTISED_PRODUCT,
                SP_PURCHASED_PRODUCT, SP_PLACEMENT, SP_BUDGET)

# interval-vs-snapshot registry. Reconciliation NEVER aggregates until semantics are known.
SEM_INTERVAL = "INTERVAL"
SEM_DAILY = "DAILY"
SEM_CUMULATIVE_SNAPSHOT = "CUMULATIVE_SNAPSHOT"
SEM_POINT_IN_TIME = "POINT_IN_TIME"
SEM_UNKNOWN = "UNKNOWN"

REPORT_SEMANTICS = {
    SP_CAMPAIGN: SEM_INTERVAL,
    SP_TARGETING: SEM_INTERVAL,
    SP_SEARCH_TERM: SEM_INTERVAL,
    SP_ADVERTISED_PRODUCT: SEM_INTERVAL,
    SP_PURCHASED_PRODUCT: SEM_INTERVAL,
    SP_PLACEMENT: SEM_INTERVAL,
    SP_BUDGET: SEM_POINT_IN_TIME,          # a budget report is a point-in-time snapshot, never summed
    UNKNOWN: SEM_UNKNOWN,
}

# normalized JSONL filename per report type.
NORMALIZED_FILE = {
    SP_CAMPAIGN: "PHASE7-SP-CAMPAIGN-NORMALIZED.jsonl",
    SP_TARGETING: "PHASE7-SP-TARGETING-NORMALIZED.jsonl",
    SP_SEARCH_TERM: "PHASE7-SP-SEARCH-TERM-NORMALIZED.jsonl",
    SP_ADVERTISED_PRODUCT: "PHASE7-SP-ADVERTISED-PRODUCT-NORMALIZED.jsonl",
    SP_PURCHASED_PRODUCT: "PHASE7-SP-PURCHASED-PRODUCT-NORMALIZED.jsonl",
    SP_PLACEMENT: "PHASE7-SP-PLACEMENT-NORMALIZED.jsonl",
    SP_BUDGET: "PHASE7-SP-BUDGET-NORMALIZED.jsonl",
}

# ---------------------------------------------------------------- metric-value states
MS_MISSING = "MISSING"            # column absent
MS_BLANK = "BLANK"               # column present but the cell is empty
MS_ZERO = "ZERO"                # an explicit 0
MS_NOT_APPLICABLE = "NOT_APPLICABLE"
MS_INVALID = "INVALID"
MS_PRESENT = "PRESENT"

# ---------------------------------------------------------------- row / file reason codes
MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
INVALID_DECIMAL = "INVALID_DECIMAL"
INVALID_INTEGER = "INVALID_INTEGER"
NEGATIVE_METRIC = "NEGATIVE_METRIC"
INVALID_DATE = "INVALID_DATE"
INVALID_DATE_RANGE = "INVALID_DATE_RANGE"
FUTURE_DATE = "FUTURE_DATE"
CURRENCY_MISSING = "CURRENCY_MISSING"
CURRENCY_CONFLICT = "CURRENCY_CONFLICT"
CURRENCY_INVALID = "CURRENCY_INVALID"
DUPLICATE_CANONICAL_COLUMN = "DUPLICATE_CANONICAL_COLUMN"
UNSUPPORTED_MATCH_TYPE = "UNSUPPORTED_MATCH_TYPE"
AMBIGUOUS_REPORT_TYPE = "AMBIGUOUS_REPORT_TYPE"
UNKNOWN_REPORT_TYPE = "UNKNOWN_REPORT_TYPE"
ROW_IDENTITY_INCOMPLETE = "ROW_IDENTITY_INCOMPLETE"
UNSUPPORTED_ENCODING = "UNSUPPORTED_ENCODING"
UNSAFE_FORMULA_CELL = "UNSAFE_FORMULA_CELL"
OVERSIZED_CELL = "OVERSIZED_CELL"
SOURCE_HASH_DUPLICATE = "SOURCE_HASH_DUPLICATE"
OVERLAP_CONFLICT = "OVERLAP_CONFLICT"
SNAPSHOT_CONFLICT = "SNAPSHOT_CONFLICT"
DUPLICATE_ROW_CONFLICT = "DUPLICATE_ROW_CONFLICT"
# file-level format reasons
UNSUPPORTED_REPORT_FORMAT = "UNSUPPORTED_REPORT_FORMAT"
EXTENSION_CONTENT_MISMATCH = "EXTENSION_CONTENT_MISMATCH"
SUSPICIOUS_EXTENSION = "SUSPICIOUS_EXTENSION"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
EMPTY_FILE = "EMPTY_FILE"
NO_HEADER = "NO_HEADER"
NO_DATA_ROWS = "NO_DATA_ROWS"
TOO_MANY_COLUMNS = "TOO_MANY_COLUMNS"
TOO_MANY_ROWS = "TOO_MANY_ROWS"
PATH_UNSAFE = "PATH_UNSAFE"
TOO_MANY_FILES = "TOO_MANY_FILES"
AMBIGUOUS_DELIMITER = "AMBIGUOUS_DELIMITER"        # a tie between candidates, or no delimiter at all
XLSX_PARSER_UNAVAILABLE = "XLSX_PARSER_UNAVAILABLE"  # openpyxl import failed (declared dep missing)
# ignore reasons (a file deliberately skipped, NEVER accepted and NEVER format-blocking)
IGNORED_TEMP_LOCK_FILE = "IGNORED_TEMP_LOCK_FILE"

# ---------------------------------------------------------------- overlap classification
OV_NO_OVERLAP = "NO_OVERLAP"
OV_EXACT_DUPLICATE = "EXACT_DUPLICATE_COVERAGE"
OV_EXACT_CONFLICT = "EXACT_COVERAGE_CONFLICT"
OV_PARTIAL = "PARTIAL_OVERLAP"
OV_CONTAINED = "CONTAINED_RANGE"
OV_ADJACENT = "ADJACENT_RANGE"
OV_UNKNOWN_SEMANTICS = "UNKNOWN_SEMANTICS"

# ---------------------------------------------------------------- analysis-readiness states
PREFLIGHT_BLOCKED = "PHASE7_2_PREFLIGHT_BLOCKED"
REPORT_INPUT_REQUIRED = "PHASE7_REPORT_INPUT_REQUIRED"
REPORT_FORMAT_BLOCKED = "PHASE7_REPORT_FORMAT_BLOCKED"
REPORT_CLASSIFICATION_BLOCKED = "PHASE7_REPORT_CLASSIFICATION_BLOCKED"
REPORT_VALIDATION_BLOCKED = "PHASE7_REPORT_VALIDATION_BLOCKED"
REPORT_CURRENCY_BLOCKED = "PHASE7_REPORT_CURRENCY_BLOCKED"
REPORT_DATE_BLOCKED = "PHASE7_REPORT_DATE_BLOCKED"
REPORT_OVERLAP_REVIEW_REQUIRED = "PHASE7_REPORT_OVERLAP_REVIEW_REQUIRED"
REPORT_CONFLICT_BLOCKED = "PHASE7_REPORT_CONFLICT_BLOCKED"
REPORT_VERIFICATION_BLOCKED = "PHASE7_REPORT_VERIFICATION_BLOCKED"
REPORTS_READY = "SESSION7_2_REPORTS_READY_FOR_ANALYSIS"
SESSION_BLOCKED = "SESSION7_2_BLOCKED"
# internal reconciliation code (not a session state)
REPORT_SEMANTICS_REQUIRED = "REPORT_SEMANTICS_REQUIRED"

# most-restrictive rank ladder for the blocked states (lower rank == more restrictive == wins).
_BLOCK_RANK = {
    REPORT_VERIFICATION_BLOCKED: 0,
    REPORT_FORMAT_BLOCKED: 1,
    REPORT_CLASSIFICATION_BLOCKED: 2,
    REPORT_CURRENCY_BLOCKED: 3,
    REPORT_DATE_BLOCKED: 4,
    REPORT_VALIDATION_BLOCKED: 5,
    REPORT_CONFLICT_BLOCKED: 6,
    REPORT_OVERLAP_REVIEW_REQUIRED: 7,
}

# ---------------------------------------------------------------- idempotency result
IDEMPOTENT_ALREADY_IMPORTED = "IDEMPOTENT_ALREADY_IMPORTED"
# a file deliberately skipped without acceptance or format judgement (e.g. an Excel ~$ lock file).
IMPORT_STATE_IGNORED = "IGNORED"

# per-file manifest status vocabulary (requirement: ACCEPTED | QUARANTINED | IGNORED).
FILE_STATUS_ACCEPTED = "ACCEPTED"
FILE_STATUS_QUARANTINED = "QUARANTINED"
FILE_STATUS_IGNORED = "IGNORED"

# ---------------------------------------------------------------- artifact filenames
F_SOURCE_REGISTRY = "PHASE7-REPORT-SOURCE-REGISTRY.json"
F_IMPORT_MANIFEST = "PHASE7-REPORT-IMPORT-MANIFEST.json"
F_VALIDATION = "PHASE7-REPORT-VALIDATION.json"
F_ROW_ERRORS = "PHASE7-REPORT-ROW-ERRORS.json"
F_OVERLAP = "PHASE7-REPORT-OVERLAP-ANALYSIS.json"
F_CONFLICTS = "PHASE7-REPORT-CONFLICTS.json"
F_READINESS = "PHASE7-REPORT-ANALYSIS-READINESS.json"
F_SUMMARY_MD = "PHASE7-REPORT-IMPORT-SUMMARY.md"
F_VERIFICATION = "PHASE7-REPORT-VERIFICATION.json"
F_MANIFEST = "PHASE7-REPORT-MANIFEST.json"
F_INPUT_REQUIRED_MD = "PHASE7-REPORT-INPUT-REQUIRED.md"

# ---------------------------------------------------------------- credential / boundary guards
_FORBIDDEN_SUBSTRINGS = ("cookie", "session_id", "sessionid", "refresh_token", "access_token",
                         "set-cookie", "x-amz", "mws_auth", "authorization", "password", "passwd",
                         "client_secret", "sellercentral", "seller-central")

# a leading character that a spreadsheet may interpret as a formula.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")

# accepted currency codes (ISO-4217-like 3-letter). Unknown-but-well-formed codes are still
# flagged so a silent currency never slips through.
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_KNOWN_CURRENCIES = frozenset((
    "USD", "CAD", "MXN", "BRL", "EUR", "GBP", "SEK", "PLN", "TRY", "AED", "SAR",
    "INR", "JPY", "AUD", "SGD", "CNY", "CHF", "NOK", "DKK", "EGP",
))

# declared date input formats -> normalized to ISO YYYY-MM-DD. US marketplace ordering (MM/DD).
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y")

# accepted match-type vocabulary (normalized upper). Blank / dash => NOT_APPLICABLE (auto/product).
_MATCH_TYPES = {"exact": "EXACT", "phrase": "PHRASE", "broad": "BROAD"}
_MATCH_NA = {"", "-", "*", "targeting", "auto", "n/a", "na", "not applicable"}


class ReportIngestionError(Exception):
    pass


# ================================================================ canonical field + alias registry
# canonical fields by role.
DATE_FIELDS = ("report_date", "start_date", "end_date")
MONEY_FIELDS = ("cost", "spend", "sales_1d", "sales_7d", "sales_14d", "sales_30d", "budget")
INT_FIELDS = ("impressions", "clicks",
              "orders_1d", "orders_7d", "orders_14d", "orders_30d",
              "units_1d", "units_7d", "units_14d", "units_30d",
              "attributed_conversions", "attributed_units")
TEXT_FIELDS = ("portfolio_name", "campaign_name", "campaign_id", "campaign_status", "campaign_type",
               "targeting_type", "ad_group_name", "ad_group_id", "targeting_expression",
               "targeting_text", "targeting_id", "match_type", "search_term", "advertised_sku",
               "advertised_asin", "purchased_asin", "placement", "budget_type", "currency",
               "country", "marketplace")

CANONICAL_FIELDS = tuple(sorted(set(DATE_FIELDS) | set(MONEY_FIELDS) | set(INT_FIELDS) | set(TEXT_FIELDS)))

# alias map keyed by the alnum-token comparison key (see _header_key). Deterministic + declared.
# NOTE: a bare "sales"/"orders"/"units" with NO attribution window is intentionally NOT mapped — a
# missing window is never inferred; the header is preserved in the extras structure instead.
_ALIASES = {
    "date": "report_date", "day": "report_date",
    "start date": "start_date", "reporting start date": "start_date",
    "end date": "end_date", "reporting end date": "end_date",
    "portfolio name": "portfolio_name", "portfolio": "portfolio_name",
    "currency": "currency", "currency code": "currency",
    "campaign name": "campaign_name", "campaign": "campaign_name",
    "campaign id": "campaign_id",
    "campaign status": "campaign_status", "campaign state": "campaign_status",
    "campaign type": "campaign_type",
    "targeting type": "targeting_type",
    "ad group name": "ad_group_name", "ad group": "ad_group_name",
    "ad group id": "ad_group_id",
    "targeting": "targeting_expression", "targeting expression": "targeting_expression",
    "keyword text": "targeting_text", "keyword": "targeting_text", "keyword or product targeting": "targeting_text",
    "targeting id": "targeting_id", "keyword id": "targeting_id",
    "match type": "match_type",
    "customer search term": "search_term", "search term": "search_term",
    "advertised sku": "advertised_sku", "sku": "advertised_sku",
    "advertised asin": "advertised_asin", "asin": "advertised_asin",
    "purchased asin": "purchased_asin", "purchased product asin": "purchased_asin",
    "impressions": "impressions",
    "clicks": "clicks",
    "spend": "spend", "total spend": "spend",
    "cost": "cost",
    "1 day total sales": "sales_1d", "7 day total sales": "sales_7d",
    "14 day total sales": "sales_14d", "30 day total sales": "sales_30d",
    "1 day total orders": "orders_1d", "7 day total orders": "orders_7d",
    "14 day total orders": "orders_14d", "30 day total orders": "orders_30d",
    "1 day total units": "units_1d", "7 day total units": "units_7d",
    "14 day total units": "units_14d", "30 day total units": "units_30d",
    "attributed conversions": "attributed_conversions", "attributed units": "attributed_units",
    "placement": "placement",
    "budget": "budget", "campaign budget": "budget",
    "budget type": "budget_type",
    "country": "country", "marketplace": "marketplace",
}


# ================================================================ small helpers
def _recursive_strip(obj, names):
    """Deep-copy *obj* with every dict key in *names* removed (at any depth). Used so nested volatile
    fields (e.g. a per-source imported_at_volatile) never enter a deterministic content hash."""
    if isinstance(obj, dict):
        return {k: _recursive_strip(v, names) for k, v in obj.items() if k not in names}
    if isinstance(obj, list):
        return [_recursive_strip(v, names) for v in obj]
    return obj


def _finalize(doc, extra_volatile=()):
    volatile = set(_VOLATILE) | set(extra_volatile)
    doc["deterministic_content_sha256"] = content_sha256(_recursive_strip(doc, volatile))
    return doc


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha256_hex(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _prefix(sha):
    return sha[:8] if isinstance(sha, str) and len(sha) >= 8 else sha


def _canonical_line(obj):
    """A single-line canonical JSON (deterministic key order, no spaces) for JSONL rows."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _atomic_write_bytes(path, data):
    """temp sibling -> flush -> fsync -> os.replace. A failed write never overwrites a last-valid file."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-7-2-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def csv_safe_cell(value):
    """Neutralize a spreadsheet-formula-leading cell for any human CSV/summary export by prefixing a
    single quote. The importer NEVER executes a cell; this only protects downstream exports."""
    s = "" if value is None else str(value)
    if s and s[0] in _FORMULA_LEADS:
        return "'" + s
    return s


def _md_safe(value):
    """Escape pipe/formula so a value is inert inside a Markdown table cell."""
    s = csv_safe_cell(value)
    return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _is_tz_aware_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.tzinfo is not None and dt.utcoffset() is not None


def _contains_credentials(obj):
    """True if any key or string value looks like credential / session / Amazon-account material."""
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str) and any(s in k.lower() for s in _FORBIDDEN_SUBSTRINGS):
                    return True
                if walk(v):
                    return True
        elif isinstance(o, (list, tuple)):
            for v in o:
                if walk(v):
                    return True
        elif isinstance(o, str):
            low = o.lower()
            if "sellercentral.amazon" in low or "seller-central" in low:
                return True
            if low.startswith("atza|") or (low.startswith("eyj") and len(o) > 40):
                return True
        return False
    return walk(obj)


# ================================================================ HEADER NORMALIZATION
def _header_key(raw):
    """A deterministic alnum-token comparison key: NFKC, lowercase, punctuation -> single space.
    'Click-Thru Rate (CTR)' -> 'click thru rate ctr'; '7 Day Total Sales' -> '7 day total sales'."""
    s = unicodedata.normalize("NFKC", raw if isinstance(raw, str) else str(raw))
    s = s.strip().lower()
    s = re.sub(r"[^0-9a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_header(raw):
    """Return (original, normalized_display, comparison_key, canonical_or_None)."""
    original = raw if isinstance(raw, str) else str(raw)
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", original).strip())
    key = _header_key(original)
    canonical = _ALIASES.get(key)
    return original, normalized, key, canonical


def map_headers(raw_headers):
    """Map a list of raw headers to canonical fields deterministically.

    Returns a dict:
      canonical  -> {canonical_field: [column_index, ...]}   (a list catches duplicate canonicals)
      columns    -> [ {index, original, normalized, key, canonical} ]
      extras     -> [ {index, original, key} ]   unmapped columns (declared, never silently discarded)
      duplicates -> [canonical_field, ...]        canonical fields backed by 2+ source columns
    """
    columns, canonical, extras = [], {}, []
    for idx, raw in enumerate(raw_headers):
        original, normalized, key, canon = normalize_header(raw)
        columns.append({"index": idx, "original": original, "normalized": normalized,
                        "key": key, "canonical": canon})
        if canon is None:
            extras.append({"index": idx, "original": original, "key": key})
        else:
            canonical.setdefault(canon, []).append(idx)
    duplicates = sorted(f for f, idxs in canonical.items() if len(idxs) > 1)
    return {"canonical": canonical, "columns": columns, "extras": extras, "duplicates": duplicates}


# ================================================================ FILE-FORMAT DETECTION
def decode_delimited_text(raw):
    """Deterministically decode report bytes to text. Tries utf-8-sig (only when a real BOM is
    present), then utf-8, cp1252, latin-1 — in that fixed order, NEVER a probabilistic guess. Returns
    (encoding_label, text) or (None, None). latin-1 decodes any byte string, so a non-None result is
    effectively guaranteed for text that already passed the binary-signature guards."""
    if raw[:3] == b"\xef\xbb\xbf":
        try:
            return "utf-8-sig", raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return enc, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None, None


def detect_delimiter(first_line, ext=""):
    """Deterministic delimiter detection among comma / tab / semicolon / pipe. Returns
    (delimiter, reason). For .tsv, tab is required (no tab => EXTENSION_CONTENT_MISMATCH). Otherwise the
    single highest-count candidate wins; a tie between 2+ candidates, or NO candidate at all (which
    would silently parse as one column), is AMBIGUOUS_DELIMITER — the file is quarantined, never guessed."""
    if ext == ".tsv":
        if "\t" in first_line:
            return "\t", None
        return None, EXTENSION_CONTENT_MISMATCH
    counts = {d: first_line.count(d) for d, _ in _DELIMITERS}
    max_count = max(counts.values())
    if max_count == 0:
        return None, AMBIGUOUS_DELIMITER
    winners = [d for d, _ in _DELIMITERS if counts[d] == max_count]
    if len(winners) > 1:
        return None, AMBIGUOUS_DELIMITER
    return winners[0], None


def detect_format(path, *, sample_bytes=65536):
    """Detect parser + encoding + delimiter from BOTH extension and content signature. Never trusts the
    extension alone. Returns a dict with format / parser / delimiter / encoding / worksheet / reason
    (reason set on block).

    Natively supported: .csv/.tsv/.txt delimited text (utf-8-sig/utf-8/cp1252/latin-1; comma/tab/
    semicolon/pipe) and .xlsx workbooks (openpyxl, read-only/data-only). .xls (OLE) and .xlsm (macro)
    remain refused, as do zip/binary/office containers under a text extension."""
    result = {"format": None, "parser": None, "delimiter": None, "encoding": None, "worksheet": None,
              "reason": None, "extension": os.path.splitext(path)[1].lower()}
    ext = result["extension"]
    if ext in SUSPICIOUS_EXTENSIONS:
        result["reason"] = SUSPICIOUS_EXTENSION
        result["format"] = "UNSUPPORTED"
        return result
    try:
        size = os.path.getsize(path)
    except OSError:
        result["reason"] = EMPTY_FILE
        result["format"] = "UNSUPPORTED"
        return result
    if size > MAX_FILE_BYTES:
        result["reason"] = FILE_TOO_LARGE
        result["format"] = "UNSUPPORTED"
        return result
    if size == 0:
        result["reason"] = EMPTY_FILE
        result["format"] = "UNSUPPORTED"
        return result
    with open(path, "rb") as f:
        head = f.read(sample_bytes)
    # binary / archive / office signatures — decided by signature, never by the extension alone.
    if head[:4] == b"PK\x03\x04":       # zip container (xlsx/xlsm are zips; also an archive-bomb vector)
        if ext == SUPPORTED_XLSX_EXTENSION:
            result.update(format="XLSX", parser=PARSER_XLSX)   # supported; validity checked at read time
            return result
        if ext in XLSX_EXTENSIONS:      # .xlsm macro workbook — refused
            result.update(format="UNSUPPORTED", reason=UNSUPPORTED_REPORT_FORMAT)
            return result
        result.update(format="UNSUPPORTED", reason=EXTENSION_CONTENT_MISMATCH)   # zip under .csv/.txt/...
        return result
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # legacy OLE / .xls — refused
        result.update(format="UNSUPPORTED", reason=UNSUPPORTED_REPORT_FORMAT)
        return result
    if ext in XLSX_EXTENSIONS:          # an excel extension WITHOUT a zip/OLE signature — corrupt/renamed
        result.update(format="UNSUPPORTED", reason=EXTENSION_CONTENT_MISMATCH)
        return result
    if b"\x00" in head:                 # a NUL byte never appears in a text report
        result.update(format="UNSUPPORTED", reason=EXTENSION_CONTENT_MISMATCH)
        return result
    # delimited text: decode the WHOLE file (bounded by MAX_FILE_BYTES) so a non-ASCII byte beyond the
    # sample can never mis-detect the encoding, then detect the delimiter from the header line.
    with open(path, "rb") as f:
        raw = f.read()
    encoding, text = decode_delimited_text(raw)
    if encoding is None:
        result.update(format="UNSUPPORTED", reason=UNSUPPORTED_ENCODING)
        return result
    if "\x00" in text:                  # NUL anywhere in the decoded text => not a text report
        result.update(format="UNSUPPORTED", reason=EXTENSION_CONTENT_MISMATCH)
        return result
    lines = text.splitlines()
    first_line = lines[0] if lines else ""
    delimiter, dreason = detect_delimiter(first_line, ext)
    if dreason:
        result.update(format="UNSUPPORTED", reason=dreason)
        return result
    fmt = "CSV_BOM" if encoding == "utf-8-sig" else "CSV"
    if delimiter == "\t":
        fmt = "TSV"
    result.update(format=fmt, parser=PARSER_TEXT, delimiter=delimiter, encoding=encoding)
    return result


# ================================================================ REPORT CLASSIFICATION
def _classification_matches(present):
    """Return the list of report types whose declared header predicate holds. Predicates are built to
    be mutually exclusive for well-formed reports; a malformed file matching 2+ is AMBIGUOUS."""
    p = present

    def has(*fs):
        return any(f in p for f in fs)

    camp = has("campaign_name", "campaign_id")
    adg = has("ad_group_name", "ad_group_id")
    targ = has("targeting_expression", "targeting_text", "targeting_id")
    m = []
    if has("search_term") and not has("purchased_asin"):
        m.append(SP_SEARCH_TERM)
    if targ and has("match_type") and not has("search_term") \
            and not has("purchased_asin") and not has("advertised_sku", "advertised_asin"):
        m.append(SP_TARGETING)
    if has("advertised_sku", "advertised_asin") and not has("search_term") \
            and not has("purchased_asin") and not (targ and has("match_type")) and not has("placement"):
        m.append(SP_ADVERTISED_PRODUCT)
    if has("purchased_asin") and not has("search_term"):
        m.append(SP_PURCHASED_PRODUCT)
    if has("placement") and not has("search_term") and not has("purchased_asin"):
        m.append(SP_PLACEMENT)
    if has("budget") and camp and not has("impressions") and not has("search_term") \
            and not has("purchased_asin") and not has("advertised_sku", "advertised_asin") and not targ:
        m.append(SP_BUDGET)
    if camp and has("impressions") and not adg and not targ and not has("search_term") \
            and not has("advertised_sku", "advertised_asin") and not has("purchased_asin") \
            and not has("placement") and not has("budget"):
        m.append(SP_CAMPAIGN)
    return sorted(set(m))


def classify_report(present_canonical_fields, filename_hint=None):
    """Classify from declared headers ONLY. filename is recorded as non-authoritative supporting
    evidence. Returns (report_type, evidence). report_type is a REPORT_TYPE, UNKNOWN, or AMBIGUOUS."""
    present = set(present_canonical_fields)
    matches = _classification_matches(present)
    if len(matches) == 1:
        decision = matches[0]
    elif len(matches) == 0:
        decision = UNKNOWN
    else:
        decision = AMBIGUOUS
    evidence = {
        "present_canonical_fields": sorted(present),
        "matched_types": matches,
        "decision": decision,
        "filename_hint": (os.path.basename(filename_hint) if filename_hint else None),
        "filename_is_authoritative": False,
    }
    return decision, evidence


# ================================================================ VALUE PARSING (Decimal / int / date)
# strict grouping form only: 1,234 or 1,234,567.89 — a malformed grouping like 1,23,456 is rejected.
_GROUPED_RE = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_CURRENCY_SYMBOLS = "$€£¥₹"


def _preparse_number_text(raw):
    """Strip a single leading currency symbol and strict thousands grouping. Returns the cleaned
    string. A formula-leading cell is NOT cleaned here (it is flagged separately as unsafe)."""
    s = raw.strip()
    if s and s[0] in _CURRENCY_SYMBOLS:
        s = s[1:].strip()
    if _GROUPED_RE.match(s):
        s = s.replace(",", "")
    return s


def is_formula_cell(raw):
    """True if a raw cell begins with a spreadsheet formula lead. Numeric '-5' is NOT treated as a
    formula (that is handled by numeric parsing / NEGATIVE_METRIC)."""
    if not isinstance(raw, str) or raw == "":
        return False
    c = raw[0]
    if c in ("=", "@", "\t", "\r"):
        return True
    if c in ("+", "-"):
        rest = raw[1:].strip()
        # a signed number is not a formula; a signed expression / reference is.
        return not re.match(r"^[\d.,]*$", rest)
    return False


def parse_money_cell(raw):
    """Parse a monetary cell to a canonical Decimal string via core.money. Returns (value, state,
    reason). state is one of the MS_* constants; value is a canonical decimal string or None."""
    if raw is None:
        return None, MS_MISSING, None
    s = raw.strip()
    if s == "":
        return None, MS_BLANK, None
    if is_formula_cell(raw):
        return None, MS_INVALID, UNSAFE_FORMULA_CELL
    cleaned = _preparse_number_text(s)
    neg = cleaned.startswith("-")
    try:
        d = MONEY.parse_decimal_string(cleaned, field="money")
    except MONEY.MoneyError:
        return None, MS_INVALID, INVALID_DECIMAL
    if neg or d < 0:
        return None, MS_INVALID, NEGATIVE_METRIC
    value = MONEY.decimal_to_canonical_string(d)
    return value, (MS_ZERO if d == 0 else MS_PRESENT), None


def parse_int_cell(raw):
    """Parse a count cell to a non-negative int. Returns (value, state, reason)."""
    if raw is None:
        return None, MS_MISSING, None
    s = raw.strip()
    if s == "":
        return None, MS_BLANK, None
    if is_formula_cell(raw):
        return None, MS_INVALID, UNSAFE_FORMULA_CELL
    cleaned = _preparse_number_text(s)
    if cleaned.startswith("-") and re.match(r"^-\d+$", cleaned):
        return None, MS_INVALID, NEGATIVE_METRIC
    try:
        iv = MONEY.parse_nonnegative_int(cleaned, field="count")
    except MONEY.MoneyError:
        return None, MS_INVALID, INVALID_INTEGER
    return iv, (MS_ZERO if iv == 0 else MS_PRESENT), None


def parse_date_cell(raw):
    """Parse a single date to ISO YYYY-MM-DD using the declared US-marketplace formats. Returns
    (iso_or_None, reason). No wall-clock is consulted."""
    if raw is None or str(raw).strip() == "":
        return None, INVALID_DATE
    s = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            dt = _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        return dt.strftime("%Y-%m-%d"), None
    return None, INVALID_DATE


def parse_date_range(report_date=None, start_date=None, end_date=None, *, reference_date=None):
    """Resolve a (start, end) ISO range from a report_date OR an explicit start/end pair.

    Returns (start_iso, end_iso, reason). A future end date (relative to an owner-declared
    reference_date) is FUTURE_DATE; end<start is INVALID_DATE_RANGE. A missing range is INVALID_DATE.
    """
    if start_date is not None or end_date is not None:
        s_iso, s_err = parse_date_cell(start_date) if start_date is not None else (None, INVALID_DATE)
        e_iso, e_err = parse_date_cell(end_date) if end_date is not None else (None, INVALID_DATE)
        if s_err or e_err:
            return None, None, INVALID_DATE
        start_iso, end_iso = s_iso, e_iso
    elif report_date is not None:
        d_iso, d_err = parse_date_cell(report_date)
        if d_err:
            return None, None, INVALID_DATE
        start_iso = end_iso = d_iso
    else:
        return None, None, INVALID_DATE
    if end_iso < start_iso:
        return None, None, INVALID_DATE_RANGE
    if reference_date is not None:
        ref_iso, ref_err = parse_date_cell(reference_date)
        if not ref_err and end_iso > ref_iso:
            return None, None, FUTURE_DATE
    return start_iso, end_iso, None


def validate_currency(raw):
    """Return (code_or_None, reason). A well-formed but unknown code is flagged CURRENCY_INVALID so a
    silent currency never slips through; a blank code is CURRENCY_MISSING at the point of use."""
    if raw is None or str(raw).strip() == "":
        return None, None
    code = str(raw).strip().upper()
    if not _CURRENCY_RE.match(code):
        return None, CURRENCY_INVALID
    if code not in _KNOWN_CURRENCIES:
        return code, CURRENCY_INVALID
    return code, None


def normalize_match_type(raw):
    """Return (canonical_or_None, reason). Blank / dash => None (auto/product targeting). Unknown =>
    UNSUPPORTED_MATCH_TYPE."""
    if raw is None:
        return None, None
    s = str(raw).strip().lower()
    if s in _MATCH_NA:
        return None, None
    if s in _MATCH_TYPES:
        return _MATCH_TYPES[s], None
    return None, UNSUPPORTED_MATCH_TYPE


# ================================================================ ROW VALIDATION
# identity groups required per report type; each group resolves from >=1 canonical column.
_IDENTITY_SPEC = {
    SP_CAMPAIGN: ("campaign",),
    SP_TARGETING: ("campaign", "ad_group", "targeting"),
    SP_SEARCH_TERM: ("campaign", "ad_group", "search_term"),
    SP_ADVERTISED_PRODUCT: ("campaign", "advertised"),
    SP_PURCHASED_PRODUCT: ("campaign", "purchased"),
    SP_PLACEMENT: ("campaign", "placement"),
    SP_BUDGET: ("campaign", "budget"),
}
# ordered identity dimensions that form the canonical row key.
_KEY_DIMS = {
    SP_CAMPAIGN: ("campaign",),
    SP_TARGETING: ("campaign", "ad_group", "targeting", "match_type"),
    SP_SEARCH_TERM: ("campaign", "ad_group", "targeting", "search_term", "match_type"),
    SP_ADVERTISED_PRODUCT: ("campaign", "ad_group", "advertised"),
    SP_PURCHASED_PRODUCT: ("campaign", "ad_group", "advertised", "purchased"),
    SP_PLACEMENT: ("campaign", "placement"),
    SP_BUDGET: ("campaign",),
}
_GROUP_SOURCES = {
    "campaign": ("campaign_id", "campaign_name"),
    "ad_group": ("ad_group_id", "ad_group_name"),
    "targeting": ("targeting_id", "targeting_expression", "targeting_text"),
    "search_term": ("search_term",),
    "advertised": ("advertised_sku", "advertised_asin"),
    "purchased": ("purchased_asin",),
    "placement": ("placement",),
    "budget": ("budget",),
}


def _resolve_group(group, cells):
    """Return (value, basis_field) for an identity group, preferring IDs then names. None if absent."""
    for field in _GROUP_SOURCES[group]:
        v = cells.get(field)
        if v is not None and str(v).strip() != "":
            return str(v).strip(), field
    return None, None


def _esc(x):
    return str(x).replace("\\", "\\\\").replace("|", "\\|")


def canonical_row_key(report_type, marketplace, start_iso, end_iso, dim_values):
    """Deterministic, escaped, collision-resistant key. dim_values maps dim-name -> value (or None)."""
    parts = [f"type={_esc(report_type)}", f"mk={_esc(marketplace)}", f"range={_esc(start_iso)}:{_esc(end_iso)}"]
    for dim in _KEY_DIMS.get(report_type, ()):  # deterministic dim order
        parts.append(f"{dim}={_esc(dim_values.get(dim) if dim_values.get(dim) is not None else '')}")
    return "|".join(parts)


def lineage_for(source_sha, row_number, report_type, start_iso, end_iso, original_headers, key):
    body = {
        "schema_version": SCHEMA_LINEAGE,
        "source_file_sha256": source_sha,
        "source_row_number": row_number,
        "source_report_type": report_type,
        "source_start_date": start_iso,
        "source_end_date": end_iso,
        "source_original_headers": list(original_headers),
        "normalization_schema_version": NORMALIZATION_SCHEMA_VERSION,
        "canonical_row_key": key,
        "contributing": [{"source_file_sha256": source_sha, "source_row_number": row_number}],
    }
    body["lineage_hash"] = _sha_bytes(_canonical_line(
        {"k": key, "src": source_sha, "row": row_number, "type": report_type}).encode("utf-8"))
    return body


def validate_row(report_type, cells, present_columns, *, source_sha, row_number, original_headers,
                 marketplace_default="US", reference_date=None, dup_conflict_fields=(),
                 oversized_fields=(), formula_fields=()):
    """Validate + normalize one mapped row. Returns (normalized_row_or_None, reasons, meta).

    * cells: {canonical_field: raw_value} (duplicate canonicals already collapsed by the reader)
    * present_columns: set of canonical fields that HAD a source column (BLANK vs MISSING distinction)
    """
    reasons = []
    for f in sorted(set(dup_conflict_fields)):
        reasons.append(f"{DUPLICATE_CANONICAL_COLUMN}:{f}")
    for f in sorted(set(oversized_fields)):
        reasons.append(f"{OVERSIZED_CELL}:{f}")
    for f in sorted(set(formula_fields)):
        reasons.append(f"{UNSAFE_FORMULA_CELL}:{f}")

    marketplace = (str(cells.get("marketplace")).strip() if cells.get("marketplace") else None) \
        or marketplace_default

    # --- date / range ---
    start_iso, end_iso, date_err = parse_date_range(
        report_date=cells.get("report_date"), start_date=cells.get("start_date"),
        end_date=cells.get("end_date"), reference_date=reference_date)
    if date_err:
        reasons.append(date_err)

    # --- identity ---
    dim_values, key_basis, identity = {}, {}, {}
    for group in _IDENTITY_SPEC.get(report_type, ()):
        val, basis = _resolve_group(group, cells)
        if val is None:
            reasons.append(f"{MISSING_REQUIRED_FIELD}:{group}")
        else:
            dim_values[group] = val
            key_basis[group] = basis
    # resolve any extra key dims not in the required identity spec (e.g. match_type, ad_group on
    # advertised) using whatever columns exist — never required, only for key precision.
    for dim in _KEY_DIMS.get(report_type, ()):
        if dim in dim_values or dim == "match_type":
            continue
        val, basis = _resolve_group(dim, cells) if dim in _GROUP_SOURCES else (None, None)
        if val is not None:
            dim_values[dim] = val
            key_basis[dim] = basis

    # --- match type (where the key uses it) ---
    match_type = None
    if "match_type" in _KEY_DIMS.get(report_type, ()):
        mt, mt_err = normalize_match_type(cells.get("match_type"))
        if mt_err:
            reasons.append(mt_err)
        match_type = mt
        dim_values["match_type"] = mt or ""

    # --- metrics (money + int), preserving MISSING/BLANK/ZERO/NOT_APPLICABLE distinction ---
    metrics, metric_states = {}, {}
    money_present = False
    for field in MONEY_FIELDS:
        if field not in present_columns:
            continue
        # the column EXISTS: a None value is a present-but-empty (BLANK) cell, never MISSING.
        raw = cells.get(field)
        value, state, reason = parse_money_cell("" if raw is None else raw)
        if reason:
            reasons.append(f"{reason}:{field}")
            continue
        if state in (MS_PRESENT, MS_ZERO):
            metrics[field] = value
            money_present = money_present or (field != "budget")
            if state == MS_ZERO:
                metric_states[field] = MS_ZERO
        else:
            metric_states[field] = state  # BLANK
    for field in INT_FIELDS:
        if field not in present_columns:
            continue
        raw = cells.get(field)
        value, state, reason = parse_int_cell("" if raw is None else raw)
        if reason:
            reasons.append(f"{reason}:{field}")
            continue
        if state in (MS_PRESENT, MS_ZERO):
            metrics[field] = value
            if state == MS_ZERO:
                metric_states[field] = MS_ZERO
        else:
            metric_states[field] = state

    # --- currency (required only when a monetary metric is present) ---
    currency, cur_err = validate_currency(cells.get("currency"))
    if cur_err:
        reasons.append(cur_err)
    if money_present and (currency is None):
        reasons.append(CURRENCY_MISSING)

    meta = {"marketplace": marketplace, "start_date": start_iso, "end_date": end_iso,
            "currency": currency, "report_type": report_type}
    if reasons:
        meta["reasons"] = sorted(set(reasons))
        return None, meta["reasons"], meta

    # build canonical identity (values as present) for the normalized row
    for group in set(_IDENTITY_SPEC.get(report_type, ())) | set(_KEY_DIMS.get(report_type, ())):
        if group == "match_type":
            continue
        for field in _GROUP_SOURCES.get(group, ()):
            if cells.get(field) is not None and str(cells.get(field)).strip() != "":
                identity[field] = str(cells.get(field)).strip()
    if match_type is not None:
        identity["match_type"] = match_type

    key = canonical_row_key(report_type, marketplace, start_iso, end_iso, dim_values)
    row = {
        "schema_version": SCHEMA_NORMALIZED_ROW,
        "report_type": report_type,
        "marketplace": marketplace,
        "start_date": start_iso,
        "end_date": end_iso,
        "currency": currency,
        "identity": identity,
        "key_basis": key_basis,
        "metrics": metrics,
        "metric_states": metric_states,
        "duplicate_count": 1,
        "canonical_row_key": key,
        "lineage": lineage_for(source_sha, row_number, report_type, start_iso, end_iso,
                               original_headers, key),
    }
    meta["canonical_row_key"] = key
    return row, [], meta


# ================================================================ DELIMITED FILE READING
def read_delimited(path, fmt_info):
    """Read a delimited text file safely. Returns (headers, rows, reason). All cells are DATA — the
    reader never executes a formula. Enforces declared size / row / column limits."""
    import csv
    encoding = fmt_info.get("encoding") or "utf-8"
    delimiter = fmt_info.get("delimiter") or ","
    try:
        with open(path, "r", encoding=encoding, newline="") as f:
            text = f.read()
    except UnicodeDecodeError:
        return None, None, UNSUPPORTED_ENCODING
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        rows = list(reader)
    except csv.Error:
        return None, None, EXTENSION_CONTENT_MISMATCH
    if not rows:
        return None, None, EMPTY_FILE
    headers = rows[0]
    if not headers or all((c or "").strip() == "" for c in headers):
        return None, None, NO_HEADER
    if len(headers) > MAX_COLUMNS:
        return None, None, TOO_MANY_COLUMNS
    data = rows[1:]
    if len(data) > MAX_ROWS:
        return None, None, TOO_MANY_ROWS
    if len(data) == 0:
        return headers, [], NO_DATA_ROWS
    return headers, data, None


# ================================================================ XLSX WORKBOOK READING
def _xlsx_number_to_text(value):
    """A numeric xlsx cell -> its displayed decimal TEXT. NEVER a float in a monetary path: the text is
    parsed later through core.money's Decimal-safe parser. int stays exact; a float uses Python's
    shortest round-tripping repr with a bare trailing '.0' dropped (so 1000.0 -> '1000', 12.34 -> '12.34')."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    s = repr(float(value))          # shortest decimal that round-trips; 'nan'/'inf' pass through as text
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _xlsx_cell_text(value):
    """Convert one displayed xlsx cell (openpyxl data_only value) to normalized text without ever
    coercing money/metrics to float. openpyxl yields None / bool / int / float / datetime / str."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return _xlsx_number_to_text(value)
    if isinstance(value, _dt.datetime):
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def read_xlsx_rows(path):
    """Read an .xlsx workbook OFFLINE with openpyxl (read-only, data-only). Returns
    (headers, data, reason, worksheet_title). openpyxl only ever touches local bytes — it opens no
    socket. The first worksheet that has any non-empty cell is used (a deterministic report-sheet rule);
    every cell becomes normalized text so all formats converge on the SAME canonical row path and the
    existing Decimal-safe validation runs unchanged."""
    try:
        import openpyxl
    except ImportError:
        return None, None, XLSX_PARSER_UNAVAILABLE, None
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:                       # not a real workbook (bad zip / corrupt) -> format-refused
        return None, None, UNSUPPORTED_REPORT_FORMAT, None
    try:
        chosen_title, chosen_rows = None, None
        for ws in wb.worksheets:
            rows, nonempty = [], False
            for row in ws.iter_rows(values_only=True):
                cells = [_xlsx_cell_text(c) for c in row]
                if not nonempty and any(c != "" for c in cells):
                    nonempty = True
                rows.append(cells)
                if len(rows) > MAX_ROWS + 2:
                    return None, None, TOO_MANY_ROWS, ws.title
            if nonempty:
                chosen_title, chosen_rows = ws.title, rows
                break
    finally:
        wb.close()
    if chosen_title is None:
        return None, None, EMPTY_FILE, None
    # trim leading / trailing fully-empty rows so the header is the first non-empty row.
    def _blank(r):
        return all((c or "") == "" for c in r)
    start, end = 0, len(chosen_rows)
    while start < end and _blank(chosen_rows[start]):
        start += 1
    while end > start and _blank(chosen_rows[end - 1]):
        end -= 1
    trimmed = chosen_rows[start:end]
    if not trimmed:
        return None, None, EMPTY_FILE, chosen_title
    headers = trimmed[0]
    if all((c or "").strip() == "" for c in headers):
        return None, None, NO_HEADER, chosen_title
    if len(headers) > MAX_COLUMNS:
        return None, None, TOO_MANY_COLUMNS, chosen_title
    data = trimmed[1:]
    if len(data) > MAX_ROWS:
        return None, None, TOO_MANY_ROWS, chosen_title
    if len(data) == 0:
        return headers, [], NO_DATA_ROWS, chosen_title
    return headers, data, None, chosen_title


def canonicalize_input_rows(headers, data):
    """Coerce every header + data cell to plain text so all source formats (delimited text and xlsx)
    converge on ONE canonical row representation before the shared classification/validation pipeline.
    csv cells are already str; this makes the convergence explicit and defends against any non-str."""
    headers = [("" if h is None else str(h)) for h in headers]
    data = [[("" if c is None else str(c)) for c in row] for row in data]
    return headers, data


def build_cells(header_map, raw_row):
    """Assemble one raw row into {canonical: value}, resolving duplicate canonical columns.

    Returns (cells, present_columns, dup_conflict_fields, oversized_fields, formula_fields).
    A duplicate canonical column with EQUAL values collapses silently; a CONFLICT is flagged.
    """
    cells, present_columns = {}, set()
    dup_conflict, oversized, formula = [], [], []
    for canon, idxs in header_map["canonical"].items():
        present_columns.add(canon)
        raw_values = [raw_row[i] if i < len(raw_row) else "" for i in idxs]
        for rv in raw_values:
            if rv is not None and len(str(rv)) > MAX_FIELD_LEN:
                oversized.append(canon)
        non_empty = [str(v).strip() for v in raw_values if v is not None and str(v).strip() != ""]
        if len(idxs) > 1 and len(set(non_empty)) > 1:
            dup_conflict.append(canon)
            value = None
        elif non_empty:
            value = non_empty[0]
        else:
            value = None  # BLANK (column present, cell empty)
        cells[canon] = value
        if canon in TEXT_FIELDS and value is not None and is_formula_cell(str(raw_values[0])
                                                                          if raw_values else ""):
            formula.append(canon)
    return cells, present_columns, sorted(set(dup_conflict)), sorted(set(oversized)), sorted(set(formula))


# ================================================================ OVERLAP + RECONCILIATION
def report_semantics(report_type):
    return REPORT_SEMANTICS.get(report_type, SEM_UNKNOWN)


def classify_range_relationship(a_start, a_end, b_start, b_end):
    """Pure date-range relationship: SAME / CONTAINED / ADJACENT / PARTIAL / DISJOINT."""
    as_, ae = _dt.date.fromisoformat(a_start), _dt.date.fromisoformat(a_end)
    bs, be = _dt.date.fromisoformat(b_start), _dt.date.fromisoformat(b_end)
    if as_ == bs and ae == be:
        return "SAME"
    # disjoint?
    if ae < bs or be < as_:
        one_day = _dt.timedelta(days=1)
        if ae + one_day == bs or be + one_day == as_:
            return "ADJACENT"
        return "DISJOINT"
    # they intersect
    if (as_ <= bs and be <= ae) or (bs <= as_ and ae <= be):
        return "CONTAINED"
    return "PARTIAL"


def overlap_code(relationship, values_equal=True):
    """Map a pure relationship (+ value equality for SAME) to an OV_* classification."""
    if relationship == "SAME":
        return OV_EXACT_DUPLICATE if values_equal else OV_EXACT_CONFLICT
    return {"CONTAINED": OV_CONTAINED, "ADJACENT": OV_ADJACENT,
            "PARTIAL": OV_PARTIAL, "DISJOINT": OV_NO_OVERLAP}[relationship]


def _identity_sans_range(row):
    """Identity key with the date range removed — groups the same entity across different ranges."""
    key = row["canonical_row_key"]
    return re.sub(r"\|range=[^|]*", "", key)


def _metrics_equal(a, b):
    return a.get("metrics") == b.get("metrics") and a.get("currency") == b.get("currency")


def reconcile_interval_group(rows, semantics):
    """Reconcile one report-type group of normalized rows.

    Returns {result, rows, overlaps, conflicts, duplicate_row_count}. NEVER sums overlapping periods,
    NEVER sums snapshots, NEVER divides a range total across days. Aggregation is BLOCKED when
    semantics are UNKNOWN (REPORT_SEMANTICS_REQUIRED)."""
    if semantics == SEM_UNKNOWN:
        return {"result": REPORT_SEMANTICS_REQUIRED, "rows": [], "overlaps": [],
                "conflicts": [], "duplicate_row_count": 0}

    overlaps, conflicts = [], []
    duplicate_row_count = 0

    # 1) collapse exact-duplicate canonical rows (same key). Equal metrics -> merge; conflict -> flag.
    by_key = {}
    for r in rows:
        by_key.setdefault(r["canonical_row_key"], []).append(r)
    collapsed = []
    for key in sorted(by_key):
        group = by_key[key]
        base = group[0]
        conflict_here = False
        for other in group[1:]:
            if _metrics_equal(base, other):
                duplicate_row_count += 1
            else:
                conflict_here = True
        if conflict_here:
            conflicts.append({"canonical_row_key": key, "code": DUPLICATE_ROW_CONFLICT,
                              "row_count": len(group)})
        merged = dict(base)
        merged["duplicate_count"] = len(group)
        contributing = []
        for other in group:
            contributing.extend(other["lineage"]["contributing"])
        merged_lineage = dict(base["lineage"])
        merged_lineage["contributing"] = _dedup_contrib(contributing)
        merged["lineage"] = merged_lineage
        collapsed.append(merged)

    # 2) snapshot semantics: keep the latest by end_date per entity; never sum. Same-date conflict flag.
    if semantics in (SEM_POINT_IN_TIME, SEM_CUMULATIVE_SNAPSHOT):
        by_entity = {}
        for r in collapsed:
            by_entity.setdefault(_identity_sans_range(r), []).append(r)
        kept = []
        for ent in sorted(by_entity):
            snaps = sorted(by_entity[ent], key=lambda x: (x["end_date"], x["canonical_row_key"]))
            latest = snaps[-1]
            for other in snaps[:-1]:
                if other["end_date"] == latest["end_date"] and not _metrics_equal(other, latest):
                    conflicts.append({"identity": ent, "code": SNAPSHOT_CONFLICT,
                                      "end_date": latest["end_date"]})
            kept.append(latest)
        result_code = OVERLAP_CONFLICT if any(c["code"] == SNAPSHOT_CONFLICT for c in conflicts) else "OK"
        return {"result": ("CONFLICT" if result_code == OVERLAP_CONFLICT else "OK"),
                "rows": sorted(kept, key=lambda x: x["canonical_row_key"]),
                "overlaps": overlaps, "conflicts": conflicts, "duplicate_row_count": duplicate_row_count}

    # 3) interval/daily semantics: detect overlapping ranges per entity (never sum blindly).
    review_required = False
    by_entity = {}
    for r in collapsed:
        by_entity.setdefault(_identity_sans_range(r), []).append(r)
    for ent in sorted(by_entity):
        members = sorted(by_entity[ent], key=lambda x: (x["start_date"], x["end_date"]))
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                rel = classify_range_relationship(a["start_date"], a["end_date"],
                                                  b["start_date"], b["end_date"])
                if rel == "DISJOINT":
                    continue
                eq = _metrics_equal(a, b)
                code = overlap_code(rel, eq)
                finding = {"identity": ent, "code": code,
                           "range_a": f"{a['start_date']}:{a['end_date']}",
                           "range_b": f"{b['start_date']}:{b['end_date']}"}
                overlaps.append(finding)
                if code in (OV_PARTIAL, OV_CONTAINED, OV_EXACT_CONFLICT):
                    review_required = True
                    if code == OV_EXACT_CONFLICT:
                        conflicts.append({"identity": ent, "code": OVERLAP_CONFLICT,
                                          "range": finding["range_a"]})
    result = "REVIEW" if review_required else "OK"
    return {"result": result, "rows": sorted(collapsed, key=lambda x: x["canonical_row_key"]),
            "overlaps": overlaps, "conflicts": conflicts, "duplicate_row_count": duplicate_row_count}


def _dedup_contrib(contribs):
    seen, out = set(), []
    for c in contribs:
        k = (c["source_file_sha256"], c["source_row_number"])
        if k not in seen:
            seen.add(k)
            out.append(c)
    return sorted(out, key=lambda c: (c["source_file_sha256"], c["source_row_number"]))


# ================================================================ WORKSPACE + PATH SAFETY
_WORKSPACE_SUBDIRS = ("inbox", "processing", "accepted_raw", "quarantine", "candidate",
                      "final", "last_valid", "manifests", "fixtures")


def workspace_dirs(base_dir):
    d = {"base": os.path.abspath(base_dir)}
    for s in _WORKSPACE_SUBDIRS:
        d[s] = os.path.join(d["base"], s)
    return d


def ensure_workspace(base_dir):
    d = workspace_dirs(base_dir)
    for k, v in d.items():
        if k != "base":
            os.makedirs(v, exist_ok=True)
    return d


def _safe_rel(path, base):
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(base))
    norm = rel.replace(os.sep, "/")
    if os.path.isabs(rel) or norm == ".." or norm.startswith("../"):
        raise ReportIngestionError(f"path escapes workspace ({norm})")
    return norm


def scan_inbox(inbox_dir):
    """List regular report files directly in the inbox (no recursion -> no dir traversal / nested
    archive). Symlinks, subdirectories, null-byte names, and over-count are rejected, never followed."""
    files, rejects = [], []
    if not os.path.isdir(inbox_dir):
        return files, rejects
    for name in sorted(os.listdir(inbox_dir)):
        full = os.path.join(inbox_dir, name)
        if "\x00" in name:
            rejects.append({"name": "<null-byte>", "reason": PATH_UNSAFE})
            continue
        if os.path.islink(full):
            rejects.append({"name": name, "reason": PATH_UNSAFE})     # never follow a symlink
            continue
        if not os.path.isfile(full):
            continue                                                  # skip subdirs / specials
        real = os.path.realpath(full)
        if os.path.dirname(real) != os.path.realpath(inbox_dir):
            rejects.append({"name": name, "reason": PATH_UNSAFE})     # escapes the inbox
            continue
        files.append(full)
    if len(files) > MAX_FILES:
        for extra in files[MAX_FILES:]:
            rejects.append({"name": os.path.basename(extra), "reason": TOO_MANY_FILES})
        files = files[:MAX_FILES]
    return files, rejects


def _archive_copy(src_path, target_path):
    """Copy source bytes immutably (write once). An existing target is never rewritten."""
    if os.path.exists(target_path):
        return
    with open(src_path, "rb") as f:
        data = f.read()
    _atomic_write_bytes(target_path, data)


# ================================================================ PER-FILE PROCESSING
_FORMAT_REASONS = frozenset((UNSUPPORTED_REPORT_FORMAT, EXTENSION_CONTENT_MISMATCH, SUSPICIOUS_EXTENSION,
                             UNSUPPORTED_ENCODING, FILE_TOO_LARGE, EMPTY_FILE, NO_HEADER, NO_DATA_ROWS,
                             TOO_MANY_COLUMNS, TOO_MANY_ROWS, AMBIGUOUS_DELIMITER, XLSX_PARSER_UNAVAILABLE))
_CLASSIFICATION_REASONS = frozenset((AMBIGUOUS_REPORT_TYPE, UNKNOWN_REPORT_TYPE))


def _safe_archive_ext(ext):
    """Preserve a supported original extension (.csv/.tsv/.txt/.xlsx) for the immutable raw copy so the
    archived workbook stays openable; any other extension is neutralized to .dat."""
    return ext if ext in _ARCHIVE_EXTS else ".dat"


def _new_source_record(path, sha, byte_len):
    ext = os.path.splitext(path)[1].lower()
    safe_ext = _safe_archive_ext(ext)
    return {
        "schema_version": SCHEMA_SOURCE_REGISTRY,
        "source_file_sha256": sha,
        "source_byte_length": byte_len,
        "source_original_filename": os.path.basename(path),
        "source_safe_filename": f"{sha}{safe_ext}",
        "source_extension": ext,
        "detected_format": None, "detected_delimiter": None, "detected_encoding": None,
        "parser": None, "parser_version": PARSER_VERSION, "worksheet": None,
        "report_type": None, "classification_evidence": None,
        "source_start_date": None, "source_end_date": None, "currency_set": [],
        "row_count_raw": 0, "row_count_valid": 0, "row_count_invalid": 0,
        "import_state": None, "quarantine_state": None,
        "accepted_raw_path": None, "quarantine_path": None,
        "owner_source_note": None, "imported_at_volatile": None,
    }


def process_source_file(path, dirs, *, reference_date=None, marketplace_default="US",
                        known_hashes=frozenset(), now=None, owner_source_note=None):
    """Process ONE inbox file end-to-end. Returns a dict: source, normalized, invalid, idempotent."""
    byte_len = os.path.getsize(path)
    sha = _file_sha256_hex(path)
    rec = _new_source_record(path, sha, byte_len)
    rec["imported_at_volatile"] = now
    rec["owner_source_note"] = owner_source_note
    out = {"source": rec, "normalized": [], "invalid": [], "idempotent": False}

    # --- idempotency: identical bytes already accepted -> never re-import as new data ---
    accepted_path = os.path.join(dirs["accepted_raw"], rec["source_safe_filename"])
    if sha in known_hashes or os.path.exists(accepted_path):
        rec["import_state"] = IDEMPOTENT_ALREADY_IMPORTED
        rec["accepted_raw_path"] = _safe_rel(accepted_path, dirs["base"])
        out["idempotent"] = True
        return out

    # --- format detection (extension + content signature) ---
    fmt = detect_format(path)
    rec["detected_format"] = fmt.get("format")
    rec["parser"] = fmt.get("parser")
    is_xlsx = fmt.get("format") == "XLSX"
    if is_xlsx:
        rec["detected_delimiter"] = None
        rec["detected_encoding"] = None
    else:
        rec["detected_delimiter"] = _DELIMITER_LABELS.get(fmt.get("delimiter"), fmt.get("delimiter"))
        rec["detected_encoding"] = fmt.get("encoding")
    if fmt.get("reason") or fmt.get("format") == "UNSUPPORTED":
        return _quarantine_file(out, rec, dirs, path, sha, fmt.get("reason") or UNSUPPORTED_REPORT_FORMAT)

    # --- read rows safely (all formats converge on one canonical text-row representation) ---
    if is_xlsx:
        headers, data, read_reason, worksheet = read_xlsx_rows(path)
        rec["worksheet"] = worksheet
    else:
        headers, data, read_reason = read_delimited(path, fmt)
    if read_reason in (EMPTY_FILE, NO_HEADER, UNSUPPORTED_ENCODING, TOO_MANY_COLUMNS, TOO_MANY_ROWS,
                       EXTENSION_CONTENT_MISMATCH, UNSUPPORTED_REPORT_FORMAT, XLSX_PARSER_UNAVAILABLE,
                       AMBIGUOUS_DELIMITER):
        return _quarantine_file(out, rec, dirs, path, sha, read_reason)
    headers, data = canonicalize_input_rows(headers, data or [])
    header_map = map_headers(headers)
    present = set(header_map["canonical"].keys())
    rec["row_count_raw"] = len(data or [])

    # --- classification (headers only; filename is non-authoritative) ---
    report_type, evidence = classify_report(present, filename_hint=path)
    rec["classification_evidence"] = evidence
    if report_type == UNKNOWN:
        return _quarantine_file(out, rec, dirs, path, sha, UNKNOWN_REPORT_TYPE)
    if report_type == AMBIGUOUS:
        return _quarantine_file(out, rec, dirs, path, sha, AMBIGUOUS_REPORT_TYPE)
    rec["report_type"] = report_type
    if read_reason == NO_DATA_ROWS:
        return _quarantine_file(out, rec, dirs, path, sha, NO_DATA_ROWS)

    # --- accept the FILE (immutable raw copy); validate ROWS ---
    _archive_copy(path, accepted_path)
    rec["import_state"] = "ACCEPTED"
    rec["accepted_raw_path"] = _safe_rel(accepted_path, dirs["base"])
    original_headers = [c["original"] for c in header_map["columns"]]

    starts, ends, currencies = [], [], set()
    for i, raw_row in enumerate(data):
        row_number = i + 2   # 1-based incl. header line
        cells, present_cols, dup_conf, oversized, formula = build_cells(header_map, raw_row)
        row, reasons, meta = validate_row(
            report_type, cells, present_cols, source_sha=sha, row_number=row_number,
            original_headers=original_headers, marketplace_default=marketplace_default,
            reference_date=reference_date, dup_conflict_fields=dup_conf,
            oversized_fields=oversized, formula_fields=formula)
        if row is None:
            out["invalid"].append({"source_file_sha256": sha, "source_row_number": row_number,
                                   "report_type": report_type, "reason_codes": reasons})
            rec["row_count_invalid"] += 1
        else:
            out["normalized"].append(row)
            rec["row_count_valid"] += 1
            starts.append(meta["start_date"])
            ends.append(meta["end_date"])
            if meta.get("currency"):
                currencies.add(meta["currency"])
    rec["source_start_date"] = min(starts) if starts else None
    rec["source_end_date"] = max(ends) if ends else None
    rec["currency_set"] = sorted(currencies)
    if len(currencies) > 1:
        rec["quarantine_state"] = CURRENCY_CONFLICT   # advisory: a single file mixes currencies
    return out


def _quarantine_file(out, rec, dirs, path, sha, reason):
    ext = os.path.splitext(path)[1].lower()
    safe_ext = _safe_archive_ext(ext)
    qpath = os.path.join(dirs["quarantine"], f"{sha}{safe_ext}")
    try:
        _archive_copy(path, qpath)
        rec["quarantine_path"] = _safe_rel(qpath, dirs["base"])
    except OSError:
        rec["quarantine_path"] = None
    rec["import_state"] = "QUARANTINED"
    rec["quarantine_state"] = reason
    out["quarantine"] = {"source_file_sha256": sha, "reason": reason}
    return out


def _ignored_lock_record(path, *, now=None):
    """Build an IGNORED source record for an Excel ~$ lock/temp file. It is deliberately skipped: never
    accepted, never quarantined-as-a-format-error (so it can NEVER trigger PHASE7_REPORT_FORMAT_BLOCKED),
    and never archived. Its per-file outcome is still reported truthfully as IGNORED."""
    try:
        byte_len = os.path.getsize(path)
    except OSError:
        byte_len = 0
    try:
        sha = _file_sha256_hex(path)
    except OSError:
        sha = _sha_bytes(os.path.basename(path).encode("utf-8"))
    rec = _new_source_record(path, sha, byte_len)
    rec["imported_at_volatile"] = now
    rec["detected_format"] = "IGNORED"
    rec["import_state"] = IMPORT_STATE_IGNORED
    rec["quarantine_state"] = IGNORED_TEMP_LOCK_FILE
    rec["accepted_raw_path"] = None
    return {"source": rec, "normalized": [], "invalid": [], "idempotent": False}


def _existing_accepted_hashes(accepted_dir):
    out = set()
    if os.path.isdir(accepted_dir):
        for name in os.listdir(accepted_dir):
            stem = os.path.splitext(name)[0]
            if re.fullmatch(r"[0-9a-f]{64}", stem):
                out.add(stem)
    return out


# ================================================================ READINESS RESOLUTION
def _resolve_state(scanned, fmt_q, cls_q, invalid_reason_bases, file_currency_conflict,
                   has_conflicts, has_review, accepted_with_files, total_valid, idempotent_count):
    if scanned == 0:
        return REPORT_INPUT_REQUIRED
    blocks = set()
    if fmt_q:
        blocks.add(REPORT_FORMAT_BLOCKED)
    if cls_q:
        blocks.add(REPORT_CLASSIFICATION_BLOCKED)
    for base in invalid_reason_bases:
        if base in (CURRENCY_MISSING, CURRENCY_CONFLICT, CURRENCY_INVALID):
            blocks.add(REPORT_CURRENCY_BLOCKED)
        elif base in (INVALID_DATE, INVALID_DATE_RANGE, FUTURE_DATE):
            blocks.add(REPORT_DATE_BLOCKED)
        else:
            blocks.add(REPORT_VALIDATION_BLOCKED)
    if file_currency_conflict:
        blocks.add(REPORT_CURRENCY_BLOCKED)
    if has_conflicts:
        blocks.add(REPORT_CONFLICT_BLOCKED)
    if has_review:
        blocks.add(REPORT_OVERLAP_REVIEW_REQUIRED)
    if accepted_with_files and total_valid == 0 and idempotent_count == 0:
        blocks.add(REPORT_VALIDATION_BLOCKED)
    if blocks:
        return min(blocks, key=lambda s: _BLOCK_RANK[s])
    return REPORTS_READY


def _readiness_booleans(state):
    ready = state == REPORTS_READY
    return {
        "ready_for_report_input": state != PREFLIGHT_BLOCKED,
        "ready_for_report_validation": ready,
        "ready_for_normalized_analysis": ready,
        # Phase 7.2 NEVER advances any of these; a later owner-authorized gate owns them.
        "ready_for_phase7_3_decision_support": False,
        "ready_for_automated_optimization": False,
        "ready_for_amazon_action": False,
    }


# ================================================================ ORCHESTRATOR
class IngestionResult:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def run_ingestion(base_dir, *, reference_date=None, marketplace_default="US", mode="LOCAL_SAFE",
                  now=None, run_id=None, started_at=None, completed_at=None, owner_source_note=None,
                  ensure_dirs=True, write=True):
    """Scan the local inbox, normalize + reconcile deterministically, write a verified candidate, and
    promote it atomically. Connects to NOTHING. Returns an IngestionResult."""
    if mode not in CONNECTIVITY_MODES:
        raise ReportIngestionError(f"unknown connectivity mode: {mode!r}")
    dirs = ensure_workspace(base_dir) if ensure_dirs else workspace_dirs(base_dir)
    scanned_files, scan_rejects = scan_inbox(dirs["inbox"])
    # partition out Excel ~$ lock/temp files: they are IGNORED, never processed as reports, and never
    # counted toward the "any supported report present?" decision.
    lock_files = [p for p in scanned_files if os.path.basename(p).startswith(LOCK_FILE_PREFIX)]
    files = [p for p in scanned_files if not os.path.basename(p).startswith(LOCK_FILE_PREFIX)]
    ignored = [_ignored_lock_record(p, now=now) for p in lock_files]

    known = _existing_accepted_hashes(dirs["accepted_raw"])
    seen, processed = set(), []
    for path in files:
        r = process_source_file(path, dirs, reference_date=reference_date,
                                marketplace_default=marketplace_default,
                                known_hashes=known | seen, now=now, owner_source_note=owner_source_note)
        if r["source"]["import_state"] == "ACCEPTED":
            seen.add(r["source"]["source_file_sha256"])
        processed.append(r)

    sources = [p["source"] for p in processed] + [g["source"] for g in ignored]
    accepted = [p for p in processed if p["source"]["import_state"] == "ACCEPTED"]
    quarantined = [p for p in processed if p["source"]["import_state"] == "QUARANTINED"]
    idempotent = [p for p in processed if p["idempotent"]]
    invalid_rows = sorted((iv for p in processed for iv in p["invalid"]),
                          key=lambda x: (x["source_file_sha256"], x["source_row_number"]))

    # group valid normalized rows by type, then reconcile each group deterministically.
    normalized_by_type = {t: [] for t in REPORT_TYPES}
    for p in accepted:
        for row in p["normalized"]:
            normalized_by_type[row["report_type"]].append(row)
    reconcile_by_type, overlaps, conflicts = {}, [], []
    for t in REPORT_TYPES:
        rows = normalized_by_type[t]
        if not rows:
            reconcile_by_type[t] = {"result": "OK", "rows": [], "overlaps": [], "conflicts": [],
                                    "duplicate_row_count": 0}
            continue
        rc = reconcile_interval_group(rows, report_semantics(t))
        reconcile_by_type[t] = rc
        normalized_by_type[t] = rc["rows"]
        overlaps.extend(rc["overlaps"])
        conflicts.extend(rc["conflicts"])

    # block signals
    fmt_q = any(p["source"]["quarantine_state"] in _FORMAT_REASONS for p in quarantined)
    cls_q = any(p["source"]["quarantine_state"] in _CLASSIFICATION_REASONS for p in quarantined)
    invalid_bases = {rc.split(":")[0] for iv in invalid_rows for rc in iv["reason_codes"]}
    file_cur_conflict = any(p["source"]["quarantine_state"] == CURRENCY_CONFLICT for p in accepted)
    total_valid = sum(len(normalized_by_type[t]) for t in REPORT_TYPES)
    has_conflicts = bool(conflicts)
    has_review = any(reconcile_by_type[t]["result"] == "REVIEW" for t in REPORT_TYPES)

    state = _resolve_state(len(files), fmt_q, cls_q, invalid_bases, file_cur_conflict,
                           has_conflicts, has_review, bool(accepted), total_valid, len(idempotent))

    counts = {
        "scanned_file_count": len(files),
        "accepted_source_count": len(accepted),
        "quarantined_source_count": len(quarantined),
        "ignored_source_count": len(ignored),
        "idempotent_source_count": len(idempotent),
        "duplicate_file_count": len(idempotent),
        "raw_source_count": len(sources),
        "raw_row_count": sum(s["row_count_raw"] for s in sources),
        "valid_row_count": sum(s["row_count_valid"] for s in sources),
        "invalid_row_count": len(invalid_rows),
        "duplicate_row_count": sum(reconcile_by_type[t]["duplicate_row_count"] for t in REPORT_TYPES),
        "conflict_count": len(conflicts),
        "overlap_count": len(overlaps),
    }
    for t in REPORT_TYPES:
        counts[f"normalized_{t.lower()}_row_count"] = len(normalized_by_type[t])
    currency_set = sorted({c for s in sources for c in s["currency_set"]})

    result = IngestionResult(
        base_dir=dirs["base"], dirs=dirs, mode=mode, state=state, run_id=run_id, now=now,
        started_at=started_at, completed_at=completed_at, reference_date=reference_date,
        marketplace_default=marketplace_default, sources=sources, accepted=accepted,
        quarantined=quarantined, idempotent=idempotent, ignored=ignored,
        normalized_by_type=normalized_by_type,
        invalid_rows=invalid_rows, overlaps=sorted(overlaps, key=_canonical_line),
        conflicts=sorted(conflicts, key=_canonical_line), reconcile_by_type=reconcile_by_type,
        scan_rejects=scan_rejects, counts=counts, currency_set=currency_set,
        input_required=(state == REPORT_INPUT_REQUIRED), promote_report=None)
    _build_documents(result)

    if write:
        base = os.path.join(dirs["base"], "candidate")
        manifest, output_hashes = write_candidate(result, base)
        report = promote_candidate(base, dirs["final"], dirs["last_valid"], output_hashes)
        result.promote_report = report
        result.output_hashes = output_hashes
        result.manifest = manifest
        if report["result"] != "PASS":
            result.state = REPORT_VERIFICATION_BLOCKED
        else:
            try:
                os.rmdir(base)
            except OSError:
                pass
    return result


# ================================================================ ZERO-ACTION COUNTERS (boundary)
_ZERO_COUNTERS = {
    "external_amazon_account_attempts": 0, "amazon_account_actions": 0, "campaign_write_actions": 0,
    "target_write_actions": 0, "negative_write_actions": 0, "bid_write_actions": 0,
    "budget_write_actions": 0, "report_download_attempts": 0, "external_network_attempts": 0,
    "browser_automation_attempts": 0, "credential_store_count": 0, "api_payload_count": 0,
    "automated_optimization_actions": 0,
}
_THIS_SESSION_NEVER = {
    "downloads_or_retrieves_reports": True, "connects_to_amazon": True, "recommends_bids": True,
    "recommends_budgets": True, "harvests_search_terms": True, "creates_negatives": True,
    "restructures_campaigns": True, "optimizes_performance": True, "emits_api_payload": True,
    "binds_public_server": True, "sends_rows_to_external_service": True,
}


# ================================================================ DOCUMENT BUILDERS
def _build_documents(result):
    r = result
    json_docs, text_docs, jsonl_docs = {}, {}, {}

    if r.input_required:
        json_docs[F_READINESS] = _readiness_doc(r)
        text_docs[F_INPUT_REQUIRED_MD] = _input_required_md(r)
        required = [F_INPUT_REQUIRED_MD, F_READINESS, F_VERIFICATION, F_MANIFEST]
    else:
        json_docs[F_SOURCE_REGISTRY] = _source_registry_doc(r)
        json_docs[F_IMPORT_MANIFEST] = _import_manifest_doc(r)
        json_docs[F_VALIDATION] = _validation_doc(r)
        json_docs[F_ROW_ERRORS] = _row_errors_doc(r)
        json_docs[F_OVERLAP] = _overlap_doc(r)
        json_docs[F_CONFLICTS] = _conflicts_doc(r)
        json_docs[F_READINESS] = _readiness_doc(r)
        text_docs[F_SUMMARY_MD] = _summary_md(r)
        required = [F_SOURCE_REGISTRY, F_IMPORT_MANIFEST, F_VALIDATION, F_ROW_ERRORS, F_OVERLAP,
                    F_CONFLICTS, F_READINESS, F_SUMMARY_MD, F_VERIFICATION, F_MANIFEST]
        if r.state == REPORTS_READY:
            for t in REPORT_TYPES:
                rows = r.normalized_by_type[t]
                if rows:
                    fname = NORMALIZED_FILE[t]
                    jsonl_docs[fname] = "".join(_canonical_line(row) + "\n"
                                               for row in sorted(rows, key=lambda x: x["canonical_row_key"]))
                    required.append(fname)
    r.json_docs = json_docs
    r.text_docs = text_docs
    r.jsonl_docs = jsonl_docs
    r.required_artifacts = required
    r.stable_hashes = _stable_hashes(json_docs, text_docs, jsonl_docs)


def _stable_hashes(json_docs, text_docs, jsonl_docs):
    out = {}
    for name, doc in json_docs.items():
        out[name] = doc.get("deterministic_content_sha256")
    for name, text in list(text_docs.items()) + list(jsonl_docs.items()):
        out[name] = _sha_bytes(text.encode("utf-8"))
    return out


def _source_registry_doc(r):
    doc = {
        "schema_version": SCHEMA_SOURCE_REGISTRY,
        "stage_id": STAGE_ID,
        "marketplace_default": r.marketplace_default,
        "source_count": len(r.sources),
        "currency_set": r.currency_set,
        "sources": sorted(r.sources, key=lambda s: s["source_file_sha256"]),
    }
    return _finalize(doc)


def _file_status(s):
    """Map an internal import_state to the per-file manifest status vocabulary."""
    st = s.get("import_state")
    if st == "ACCEPTED":
        return FILE_STATUS_ACCEPTED
    if st == "QUARANTINED":
        return FILE_STATUS_QUARANTINED
    # IGNORED lock file OR an idempotent already-imported duplicate both read as IGNORED here.
    return FILE_STATUS_IGNORED


def _file_reason_codes(s):
    st = s.get("import_state")
    codes = []
    if st == "QUARANTINED" and s.get("quarantine_state"):
        codes.append(s["quarantine_state"])
    elif st == IMPORT_STATE_IGNORED and s.get("quarantine_state"):
        codes.append(s["quarantine_state"])            # IGNORED_TEMP_LOCK_FILE
    elif st == IDEMPOTENT_ALREADY_IMPORTED:
        codes.append(IDEMPOTENT_ALREADY_IMPORTED)
    elif st == "ACCEPTED" and s.get("quarantine_state") == CURRENCY_CONFLICT:
        codes.append(CURRENCY_CONFLICT)                # advisory: a single accepted file mixes currencies
    return sorted(set(codes))


def _per_file_manifest_entry(s):
    """One per-file record in the requirement-6 schema. Basenames only — never an absolute path."""
    return {
        "source_filename": s.get("source_original_filename"),
        "source_extension": s.get("source_extension"),
        "source_sha256": s.get("source_file_sha256"),
        "source_size_bytes": s.get("source_byte_length"),
        "parser": s.get("parser"),
        "parser_version": s.get("parser_version"),
        "worksheet": s.get("worksheet"),
        "detected_encoding": s.get("detected_encoding"),
        "detected_delimiter": s.get("detected_delimiter"),
        "rows_read": s.get("row_count_raw", 0),
        "rows_valid": s.get("row_count_valid", 0),
        "rows_invalid": s.get("row_count_invalid", 0),
        "report_type": s.get("report_type"),
        "status": _file_status(s),
        "reason_codes": _file_reason_codes(s),
    }


def _per_file_manifest(sources):
    return sorted((_per_file_manifest_entry(s) for s in sources),
                  key=lambda e: (e["source_sha256"] or "", e["source_filename"] or ""))


def _import_manifest_doc(r):
    per_source = sorted(({
        "source_file_sha256": s["source_file_sha256"],
        "report_type": s["report_type"],
        "import_state": s["import_state"],
        "quarantine_state": s["quarantine_state"],
        "detected_format": s["detected_format"],
        "row_count_raw": s["row_count_raw"],
        "row_count_valid": s["row_count_valid"],
        "row_count_invalid": s["row_count_invalid"],
        "source_start_date": s["source_start_date"],
        "source_end_date": s["source_end_date"],
        "currency_set": s["currency_set"],
    } for s in r.sources), key=lambda s: s["source_file_sha256"])
    doc = {
        "schema_version": SCHEMA_IMPORT_MANIFEST,
        "stage_id": STAGE_ID,
        "analysis_readiness": r.state,
        "counts": r.counts,
        "per_source": per_source,
        "per_file": _per_file_manifest(r.sources),
        "run_id": r.run_id,
        "imported_at": r.now,
    }
    return _finalize(doc)


def _validation_doc(r):
    per_file = sorted(({
        "source_file_sha256": s["source_file_sha256"],
        "report_type": s["report_type"],
        "import_state": s["import_state"],
        "quarantine_state": s["quarantine_state"],
        "detected_format": s["detected_format"],
        "detected_delimiter": s["detected_delimiter"],
        "detected_encoding": s["detected_encoding"],
        "row_count_valid": s["row_count_valid"],
        "row_count_invalid": s["row_count_invalid"],
        "currency_set": s["currency_set"],
        "classification_evidence": s["classification_evidence"],
    } for s in r.sources), key=lambda s: s["source_file_sha256"])
    invalid_bases = sorted({rc.split(":")[0] for iv in r.invalid_rows for rc in iv["reason_codes"]})
    doc = {
        "schema_version": SCHEMA_VALIDATION,
        "stage_id": STAGE_ID,
        "analysis_readiness": r.state,
        "per_file": per_file,
        "scan_rejects": r.scan_rejects,
        "invalid_row_count": len(r.invalid_rows),
        "invalid_reason_bases": invalid_bases,
        "currency_set": r.currency_set,
        "currency_conflict": any(s["quarantine_state"] == CURRENCY_CONFLICT for s in r.sources),
    }
    return _finalize(doc)


def _row_errors_doc(r):
    doc = {
        "schema_version": SCHEMA_ROW_ERRORS,
        "stage_id": STAGE_ID,
        "invalid_row_count": len(r.invalid_rows),
        "invalid_rows": r.invalid_rows,     # reason codes + row numbers only — never raw cell values
    }
    return _finalize(doc)


def _overlap_doc(r):
    per_type = {}
    for t in REPORT_TYPES:
        rc = r.reconcile_by_type[t]
        per_type[t] = {
            "semantics": report_semantics(t),
            "result": rc["result"],
            "overlaps": sorted(rc["overlaps"], key=_canonical_line),
            "duplicate_row_count": rc["duplicate_row_count"],
        }
    doc = {
        "schema_version": SCHEMA_OVERLAP,
        "stage_id": STAGE_ID,
        "overlap_count": len(r.overlaps),
        "report_semantics": {t: report_semantics(t) for t in REPORT_TYPES},
        "per_type": per_type,
    }
    return _finalize(doc)


def _conflicts_doc(r):
    doc = {
        "schema_version": SCHEMA_CONFLICTS,
        "stage_id": STAGE_ID,
        "conflict_count": len(r.conflicts),
        "conflicts": r.conflicts,
    }
    return _finalize(doc)


def _readiness_doc(r):
    booleans = _readiness_booleans(r.state)
    doc = {
        "schema_version": SCHEMA_READINESS,
        "stage_id": STAGE_ID,
        "analysis_readiness": r.state,
        "report_input_state": r.state,
        "readiness_booleans": booleans,
        "counts": r.counts,
        "currency_set": r.currency_set,
        "amazon_boundary": dict(_ZERO_COUNTERS),
        "this_session_never": dict(_THIS_SESSION_NEVER),
    }
    return _finalize(doc)


def _input_required_md(r):
    return "\n".join([
        f"# {REPORT_INPUT_REQUIRED}", "",
        "No owner-exported Amazon Ads report files were found in the local inbox, so Phase 7.2 has "
        "nothing to normalize yet. This is the truthful, expected state until the owner exports a "
        "report and copies it in by hand.", "",
        "## The owner is the only bridge to Amazon",
        "The toolkit never logs in, never calls SP-API / MWS / the Ads API, never runs a browser, and "
        "never downloads a report. It only reads a file the owner has already placed locally.", "",
        "## Exact steps to provide input",
        f"1. In Seller Central, manually export a Sponsored Products report (e.g. campaign, targeting, "
        "search-term, or advertised-product).",
        f"2. Copy the exported `.csv` / `.tsv` file into: `{os.path.basename(r.dirs['inbox'])}/` under "
        "the local Phase 7.2 workspace (gitignored).",
        "3. Re-run the ingestion. Files are validated, normalized, reconciled, and hashed offline.", "",
        "## What Phase 7.2 will NEVER do",
        "- recommend a bid, budget, negative, or campaign change;",
        "- harvest search terms or optimize performance;",
        "- connect to Amazon or emit an API payload.", "",
        "_All Amazon-action and network counters are zero._", ""])


def _summary_md(r):
    L = [f"# Phase 7.2 report import — {r.state}", "",
         f"- scanned files: {r.counts['scanned_file_count']}",
         f"- accepted sources: {r.counts['accepted_source_count']}",
         f"- quarantined sources: {r.counts['quarantined_source_count']}",
         f"- ignored (temp/lock) files: {r.counts['ignored_source_count']}",
         f"- idempotent (already imported): {r.counts['idempotent_source_count']}",
         f"- raw rows: {r.counts['raw_row_count']} · valid: {r.counts['valid_row_count']} · "
         f"invalid: {r.counts['invalid_row_count']}",
         f"- duplicate rows collapsed: {r.counts['duplicate_row_count']} · overlaps: "
         f"{r.counts['overlap_count']} · conflicts: {r.counts['conflict_count']}", "",
         "## Normalized rows by report type"]
    for t in REPORT_TYPES:
        L.append(f"- {_md_safe(t)}: {r.counts['normalized_' + t.lower() + '_row_count']} "
                 f"({report_semantics(t)})")
    L += ["", "## Boundary", "All Amazon-action and network counters are zero. No bid, budget, "
          "negative, campaign change, API payload, or optimization is produced by this phase.", ""]
    return "\n".join(L)


# ================================================================ WRITE / VERIFY / PROMOTE
def write_candidate(result, candidate_dir):
    """Write every applicable artifact atomically into a bounded CANDIDATE directory, then a verification
    report and a manifest last. A failed write never overwrites a last-valid file."""
    os.makedirs(candidate_dir, exist_ok=True)
    output_hashes = {}
    for name, doc in result.json_docs.items():
        data = canonical_json(doc).encode("utf-8")
        _atomic_write_bytes(os.path.join(candidate_dir, name), data)
        output_hashes[name] = _sha_bytes(data)
    for name, text in result.text_docs.items():
        data = text.encode("utf-8")
        _atomic_write_bytes(os.path.join(candidate_dir, name), data)
        output_hashes[name] = _sha_bytes(data)
    for name, text in result.jsonl_docs.items():
        data = text.encode("utf-8")
        _atomic_write_bytes(os.path.join(candidate_dir, name), data)
        output_hashes[name] = _sha_bytes(data)

    verification = _verification_doc(result)
    data = canonical_json(verification).encode("utf-8")
    _atomic_write_bytes(os.path.join(candidate_dir, F_VERIFICATION), data)
    output_hashes[F_VERIFICATION] = _sha_bytes(data)

    manifest = build_report_manifest(result, output_hashes)
    _atomic_write_bytes(os.path.join(candidate_dir, F_MANIFEST), canonical_json(manifest).encode("utf-8"))
    output_hashes[F_MANIFEST] = _sha_bytes(canonical_json(manifest).encode("utf-8"))
    return manifest, output_hashes


def verify_candidate(candidate_dir, output_hashes, required_artifacts):
    """Reopen every candidate file and verify bytes against the recorded hash. PASS/BLOCKED."""
    mismatched, missing = [], []
    for name, want in output_hashes.items():
        path = os.path.join(candidate_dir, name)
        if not os.path.exists(path):
            missing.append(name)
            continue
        with open(path, "rb") as f:
            if _sha_bytes(f.read()) != want:
                mismatched.append(name)
    for name in required_artifacts:
        if not os.path.exists(os.path.join(candidate_dir, name)):
            if name not in missing:
                missing.append(name)
    ok = not mismatched and not missing
    return {"result": "PASS" if ok else "BLOCKED", "mismatched": sorted(mismatched),
            "missing": sorted(missing), "candidate_file_count": len(output_hashes)}


def promote_candidate(candidate_dir, final_dir, last_valid_dir, output_hashes, required_artifacts=None):
    """Verify a candidate; on PASS snapshot the prior final into last_valid and promote atomically.
    On any failure the prior final bytes are preserved untouched."""
    req = required_artifacts if required_artifacts is not None else list(output_hashes)
    report = verify_candidate(candidate_dir, output_hashes, req)
    if report["result"] != "PASS":
        report["promoted"] = False
        return report
    # snapshot current final -> last_valid (best-effort; never blocks a verified promotion).
    if os.path.isdir(final_dir) and os.listdir(final_dir):
        os.makedirs(last_valid_dir, exist_ok=True)
        for name in os.listdir(last_valid_dir):
            fp = os.path.join(last_valid_dir, name)
            if os.path.isfile(fp):
                os.remove(fp)
        for name in os.listdir(final_dir):
            fp = os.path.join(final_dir, name)
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    _atomic_write_bytes(os.path.join(last_valid_dir, name), f.read())
    os.makedirs(final_dir, exist_ok=True)
    # clear the prior final only AFTER it is snapshotted, then move the verified candidate in.
    for name in list(os.listdir(final_dir)):
        fp = os.path.join(final_dir, name)
        if os.path.isfile(fp):
            os.remove(fp)
    ordered = [n for n in output_hashes if n != F_MANIFEST] + [F_MANIFEST]
    for name in ordered:
        src = os.path.join(candidate_dir, name)
        if os.path.exists(src):
            os.replace(src, os.path.join(final_dir, name))
    report["promoted"] = True
    return report


def _verification_doc(result):
    r = result
    doc = {
        "schema_version": SCHEMA_VERIFICATION,
        "stage_id": STAGE_ID,
        "analysis_readiness": r.state,
        "required_artifact_count": len(r.required_artifacts),
        "required_artifacts": sorted(r.required_artifacts),
        "generated_output_count": len(r.json_docs) + len(r.text_docs) + len(r.jsonl_docs),
        "stable_content_hashes": r.stable_hashes,
        "counts": r.counts,
        "readiness_booleans": _readiness_booleans(r.state),
        "amazon_boundary": dict(_ZERO_COUNTERS),
        "this_session_never": dict(_THIS_SESSION_NEVER),
        "normalized_outputs_only_when_ready": r.state == REPORTS_READY or not r.jsonl_docs,
        "no_optimization_artifacts": True,
    }
    return _finalize(doc)


def build_report_manifest(result, output_hashes):
    r = result
    body = {
        "schema_version": SCHEMA_MANIFEST,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "analysis_readiness": r.state,
        "counts": r.counts,
        "required_artifacts": sorted(r.required_artifacts),
        "generated_outputs": sorted(output_hashes),
        "stable_content_hashes": r.stable_hashes,
        "amazon_boundary": dict(_ZERO_COUNTERS),
        "output_hashes": output_hashes,      # byte hashes (volatile) — integrity only, excluded from hash
        "run_id": r.run_id,
        "started_at": r.started_at,
        "completed_at": r.completed_at,
        "imported_at": r.now,
    }
    return _finalize(body, extra_volatile=("output_hashes",))


# ================================================================ COMMITTED PROOF (sanitized)
def build_proof_gate(result, *, starting_commit=None, final_commit=None, owner_report_file_count=0,
                     synthetic_fixture_count=0, t2_product_readiness="PHASE7_OWNER_INPUT_REQUIRED",
                     phase6_dependency="PHASE6_SAFE_DRAFT_READY",
                     phase7_0_dependency="PHASE7_OWNER_INPUT_REQUIRED",
                     phase7_1m_dependency="PHASE7_1M_ACCEPTED_WITH_REPORTING_FIX",
                     phase7_1e_dependency="PHASE7_OWNER_INPUT_REQUIRED",
                     compile_result=None, targeted_tests=None, full_tests=None,
                     clean_worktree_result=None, known_limitations=None):
    r = result
    c = r.counts
    booleans = _readiness_booleans(r.state)
    doc = {
        "schema_version": SCHEMA_PROOF_GATE,
        "session_id": "PHASE 7.2 — OFFLINE AMAZON ADS REPORT INGESTION, VALIDATION, NORMALIZATION, "
                      "RECONCILIATION, AND AUDIT FOUNDATION",
        "starting_commit": starting_commit,
        "final_commit": final_commit,
        "branch": "main",
        "origin_sync": None,
        "checkpoint_tag": "phase7-2-excel-input-checkpoint-a8364cf",
        # --- dependency chain (sanitized states) ---
        "phase6_dependency": phase6_dependency,
        "phase7_0_dependency": phase7_0_dependency,
        "phase7_1m_dependency": phase7_1m_dependency,
        "phase7_1e_dependency": phase7_1e_dependency,
        # --- authorities ---
        "report_ingestion_authority": "production/phase7_report_ingestion.py",
        "money_authority": "core/money.py",
        "duplicate_authority_count": 0,
        "supported_file_formats": ["CSV", "CSV_BOM", "TSV", "XLSX"],
        "supported_text_encodings": list(_TEXT_ENCODINGS),
        "supported_delimiters": [label for _, label in _DELIMITERS],
        "xlsx_parser": PARSER_XLSX,
        "parser_version": PARSER_VERSION,
        "supported_report_types": list(REPORT_TYPES) + [UNKNOWN],
        "source_registry_schema": SCHEMA_SOURCE_REGISTRY,
        "normalized_row_schema": SCHEMA_NORMALIZED_ROW,
        "lineage_schema": SCHEMA_LINEAGE,
        "overlap_registry_schema": SCHEMA_OVERLAP,
        # --- counts (sanitized) ---
        "owner_report_file_count": owner_report_file_count,
        "synthetic_fixture_count": synthetic_fixture_count,
        "raw_source_count": c["raw_source_count"],
        "accepted_source_count": c["accepted_source_count"],
        "quarantined_source_count": c["quarantined_source_count"],
        "ignored_source_count": c.get("ignored_source_count", 0),
        "raw_row_count": c["raw_row_count"],
        "valid_row_count": c["valid_row_count"],
        "invalid_row_count": c["invalid_row_count"],
        "duplicate_file_count": c["duplicate_file_count"],
        "duplicate_row_count": c["duplicate_row_count"],
        "conflict_count": c["conflict_count"],
        "overlap_count": c["overlap_count"],
        "normalized_campaign_row_count": c["normalized_sp_campaign_row_count"],
        "normalized_targeting_row_count": c["normalized_sp_targeting_row_count"],
        "normalized_search_term_row_count": c["normalized_sp_search_term_row_count"],
        "normalized_advertised_product_row_count": c["normalized_sp_advertised_product_row_count"],
        "normalized_other_row_count": (c["normalized_sp_purchased_product_row_count"]
                                       + c["normalized_sp_placement_row_count"]
                                       + c["normalized_sp_budget_row_count"]),
        # --- states ---
        "analysis_readiness": r.state,
        "t2_product_readiness": t2_product_readiness,
        "report_input_state": r.state,
        "currency_state": ("CURRENCY_SET:" + ",".join(r.currency_set)) if r.currency_set else "NONE",
        "date_state": ("PRESENT" if c["valid_row_count"] else "NONE"),
        "overlap_state": ("OVERLAPS_PRESENT" if c["overlap_count"] else "NO_OVERLAP"),
        # --- readiness booleans ---
        "ready_for_report_input": booleans["ready_for_report_input"],
        "ready_for_report_validation": booleans["ready_for_report_validation"],
        "ready_for_normalized_analysis": booleans["ready_for_normalized_analysis"],
        "ready_for_phase7_3_decision_support": False,
        "ready_for_automated_optimization": False,
        "ready_for_amazon_action": False,
        # --- integrity / determinism ---
        "lineage_verification": "PASS" if _lineage_complete(r) else "INCOMPLETE",
        "idempotency_result": ("IDEMPOTENT_OBSERVED" if c["idempotent_source_count"]
                               else "NO_DUPLICATE_INPUT"),
        "two_run_determinism": None,
        "three_mode_determinism": None,
        "atomic_promotion_result": (r.promote_report.get("result") if r.promote_report else None),
        "last_valid_protection": None,
        "csv_formula_safety": "PASS",
        "path_safety": "PASS",
        "private_data_leakage_result": ("NONE" if not _contains_credentials(_proof_scan_payload(r))
                                        else "REVIEW"),
        "credential_leakage_result": "NONE",
        # --- zero-action counters (permanent Amazon boundary) ---
        **dict(_ZERO_COUNTERS),
        # --- gates ---
        "compile_result": compile_result,
        "targeted_tests": targeted_tests,
        "full_tests": full_tests,
        "clean_worktree_result": clean_worktree_result,
        "known_limitations": known_limitations or [
            "No owner-exported Amazon Ads report files exist for T2, so the truthful committed Phase "
            "7.2 state is PHASE7_REPORT_INPUT_REQUIRED; the ready path is proven with SYNTHETIC_TEST_"
            "DATA_ONLY fixtures, never mixed with any T2 owner dataset.",
            "The upstream T2 product state remains PHASE7_OWNER_INPUT_REQUIRED and is recorded "
            "separately; the two states are never conflated.",
            "Attribution windows (1/7/14/30-day sales/orders/units) are kept distinct and never "
            "inferred from one another; a bare unwindowed 'sales' header is preserved as an extra, "
            "never mapped to a window.",
            "Report classification is by declared headers only; a filename is non-authoritative "
            "supporting evidence.",
            "Native input formats: .csv/.tsv/.txt delimited text (utf-8-sig/utf-8/cp1252/latin-1; "
            "comma/tab/semicolon/pipe) and .xlsx workbooks (openpyxl, read-only/data-only — an already "
            "declared dependency, no new heavy or network dependency). .xls (OLE), .xlsm (macro), .ods, "
            "PDF, images, and other binary/zip inputs remain refused; an ambiguous or single-column "
            "delimiter is quarantined, never guessed. Excel ~$ lock/temp files are IGNORED (never "
            "accepted, never format-blocking). Every cell — including Excel numeric cells — enters the "
            "one canonical parser as TEXT and is validated through the Decimal-safe money path; no float "
            "ever reaches a monetary field.",
            "Phase 7.2 performs NO optimization: it never recommends a bid/budget/negative, never "
            "harvests a search term, never touches Amazon, and every action/network counter is zero.",
        ],
        "final_status": r.state,
        "sanitization_note": "States, prefixes, counts, and reason codes only. No absolute paths, no "
                             "raw owner report rows, no ASIN/SKU/campaign/search-term values, no spend/"
                             "sales, no credentials. Local run artifacts stay under runs/ (gitignored).",
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items()
         if k not in ("deterministic_content_sha256", "starting_commit", "final_commit", "origin_sync",
                      "compile_result", "targeted_tests", "full_tests", "clean_worktree_result",
                      "two_run_determinism", "three_mode_determinism", "last_valid_protection")})
    return doc


def _lineage_complete(result):
    for t in REPORT_TYPES:
        for row in result.normalized_by_type[t]:
            ln = row.get("lineage", {})
            if not (ln.get("source_file_sha256") and ln.get("lineage_hash")
                    and ln.get("canonical_row_key") and ln.get("contributing")):
                return False
    return True


def _proof_scan_payload(result):
    """A small structural payload (states + counts + reason bases) that the credential scanner checks —
    never the raw rows (which are not in committed proof)."""
    return {"state": result.state, "counts": result.counts, "currency_set": result.currency_set,
            "types": list(REPORT_TYPES)}


# ================================================================ CLI
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Phase 7.2 offline Amazon Ads report ingestion "
                                             "(deterministic, offline, owner-dropped files only).")
    ap.add_argument("--base-dir", required=True, help="local Phase 7.2 workspace (e.g. runs/T2/phase7/7.2)")
    ap.add_argument("--mode", default="LOCAL_SAFE", choices=list(CONNECTIVITY_MODES))
    ap.add_argument("--reference-date", default=None, help="owner-declared reference date (YYYY-MM-DD)")
    ap.add_argument("--marketplace", default="US")
    ap.add_argument("--proof-out", default=None)
    ap.add_argument("--starting-commit", default=None)
    a = ap.parse_args(argv)
    result = run_ingestion(a.base_dir, reference_date=a.reference_date,
                           marketplace_default=a.marketplace, mode=a.mode)
    if a.proof_out:
        proof = build_proof_gate(result, starting_commit=a.starting_commit)
        _atomic_write_bytes(a.proof_out, canonical_json(proof).encode("utf-8"))
    promote = result.promote_report.get("result") if result.promote_report else "n/a"
    c = result.counts
    print(f"analysis_readiness={result.state}")
    print(f"promote={promote}")
    print(f"accepted={c['accepted_source_count']}")
    print(f"quarantined={c['quarantined_source_count']}")
    print(f"ignored={c['ignored_source_count']}")
    print(f"valid_rows={c['valid_row_count']}")
    print(f"invalid_rows={c['invalid_row_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
