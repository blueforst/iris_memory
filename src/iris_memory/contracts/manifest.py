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
    version="0.1.0",
    major_version=0,
    schemas=(
        "capability-handshake-v1.schema.json",
        "historian-publication-v1.schema.json",
        "publication-acceptance-request-v1.schema.json",
        "acceptance-receipt-v1.schema.json",
        "duplicate-replay-receipt-v1.schema.json",
        "idempotency-conflict-error-v1.schema.json",
        "unsupported-version-error-v1.schema.json",
        "health-response-v1.schema.json",
        "recall-request-v1.schema.json",
        "memory-recall-card-v1.schema.json",
        "expansion-request-v1.schema.json",
        "expansion-response-v1.schema.json",
        "sequence-conflict-error-v1.schema.json",
    ),
)
