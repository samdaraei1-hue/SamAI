"""Pluggable, deliberately offline-by-default factual verification interface."""

from dataclasses import dataclass


@dataclass
class FactCheck:
    verified: bool
    summary: str
    sources: list[str]


class FactChecker:
    def check(self, claim: str) -> FactCheck:
        # No search provider is configured. Never invent sources or claim verification.
        return FactCheck(False, "No authoritative external verification provider is configured.", [])


fact_checker = FactChecker()
