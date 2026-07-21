#!/usr/bin/env python3
"""Local SwimTimeline web server."""

from __future__ import annotations

import argparse
import cgi
from datetime import date, timedelta
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.parse import unquote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNS_DIR = ROOT / ".swimtimeline-runs"
CURRENT_MEETS_PATH = ROOT / "data" / "current_meets.json"
USAGE_STATS_PATH = ROOT / "data" / "usage_stats.json"
HOSTED_MEETS_DIR = ROOT / "meets" / "current-hosted"
DEFAULT_MODES = ["daily"]
VALID_MODES = {"daily", "weekend", "detailed"}
sys.path.insert(0, str(ROOT))

from swimtimeline.extract import analyze_uploads
from swimtimeline.ics import build_ics


class SwimTimelineHandler(BaseHTTPRequestHandler):
    server_version = "SwimTimeline/0.1"

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.send_static(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            rel = path.removeprefix("/static/")
            self.send_static((STATIC_DIR / rel).resolve())
            return
        if path.startswith("/download/"):
            self.send_download(path)
            return
        if path == "/api/health":
            self.send_json({"ok": True})
            return
        if path == "/api/current-meets":
            self.send_json(public_meets_payload())
            return
        if path == "/api/usage":
            self.send_json(public_usage_stats())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/analyze":
                result = self.handle_analyze()
                self.send_json(result)
                return
            if self.path == "/api/analyze-current":
                result = self.handle_analyze_current()
                self.send_json(result)
                return
            if self.path == "/api/publish-current":
                result = self.handle_publish_current()
                self.send_json(result)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:  # Keep local app errors visible and debuggable.
            self.send_json({"error": str(exc)}, status=500)

    def handle_analyze(self) -> dict:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("Expected multipart form upload.")

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )

        swimmer_names = swimmer_names_from_form(form)
        if not swimmer_names:
            raise ValueError("At least one swimmer name is required.")

        state = form_value(form, "state").strip().upper()
        modes = normalize_modes(form_values(form, "modes"))
        combine_family = form_bool(form, "combine_family", default=True)
        estimate_heat_lanes = form_bool(form, "estimate_heat_lanes", default=False)

        run_id = f"{int(time.time())}-{uuid4().hex[:8]}"
        run_dir = RUNS_DIR / run_id
        upload_dir = run_dir / "uploads"
        output_dir = run_dir / "outputs"
        upload_dir.mkdir(parents=True, exist_ok=True)

        flyer_path = save_upload(form, "flyer_pdf", upload_dir, required=False)
        psych_path = save_upload(form, "psych_pdf", upload_dir, required=True)
        timeline_path = save_upload(form, "timeline_pdf", upload_dir, required=True)
        relay_path = save_upload(form, "relay_pdf", upload_dir, required=False)

        result = analyze_swimmer_set(
            flyer_path=flyer_path,
            psych_path=psych_path,
            timeline_path=timeline_path,
            relay_path=relay_path,
            internal_relay_sources=None,
            swimmer_names=swimmer_names,
            output_dir=output_dir,
            state=state,
            modes=modes,
            combine_family=combine_family,
            estimate_heat_lanes=estimate_heat_lanes,
        )
        result["run_id"] = run_id
        result["relay_status"] = "uploaded_and_parsed" if relay_path else "not_uploaded"
        result["can_publish_current"] = True
        result["downloads"] = download_urls(run_id, result["files"])
        add_individual_download_urls(run_id, result)
        for swimmer in result.get("swimmers", [{"name": result.get("swimmer") or swimmer_names[0]}]):
            record_swimmer_lookup(str(swimmer.get("name") or ""), result["meet"].get("id"), source="upload")
        write_run_manifest(
            run_dir,
            {
                "run_id": run_id,
                "swimmer": swimmer_names[0],
                "swimmers": swimmer_names,
                "state": state,
                "meet": result["meet"],
                "sessions": result["sessions"],
                "uploads": {
                    "flyer": relative_path(flyer_path),
                    "psych": relative_path(psych_path),
                    "timeline": relative_path(timeline_path),
                    "relay": relative_path(relay_path),
                },
            },
        )
        return result

    def handle_analyze_current(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        meet_id = str(payload.get("meet_id", "")).strip()
        swimmer_names = swimmer_names_from_payload(payload)
        modes = normalize_modes(payload.get("modes"))
        combine_family = payload_bool(payload, "combine_family", default=True)
        estimate_heat_lanes = payload_bool(payload, "estimate_heat_lanes", default=False)
        relay_option_ids = payload_relay_options(payload)
        if not meet_id:
            raise ValueError("Current meet id is required.")
        if not swimmer_names:
            raise ValueError("At least one swimmer name is required.")

        meet = resolve_current_meet(meet_id)
        state = str(payload.get("state") or meet.get("state") or "").strip().upper()
        files = meet.get("files", {})
        flyer_path = resolve_repo_file(files.get("flyer"), required=False)
        psych_path = resolve_repo_file(files.get("psych"), required=True)
        timeline_path = resolve_repo_file(files.get("timeline"), required=True)
        relay_path = resolve_repo_file(files.get("relay"), required=False)
        internal_relay_sources = resolve_current_meet_relay_sources(meet, relay_option_ids)

        run_id = f"{int(time.time())}-{uuid4().hex[:8]}"
        output_dir = RUNS_DIR / run_id / "outputs"
        result = analyze_swimmer_set(
            flyer_path=flyer_path,
            psych_path=psych_path,
            timeline_path=timeline_path,
            relay_path=relay_path,
            internal_relay_sources=internal_relay_sources,
            swimmer_names=swimmer_names,
            output_dir=output_dir,
            state=state,
            modes=modes,
            combine_family=combine_family,
            estimate_heat_lanes=estimate_heat_lanes,
        )
        result["run_id"] = run_id
        result["current_meet_id"] = meet_id
        result["relay_status"] = relay_status(relay_path, internal_relay_sources)
        result["can_publish_current"] = False
        result["downloads"] = download_urls(run_id, result["files"])
        add_individual_download_urls(run_id, result)
        for swimmer in result.get("swimmers", [{"name": result.get("swimmer") or swimmer_names[0]}]):
            record_swimmer_lookup(str(swimmer.get("name") or ""), result["meet"].get("id"), source="current_meet")
        return result

    def handle_publish_current(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        run_id = str(payload.get("run_id", "")).strip()
        if not run_id:
            raise ValueError("Run id is required.")
        if not re.match(r"^[0-9]+-[a-f0-9]{8}$", run_id):
            raise ValueError("Run id is invalid.")

        run_dir = RUNS_DIR / run_id
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("This run cannot be saved to Current Meets.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("published_current_meet_id"):
            meet = resolve_current_meet(str(manifest["published_current_meet_id"]))
            return {"current_meet": public_current_meet(meet), "already_saved": True}

        meet = manifest.get("meet", {})
        uploads = manifest.get("uploads", {})
        meet_name = str(meet.get("name") or "Swim Meet")
        short_name = str(meet.get("short_name") or meet_name)
        state = str(manifest.get("state") or "").upper()
        dates = dates_label_from_sessions(manifest.get("sessions", []))
        start_date, end_date = date_bounds_from_sessions(manifest.get("sessions", []))
        meet_id = unique_current_meet_id(meet_name, dates)

        target_dir = HOSTED_MEETS_DIR / meet_id / "input"
        target_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "flyer": copy_hosted_upload(uploads.get("flyer"), target_dir),
            "psych": copy_hosted_upload(uploads.get("psych"), target_dir),
            "timeline": copy_hosted_upload(uploads.get("timeline"), target_dir),
            "relay": copy_hosted_upload(uploads.get("relay"), target_dir),
        }
        if not files["psych"] or not files["timeline"]:
            raise ValueError("A psych sheet and timeline are required before saving to Current Meets.")

        entry = {
            "id": meet_id,
            "name": meet_name,
            "short_name": short_name,
            "dates": dates,
            "start_date": start_date,
            "end_date": end_date,
            "expires_at": expiration_date(end_date),
            "state": state,
            "status": "ready",
            "files": files,
            "documents": hosted_document_labels(files),
        }
        data = {"current_meets": load_current_meets()}
        data["current_meets"].append(entry)
        write_json(CURRENT_MEETS_PATH, data)
        manifest["published_current_meet_id"] = meet_id
        write_json(manifest_path, manifest)
        return {"current_meet": public_current_meet(entry), "already_saved": False}

    def send_static(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            if STATIC_DIR.resolve() not in [resolved, *resolved.parents]:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not resolved.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content = resolved.read_bytes()
            mime = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)

    def send_download(self, path: str) -> None:
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) < 3:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        _download, run_id, *filename_parts = parts
        target = (RUNS_DIR / run_id / "outputs" / Path(*filename_parts)).resolve()
        allowed_root = (RUNS_DIR / run_id / "outputs").resolve()
        if allowed_root not in [target, *target.parents] or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = target.read_bytes()
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, payload: dict, status: int = 200) -> None:
        content = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}")


def form_value(form: cgi.FieldStorage, name: str) -> str:
    item = form[name] if name in form else None
    if item is None:
        return ""
    if isinstance(item, list):
        item = item[0]
    return item.value if isinstance(item.value, str) else ""


def form_values(form: cgi.FieldStorage, name: str) -> list[str]:
    if name not in form:
        return []
    item = form[name]
    if not isinstance(item, list):
        item = [item]
    return [field.value for field in item if isinstance(field.value, str)]


def form_bool(form: cgi.FieldStorage, name: str, default: bool = False) -> bool:
    if name not in form:
        return default
    value = form_value(form, name).strip().lower()
    return value not in {"", "0", "false", "off", "no"}


def payload_bool(payload: dict, name: str, default: bool = False) -> bool:
    if name not in payload:
        return default
    value = payload.get(name)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in {"", "0", "false", "off", "no"}


def payload_relay_options(payload: dict) -> list[str]:
    raw_options = payload.get("relay_options")
    if isinstance(raw_options, str):
        candidates = [raw_options]
    elif isinstance(raw_options, list):
        candidates = [str(option) for option in raw_options]
    else:
        candidates = []
    options: list[str] = []
    for option in candidates:
        cleaned = option.strip()
        if cleaned and cleaned not in options:
            options.append(cleaned)
    return options


def normalize_modes(raw_modes: object) -> list[str]:
    if isinstance(raw_modes, str):
        candidates = [raw_modes]
    elif isinstance(raw_modes, list):
        candidates = [str(mode) for mode in raw_modes]
    else:
        candidates = []
    modes: list[str] = []
    for mode in candidates:
        cleaned = mode.strip().lower()
        if cleaned in VALID_MODES and cleaned not in modes:
            modes.append(cleaned)
    return modes or DEFAULT_MODES.copy()


def swimmer_names_from_form(form: cgi.FieldStorage) -> list[str]:
    names = [name.strip() for name in form_values(form, "swimmer_names") if name.strip()]
    if not names:
        single = form_value(form, "swimmer_name").strip()
        if single:
            names = [single]
    return unique_swimmer_names(names)


def swimmer_names_from_payload(payload: dict) -> list[str]:
    raw_names = payload.get("swimmer_names")
    names: list[str] = []
    if isinstance(raw_names, list):
        names = [str(name).strip() for name in raw_names if str(name).strip()]
    if not names:
        single = str(payload.get("swimmer_name", "")).strip()
        if single:
            names = [single]
    return unique_swimmer_names(names)


def unique_swimmer_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        key = normalize_swimmer_for_stats(name)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def save_upload(form: cgi.FieldStorage, name: str, upload_dir: Path, required: bool) -> Path | None:
    if name not in form:
        if required:
            raise ValueError(f"{name} is required.")
        return None
    item = form[name]
    if isinstance(item, list):
        item = item[0]
    if not item.filename:
        if required:
            raise ValueError(f"{name} is required.")
        return None
    filename = safe_filename(item.filename)
    target = upload_dir / filename
    with target.open("wb") as fh:
        shutil.copyfileobj(item.file, fh)
    return target


def safe_filename(filename: str) -> str:
    cleaned = "".join(char for char in Path(filename).name if char.isalnum() or char in " ._-").strip()
    return cleaned or "upload.pdf"


def relative_path(path: Path | None) -> str | None:
    if path is None:
        return None
    return path.resolve().relative_to(ROOT).as_posix()


def analyze_swimmer_set(
    flyer_path: Path | None,
    psych_path: Path,
    timeline_path: Path,
    relay_path: Path | None,
    internal_relay_sources: list[Path] | None,
    swimmer_names: list[str],
    output_dir: Path,
    state: str,
    modes: list[str],
    combine_family: bool,
    estimate_heat_lanes: bool,
) -> dict:
    if len(swimmer_names) == 1:
        return analyze_uploads(
            flyer_pdf=flyer_path,
            psych_pdf=psych_path,
            timeline_pdf=timeline_path,
            swimmer_name=swimmer_names[0],
            output_dir=output_dir,
            relay_pdf=relay_path,
            internal_relay_sources=internal_relay_sources,
            state=state,
            modes=modes,
            estimate_heat_lanes=estimate_heat_lanes,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    individual_results: list[dict] = []
    warnings: list[str] = []
    combined_items: list[dict] = []
    used_dirs: set[str] = set()

    for index, swimmer_name in enumerate(swimmer_names, start=1):
        subdir_name = unique_output_subdir(index, swimmer_name, used_dirs)
        swimmer_output_dir = output_dir / subdir_name
        result = analyze_uploads(
            flyer_pdf=flyer_path,
            psych_pdf=psych_path,
            timeline_pdf=timeline_path,
            swimmer_name=swimmer_name,
            output_dir=swimmer_output_dir,
            relay_pdf=relay_path,
            internal_relay_sources=internal_relay_sources,
            state=state,
            modes=modes,
            estimate_heat_lanes=estimate_heat_lanes,
        )
        result["output_subdir"] = subdir_name
        result["files"] = {key: f"{subdir_name}/{name}" for key, name in result["files"].items()}
        for warning in result.get("warnings", []):
            warnings.append(f"{result['swimmer']}: {warning}")
        for item in result.get("items", []):
            combined_items.append({**item, "swimmer": result["swimmer"]})
        individual_results.append(result)

    first = individual_results[0]
    family_files: dict[str, str] = {}
    if combine_family:
        family_files = write_family_outputs(output_dir, individual_results, modes)

    return {
        "family": True,
        "combine_family": combine_family,
        "meet": first["meet"],
        "swimmer": f"Family ({len(individual_results)} swimmers)",
        "swimmers": [
            {
                "name": result["swimmer"],
                "requested_name": result.get("requested_swimmer"),
                "verified_event_count": result.get("verified_event_count", 0),
                "verified_relay_count": result.get("verified_relay_count", 0),
                "files": result.get("files", {}),
            }
            for result in individual_results
        ],
        "verified_event_count": sum(int(result.get("verified_event_count", 0)) for result in individual_results),
        "verified_relay_count": sum(int(result.get("verified_relay_count", 0)) for result in individual_results),
        "psych_match_pages": [],
        "events": combined_items,
        "relays": [],
        "items": sorted(combined_items, key=lambda item: item["sort_start"]),
        "files": family_files,
        "sessions": first.get("sessions", []),
        "warnings": warnings,
    }


def unique_output_subdir(index: int, swimmer_name: str, used_dirs: set[str]) -> str:
    base = f"{index}-{slugify_value(swimmer_name)}"
    candidate = base
    suffix = 2
    while candidate in used_dirs:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_dirs.add(candidate)
    return candidate


def write_family_outputs(output_dir: Path, individual_results: list[dict], modes: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for mode in modes:
        if mode not in {"daily", "weekend", "detailed"}:
            continue
        payloads = load_mode_payloads(output_dir, individual_results, mode)
        if not payloads:
            continue
        family_payload = build_family_payload(mode, payloads, individual_results)
        json_name = f"family-{mode}.json"
        ics_name = f"family-{mode}.ics"
        (output_dir / json_name).write_text(json.dumps(family_payload, indent=2), encoding="utf-8")
        (output_dir / ics_name).write_text(build_ics(family_payload), encoding="utf-8")
        files[f"family_{mode}_json"] = json_name
        files[f"family_{mode}_ics"] = ics_name
    return files


def load_mode_payloads(output_dir: Path, individual_results: list[dict], mode: str) -> list[dict]:
    payloads: list[dict] = []
    key = f"{mode}_json"
    for result in individual_results:
        file_value = result.get("files", {}).get(key)
        if not file_value:
            continue
        path = output_dir / file_value
        if path.is_file():
            payloads.append({"swimmer": result["swimmer"], "payload": json.loads(path.read_text(encoding="utf-8"))})
    return payloads


def build_family_payload(mode: str, payloads: list[dict], individual_results: list[dict]) -> dict:
    first_payload = payloads[0]["payload"]
    meet = first_payload.get("meet", {})
    short_name = str(meet.get("short_name") or meet.get("name") or "Swim Meet")
    swimmer_names = [str(result["swimmer"]) for result in individual_results]
    family_name = ", ".join(swimmer_names)
    if mode == "detailed":
        events = []
        for row in payloads:
            events.extend(row["payload"].get("events", []))
        events.sort(key=lambda event: event["start"])
        return {
            "calendar": {"name": f"Family - {short_name} Swim-by-Swim", "timezone": first_payload["calendar"].get("timezone", "America/Phoenix")},
            "meet": meet,
            "events": events,
        }

    daily_events = build_family_daily_events(payloads, meet, short_name, family_name)
    if mode == "daily":
        return {
            "calendar": {"name": f"Family - {short_name} Daily", "timezone": first_payload["calendar"].get("timezone", "America/Phoenix")},
            "meet": meet,
            "events": daily_events,
        }

    if not daily_events:
        return {"calendar": {"name": f"Family - {short_name} Whole Meet", "timezone": first_payload["calendar"].get("timezone", "America/Phoenix")}, "meet": meet, "events": []}
    start = min(event["start"] for event in daily_events)
    end = max(event["end"] for event in daily_events)
    lines = [family_name, short_name, "", "Meet summary:"]
    for event in daily_events:
        lines.extend(["", event["title"], *event["description_lines"][5:]])
    return {
        "calendar": {"name": f"Family - {short_name} Whole Meet", "timezone": first_payload["calendar"].get("timezone", "America/Phoenix")},
        "meet": meet,
        "events": [
            {
                "uid": f"{meet.get('id', 'swim-meet')}-family-weekend@swimtimeline",
                "title": f"Family - {short_name}: Whole Meet",
                "start": start,
                "end": end,
                "location": "Multiple meet facilities",
                "description_lines": lines,
            }
        ],
    }


def build_family_daily_events(payloads: list[dict], meet: dict, short_name: str, family_name: str) -> list[dict]:
    by_day: dict[str, list[dict]] = {}
    for row in payloads:
        swimmer = row["swimmer"]
        for event in row["payload"].get("events", []):
            day = str(event.get("start", ""))[:10]
            if not day:
                continue
            by_day.setdefault(day, []).append({"swimmer": swimmer, "event": event})

    events: list[dict] = []
    for day_number, (day, rows) in enumerate(sorted(by_day.items()), start=1):
        rows.sort(key=lambda row: row["event"]["start"])
        starts = [row["event"]["start"] for row in rows]
        ends = [row["event"]["end"] for row in rows]
        first_event = rows[0]["event"]
        day_name = display_day_name(first_event)
        lines = [
            family_name,
            short_name,
            "",
            f"Day: Day {day_number} ({day_name})",
            "Combined family calendar.",
        ]
        for row in rows:
            event = row["event"]
            swimmer = row["swimmer"]
            lines.extend(["", swimmer])
            swimmer_lines = event.get("description_lines", [])
            if len(swimmer_lines) > 9:
                lines.extend(swimmer_lines[9:])
            else:
                lines.extend(swimmer_lines)
        events.append(
            {
                "uid": f"{meet.get('id', 'swim-meet')}-family-{day}@swimtimeline",
                "title": f"{short_name}: Family Day {day_number} ({day_name})",
                "start": min(starts),
                "end": max(ends),
                "location": first_event.get("location", "Meet facility"),
                "description_lines": lines,
            }
        )
    return events


def display_day_name(event: dict) -> str:
    title = str(event.get("title", ""))
    match = re.search(r"\(([^)]+)\)\s*$", title)
    if match:
        return match.group(1)
    return str(event.get("start", ""))[:10]


def download_urls(run_id: str, files: dict) -> dict:
    return {key: f"/download/{run_id}/{name}" for key, name in files.items()}


def add_individual_download_urls(run_id: str, result: dict) -> None:
    for swimmer in result.get("swimmers", []):
        files = swimmer.get("files", {})
        swimmer["downloads"] = download_urls(run_id, files)
    for individual in result.get("individual_results", []):
        individual["downloads"] = download_urls(run_id, individual.get("files", {}))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def write_run_manifest(run_dir: Path, payload: dict) -> None:
    write_json(run_dir / "manifest.json", payload)


def load_usage_stats() -> dict:
    if not USAGE_STATS_PATH.exists():
        return {"total_lookups": 0, "swimmers": {}}
    try:
        data = json.loads(USAGE_STATS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"total_lookups": 0, "swimmers": {}}
    data.setdefault("total_lookups", 0)
    data.setdefault("swimmers", {})
    return data


def public_usage_stats() -> dict:
    data = load_usage_stats()
    swimmers = data.get("swimmers", {})
    return {
        "total_lookups": int(data.get("total_lookups") or 0),
        "unique_swimmer_names": len(swimmers),
        "last_lookup_at": data.get("last_lookup_at"),
    }


def record_swimmer_lookup(swimmer_name: str, meet_id: str | None, source: str) -> None:
    normalized = normalize_swimmer_for_stats(swimmer_name)
    if not normalized:
        return
    data = load_usage_stats()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    swimmer_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    swimmers = data.setdefault("swimmers", {})
    row = swimmers.setdefault(
        swimmer_hash,
        {
            "count": 0,
            "first_seen_at": now,
            "last_seen_at": now,
            "meet_ids": [],
            "sources": {},
        },
    )
    row["count"] = int(row.get("count") or 0) + 1
    row["last_seen_at"] = now
    meet_ids = row.setdefault("meet_ids", [])
    if meet_id and meet_id not in meet_ids:
        meet_ids.append(meet_id)
    sources = row.setdefault("sources", {})
    sources[source] = int(sources.get(source) or 0) + 1
    data["total_lookups"] = int(data.get("total_lookups") or 0) + 1
    data["last_lookup_at"] = now
    write_json(USAGE_STATS_PATH, data)


def normalize_swimmer_for_stats(swimmer_name: str) -> str:
    return re.sub(r"[^a-z]+", " ", swimmer_name.casefold()).strip()


def load_current_meets() -> list[dict]:
    if not CURRENT_MEETS_PATH.exists():
        return []
    data = json.loads(CURRENT_MEETS_PATH.read_text(encoding="utf-8"))
    return data.get("current_meets", [])


def public_meets_payload() -> dict:
    current_meets: list[dict] = []
    past_meets: list[dict] = []
    for meet in load_current_meets():
        public_meet = public_current_meet(meet)
        if current_meet_is_active(meet):
            current_meets.append(public_meet)
        else:
            past_meets.append(public_meet)
    return {"current_meets": current_meets, "past_meets": past_meets}


def public_current_meet(meet: dict) -> dict:
    files = meet.get("files", {})
    featured_until = parse_iso_date(str(meet.get("featured_until") or ""))
    is_featured = current_meet_is_featured(meet)
    relay_options = public_relay_options(meet)
    return {
        "id": meet.get("id"),
        "name": meet.get("name"),
        "short_name": meet.get("short_name"),
        "dates": meet.get("dates"),
        "start_date": meet.get("start_date"),
        "end_date": meet.get("end_date"),
        "expires_at": meet.get("expires_at"),
        "state": meet.get("state"),
        "status": meet.get("status"),
        "documents": meet.get("documents", []),
        "has_relay": bool(files.get("relay")),
        "has_private_relay": bool(relay_options),
        "relay_options": relay_options,
        "is_featured": is_featured,
        "featured_until": meet.get("featured_until"),
        "featured_until_label": short_date_label(featured_until) if featured_until else "",
        "featured_label": meet.get("featured_label") or "Featured current meet",
        "featured_note": meet.get("featured_note") or "",
    }


def public_relay_options(meet: dict) -> list[dict]:
    options: list[dict] = []
    for option in meet.get("relay_options", []):
        if not isinstance(option, dict) or not option.get("id"):
            continue
        options.append(
            {
                "id": option.get("id"),
                "label": option.get("label") or "Include relay lineup",
                "club": option.get("club") or "",
                "description": option.get("description") or "",
            }
        )
    return options


def current_meet_is_active(meet: dict) -> bool:
    expires_at = parse_iso_date(str(meet.get("expires_at") or ""))
    if expires_at:
        return date.today() <= expires_at
    end_date = parse_iso_date(str(meet.get("end_date") or ""))
    if end_date:
        return date.today() <= end_date
    return True


def current_meet_is_featured(meet: dict) -> bool:
    if not meet.get("featured"):
        return False
    featured_until = parse_iso_date(str(meet.get("featured_until") or ""))
    if featured_until:
        return date.today() <= featured_until
    return current_meet_is_active(meet)


def parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def short_date_label(value: date) -> str:
    return f"{value.strftime('%A')}, {value.strftime('%b')} {value.day}"


def resolve_current_meet(meet_id: str) -> dict:
    for meet in load_current_meets():
        if meet.get("id") == meet_id:
            return meet
    raise ValueError(f"Unknown current meet: {meet_id}")


def resolve_current_meet_relay_sources(meet: dict, relay_option_ids: list[str]) -> list[Path]:
    if not relay_option_ids:
        return []
    options = {str(option.get("id")): option for option in meet.get("relay_options", []) if isinstance(option, dict)}
    sources: list[Path] = []
    for option_id in relay_option_ids:
        option = options.get(option_id)
        if not option:
            raise ValueError("Selected relay option is not available for this meet.")
        source = resolve_repo_file(option.get("source"), required=True)
        assert source is not None
        sources.append(source)
    return sources


def relay_status(relay_path: Path | None, internal_relay_sources: list[Path]) -> str:
    if relay_path and internal_relay_sources:
        return "uploaded_and_private_relay_parsed"
    if internal_relay_sources:
        return "private_relay_parsed"
    if relay_path:
        return "hosted_and_parsed"
    return "not_uploaded"


def resolve_repo_file(path_value: str | None, required: bool) -> Path | None:
    if not path_value:
        if required:
            raise ValueError("Current meet is missing a required document.")
        return None
    target = (ROOT / path_value).resolve()
    if ROOT not in [target, *target.parents]:
        raise ValueError("Current meet document path is outside the workspace.")
    if not target.is_file():
        if required:
            raise ValueError(f"Current meet document not found: {path_value}")
        return None
    return target


def copy_hosted_upload(path_value: str | None, target_dir: Path) -> str | None:
    if not path_value:
        return None
    source = resolve_repo_file(path_value, required=True)
    assert source is not None
    target = target_dir / safe_filename(source.name)
    shutil.copy2(source, target)
    return relative_path(target)


def hosted_document_labels(files: dict[str, str | None]) -> list[str]:
    labels = [
        ("flyer", "Meet flyer"),
        ("psych", "Psych/heat sheet"),
        ("timeline", "Final timeline"),
        ("relay", "Relay document"),
    ]
    return [label for key, label in labels if files.get(key)]


def dates_label_from_sessions(sessions: list[dict]) -> str:
    dates = sorted({str(session.get("date")) for session in sessions if session.get("date")})
    if not dates:
        return ""
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]} through {dates[-1]}"


def date_bounds_from_sessions(sessions: list[dict]) -> tuple[str, str]:
    dates = sorted({str(session.get("date")) for session in sessions if session.get("date")})
    if not dates:
        return "", ""
    return dates[0], dates[-1]


def expiration_date(end_date: str) -> str:
    parsed = parse_iso_date(end_date)
    if not parsed:
        return ""
    return (parsed + timedelta(days=1)).isoformat()


def unique_current_meet_id(meet_name: str, dates: str) -> str:
    slug = slugify_value(meet_name)
    year_match = re.search(r"\b(20\d{2})\b", dates)
    if year_match and not slug.startswith(year_match.group(1)):
        base = f"{year_match.group(1)}-{slug}"
    else:
        base = slug
    existing = {str(meet.get("id")) for meet in load_current_meets()}
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


def slugify_value(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "swim-meet"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", default=int(os.environ.get("PORT", "8765")), type=int)
    args = parser.parse_args()

    RUNS_DIR.mkdir(exist_ok=True)
    httpd = ThreadingHTTPServer((args.host, args.port), SwimTimelineHandler)
    print(f"SwimTimeline running at http://{args.host}:{args.port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
