from typing import Iterable

from yogurt_contracts.foundation.identifiers import OperationId
from yogurt_contracts.presentation.projection import PresentationProjection, project_presentation


def run_surface_conformance(operation_id: OperationId, facts: Iterable[object]) -> PresentationProjection:
    return project_presentation(operation_id, facts)
