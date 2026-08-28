from typing import Tuple, Union

from pydantic import field_validator

from ..foundation.identifiers import InputId
from ..foundation.model import FrozenContract
from .refs import TurnOrigin


class TextInputPart(FrozenContract):
    text: str


class PerceptionReferencePart(FrozenContract):
    perception_reference: str


class MediaReferencePart(FrozenContract):
    media_reference: str


class ToolResultInputPart(FrozenContract):
    tool_result_reference: str


InputPart = Union[TextInputPart, PerceptionReferencePart, MediaReferencePart, ToolResultInputPart]


class TurnInput(FrozenContract):
    input_id: InputId
    parts: Tuple[InputPart, ...]
    origin: TurnOrigin

    @field_validator("parts")
    @classmethod
    def has_parts(cls, value: Tuple[InputPart, ...]) -> Tuple[InputPart, ...]:
        if not value:
            raise ValueError("TurnInput requires at least one normalized input part")
        return value
