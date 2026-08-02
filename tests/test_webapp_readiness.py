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


class TimelineDocumentLabelTest(unittest.TestCase):
    """The 'Projected timeline'/'Final timeline' checklist wording is derived from timeline_type,
    not stored as its own independent string -- data/current_meets.json stores a neutral
    "Timeline" placeholder, expanded here, so the two facts can't drift apart.
    """

    def test_projected_timeline_type_expands_to_projected_label(self):
        self.assertEqual(server.timeline_document_label({"timeline_type": "projected"}), "Projected timeline")

    def test_final_timeline_type_expands_to_final_label(self):
        self.assertEqual(server.timeline_document_label({"timeline_type": "final"}), "Final timeline")

    def test_missing_timeline_type_defaults_to_final_label(self):
        # Matches timeline_projected defaulting to False everywhere else this field is used.
        self.assertEqual(server.timeline_document_label({}), "Final timeline")

    def test_document_labels_expands_the_placeholder_and_passes_other_entries_through(self):
        meet = {"timeline_type": "projected", "documents": ["Meet flyer", "Psych/heat sheet", "Timeline"]}

        self.assertEqual(server.document_labels(meet), ["Meet flyer", "Psych/heat sheet", "Projected timeline"])

    def test_real_wzag_entry_shows_projected_label_from_timeline_type(self):
        meet = server.resolve_current_meet("2026-wzag-championships-boise")

        self.assertEqual(meet["timeline_type"], "projected")
        self.assertIn("Projected timeline", server.public_current_meet(meet)["documents"])
        self.assertNotIn("Final timeline", server.public_current_meet(meet)["documents"])

    def test_real_narwhal_entry_shows_final_label_from_timeline_type(self):
        meet = server.resolve_current_meet("2026-narwhal-invite")

        self.assertEqual(meet["timeline_type"], "final")
        self.assertIn("Final timeline", server.public_current_meet(meet)["documents"])
        self.assertNotIn("Projected timeline", server.public_current_meet(meet)["documents"])

    def test_real_para_nationals_entry_has_no_computed_timeline_label(self):
        # Schedule-only meet: its documents use different vocabulary ("Schedule source") and
        # carries no timeline_type at all, so nothing here should be expanded or invented.
        meet = server.resolve_current_meet("2026-para-nationals")

        documents = server.public_current_meet(meet)["documents"]
        self.assertNotIn("Final timeline", documents)
        self.assertNotIn("Projected timeline", documents)
        self.assertIn("Schedule source", documents)


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
