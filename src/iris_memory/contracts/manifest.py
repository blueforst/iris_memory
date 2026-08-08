"""Contract package identity and supported capability surface."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContractPackage:
    """Public package identity used for compatibility negotiation."""

    name: str
    version: str
    major_version: int
    schemas: tuple[str, ...]
    status: str = "candidate_v1"


CONTRACT_PACKAGE = ContractPackage(
    name="iris-memory-contracts",
    version="0.3.0",
    major_version=0,
    status="candidate_v3",
    schemas=(
        "acceptance-receipt-v1.schema.json",
        "acceptance-receipt-v3.schema.json",
        "capability-handshake-v2.schema.json",
        "compartment-revision-v1.schema.json",
        "context-range-v1.schema.json",
        "duplicate-replay-receipt-v1.schema.json",
        "duplicate-replay-receipt-v2.schema.json",
        "evidence-basis-ref-v1.schema.json",
        "expansion-request-v1.schema.json",
        "expansion-response-v1.schema.json",
        "graphiti-episode-source-v1.schema.json",
        "health-response-v1.schema.json",
        "historian-publication-v1.schema.json",
        "historian-publication-v2.schema.json",
        "historian-publication-v3.schema.json",
        "idempotency-conflict-error-v1.schema.json",
        "memory-recall-card-v1.schema.json",
        "not-implemented-error-v1.schema.json",
        "publication-acceptance-request-v1.schema.json",
        "publication-acceptance-request-v2.schema.json",
        "publication-acceptance-request-v3.schema.json",
        "raw-archive-ref-v1.schema.json",
        "recall-request-v1.schema.json",
        "semantic-derivation-refs-v1.schema.json",
        "sequence-conflict-error-v1.schema.json",
        "unsupported-version-error-v1.schema.json",
    ),
)
