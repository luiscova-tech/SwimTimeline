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
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

# mimetypes.guess_type() has no built-in ".ics" entry of its own -- on this Mac it resolves via
# the system's /etc/mime.types, which a minimal Linux container (e.g. Render's) is not guaranteed
# to have installed at all, in which case guess_type() would silently fall back to
# application/octet-stream and mobile Safari/Chrome would offer a generic file download instead of
# an "Add to Calendar" prompt. Registering it explicitly makes the mapping correct everywhere,
# regardless of what mime database (if any) happens to be present on the host.
mimetypes.add_type("text/calendar", ".ics")

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNS_DIR = ROOT / ".swimtimeline-runs"
CURRENT_MEETS_PATH = ROOT / "data" / "current_meets.json"
USAGE_STATS_PATH = ROOT / "data" / "usage_stats.json"
HOSTED_MEETS_DIR = ROOT / "meets" / "current-hosted"
DEFAULT_MODES = ["daily"]
VALID_MODES = {"daily", "weekend", "detailed"}
# /subscribe.ics is polled repeatedly, forever, by calendar apps -- unlike every other endpoint
# here, which is called once per user action. A short in-memory TTL absorbs bursts (e.g. a
# family's several devices all refreshing around the same time) without re-parsing PDFs for each
# one, while staying well under any calendar app's own poll interval so "add a heat sheet mid-meet"
# still shows up on the next real poll. Render's free tier runs a single instance, so a plain
# in-process dict is enough -- no shared cache needed, and it can't grow unbounded in practice
# (bounded by distinct meet/swimmer/option combinations, and reset on every process restart).
SUBSCRIBE_CACHE_TTL_SECONDS = 300
UPLOAD_FIELD_LABELS = {
    "flyer_pdf": "Meet Flyer",
    "psych_pdf": "Psych Sheet or Heat Sheet",
    "timeline_pdf": "Timeline",
    "relay_pdf": "Relay Doc",
    "warmup_pdf": "Warm-up Assignments",
}
sys.path.insert(0, str(ROOT))

from swimtimeline.badges import (
    card_filename,
    cards_for_timeline,
    is_ambiguous_warning,
    render_cards_pdf,
    render_handout_sheet_pdf,
    render_sheet_pdf,
)
from swimtimeline.failure_alerts import FailureReport, InputError, notify_failure
from swimtimeline.extract import analyze_uploads, extract_text_pages, resolve_meet_timezone
from swimtimeline.ics import build_ics


class SwimTimelineHandler(BaseHTTPRequestHandler):
    server_version = "SwimTimeline/0.1"

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
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
        if path == "/subscribe.ics":
            # Current-Meets-only live feed (see send_subscribe_ics). Every other route above only
            # cares about the path, so this is the only one that needs the query string parsed.
            self.send_subscribe_ics(parse_qs(parsed.query))
            return
        if path == "/api/health":
            self.send_json({"ok": True})
            return
        if path == "/api/current-meets":
            self.send_json(public_meets_payload())
            return
        if path == "/officials":
            self.send_static(STATIC_DIR / "officials.html")
            return
        if path == "/api/officials/meets":
            # do_GET has no blanket handler of its own, so without this an exception here would be
            # an unhandled 500 with a traceback and no alert at all. That is exactly the case
            # worth an email -- nobody's input can make listing the hosted meets fail.
            try:
                self.send_json(officials_meets_payload())
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=500)
                self.report_officials_failure("/api/officials/meets", exc, {})
            return
        if path == "/api/officials/badges":
            # A GET (not POST) so the browser can download it with a plain link, same as
            # /download/... -- there is no request body, only the meet/token the caller already has.
            query = parse_qs(parsed.query)
            try:
                self.send_badges_pdf(query)
            except Exception as exc:
                # send_badges_pdf already answers its own errors with a 400; reaching here means
                # even that failed (e.g. the write itself), which is never routine input.
                self.report_officials_failure("/api/officials/badges", exc, query, stage="responding")
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
            if self.path == "/api/officials/sessions":
                # Its own try/except, not the blanket one below: every failure here (a bad upload,
                # a real PDF that just isn't a valid Session Report, an unknown meet id) is a
                # client-input problem, not a server bug, so it gets 400 -- the same contract
                # send_badges_pdf() already uses for the rest of this feature (see its own
                # docstring), not the generic 500 every other JSON endpoint below falls through to.
                try:
                    result = self.handle_officials_sessions()
                except Exception as exc:
                    # Response first, alert second, and only for real failures -- a psych sheet
                    # uploaded as a timeline is a genuine document-processing failure worth an
                    # email, while "choose a meet" is not. See failure_alerts.
                    self.send_json({"error": str(exc)}, status=400)
                    self.report_officials_failure(
                        "/api/officials/sessions", exc, getattr(self, "_officials_params", {})
                    )
                    return
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
        warmup_path = save_upload(form, "warmup_pdf", upload_dir, required=False)

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
            warmup_path=warmup_path,
            include_relays=bool(relay_path),
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
                    "warmup": relative_path(warmup_path),
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
        # General opt-in for tentative team-entered relays, decoupled from the private-roster
        # add-ons above -- those only exist for meets with a roster configured (currently AZ-only),
        # so this is the only opt-in path for every other meet.
        show_team_relays = payload_bool(payload, "show_team_relays", default=False)
        if not meet_id:
            raise ValueError("Current meet id is required.")
        if not swimmer_names:
            raise ValueError("At least one swimmer name is required.")

        meet = resolve_current_meet(meet_id)
        # The State/LSC field is the SWIMMER's LSC, so a blank stays blank -- build_swim_events then
        # auto-detects each swimmer's LSC from their own parsed team code. The meet record's "state"
        # is the VENUE's state (WZAG is in ID while its AZ swimmers need AZSI); substituting it here
        # used to masquerade as an explicitly-entered LSC and block auto-detection entirely.
        state = str(payload.get("state") or "").strip().upper()
        docs = resolve_current_meet_documents(meet)
        internal_relay_sources = resolve_current_meet_relay_sources(meet, relay_option_ids)

        run_id = f"{int(time.time())}-{uuid4().hex[:8]}"
        output_dir = RUNS_DIR / run_id / "outputs"
        result = analyze_swimmer_set(
            flyer_path=docs["flyer_path"],
            psych_path=docs["psych_path"],
            timeline_path=docs["timeline_path"],
            relay_path=docs["relay_path"],
            internal_relay_sources=internal_relay_sources,
            swimmer_names=swimmer_names,
            output_dir=output_dir,
            state=state,
            modes=modes,
            combine_family=combine_family,
            estimate_heat_lanes=estimate_heat_lanes,
            meet_timezone=docs["meet_timezone"],
            meet_venue=docs["meet_venue"],
            timeline_projected=docs["timeline_projected"],
            warmup_path=docs["warmup_path"],
            meet_warmup_window=docs["meet_warmup_window"],
            heat_sheet_paths=docs["heat_sheet_paths"],
            distance_timeline_path=docs["distance_timeline_path"],
            # The relay add-on checkboxes (or the general "show my team's entered relays" toggle)
            # are the parent's opt-in. All unchecked -> no relay output at all, confirmed or
            # tentative, exactly as before tentative relays existed.
            include_relays=bool(relay_option_ids or docs["relay_path"] or show_team_relays),
        )
        result["run_id"] = run_id
        result["current_meet_id"] = meet_id
        result["relay_status"] = relay_status(docs["relay_path"], internal_relay_sources)
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
            "flyer": copy_hosted_upload(uploads.get("flyer"), target_dir, label="Meet Flyer"),
            "psych": copy_hosted_upload(uploads.get("psych"), target_dir, label="Psych Sheet or Heat Sheet"),
            "timeline": copy_hosted_upload(uploads.get("timeline"), target_dir, label="Timeline"),
            "relay": copy_hosted_upload(uploads.get("relay"), target_dir, label="Relay Doc"),
            "warmup": copy_hosted_upload(uploads.get("warmup"), target_dir, label="Warm-up Assignments"),
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
            # Best-effort: `state` here is whatever the uploader typed in the
            # State/LSC field, which for a traveling swimmer may not match the
            # meet's actual venue. Correct the "timezone" field by hand in
            # data/current_meets.json if this guess is wrong for the venue.
            "timezone": resolve_meet_timezone(state=state),
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

    def handle_officials_sessions(self) -> dict:
        """The session list for the badge-card page: either a hosted meet's own timeline, or one
        the official uploads. An upload is saved under RUNS_DIR and answered with a token so the
        follow-up badge download does not have to re-upload the same PDF.
        """
        # Recorded as they are parsed so a failure email can say what was asked for even when
        # the request blew up partway through -- read back by do_POST's alert path.
        self._officials_params = {}
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            self._officials_params["token"] = "(upload)"
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                    "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                },
            )
            run_id = f"{int(time.time())}-{uuid4().hex[:8]}"
            upload_dir = RUNS_DIR / run_id / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            # Saved under FIXED names, and read back by name -- see officials_upload_paths. The
            # psych sheet is optional: without it there is nothing to match swimmers against, so
            # cards still generate, just with no highlights.
            save_officials_upload(form, "timeline_pdf", upload_dir / "timeline.pdf", required=True)
            save_officials_upload(form, "psych_pdf", upload_dir / "psych.pdf", required=False)
            swimmer_names = swimmer_names_from_form(form)
            self._officials_params.update({"token": run_id, "swimmer_names": swimmer_names})
            meet_name, cards, highlights = officials_cards_for_token(run_id, swimmer_names)
            self.report_officials_psych_failures(
                "/api/officials/sessions", highlights, self._officials_params
            )
            return {
                "source": "upload",
                "token": run_id,
                "meet_id": None,
                "meet_name": meet_name,
                "sessions": [officials_session_summary(card) for card in cards],
                **officials_highlight_payload(highlights, swimmer_names),
            }

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        meet_id = str(payload.get("meet_id", "")).strip()
        if not meet_id:
            raise InputError("Choose a hosted meet or upload a Session Report PDF.")
        swimmer_names = swimmer_names_from_payload(payload)
        self._officials_params.update({"meet_id": meet_id, "swimmer_names": swimmer_names})
        meet, cards, highlights = officials_cards_for_meet(meet_id, swimmer_names)
        self.report_officials_psych_failures(
            "/api/officials/sessions", highlights, self._officials_params
        )
        return {
            "source": "current_meet",
            "token": None,
            "meet_id": meet_id,
            "meet_name": str(meet.get("name") or "Swim Meet"),
            "sessions": [officials_session_summary(card) for card in cards],
            **officials_highlight_payload(highlights, swimmer_names),
        }

    def send_badges_pdf(self, query: dict) -> None:
        """The badge-card PDF itself. Four shapes, all built from the same per-session draw_card()
        call, so a card's content never differs between them:

          * default            -- one 144x216pt page per session (a page IS a card, so a printed
                                  page can be cut out and worn in a badge holder)
          * ?session=N         -- the same thing for one session, i.e. a one-page version
          * ?layout=sheet      -- those same native-size cards tiled on shared letter sheets, so a
                                  whole meet's reference schedule isn't N mostly-blank pages
          * ?layout=handout    -- ONE session's card repeated `copies` times, tiled the same way,
                                  to hand out to that session's officials (the original spec's
                                  deferred "12-up" idea)

        `layout` (not `format`) because it selects an arrangement of the same content, whereas
        "format" would imply a different file type; its values name the arrangement ("cards" /
        "sheet" / "handout") rather than being a bare on/off flag, which is exactly what let this
        fourth value slot in without a new parameter. Unknown values are rejected rather than
        silently defaulting, so a typo can't quietly hand back the wrong document.

        `highlighted_only=1` drops sessions no named swimmer is entered in. It reads as a filter
        next to `session`'s selection, and matches the `swimmer_names` it depends on. It is applied
        AFTER `session`, with no special-casing: asking for one specific session AND a filter that
        excludes it is contradictory, and falls into the same clear zero-match error below rather
        than silently ignoring one of the two.

        `copies=N` is only meaningful paired with `layout=handout` AND an explicit `session` --
        repeating "the meet" doesn't mean anything, so either one missing is a clear error rather
        than a silent guess. The cap on N (see badges.MAX_HANDOUT_COPIES) is enforced inside
        render_handout_sheet_pdf(), not here, so there is one place that owns the number.
        """
        try:
            meet_id = str((query.get("meet_id") or [""])[0]).strip()
            token = str((query.get("token") or [""])[0]).strip()
            session_raw = str((query.get("session") or [""])[0]).strip()
            layout = str((query.get("layout") or ["cards"])[0]).strip().lower() or "cards"
            if layout not in {"cards", "sheet", "handout"}:
                raise InputError(f"Unknown layout '{layout}'. Use 'cards', 'sheet', or 'handout'.")
            copies_raw = str((query.get("copies") or [""])[0]).strip()
            if (layout == "handout" or copies_raw) and not session_raw:
                raise InputError(
                    "The handout layout repeats one session's card, so an explicit session is "
                    "required -- repeating the whole meet's schedule doesn't mean anything."
                )
            if copies_raw and layout != "handout":
                raise InputError("copies is only used with layout=handout.")
            copies: int | None = None
            if layout == "handout":
                if not copies_raw:
                    raise InputError("layout=handout needs a copies=N param saying how many to print.")
                if not copies_raw.isdigit() or int(copies_raw) < 1:
                    raise InputError("copies must be a positive whole number.")
                copies = int(copies_raw)
            highlighted_only = query_bool(query, "highlighted_only")
            # Same "swimmer_names" field name the rest of the app uses, repeated once per swimmer
            # so the page can hand the exact list it already validated straight to the download.
            swimmer_names = unique_swimmer_names(
                [name.strip() for name in (query.get("swimmer_names") or []) if name.strip()]
            )
            if highlighted_only and not swimmer_names:
                raise InputError(
                    "highlighted_only needs at least one swimmer name to filter by."
                )
            if meet_id:
                meet, cards, highlights = officials_cards_for_meet(meet_id, swimmer_names)
                meet_name = str(meet.get("name") or "Swim Meet")
            elif token:
                meet_name, cards, highlights = officials_cards_for_token(token, swimmer_names)
            else:
                raise InputError("A hosted meet id or an upload token is required.")

            if session_raw:
                if not session_raw.isdigit():
                    raise InputError("Session must be a number.")
                wanted = int(session_raw)
                cards = [card for card in cards if card.session_number == wanted]
                if not cards:
                    raise InputError(f"Session {wanted} is not in this meet's timeline.")

            if highlighted_only:
                # Reuses the per-card highlight list the page's own session table already reads --
                # no second "is this session interesting" rule to drift out of step with it.
                cards = [card for card in cards if card.highlighted_event_numbers]
                if not cards:
                    named = ", ".join(swimmer_names)
                    raise InputError(
                        f"No session at this meet has an event for {named}, so there is nothing "
                        "to print. Clear the 'only sessions with my swimmer(s)' filter, or check "
                        "the spelling of the name(s)."
                    )

            if layout == "handout":
                content = render_handout_sheet_pdf(cards[0], copies)
            elif layout == "sheet":
                content = render_sheet_pdf(cards)
            else:
                content = render_cards_pdf(cards)
            filename = card_filename(
                meet_name,
                cards[0] if session_raw else None,
                layout=layout,
                highlighted_only=highlighted_only,
                copies=copies,
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            self.report_officials_psych_failures("/api/officials/badges", highlights, query)
        except Exception as exc:  # Same visible-error contract as the JSON endpoints.
            # The response goes out FIRST and is never affected by the alerting: notify_failure()
            # cannot raise, and only emails when this is a real failure rather than an InputError
            # the caller simply needs to correct.
            self.send_json({"error": str(exc)}, status=400)
            self.report_officials_failure("/api/officials/badges", exc, query)

    def report_officials_psych_failures(self, endpoint: str, highlights, query: dict) -> None:
        """Alert on psych-sheet failures that swimmer_event_numbers() deliberately swallowed.

        Those are caught per name on purpose -- one unreadable name must not cost the others their
        highlights -- so they never reach an except block and would otherwise be invisible. The
        request still succeeded, so this changes nothing the caller sees; it only means a psych
        sheet that will not parse reaches the maintainer instead of dying as a page warning.
        """
        for name, exc in getattr(highlights, "processing_errors", []) or []:
            self.report_officials_failure(
                endpoint, exc, query, stage=f"matching swimmer {name!r} against the psych sheet"
            )

    def report_officials_failure(self, endpoint: str, exc: BaseException, query: dict, stage: str = "") -> None:
        """Email one Officials failure, if it is a real one. Deliberately the LAST thing any
        handler does, after the response is already written, and incapable of raising -- see
        failure_alerts.notify_failure. Scoped to the Officials endpoints on purpose: the family
        flow has its own long-standing behaviour and is not part of this alerting.
        """
        notify_failure(
            FailureReport(
                endpoint=endpoint,
                exc=exc,
                params=officials_failure_params(query),
                stage=stage,
            ),
            logger=lambda message: self.log_message("%s", message),
        )

    def send_subscribe_ics(self, query: dict[str, list[str]]) -> None:
        try:
            ics_bytes, filename = build_subscribe_ics(query)
        except SubscribeError as exc:
            self.send_error(exc.status, str(exc))
            return
        except Exception as exc:  # Never let a parsing bug 500 a feed a calendar app polls forever.
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        # "inline", not "attachment" (unlike send_download below): this URL is meant to be fetched
        # and parsed by calendar software on its own schedule, not saved as a file by a human.
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        # Calendar apps and any intermediate proxy/CDN should re-poll on their own cadence rather
        # than pin this response for a long time -- max-age matches our own short server-side
        # cache, so allowing a shared cache to hold it that long adds no real extra staleness.
        self.send_header("Cache-Control", f"public, max-age={SUBSCRIBE_CACHE_TTL_SECONDS}, must-revalidate")
        self.send_header("Content-Length", str(len(ics_bytes)))
        self.end_headers()
        self.wfile.write(ics_bytes)

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
        # Calendar files are always written as UTF-8 text (see build_ics) and may contain
        # non-ASCII characters (accented names, punctuation); be explicit rather than let a
        # client fall back to guessing an encoding for a downloaded file.
        if mime == "text/calendar":
            mime = "text/calendar; charset=utf-8"
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
            raise ValueError(f"{UPLOAD_FIELD_LABELS.get(name, name)} is required.")
        return None
    item = form[name]
    if isinstance(item, list):
        item = item[0]
    if not item.filename:
        if required:
            raise ValueError(f"{UPLOAD_FIELD_LABELS.get(name, name)} is required.")
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
    meet_timezone: str | None = None,
    meet_venue: str | None = None,
    timeline_projected: bool = False,
    warmup_path: Path | None = None,
    meet_warmup_window: str | None = None,
    heat_sheet_paths: list[Path] | None = None,
    distance_timeline_path: Path | None = None,
    include_relays: bool = False,
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
            meet_timezone=meet_timezone,
            meet_venue=meet_venue,
            timeline_projected=timeline_projected,
            warmup_pdf=warmup_path,
            meet_warmup_window=meet_warmup_window,
            heat_sheet_pdfs=heat_sheet_paths,
            distance_timeline_pdf=distance_timeline_path,
            include_relays=include_relays,
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
            meet_timezone=meet_timezone,
            meet_venue=meet_venue,
            timeline_projected=timeline_projected,
            warmup_pdf=warmup_path,
            meet_warmup_window=meet_warmup_window,
            heat_sheet_pdfs=heat_sheet_paths,
            distance_timeline_pdf=distance_timeline_path,
            include_relays=include_relays,
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
                "tentative_relay_count": result.get("tentative_relay_count", 0),
                "ambiguous_swimmer_match": bool(result.get("ambiguous_swimmer_match")),
                "files": result.get("files", {}),
            }
            for result in individual_results
        ],
        # True when ANY swimmer's name was ambiguous. Without this the family UI reported "Calendar
        # ready" while silently omitting that child, since the others made verifiedTotal non-zero.
        "ambiguous_swimmer_match": any(bool(result.get("ambiguous_swimmer_match")) for result in individual_results),
        "verified_event_count": sum(int(result.get("verified_event_count", 0)) for result in individual_results),
        "verified_relay_count": sum(int(result.get("verified_relay_count", 0)) for result in individual_results),
        "tentative_relay_count": sum(int(result.get("tentative_relay_count", 0)) for result in individual_results),
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


# ---------------------------------------------------------------------------
# Officials' badge cards (/officials)
# ---------------------------------------------------------------------------


def officials_meets_payload() -> dict:
    """Hosted meets whose own timeline can drive badge cards, split current/past by exactly the
    same expiry rule the family-facing list uses (current_meet_is_active), so the two lists can't
    disagree about what "current" means.

    Extra filter beyond that: the meet must actually have a timeline file, and must not be in a
    NOT_READY status -- "schedule-only" meets carry a meet-packet schedule rather than a HY-TEK
    Session Report, which has no heat counts to put on a card.
    """
    current_meets: list[dict] = []
    past_meets: list[dict] = []
    for meet in load_current_meets():
        files = meet.get("files", {})
        if not files.get("timeline"):
            continue
        if str(meet.get("status") or "") in NOT_READY_STATUSES:
            continue
        entry = {
            "id": meet.get("id"),
            "name": meet.get("name"),
            "short_name": meet.get("short_name"),
            "dates": meet.get("dates"),
            "state": meet.get("state"),
            "timeline_label": timeline_document_label(meet),
        }
        if current_meet_is_active(meet):
            current_meets.append(entry)
        else:
            past_meets.append(entry)
    return {"current_meets": current_meets, "past_meets": past_meets}


def officials_failure_params(query: dict) -> dict:
    """The request params worth repeating back in a failure email, flattened out of either a
    parsed query string (lists per key, from /api/officials/badges) or an already-flat dict (from
    the POST handler). Never includes uploaded file contents -- only what someone would retype.
    """
    flattened: dict = {}
    for key, value in (query or {}).items():
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            # swimmer_names is legitimately repeated; everything else uses its first value.
            flattened[key] = list(value) if key == "swimmer_names" else value[0]
        else:
            flattened[key] = value
    return flattened


def officials_session_summary(card) -> dict:
    """One session as the page's picker needs it -- no PDF, just what to label the option with."""
    return {
        "session_number": card.session_number,
        "session_name": card.session_name,
        "session_label": card.session_label,
        "date_label": card.date_label,
        "start_label": card.start_label,
        "finish_label": card.finish_label,
        "heat_interval": card.heat_interval,
        "age_qualifier": card.age_qualifier,
        "event_count": card.event_count,
        "highlighted_events": card.highlighted_event_numbers,
    }


def officials_highlight_payload(highlights, swimmer_names: list[str]) -> dict:
    """The swimmer-highlight half of the sessions response.

    Warnings are split so the page can distinguish "be more specific" from "not found" -- the same
    distinction the family payload draws off AMBIGUOUS_NAME_MARKER -- and matched swimmers are
    reported per name so an official can see WHY a name contributed nothing.
    """
    return {
        "swimmer_names": swimmer_names,
        "highlighted_events": sorted(highlights.event_numbers),
        "matched_swimmers": [
            {"name": name, "event_numbers": numbers} for name, numbers in highlights.matched.items()
        ],
        "swimmer_warnings": [
            {"message": warning, "ambiguous": is_ambiguous_warning(warning)}
            for warning in highlights.warnings
        ],
    }


def officials_cards_for_meet(meet_id: str, swimmer_names: list[str] | None = None):
    """(meet record, cards, highlights) for a hosted meet, reusing resolve_current_meet for the
    lookup and resolve_repo_file for the same path-containment check every other hosted document
    goes through. Only the timeline is required here -- the flyer is read when present because
    parse_timeline uses it to fall back on a date range the timeline itself may not state, and the
    meet's own psych sheet is read when swimmer names were given, to star their events.
    """
    meet = resolve_current_meet(meet_id)
    files = meet.get("files", {})
    timeline_path = resolve_repo_file(files.get("timeline"), required=True, label="Timeline")
    assert timeline_path is not None
    flyer_path = resolve_repo_file(files.get("flyer"), required=False, label="Meet Flyer")
    flyer_text = "\n".join(extract_text_pages(flyer_path)) if flyer_path else ""
    psych_path = (
        resolve_repo_file(files.get("psych"), required=False, label="Psych Sheet or Heat Sheet")
        if swimmer_names
        else None
    )
    _meet_name, cards, highlights = cards_for_timeline(
        timeline_path,
        flyer_text=flyer_text,
        meet_venue=meet.get("venue") or None,
        psych_pdf=psych_path,
        swimmer_names=swimmer_names,
    )
    return meet, cards, highlights


def officials_upload_paths(token: str) -> tuple[Path, Path | None]:
    """(timeline, psych-or-None) for an official's upload, addressed by FIXED filenames.

    This used to glob "*.pdf" and take the first result, which was a latent bug the moment a
    second file joined the directory: "psych.pdf" sorts before "timeline.pdf", so the psych sheet
    would silently have been parsed AS the timeline. Uploads are written under known names by
    handle_officials_sessions and read back by those names here, so neither file can stand in for
    the other.
    """
    if not re.match(r"^[0-9]+-[a-f0-9]{8}$", token):
        raise InputError("Upload token is invalid.")
    upload_dir = (RUNS_DIR / token / "uploads").resolve()
    if RUNS_DIR.resolve() not in upload_dir.parents or not upload_dir.is_dir():
        raise InputError("That upload has expired. Please upload the Session Report again.")
    timeline_path = upload_dir / "timeline.pdf"
    if not timeline_path.is_file():
        raise InputError("That upload has expired. Please upload the Session Report again.")
    psych_path = upload_dir / "psych.pdf"
    return timeline_path, (psych_path if psych_path.is_file() else None)


def officials_cards_for_token(token: str, swimmer_names: list[str] | None = None):
    """(meet_name, cards, highlights) for a timeline an official uploaded a moment ago. The token
    is a run id, validated and resolved under RUNS_DIR the same way /download/... and
    publish-current do, so it can't be pointed at a path outside the run directory.
    """
    timeline_path, psych_path = officials_upload_paths(token)
    return cards_for_timeline(
        timeline_path, psych_pdf=psych_path, swimmer_names=swimmer_names
    )


def save_officials_upload(
    form: cgi.FieldStorage, field: str, target: Path, required: bool
) -> Path | None:
    """Save one officials upload to an EXACT path (not the client's filename).

    save_upload() keeps the uploaded file's own name, which is what the family flow wants for its
    manifest; here the names must be predictable so officials_upload_paths can tell the timeline
    and the psych sheet apart without guessing. Also refuses a non-PDF outright rather than
    letting the parser fail obscurely later.
    """
    if field not in form:
        if required:
            raise InputError(f"{UPLOAD_FIELD_LABELS.get(field, field)} is required.")
        return None
    item = form[field]
    if isinstance(item, list):
        item = item[0]
    if not item.filename:
        if required:
            raise InputError(f"{UPLOAD_FIELD_LABELS.get(field, field)} is required.")
        return None
    if not str(item.filename).lower().endswith(".pdf"):
        raise InputError(f"{UPLOAD_FIELD_LABELS.get(field, field)} must be a PDF.")
    with target.open("wb") as handle:
        shutil.copyfileobj(item.file, handle)
    return target


# Statuses that block clickable calendar-generation regardless of which
# documents are on file. "documents-pending" means the meet isn't loaded yet;
# "schedule-only" means it's loaded from a meet-packet schedule rather than a
# final timeline, so calendar generation isn't offered even though a psych
# sheet may already be present.
NOT_READY_STATUSES = {"documents-pending", "schedule-only"}


def public_current_meet(meet: dict) -> dict:
    files = meet.get("files", {})
    featured_until = parse_iso_date(str(meet.get("featured_until") or ""))
    is_featured = current_meet_is_featured(meet)
    relay_options = public_relay_options(meet)
    missing_documents = missing_current_meet_documents(files)
    status = str(meet.get("status") or "")
    is_ready_for_lookup = not missing_documents and status not in NOT_READY_STATUSES
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
        "documents": document_labels(meet),
        "missing_documents": missing_documents,
        "readiness": meet_readiness_items(files, missing_documents, relay_options, status),
        "rules_summary": meet.get("rules_summary", []),
        "last_updated": meet.get("last_updated") or "",
        "is_ready_for_lookup": is_ready_for_lookup,
        "status_note": meet.get("status_note") or default_status_note(status, is_ready_for_lookup),
        "has_relay": bool(files.get("relay")),
        "has_private_relay": bool(relay_options),
        "relay_options": relay_options,
        "is_featured": is_featured,
        "featured_until": meet.get("featured_until"),
        "featured_until_label": short_date_label(featured_until) if featured_until else "",
        "featured_label": meet.get("featured_label") or "Featured current meet",
        "featured_note": meet.get("featured_note") or "",
    }


def default_status_note(status: str, is_ready_for_lookup: bool) -> str:
    if is_ready_for_lookup:
        return ""
    if status == "schedule-only":
        return "This meet's schedule is posted, but automatic calendar generation isn't available for this meet yet."
    return "Calendar generation will unlock after the psych/heat sheet and timeline are added."


def timeline_document_label(meet: dict) -> str:
    # Single source of truth: the displayed wording is computed from timeline_type, not stored
    # as its own independent string, so the two can no longer drift apart. Anything not
    # explicitly "projected" (including absent) reads as a final timeline, matching how
    # timeline_projected defaults to False everywhere else this field is used.
    return "Projected timeline" if meet.get("timeline_type") == "projected" else "Final timeline"


def document_labels(meet: dict) -> list[str]:
    # data/current_meets.json stores a neutral "Timeline" placeholder in the documents checklist;
    # here it's expanded to the real "Projected timeline"/"Final timeline" wording derived from
    # timeline_type. Any other entry (Meet flyer, Event order, Psych/heat sheet, Meet packet,
    # Schedule source, ...) passes through unchanged.
    return [timeline_document_label(meet) if label == "Timeline" else label for label in meet.get("documents", [])]


def missing_current_meet_documents(files: dict) -> list[str]:
    missing: list[str] = []
    if not files.get("psych"):
        missing.append("Psych/heat sheet")
    if not files.get("timeline"):
        missing.append("Timeline")
    return missing


def meet_readiness_items(files: dict, missing_documents: list[str], relay_options: list[dict], status: str = "") -> list[dict]:
    missing = set(missing_documents)
    items = [
        {"label": "Meet flyer", "status": "ready" if files.get("flyer") else "optional", "detail": "Loaded" if files.get("flyer") else "Optional"},
        {"label": "Psych/heat sheet", "status": "missing" if "Psych/heat sheet" in missing else "ready", "detail": "Needed" if "Psych/heat sheet" in missing else "Loaded"},
        {"label": "Timeline", "status": "missing" if "Timeline" in missing else "ready", "detail": "Needed" if "Timeline" in missing else "Loaded"},
    ]
    if files.get("relay"):
        items.append({"label": "Relay file", "status": "ready", "detail": "Loaded"})
    elif relay_options:
        items.append({"label": "Relay add-on", "status": "optional", "detail": "Available"})
    if status == "schedule-only":
        items.append({"label": "Swimmer verification", "status": "missing", "detail": "Not available for this meet"})
    return items


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
    raise InputError(f"Unknown current meet: {meet_id}")


def resolve_current_meet_relay_sources(meet: dict, relay_option_ids: list[str]) -> list[Path]:
    if not relay_option_ids:
        return []
    options = {str(option.get("id")): option for option in meet.get("relay_options", []) if isinstance(option, dict)}
    sources: list[Path] = []
    for option_id in relay_option_ids:
        option = options.get(option_id)
        if not option:
            raise ValueError("Selected relay option is not available for this meet.")
        source = resolve_repo_file(option.get("source"), required=True, label="Relay add-on")
        assert source is not None
        sources.append(source)
    return sources


def resolve_current_meet_documents(meet: dict) -> dict:
    """Resolve every per-meet document path and derived metadata straight from disk. Shared by
    handle_analyze_current and the /subscribe.ics feed so both always see the meet's LATEST files
    -- re-reading them here, on every call, rather than caching anything is the whole point of a
    live feed (a heat sheet added mid-meet must show up on the next poll unattended)."""
    # Timezone/venue come from the MEET record, not any swimmer-entered state -- see the caller's
    # comment on why the swimmer's State/LSC field must never substitute for either.
    meet_timezone = resolve_meet_timezone(state=meet.get("state"), explicit_timezone=meet.get("timezone"))
    meet_venue = meet.get("venue") or None
    # Whether this meet's timeline is a settled final schedule or a pre-meet projection. Drives
    # STATUS:CONFIRMED vs STATUS:TENTATIVE and a per-event caveat in the generated calendar.
    # Anything not explicitly "projected" (including absent) is treated as final.
    timeline_projected = meet.get("timeline_type") == "projected"
    files = meet.get("files", {})
    flyer_path = resolve_repo_file(files.get("flyer"), required=False, label="Meet Flyer")
    psych_path = resolve_repo_file(files.get("psych"), required=True, label="Psych Sheet or Heat Sheet")
    timeline_path = resolve_repo_file(files.get("timeline"), required=True, label="Timeline")
    relay_path = resolve_repo_file(files.get("relay"), required=False, label="Relay Doc")
    # Warm-up assignments doc (per-team/day matrix) and/or a universal warm-up window scalar, both
    # optional and both threaded like the other per-meet config.
    warmup_path = resolve_repo_file(files.get("warmup"), required=False, label="Warm-up Assignments")
    meet_warmup_window = meet.get("warmup_window") or None
    # Real heat sheets, one per day as they are published. They OVERLAY the psych sheet for the
    # days they cover; every other day keeps its existing estimate, so a partial-day drop does not
    # change the meet's readiness or affect the rest of the schedule.
    heat_sheet_paths = [
        path for path in (
            resolve_repo_file(entry, required=False, label="Heat Sheet")
            for entry in (files.get("heat_sheets") or [])
        ) if path is not None
    ]
    distance_timeline_path = resolve_repo_file(
        files.get("distance_timeline"), required=False, label="Distance Timeline"
    )
    return {
        "flyer_path": flyer_path,
        "psych_path": psych_path,
        "timeline_path": timeline_path,
        "relay_path": relay_path,
        "warmup_path": warmup_path,
        "meet_warmup_window": meet_warmup_window,
        "heat_sheet_paths": heat_sheet_paths,
        "distance_timeline_path": distance_timeline_path,
        "meet_timezone": meet_timezone,
        "meet_venue": meet_venue,
        "timeline_projected": timeline_projected,
    }


def relay_status(relay_path: Path | None, internal_relay_sources: list[Path]) -> str:
    if relay_path and internal_relay_sources:
        return "uploaded_and_private_relay_parsed"
    if internal_relay_sources:
        return "private_relay_parsed"
    if relay_path:
        return "hosted_and_parsed"
    return "not_uploaded"


def resolve_repo_file(path_value: str | None, required: bool, label: str = "document") -> Path | None:
    if not path_value:
        if required:
            raise ValueError(f"Current meet is missing its {label}.")
        return None
    target = (ROOT / path_value).resolve()
    if ROOT not in [target, *target.parents]:
        raise ValueError("Current meet document path is outside the workspace.")
    if not target.is_file():
        if required:
            raise ValueError(f"Current meet's {label} could not be found. Please contact SwimTimeline support.")
        return None
    return target


def copy_hosted_upload(path_value: str | None, target_dir: Path, label: str = "document") -> str | None:
    if not path_value:
        return None
    source = resolve_repo_file(path_value, required=True, label=label)
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


class SubscribeError(Exception):
    """Raised for any /subscribe.ics request that can't be fulfilled, carrying the HTTP status
    send_subscribe_ics should reply with -- so a bad/missing param, an unknown meet, or a swimmer
    with no matches becomes a clear error response, never a 500 or a malformed calendar file."""

    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


# Keyed by the resolved request (not the raw query string, so param order never causes a miss).
# Guarded by a lock because ThreadingHTTPServer runs each request on its own thread; the lock only
# protects the dict itself; two concurrent misses for the same key just both compute it, which is
# harmless.
_subscribe_cache: dict[tuple, tuple[float, bytes, str]] = {}
_subscribe_cache_lock = threading.Lock()


def query_value(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name)
    return values[0] if values else default


def query_bool(query: dict[str, list[str]], name: str, default: bool = False) -> bool:
    values = query.get(name)
    if not values:
        return default
    return values[0].strip().lower() not in {"", "0", "false", "off", "no"}


def swimmer_matched(result: dict) -> bool:
    # Mirrors the frontend's own verifiedTotal() -- tentative relays don't count, since a swimmer
    # can only get a tentative team-entered relay after their individual entries resolved a team
    # code for them (see swimmer_relay_identity), so this can't false-negative a relay-only match.
    return (int(result.get("verified_event_count") or 0) + int(result.get("verified_relay_count") or 0)) > 0


def subscribe_filename(meet: dict, swimmer_name: str, mode: str) -> str:
    meet_slug = slugify_value(str(meet.get("short_name") or meet.get("name") or meet.get("id") or "meet"))
    return f"{meet_slug}-{slugify_value(swimmer_name)}-{mode}.ics"


def build_subscribe_ics(query: dict[str, list[str]]) -> tuple[bytes, str]:
    meet_id = query_value(query, "meet_id").strip()
    swimmer_name = query_value(query, "swimmer").strip()
    if not meet_id:
        raise SubscribeError(HTTPStatus.BAD_REQUEST, "meet_id is required.")
    if not swimmer_name:
        raise SubscribeError(HTTPStatus.BAD_REQUEST, "swimmer is required.")
    state = query_value(query, "state").strip().upper()
    modes = normalize_modes(query.get("modes", []))
    mode = modes[0]
    # Same opt-in shape as handle_analyze_current: a private-roster add-on id, or the general
    # "show my team's entered relays" toggle. payload_relay_options only reads one key, so handing
    # it a synthetic dict reuses its cleaning/dedup logic without duplicating it here.
    relay_option_ids = payload_relay_options({"relay_options": query.get("relay_options", [])})
    show_team_relays = query_bool(query, "show_team_relays", default=False)

    try:
        meet = resolve_current_meet(meet_id)
    except ValueError as exc:
        raise SubscribeError(HTTPStatus.NOT_FOUND, str(exc)) from exc
    if not public_current_meet(meet).get("is_ready_for_lookup"):
        raise SubscribeError(HTTPStatus.CONFLICT, "This meet is not ready for calendar generation yet.")

    cache_key = (meet_id, swimmer_name, state, mode, tuple(sorted(relay_option_ids)), show_team_relays)
    with _subscribe_cache_lock:
        cached = _subscribe_cache.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1], cached[2]

    try:
        internal_relay_sources = resolve_current_meet_relay_sources(meet, relay_option_ids)
        docs = resolve_current_meet_documents(meet)
    except ValueError as exc:
        raise SubscribeError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    # Throwaway run dir: nothing else ever looks it up again (unlike /api/analyze and
    # /api/analyze-current, whose run_id is handed back to the browser for /download/ links and
    # the publish-to-current-meets flow). There is no existing sweep that cleans up RUNS_DIR, and
    # this route is polled repeatedly forever by calendar apps, so it is deleted explicitly below
    # rather than left to accumulate.
    run_id = f"subscribe-{int(time.time())}-{uuid4().hex[:8]}"
    run_dir = RUNS_DIR / run_id
    output_dir = run_dir / "outputs"
    try:
        result = analyze_swimmer_set(
            flyer_path=docs["flyer_path"],
            psych_path=docs["psych_path"],
            timeline_path=docs["timeline_path"],
            relay_path=docs["relay_path"],
            internal_relay_sources=internal_relay_sources,
            swimmer_names=[swimmer_name],
            output_dir=output_dir,
            state=state,
            modes=[mode],
            combine_family=False,  # Single swimmer only -- see the module docstring on scope.
            # Real heat sheets overlay unconditionally regardless of this flag (see
            # overlay_heat_sheet_entries in extract.py); only the PRE-heat-sheet estimation
            # heuristic is gated by it. A live feed should show the confirmed heat/lane the moment
            # Luis posts the real sheet either way, so skipping speculative estimates here is a
            # reasonable default, not a real capability gap.
            estimate_heat_lanes=False,
            meet_timezone=docs["meet_timezone"],
            meet_venue=docs["meet_venue"],
            timeline_projected=docs["timeline_projected"],
            warmup_path=docs["warmup_path"],
            meet_warmup_window=docs["meet_warmup_window"],
            heat_sheet_paths=docs["heat_sheet_paths"],
            distance_timeline_path=docs["distance_timeline_path"],
            include_relays=bool(relay_option_ids or show_team_relays),
        )
        if result.get("ambiguous_swimmer_match"):
            raise SubscribeError(
                HTTPStatus.BAD_REQUEST,
                f"'{swimmer_name}' matches more than one swimmer at this meet. Use a more specific name.",
            )
        if not swimmer_matched(result):
            raise SubscribeError(
                HTTPStatus.NOT_FOUND,
                f"No swims found for '{swimmer_name}' at this meet. Check the spelling and try again.",
            )
        ics_name = result["files"].get(f"{mode}_ics")
        if not ics_name:
            raise SubscribeError(HTTPStatus.BAD_REQUEST, f"Unsupported calendar mode: {mode}")
        ics_bytes = (output_dir / ics_name).read_bytes()
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    filename = subscribe_filename(meet, swimmer_name, mode)
    with _subscribe_cache_lock:
        _subscribe_cache[cache_key] = (time.time() + SUBSCRIBE_CACHE_TTL_SECONDS, ics_bytes, filename)
    return ics_bytes, filename


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
