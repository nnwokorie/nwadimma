"""Itoju web app: serves the single-page UI and the triage API."""
import os

from flask import Flask, jsonify, request, send_from_directory

from itoju import config, facilities
from itoju.agents import triage

app = Flask(__name__, static_folder="static")


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/locations")
def locations():
    return jsonify({"data_mode": config.DATA_MODE, "states": facilities.states_and_lgas(), "model": config.MODEL})


@app.post("/api/triage")
def api_triage():
    body = request.get_json(force=True, silent=True) or {}
    note = str(body.get("note", "")).strip()
    if not note:
        return jsonify({"status": "error", "message": "Please describe the child first."}), 400
    loc = body.get("location") or {}
    location = {
        "state": str(loc.get("state") or "")[:60],
        "lga": str(loc.get("lga") or "")[:60],
        "lat": float(loc["lat"]) if loc.get("lat") not in (None, "") else None,
        "lon": float(loc["lon"]) if loc.get("lon") not in (None, "") else None,
    }
    return jsonify(triage(note, location))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5050"))
    print(f"Itoju running on http://localhost:{port}  (model {config.MODEL}, {config.DATA_MODE} facility data)")
    app.run(host="127.0.0.1", port=port, debug=False)
