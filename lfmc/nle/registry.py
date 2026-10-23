"""NLE collaboration adapters: capability matrix + fail-closed preflight.

Rules encoded here (blueprint sections 4G, 6F, acceptance criteria):

* Two data-residency modes per project: `vendor_hosted` and `private_controlled`.
* Never silently fall back from private to vendor-hosted.
* Never claim a vendor integration works: every adapter is `unverified` until a
  human records a real test on real machines with real NLE installs.
* Vendor collaboration entitlements and NLE licences are USER-SIDE costs. The
  platform does not buy, bundle, reimburse or resell them.
* Media sync / proxy upload defaults off and requires separate approval.
* We store references, mode, consent and status only -- never a second copy of
  proprietary project contents, and never a byte-merge of .prproj/.drp.

Official sources are cited inline so the capability matrix can be re-verified
before each pilot; see docs/COSTS.md for the verified-as-of date and the
open questions (e.g. conflicting statements about Team Projects eligibility).
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERIFICATION_PENDING = "unverified-needs-real-nle-test"

SOURCE_ADOBE_TEAM_PROJECTS = "https://helpx.adobe.com/premiere/desktop/collaborate-with-others/collaborate-using-team-projects/about-team-projects.html"
SOURCE_ADOBE_TEAM_PROJECTS_ELIGIBILITY = "https://helpx.adobe.com/premiere/desktop/collaborate-with-others/collaborate-using-team-projects/collaboration-using-team-projects.html"
SOURCE_ADOBE_PRODUCTIONS = "https://helpx.adobe.com/premiere/desktop/collaborate-with-others/collaborate-using-productions/about-productions.html"
SOURCE_BMD_COLLAB = "https://www.blackmagicdesign.com/products/davinciresolve/collaboration"
SOURCE_BMD_FREE_PLAN = "https://help.cloud.blackmagicdesign.com/getting-started/free-plan/"


@dataclass
class Adapter:
    key: str
    nle: str
    mode: str
    authoritative_state: str
    display_name: str
    account_required: bool
    user_side_access: str
    official_sources: list[str]
    verification_status: str = VERIFICATION_PENDING
    known_constraints: list[str] = field(default_factory=list)
    unverified_claims: list[str] = field(default_factory=list)


ADAPTERS: dict[str, Adapter] = {
    "premiere.team_projects": Adapter(
        key="premiere.team_projects",
        nle="premiere",
        mode="vendor_hosted",
        authoritative_state="team_projects",
        display_name="Adobe Team Projects (hosted by Adobe Creative Cloud)",
        account_required=True,
        user_side_access="Adobe Creative Cloud subscription (or Teams/Enterprise plan) plus an Adobe ID "
                         "signed in to the Creative Cloud desktop app, per participating editor.",
        official_sources=[SOURCE_ADOBE_TEAM_PROJECTS, SOURCE_ADOBE_TEAM_PROJECTS_ELIGIBILITY],
        known_constraints=[
            "Project metadata, locks and version history are hosted by Adobe (vendor-hosted by definition).",
            "Premiere builds older than 24.6.1 lost Team Projects functionality; version must be current.",
            "Adobe's own help pages conflict on whether Individual CC plans include Team Projects "
            "(one page says 'all Creative Cloud subscriptions, including Individual'; secondary sources "
            "say Teams/Enterprise only). MUST be verified on the pilot's actual plan before relying on it.",
            "Referenced media must be reachable by every collaborator; originals stay on our host drive.",
        ],
        unverified_claims=[
            "That Team Projects can consume media through our remote mount at all",
            "That per-sequence locking behaves acceptably over a WAN-mounted path",
            "Publish/version propagation timing",
        ],
    ),
    "premiere.productions": Adapter(
        key="premiere.productions",
        nle="premiere",
        mode="private_controlled",
        authoritative_state="productions_shared_storage",
        display_name="Premiere Productions on customer-controlled shared storage",
        account_required=True,
        user_side_access="Adobe Premiere Pro licence per editor (Productions is a Premiere feature, "
                         "not a hosted service) plus compatible customer-controlled shared storage.",
        official_sources=[SOURCE_ADOBE_PRODUCTIONS],
        known_constraints=[
            "Productions is designed around shared storage semantics (NAS/SAN/local shared volume).",
            "Adobe documents that collaborators need access to the same media and project files.",
            "Our peer-to-peer mount is NOT assumed to provide NAS semantics: lock/write/version "
            "behaviour on the exact topology must be proven before this mode ships.",
            "A VPN or P2P mount may be slow for media access; Adobe explicitly notes VPN is slow for media.",
        ],
        unverified_claims=[
            "That project-file locking is safe over a P2P/fuse mount",
            "That concurrent writes cannot corrupt a .prproj on a non-NAS filesystem",
            "Recovery behaviour after an induced offline conflict",
        ],
    ),
    "resolve.blackmagic_cloud_project_server": Adapter(
        key="resolve.blackmagic_cloud_project_server",
        nle="resolve",
        mode="vendor_hosted",
        authoritative_state="blackmagic_cloud_library",
        display_name="Blackmagic Cloud Project Server (hosted project library)",
        account_required=True,
        user_side_access="Blackmagic Cloud ID per collaborator (free account) AND a paid Cloud Project "
                         "Library hosted by one participant. This fee is USER-SIDE, not a platform cost.",
        official_sources=[SOURCE_BMD_COLLAB, SOURCE_BMD_FREE_PLAN],
        known_constraints=[
            "Official free plan includes only: 2 GB cloud storage, 20 presentations, and ACCESS to "
            "libraries others share with you. Creating/hosting a Cloud Project Library is a paid add-on, "
            "so vendor-hosted Resolve collaboration is NOT $0 for the host. The host pays, collaborators join free.",
            "Reported fee ~US$5/month per active library (secondary sources; Blackmagic does not publish "
            "it on the page we verified) -- treat as unconfirmed and check in the account before promising.",
            "Project library content (bins/timelines/grades) is hosted by Blackmagic in vendor mode.",
            "Media sync and proxy sync must stay DISABLED so originals are not uploaded.",
            "Blackmagic marketing states the free Resolve version includes multi-user collaboration, while "
            "some third-party guides claim Studio is required. Must be settled on the pilot's installed version.",
        ],
        unverified_claims=[
            "Which Resolve edition/version the pilot machines actually need for collaboration",
            "Whether our mount can serve media to a cloud-project Resolve session without media sync",
        ],
    ),
    "resolve.private_project_server": Adapter(
        key="resolve.private_project_server",
        nle="resolve",
        mode="private_controlled",
        authoritative_state="private_project_server",
        display_name="DaVinci Resolve Project Server (self-hosted PostgreSQL library)",
        account_required=False,
        user_side_access="DaVinci Resolve install per editor; Blackmagic lists the Project Server app as "
                         "a free download. A machine must host PostgreSQL and stay online.",
        official_sources=[SOURCE_BMD_COLLAB],
        known_constraints=[
            "Blackmagic documents the Project Server app as free; it runs on customer hardware.",
            "Historically restricted to the same local network; remote access needs a user-provided "
            "VPN or equivalent private path -- which the platform does not pay for.",
            "All-or-nothing library sharing: a shared library has no per-project access control inside it.",
            "Authoritative project state stays on the customer-controlled server (this is the private mode).",
        ],
        unverified_claims=[
            "Actual lock behaviour and conflict recovery across our remote mount topology",
            "Whether remote editors can reach a project server outside the LAN without a VPN",
            "Resolve version compatibility across mixed macOS/Windows editors",
        ],
    ),
}

BY_MODE = {
    ("premiere", "vendor_hosted"): "premiere.team_projects",
    ("premiere", "private_controlled"): "premiere.productions",
    ("resolve", "vendor_hosted"): "resolve.blackmagic_cloud_project_server",
    ("resolve", "private_controlled"): "resolve.private_project_server",
}

NL_NAMES = {"premiere": "Adobe Premiere Pro", "resolve": "DaVinci Resolve"}


def get_adapter(nle: str, mode: str) -> Adapter | None:
    key = BY_MODE.get((nle, mode))
    return ADAPTERS.get(key) if key else None


def capability_matrix() -> dict:
    return {
        "verification_notice": (
            "No NLE integration in this build has been executed against a real Premiere Pro or "
            "Resolve installation. Every entry is UNVERIFIED until docs/NLE_TEST_LOG.md records a "
            "passing real-world test. Do not describe these paths as working to a pilot team."
        ),
        "nle_access_is_user_side": True,
        "nle_access_note": (
            "Each pilot user/team must obtain and maintain eligible Premiere Pro and Resolve access, "
            "including any vendor collaboration entitlement. The platform does not buy, bundle, "
            "reimburse or resell those licences, and does not treat them as a platform-service cost."
        ),
        "adapters": [
            {
                "key": a.key,
                "nle": a.nle,
                "nle_name": NL_NAMES[a.nle],
                "mode": a.mode,
                "display_name": a.display_name,
                "authoritative_state": a.authoritative_state,
                "account_required": a.account_required,
                "user_side_access": a.user_side_access,
                "official_sources": a.official_sources,
                "verification_status": a.verification_status,
                "known_constraints": a.known_constraints,
                "unverified_claims": a.unverified_claims,
                "originals_stay_local": True,
                "media_sync_default": "disabled",
            }
            for a in ADAPTERS.values()
        ],
    }


def preflight(*, nle: str, mode: str, user_acknowledged_access: bool = False,
              consent_recorded: bool = False, private_server_configured: bool = False,
              media_sync_enabled: bool = False) -> dict:
    """Decide whether a project may be bound to this NLE path. Fails closed."""
    blockers: list[dict] = []
    warnings: list[dict] = []
    adapter = get_adapter(nle, mode)

    if adapter is None:
        return {
            "allowed": False,
            "adapter": None,
            "blockers": [{
                "code": "unsupported_combination",
                "message": f"No supported path for nle={nle!r} mode={mode!r}.",
                "user_action": "Choose premiere|resolve and vendor_hosted|private_controlled.",
            }],
            "warnings": [],
            "silent_fallback_forbidden": True,
        }

    if not user_acknowledged_access:
        blockers.append({
            "code": "nle_access_not_confirmed",
            "message": "Participant has not confirmed eligible access to this NLE.",
            "user_action": adapter.user_side_access,
        })

    if mode == "vendor_hosted" and not consent_recorded:
        blockers.append({
            "code": "vendor_consent_required",
            "message": "Vendor-hosted project state processes project metadata, locks and versions "
                       "on the vendor's servers. Informed consent is required before setup.",
            "user_action": "Record consent for vendor-hosted project state, or choose private mode.",
        })

    if mode == "private_controlled" and not private_server_configured:
        blockers.append({
            "code": "private_server_not_configured",
            "message": "Private mode needs a named customer-controlled server/storage for authoritative "
                       "NLE project state.",
            "user_action": "Configure the private project server or shared storage path first. "
                           "The platform will not fall back to a vendor service.",
        })

    if media_sync_enabled:
        blockers.append({
            "code": "media_sync_requires_separate_approval",
            "message": "Media/proxy synchronisation is a separate opt-in and must never be enabled "
                       "silently; it can move originals or proxies off the local drive.",
            "user_action": "Get explicit approval for media sync and record what leaves the host.",
        })

    if mode == "vendor_hosted" and nle == "resolve":
        warnings.append({
            "code": "vendor_hosted_resolve_is_not_free",
            "message": "The Blackmagic free plan does not include hosting a Cloud Project Library; "
                       "the library host must pay. This is a user-side NLE cost, not a platform cost, "
                       "but it must be disclosed before the pilot.",
            "user_action": "Confirm who hosts and pays for the library, or use private Project Server.",
        })

    warnings.append({
        "code": "integration_unverified",
        "message": adapter.verification_status,
        "user_action": "Record a real test in docs/NLE_TEST_LOG.md before claiming support.",
    })

    return {
        "allowed": not blockers,
        "adapter": adapter.key,
        "mode": mode,
        "nle": nle,
        "authoritative_state": adapter.authoritative_state,
        "account_required": adapter.account_required,
        "blockers": blockers,
        "warnings": warnings,
        "silent_fallback_forbidden": True,
        "originals_stay_local": True,
        "platform_stores_project_contents": False,
        "disclosure": _disclosure(adapter),
    }


def _disclosure(adapter: Adapter) -> dict:
    """Plain-language data-flow boundary for the project hub."""
    if adapter.mode == "vendor_hosted":
        nle_state = f"{adapter.display_name} — project metadata/locks/versions handled by the vendor."
    else:
        nle_state = (f"{adapter.display_name} — authoritative project state stays on "
                     "customer-controlled infrastructure.")
    return {
        "original_media": "Stays on the host user's local drive in both modes.",
        "nle_project_state": nle_state,
        "proxies_and_cache": "Host-generated, host-cached; not uploaded unless separately approved.",
        "catalogue_and_index": "Stored in the host's local SQLite catalogue (self-hosted).",
        "review_data": "Stored locally on the host control plane in this build.",
        "account_identity": ("Vendor account required for the NLE collaboration service."
                             if adapter.account_required else
                             "No vendor account required for the NLE project path."),
        "relay_traffic": "None in this build: no third-party relay is used or required.",
    }
