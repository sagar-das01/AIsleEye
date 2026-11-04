from collections.abc import Iterable, Mapping
from dataclasses import dataclass

ALLOWED_TOOLS = ("create_incident_ticket", "adjust_inventory", "log_compliance_event")
# Acknowledged work is still active: a second ticket for the same finding would duplicate it.
ACTIVE_STATUSES = frozenset({"open", "in_progress"})


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""
    duplicate_of: str | None = None


def _reject(reason: str, duplicate_of: str | None = None) -> Verdict:
    return Verdict(False, reason[:280], duplicate_of)


class Guard:
    """Policy between the agent and the tools. Model output is untrusted; every call is judged here first."""

    def __init__(
        self,
        scan_id: str,
        approved_ids: Iterable[str],
        units_by_finding: Mapping[str, int],
        stock: Mapping[str, int],
        open_tickets: Iterable[Mapping] = (),
        max_tool_calls: int = 8,
    ):
        self.scan_id = scan_id
        self.max_tool_calls = max_tool_calls
        self._approved = set(approved_ids)
        self._units = dict(units_by_finding)
        self._stock = dict(stock)
        self._open_tickets = [dict(t) for t in open_tickets]
        self._calls = 0
        self._ticketed: set[str] = set()
        self._moved_by_finding: dict[str, int] = {}
        self._moved_by_sku: dict[str, int] = {}

    def tick(self) -> None:
        self._calls += 1

    def evaluate(self, tool: str, args: Mapping) -> Verdict:
        self.tick()
        if self._calls > self.max_tool_calls:
            return _reject(f"Tool call cap of {self.max_tool_calls} per scan reached.")
        if tool not in ALLOWED_TOOLS:
            return _reject(f"Tool {tool[:60]!r} is not allowed. Allowed tools: {', '.join(ALLOWED_TOOLS)}.")

        finding_id = args.get("finding_id") if isinstance(args, Mapping) else None
        if not isinstance(finding_id, str) or not finding_id:
            return _reject("finding_id is missing or invalid.")
        if finding_id not in self._approved:
            return _reject(f"Finding {finding_id[:60]!r} was not approved by the supervisor.")

        if tool == "create_incident_ticket":
            return self._ticket(finding_id)
        if tool == "adjust_inventory":
            return self._inventory(finding_id, args)
        return Verdict(True)

    def forget(self, tool: str, args: Mapping) -> None:
        """Release what an allowed call reserved when the tool then failed, so a corrected retry is not blocked."""
        finding_id = args.get("finding_id")
        if tool == "create_incident_ticket":
            self._ticketed.discard(finding_id)
        elif tool == "adjust_inventory":
            delta, sku = args.get("delta", 0), args.get("sku")
            self._moved_by_finding[finding_id] = max(0, self._moved_by_finding.get(finding_id, 0) - delta)
            self._moved_by_sku[sku] = max(0, self._moved_by_sku.get(sku, 0) - delta)

    def _ticket(self, finding_id: str) -> Verdict:
        if finding_id in self._ticketed:
            return _reject(f"A ticket for {finding_id} was already created in this run.")
        for ticket in self._open_tickets:
            same_scan = ticket.get("scan_id", self.scan_id) == self.scan_id
            if same_scan and ticket.get("finding_id") == finding_id and ticket.get("status", "open") in ACTIVE_STATUSES:
                return _reject(f"Duplicate of active ticket {ticket.get('ticket_id')}.", duplicate_of=ticket.get("ticket_id"))
        self._ticketed.add(finding_id)
        return Verdict(True)

    def _inventory(self, finding_id: str, args: Mapping) -> Verdict:
        delta, sku = args.get("delta"), args.get("sku")
        if isinstance(delta, bool) or not isinstance(delta, int) or delta <= 0:
            return _reject("delta must be a positive integer.")
        if not isinstance(sku, str) or sku not in self._stock:
            return _reject(f"Unknown SKU {str(sku)[:40]!r}.")
        units = self._units.get(finding_id, 0)
        moved = self._moved_by_finding.get(finding_id, 0)
        if moved + delta > units:
            return _reject(f"Quantity exceeds the {units} unit(s) detected for {finding_id}.")
        available = self._stock[sku] - self._moved_by_sku.get(sku, 0)
        if delta > available:
            return _reject(f"Stock for {sku} would go below zero ({available} available).")
        self._moved_by_finding[finding_id] = moved + delta
        self._moved_by_sku[sku] = self._moved_by_sku.get(sku, 0) + delta
        return Verdict(True)
