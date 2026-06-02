"""
Central session-reportability filter.

Usage::

    where.append(reportable_session_where_clause())
    # or with an alias:
    where.append(reportable_session_where_clause("s"))

The COALESCE guards ensure historical rows without the new columns are treated
as fully reportable — i.e. excluded_from_reports defaults to 0 and
vehicle_assignment_status defaults to 'confirmed'.
"""


def reportable_session_where_clause(alias: str | None = None) -> str:
    """Return an SQL fragment (starts with AND) that filters out non-reportable sessions.

    Rows without the new columns (old installs) are treated as confirmed/reportable.
    """
    p = f"{alias}." if alias else ""
    return (
        f" AND COALESCE({p}excluded_from_reports, 0) = 0"
        f" AND COALESCE({p}vehicle_assignment_status, 'confirmed')"
        f" NOT IN ('unassigned','foreign_vehicle','ignored')"
    )
