from __future__ import annotations

from tabulate import tabulate


class ScoreTracker:
    """Tracks point additions/deductions for signal entry scoring."""

    def __init__(self, base_score: float = 10.0, min_score: float = 8.0, enabled: bool = False):
        self.total = base_score
        self.min_score = min_score
        self.enabled = enabled
        self.logs: list[tuple[str, float, str]] = [("Base Score", base_score, "Starting value")]
        self.gates: list[tuple[str, bool, str]] = []

    def add(self, category: str, points: float, reason: str) -> None:
        self.total += points
        self.logs.append((category, points, reason))

    def add_gate(self, name: str, passed: bool, detail: str) -> None:
        self.gates.append((name, passed, detail))

    def passed_scoring(self) -> bool:
        return self.total > self.min_score

    def passed_all_gates(self) -> bool:
        return all(g[1] for g in self.gates)

    def get_score(self, category: str) -> float | None:
        """Return the sum of points for a specific category."""
        matches = [pts for cat, pts, _ in self.logs if cat == category]
        return sum(matches) if matches else None

    def print_table(self) -> None:
        if not self.enabled:
            return

        if self.gates:
            print("\n[ENTRY GATES]")
            gate_headers = ["Gate", "Status", "Detail"]
            gate_data = []
            for name, passed, detail in self.gates:
                status = "PASS" if passed else "FAIL"
                gate_data.append([name, status, detail])
            print(tabulate(gate_data, headers=gate_headers, tablefmt="grid"))

        print("\n[SCORING TELEMETRY]")
        headers = ["Category", "Points", "Reason"]
        table_data = []
        for cat, pts, reason in self.logs:
            table_data.append([cat, f"{pts:+.1f}", reason])
        
        status = "PASSED" if self.passed_scoring() else "FAILED"
        table_data.append(["TOTAL SCORE", f"{self.total:+.1f}", f"{status} (Minimum required: > {self.min_score})"])

        print(tabulate(table_data, headers=headers, tablefmt="grid"))
        print()
