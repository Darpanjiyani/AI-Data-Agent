"""
Pick a sensible chart for a query result, or none.

The rules are simple on purpose:
- one row with a few numbers         -> headline numbers (metrics)
- a label column + a number column   -> bar chart (in the query's order)
- a time column + a number column    -> line chart
- anything else                      -> just the table
"""

import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

TIME_NAME = re.compile(r"(^|_)(year|month|day|date|hour|quarter|week|time|at)($|_)", re.I)
ID_NAME = re.compile(r"(^id$|_id$)", re.I)
# Which number to chart first when a result has several (lower = first)
MEASURE_PRIORITY = [
    re.compile(r"pct|percent|rate|share|ratio", re.I),
    re.compile(r"avg|average|mean|median", re.I),
]

MAX_CHART_ROWS = 40
MAX_METRICS = 4


@dataclass
class ChartPlan:
    kind: str                           # "metrics", "bar" or "line"
    x: str = ""                         # label / time column (bar, line)
    measures: list = field(default_factory=list)   # numeric columns that can be plotted
    data: Optional[pd.DataFrame] = None             # data prepared for plotting


def to_dataframe(rows: list) -> pd.DataFrame:
    """Rows from the database come as JSON, so decimals and dates arrive as text: convert them."""
    df = pd.DataFrame(rows)
    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            numbers = pd.to_numeric(df[col], errors="coerce")
            if df[col].notna().any() and numbers.notna().sum() == df[col].notna().sum():
                df[col] = numbers
                continue
            looks_like_date = df[col].dropna().astype(str).str.match(r"^\d{4}-\d{2}-\d{2}").all()
            if df[col].notna().any() and looks_like_date:
                dates = pd.to_datetime(df[col], errors="coerce", format="mixed")
                if dates.notna().sum() == df[col].notna().sum():
                    df[col] = dates
    return df


def _is_time(df: pd.DataFrame, col: str) -> bool:
    return pd.api.types.is_datetime64_any_dtype(df[col]) or bool(TIME_NAME.search(col))


def _is_label(df: pd.DataFrame, col: str) -> bool:
    """Columns that name things rather than measure them (text, ids, years, months...)."""
    if not pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_bool_dtype(df[col]):
        return True
    return bool(ID_NAME.search(col)) or _is_time(df, col)


def plan_chart(rows: Optional[list]) -> Optional[ChartPlan]:
    if not rows:
        return None
    df = to_dataframe(rows)
    labels = [c for c in df.columns if _is_label(df, c)]
    measures = [c for c in df.columns if c not in labels]

    # All numeric, e.g. "rating | count": treat the first column as the group key
    # when it has whole, unique values (queries usually list the group column first).
    first = df.columns[0]
    if not labels and len(df) > 1 and len(df.columns) >= 2 and df[first].is_unique \
            and (df[first] % 1 == 0).all():
        labels, measures = [first], measures[1:]
    if not measures:
        return None

    # One row: show the numbers as headline metrics
    if len(df) == 1:
        if len(df.columns) <= MAX_METRICS:
            return ChartPlan(kind="metrics", measures=measures, data=df)
        return None

    if len(df) > MAX_CHART_ROWS or not 1 <= len(labels) <= 2:
        return None

    # Most useful measure first (e.g. a percentage before the raw counts)
    def priority(column: str) -> int:
        for rank, pattern in enumerate(MEASURE_PRIORITY):
            if pattern.search(column):
                return rank
        return len(MEASURE_PRIORITY)

    measures.sort(key=priority)  # stable: otherwise keeps the query's column order

    data = df.copy()
    if len(labels) == 2:
        # Two label columns only make one axis when both are time parts (year + month
        # -> "2025-07"). Anything else (e.g. ride_id + status) is a list of records.
        a, b = labels
        if not (_is_time(df, a) and _is_time(df, b)):
            return None
        x = f"{a}-{b}"
        data[x] = data[a].astype(str) + "-" + data[b].astype(str).str.zfill(2)
        is_time = True
    else:
        x = labels[0]
        is_time = _is_time(df, x)

    if not data[x].is_unique:   # repeated labels: a chart would merge rows
        return None

    kind = "line" if is_time and len(df) >= 4 else "bar"
    if kind == "bar":
        data[x] = data[x].astype(str)
    return ChartPlan(kind=kind, x=x, measures=measures, data=data)


def pretty(name: str) -> str:
    """'avg_fare_per_km' -> 'Avg fare per km'"""
    text = str(name).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def build_chart(plan: ChartPlan, measure: str, color: str):
    """Altair chart for a bar or line plan: one series, one axis, tooltips on every mark."""
    import altair as alt

    data = plan.data
    x_title, y_title = pretty(plan.x), pretty(measure)
    number_format = ",.2f" if (data[measure] % 1 != 0).any() else ",.0f"
    tooltip = [alt.Tooltip(f"{plan.x}:N" if plan.kind == "bar" else plan.x, title=x_title),
               alt.Tooltip(f"{measure}:Q", title=y_title, format=number_format)]

    if plan.kind == "line":
        x_type = "T" if pd.api.types.is_datetime64_any_dtype(data[plan.x]) else (
            "Q" if pd.api.types.is_numeric_dtype(data[plan.x]) else "O")
        base = alt.Chart(data).encode(
            x=alt.X(f"{plan.x}:{x_type}", title=x_title, sort=None),
            y=alt.Y(f"{measure}:Q", title=y_title, axis=alt.Axis(format=",.2~f")),
            tooltip=tooltip,
        )
        chart = base.mark_line(color=color, strokeWidth=2) + base.mark_point(
            color=color, filled=True, size=64)
        return chart.properties(height=320)

    # Horizontal bars keep long category names readable; sort=None keeps the SQL's order
    bar_size = 20
    chart = alt.Chart(data).mark_bar(color=color, size=bar_size, cornerRadiusEnd=4).encode(
        y=alt.Y(f"{plan.x}:N", title=x_title, sort=None),
        x=alt.X(f"{measure}:Q", title=y_title, axis=alt.Axis(format=",.2~f")),
        tooltip=tooltip,
    )
    return chart.properties(height=alt.Step(32))  # one 32px band per bar
