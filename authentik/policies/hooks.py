"""Extension hooks for the policy engine.

Lets other apps augment a :class:`~authentik.policies.types.PolicyRequest` before evaluation
without the core engine importing app-specific code. This keeps the upstream policy hot path
free of fork-specific imports (smaller merge surface) and isolates extension failures so a
broken processor can never break policy evaluation for the whole product.
"""

from collections.abc import Callable

from structlog.stdlib import get_logger

from authentik.policies.types import PolicyRequest

LOGGER = get_logger()

PolicyRequestProcessor = Callable[[PolicyRequest], None]

_PROCESSORS: list[PolicyRequestProcessor] = []


def register_policy_request_processor(processor: PolicyRequestProcessor) -> None:
    """Register a callable invoked for every PolicyRequest just before evaluation.

    Processors receive the :class:`PolicyRequest` and may mutate ``request.context``.
    Registration is idempotent.
    """
    if processor not in _PROCESSORS:
        _PROCESSORS.append(processor)


def unregister_policy_request_processor(processor: PolicyRequestProcessor) -> None:
    """Remove a previously registered processor (primarily for tests)."""
    if processor in _PROCESSORS:
        _PROCESSORS.remove(processor)


def apply_policy_request_processors(request: PolicyRequest) -> None:
    """Run all registered processors, isolating failures from policy evaluation."""
    for processor in _PROCESSORS:
        try:
            processor(request)
        except Exception as exc:  # noqa: BLE001 - never break policy eval on an extension error
            LOGGER.warning("policy request processor failed", exc=exc, processor=processor)
