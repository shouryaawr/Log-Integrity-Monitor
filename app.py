"""
Flask API wrapper for Log Integrity Monitor (integrity_2.py)
Exposes the analyze_log() function via REST endpoints.
Log files are stored in ./logs/ by default.
"""

import io
import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, request, jsonify
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


def _serialize(obj):
    """JSON serialiser for datetime objects (fallback)."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Not serialisable: {type(obj)}")


ALLOWED_LOG_EXTENSIONS = frozenset({
    ".log", ".txt", ".csv", ".tsv", ".out", ".gz",
})

def _safe_filename(filename: str) -> str:
    """Return a sanitized filename, raising ValueError on dangerous input.

    Rejects:
      • Empty names
      • Names containing null bytes
      • Names containing path separators (/ or \\)
      • Names that resolve to a parent-directory traversal (..)
      • Names whose extension is not in ALLOWED_LOG_EXTENSIONS
    """
    if not filename:
        raise ValueError("Filename must not be empty.")
    if "\x00" in filename:
        raise ValueError("Filename contains null bytes.")
    if "/" in filename or "\\" in filename:
        raise ValueError("Filename must not contain path separators.")
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


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "log-integrity-monitor"})


@app.route("/api/logs", methods=["GET"])
def list_logs():
    """Return a list of all log files stored in LOGS_DIR."""
    files = []
    for f in sorted(LOGS_DIR.iterdir()):
        if f.is_file():
            stat = f.stat()
            files.append({
                "name":     f.name,
                "size":     stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
    return jsonify({"logs": files})


@app.route("/api/logs/upload", methods=["POST"])
def upload_log():
    from urllib.parse import unquote
    import base64

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
        dest.write_text(decoded, encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not decode upload: %s", exc)
        return jsonify({"error": f"Failed to decode file: {exc}"}), 400

    logger.info("Uploaded log file: %s (%d bytes)", safe_name, dest.stat().st_size)
    return jsonify({"message": "Uploaded successfully", "filename": safe_name})


@app.route("/api/logs/<filename>", methods=["DELETE"])
def delete_log(filename: str):
    """Delete a log file from LOGS_DIR."""
    try:
        safe_name = _safe_filename(filename)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    target = LOGS_DIR / safe_name
    if not target.exists():
        return jsonify({"error": "File not found"}), 404
    target.unlink()
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
    # ── Parse body ───────────────────────────────────────────────────────────
    data = request.get_json(silent=True) or {}

    filename         = data.get("filename")
    threshold        = int(data.get("threshold", 60))
    high_threshold   = data.get("high_threshold")
    medium_threshold = data.get("medium_threshold")
    max_gaps         = int(data.get("max_gaps", 0))
    assume_tz_str    = data.get("assume_tz")
    summary_only     = bool(data.get("summary_only", False))

    if high_threshold is not None:
        high_threshold = int(high_threshold)
    if medium_threshold is not None:
        medium_threshold = int(medium_threshold)

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

    result["_meta"] = {"filename": filename, "result_file": out_name}
    return jsonify(result)


@app.route("/api/results", methods=["GET"])
def list_results():
    """Return a list of saved analysis result JSON files."""
    files = []
    for f in sorted(RESULTS_DIR.iterdir(), reverse=True):
        if f.is_file() and f.suffix == ".json":
            stat = f.stat()
            files.append({
                "name":     f.name,
                "size":     stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
    return jsonify({"results": files})


@app.route("/api/results/<filename>", methods=["GET"])
def get_result(filename: str):
    """Retrieve a previously saved analysis result."""
    target = RESULTS_DIR / Path(filename).name
    if not target.exists():
        return jsonify({"error": "Result not found"}), 404
    with open(target, "r", encoding="utf-8") as jf:
        data = json.load(jf)
    return jsonify(data)


# ============================================================================
# RUN
# ============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  Log Integrity Monitor — Flask API")
    print(f"  Logs directory  : {LOGS_DIR}")
    print(f"  Results directory: {RESULTS_DIR}")
    print("  Running on       : http://localhost:5000")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=True)
