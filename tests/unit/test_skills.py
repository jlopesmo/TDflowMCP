"""Skills install/update/uninstall round-trip on disposable directories (ADR-0011 validation gate)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.unit import SRC
from tdflowmcp import SKILL_VERSION, skills as K

CLIENT = {"skills": {"user_dir": "~/.claude/skills", "project_dir": ".claude/skills"}}
TEXT = (SRC / "skills" / "tdflow-core" / "SKILL.md").read_text(encoding="utf-8")
BUNDLE = {"tdflow-core/SKILL.md": TEXT}
EXPECTED = {"tdflow-core/SKILL.md": K.sha(TEXT)}


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tdflow-skills-home-")
        self.base, self.target = K.resolve_target(CLIENT, "user", self.home, "")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)


class SkillsTests(Base):
    def test_bundle_hash_and_frontmatter(self):
        self.assertEqual(K.verify_bundle(BUNDLE, EXPECTED), BUNDLE)
        with self.assertRaises(K.SkillsError):
            K.verify_bundle({"tdflow-core/SKILL.md": TEXT + "x"}, EXPECTED)
        self.assertTrue(TEXT.startswith("---\nname: tdflow-core\n"))
        self.assertIn(f'tdflow-skill-version: "{SKILL_VERSION}"', TEXT)
        self.assertLess(len(TEXT.splitlines()), 500)

    def test_round_trip_install_update_uninstall(self):
        p = K.preview(self.base, self.target, BUNDLE, SKILL_VERSION)
        self.assertEqual([a["action"] for a in p["actions"]], ["create"])
        self.assertFalse(os.path.exists(self.target))                         # preview writes nothing
        r = K.install(self.base, self.target, BUNDLE, SKILL_VERSION, p["digest"], "TDflowMCP test")
        self.assertEqual(r["written"], ["tdflow-core/SKILL.md"])
        man = K.read_manifest(self.target)
        self.assertEqual(man["version"], SKILL_VERSION)
        # Update: the installed file is unmodified, so a new bundle version may replace it.
        new = {"tdflow-core/SKILL.md": TEXT + "\nnew line\n"}
        p2 = K.preview(self.base, self.target, new, "1.1")
        self.assertEqual(p2["actions"][0]["action"], "update")
        self.assertEqual(p2["installed_version"], SKILL_VERSION)
        K.install(self.base, self.target, new, "1.1", p2["digest"], "TDflowMCP test")
        # The user edits the file: neither update nor uninstall may touch it.
        f = os.path.join(self.target, "tdflow-core", "SKILL.md")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("\nmy own note\n")
        p3 = K.preview(self.base, self.target, BUNDLE, SKILL_VERSION)
        self.assertEqual(p3["conflicts"], ["tdflow-core/SKILL.md"])
        with self.assertRaises(K.SkillsError) as cm:
            K.install(self.base, self.target, BUNDLE, SKILL_VERSION, p3["digest"], "t")
        self.assertEqual(cm.exception.code, "CONFLICT")
        u = K.preview(self.base, self.target, {}, "", "uninstall")
        self.assertEqual(u["actions"][0]["action"], "keep_modified")
        r = K.uninstall(self.base, self.target, u["digest"])
        self.assertEqual(r["kept_modified"], ["tdflow-core/SKILL.md"])
        self.assertTrue(os.path.exists(f))
        self.assertFalse(os.path.exists(os.path.join(self.target, K.MANIFEST)))

    def test_clean_uninstall_and_preview_changed(self):
        p = K.preview(self.base, self.target, BUNDLE, SKILL_VERSION)
        K.install(self.base, self.target, BUNDLE, SKILL_VERSION, p["digest"], "t")
        u = K.preview(self.base, self.target, {}, "", "uninstall")
        os.remove(os.path.join(self.target, "tdflow-core", "SKILL.md"))           # changed after the preview
        with self.assertRaises(K.SkillsError) as cm:
            K.uninstall(self.base, self.target, u["digest"])
        self.assertEqual(cm.exception.code, "PREVIEW_CHANGED")
        u = K.preview(self.base, self.target, {}, "", "uninstall")
        K.uninstall(self.base, self.target, u["digest"])
        self.assertFalse(os.path.exists(os.path.join(self.target, "tdflow-core")))

    def test_targets_paths_and_foreign_skills(self):
        with self.assertRaises(K.SkillsError):
            K.resolve_target({"skills": None}, "user", self.home, "")
        with self.assertRaises(K.SkillsError):
            K.resolve_target(CLIENT, "elsewhere", self.home, "")
        with self.assertRaises(K.SkillsError):
            K.resolve_target(CLIENT, "project", self.home, "")                  # unsaved project
        with self.assertRaises(K.SkillsError):
            K.verify_bundle({"../evil/SKILL.md": "x"}, {"../evil/SKILL.md": K.sha("x")})
        with self.assertRaises(K.SkillsError):
            K.verify_bundle({"td-other/SKILL.md": "x"}, {"td-other/SKILL.md": K.sha("x")})   # not our namespace
        # A foreign td-* skill next to ours is never touched.
        other = os.path.join(self.target, "td-foreign", "SKILL.md")
        os.makedirs(os.path.dirname(other))
        open(other, "w").close()
        p = K.preview(self.base, self.target, BUNDLE, SKILL_VERSION)
        K.install(self.base, self.target, BUNDLE, SKILL_VERSION, p["digest"], "t")
        u = K.preview(self.base, self.target, {}, "", "uninstall")
        K.uninstall(self.base, self.target, u["digest"])
        self.assertTrue(os.path.exists(other))

    def test_symlink_or_junction_rejected(self):
        real = os.path.join(self.home, "elsewhere")
        os.makedirs(real)
        link = os.path.join(self.home, ".claude")
        if sys.platform == "win32":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", link, real], capture_output=True)
            if r.returncode != 0:
                self.skipTest("cannot create a junction here")
        else:
            os.symlink(real, link)
        with self.assertRaises(K.SkillsError) as cm:
            K.preview(self.base, self.target, BUNDLE, SKILL_VERSION)
        self.assertEqual(cm.exception.code, "SYMLINK_REJECTED")
        self.assertEqual(os.listdir(real), [])


if __name__ == "__main__":
    unittest.main()


class Review7SkillsTests(Base):
    def _junction(self, link, real):
        if sys.platform == "win32":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", link, real], capture_output=True)
            if r.returncode != 0:
                self.skipTest("cannot create a junction here")
        else:
            os.symlink(real, link)

    def test_uninstall_refuses_linked_skill_dir(self):
        outside = os.path.join(self.home, "outside")
        os.makedirs(outside)
        victim = os.path.join(outside, "SKILL.md")
        with open(victim, "w", encoding="utf-8") as f:
            f.write("precious")
        os.makedirs(self.target)
        self._junction(os.path.join(self.target, "tdflow-core"), outside)
        with open(os.path.join(self.target, K.MANIFEST), "w", encoding="utf-8") as f:
            json.dump({"version": "1.0", "files": {"tdflow-core/SKILL.md": K.sha("precious")}}, f)
        with self.assertRaises(K.SkillsError) as cm:
            K.preview(self.base, self.target, {}, "", "uninstall")
        self.assertEqual(cm.exception.code, "SYMLINK_REJECTED")
        self.assertTrue(os.path.exists(victim))

    def test_planted_temp_name_is_not_followed(self):
        p = K.preview(self.base, self.target, BUNDLE, SKILL_VERSION)
        os.makedirs(os.path.join(self.target, "tdflow-core"))
        planted = os.path.join(self.target, "tdflow-core", "SKILL.md.tdflow-tmp")   # the old fixed name
        with open(planted, "w") as f:
            f.write("x")
        p = K.preview(self.base, self.target, BUNDLE, SKILL_VERSION)
        K.install(self.base, self.target, BUNDLE, SKILL_VERSION, p["digest"], "t")
        self.assertEqual(open(planted).read(), "x")
        self.assertEqual(open(os.path.join(self.target, "tdflow-core", "SKILL.md"), encoding="utf-8").read(),
                         TEXT.replace("\r\n", "\n"))
