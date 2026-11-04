from app.schemas import Finding, ScanResult


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def plan(scan: ScanResult, approved: list[str]) -> list[tuple[str, dict]]:
    """Deterministic dispatch for approved findings: the same routing the agent prompt describes, with no model.

    Used for offline replay and as the fallback when the model API is unavailable.
    """
    wanted = set(approved)
    calls: list[tuple[str, dict]] = []
    for finding in scan.findings:
        if finding.id not in wanted:
            continue
        if finding.type == "walkway_blocked":
            calls += _walkway(finding)
        else:
            calls += _damage(finding)
    return calls


def _walkway(f: Finding) -> list[tuple[str, dict]]:
    return [
        ("create_incident_ticket", dict(finding_id=f.id, title="Walkway blocked by freight", severity=f.severity,
                                        assignee_team="Safety", description=_short(f.description, 400))),
        ("log_compliance_event", dict(finding_id=f.id, category="walkway_obstruction", location="Marked walkway",
                                      severity=f.severity, note=_short(f.description, 400))),
    ]


def _damage(f: Finding) -> list[tuple[str, dict]]:
    calls = [
        ("create_incident_ticket", dict(finding_id=f.id, title="Damaged box needs inspection", severity=f.severity,
                                        assignee_team="Maintenance", description=_short(f.description, 400))),
    ]
    if f.sku:
        calls.append(("adjust_inventory", dict(finding_id=f.id, sku=f.sku, delta=1, reason="Damaged box quarantined")))
    return calls
