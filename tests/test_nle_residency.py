"""Residency rules must fail closed: no consent -> no vendor project,
no private server -> no private project, and never a silent mode switch."""

import tempfile
import unittest
from pathlib import Path

from lfmc.core import db
from lfmc.nle import registry, residency


class MatrixTests(unittest.TestCase):
    def test_four_paths_documented_and_all_unverified(self):
        matrix = registry.capability_matrix()
        pairs = {(a["nle"], a["mode"]) for a in matrix["adapters"]}
        self.assertEqual(pairs, {("premiere", "vendor_hosted"), ("premiere", "private_controlled"),
                                 ("resolve", "vendor_hosted"), ("resolve", "private_controlled")})
        for a in matrix["adapters"]:
            self.assertTrue(a["verification_status"].startswith("unverified"), a["key"])
            self.assertTrue(a["originals_stay_local"])
            self.assertEqual(a["media_sync_default"], "disabled")
            self.assertTrue(a["official_sources"], "each adapter must cite official sources")
            self.assertTrue(a["unverified_claims"], "must state what is NOT proven")

    def test_user_side_licensing_is_explicit(self):
        matrix = registry.capability_matrix()
        self.assertTrue(matrix["nle_access_is_user_side"])
        self.assertIn("does not buy", matrix["nle_access_note"])


class PreflightTests(unittest.TestCase):
    def test_unknown_combination_fails_closed(self):
        r = registry.preflight(nle="finalcut", mode="vendor_hosted")
        self.assertFalse(r["allowed"])
        self.assertEqual(r["blockers"][0]["code"], "unsupported_combination")
        self.assertTrue(r["silent_fallback_forbidden"])

    def test_vendor_mode_requires_consent(self):
        r = registry.preflight(nle="premiere", mode="vendor_hosted",
                               user_acknowledged_access=True, consent_recorded=False)
        self.assertFalse(r["allowed"])
        codes = {b["code"] for b in r["blockers"]}
        self.assertIn("vendor_consent_required", codes)

    def test_vendor_mode_requires_nle_access(self):
        r = registry.preflight(nle="premiere", mode="vendor_hosted",
                               user_acknowledged_access=False, consent_recorded=True)
        self.assertFalse(r["allowed"])
        self.assertIn("nle_access_not_confirmed", {b["code"] for b in r["blockers"]})

    def test_vendor_mode_allowed_with_consent_and_access(self):
        r = registry.preflight(nle="premiere", mode="vendor_hosted",
                               user_acknowledged_access=True, consent_recorded=True)
        self.assertTrue(r["allowed"])
        self.assertIn("integration_unverified", {w["code"] for w in r["warnings"]})
        self.assertFalse(r["platform_stores_project_contents"])

    def test_resolve_vendor_mode_warns_about_user_side_library_fee(self):
        r = registry.preflight(nle="resolve", mode="vendor_hosted",
                               user_acknowledged_access=True, consent_recorded=True)
        self.assertTrue(r["allowed"])
        self.assertIn("vendor_hosted_resolve_is_not_free",
                      {w["code"] for w in r["warnings"]})

    def test_private_mode_requires_configured_server(self):
        r = registry.preflight(nle="resolve", mode="private_controlled",
                               user_acknowledged_access=True,
                               private_server_configured=False)
        self.assertFalse(r["allowed"])
        self.assertIn("private_server_not_configured", {b["code"] for b in r["blockers"]})

    def test_private_mode_allowed_and_authoritative_state_is_local(self):
        r = registry.preflight(nle="resolve", mode="private_controlled",
                               user_acknowledged_access=True,
                               private_server_configured=True)
        self.assertTrue(r["allowed"])
        self.assertEqual(r["authoritative_state"], "private_project_server")
        self.assertIn("customer-controlled", r["disclosure"]["nle_project_state"])

    def test_media_sync_blocks_until_separately_approved(self):
        r = registry.preflight(nle="premiere", mode="vendor_hosted",
                               user_acknowledged_access=True, consent_recorded=True,
                               media_sync_enabled=True)
        self.assertFalse(r["allowed"])
        self.assertIn("media_sync_requires_separate_approval",
                      {b["code"] for b in r["blockers"]})

    def test_disclosure_separates_nle_state_from_platform_data(self):
        r = registry.preflight(nle="premiere", mode="private_controlled",
                               user_acknowledged_access=True,
                               private_server_configured=True)
        d = r["disclosure"]
        self.assertIn("customer-controlled", d["nle_project_state"])
        self.assertIn("local", d["catalogue_and_index"].lower())
        self.assertEqual(d["relay_traffic"], "None in this build: no third-party relay is used or required.")


class BindingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self._tmp.name) / "c.db")
        self.project = residency.ensure_project(self.conn, "Addis Launch Film")

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_binding_creation_blocked_without_consent(self):
        with self.assertRaises(residency.ResidencyBlocked) as ctx:
            residency.create_binding(self.conn, project_id=self.project, nle="premiere",
                                     mode="vendor_hosted", user_acknowledged_access=True,
                                     consent_recorded=False)
        self.assertIn("vendor_consent_required",
                      {b["code"] for b in ctx.exception.blockers})

    def test_binding_records_mode_consent_and_disclosure(self):
        b = residency.create_binding(self.conn, project_id=self.project, nle="premiere",
                                     mode="vendor_hosted", user_acknowledged_access=True,
                                     consent_recorded=True, consent_by="producer@example.test",
                                     service_location="Adobe Creative Cloud (vendor-hosted)")
        self.assertEqual(b["mode"], "vendor_hosted")
        self.assertEqual(b["status"], "configured_unverified")
        self.assertEqual(b["media_sync_enabled"], 0)
        self.assertIsNotNone(b["consent_recorded_at"])
        self.assertIn("nle_project_state", b["disclosure"])
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM events WHERE kind='nle.binding.created'").fetchone()
        self.assertEqual(row["c"], 1)

    def test_no_project_contents_are_stored(self):
        residency.create_binding(self.conn, project_id=self.project, nle="resolve",
                                 mode="private_controlled", user_acknowledged_access=True,
                                 consent_recorded=False, private_server_configured=True)
        cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(nle_bindings)")]
        for forbidden in ("project_bytes", "prproj", "drp", "project_blob"):
            self.assertNotIn(forbidden, cols)

    def test_silent_mode_change_is_refused(self):
        b = residency.create_binding(self.conn, project_id=self.project, nle="resolve",
                                     mode="vendor_hosted", user_acknowledged_access=True,
                                     consent_recorded=True)
        with self.assertRaises(residency.SilentFallbackRefused):
            residency.change_mode(self.conn, b["id"], "private_controlled", approved_by="x")
        unchanged = residency.get_binding(self.conn, b["id"])
        self.assertEqual(unchanged["mode"], "vendor_hosted")

    def test_approved_mode_change_clears_old_consent(self):
        b = residency.create_binding(self.conn, project_id=self.project, nle="resolve",
                                     mode="vendor_hosted", user_acknowledged_access=True,
                                     consent_recorded=True, consent_by="owner")
        changed = residency.change_mode(self.conn, b["id"], "private_controlled",
                                        approved_by="owner", allow_switch=True)
        self.assertEqual(changed["mode"], "private_controlled")
        self.assertIsNone(changed["consent_recorded_at"],
                          "consent for one residency mode must not carry to another")
        events = self.conn.execute(
            "SELECT COUNT(*) c FROM events WHERE kind='nle.binding.mode_changed'").fetchone()
        self.assertEqual(events["c"], 1)


if __name__ == "__main__":
    unittest.main()
