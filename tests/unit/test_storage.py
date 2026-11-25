"""Project-local data storage: location classes, identity decisions, git protection, verified migration."""
import os
import shutil
import tempfile
import unittest

from tdflowmcp import storage as S
from tdflowmcp.store import Store


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tdflow-storage-")
        self.priv = os.path.join(self.root, "private")
        self.proj = os.path.join(self.root, "work")
        os.makedirs(self.proj)
        self.toe = os.path.join(self.proj, "show.toe")
        open(self.toe, "w").close()
        self.reg = S.Registry(self.priv)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class LocationTests(Base):
    def test_classes(self):
        self.assertEqual(S.classify_location(self.proj, is_remote=lambda p: False, env={}), "ok")
        self.assertEqual(S.classify_location(os.path.join(self.root, "nope"), is_remote=lambda p: False, env={}), "missing")
        self.assertEqual(S.classify_location(self.proj, is_remote=lambda p: True, env={}), "network")
        od = os.path.join(self.root, "OneDrive - Org", "proj")
        os.makedirs(od)
        self.assertEqual(S.classify_location(od, is_remote=lambda p: False, env={}), "cloud")
        self.assertEqual(S.classify_location(od, is_remote=lambda p: False,
                                             env={"OneDrive": os.path.join(self.root, "OneDrive - Org")}), "cloud")
        db = os.path.join(self.root, "Dropbox", "x")
        os.makedirs(db)
        self.assertEqual(S.classify_location(db, is_remote=lambda p: False, env={}), "cloud")


class DecisionTests(Base):
    loc = staticmethod(lambda folder: "ok")

    def test_new_project_is_project_local(self):
        d = S.resolve(self.toe, "", self.priv, self.reg, location=self.loc)
        self.assertEqual(d.status, "NEW")
        self.assertTrue(S.valid_id(d.project_id))
        self.assertEqual(d.data_dir, os.path.join(self.proj, ".tdflowmcp", "show"))
        self.assertEqual(d.private_dir, os.path.join(self.priv, "projects", d.project_id))

    def test_known_project_ok(self):
        d = S.resolve(self.toe, "", self.priv, self.reg, location=self.loc)
        self.reg.put(d.project_id, self.toe, d.data_dir)
        d2 = S.resolve(self.toe, d.project_id, self.priv, self.reg, location=self.loc)
        self.assertEqual((d2.status, d2.usable), ("OK", True))

    def test_unsaved_and_fallbacks(self):
        d = S.resolve(os.path.join(self.proj, "never_saved.toe"), "", self.priv, self.reg, location=self.loc)
        self.assertEqual(d.status, "UNSAVED")
        self.assertTrue(d.data_dir.startswith(self.priv))
        for cls in ("read_only", "network", "cloud"):
            d = S.resolve(self.toe, "", self.priv, self.reg, location=lambda f, c=cls: c)
            self.assertEqual(d.status, "FALLBACK", cls)
            self.assertTrue(d.data_dir.startswith(self.priv), cls)
            self.assertTrue(d.reason)
        d = S.resolve(self.toe, "", self.priv, self.reg, mode="private", location=self.loc)
        self.assertEqual(d.status, "FALLBACK")

    def test_moved_copied_and_unknown_identity(self):
        d = S.resolve(self.toe, "", self.priv, self.reg, location=self.loc)
        self.reg.put(d.project_id, self.toe, d.data_dir)
        copy = os.path.join(self.root, "other", "show_v2.toe")
        os.makedirs(os.path.dirname(copy))
        open(copy, "w").close()
        c = S.resolve(copy, d.project_id, self.priv, self.reg, location=self.loc)
        self.assertEqual((c.status, c.usable), ("COPIED", False))          # original still exists
        os.remove(self.toe)
        m = S.resolve(copy, d.project_id, self.priv, self.reg, location=self.loc)
        self.assertEqual((m.status, m.usable), ("MOVED", False))
        self.assertEqual(m.previous_data_dir, os.path.abspath(d.data_dir))
        u = S.resolve(copy, S.new_project_id(), self.priv, self.reg, location=self.loc)
        self.assertEqual((u.status, u.usable), ("COPIED", False))           # identity unknown here

    def test_unsaved_project_carrying_a_saved_projects_identity_is_a_copy(self):
        """Review 20 (3): a component pasted (or a .tox made from it) into a new, unsaved project must not open the
        saved project's data, also when that data lives in private storage."""
        d = S.resolve(self.toe, "", self.priv, self.reg, mode="private", location=self.loc)
        self.reg.put(d.project_id, self.toe, d.data_dir)
        u = S.resolve(os.path.join(self.proj, "unsaved_new.toe"), d.project_id, self.priv, self.reg, mode="private",
                      location=self.loc)
        self.assertEqual((u.status, u.usable), ("COPIED", False))
        self.assertFalse(S.valid_id(d.project_id + "\n"))                 # strict ids (no trailing newline)

    def test_new_id_and_copied_folder_suffix(self):
        nid = S.new_project_id()
        d = S.resolve(self.toe, "", self.priv, self.reg, new_id=nid, location=self.loc)
        self.assertEqual(d.project_id, nid)
        os.makedirs(d.data_dir)
        open(os.path.join(d.data_dir, S.DB_NAME), "w").close()          # another identity's database
        d2 = S.resolve(self.toe, "", self.priv, self.reg, location=self.loc)
        self.assertNotEqual(d2.data_dir, d.data_dir)
        self.assertTrue(os.path.basename(d2.data_dir).startswith("show-"))
        self.reg.put(d2.project_id, self.toe, d2.data_dir)
        d3 = S.resolve(self.toe, d2.project_id, self.priv, self.reg, location=self.loc)
        self.assertEqual((d3.status, d3.data_dir), ("OK", os.path.abspath(d2.data_dir)))   # registry wins

    def test_incremental_saves_are_the_same_project(self):
        self.assertEqual(S.base_stem("/x/show.12.toe"), "show")
        self.assertEqual(S.base_stem("/x/show.toe"), "show")
        self.assertEqual(S.base_stem("/x/v1.2.toe"), "v1")
        d = S.resolve(self.toe, "", self.priv, self.reg, location=self.loc)
        self.reg.put(d.project_id, self.toe, d.data_dir)
        nxt = os.path.join(self.proj, "show.1.toe")                       # TD's Ctrl+S with auto-increment
        open(nxt, "w").close()
        r = S.resolve(nxt, d.project_id, self.priv, self.reg, location=self.loc)
        self.assertEqual((r.status, r.data_dir), ("OK", os.path.abspath(d.data_dir)))
        other = os.path.join(self.proj, "other.toe")                     # a different project next to it
        open(other, "w").close()
        self.assertEqual(S.resolve(other, d.project_id, self.priv, self.reg, location=self.loc).status, "COPIED")

    def test_unsaved_project_id_is_recovered_from_registry(self):
        d = S.resolve(self.toe, "", self.priv, self.reg, location=self.loc)
        self.reg.put(d.project_id, self.toe, d.data_dir)
        again = S.resolve(self.toe, "", self.priv, self.reg, location=self.loc)   # Projectid never saved
        self.assertEqual((again.status, again.project_id), ("OK", d.project_id))

    def test_location_change_needs_confirmation(self):
        d = S.resolve(self.toe, "", self.priv, self.reg, location=self.loc)
        self.reg.put(d.project_id, self.toe, d.data_dir)
        os.makedirs(d.data_dir)
        open(os.path.join(d.data_dir, S.DB_NAME), "w").close()
        r = S.resolve(self.toe, d.project_id, self.priv, self.reg, mode="private", location=self.loc)
        self.assertEqual((r.status, r.usable), ("RELOCATE", False))
        self.assertEqual(r.previous_data_dir, os.path.abspath(d.data_dir))
        # Saved after being unsaved: the private-fallback data must be confirmed, not silently abandoned.
        u = S.resolve(os.path.join(self.proj, "later.toe"), "", self.priv, self.reg, location=self.loc)
        self.reg.put(u.project_id, "", u.data_dir)
        os.makedirs(u.data_dir)
        open(os.path.join(u.data_dir, S.DB_NAME), "w").close()
        open(os.path.join(self.proj, "later.toe"), "w").close()
        r2 = S.resolve(os.path.join(self.proj, "later.toe"), u.project_id, self.priv, self.reg, location=self.loc)
        self.assertEqual(r2.status, "RELOCATE")

    def test_gitignore(self):
        d = S.resolve(self.toe, "", self.priv, self.reg, location=self.loc)
        S.prepare_project_folder(d.data_dir)
        with open(os.path.join(self.proj, ".tdflowmcp", ".gitignore")) as f:
            self.assertIn("\n*\n", "\n" + f.read())


class MigrationTests(Base):
    def test_verified_copy_keeps_source_and_integrity(self):
        old = os.path.join(self.root, "old_layout")                         # 1B: one folder for everything
        st = Store(old).open()
        for i in range(7):
            st.append("outcome", tool="t", detail={"i": i})
        st.checkpoint()
        st.close()
        dst = os.path.join(self.proj, ".tdflowmcp", "show")
        priv = os.path.join(self.priv, "projects", "prj_0123456789abcdef")
        before = sorted(os.listdir(old))
        r = S.migrate(old, dst, priv, Store, src_private_dir=old)
        self.assertEqual(sorted(os.listdir(old)), before)                   # nothing created in the source
        self.assertTrue(r["verified"])
        self.assertEqual(r["events"], 7)
        self.assertTrue(os.path.exists(os.path.join(old, "tdflowmcp.sqlite")))  # source untouched
        with open(os.path.join(dst, "tdflowmcp.sqlite"), "rb") as f:
            blob = f.read()
        self.assertNotIn(b"CREATE TABLE tokens", blob)                     # credentials stripped
        st2 = Store(dst, priv).open()
        v = st2.verify()
        self.assertTrue(v["ok"])
        self.assertEqual(v["events"], 8)                                     # + the migration event
        st2.close()
        with self.assertRaises(FileExistsError):
            S.migrate(old, dst, priv, Store, src_private_dir=old)

    def _proj_store(self, n=3):
        d = S.resolve(self.toe, "", self.priv, self.reg, location=lambda f: "ok")
        S.prepare_project_folder(d.data_dir)
        st = Store(d.data_dir, d.private_dir).open()
        st.bind_project(d.project_id)
        for i in range(n):
            st.append("outcome", tool="t", detail={"i": i})
        st.checkpoint()
        st.close()
        self.reg.put(d.project_id, self.toe, d.data_dir)
        return d

    def test_confirm_rename_copies_and_verifies(self):
        d = self._proj_store()
        new_toe = os.path.join(self.proj, "renamed.toe")
        os.replace(self.toe, new_toe)
        m = S.resolve(new_toe, d.project_id, self.priv, self.reg, location=lambda f: "ok")
        self.assertEqual(m.status, "MOVED")
        r = S.confirm_location(m, Store)
        self.assertTrue(r["verified"])
        self.assertTrue(os.path.exists(os.path.join(d.data_dir, S.DB_NAME)))          # source untouched
        st = Store(m.data_dir, m.private_dir).open()
        self.assertTrue(st.verify()["ok"])
        self.assertEqual(st.bind_project(d.project_id), d.project_id)
        st.close()

    def test_confirm_folder_move_adopts_moved_database(self):
        d = self._proj_store()
        moved = os.path.join(self.root, "moved")
        shutil.copytree(self.proj, moved)                                      # the user moved the folder
        shutil.rmtree(self.proj)
        m = S.resolve(os.path.join(moved, "show.toe"), d.project_id, self.priv, self.reg, location=lambda f: "ok")
        self.assertEqual(m.status, "MOVED")
        r = S.confirm_location(m, Store)
        self.assertTrue(r["adopted"])

    def test_database_of_another_project_is_refused(self):
        d = self._proj_store()
        st = Store(d.data_dir, d.private_dir).open()
        from tdflowmcp.store import AuditUnavailable
        with self.assertRaises(AuditUnavailable):
            st.bind_project(S.new_project_id())
        st.close()

    def test_tampered_source_is_not_migrated(self):
        old = os.path.join(self.root, "old2")
        st = Store(old).open()
        st.append("outcome", tool="t")
        st.append("outcome", tool="t")
        st.conn.execute("DROP TRIGGER events_no_update")
        st.conn.execute("UPDATE events SET tool='evil' WHERE seq=1")
        st.close()
        dst = os.path.join(self.proj, ".tdflowmcp", "x")
        with self.assertRaises(ValueError):
            S.migrate(old, dst, os.path.join(self.priv, "p"), Store, src_private_dir=old)
        self.assertFalse(os.path.exists(os.path.join(dst, "tdflowmcp.sqlite")))


if __name__ == "__main__":
    unittest.main()


class Review7StorageTests(Base):
    def test_toe_name_cannot_escape_tdflowmcp_folder(self):
        for bad in ("...1.toe", "..1.toe", ". .toe"):
            self.assertEqual(S.base_stem(os.path.join(self.proj, bad)), "project", bad)
        evil = os.path.join(self.proj, "...1.toe")
        open(evil, "w").close()
        d = S.resolve(evil, "", self.priv, self.reg, location=lambda f: "ok")
        self.assertEqual(os.path.dirname(d.data_dir), os.path.join(self.proj, ".tdflowmcp"))

    def test_incremental_sibling_left_behind_means_copy(self):
        d = S.resolve(self.toe, "", self.priv, self.reg, location=lambda f: "ok")
        self.reg.put(d.project_id, self.toe, d.data_dir)
        open(os.path.join(self.proj, "show.3.toe"), "w").close()           # a later save of the original
        os.remove(self.toe)
        other = os.path.join(self.root, "elsewhere", "show.toe")
        os.makedirs(os.path.dirname(other))
        open(other, "w").close()
        self.assertEqual(S.resolve(other, d.project_id, self.priv, self.reg, location=lambda f: "ok").status, "COPIED")
