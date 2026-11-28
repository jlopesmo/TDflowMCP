from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import hashlib
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest

from spikes.v17.recovery_ledger import Ledger, Refused
from spikes.v17.transfer_review import Lease, binding, read_manifest, verify_transfer
from tests.td.run_td_script_boundary_probe import BUILD, PHASES, phase_accepted, run_accepted


class SupervisorTests(unittest.TestCase):
    def test_valid_final_observation_cannot_mask_failed_process(self):
        report = {"build": BUILD, "pid": 123, "result": {}}
        passed = {"exit": 0, "killed": False, "other_processes_untouched": True}
        statuses = dict.fromkeys(PHASES, {"accepted": True})
        self.assertTrue(phase_accepted("restart", report, 123, passed, 1))
        self.assertTrue(run_accepted(statuses))
        for override in ({"exit": 1}, {"killed": True}, {"other_processes_untouched": False}):
            with self.subTest(override=override):
                accepted = phase_accepted("restart", report, 123, {**passed, **override}, 1)
                self.assertFalse(accepted)
                self.assertFalse(run_accepted({**statuses, "restart": {"accepted": accepted}}))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"schema": 1, "operators": [
            {"family": "script" + family, "operator": family, "callback": family + "Source",
             "source": "import owner_review_only\nraise RuntimeError('inert')", "wires": [["input_a", 0, 0]]}
            for family in ("CHOP", "SOP", "TOP")
        ]}
        self.row = dict(path="/project1/stage/top", callback_path="/project1/stage/text", id=10,
                        callback_id=11, parent="/project1/stage", parent_id=9, source_sha256="source-hash",
                        callback_mode="CONSTANT", source_kind="authored_text", protected=False,
                        dependencies=["/project1/stage/input"], wires=[["input_a", 0, 0]], outputs=[], trigger="force-cook",
                        cook_disabled=True, bypass=False, imports=["os"])
        self.live = dict(project="receiver", build="2025.32440", epoch="epoch-a", policy="policy-a",
                         operators=[self.row])
        self.lease = Lease("lease-a", "fixture-agent", "receiver", "/project1/stage", "activate", 20)

    def bound(self, live=None, lease=None, **overrides):
        settings = dict(now=10, policy="policy-a")
        settings.update(overrides)
        return binding("activate", "fixture-agent", lease or self.lease, "manifest-hash", live or self.live, **settings)

    def test_manifest_is_inert_and_recomputed(self):
        raw = json.dumps(self.manifest).encode()
        data, seal = read_manifest(raw)
        self.assertEqual(data["operators"][0]["source_facts"]["imports"], ["owner_review_only"])
        self.assertEqual(read_manifest(json.dumps(self.manifest, indent=2).encode())[1], seal)
        self.manifest["operators"][0]["source"] += "\n# owner edit"
        self.assertNotEqual(read_manifest(json.dumps(self.manifest).encode())[1], seal)

    def test_transfer_checks_both_exact_bytes_and_canonical_manifest(self):
        raw = json.dumps(self.manifest).encode()
        data, seal = read_manifest(raw)
        byte_hash = hashlib.sha256(raw).hexdigest()
        self.assertEqual(verify_transfer(raw, byte_hash, seal), (data, seal))
        with self.assertRaises(ValueError):
            verify_transfer(raw + b" ", byte_hash, seal)
        with self.assertRaises(ValueError):
            verify_transfer(raw, byte_hash, "different manifest")

    def test_executable_carriers_and_invalid_manifests_are_refused(self):
        for raw in (b'{"schema":1,"schema":1}', b"binary-graph", b"[" * (128 * 1024 + 1),
                    b'{"schema":true,"operators":[]}', b"\xff"):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                read_manifest(raw)
        row = self.manifest["operators"][0]
        for field, value in (("tox", "binary"), ("callback", "../external"), ("wires", [["a", 0, 0], ["b", 0, 0]]),
                             ("wires", [["a", False, 0]]), ("source", 1)):
            candidate = {**self.manifest, "operators": [{**row, field: value}, *self.manifest["operators"][1:]]}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                read_manifest(json.dumps(candidate).encode())
        self.manifest["operators"][0]["source"] = "def invalid("
        with self.assertRaises(SyntaxError):
            read_manifest(json.dumps(self.manifest).encode())

    def test_snapshot_drift_invalidates_exact_approval(self):
        actual = self.bound()
        for field, changed in (("callback_id", 12), ("id", 99), ("parent_id", 72), ("source_sha256", "other"),
                               ("wires", [["input_b", 1, 0]]), ("trigger", "setup"), ("cook_disabled", False),
                               ("bypass", True), ("imports", ["native"])):
            with self.subTest(field=field):
                self.assertNotEqual(self.bound(live={**self.live, "operators": [{**self.row, field: changed}]}), actual)
        self.assertNotEqual(self.bound(live={**self.live, "epoch": "restart"}), actual)
        self.assertNotEqual(self.bound(live={**self.live, "policy": "policy-b"}, policy="policy-b"), actual)
        self.assertNotEqual(self.bound(lease=replace(self.lease, lease_id="new-lease")), actual)

    def test_authority_scope_revocation_and_external_sources_fail_closed(self):
        for field, value in (("callback_mode", "EXPRESSION"), ("source_kind", "linked_file"), ("protected", True),
                             ("dependencies", ["/project1/stage_peer/in"]), ("parent", "/project1"),
                             ("outputs", [["/project1/stage_peer/out", 0, 0]])):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.bound(live={**self.live, "operators": [{**self.row, field: value}]})
        for lease in (replace(self.lease, actor="different"), replace(self.lease, project="different"),
                      replace(self.lease, action="author")):
            with self.assertRaises(ValueError):
                self.bound(lease=lease)
        for settings in (dict(revoked=True), dict(suspended=True), dict(now=20), dict(policy="changed")):
            with self.assertRaises(ValueError):
                self.bound(**settings)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "research.sqlite"
        self.ledger = Ledger.create(self.path)

    def review(self, suffix="", expires=20):
        self.ledger.owner_review("author" + suffix, "author", "source", expires)
        self.ledger.owner_review("activate" + suffix, "activate", "receiving", expires)

    def begin(self, request="run", suffix="", activation="receiving", now=10):
        self.ledger.begin(request, "author" + suffix, "source", "activate" + suffix, activation, now=now)

    def process_exit(self, phase):
        script = """
import os, sys
from pathlib import Path
from spikes.v17.recovery_ledger import Ledger
ledger = Ledger(sys.argv[1])
phase = sys.argv[2]
if phase != 'pre-intent':
    ledger.begin('run', 'author', 'source', 'activate', 'receiving', now=10)
if phase == 'lost-outcome':
    with Path(sys.argv[1]).with_suffix('.effect').open('x') as effect:
        effect.write('benign fixture effect')
        effect.flush()
        os.fsync(effect.fileno())
os._exit(27)
"""
        return subprocess.run([sys.executable, "-B", "-c", script, str(self.path), phase], timeout=10,
                              cwd=Path(__file__).resolve().parents[2], check=False).returncode

    def test_pre_intent_exit_requires_fresh_runtime_review(self):
        self.review()
        self.assertEqual(self.process_exit("pre-intent"), 27)
        self.assertEqual(self.ledger.recover_after_exit(), 0)
        with self.assertRaises(Refused):
            self.begin(activation="new epoch")
        self.review("new")
        self.begin(suffix="new")
        self.ledger.finish("run", "observed")
        self.assertEqual(self.ledger.status(), (False, {"run": "observed"}))

    def test_post_intent_exit_and_lost_outcome_suspend_without_replay(self):
        self.review()
        self.assertEqual(self.process_exit("post-intent"), 27)
        self.assertEqual(self.ledger.status(), (False, {"run": "dispatching"}))
        self.assertEqual(self.ledger.recover_after_exit(), 1)
        self.assertEqual(self.ledger.recover_after_exit(), 0)
        self.assertEqual(self.ledger.status(), (True, {"run": "unknown"}))
        self.assertFalse(self.path.with_suffix(".effect").exists())
        with self.assertRaises(Refused):
            self.begin()
        with self.assertRaises(Refused):
            self.review("new")

    def test_lost_outcome_cannot_reverse_a_durable_external_marker(self):
        self.review()
        self.assertEqual(self.process_exit("lost-outcome"), 27)
        self.assertEqual(self.ledger.recover_after_exit(), 1)
        self.assertEqual(self.ledger.status(), (True, {"run": "unknown"}))
        self.assertEqual(self.path.with_suffix(".effect").read_text(), "benign fixture effect")

    def test_wrong_purpose_expiry_and_timeout_fail_without_consuming_a_valid_review(self):
        self.review(expires=20)
        with self.assertRaises(Refused):
            self.ledger.begin("run", "author", "source", "author", "source", now=10)
        with self.assertRaises(Refused):
            self.begin(now=20)
        with closing(sqlite3.connect(self.path)) as locked, locked:
            locked.execute("BEGIN IMMEDIATE")
            with self.assertRaises(Refused):
                self.begin()
        self.begin()
        self.ledger.finish("run", "cancelled")
        with self.assertRaises(Refused):
            self.begin("replay")

    def test_contenders_cannot_double_dispatch(self):
        self.review()
        barrier = Barrier(2)

        def contender(id_):
            barrier.wait(timeout=5)
            try:
                self.begin(id_)
                return True
            except Refused:
                return False

        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(contender, ("first", "second"))), 1)
        self.assertEqual(self.ledger.recover_after_exit(), 1)

    def test_audit_absence_does_not_create_new_lineage(self):
        missing = self.path.with_name("absent.sqlite")
        with self.assertRaises(Refused):
            Ledger(missing).owner_review("a", "author", "source", 20)
        self.assertFalse(missing.exists())
        with self.assertRaises(FileExistsError):
            Ledger.create(self.path)
        self.review()
        self.begin()
        self.ledger.finish("run", "fault")
        self.assertEqual(self.ledger.status(), (True, {"run": "fault"}))

    def test_unavailable_outcome_audit_quarantines_on_reopen(self):
        self.review()
        self.begin()
        with closing(sqlite3.connect(self.path)) as locked, locked:
            locked.execute("BEGIN IMMEDIATE")
            with self.assertRaises(Refused):
                self.ledger.finish("run", "observed")
        with self.assertRaises(Refused):
            self.review("bypass")
        reopened = Ledger(self.path)
        self.assertEqual(reopened.recover_after_exit(), 1)
        self.assertEqual(reopened.status(), (True, {"run": "unknown"}))
