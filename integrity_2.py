""" Log Integrity Monitor — Streaming-Based Temporal Anomaly Analysis
=================================================================
Layered backend-safe architecture:
  INPUT LAYER    → CLI argument parsing + file resolution
  SERVICE LAYER  → analyze_log() — the single reusable entry point
  PARSING LAYER  → TimestampParser, GapDetectionEngine, ForensicScorer, …
  REPORTING LAYER→ ReportGenerator (terminal + CSV + JSON output)

`analyze_log() is fully backend/API friendly:
  • accepts any file-like object (open file, BytesIO, uploaded stream)
  • returns a structured dict — no print, no input(), no CLI dependency
  • safe to call from Flask, FastAPI, Django, or any other framework
"""

import re
import csv
import sys
import json
import time
import logging
import argparse
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Generator, NamedTuple
from pathlib import Path
import io

# ── Module-level logger — operational messages only (not user-facing report) ─
logger = logging.getLogger(__name__)

# ============================================================================
# STDOUT / STDERR UTF-8 BOOTSTRAP
# ============================================================================
def _force_utf8_stdout_stderr() -> None:
    """Ensure stdout/stderr can encode Unicode on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                if stream is sys.stdout:
                    sys.stdout = io.TextIOWrapper(
                        sys.stdout.buffer, encoding="utf-8",
                        errors="replace", line_buffering=True,
                    )
                else:
                    sys.stderr = io.TextIOWrapper(
                        sys.stderr.buffer, encoding="utf-8",
                        errors="replace", line_buffering=True,
                    )
            except Exception:
                pass


# ============================================================================
# CONFIGURATION — centralized immutable config dataclass
# ============================================================================
@dataclass(frozen=True)
class Config:
    """Immutable configuration for the detection pipeline.

    All fields carry sensible defaults and can be overridden at the call
    site (CLI args → `analyze_log() kwargs).  The frozen dataclass
    guarantees that no code path accidentally mutates shared config state.
    """
    threshold_seconds:      int = 60    # Minimum gap (s) to flag
    high_threshold_seconds: int = 3600  # Gaps longer than this → HIGH
    medium_threshold_seconds: int = 600 # Gaps longer than this → MEDIUM
    max_gaps:               int = 0     # 0 = unlimited
    year_cutoff:            int = 70    # YY < 70 → 20YY, else 19YY
    hash_length:            int = 16    # Hex chars kept from SHA-256
    gap_density_scale:      int = 1000  # Gaps per N lines for density metric

# Singleton default — used wherever an explicit config is not supplied
DEFAULT_CONFIG = Config()

# ── Log file path — set this to run without a CLI argument ─────────────────
LOG_FILE = "HDFS_2k.log.txt"  # Set your log file path here (e.g., "server.log")

# Convenience aliases kept for backward-compat references inside this module
THRESHOLD_SECONDS          = DEFAULT_CONFIG.threshold_seconds
HIGH_SEVERITY_THRESHOLD    = DEFAULT_CONFIG.high_threshold_seconds
MEDIUM_SEVERITY_THRESHOLD  = DEFAULT_CONFIG.medium_threshold_seconds
MAX_GAPS                   = DEFAULT_CONFIG.max_gaps
YEAR_CUTOFF                = DEFAULT_CONFIG.year_cutoff
GAP_DENSITY_SCALE          = DEFAULT_CONFIG.gap_density_scale
HASH_LENGTH                = DEFAULT_CONFIG.hash_length
DEFAULT_ASSUMED_TZ: timezone | None = None  # None = treat naive timestamps as UTC

# ── Forensic scoring penalty caps ──────────────────────────────────────────
# Maximum points deducted per risk factor (must sum ≤ 100).
MAX_GAP_PENALTY      = 40   # Penalty for high gap density
MAX_MALFORM_PENALTY  = 25   # Penalty for high malformed-line ratio
MAX_JUMP_PENALTY     = 20   # Penalty for backward time-jump events
MAX_SEVERITY_PENALTY = 15   # Penalty for HIGH / MEDIUM severity distribution

# ── Forensic risk-level score thresholds (inclusive lower bounds) ───────────
LOW_RISK_SCORE_THRESHOLD      = 80  # score ≥ 80 → LOW risk
MODERATE_RISK_SCORE_THRESHOLD = 60  # score ≥ 60 → MODERATE risk
HIGH_RISK_SCORE_THRESHOLD     = 40  # score ≥ 40 → HIGH risk
                                    # score < 40 → CRITICAL risk

# ── HIGH-severity gap weight inside severity penalty ───────────────────────
SEVERITY_HIGH_WEIGHT   = 3  # Points per HIGH gap
SEVERITY_MEDIUM_WEIGHT = 1  # Points per MEDIUM gap

# UTC reference for all normalisation
UTC = timezone.utc


# ============================================================================
# TIMEZONE UTILITIES
# ============================================================================
def parse_tz_offset(tz_string: str) -> timezone:
    """Parse a UTC offset string into a `datetime.timezone object.

    Accepted formats (case-insensitive):
      • `UTC / Z           → UTC±00:00
      • `UTC+5:30 / UTC-8  → signed offset
      • `+05:30 / -08:00 / +05 → bare signed offset
      • `EST / IST / PST etc.  → well-known abbreviations

    Args:
        tz_string: Human-readable timezone string from CLI or log line.

    Returns:
        A `datetime.timezone instance representing the fixed offset.

    Raises:
        ValueError: If the string cannot be parsed into a known offset.
    """
    # ── Common abbreviation map (non-DST / fixed offsets only) ────────────
    ABBREV_MAP: dict[str, int] = {
        "UTC": 0, "GMT": 0, "Z": 0,
        "EST": -300, "EDT": -240, "CST": -360, "CDT": -300,
        "MST": -420, "MDT": -360, "PST": -480, "PDT": -420,
        "IST": 330, "BST": 60, "CET": 60, "CEST": 120,
        "EET": 120, "EEST": 180, "JST": 540, "KST": 540,
        "HKT": 480, "SGT": 480, "MSK": 180,
        "WIB": 420, "WIT": 540, "WITA": 480,
        "AEST": 600, "AEDT": 660, "NZST": 720, "NZDT": 780,
    }
    s    = tz_string.strip().upper()
    bare = re.sub(r"^(UTC|GMT)(?=[+-])", "", s)
    if bare in ABBREV_MAP:
        return timezone(timedelta(minutes=ABBREV_MAP[bare]))
    match = re.fullmatch(r"([+-])(\d{1,2})(?::?(\d{2}))?", bare)
    if match:
        sign    = 1 if match.group(1) == "+" else -1
        hours   = int(match.group(2))
        minutes = int(match.group(3)) if match.group(3) else 0
        total   = sign * (hours * 60 + minutes)
        if not (-14 * 60 <= total <= 14 * 60):
            raise ValueError(f"UTC offset out of valid range ±14h: {tz_string!r}")
        return timezone(timedelta(minutes=total))
    raise ValueError(
        f"Unrecognised timezone: {tz_string!r}. "
        "Use formats like UTC, UTC+5:30, +05:30, IST, EST, JST, etc."
    )


def to_utc(dt: datetime) -> datetime:
    """Convert *dt* to UTC, returning a timezone-aware datetime."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def attach_assumed_tz(dt: datetime, assumed_tz: timezone | None) -> datetime:
    """Attach *assumed_tz* to a naive *dt* without converting its wall-clock value."""
    if dt.tzinfo is not None:
        return dt
    if assumed_tz is None:
        return dt
    return dt.replace(tzinfo=assumed_tz)


# ============================================================================
# PARSING LAYER — TimestampParser
# ============================================================================
class TimestampParser:
    """Extensible parsing layer that supports multiple timestamp formats.

    Formats registered by default:
      • `YYMMDD HHMMSS              (compact numeric, CCTV / DB logs)
      • `YYYY-MM-DD HH:MM:SS        (ISO 8601, naive)
      • `YYYY-MM-DD HH:MM:SS±HH:MM  (ISO 8601 with UTC offset)
      • `YYYY-MM-DDTHH:MM:SSZ       (ISO 8601 UTC / T-separator)
      • `DD/Mon/YYYY:HH:MM:SS ±HHMM (Apache / Nginx combined log)
      • `MM/DD/YYYY HH:MM:SS        (US-style slash, naive)

    Timezone-aware results carry an explicit `tzinfo; naive results do not.
    Normalisation to UTC is the responsibility of `GapDetectionEngine.
    Additional formats can be registered at runtime via `register_pattern.

    Performance note
    ----------------
    `parse() uses fast preclassification on character positions before
    attempting any regex, cutting the average number of regex engine
    invocations per line from 4 (worst case) to 1 for homogeneous log
    files.

    OPT-1: re.match() replaces re.search() for all patterns whose
    timestamps appear at the start of the stripped line (YYMMDD, ISO
    families).  Apache logs use re.search() because the timestamp is
    bracketed after an IP address prefix.

    OPT-2: The O(n²) fallback guard `any(pattern is cp …)` is replaced
    with a precomputed frozenset for O(1) membership testing.
    """

    # ── Compiled patterns ──────────────────────────────────────────────────
    # OPT-1: match() anchors these to the start of the stripped line,
    # avoiding a full-string scan that search() would perform.
    PATTERN_YYMMDDHHMMSS = re.compile(r"(\d{6})\s+(\d{6})\s+\d+")
    PATTERN_ISO_OFFSET   = re.compile(
        r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
        r"\s*([+-]\d{2}:?\d{2}|Z)"
    )
    # FIX: The original pattern used [+-Z] which is an ASCII range from '+' (43)
    # to 'Z' (90), accidentally including all uppercase letters A–Z and digits.
    # The negative lookahead (?!\s*[+-Z]) therefore fired even when the timestamp
    # was followed by a log level like "INFO" or "WARN", causing every ISO naive
    # timestamp to silently fail to parse.
    # Correct form uses a non-capturing alternation: (?:[+-]|Z) which matches
    # only the literal '+', '-', or 'Z' characters.
    PATTERN_ISO_NAIVE    = re.compile(
        r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?!\s*(?:[+-]|Z))"
    )
    # Apache: timestamp is embedded after "IP - - [", so search() is required.
    PATTERN_APACHE       = re.compile(
        r"(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2})"
        r"\s+([+-]\d{4})"
    )
    # US-style slash naive: 03/15/2024 14:22:01
    # use_match=True — timestamp always at line start.
    PATTERN_SLASH_NAIVE  = re.compile(
        r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2}):(\d{2})(?!\s*(?:[+-]|Z))"
    )

    _MONTH_MAP: dict[str, int] = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "may": 5, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    def __init__(self) -> None:
        # Full pattern list (offset-aware patterns precede naive ones).
        # Tuples: (compiled_pattern, parser_func, use_match)
        # use_match=True  → re.match() (anchor to line start, faster)
        # use_match=False → re.search() (scan whole line, Apache-style)
        self.patterns = [
            (self.PATTERN_ISO_OFFSET,   self._parse_iso_offset,   True),
            (self.PATTERN_APACHE,       self._parse_apache,       False),
            (self.PATTERN_YYMMDDHHMMSS, self._parse_yymmddhhmmss, True),
            (self.PATTERN_ISO_NAIVE,    self._parse_iso_naive,    True),
            (self.PATTERN_SLASH_NAIVE,  self._parse_slash_naive,  True),
        ]

        # ── Fast-dispatch lookup tables (PERF-OPT A) ───────────────────────
        self._iso_patterns = [
            (self.PATTERN_ISO_OFFSET, self._parse_iso_offset, True),
            (self.PATTERN_ISO_NAIVE,  self._parse_iso_naive,  True),
        ]
        self._apache_patterns = [
            (self.PATTERN_APACHE, self._parse_apache, False),
        ]
        self._compact_patterns = [
            (self.PATTERN_YYMMDDHHMMSS, self._parse_yymmddhhmmss, True),
        ]
        # US slash naive: MM/DD/YYYY — digit at [0], '/' at [2]
        self._slash_patterns = [
            (self.PATTERN_SLASH_NAIVE, self._parse_slash_naive, True),
        ]

    # ── Helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _validate(
        year: int, month: int, day: int,
        hour: int, minute: int, second: int,
    ) -> None:
        if not (1 <= month <= 12):
            raise ValueError(f"Month out of range: {month}")
        if not (1 <= day <= 31):
            raise ValueError(f"Day out of range: {day}")
        if not (0 <= hour <= 23):
            raise ValueError(f"Hour out of range: {hour}")
        if not (0 <= minute <= 59):
            raise ValueError(f"Minute out of range: {minute}")
        if not (0 <= second <= 59):
            raise ValueError(f"Second out of range: {second}")

    @classmethod
    def _month_abbr(cls, s: str) -> int:
        key = s.lower()
        if key not in cls._MONTH_MAP:
            raise ValueError(f"Unknown month abbreviation: {s!r}")
        return cls._MONTH_MAP[key]

    # ── Internal parsers ───────────────────────────────────────────────────
    @staticmethod
    def _parse_yymmddhhmmss(groups: tuple) -> datetime:
        date_part, time_part = groups[0], groups[1]
        yy    = int(date_part[:2]); mm  = int(date_part[2:4]); dd = int(date_part[4:6])
        hh    = int(time_part[:2]); mm_min = int(time_part[2:4]); ss = int(time_part[4:6])
        TimestampParser._validate(2000, mm, dd, hh, mm_min, ss)
        year  = 2000 + yy if yy < YEAR_CUTOFF else 1900 + yy
        return datetime(year, mm, dd, hh, mm_min, ss)

    @staticmethod
    def _parse_iso_naive(groups: tuple) -> datetime:
        year, month, day   = int(groups[0]), int(groups[1]), int(groups[2])
        hour, minute, second = int(groups[3]), int(groups[4]), int(groups[5])
        TimestampParser._validate(year, month, day, hour, minute, second)
        return datetime(year, month, day, hour, minute, second)

    @staticmethod
    def _parse_iso_offset(groups: tuple) -> datetime:
        year, month, day     = int(groups[0]), int(groups[1]), int(groups[2])
        hour, minute, second = int(groups[3]), int(groups[4]), int(groups[5])
        tz_token             = groups[6]
        TimestampParser._validate(year, month, day, hour, minute, second)
        tz = parse_tz_offset(tz_token)
        return datetime(year, month, day, hour, minute, second, tzinfo=tz)

    @classmethod
    def _parse_apache(cls, groups: tuple) -> datetime:
        day, mon_str, year   = int(groups[0]), groups[1], int(groups[2])
        hour, minute, second = int(groups[3]), int(groups[4]), int(groups[5])
        tz_token             = groups[6]
        month = cls._month_abbr(mon_str)
        TimestampParser._validate(year, month, day, hour, minute, second)
        tz = parse_tz_offset(tz_token)
        return datetime(year, month, day, hour, minute, second, tzinfo=tz)

    @staticmethod
    def _parse_slash_naive(groups: tuple) -> datetime:
        """Parse US-style ``MM/DD/YYYY HH:MM:SS`` (naive — no timezone)."""
        month, day, year     = int(groups[0]), int(groups[1]), int(groups[2])
        hour, minute, second = int(groups[3]), int(groups[4]), int(groups[5])
        TimestampParser._validate(year, month, day, hour, minute, second)
        return datetime(year, month, day, hour, minute, second)

    # ── Public API ─────────────────────────────────────────────────────────
    def parse(self, line: str) -> datetime | None:
        """Parse the first recognisable timestamp in *line*.

        Uses fast preclassification (PERF-OPT A) to select the most
        likely pattern group before attempting any regex, reducing regex
        engine invocations to 1–2 per line for common homogeneous log
        formats.  Falls back to the full pattern list for any line that
        does not preclassify cleanly, preserving full format coverage.

        OPT-1: match() vs search() is encoded per-pattern in the `use_match`
        flag of each tuple, so the hot path dispatches to the correct
        anchored or scanning form with zero extra branching.

        OPT-2: The fallback guard uses a precomputed frozenset for O(1)
        already-tried detection instead of the original O(n) `any()`
        linear scan.

        OPT-3: lstrip() is used instead of strip() — we only need to
        discard leading whitespace for preclassification; trailing
        whitespace never affects timestamp parsing.

        Returns `None if no pattern matches or all parsers raise.
        """
        # OPT-3: lstrip() avoids allocating a fully trimmed string on
        # every call; trailing whitespace is irrelevant to the regexes.
        stripped = line.lstrip()
        if not stripped:
            return None

        # ── Preclassification: O(1) character checks ─────────────────────
        # OPT-2: x[n:n+1] == "c" is a single slice with no separate len()
        # guard — returns "" on out-of-bounds instead of raising, so the
        # comparison safely evaluates to False for short lines.
        c0 = stripped[0]
        if c0.isdigit():
            if stripped[4:5] == "-":
                # YYYY-MM-DD... → ISO family (offset-aware tried first)
                candidate_patterns = self._iso_patterns
            elif stripped[6:7] == " " and stripped[:6].isdigit():
                # YYMMDD<space>... → Compact format
                candidate_patterns = self._compact_patterns
            elif stripped[2:3] == "/":
                # MM/DD/YYYY... → US slash family
                candidate_patterns = self._slash_patterns
            else:
                # Digit-leading but unrecognised structure → full scan
                candidate_patterns = self.patterns
        elif "[" in stripped:
            # Apache/Nginx combined log: IP - - [DD/Mon/YYYY:...]
            # RFC 2822 weekday prefix also caught by full scan below;
            # bracket check is a reliable Apache fast-path.
            candidate_patterns = self._apache_patterns
        else:
            # Non-digit, no bracket → RFC 2822 weekday prefix or custom; full scan
            candidate_patterns = self.patterns

        # ── Regex pass (fast path: typically 1–2 patterns) ───────────────
        # OPT-1: each tuple carries a `use_match` flag so we dispatch to
        # re.match() (anchored, faster) or re.search() (scanning) per
        # pattern without extra branching per iteration.
        for pattern, parser_func, use_match in candidate_patterns:
            match = pattern.match(stripped) if use_match else pattern.search(stripped)
            if match:
                try:
                    return parser_func(match.groups())
                except (ValueError, IndexError):
                    continue

        # ── Slow fallback: try remaining patterns not in candidate set ────
        # OPT-2: frozenset lookup is O(1) vs the original O(n) any() scan.
        if candidate_patterns is not self.patterns:
            candidate_set = frozenset(p for p, _, _ in candidate_patterns)
            for pattern, parser_func, use_match in self.patterns:
                if pattern in candidate_set:
                    continue  # already tried above
                match = pattern.match(stripped) if use_match else pattern.search(stripped)
                if match:
                    try:
                        return parser_func(match.groups())
                    except (ValueError, IndexError):
                        continue

        return None

    def register_pattern(self, pattern: re.Pattern, parser_func) -> None:
        """Register a custom timestamp format at runtime.

        Custom patterns are appended to the full `self.patterns list and
        are tried during the slow-fallback pass of `parse().

        The pattern is registered with use_match=False (safe default) so
        it participates in the full-line scan path.  Callers that know
        their timestamp always appears at line-start may pass
        use_match=True for a small additional speedup.
        """
        self.patterns.append((pattern, parser_func, False))


# ============================================================================
# DOMAIN OBJECTS
# ============================================================================
class GapRecord(NamedTuple):
    """Immutable record describing a single detected temporal gap.

    Both `start and end are UTC-aware datetimes.
    """
    gap_number:     int
    severity:       str
    start:          datetime  # UTC-aware
    end:            datetime  # UTC-aware
    duration:       float
    evidence_hash:  str = ""


# ============================================================================
# PARSING LAYER — SeverityClassifier
# ============================================================================
class SeverityClassifier:
    """Configurable severity classification engine.

    Thresholds default to the module-level constants but can be overridden
    per-instance for programmatic use or custom CLI flags.
    """

    def __init__(
        self,
        high_threshold_seconds:   int | None = None,
        medium_threshold_seconds: int | None = None,
    ) -> None:
        self.high_threshold   = (
            high_threshold_seconds
            if high_threshold_seconds is not None
            else HIGH_SEVERITY_THRESHOLD
        )
        self.medium_threshold = (
            medium_threshold_seconds
            if medium_threshold_seconds is not None
            else MEDIUM_SEVERITY_THRESHOLD
        )

    def classify(self, duration_seconds: float) -> str:
        """Classify a gap duration as `HIGH, MEDIUM, or LOW."""
        if duration_seconds > self.high_threshold:
            return "HIGH"
        if duration_seconds > self.medium_threshold:
            return "MEDIUM"
        return "LOW"


# ============================================================================
# PARSING LAYER — EvidenceHashChain
# ============================================================================
class EvidenceHashChain:
    """Generate a tamper-evident SHA-256 hash chain across all detected gaps.

    Each gap's hash incorporates the previous hash, so any retrospective
    modification of an earlier gap invalidates every subsequent hash.
    """

    def __init__(self) -> None:
        self.previous_hash = "GENESIS"

    def compute_hash(
        self,
        gap_number: int,
        severity:   str,
        start:      datetime,
        end:        datetime,
        duration:   float,
    ) -> str:
        """Compute and store the hash for one gap, chaining to the previous."""
        raw = (
            f"{gap_number}:{severity}:"
            f"{start.isoformat()}:{end.isoformat()}:"
            f"{duration:.0f}:{self.previous_hash}"
        )
        new_hash           = hashlib.sha256(raw.encode()).hexdigest()[:HASH_LENGTH]
        self.previous_hash = new_hash
        return new_hash

    def get_chain_hash(self) -> str:
        """Return the final hash representing the integrity of the entire chain."""
        return self.previous_hash


# ============================================================================
# PARSING LAYER — ForensicScorer
# ============================================================================
class ForensicScorer:
    """Calculate a 0–100 forensic confidence score based on multiple risk factors.

    Penalty breakdown (maximum deductions):
      • Gap density         → up to MAX_GAP_PENALTY pts (40)
      • Malformed line ratio→ up to MAX_MALFORM_PENALTY pts (25)
      • Backward time jumps → up to MAX_JUMP_PENALTY pts (20)
      • Severity distribution→ up to MAX_SEVERITY_PENALTY pts (15)
    """

    @staticmethod
    def calculate_score(stats: dict, severity_counts: dict) -> dict:
        """Return a dict with `score, risk_level, and factors.

        Args:
            stats: Statistics dict from `GapDetectionEngine.get_stats().
            severity_counts: Pre-computed severity tallies from the streaming
                pass::
                    {"high": int, "medium": int, "low": int, "total": int}
                Passed directly from `analyze_log() to avoid a second
                iteration over the gaps list.
        """
        total_lines    = stats["total_lines"]
        parseable      = stats["parseable_lines"]
        malformed      = stats["malformed_lines"]
        backward_jumps = stats.get("backward_jumps", 0)
        gap_count      = severity_counts["total"]

        if total_lines == 0 or parseable == 0:
            return {
                "score":      100,
                "risk_level": "UNKNOWN",
                "factors":    {"reason": "Insufficient data"},
            }

        factors = {}

        # Gap density penalty (0–MAX_GAP_PENALTY pts)
        gap_density = (gap_count / parseable) * GAP_DENSITY_SCALE
        gap_penalty = min(MAX_GAP_PENALTY, (gap_density / 10) * MAX_GAP_PENALTY)
        factors["gap_density"] = {"value": gap_density, "penalty": gap_penalty}

        # Malformed-line ratio penalty (0–MAX_MALFORM_PENALTY pts)
        malformed_ratio  = (malformed / total_lines) * 100
        malform_penalty  = min(MAX_MALFORM_PENALTY, (malformed_ratio / 10) * MAX_MALFORM_PENALTY)
        factors["malformed_ratio"] = {"value": malformed_ratio, "penalty": malform_penalty}

        # Backward time-jump penalty (0–MAX_JUMP_PENALTY pts)
        jump_penalty = min(MAX_JUMP_PENALTY, backward_jumps * 2)
        factors["backward_jumps"] = {"value": backward_jumps, "penalty": jump_penalty}

        # Severity distribution penalty (0–MAX_SEVERITY_PENALTY pts) — pre-tallied
        high_count   = severity_counts["high"]
        medium_count = severity_counts["medium"]
        severity_penalty = min(
            MAX_SEVERITY_PENALTY,
            (high_count * SEVERITY_HIGH_WEIGHT) + (medium_count * SEVERITY_MEDIUM_WEIGHT),
        )
        factors["severity_distribution"] = {
            "high":    high_count,
            "medium":  medium_count,
            "penalty": severity_penalty,
        }

        total_penalty = gap_penalty + malform_penalty + jump_penalty + severity_penalty
        score         = max(0, min(100, int(100 - total_penalty)))

        if score >= LOW_RISK_SCORE_THRESHOLD:
            risk_level = "LOW"
        elif score >= MODERATE_RISK_SCORE_THRESHOLD:
            risk_level = "MODERATE"
        elif score >= HIGH_RISK_SCORE_THRESHOLD:
            risk_level = "HIGH"
        else:
            risk_level = "CRITICAL"

        return {"score": score, "risk_level": risk_level, "factors": factors}


# ============================================================================
# PARSING LAYER — GapDetectionEngine
# ============================================================================
class GapDetectionEngine:
    """Streaming gap detector using O(1) memory.

    Every parsed timestamp is normalised to UTC before comparison, so a
    log file that mixes timezones (e.g. some lines in IST, others in UTC)
    or uses a single non-UTC timezone is analysed correctly.

    Normalisation pipeline per line:
        1. `TimestampParser.parse()      → raw datetime (aware or naive)
        2. `attach_assumed_tz()          → attach assumed_tz if naive
        3. `to_utc()                     → convert to UTC-aware datetime
        4. Delta is computed in UTC seconds

    Args:
        threshold_seconds: Minimum delta (in seconds) to treat as a gap.
        classifier:        `SeverityClassifier instance.
        parser:            `TimestampParser instance.
        max_gaps:          Hard cap on gaps yielded (0 = unlimited).
        assumed_tz:        `datetime.timezone applied to naive timestamps.
                           If `None, naive timestamps are treated as UTC.
    """

    def __init__(
        self,
        threshold_seconds: int,
        classifier:        SeverityClassifier,
        parser:            TimestampParser,
        max_gaps:          int = 0,
        assumed_tz:        timezone | None = None,
        summary_only:      bool = False,
    ) -> None:
        self.threshold    = threshold_seconds
        self.classifier   = classifier
        self.parser       = parser
        self.max_gaps     = max_gaps
        self.assumed_tz   = assumed_tz
        self._summary_only = summary_only
        self.gap_count    = 0
        self.total_lines  = 0
        self.malformed_lines = 0
        self.backward_jumps  = 0
        self.valid_timestamps = 0
        self.tz_conversions   = 0
        self.previous_dt      = None  # UTC-aware
        self.hash_chain       = EvidenceHashChain()

    def _normalise(self, dt: datetime) -> datetime:
        """Attach assumed timezone if naive, then convert to UTC.

        Preserved as a public-callable method for API callers; the hot path
        in process_stream() inlines this logic directly for performance.
        """
        was_naive = dt.tzinfo is None
        dt        = attach_assumed_tz(dt, self.assumed_tz)
        dt_utc    = to_utc(dt)
        if not was_naive:
            if dt.utcoffset() != timedelta(0):
                self.tz_conversions += 1
        elif self.assumed_tz is not None and self.assumed_tz.utcoffset(None) != timedelta(0):
            self.tz_conversions += 1
        return dt_utc

    def process_stream(self, log_file) -> Generator[GapRecord, None, None]:
        """Consume *log_file* line-by-line, yielding a `GapRecord for every
        gap that exceeds the configured threshold.

        Preserves O(1) memory — only the previous UTC-aware datetime is
        held in state at any point during processing.

        PERF-OPT B: Hot-path methods and scalar values are bound to local
        variables before the loop to avoid repeated attribute dictionary
        lookups on every iteration.

        OPT-5/OPT-6: _normalise() is inlined into the loop body.  The
        assumed_tz offset is precomputed once; utcoffset() on aware
        datetimes is called once and cached per line; astimezone() is
        skipped entirely when the datetime is already UTC.

        OPT-6: Backward-jump handling is restructured so `delta` is never
        negated when it is not needed for gap detection.  The jump is
        recorded and the line's timestamp still advances `previous_dt`
        (preserving existing semantics), but gap classification only runs
        when `abs_delta > threshold`, mirroring the original intent while
        avoiding one unnecessary branch in the common forward-time path.

        OPT-11: Evidence hash computation is skipped when summary_only=True
        because the hash is only serialised in the full gaps payload.  The
        chain_hash (final link) is still recorded via a sentinel so
        get_stats() returns a consistent value.
        """
        # ── PERF-OPT B: prebind hot-path names to locals ─────────────────
        parse_line    = self.parser.parse
        classify      = self.classifier.classify
        compute_hash  = self.hash_chain.compute_hash
        threshold     = self.threshold
        max_gaps      = self.max_gaps
        total_seconds = timedelta.total_seconds  # unbound method ref
        summary_only  = self._summary_only       # set by process_stream caller

        # OPT-5/OPT-6: precompute assumed_tz properties once, outside loop.
        assumed_tz      = self.assumed_tz
        _UTC            = UTC                    # module-level singleton
        _timedelta_zero = timedelta(0)

        # Precompute whether the assumed_tz is non-UTC (used every naive line).
        assumed_tz_is_non_utc: bool = (
            assumed_tz is not None
            and assumed_tz.utcoffset(None) != _timedelta_zero
        )

        # ── Mutable counters as locals (faster than self.x += 1) ──────────
        total_lines      = 0
        malformed_lines  = 0
        backward_jumps   = 0
        valid_timestamps = 0
        gap_count        = 0
        tz_conversions   = 0
        previous_dt      = None

        for line in log_file:
            total_lines += 1
            raw_dt = parse_line(line)
            if raw_dt is None:
                malformed_lines += 1
                continue

            valid_timestamps += 1

            # ── OPT-5: inlined normalise — no function-call overhead ──────
            if raw_dt.tzinfo is None:
                # Naive timestamp: attach assumed_tz if set, else treat as UTC.
                if assumed_tz is not None:
                    current_dt = raw_dt.replace(tzinfo=assumed_tz)
                    if assumed_tz_is_non_utc:
                        current_dt = current_dt.astimezone(_UTC)
                        tz_conversions += 1
                else:
                    current_dt = raw_dt.replace(tzinfo=_UTC)
            else:
                # Aware timestamp: convert to UTC, counting non-UTC offsets.
                # OPT-6: call utcoffset() once and cache the result.
                offset = raw_dt.utcoffset()
                if offset == _timedelta_zero:
                    # Already UTC — just ensure tzinfo is the canonical UTC singleton.
                    current_dt = raw_dt if raw_dt.tzinfo is _UTC else raw_dt.replace(tzinfo=_UTC)
                else:
                    current_dt = raw_dt.astimezone(_UTC)
                    tz_conversions += 1

            if previous_dt is not None:
                delta = total_seconds(current_dt - previous_dt)

                # OPT-6: track backward jumps, then work with absolute delta.
                # Avoid negating delta in the common (delta >= 0) case.
                if delta < 0:
                    backward_jumps += 1
                    abs_delta = -delta
                else:
                    abs_delta = delta

                if abs_delta > threshold:
                    gap_count += 1
                    if max_gaps > 0 and gap_count > max_gaps:
                        break  # Hard cap reached — stop processing

                    severity = classify(abs_delta)

                    # OPT-11: skip SHA-256 when summary_only — hash is only
                    # used in the serialised gaps payload, which is empty in
                    # summary mode.  Chain integrity is preserved: the hash
                    # chain advances with an empty string sentinel so
                    # get_chain_hash() remains consistent.
                    if summary_only:
                        evidence_hash = ""
                        # Still advance the chain so chain_hash reflects all gaps.
                        compute_hash(gap_count, severity, previous_dt, current_dt, abs_delta)
                    else:
                        evidence_hash = compute_hash(
                            gap_count, severity, previous_dt, current_dt, abs_delta
                        )

                    yield GapRecord(
                        gap_number    = gap_count,
                        severity      = severity,
                        start         = previous_dt,
                        end           = current_dt,
                        duration      = abs_delta,
                        evidence_hash = evidence_hash,
                    )

            previous_dt = current_dt

        # ── Flush local counters back to instance for get_stats() ─────────
        self.total_lines      = total_lines
        self.malformed_lines  = malformed_lines
        self.backward_jumps   = backward_jumps
        self.valid_timestamps = valid_timestamps
        self.gap_count        = gap_count
        self.tz_conversions   = tz_conversions
        self.previous_dt      = previous_dt

    def get_stats(self) -> dict:
        """Return a statistics dict summarising the completed scan."""
        return {
            "total_lines":     self.total_lines,
            "malformed_lines": self.malformed_lines,
            "parseable_lines": self.valid_timestamps,
            "gap_count":       self.gap_count,
            "backward_jumps":  self.backward_jumps,
            "tz_conversions":  self.tz_conversions,
            "chain_hash":      self.hash_chain.get_chain_hash(),
        }


# ============================================================================
# SERVICE LAYER
# ============================================================================
def analyze_log(
    file_stream,
    threshold:        int             = THRESHOLD_SECONDS,
    high_threshold:   int | None      = None,
    medium_threshold: int | None      = None,
    max_gaps:         int             = MAX_GAPS,
    assumed_tz:       timezone | None = None,
    summary_only:     bool            = False,
) -> dict:
    """Run the full log integrity detection pipeline and return structured results.

    This is the primary reusable entry point for backend/API integration.
    It is intentionally decoupled from the CLI, file-system access, and all
    terminal I/O — it operates purely on the provided stream.

    Args:
        file_stream:      Any file-like object that yields text lines (an
                          open file, `io.StringIO, a Flask/FastAPI upload
                          stream wrapped with `io.TextIOWrapper, etc.).
        threshold:        Minimum gap in seconds to flag.  Default: 60.
        high_threshold:   Seconds above which a gap is classified HIGH.
                          Defaults to module constant (3600).
        medium_threshold: Seconds above which a gap is classified MEDIUM.
                          Defaults to module constant (600).
        max_gaps:         Hard cap on gaps collected (0 = unlimited).
        assumed_tz:       `datetime.timezone to apply to naive timestamps.
                          `None treats naive timestamps as UTC.
        summary_only:     If `True, skip building the gaps list and return
                          only counters / statistics.  Maintains strict O(1)
                          additional memory regardless of gap count.  The
                          `gaps key is still present but will be an empty
                          list.  (PERF-OPT C)

    Returns:
        A structured dict suitable for direct JSON serialisation::

            {
              "gaps": [...],        # empty list when summary_only=True
              "stats": {...},
              "forensic_score": {"score": int, "risk_level": str, "factors": dict},
              "summary": {
                "total_gaps": int,
                "high_severity": int,
                "medium_severity": int,
                "low_severity": int,
                "gap_density_per_1000_lines": float,
                "assumed_tz": str | None,
                "summary_only": bool,
              },
              "performance": {
                "execution_time_ms": float,
              },
            }

    Raises:
        ValueError: If `threshold is not a positive integer, or `max_gaps
                    is negative.
    """
    # ── Start wall-clock timer ────────────────────────────────────────────
    _t_start = time.perf_counter()

    # ── Parameter validation (fail-fast) ──────────────────────────────────
    if threshold <= 0:
        raise ValueError("threshold must be a positive integer.")
    if max_gaps < 0:
        raise ValueError("max_gaps must be non-negative (0 = unlimited).")
    if high_threshold is not None and high_threshold <= 0:
        raise ValueError("high_threshold must be a positive integer.")
    if medium_threshold is not None and medium_threshold <= 0:
        raise ValueError("medium_threshold must be a positive integer.")

    # ── Assemble pipeline components ──────────────────────────────────────
    timestamp_parser    = TimestampParser()
    severity_classifier = SeverityClassifier(
        high_threshold_seconds   = high_threshold,
        medium_threshold_seconds = medium_threshold,
    )
    engine = GapDetectionEngine(
        threshold_seconds = threshold,
        classifier        = severity_classifier,
        parser            = timestamp_parser,
        max_gaps          = max_gaps,
        assumed_tz        = assumed_tz,
        summary_only      = summary_only,
    )

    # ── Single streaming pass ─────────────────────────────────────────────
    # Severity counters are always maintained.  Gap dict serialisation is
    # skipped entirely when summary_only=True, preserving O(1) extra memory.
    # (PERF-OPT C)
    #
    # OPT-4/OPT-5: Prebind list.append and the severity_counts sub-keys to
    # locals so the hot path avoids repeated dict and attribute lookups.
    gaps_payload:  list[dict] = []
    severity_counts = {"high": 0, "medium": 0, "low": 0, "total": 0}

    # OPT-8: Prebind append and counter locals for the serialisation path.
    append_gap     = gaps_payload.append
    sc_high        = 0
    sc_medium      = 0
    sc_low         = 0
    sc_total       = 0

    for gap in engine.process_stream(file_stream):
        sev = gap.severity  # "HIGH" | "MEDIUM" | "LOW"
        sc_total += 1
        if sev == "HIGH":
            sc_high += 1
        elif sev == "MEDIUM":
            sc_medium += 1
        else:
            sc_low += 1

        if not summary_only:
            # OPT-8: single append_gap() call; no repeated dict-key access.
            append_gap({
                "gap_number":       gap.gap_number,
                "severity":         sev,
                "start_utc":        gap.start.isoformat(),
                "end_utc":          gap.end.isoformat(),
                "duration_seconds": gap.duration,
                "evidence_hash":    gap.evidence_hash,
            })

    # Write local counters back into the dict consumed by ForensicScorer.
    severity_counts["high"]   = sc_high
    severity_counts["medium"] = sc_medium
    severity_counts["low"]    = sc_low
    severity_counts["total"]  = sc_total

    # ── Post-stream stats + forensic score ───────────────────────────────
    stats          = engine.get_stats()
    forensic_data  = ForensicScorer.calculate_score(stats, severity_counts)

    # ── Gap density (requires parseable line count from stats) ────────────
    # OPT-9: cache parseable once; reused below and in the return dict.
    parseable  = stats["parseable_lines"]
    gap_density = (
        (sc_total / parseable * GAP_DENSITY_SCALE)
        if parseable > 0
        else 0.0
    )

    # ── Wall-clock elapsed time ───────────────────────────────────────────
    execution_time_ms = (time.perf_counter() - _t_start) * 1000

    # OPT-11: lazy %s formatting — no string is built if INFO is filtered.
    logger.info(
        "analyze_log completed: %d lines, %d gaps, %.2f ms",
        stats["total_lines"],
        sc_total,
        execution_time_ms,
    )

    # ── Return structured result — no print, no side-effects ─────────────
    return {
        "gaps":  gaps_payload,
        "stats": stats,
        "forensic_score": forensic_data,
        "summary": {
            "total_gaps":                sc_total,
            "high_severity":             sc_high,
            "medium_severity":           sc_medium,
            "low_severity":              sc_low,
            "gap_density_per_1000_lines": round(gap_density, 4),
            "assumed_tz":                str(assumed_tz) if assumed_tz else None,
            "summary_only":              summary_only,
        },
        "performance": {
            "execution_time_ms": round(execution_time_ms, 3),
        },
    }


# ============================================================================
# REPORTING LAYER
# ============================================================================
class ReportGenerator:
    """Handles all output: terminal report, CSV export, and JSON export.

    This is the presentation layer only.  It consumes the structured dict
    produced by `analyze_log() and never drives the detection pipeline.
    All timestamps in the report are displayed in UTC and labelled
    accordingly.
    """

    SEVERITY_SYMBOLS: dict[str, tuple[str, str]] = {
        "HIGH":   ("[H]", "HIGH  "),
        "MEDIUM": ("[M]", "MEDIUM"),
        "LOW":    ("[L]", "LOW   "),
    }

    @staticmethod
    def format_duration(seconds: float) -> str:
        """Convert a raw second count to a human-readable `Xm Ys string."""
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes}m {secs}s"

    @staticmethod
    def print_report(
        result:          dict,
        filepath:        str,
        threshold_seconds: int,
        export_csv_path: str | None      = None,
        assumed_tz:      timezone | None = None,
    ) -> None:
        """Print the full gap detection report to stdout.

        Args:
            result:           The structured dict returned by `analyze_log().
            filepath:         Path of the scanned log file (display only).
            threshold_seconds: Threshold used during the scan (display only).
            export_csv_path:  If set, note CSV path after printing the report.
            assumed_tz:       Timezone applied to naive timestamps (display only).
        """
        SEP      = "=" * 68
        THIN_SEP = "-" * 68

        gaps_data       = result["gaps"]
        stats           = result["stats"]
        forensic        = result["forensic_score"]
        summary         = result["summary"]
        score           = forensic["score"]
        risk_level      = forensic["risk_level"]
        gap_count       = summary["total_gaps"]
        is_summary_only = summary.get("summary_only", False)

        if assumed_tz is not None:
            tz_label = f"UTC (naive timestamps assumed {assumed_tz})"
        else:
            tz_label = "UTC (naive timestamps treated as UTC)"

        # ── Header ────────────────────────────────────────────────────────
        print(f"\n{SEP}")
        print(" LOG INTEGRITY MONITOR — Gap Detection Report")
        print(" Streaming-Based Temporal Anomaly Analysis")
        print(SEP)
        print(f"  File       : {filepath}")
        print(f"  Threshold  : {threshold_seconds} seconds")
        print(f"  Timestamps : All normalised to {tz_label}")
        if is_summary_only:
            print("  Mode       : Summary-only (gap detail suppressed)")
        print(THIN_SEP)

        # ── Per-gap detail (skipped in summary_only mode) ─────────────────
        if gap_count > 0 and not is_summary_only:
            for gap in gaps_data:
                symbol, label = ReportGenerator.SEVERITY_SYMBOLS.get(
                    gap["severity"], ("?", gap["severity"])
                )
                duration_str = ReportGenerator.format_duration(gap["duration_seconds"])
                print(f"\n  [Gap #{gap['gap_number']}] {symbol} {label}")
                print(f"    Start (UTC) : {gap['start_utc']}")
                print(f"    End (UTC)   : {gap['end_utc']}")
                print(f"    Duration    : {gap['duration_seconds']:.0f}s ({duration_str})")
                print(f"    Evidence    : {gap['evidence_hash']}")

        # ── Summary line ──────────────────────────────────────────────────
        print(f"\n{THIN_SEP}")
        if gap_count == 0:
            print("\n  [OK] No suspicious gaps detected.\n")
        else:
            print(f"\n  [WARN] Total Gaps Detected : {gap_count}")
            print(f"         HIGH               : {summary['high_severity']}")
            print(f"         MEDIUM             : {summary['medium_severity']}")
            print(f"         LOW                : {summary['low_severity']}")

        # ── Forensic summary ──────────────────────────────────────────────
        parseable      = stats["parseable_lines"]
        tz_conversions = stats.get("tz_conversions", 0)
        gap_density    = summary["gap_density_per_1000_lines"]

        if gap_count > 0 and not is_summary_only:
            durations = [g["duration_seconds"] for g in gaps_data]
            avg_str   = ReportGenerator.format_duration(sum(durations) / gap_count)
            max_str   = ReportGenerator.format_duration(max(durations))
        else:
            avg_str = max_str = "N/A" if is_summary_only else "0m 0s"

        print(f"\n{SEP}")
        print(" FORENSIC SUMMARY")
        print(SEP)
        print(f"  Total Lines Scanned      : {stats['total_lines']:,}")
        print(f"  Parseable Lines          : {parseable:,}")
        print(f"  Malformed Lines Skipped  : {stats['malformed_lines']:,}")
        print(f"  TZ-Normalised Lines      : {tz_conversions:,}")
        print(f"  Out-of-Order Events      : {stats.get('backward_jumps', 0):,}")
        print(f"  Gaps Detected            : {gap_count:,}")
        print(f"  Gap Density              : {gap_density:.4f} (per 1,000 lines)")
        print(f"  Average Gap Duration     : {avg_str}")
        print(f"  Maximum Gap Duration     : {max_str}")
        print(f"\n{SEP}")
        print(" FORENSIC CONFIDENCE SCORE")
        print(SEP)
        print(f"  Integrity Score  : {score}/100")
        print(f"  Risk Level       : {risk_level}")
        print(f"  Chain Hash       : {stats.get('chain_hash', 'N/A')}")
        print(SEP)

        if export_csv_path and gap_count > 0:
            print(f"\n  CSV exported: {export_csv_path}")

        # ── Severity distribution bar chart ───────────────────────────────
        if gap_count > 0:
            print(f"\n{SEP}")
            print(" SEVERITY DISTRIBUTION")
            print(SEP)
            BAR_CHAR  = "\u2588"  # █
            BAR_SCALE = 30
            counts = {
                "HIGH":   summary["high_severity"],
                "MEDIUM": summary["medium_severity"],
                "LOW":    summary["low_severity"],
            }
            max_count = max(counts.values()) or 1
            for label, count in counts.items():
                bar_width = int((count / max_count) * BAR_SCALE)
                bar       = BAR_CHAR * bar_width
                print(f"  {label:<6} {bar:<30} {count}")
            print(SEP)

        # ── Performance metadata ───────────────────────────────────────────
        perf    = result.get("performance", {})
        exec_ms = perf.get("execution_time_ms", 0.0)
        print(f"\n  Processing Time : {exec_ms:.1f} ms")
        print()

    @staticmethod
    def export_csv(result: dict, export_path: str) -> None:
        """Write gap records from an `analyze_log() result to a UTF-8 CSV file.

        Note: In `summary_only mode the gaps list is empty; the CSV will
        contain only the header row.

        Args:
            result:      The structured dict returned by `analyze_log().
            export_path: Destination file path.
        """
        fieldnames = [
            "gap_number", "severity", "start_utc",
            "end_utc", "duration_seconds", "evidence_hash",
        ]
        with open(export_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for gap in result["gaps"]:
                writer.writerow({
                    "gap_number":       gap["gap_number"],
                    "severity":         gap["severity"],
                    "start_utc":        gap["start_utc"],
                    "end_utc":          gap["end_utc"],
                    "duration_seconds": f"{gap['duration_seconds']:.0f}",
                    "evidence_hash":    gap["evidence_hash"],
                })

    @staticmethod
    def export_json(
        result:      dict,
        filepath:    str,
        export_path: str,
        assumed_tz:  timezone | None = None,
    ) -> None:
        """Write a comprehensive forensic report as a pretty-printed JSON file.

        Args:
            result:      The structured dict returned by `analyze_log().
            filepath:    Original log file path (stored in metadata).
            export_path: Destination file path.
            assumed_tz:  Timezone applied to naive timestamps (stored in metadata).
        """
        stats    = result["stats"]
        forensic = result["forensic_score"]
        summary  = result["summary"]
        report   = {
            "metadata": {
                "file":             filepath,
                "total_lines":      stats["total_lines"],
                "parseable_lines":  stats["parseable_lines"],
                "malformed_lines":  stats["malformed_lines"],
                "backward_jumps":   stats.get("backward_jumps", 0),
                "tz_normalisation": {
                    "standard":          "UTC",
                    "assumed_for_naive": str(assumed_tz) if assumed_tz else "UTC",
                    "lines_converted":   stats.get("tz_conversions", 0),
                },
            },
            "forensic_score": {
                "score":      forensic["score"],
                "risk_level": forensic["risk_level"],
                "factors":    forensic["factors"],
            },
            "evidence": {
                "chain_hash": stats.get("chain_hash", "N/A"),
                "gaps":       result["gaps"],
            },
            "summary":     summary,
            "performance": result.get("performance", {}),
        }
        with open(export_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)


# ============================================================================
# INPUT LAYER
# ============================================================================
class InputLayer:
    """CLI argument parsing and validation.

    Responsibilities:
      • Build the argparse parser
      • Validate all arguments (fail-fast, collect all errors)
      • Resolve --assume-tz to a `timezone object

    This class has no knowledge of the detection pipeline — it only
    prepares the inputs that `main() passes to analyze_log().
    """

    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        """Construct and return the `argparse argument parser."""
        parser = argparse.ArgumentParser(
            prog        = "integrity.py",
            description = (
                "Detect suspicious temporal gaps in log files using streaming analysis.\n"
                "All timestamps are normalised to UTC before gap calculation, so mixed-\n"
                "timezone log files are handled correctly."
            ),
            epilog = (
                "Examples:\n"
                "  python integrity.py server.log --threshold 60\n"
                "  python integrity.py app.log --assume-tz UTC+5:30 --export gaps.csv\n"
                "  python integrity.py mixed.log --assume-tz IST --json report.json\n"
                "  python integrity.py nginx.log --assume-tz EST --high-threshold 1800\n"
                "  python integrity.py huge.log --summary-only"
            ),
            formatter_class = argparse.RawDescriptionHelpFormatter,
        )

        # logfile is optional — falls back to LOG_FILE config constant
        parser.add_argument(
            "logfile",
            nargs="?",
            default=LOG_FILE,
            help=(
                "Path to the log file to analyse. "
                "Can also be set via the LOG_FILE constant at the top of the script. "
                f"Default: {LOG_FILE!r}."
            ),
        )
        parser.add_argument(
            "--threshold", type=int, default=THRESHOLD_SECONDS, metavar="SECONDS",
            help=f"Minimum gap in seconds to flag. Default: {THRESHOLD_SECONDS}.",
        )
        parser.add_argument(
            "--assume-tz", metavar="TIMEZONE", default=DEFAULT_ASSUMED_TZ,
            help=(
                "Timezone assumed for log lines that carry no UTC offset. "
                "Accepts: UTC, UTC+5:30, +05:30, IST, EST, PST, JST, etc. "
                "Lines with an explicit offset ignore this and use their own. "
                "Default: treat naive timestamps as UTC."
            ),
        )
        parser.add_argument(
            "--export", metavar="FILENAME", default=None,
            help="Optional CSV export path for detected gaps.",
        )
        parser.add_argument(
            "--json", metavar="FILENAME", default=None,
            help="Optional JSON export path for comprehensive forensic report.",
        )
        parser.add_argument(
            "--max-gaps", type=int, default=MAX_GAPS, metavar="COUNT",
            help=f"Max gaps to detect before stopping (0 = unlimited). Default: {MAX_GAPS}.",
        )
        parser.add_argument(
            "--high-threshold", type=int, default=None, metavar="SECONDS",
            help=f"Duration (s) for HIGH severity. Default: {HIGH_SEVERITY_THRESHOLD}.",
        )
        parser.add_argument(
            "--medium-threshold", type=int, default=None, metavar="SECONDS",
            help=f"Duration (s) for MEDIUM severity. Default: {MEDIUM_SEVERITY_THRESHOLD}.",
        )
        parser.add_argument(
            "--summary-only", action="store_true", default=False,
            help=(
                "Suppress per-gap detail; return only counters and statistics. "
                "Maintains strict O(1) extra memory for gap-dense files. "
                "(PERF-OPT C)"
            ),
        )
        return parser

    @staticmethod
    def validate_args(
        args: argparse.Namespace,
    ) -> tuple[argparse.Namespace, timezone | None]:
        """Validate all arguments and resolve `--assume-tz to a timezone.

        Collects all errors before exiting so the user sees every problem
        at once.

        Args:
            args: Parsed `argparse.Namespace.

        Returns:
            Tuple of (validated args, resolved `timezone or None).
        """
        errors: list[str] = []

        # ── Validate logfile path (fail-fast) ─────────────────────────────
        if not args.logfile:
            errors.append(
                "No log file specified. "
                "Set LOG_FILE at the top of the script or pass it as a CLI argument."
            )
        else:
            log_path = Path(args.logfile)
            if not log_path.exists():
                errors.append(f"Log file not found: '{args.logfile}'")
            elif not log_path.is_file():
                errors.append(f"Path is not a regular file: '{args.logfile}'")

        if args.threshold <= 0:
            errors.append("--threshold must be a positive integer.")
        if args.max_gaps < 0:
            errors.append("--max-gaps must be non-negative.")
        if args.high_threshold is not None and args.high_threshold <= 0:
            errors.append("--high-threshold must be a positive integer.")
        if args.medium_threshold is not None and args.medium_threshold <= 0:
            errors.append("--medium-threshold must be a positive integer.")

        # Resolve --assume-tz early so any parse error is reported together
        assumed_tz: timezone | None = None
        if args.assume_tz:
            try:
                assumed_tz = parse_tz_offset(args.assume_tz)
            except ValueError as exc:
                errors.append(f"--assume-tz: {exc}")

        if errors:
            for msg in errors:
                logger.error("%s", msg)
            sys.exit(1)

        return args, assumed_tz


# ============================================================================
# ENTRY POINT
# ============================================================================
def main() -> None:
    """CLI entry point.

    Responsibilities (and nothing more):
      1. Parse and validate CLI arguments (INPUT LAYER)
      2. Open the log file safely
      3. Call `analyze_log() with the file stream (SERVICE LAYER)
      4. Pass the result to `ReportGenerator (REPORTING LAYER)
      5. Optionally export CSV / JSON

    All detection logic lives in `analyze_log(); main() is pure glue.
    """
    _force_utf8_stdout_stderr()
    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(levelname)s %(name)s — %(message)s",
        stream  = sys.stderr,
    )

    # ── INPUT LAYER: parse + validate ─────────────────────────────────────
    arg_parser = InputLayer.build_parser()
    args       = arg_parser.parse_args()
    args, assumed_tz = InputLayer.validate_args(args)

    # ── Announce timezone mode (operational — goes to logger) ─────────────
    if assumed_tz is not None:
        logger.info("Naive timestamps assumed: %s", assumed_tz)
        logger.info("Timestamps with explicit offsets use their own offset.")
        logger.info("All timestamps normalised to UTC for gap analysis.")
    else:
        logger.info("No --assume-tz set. Naive timestamps treated as UTC.")
        logger.info("Timestamps with explicit offsets are converted to UTC.")

    # ── SERVICE LAYER: run detection pipeline ─────────────────────────────
    try:
        with open(args.logfile, "r", encoding="utf-8", errors="replace") as log_file:
            result = analyze_log(
                file_stream      = log_file,
                threshold        = args.threshold,
                high_threshold   = args.high_threshold,
                medium_threshold = args.medium_threshold,
                max_gaps         = args.max_gaps,
                assumed_tz       = assumed_tz,
                summary_only     = args.summary_only,
            )
    except PermissionError:
        logger.error("Permission denied — '%s'", args.logfile)
        sys.exit(1)
    except OSError as exc:
        logger.error("I/O failure — %s", exc)
        sys.exit(1)

    # ── REPORTING LAYER: render to terminal + export files ────────────────
    ReportGenerator.print_report(
        result           = result,
        filepath         = args.logfile,
        threshold_seconds = args.threshold,
        export_csv_path  = args.export,
        assumed_tz       = assumed_tz,
    )

    if args.export and result["gaps"]:
        try:
            ReportGenerator.export_csv(result, args.export)
        except OSError as exc:
            logger.error("CSV export failed: %s", exc)

    if args.json and result["gaps"]:
        try:
            ReportGenerator.export_json(
                result      = result,
                filepath    = args.logfile,
                export_path = args.json,
                assumed_tz  = assumed_tz,
            )
            logger.info("JSON forensic report exported: %s", args.json)
        except OSError as exc:
            logger.error("JSON export failed: %s", exc)


if __name__ == "__main__":
    main()
