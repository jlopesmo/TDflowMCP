from dataclasses import replace
import unittest

from spikes.v17 import script_intent as intent


class ScriptIntentResearchTests(unittest.TestCase):
    def setUp(self):
        self.grant = intent.Grant("agent", "project", "/project1/draft", "activate", 20)
        self.facts = dict(project="project", target="/project1/draft/node", type="scriptTOP",
                          selected_dat="/project1/draft/source", parent="/project1/draft",
                          inputs=["/project1/draft/input"], protected=False, callback_mode="CONSTANT",
                          callback_source_kind="authored_text", target_id=31, callback_id=32, runtime="epoch",
                          build="2025.32440", policy="policy revision", cook_disabled=True, source_sha256="digest")
        self.planned = intent.digest({"action": "activate", "facts": self.facts})

    def outcome(self, *, grant=None, facts=None, **overrides):
        settings = dict(actor="agent", action="activate", now=10)
        settings.update(overrides)
        return intent.eligibility(grant or self.grant, facts or self.facts, self.planned, **settings)

    def test_inert_source_preview_does_not_import_module_or_run_statement(self):
        text = "import nonexistent_fixture_module\nraise RuntimeError('must remain text')\n"
        self.assertEqual(intent.source_preview(text)["imports"], ["nonexistent_fixture_module"])
        with self.assertRaises(SyntaxError):
            intent.source_preview("def invalid(")

    def test_exact_snapshot_reaches_review_state(self):
        self.assertEqual(self.outcome(), "eligible_for_review")

    def test_draft_grant_cannot_authorize_activation(self):
        grant = replace(self.grant, action="draft")
        planned = intent.digest({"action": "draft", "facts": self.facts})
        self.assertEqual(intent.eligibility(grant, self.facts, planned, actor="agent", action="draft", now=10),
                         "eligible_for_review")
        self.assertEqual(self.outcome(grant=grant), "denied")

    def test_changed_inputs_source_runtime_or_policy_invalidate_snapshot(self):
        for field, changed in (("inputs", ["/project1/draft/other"]), ("source_sha256", "next"),
                               ("runtime", "restart"), ("build", "different"), ("callback_id", 42),
                               ("target_id", 99), ("policy", "next"), ("cook_disabled", False)):
            with self.subTest(field=field):
                self.assertEqual(self.outcome(facts={**self.facts, field: changed}), "changed")

    def test_scope_dynamic_callback_protection_actor_and_expiry(self):
        for field, changed in (("inputs", ["/project1/unreadable/input"]), ("protected", True),
                               ("selected_dat", "/project1/draft/../secret"), ("callback_mode", "EXPRESSION"),
                               ("callback_source_kind", "file_linked")):
            with self.subTest(field=field):
                self.assertEqual(self.outcome(facts={**self.facts, field: changed}), "denied")
        self.assertEqual(self.outcome(actor="other"), "denied")
        self.assertEqual(self.outcome(now=20), "denied")

    def test_stop_conditions_refuse_even_an_exact_match(self):
        for state in (dict(suspended=True), dict(revoked=True), dict(audit_ready=False), dict(consumed=True)):
            self.assertEqual(self.outcome(**state), "unavailable")
