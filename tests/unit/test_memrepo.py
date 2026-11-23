"""Phase 2A increment 3: repository-mode project memory (ADR-0013 gates: Projectid link check, migration in
both directions preserving records and hashes) and the engine's repository path."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tdflowmcp import memory as MEM
from tdflowmcp import memrepo as R
from tdflowmcp.engine import Engine, ToolFailure
from tdflowmcp.identity import Identity
from tdflowmcp.policy import CAP_MEMORY_REPO_WRITE, Policy
from tdflowmcp.store import Store
from tdflowmcp.tools import core_registry
from tests.unit.test_memory import ALL, COMP, MID, FakeAdapter

OTHER = "tfp_ffffffffffffffff"


def rec(kind="checkpoint", task="blur", rev=0, entries=(), **body):
    b = dict({"scopes": ["/project1/fx"], "status": "in_progress"} if kind == "checkpoint" else {"scopes": ["/project1/fx"]})
    b.update(body)
    return MEM.new_record(kind, task, list(entries), b, "agt_x", rev)


def dat(r):
    return r["record_id"], MEM.record_text(r)


class Base(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp(prefix="tdflow-repo-")
        self.proj = os.path.join(self.top, "repo", "td")          # the .toe folder, inside a "repository"
        os.makedirs(self.proj)
        self.repo = os.path.join(self.top, "repo")

    def tearDown(self):
        shutil.rmtree(self.top, ignore_errors=True)


class PathTests(Base):
    def test_repopath_is_always_relative_and_inside_the_projects_tree(self):
        self.assertEqual(R.to_repopath(self.proj, "."), ".")
        self.assertEqual(R.to_repopath(self.proj, self.proj), ".")
        self.assertEqual(R.to_repopath(self.proj, ".."), "..")
        self.assertEqual(R.to_repopath(self.proj, self.repo), "..")
        self.assertEqual(R.to_repopath(self.proj, "docs/memory"), "docs/memory")
        self.assertEqual(R.to_repopath(self.proj, os.path.join(self.proj, "docs")), "docs")
        for bad in ("../sibling", "../../../..", "docs/../../x", ""):
            with self.assertRaises(R.RepoError, msg=bad) as c:
                R.to_repopath(self.proj, bad)
            self.assertEqual(c.exception.code, "MEMORY_REPO_NOT_ALLOWED")
        with self.assertRaises(R.RepoError) as c:
            R.to_repopath("", ".")                                    # unsaved project
        self.assertEqual(c.exception.code, "MEMORY_UNLINKED")

    def test_resolve_refuses_absolute_and_escaping_links(self):
        self.assertEqual(os.path.normcase(R.resolve(self.proj, "..")), os.path.normcase(self.repo))
        self.assertEqual(os.path.normcase(R.resolve(self.proj, ".")), os.path.normcase(self.proj))
        for bad in (self.repo, "C:/x", "/etc", "..\\other", "../../../../x", "a/../../b"):
            with self.assertRaises(R.RepoError, msg=bad) as c:
                R.resolve(self.proj, bad)
            self.assertEqual(c.exception.code, "MEMORY_REPO_NOT_ALLOWED")
        with self.assertRaises(R.RepoError) as c:
            R.resolve(self.proj, "")
        self.assertEqual(c.exception.code, "MEMORY_UNLINKED")
        with self.assertRaises(R.RepoError) as c:
            R.resolve("", "..")
        self.assertEqual(c.exception.code, "MEMORY_UNLINKED")

    def test_display_never_shows_an_absolute_path(self):
        self.assertEqual(R.display(self.proj, "."), "<project folder>")
        self.assertEqual(R.display(self.proj, "docs"), "<project folder>/docs")
        self.assertEqual(R.display(self.proj, ""), "(not linked)")
        self.assertNotIn(os.path.basename(self.top), R.display(self.proj, self.repo))

    def test_folder_checks(self):
        with self.assertRaises(R.RepoError) as c:
            R.check_folder(os.path.join(self.repo, "missing"))
        self.assertEqual(c.exception.code, "MEMORY_REPO_MISSING")
        for part in (".git", ".tdflowmcp"):
            d = os.path.join(self.repo, part, "x")
            os.makedirs(d)
            with self.assertRaises(R.RepoError) as c:
                R.check_folder(d)
            self.assertEqual(c.exception.code, "MEMORY_REPO_NOT_ALLOWED")
        with open(os.path.join(self.repo, "tdflow"), "w") as f:      # a file where the folder must be
            f.write("x")
        with self.assertRaises(R.RepoError) as c:
            R.check_folder(self.repo)
        self.assertEqual(c.exception.code, "MEMORY_REPO_NOT_ALLOWED")

    def test_link_in_the_path_is_rejected(self):
        real = os.path.join(self.top, "elsewhere")
        os.makedirs(real)
        link = os.path.join(self.repo, "tdflow")
        if sys.platform == "win32":                                    # a junction needs no privilege
            if subprocess.run(["cmd", "/c", "mklink", "/J", link, real], capture_output=True).returncode != 0:
                self.skipTest("cannot create a junction here")
        else:
            os.symlink(real, link)
        with self.assertRaises(R.RepoError) as c:
            R.check_folder(self.repo)
        self.assertEqual(c.exception.code, "SYMLINK_REJECTED")
        R_ok = os.path.join(self.top, "repo2")
        os.makedirs(R_ok)
        R.write_project(R_ok, MID, "repo")
        os.makedirs(os.path.join(R_ok, "tdflow"))
        if sys.platform == "win32":
            subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(R_ok, "tdflow", "memory"), real], capture_output=True)
        else:
            os.symlink(real, os.path.join(R_ok, "tdflow", "memory"))
        with self.assertRaises(R.RepoError) as c:                      # never written through a linked folder
            R.write_record(R_ok, "checkpoint_blur_r1", "x", MEM.dats_digest([]))
        self.assertEqual(c.exception.code, "SYMLINK_REJECTED")
        self.assertEqual(os.listdir(real), [])


class LinkTests(Base):
    def test_projectid_mismatch_blocks_linking(self):                  # ADR-0013 gate
        with self.assertRaises(R.RepoError) as c:
            R.check_link(self.repo, MID)
        self.assertEqual(c.exception.code, "MEMORY_UNLINKED")
        R.write_project(self.repo, OTHER, "repo")
        with self.assertRaises(R.RepoError) as c:
            R.check_link(self.repo, MID)
        self.assertEqual(c.exception.code, "MEMORY_LINK_MISMATCH")
        with self.assertRaises(R.RepoError) as c:                      # another project's file is never replaced
            R.write_project(self.repo, MID, "repo")
        self.assertEqual(c.exception.code, "MEMORY_LINK_MISMATCH")
        self.assertEqual(R.read_project(self.repo)["project_id"], OTHER)
        with self.assertRaises(R.RepoError) as c:
            R.plan_to_repo(self.repo, MID, [])
        self.assertEqual(c.exception.code, "MEMORY_LINK_MISMATCH")

    def test_invalid_and_derived_project_files(self):
        with open(os.path.join(self.repo, R.PROJECT_FILE), "w") as f:
            f.write("{not json")
        with self.assertRaises(R.RepoError) as c:
            R.check_link(self.repo, MID)
        self.assertEqual(c.exception.code, "MEMORY_LINK_INVALID")
        os.remove(os.path.join(self.repo, R.PROJECT_FILE))
        R.write_project(self.repo, MID, "repo")
        self.assertEqual(R.check_link(self.repo, MID)["authority"], "repo")
        R.mark_derived(self.repo, MID, "2 record(s)")
        pj = R.read_project(self.repo)
        self.assertEqual(pj["authority"], "embedded")
        self.assertIn("DERIVED", pj["note"])
        with self.assertRaises(R.RepoError) as c:
            R.check_link(self.repo, MID)
        self.assertEqual(c.exception.code, "MEMORY_LINK_STALE")


class RecordTests(Base):
    def test_write_read_and_never_overwrite(self):
        R.write_project(self.repo, MID, "repo")
        r1 = rec()
        d0 = MEM.dats_digest([])
        out = R.write_record(self.repo, r1["record_id"], MEM.record_text(r1), d0)
        self.assertEqual(out["file"], "tdflow/memory/checkpoint_blur_r1.json")
        got = R.read_records(self.repo)
        self.assertEqual([n for n, _ in got], ["checkpoint_blur_r1"])
        self.assertEqual(MEM.text_sha(got[0][1]), MEM.text_sha(MEM.record_text(r1)))
        self.assertEqual(MEM.read_records(got)[0]["problem"], "")
        with self.assertRaises(R.RepoError) as c:                      # a stale plan: the set changed
            R.write_record(self.repo, "checkpoint_other_r1", "x", d0)
        self.assertEqual(c.exception.code, "MEMORY_CONFLICT")
        with self.assertRaises(R.RepoError) as c:                      # the same record again
            R.write_record(self.repo, r1["record_id"], "changed", MEM.dats_digest(got))
        self.assertEqual(c.exception.code, "MEMORY_CONFLICT")
        self.assertEqual(MEM.text_sha(R.read_records(self.repo)[0][1]), MEM.text_sha(MEM.record_text(r1)))
        with self.assertRaises(R.RepoError):
            R.write_record(self.repo, "../escape", "x", MEM.dats_digest(got))
        self.assertEqual(sorted(os.listdir(R.record_dir(self.repo))), ["checkpoint_blur_r1.json"])   # no temp left

    def test_unreadable_files_are_invalid_not_skipped(self):
        d = R.record_dir(self.repo)
        os.makedirs(d)
        with open(os.path.join(d, "checkpoint_big_r1.json"), "wb") as f:
            f.write(b"x" * (R.MAX_FILE_BYTES + 1))
        with open(os.path.join(d, "handoff_bad_r1.json"), "w") as f:
            f.write("not a record")
        with open(os.path.join(d, "notes.txt"), "w") as f:              # not record-named: ignored
            f.write("x")
        ents = MEM.read_records(R.read_records(self.repo))
        self.assertEqual({e["name"]: e["rec"] for e in ents}, {"checkpoint_big_r1": None, "handoff_bad_r1": None})
        with self.assertRaises(MEM.MemoryError):
            MEM.claim_records(ents)                                     # invalid records are never claimable


class MigrationTests(Base):
    def records(self):
        r1 = rec()
        r2 = rec("handoff", rev=1, entries=[{"name": r1["record_id"], "rec": r1}], summary="s", claims=[])
        return [dat(r1), dat(r2)]

    def test_round_trip_preserves_records_and_hashes(self):            # ADR-0013 gate
        dats = self.records()
        before = {n: MEM.text_sha(t) for n, t in dats}
        plan = R.plan_to_repo(self.repo, MID, dats, "<project folder>/..")
        self.assertEqual([a["action"] for a in plan["actions"]], ["create", "create"])
        self.assertEqual((plan["project_file"], plan["conflicts"], plan["target"]), ("create", [], "<project folder>/.."))
        self.assertFalse(os.path.exists(R.record_dir(self.repo)))       # a preview writes nothing
        with self.assertRaises(R.RepoError) as c:
            R.to_repo(self.repo, MID, dats, "wrong digest")
        self.assertEqual(c.exception.code, "PREVIEW_CHANGED")
        res = R.to_repo(self.repo, MID, dats, plan["digest"])
        self.assertEqual((res["records"], res["records_digest"]), (2, MEM.dats_digest(dats)))
        files = R.read_records(self.repo)
        self.assertEqual({n: MEM.text_sha(t) for n, t in files}, before)
        self.assertEqual(R.check_link(self.repo, MID)["authority"], "repo")
        # A third record is written in repository mode, then everything moves back.
        ents = MEM.read_records(files)
        r3 = rec(rev=2, entries=ents)
        R.write_record(self.repo, r3["record_id"], MEM.record_text(r3), MEM.records_digest(ents))
        back = R.plan_to_embedded(self.repo, MID, [], "<project folder>/..")
        self.assertEqual([a["action"] for a in back["actions"]], ["create", "create", "create"])
        files = R.read_records(self.repo)
        self.assertEqual(MEM.dats_digest(files), back["records_digest"])
        after = {n: MEM.text_sha(t) for n, t in files}
        self.assertEqual({k: after[k] for k in before}, before)          # hashes unchanged in both directions
        self.assertEqual(len(after), 3)
        # Stale copies left in the component are replaced or removed, never merged.
        stale = [(dats[0][0], dats[0][1].replace("in_progress", "blocked")), ("checkpoint_gone_r1", "x")]
        acts = {a["record"]: a["action"] for a in R.plan_to_embedded(self.repo, MID, stale)["actions"]}
        self.assertEqual(acts[dats[0][0]], "replace")
        self.assertEqual(acts["checkpoint_gone_r1"], "remove_stale_copy")

    def test_a_folder_with_a_different_version_of_a_record_is_a_conflict(self):
        dats = self.records()
        d = R.record_dir(self.repo)
        os.makedirs(d)
        with open(os.path.join(d, dats[0][0] + ".json"), "w") as f:
            f.write("different")
        with open(os.path.join(d, "checkpoint_extra_r1.json"), "w") as f:
            f.write("x")
        plan = R.plan_to_repo(self.repo, MID, dats)
        self.assertEqual(plan["conflicts"], ["checkpoint_blur_r1"])               # never overwritten
        self.assertEqual(plan["already_in_folder"], ["checkpoint_extra_r1"])       # left alone, not a dead end
        with self.assertRaises(R.RepoError) as c:
            R.to_repo(self.repo, MID, dats, plan["digest"])
        self.assertEqual(c.exception.code, "CONFLICT")
        self.assertFalse(os.path.exists(os.path.join(self.repo, R.PROJECT_FILE)))   # nothing was linked

    def test_records_already_in_the_folder_stay_and_face_the_ledger(self):         # review 11, 8c
        dats = self.records()
        d = R.record_dir(self.repo)
        os.makedirs(d)
        newer = rec(task="later")
        with open(os.path.join(d, newer["record_id"] + ".json"), "w", encoding="utf-8") as f:
            f.write(MEM.record_text(newer))
        plan = R.plan_to_repo(self.repo, MID, dats)
        self.assertEqual((plan["conflicts"], plan["already_in_folder"]), ([], ["checkpoint_later_r1"]))
        self.assertIn(R.PROJECT_FILE, plan["also_written"])
        R.to_repo(self.repo, MID, dats, plan["digest"])
        ents = MEM.read_records(R.read_records(self.repo))
        self.assertEqual(len(ents), 3)
        led = MEM.ledger([{"seq": 1, "kind": "admin", "tool": "memory_claim",
                           "detail": {"memory_id": MID, "records": {n: MEM.text_sha(t) for n, t in dats}}}], MID)
        self.assertEqual(MEM.assess(ents, led)["foreign"], ["checkpoint_later_r1"])  # nothing was laundered

    def test_remigrating_into_a_derived_export(self):
        dats = self.records()
        R.to_repo(self.repo, MID, dats, R.plan_to_repo(self.repo, MID, dats)["digest"])
        R.mark_derived(self.repo, MID)
        plan = R.plan_to_repo(self.repo, MID, dats)
        self.assertEqual((plan["project_file"], [a["action"] for a in plan["actions"]]), ("update", ["unchanged", "unchanged"]))
        R.to_repo(self.repo, MID, dats, plan["digest"])
        self.assertEqual(R.check_link(self.repo, MID)["authority"], "repo")
        R.log_migration(self.repo, {"from": "embedded", "to": "repo"})
        with open(os.path.join(R.record_dir(self.repo), R.MIGRATIONS)) as f:
            self.assertEqual(json.loads(f.read().strip())["to"], "repo")
        self.assertEqual(len(R.read_records(self.repo)), 2)             # the log is not a record


class RepoAdapter(FakeAdapter):
    """The component in repository mode: no record DATs, only the link."""

    def __init__(self, folder):
        super().__init__()
        self.folder, self.repopath, self.derived, self.checks = folder, "..", None, 0

    def memory_snapshot(self, task_id, scopes, claims, fingerprint, readable):
        out = super().memory_snapshot(task_id if scopes is not None else None, scopes, claims, fingerprint, readable)
        out.update(authority="repo", repopath=self.repopath, project_folder=self.folder, dats=[],
                   needs_scopes=bool(scopes is None and task_id and fingerprint))
        return out

    def memory_check(self, comp, memory_id, authority, repopath):
        self.checks += 1
        if repopath != self.repopath or authority != "repo":
            raise ToolFailure("MEMORY_CONFLICT", "project memory changed")
        return {"ok": True}

    def memory_derived(self, comp, memory_id, text):
        self.derived = text
        return {"written": True}

    def memory_write(self, *a, **kw):
        raise AssertionError("embedded write in repository mode")


class EngineRepoTests(Base):
    def setUp(self):
        super().setUp()
        self.store = Store(os.path.join(self.top, "d")).open()
        self.idn = Identity(self.store)
        _, tok = self.idn.create_agent("W", "builder", ["/project1/fx/**"], [CAP_MEMORY_REPO_WRITE])
        self.W = self.idn.authenticate(tok)
        _, tok = self.idn.create_agent("N", "builder", ["/project1/fx/**"])          # no dedicated permission
        self.N = self.idn.authenticate(tok)
        _, tok = self.idn.create_agent("O", "observer", ["/project1/fx/**"])
        self.O = self.idn.authenticate(tok)
        self.ad = RepoAdapter(self.proj)
        self.ad.fp = {"/project1/fx": "f0", "/project1/fx/level1": "l0"}
        self.susp = [False]
        self.eng = Engine(self.store, self.idn, Policy("/project1"), core_registry(), self.ad, ALL,
                          lambda: self.susp[0])
        R.write_project(self.repo, MID, "repo")

    def tearDown(self):
        self.store.close()
        super().tearDown()

    def call(self, tok, name, args):
        r = self.eng.call(tok, name, args)
        return r["isError"], r["structuredContent"]

    def cp(self, tok=None, rev=0, task="blur"):
        return self.call(tok or self.W, "tdflow_task_checkpoint", {"task_id": task, "title": "T", "status": "in_progress",
                                                                  "scopes": ["/project1/fx"], "based_on_rev": rev})

    def test_write_needs_the_dedicated_permission(self):               # owner condition D18
        err, out = self.cp(self.N)
        self.assertTrue(err)
        self.assertEqual((out["code"], out["rule_id"]), ("DENIED", "M8"))
        self.assertIn("repository-memory permission", out["message"])
        self.assertFalse(os.path.exists(R.record_dir(self.repo)))
        self.assertEqual(self.call(self.O, "tdflow_task_checkpoint", {"task_id": "blur", "title": "T", "status": "done",
                                   "scopes": ["/project1/fx"], "based_on_rev": 0})[1]["rule_id"], "M1")
        _, who = self.call(self.N, "tdflow_whoami", {})
        self.assertFalse(who["memory"]["repository_write"])
        self.assertTrue(self.call(self.W, "tdflow_whoami", {})[1]["memory"]["repository_write"])
        err, lst = self.call(self.N, "tdflow_task_list", {})           # reading stays role-and-scope based
        self.assertFalse(err, lst)
        self.assertEqual((lst["memory"]["authority"], lst["memory"]["folder"]), ("repo", "<project folder>/.."))

    def test_records_are_files_and_the_ledger_accepts_them(self):
        err, out = self.cp()
        self.assertFalse(err, out)
        self.assertEqual((out["record_id"], self.ad.checks), ("checkpoint_blur_r1", 1))
        files = R.read_records(self.repo)
        self.assertEqual([n for n, _ in files], ["checkpoint_blur_r1"])
        intent = [e for e in self.store.memory_events() if e["kind"] == "intent"][0]
        m = json.loads(intent["detail"])["memory"]
        self.assertEqual((m["sha256"], m["authority"]), (MEM.text_sha(files[0][1]), "repo"))
        self.assertEqual(MEM.derived_counts(self.ad.derived), (1, 1))
        err, out = self.call(self.W, "tdflow_handoff_create", {"task_id": "blur", "summary": "done", "status": "done",
                                                               "based_on_rev": 1})   # scopes from the files
        self.assertFalse(err, out)
        self.assertEqual(out["rev"], 2)
        _, lst = self.call(self.N, "tdflow_task_list", {})
        self.assertEqual((lst["memory"]["state"], lst["tasks"][0]["status"], lst["tasks"][0]["rev"]), ("ok", "done", 2))
        self.assertEqual(MEM.derived_counts(self.ad.derived), (1, 0))
        err, res = self.call(self.N, "tdflow_handoff_resume", {"task_id": "blur"})
        self.assertFalse(err, res)
        self.assertEqual((res["latest_rev"], res["handoff"]["ledger"], res["memory"]["authority"]), (2, "accepted", "repo"))

    def test_link_mismatch_blocks_reads_and_writes(self):              # ADR-0013 gate, end to end
        self.cp()
        os.remove(os.path.join(self.repo, R.PROJECT_FILE))
        R.write_project(self.repo, OTHER, "repo")
        for tok, name, args in ((self.N, "tdflow_task_list", {}), (self.N, "tdflow_handoff_resume", {"task_id": "blur"})):
            err, out = self.call(tok, name, args)
            self.assertTrue(err)
            self.assertEqual((out["code"], out["rule_id"]), ("MEMORY_LINK_MISMATCH", "M7"))
        err, out = self.cp(rev=1)
        self.assertEqual(out["code"], "MEMORY_LINK_MISMATCH")
        self.assertEqual(len(R.read_records(self.repo)), 1)

    def test_unlinked_or_missing_folder_is_unavailable(self):
        os.remove(os.path.join(self.repo, R.PROJECT_FILE))
        err, out = self.call(self.N, "tdflow_task_list", {})
        self.assertEqual((out["code"], out["rule_id"]), ("MEMORY_UNAVAILABLE", "M7"))
        self.assertIn("MEMORY_UNLINKED", out["message"])
        self.ad.repopath = "../../../../.."
        self.assertEqual(self.call(self.N, "tdflow_task_list", {})[1]["code"], "MEMORY_UNAVAILABLE")
        # The temp folder name is unique; json.dumps would double backslashes, so compare by name (review 11).
        self.assertNotIn(os.path.basename(self.top), json.dumps(self.call(self.N, "tdflow_task_list", {})[1]))

    def test_files_changed_outside_make_memory_read_only(self):
        self.cp()
        p = os.path.join(R.record_dir(self.repo), "checkpoint_blur_r1.json")
        with open(p, encoding="utf-8") as f:
            text = f.read()
        with open(p, "w", encoding="utf-8", newline="\n") as f:          # e.g. a git checkout of another version
            f.write(text.replace("in_progress", "blocked"))
        err, out = self.cp(rev=1)
        self.assertEqual((out["code"], out["rule_id"]), ("MEMORY_READ_ONLY", "M5"))
        self.assertIn("linked project-memory folder", out["message"])
        _, lst = self.call(self.N, "tdflow_task_list", {})
        self.assertEqual((lst["memory"]["state"], lst["memory"]["changed_outside"]), ("needs_claim", ["checkpoint_blur_r1"]))
        ents = MEM.read_records(R.read_records(self.repo))               # the user's Claim Project Memory
        self.store.append("admin", tool="memory_claim", outcome="accepted",
                          detail={"memory_id": MID, "records": MEM.claim_records(ents), "authority": "repo"})
        self.assertFalse(self.cp(rev=1)[0])

    def test_kill_switch_and_component_change_before_the_file_write(self):
        def main(fn):
            r = fn()
            if self.ad.checks == 1:                                    # right after the main-thread check
                self.susp[0] = True
            return r
        self.eng.main = main
        err, out = self.cp()
        self.assertEqual(out["code"], "SUSPENDED")
        self.assertEqual(R.read_records(self.repo), [])
        self.susp[0] = False
        self.eng.audit_failed = False
        self.eng.main = lambda fn: fn()
        self.ad.memory_check = lambda *a: (_ for _ in ()).throw(ToolFailure("MEMORY_CONFLICT", "changed"))
        err, out = self.cp()
        self.assertEqual(out["code"], "MEMORY_CONFLICT")
        self.assertEqual(R.read_records(self.repo), [])
        outs = [e for e in self.store.memory_events() if e["kind"] == "outcome"]
        self.assertEqual([e["outcome"] for e in outs], ["error", "error"])   # every intent got its outcome

    def test_concurrent_file_appearing_is_a_conflict(self):
        self.cp()
        orig = self.ad.memory_check

        def check(*a):
            other = rec(task="other")
            d = R.record_dir(self.repo)
            with open(os.path.join(d, other["record_id"] + ".json"), "w", encoding="utf-8") as f:
                f.write(MEM.record_text(other))
            return orig(*a)
        self.ad.memory_check = check
        err, out = self.cp(rev=1)
        self.assertEqual(out["code"], "MEMORY_CONFLICT")
        self.assertEqual(sorted(n for n, _ in R.read_records(self.repo)), ["checkpoint_blur_r1", "checkpoint_other_r1"])


if __name__ == "__main__":
    unittest.main()
