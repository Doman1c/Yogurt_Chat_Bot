from enum import Enum


class PresentationTarget(str, Enum):
    VOICE = "voice"
    MESSAGING = "messaging"
    EMBODIMENT = "embodiment"
