"""Durable audit store, identity/tokens, sessions and admin pairing."""
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest

from tdflowmcp.admin import PAIR_COOKIE, SESSION_COOKIE, AdminAuth, Authenticator, cookie_value
from tdflowmcp.identity import Identity, Sessions
from tdflowmcp.store import AuditUnavailable, Store, canonical, redact


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tdflow-test-")
        self.store = Store(os.path.join(self.dir, "data")).open()

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)


class StoreTests(Tmp):
    def test_chain_and_append_only(self):
        for i in range(5):
            self.store.append("intent", tool="t", agent_id="a", detail={"i": i})
        v = self.store.verify()
        self.assertTrue(v["ok"])
        self.assertEqual(v["events"], 5)
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.conn.execute("UPDATE events SET tool='x' WHERE seq=1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.conn.execute("DELETE FROM events WHERE seq=1")

    def test_tamper_detected_even_with_recomputed_chain(self):
        for i in range(4):
            self.store.append("outcome", tool="t", outcome="ok")
        self.store.checkpoint()
        c = self.store.conn
        c.execute("DROP TRIGGER events_no_update")                    # an attacker with file access
        c.execute("UPDATE events SET tool='evil' WHERE seq=2")
        v = self.store.verify()
        self.assertFalse(v["chain_ok"])
        self.assertEqual(v["first_bad_seq"], 2)
        # Recompute the whole unkeyed chain: the keyed checkpoint still exposes it.
        import hashlib
        from tdflowmcp.store import EVENT_FIELDS, GENESIS
        cols = ["seq", *EVENT_FIELDS]
        prev = GENESIS
        for row in c.execute(f"SELECT {', '.join(cols)} FROM events ORDER BY seq").fetchall():
            body = dict(zip(cols, row))
            h = hashlib.sha256((prev + canonical(body)).encode()).hexdigest()
            c.execute("UPDATE events SET prev_hash=?, hash=? WHERE seq=?", (prev, h, body["seq"]))
            prev = h
        v = self.store.verify()
        self.assertTrue(v["chain_ok"])
        self.assertFalse(v["checkpoints_ok"])
        self.assertFalse(v["ok"])

    def test_periodic_checkpoints_and_deleted_checkpoint_file(self):
        self.store.checkpoint_every = 5
        for i in range(12):
            self.store.append("intent")
        v = self.store.verify()
        self.assertTrue(v["ok"])
        self.assertEqual((v["verified_through"], v["unverified_tail"]), (10, 2))
        os.remove(self.store.checkpoint_path)
        v = self.store.verify()
        self.assertTrue(v["checkpoints_missing"])
        self.assertFalse(v["ok"])

    def test_forged_checkpoint_rejected(self):
        self.store.append("intent")
        with open(self.store.checkpoint_path, "a") as f:
            f.write(json.dumps({"seq": 1, "hash": "0" * 64, "mac": "00"}) + "\n")
        self.assertFalse(self.store.verify()["checkpoints_ok"])

    def test_floats_and_odd_values_in_detail_are_stored(self):
        seq = self.store.append("outcome", detail={"prev": {"opacity": 0.5}, "obj": object()}, duration_ms=1.7,
                                outcome=True)
        ev = self.store.events(limit=1)[0]
        self.assertEqual(ev["seq"], seq)
        self.assertIn("0.5", ev["detail"])
        self.assertTrue(self.store.verify()["ok"])

    def test_fail_closed_when_unavailable(self):
        self.store.close()
        with self.assertRaises(AuditUnavailable):
            self.store.append("intent")
        bad = Store(os.path.join(self.dir, "data", "audit.key", "x"))     # a file, not a directory
        with self.assertRaises(AuditUnavailable):
            bad.open()

    def test_wrong_thread_is_refused(self):
        err = []
        th = threading.Thread(target=lambda: self._try(err))
        th.start()
        th.join()
        self.assertEqual(err, ["AuditUnavailable"])

    def _try(self, err):
        try:
            self.store.append("intent")
        except AuditUnavailable:
            err.append("AuditUnavailable")

    def test_canonical_and_redaction(self):
        self.assertEqual(canonical({"b": 1, "a": [None, "x"]}), '{"a":[null,"x"],"b":1}')
        with self.assertRaises(TypeError):
            canonical({"a": 1.5})
        text = redact({"h": "Bearer abcdefghijklmnop", "t": "tdf_ABCDEFGHIJKL.abcdefghijklmnopqrstuvwxyz"})
        self.assertNotIn("abcdefghijklmnop", text)
        self.assertEqual(len(redact("x" * 5000, 100)), 100 + len("…<truncated>"))

    def test_args_hmac_is_keyed(self):
        import hashlib
        h = self.store.args_hmac({"a": 1})
        self.assertNotEqual(h, hashlib.sha256(b'{"a": 1}').hexdigest())
        self.assertEqual(h, self.store.args_hmac({"a": 1}))

    def test_persistence_across_reopen(self):
        self.store.append("outcome", session_id="s1", agent_id="a", outcome="ok")
        self.store.close()
        s2 = Store(self.store.datadir).open()
        self.assertEqual(s2.sessions()[0]["session_id"], "s1")
        self.assertTrue(s2.verify()["ok"])
        s2.close()
        self.store = Store(self.store.datadir).open()


class SplitStorageTests(unittest.TestCase):
    def test_project_folder_holds_no_credentials_key_or_checkpoints(self):
        root = tempfile.mkdtemp(prefix="tdflow-split-")
        try:
            proj, priv = os.path.join(root, "proj", ".tdflowmcp", "p"), os.path.join(root, "private")
            st = Store(proj, priv).open()
            idn = Identity(st)
            agent, tok = idn.create_agent("A", "builder", ["/project1/**"])
            st.append("intent", tool="x")
            st.checkpoint()
            st.close()
            self.assertEqual(sorted(f for f in os.listdir(proj) if not f.endswith(("-wal", "-shm"))), ["tdflowmcp.sqlite"])
            self.assertEqual(sorted(f for f in os.listdir(priv) if not f.endswith(("-wal", "-shm"))),
                             ["audit.key", "checkpoints.jsonl", "credentials.sqlite"])
            blob = b"".join(open(os.path.join(proj, f), "rb").read() for f in os.listdir(proj))
            self.assertNotIn(tok.split(".")[1].encode(), blob)
            self.assertNotIn(b"agt_", blob)
            st2 = Store(proj, priv).open()
            self.assertTrue(st2.verify()["ok"])
            self.assertEqual(Identity(st2).agents[agent.id].display_name, "A")
            st2.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)


class IdentityTests(Tmp):
    def test_create_authenticate_revoke(self):
        idn = Identity(self.store)
        agent, token = idn.create_agent("Claude", "builder", ["/project1/fx/**"])
        self.assertRegex(token, r"^tdf_[A-Za-z0-9]{12}\.[A-Za-z0-9_\-]{43,}$")
        tid = idn.authenticate(token)
        self.assertIsNotNone(tid)
        self.assertEqual(idn.agent_for_token(tid).id, agent.id)
        self.assertIsNone(idn.authenticate(token[:-1] + ("A" if token[-1] != "A" else "B")))
        self.assertIsNone(idn.authenticate("tdf_xxx"))
        self.assertNotIn(token.split(".")[1], json.dumps(idn.public_list()))
        rows = self.store.cconn.execute("SELECT * FROM tokens").fetchall()
        self.assertNotIn(token.split(".")[1], repr(rows))
        self.assertTrue(idn.revoke(tid))
        self.assertIsNone(idn.authenticate(token))
        self.assertIsNone(idn.agent_for_token(tid))

    def test_disable_agent_and_reload(self):
        idn = Identity(self.store)
        agent, token = idn.create_agent("A", "observer", ["/project1/**"])
        self.assertTrue(idn.has_enabled_agent())
        idn.set_enabled(agent.id, False)
        self.assertIsNone(idn.authenticate(token))
        self.assertFalse(idn.has_enabled_agent())
        idn2 = Identity(self.store)
        self.assertFalse(idn2.agents[agent.id].enabled)

    def test_validation(self):
        idn = Identity(self.store)
        for name, role, scopes in (("", "builder", ["/project1/**"]), ("a\x00", "builder", ["/project1/**"]),
                                   ("a", "root", ["/project1/**"]), ("a", "builder", []),
                                   ("a", "builder", ["project1"])):
            with self.assertRaises(ValueError):
                idn.create_agent(name, role, scopes)

    def test_sessions_group_by_idle_gap(self):
        t = [0.0]
        s = Sessions(idle_seconds=10, clock=lambda: t[0])
        a = s.session_for("tok", "ci")
        t[0] = 5
        self.assertEqual(s.session_for("tok", "ci"), a)
        self.assertNotEqual(s.session_for("tok", "other"), a)
        t[0] = 20
        self.assertNotEqual(s.session_for("tok", "ci"), a)


class H(dict):
    def get_all(self, k):
        v = self.get(k)
        return None if v is None else (v if isinstance(v, list) else [v])


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.t = [0.0]
        self.a = AdminAuth(clock=lambda: self.t[0])

    def test_pairing_flow(self):
        secret, code = self.a.request()
        self.assertRegex(code, r"^[A-Z2-9]{3}-[A-Z2-9]{3}$")
        ph = self.a.check("pair", H(Cookie=f"{PAIR_COOKIE}={secret}"))[5:]
        self.assertEqual(self.a.poll(ph), ("pending", None))
        self.assertTrue(self.a.approve())
        state, creds = self.a.poll(ph)
        self.assertEqual(state, "approved")
        sid, csrf = creds
        self.assertEqual(self.a.poll(ph), ("unknown", None))                # handed out once
        self.assertTrue(self.a.check("admin_session", H(Cookie=f"{SESSION_COOKIE}={sid}")))
        self.assertTrue(self.a.check("admin_session+csrf", H(Cookie=f"{SESSION_COOKIE}={sid}", **{"X-TDflow-CSRF": csrf})))
        self.assertIsNone(self.a.check("admin_session+csrf", H(Cookie=f"{SESSION_COOKIE}={sid}", **{"X-TDflow-CSRF": "x"})))
        self.assertIsNone(self.a.check("admin_session+csrf", H(Cookie=f"{SESSION_COOKIE}={sid}")))
        self.assertIsNone(self.a.check("admin_session", H(Cookie=f"{SESSION_COOKIE}=nope")))
        self.t[0] = 9 * 3600
        self.assertIsNone(self.a.check("admin_session", H(Cookie=f"{SESSION_COOKIE}={sid}")))

    def test_pending_is_never_replaced_reject_expiry_and_rate_limit(self):
        s1, _ = self.a.request()
        with self.assertRaises(LookupError):
            self.a.request()                                                 # no swap while pending
        self.assertTrue(self.a.reject())
        self.assertFalse(self.a.approve())
        self.a.request()                                                     # second counted request
        self.t[0] = 121                                                      # expires; new minute window
        self.assertFalse(self.a.approve())
        self.assertEqual(self.a.pending_view()["state"], "expired")
        self.a.request(); self.a.reject(); self.a.request(); self.a.reject(); self.a.request(); self.a.reject()
        with self.assertRaises(PermissionError):
            self.a.request()                                                 # 3 per minute

    def test_new_pairing_ends_older_sessions_and_logout(self):
        s1, _ = self.a.request()
        self.a.approve()
        sid1, _ = self.a.poll(self.a.check("pair", H(Cookie=f"{PAIR_COOKIE}={s1}"))[5:])[1]
        s2, _ = self.a.request()
        self.a.approve()
        sid2, _ = self.a.poll(self.a.check("pair", H(Cookie=f"{PAIR_COOKIE}={s2}"))[5:])[1]
        self.assertIsNone(self.a.check("admin_session", H(Cookie=f"{SESSION_COOKIE}={sid1}")))
        p = self.a.check("admin_session", H(Cookie=f"{SESSION_COOKIE}={sid2}"))
        self.assertTrue(p)
        self.a.logout(p[len("admin:"):])
        self.assertIsNone(self.a.check("admin_session", H(Cookie=f"{SESSION_COOKIE}={sid2}")))

    def test_conflicting_cookie_duplicates_refused(self):
        self.assertIsNone(cookie_value(H(Cookie=[f"{SESSION_COOKIE}=a", f"{SESSION_COOKIE}=b"]), SESSION_COOKIE))

    def test_authenticator_routes(self):
        class FakeId:
            def authenticate(self, tok):
                return "TID" if tok == "good" else None
        auth = Authenticator(FakeId(), self.a)
        self.assertEqual(auth("agent_token", H(Authorization="Bearer good")), "agent:TID")
        self.assertEqual(auth("agent_token", H(Authorization="bearer good")), "agent:TID")
        self.assertIsNone(auth("agent_token", H(Authorization=["Bearer good", "Bearer good"])))
        self.assertIsNone(auth("agent_token", H(Authorization="Basic good")))
        self.assertIsNone(auth("admin_session", H()))


if __name__ == "__main__":
    unittest.main()


class CrashTailTests(unittest.TestCase):
    def test_torn_final_checkpoint_line_is_ignored_but_middle_garbage_is_not(self):
        d = tempfile.mkdtemp(prefix="tdflow-torn-")
        try:
            st = Store(d).open()
            st.append("intent")
            st.checkpoint()
            with open(st.checkpoint_path, "a", encoding="utf-8") as f:
                f.write('{"seq": 1, "ha')                       # killed while writing
            self.assertTrue(st.verify()["ok"])
            with open(st.checkpoint_path, "a", encoding="utf-8") as f:
                f.write("\n")                                    # now a complete, bogus line
            st.append("intent")
            st.checkpoint()
            self.assertFalse(st.verify()["ok"])
            st.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Review7StoreTests(unittest.TestCase):
    def test_torn_tail_then_new_checkpoint_still_verifies(self):
        d = tempfile.mkdtemp(prefix="tdflow-torn2-")
        try:
            st = Store(d).open()
            st.append("intent")
            st.checkpoint()
            with open(st.checkpoint_path, "a", encoding="utf-8") as f:
                f.write('{"seq": 1, "ha')                       # killed mid-write
            st.append("intent")
            st.checkpoint()                                     # must not glue onto the fragment
            self.assertTrue(st.verify()["ok"])
            st.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_revoke_denies_even_if_reload_fails(self):
        d = tempfile.mkdtemp(prefix="tdflow-rev-")
        try:
            st = Store(d).open()
            idn = Identity(st)
            agent, tok = idn.create_agent("A", "builder", ["/project1/**"])
            tid = idn.authenticate(tok)
            self.assertTrue(tid)

            def boom():
                raise RuntimeError("timeout")
            idn.reload = boom
            idn.revoke(tid)
            self.assertIsNone(idn.authenticate(tok))
            self.assertIsNone(idn.agent_for_token(tid))
            st.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)
