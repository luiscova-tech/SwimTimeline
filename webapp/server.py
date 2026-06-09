#!/usr/bin/env python3
"""Local SwimTimeline web server."""

from __future__ import annotations

import argparse
import cgi
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
HOSTED_MEETS_DIR = ROOT / "meets" / "current-hosted"
sys.path.insert(0, str(ROOT))

from swimtimeline.extract import analyze_uploads


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
            self.send_json({"current_meets": public_current_meets()})
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

        swimmer_name = form_value(form, "swimmer_name").strip()
        if not swimmer_name:
            raise ValueError("Swimmer name is required.")

        state = (form_value(form, "state") or "AZ").strip().upper()
        modes = form_values(form, "modes") or ["daily", "weekend", "detailed"]

        run_id = f"{int(time.time())}-{uuid4().hex[:8]}"
        run_dir = RUNS_DIR / run_id
        upload_dir = run_dir / "uploads"
        output_dir = run_dir / "outputs"
        upload_dir.mkdir(parents=True, exist_ok=True)

        flyer_path = save_upload(form, "flyer_pdf", upload_dir, required=False)
        psych_path = save_upload(form, "psych_pdf", upload_dir, required=True)
        timeline_path = save_upload(form, "timeline_pdf", upload_dir, required=True)
        relay_path = save_upload(form, "relay_pdf", upload_dir, required=False)

        result = analyze_uploads(
            flyer_pdf=flyer_path,
            psych_pdf=psych_path,
            timeline_pdf=timeline_path,
            swimmer_name=swimmer_name,
            output_dir=output_dir,
            relay_pdf=relay_path,
            state=state,
            modes=modes,
        )
        result["run_id"] = run_id
        result["relay_status"] = "uploaded_and_parsed" if relay_path else "not_uploaded"
        result["can_publish_current"] = True
        result["downloads"] = {
            key: f"/download/{run_id}/{name}" for key, name in result["files"].items()
        }
        write_run_manifest(
            run_dir,
            {
                "run_id": run_id,
                "swimmer": swimmer_name,
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
        swimmer_name = str(payload.get("swimmer_name", "")).strip()
        modes = payload.get("modes") or ["daily", "weekend", "detailed"]
        if not meet_id:
            raise ValueError("Current meet id is required.")
        if not swimmer_name:
            raise ValueError("Swimmer name is required.")

        meet = resolve_current_meet(meet_id)
        state = str(payload.get("state") or meet.get("state") or "AZ").strip().upper()
        files = meet.get("files", {})
        flyer_path = resolve_repo_file(files.get("flyer"), required=False)
        psych_path = resolve_repo_file(files.get("psych"), required=True)
        timeline_path = resolve_repo_file(files.get("timeline"), required=True)
        relay_path = resolve_repo_file(files.get("relay"), required=False)

        run_id = f"{int(time.time())}-{uuid4().hex[:8]}"
        output_dir = RUNS_DIR / run_id / "outputs"
        result = analyze_uploads(
            flyer_pdf=flyer_path,
            psych_pdf=psych_path,
            timeline_pdf=timeline_path,
            swimmer_name=swimmer_name,
            output_dir=output_dir,
            relay_pdf=relay_path,
            state=state,
            modes=modes,
        )
        result["run_id"] = run_id
        result["current_meet_id"] = meet_id
        result["relay_status"] = "hosted_and_parsed" if relay_path else "not_uploaded"
        result["can_publish_current"] = False
        result["downloads"] = {
            key: f"/download/{run_id}/{name}" for key, name in result["files"].items()
        }
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
        state = str(manifest.get("state") or "AZ").upper()
        dates = dates_label_from_sessions(manifest.get("sessions", []))
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
        if len(parts) != 3:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        _download, run_id, filename = parts
        target = (RUNS_DIR / run_id / "outputs" / filename).resolve()
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


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def write_run_manifest(run_dir: Path, payload: dict) -> None:
    write_json(run_dir / "manifest.json", payload)


def load_current_meets() -> list[dict]:
    if not CURRENT_MEETS_PATH.exists():
        return []
    data = json.loads(CURRENT_MEETS_PATH.read_text(encoding="utf-8"))
    return data.get("current_meets", [])


def public_current_meets() -> list[dict]:
    return [public_current_meet(meet) for meet in load_current_meets()]


def public_current_meet(meet: dict) -> dict:
    files = meet.get("files", {})
    return {
        "id": meet.get("id"),
        "name": meet.get("name"),
        "short_name": meet.get("short_name"),
        "dates": meet.get("dates"),
        "state": meet.get("state"),
        "status": meet.get("status"),
        "documents": meet.get("documents", []),
        "has_relay": bool(files.get("relay")),
    }


def resolve_current_meet(meet_id: str) -> dict:
    for meet in load_current_meets():
        if meet.get("id") == meet_id:
            return meet
    raise ValueError(f"Unknown current meet: {meet_id}")


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
        ("psych", "Psych sheet"),
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
