from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from webapp import server
except ModuleNotFoundError as exc:
    if exc.name == "cgi":
        raise unittest.SkipTest("webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13") from exc
    raise


class ScheduleOnlyReadinessTest(unittest.TestCase):
    def test_schedule_only_meet_is_not_ready_for_lookup(self):
        meet = {"id": "test-schedule-only", "status": "schedule-only", "files": {"psych": "a.pdf", "timeline": "b.pdf"}}

        self.assertFalse(server.public_current_meet(meet)["is_ready_for_lookup"])

    def test_documents_pending_meet_is_not_ready_for_lookup(self):
        meet = {"id": "test-pending", "status": "documents-pending", "files": {}}

        self.assertFalse(server.public_current_meet(meet)["is_ready_for_lookup"])

    def test_ready_meet_with_all_documents_is_ready_for_lookup(self):
        meet = {"id": "test-ready", "status": "ready", "files": {"psych": "a.pdf", "timeline": "b.pdf"}}

        self.assertTrue(server.public_current_meet(meet)["is_ready_for_lookup"])

    def test_schedule_only_status_note_differs_from_documents_pending(self):
        schedule_only = server.public_current_meet({"id": "a", "status": "schedule-only", "files": {"psych": "a.pdf", "timeline": "b.pdf"}})
        pending = server.public_current_meet({"id": "b", "status": "documents-pending", "files": {}})

        self.assertNotEqual(schedule_only["status_note"], pending["status_note"])
        self.assertIn("schedule", schedule_only["status_note"].lower())
        self.assertIn("psych/heat sheet and timeline", pending["status_note"].lower())

    def test_schedule_only_readiness_checklist_flags_missing_verification(self):
        items = server.meet_readiness_items({"psych": "a.pdf", "timeline": "b.pdf"}, [], [], status="schedule-only")

        labels = {item["label"]: item["status"] for item in items}
        self.assertEqual(labels.get("Swimmer verification"), "missing")

    def test_ready_meet_readiness_checklist_has_no_verification_row(self):
        items = server.meet_readiness_items({"psych": "a.pdf", "timeline": "b.pdf"}, [], [], status="ready")

        self.assertNotIn("Swimmer verification", {item["label"] for item in items})

    def test_explicit_status_note_from_meet_record_is_preferred(self):
        meet = {
            "id": "c",
            "status": "schedule-only",
            "status_note": "Custom note.",
            "files": {"psych": "a.pdf", "timeline": "b.pdf"},
        }

        self.assertEqual(server.public_current_meet(meet)["status_note"], "Custom note.")

    def test_real_para_nationals_entry_is_not_ready_and_keeps_its_authored_note(self):
        meet = server.resolve_current_meet("2026-para-nationals")

        public = server.public_current_meet(meet)
        self.assertEqual(meet["status"], "schedule-only")
        self.assertFalse(public["is_ready_for_lookup"])
        self.assertEqual(public["status_note"], meet["status_note"])

    def test_real_narwhal_invite_entry_is_unaffected_and_stays_ready(self):
        meet = server.resolve_current_meet("2026-narwhal-invite")

        self.assertTrue(server.public_current_meet(meet)["is_ready_for_lookup"])


class UploadErrorMessageTest(unittest.TestCase):
    def test_upload_field_labels_cover_all_document_fields(self):
        self.assertEqual(server.UPLOAD_FIELD_LABELS["psych_pdf"], "Psych Sheet or Heat Sheet")
        self.assertEqual(server.UPLOAD_FIELD_LABELS["timeline_pdf"], "Timeline")
        self.assertEqual(server.UPLOAD_FIELD_LABELS["flyer_pdf"], "Meet Flyer")
        self.assertEqual(server.UPLOAD_FIELD_LABELS["relay_pdf"], "Relay Doc")

    def test_missing_required_repo_file_error_uses_friendly_label_not_raw_path(self):
        with self.assertRaises(ValueError) as ctx:
            server.resolve_repo_file(None, required=True, label="Psych Sheet or Heat Sheet")

        message = str(ctx.exception)
        self.assertIn("Psych Sheet or Heat Sheet", message)
        self.assertNotIn("psych_pdf", message)

    def test_repo_file_not_found_error_does_not_leak_the_internal_path(self):
        with self.assertRaises(ValueError) as ctx:
            server.resolve_repo_file("meets/does-not-exist/psych.pdf", required=True, label="Psych Sheet or Heat Sheet")

        message = str(ctx.exception)
        self.assertIn("Psych Sheet or Heat Sheet", message)
        self.assertNotIn("does-not-exist", message)


if __name__ == "__main__":
    unittest.main()
