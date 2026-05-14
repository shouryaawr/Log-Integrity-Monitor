"""
Flask API wrapper for Log Integrity Monitor (integrity_2.py)
Exposes the analyze_log() function via REST endpoints.
Log files are stored in ./logs/ by default.
"""

import io
import os
import json
import math
import time
import uuid
import base64
import logging
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import unquote
from flask import Flask, request, jsonify, g
from flask_cors import CORS

from integrity_2 import analyze_log, parse_tz_offset

# ── Logging setup ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("api")

app = Flask(__name__)
_cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
if _frontend_url := os.environ.get("FRONTEND_URL"):
    _cors_origins.append(_frontend_url)

CORS(app, origins=_cors_origins)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload cap

# ── Directories ─────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
LOGS_DIR  = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# ── Module-level constants ───────────────────────────────────────────────────
_START_TIME: float = time.time()

# Files larger than this are automatically analysed in summary_only mode to
# prevent Render's 30-second request timeout from killing large analyses.
# Override via SYNC_SIZE_LIMIT_BYTES environment variable.
SYNC_SIZE_LIMIT_BYTES: int = int(os.environ.get("SYNC_SIZE_LIMIT_BYTES", 5 * 1024 * 1024))

MAX_FILENAME_LENGTH = 200  # well below most filesystem limits (255 bytes)

ALLOWED_LOG_EXTENSIONS = frozenset({
    ".log", ".txt", ".csv", ".tsv", ".out", ".gz",
})

ALLOWED_RESULT_EXTENSIONS: frozenset[str] = frozenset({".json"})


def _serialize(obj):
    """JSON serialiser for datetime objects (fallback)."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Not serialisable: {type(obj)}")


def _safe_filename(filename: str) -> str:
    """Return a sanitized filename, raising ValueError on dangerous input.

    Rejects:
      • Empty names
      • Names containing null bytes
      • Names containing path separators (/ or \\)
      • Names that resolve to a parent-directory traversal (..)
      • Names longer than MAX_FILENAME_LENGTH
      • Names whose extension is not in ALLOWED_LOG_EXTENSIONS
    """
    if not filename:
        raise ValueError("Filename must not be empty.")
    if "\x00" in filename:
        raise ValueError("Filename contains null bytes.")
    if "/" in filename or "\\" in filename:
        raise ValueError("Filename must not contain path separators.")
    if len(filename) > MAX_FILENAME_LENGTH:
        raise ValueError(
            f"Filename is too long ({len(filename)} chars). "
            f"Maximum allowed is {MAX_FILENAME_LENGTH} characters."
        )
    safe = Path(filename).name
    if safe in ("", ".", ".."):
        raise ValueError(f"Filename {filename!r} is not a valid file name.")
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_LOG_EXTENSIONS:
        raise ValueError(
            f"File extension {ext!r} is not allowed. "
            f"Permitted extensions: {', '.join(sorted(ALLOWED_LOG_EXTENSIONS))}"
        )
    return safe


def _safe_result_filename(filename: str) -> str:
    """Return a sanitized result filename. Only .json files are permitted.

    Rejects:
      • Empty names
      • Names containing null bytes
      • Names containing path separators (/ or \\)
      • Names that resolve to . or ..
      • Names longer than MAX_FILENAME_LENGTH
      • Names whose extension is not .json
    """
    if not filename:
        raise ValueError("Filename must not be empty.")
    if "\x00" in filename:
        raise ValueError("Filename contains null bytes.")
    if "/" in filename or "\\" in filename:
        raise ValueError("Filename must not contain path separators.")
    if len(filename) > MAX_FILENAME_LENGTH:
        raise ValueError(
            f"Filename is too long ({len(filename)} chars). "
            f"Maximum allowed is {MAX_FILENAME_LENGTH} characters."
        )
    safe = Path(filename).name
    if safe in ("", ".", ".."):
        raise ValueError(f"Filename {filename!r} is not a valid file name.")
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_RESULT_EXTENSIONS:
        raise ValueError("Result filenames must have a .json extension.")
    return safe


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(Exception)
def handle_unexpected(exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    return jsonify({"error": "An unexpected server error occurred."}), 500

@app.errorhandler(413)
def handle_too_large(exc: Exception):
    return jsonify({"error": "File too large. Maximum upload size is 50 MB."}), 413

@app.errorhandler(404)
def handle_not_found(exc: Exception):
    return jsonify({"error": "Endpoint not found."}), 404

@app.errorhandler(405)
def handle_method_not_allowed(exc: Exception):
    return jsonify({"error": "Method not allowed."}), 405


# ============================================================================
# REQUEST / RESPONSE HOOKS
# ============================================================================

@app.before_request
def _assign_request_id() -> None:
    g.request_id = uuid.uuid4().hex[:12]

@app.after_request
def _add_request_id_header(response):
    response.headers["X-Request-ID"] = getattr(g, "request_id", "")
    return response


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.route("/api/health", methods=["GET"])
def health():
    checks = {
        "logs_dir_accessible":    LOGS_DIR.is_dir(),
        "results_dir_accessible": RESULTS_DIR.is_dir(),
        "logs_dir_writable":      os.access(LOGS_DIR, os.W_OK),
        "results_dir_writable":   os.access(RESULTS_DIR, os.W_OK),
    }
    all_ok = all(checks.values())
    return jsonify({
        "status":         "ok" if all_ok else "degraded",
        "service":        "log-integrity-monitor",
        "checks":         checks,
        "uptime_seconds": round(time.time() - _START_TIME, 1),
    }), 200 if all_ok else 503


@app.route("/api/logs", methods=["GET"])
def list_logs():
    """Return a paginated list of log files stored in LOGS_DIR.

    Query parameters:
      page     int – 1-based page number (default: 1)
      per_page int – results per page, max 100 (default: 50)
    """
    try:
        page     = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 50))))
    except (TypeError, ValueError):
        return jsonify({"error": "page and per_page must be integers."}), 400

    all_files = []
    for f in sorted(LOGS_DIR.iterdir()):
        if f.is_file():
            stat = f.stat()
            all_files.append({
                "name":     f.name,
                "size":     stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })

    total = len(all_files)
    start = (page - 1) * per_page
    return jsonify({
        "logs":        all_files[start : start + per_page],
        "total":       total,
        "page":        page,
        "per_page":    per_page,
        "total_pages": math.ceil(total / per_page) if total > 0 else 1,
    })


@app.route("/api/logs/upload", methods=["POST"])
def upload_log():
    req_id = getattr(g, "request_id", "")

    data = request.get_json(silent=True) or {}
    filename        = data.get("filename")
    content_encoded = data.get("content")

    if not filename or not content_encoded:
        return jsonify({"error": "filename and content are required"}), 400

    try:
        safe_name = _safe_filename(filename)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    dest = LOGS_DIR / safe_name

    try:
        # Invert the frontend's btoa(encodeURIComponent(text)):
        #   Step 1 — base64-decode the payload back to the percent-encoded string
        #   Step 2 — percent-decode (unquote) to recover the original UTF-8 text
        b64_decoded = base64.b64decode(content_encoded + "==").decode("utf-8", errors="replace")
        decoded = unquote(b64_decoded)
        if not decoded.strip():
            return jsonify({"error": "Uploaded file is empty or contains only whitespace."}), 400
        if len(decoded) < 10:
            return jsonify({"error": "Uploaded file is too small to be a valid log."}), 400
        dest.write_text(decoded, encoding="utf-8")
    except Exception as exc:
        logger.warning("[%s] Could not decode upload: %s", req_id, exc)
        return jsonify({"error": f"Failed to decode file: {exc}"}), 400

    logger.info("[%s] Uploaded log file: %s (%d bytes)", req_id, safe_name, dest.stat().st_size)
    return jsonify({"message": "Uploaded successfully", "filename": safe_name})


@app.route("/api/logs/<filename>", methods=["DELETE"])
def delete_log(filename: str):
    """Delete a log file from LOGS_DIR."""
    req_id = getattr(g, "request_id", "")
    logger.info("[%s] Delete requested: %s", req_id, filename)
    try:
        safe_name = _safe_filename(filename)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    target = LOGS_DIR / safe_name
    if not target.exists():
        return jsonify({"error": "File not found"}), 404
    target.unlink()
    logger.info("[%s] Deleted: %s", req_id, safe_name)
    return jsonify({"message": f"Deleted {safe_name}"})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    Analyze a stored log file or an uploaded stream.

    JSON body parameters:
      filename         str  – name of a file already in LOGS_DIR (mutually exclusive with raw upload)
      threshold        int  – minimum gap seconds (default 60)
      high_threshold   int  – HIGH severity threshold (default 3600)
      medium_threshold int  – MEDIUM severity threshold (default 600)
      max_gaps         int  – hard cap on gaps (0 = unlimited)
      assume_tz        str  – timezone string for naive timestamps
      summary_only     bool – omit per-gap detail
    """
    req_id = getattr(g, "request_id", "")

    # ── Content-Type guard ───────────────────────────────────────────────────
    if not request.is_json:
        return jsonify({
            "error": "Request Content-Type must be application/json."
        }), 415

    # ── Parse body ───────────────────────────────────────────────────────────
    data = request.get_json(silent=True) or {}

    filename         = data.get("filename")
    assume_tz_str    = data.get("assume_tz")
    summary_only     = bool(data.get("summary_only", False))
    high_threshold   = data.get("high_threshold")
    medium_threshold = data.get("medium_threshold")

    try:
        threshold = int(data.get("threshold", 60))
        max_gaps  = int(data.get("max_gaps", 0))
        if (ht := data.get("high_threshold")) is not None:
            high_threshold = int(ht)
        if (mt := data.get("medium_threshold")) is not None:
            medium_threshold = int(mt)
    except (TypeError, ValueError) as exc:
        return jsonify({"error": f"Invalid numeric parameter: {exc}"}), 400

    # ── Range guards ─────────────────────────────────────────────────────────
    if threshold <= 0:
        return jsonify({"error": "threshold must be a positive integer."}), 400
    if max_gaps < 0:
        return jsonify({"error": "max_gaps must be non-negative."}), 400

    # Cross-validate threshold ordering
    _eff_high   = high_threshold   if high_threshold   is not None else 3600
    _eff_medium = medium_threshold if medium_threshold is not None else 600
    if _eff_medium >= _eff_high:
        return jsonify({
            "error": (
                f"medium_threshold ({_eff_medium}s) must be less than "
                f"high_threshold ({_eff_high}s)."
            )
        }), 400

    # ── Resolve timezone ─────────────────────────────────────────────────────
    assumed_tz = None
    if assume_tz_str:
        try:
            assumed_tz = parse_tz_offset(assume_tz_str)
        except ValueError as exc:
            return jsonify({"error": f"Invalid timezone: {exc}"}), 400

    # ── Locate the log file ──────────────────────────────────────────────────
    if not filename:
        return jsonify({"error": "filename is required"}), 400

    log_path = LOGS_DIR / Path(filename).name
    if not log_path.exists():
        return jsonify({"error": f"File not found: {filename}"}), 404

    # ── File size check / forced summary guard ───────────────────────────────
    file_size      = log_path.stat().st_size
    forced_summary = False
    if file_size > SYNC_SIZE_LIMIT_BYTES and not summary_only:
        summary_only   = True
        forced_summary = True
        logger.info(
            "[%s] Large file (%d bytes) — forcing summary_only mode",
            req_id, file_size,
        )

    # ── Analysis start log ───────────────────────────────────────────────────
    logger.info(
        "[%s] Analyze started: file=%s threshold=%d max_gaps=%d summary_only=%s",
        req_id, filename, threshold, max_gaps, summary_only,
    )

    # ── Run analysis ─────────────────────────────────────────────────────────
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            result = analyze_log(
                file_stream      = fh,
                threshold        = threshold,
                high_threshold   = high_threshold,
                medium_threshold = medium_threshold,
                max_gaps         = max_gaps,
                assumed_tz       = assumed_tz,
                summary_only     = summary_only,
            )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"I/O failure: {exc}"}), 500

    # ── Analysis done log ────────────────────────────────────────────────────
    logger.info(
        "[%s] Analyze done: file=%s gaps=%d risk=%s score=%d elapsed=%.1fms",
        req_id,
        filename,
        result["summary"]["total_gaps"],
        result["forensic_score"]["risk_level"],
        result["forensic_score"]["score"],
        result["performance"]["execution_time_ms"],
    )

    # ── Persist result JSON in RESULTS_DIR ──────────────────────────────────
    ts_tag   = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_name = f"{Path(filename).stem}_{ts_tag}.json"
    out_path = RESULTS_DIR / out_name
    try:
        with open(out_path, "w", encoding="utf-8") as jf:
            json.dump({"filename": filename, "params": data, "result": result}, jf,
                      default=_serialize, indent=2)
        logger.info("Result saved to %s", out_path)
    except OSError as exc:
        logger.warning("Could not save result JSON: %s", exc)

    result["_meta"] = {
        "filename":            filename,
        "result_file":         out_name,
        "request_id":          req_id,
        "file_size_bytes":     file_size,
        "forced_summary_only": forced_summary,
    }
    return jsonify(result)


@app.route("/api/results", methods=["GET"])
def list_results():
    """Return a paginated list of saved analysis result JSON files.

    Query parameters:
      page     int – 1-based page number (default: 1)
      per_page int – results per page, max 100 (default: 50)
    """
    try:
        page     = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 50))))
    except (TypeError, ValueError):
        return jsonify({"error": "page and per_page must be integers."}), 400

    all_files = []
    for f in sorted(RESULTS_DIR.iterdir(), reverse=True):
        if f.is_file() and f.suffix == ".json":
            stat = f.stat()
            all_files.append({
                "name":     f.name,
                "size":     stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })

    total = len(all_files)
    start = (page - 1) * per_page
    return jsonify({
        "results":     all_files[start : start + per_page],
        "total":       total,
        "page":        page,
        "per_page":    per_page,
        "total_pages": math.ceil(total / per_page) if total > 0 else 1,
    })


@app.route("/api/results/<filename>", methods=["GET"])
def get_result(filename: str):
    """Retrieve a previously saved analysis result."""
    try:
        safe_name = _safe_result_filename(filename)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    target = RESULTS_DIR / safe_name
    if not target.exists():
        return jsonify({"error": "Result not found"}), 404
    with open(target, "r", encoding="utf-8") as jf:
        data = json.load(jf)
    return jsonify(data)


# ============================================================================
# RUN
# ============================================================================
if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_ENV", "production") == "development"
    print("=" * 60)
    print("  Log Integrity Monitor — Flask API")
    print(f"  Logs directory  : {LOGS_DIR}")
    print(f"  Results directory: {RESULTS_DIR}")
    print("  Running on       : http://localhost:5000")
    print(f"  Debug mode      : {'ON (development)' if debug_mode else 'OFF (production)'}")
    print("=" * 60)
    # For production, use: gunicorn app:app --bind 0.0.0.0:5000 --workers 2 --timeout 120
    # Set FLASK_ENV=development to enable debug mode locally.
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)
