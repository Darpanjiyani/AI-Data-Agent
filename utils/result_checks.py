"""
Sanity checks on a query's result, run after the query succeeds.

A query can run without errors and still answer the wrong question. The most common
case: a question that asks for one number ("How many users...?") gets a query that
returns one row per group, for example a COUNT placed inside a GROUP BY, which returns
56 rows of "1" instead of one row saying 56.

The check is rule-based (no extra model call): if the question asks for a single value
and the result has more than one row, the query goes back to be rewritten.
"""

import re

# Questions that ask for one number
SINGLE_VALUE_START = re.compile(
    r"^\s*(how many|how much|what percentage|what share|what proportion|what fraction|"
    r"by what percentage|what(?: is| was|'s) the (total|average|mean|median|number|count|"
    r"percentage|share|sum|longest|shortest|highest|lowest|maximum|minimum)\b)",
    re.IGNORECASE,
)

# Words that ask for several values, a list or a breakdown
MANY_VALUES = re.compile(
    r"\b(each|every|per|by|broken down|break (it|that|this) down|breakdown|list|which|"
    r"top \d+|\d+ (busiest|most|least|highest|lowest)|monthly|yearly|daily|weekly|quarterly|"
    r"for all|group(ed)?)\b",
    re.IGNORECASE,
)


def expects_single_value(question: str) -> bool:
    """True when the question asks for one number, e.g. "How many riders joined in 2024?"."""
    question = question.strip()
    if not SINGLE_VALUE_START.search(question):
        return False
    # "By what percentage did ... grow" starts with "by" but still asks for one number
    rest = re.sub(r"^\s*by what percentage", "", question, flags=re.IGNORECASE)
    return not MANY_VALUES.search(rest)


def result_issue(question: str, rows: list) -> str:
    """
    Describe what looks wrong with a successful result, or return "" if it looks fine.
    The description is shown to the model when it rewrites the query.
    """
    if expects_single_value(question) and len(rows) > 1:
        return (f"The question asks for a single value, but the query returned {len(rows)} rows. "
                "This usually means an aggregate is computed per group (for example COUNT inside "
                "a GROUP BY) instead of over the whole set. Rewrite it to return one row, for "
                "example by counting the groups in an outer query.")
    return ""
