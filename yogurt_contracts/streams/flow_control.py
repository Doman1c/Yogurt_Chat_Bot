from enum import Enum


class OrderingSemantics(str, Enum): ORDERED = "ordered"; LATEST_ONLY = "latest_only"
class OverflowSemantics(str, Enum): BLOCK_PRODUCER = "block_producer"; LATEST_WINS = "latest_wins"; SUPERSEDE_PRIOR = "supersede_prior"
class SupersessionSemantics(str, Enum): NONE = "none"; SUPERSEDE_PRIOR = "supersede_prior"
class CancellationSemantics(str, Enum): CANCELLABLE = "cancellable"; CANCEL_OLD_STREAM = "cancel_old_stream"; NOT_CANCELLABLE = "not_cancellable"
class CapacityUnit(str, Enum): ITEMS = "items"; DURATION = "duration"; BYTES = "bytes"
