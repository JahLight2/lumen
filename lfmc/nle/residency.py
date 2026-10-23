"""Per-project NLE data-residency records and mode-change guards.

We store: NLE type, mode, which service/storage is authoritative, whether an
account is required, whether media sync is on, consent record, and status.
We do NOT store project contents. The NLE (or the customer's server) remains
authoritative.
"""

from __future__ import annotations

import json
import uuid

from ..core.db import now_iso
from . import registry


class ResidencyBlocked(RuntimeError):
    """Raised when a configuration would violate a residency/consent rule."""

    def __init__(self, message: str, blockers: list[dict]):
        super().__init__(message)
        self.blockers = blockers


class SilentFallbackRefused(ResidencyBlocked):
    """Raised when something tries to switch modes without explicit approval."""


def ensure_project(conn, name: str) -> str:
    row = conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    project_id = str(uuid.uuid4())
    conn.execute("INSERT INTO projects(id, name, created_at) VALUES(?,?,?)",
                 (project_id, name, now_iso()))
    conn.commit()
    return project_id


def create_binding(conn, *, project_id: str, nle: str, mode: str,
                   user_acknowledged_access: bool, consent_recorded: bool,
                   consent_by: str | None = None,
                   private_server_configured: bool = False,
                   service_location: str | None = None,
                   media_sync_enabled: bool = False) -> dict:
    """Create an NLE binding only if preflight allows it. Fails closed."""
    result = registry.preflight(
        nle=nle, mode=mode,
        user_acknowledged_access=user_acknowledged_access,
        consent_recorded=consent_recorded,
        private_server_configured=private_server_configured,
        media_sync_enabled=media_sync_enabled,
    )
    if not result["allowed"]:
        raise ResidencyBlocked(
            f"Cannot bind {nle}/{mode}: {len(result['blockers'])} blocker(s).", result["blockers"]
        )

    adapter = registry.get_adapter(nle, mode)
    binding_id = str(uuid.uuid4())
    ts = now_iso()
    conn.execute(
        """INSERT INTO nle_bindings(id, project_id, nle, mode, adapter, authoritative_state,
                                    service_location, account_required, media_sync_enabled, status,
                                    consent_recorded_at, consent_by, disclosure, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (binding_id, project_id, nle, mode, adapter.key, adapter.authoritative_state,
         service_location, 1 if adapter.account_required else 0, 1 if media_sync_enabled else 0,
         "configured_unverified",
         ts if consent_recorded else None, consent_by,
         json.dumps(result["disclosure"]), ts, ts),
    )
    conn.execute(
        "INSERT INTO events(ts, kind, actor, subject, detail) VALUES(?,?,?,?,?)",
        (ts, "nle.binding.created", consent_by, binding_id,
         json.dumps({"nle": nle, "mode": mode, "adapter": adapter.key})),
    )
    conn.commit()
    return get_binding(conn, binding_id)


def get_binding(conn, binding_id: str) -> dict:
    row = conn.execute("SELECT * FROM nle_bindings WHERE id=?", (binding_id,)).fetchone()
    if row is None:
        raise KeyError(binding_id)
    d = dict(row)
    d["disclosure"] = json.loads(d["disclosure"]) if d["disclosure"] else {}
    adapter = registry.ADAPTERS.get(d["adapter"])
    d["verification_status"] = adapter.verification_status if adapter else "unknown"
    d["unverified_claims"] = adapter.unverified_claims if adapter else []
    return d


def list_bindings(conn, project_id: str | None = None) -> list[dict]:
    if project_id:
        rows = conn.execute("SELECT id FROM nle_bindings WHERE project_id=? ORDER BY created_at",
                            (project_id,)).fetchall()
    else:
        rows = conn.execute("SELECT id FROM nle_bindings ORDER BY created_at").fetchall()
    return [get_binding(conn, r["id"]) for r in rows]


def change_mode(conn, binding_id: str, new_mode: str, *, approved_by: str,
                allow_switch: bool = False) -> dict:
    """Change residency mode. Never silent; consent does not carry across modes."""
    current = get_binding(conn, binding_id)
    if current["mode"] == new_mode:
        return current
    if not allow_switch:
        raise SilentFallbackRefused(
            "Refusing to change NLE data-residency mode without explicit approval.",
            [{
                "code": "silent_fallback_forbidden",
                "message": f"Mode change {current['mode']} -> {new_mode} requires explicit confirmation.",
                "user_action": "Re-run with allow_switch=True and re-record consent for the new mode.",
            }],
        )
    result = registry.preflight(nle=current["nle"], mode=new_mode,
                               user_acknowledged_access=True,
                               consent_recorded=(new_mode == "vendor_hosted"),
                               private_server_configured=(new_mode == "private_controlled"),
                               media_sync_enabled=bool(current["media_sync_enabled"]))
    if not result["allowed"]:
        raise ResidencyBlocked("New mode failed preflight", result["blockers"])

    ts = now_iso()
    conn.execute(
        "UPDATE nle_bindings SET mode=?, adapter=?, authoritative_state=?, status=?, "
        "consent_recorded_at=NULL, consent_by=NULL, disclosure=?, updated_at=? WHERE id=?",
        (new_mode, result["adapter"], result["authoritative_state"], "configured_unverified",
         json.dumps(result["disclosure"]), ts, binding_id),
    )
    conn.execute(
        "INSERT INTO events(ts, kind, actor, subject, detail) VALUES(?,?,?,?,?)",
        (ts, "nle.binding.mode_changed", approved_by, binding_id,
         json.dumps({"from": current["mode"], "to": new_mode, "approved": True})),
    )
    conn.commit()
    return get_binding(conn, binding_id)
