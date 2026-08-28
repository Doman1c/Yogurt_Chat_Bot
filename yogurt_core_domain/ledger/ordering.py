from typing import Tuple


def append_fact(facts: Tuple[object, ...], fact: object) -> Tuple[object, ...]:
    return facts + (fact,)
