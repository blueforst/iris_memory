"""Contract package identity and supported capability surface."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContractPackage:
    """Public package identity used for compatibility negotiation."""

    name: str
    version: str
    major_version: int
    schemas: tuple[str, ...]


CONTRACT_PACKAGE = ContractPackage(
    name="iris-memory-contracts",
    version="0.2.0",
    major_version=0,
    schemas=(
        "acceptance-receipt-v1.schema.json",
        "capability-handshake-v2.schema.json",
        "context-range-v1.schema.json",
        "duplicate-replay-receipt-v1.schema.json",
        "evidence-basis-ref-v1.schema.json",
        "expansion-request-v1.schema.json",
        "expansion-response-v1.schema.json",
        "health-response-v1.schema.json",
        "historian-publication-v1.schema.json",
        "historian-publication-v2.schema.json",
        "idempotency-conflict-error-v1.schema.json",
        "memory-recall-card-v1.schema.json",
        "not-implemented-error-v1.schema.json",
        "publication-acceptance-request-v1.schema.json",
        "publication-acceptance-request-v2.schema.json",
        "raw-archive-ref-v1.schema.json",
        "recall-request-v1.schema.json",
        "semantic-derivation-refs-v1.schema.json",
        "sequence-conflict-error-v1.schema.json",
        "unsupported-version-error-v1.schema.json",
    ),
)
