"""
Report service — re-exports from server.py.
Provides report period calculation and session/record helpers.
"""
from __future__ import annotations


def calculate_report_periods(schedule_type, period_mode, now, config):
    from server import calculate_report_periods as _f
    return _f(schedule_type, period_mode, now, config)


def _get_report_sessions(start_date, end_date, location_filter="all", vehicle_filter="all"):
    from server import _get_report_sessions as _f
    return _f(start_date, end_date, location_filter, vehicle_filter)


def _save_report_record(vehicle_id, period_info, loc_filter, veh_filter, status,
                        created_by, excel_bytes=None, pdf_bytes=None, summary=None):
    from server import _save_report_record as _f
    return _f(vehicle_id, period_info, loc_filter, veh_filter, status,
              created_by, excel_bytes, pdf_bytes, summary)
