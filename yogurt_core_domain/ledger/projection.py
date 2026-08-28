from typing import Iterable, Tuple


def facts_by_type(facts: Iterable[object], fact_type: type) -> Tuple[object, ...]:
    return tuple(fact for fact in facts if isinstance(fact, fact_type))
