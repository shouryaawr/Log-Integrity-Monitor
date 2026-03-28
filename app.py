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
CORS(app)  # Allow Next.js dev server (localhost:3000) to call this API

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
    """Upload a log file and save it to LOGS_DIR."""
    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    safe_name = Path(f.filename).name  # strip any directory traversal
    dest = LOGS_DIR / safe_name
    f.save(str(dest))
    logger.info("Uploaded log file: %s (%d bytes)", safe_name, dest.stat().st_size)

    return jsonify({"message": "File uploaded", "filename": safe_name}), 201


@app.route("/api/logs/<filename>", methods=["DELETE"])
def delete_log(filename: str):
    """Delete a log file from LOGS_DIR."""
    target = LOGS_DIR / Path(filename).name
    if not target.exists():
        return jsonify({"error": "File not found"}), 404
    target.unlink()
    return jsonify({"message": f"Deleted {filename}"})


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
