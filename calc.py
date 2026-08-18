from __future__ import annotations
from datetime import date, datetime, timedelta, time
from calendar import monthrange

WEEKDAY_NAMES = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


def hm(minutes: int | float | None) -> str:
    if minutes is None:
        return "—"
    sign = "-" if minutes < 0 else ""
    minutes = abs(int(round(minutes)))
    h, m = divmod(minutes, 60)
    return f"{sign}{h:02d}:{m:02d}"


def _minutes_between(start: str | None, end: str | None) -> int:
    if not start or not end:
        return 0
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    s = sh * 60 + sm
    e = eh * 60 + em
    if e < s:
        e += 24 * 60
    return e - s


def derive_expected_minutes(start1, end1, start2, end2, fallback=0):
    total = _minutes_between(start1, end1) + _minutes_between(start2, end2)
    return total if total > 0 else int(fallback or 0)


def pair_punches(datetimes: list[datetime]):
    points = sorted(datetimes)
    pairs = []
    for i in range(0, len(points) - 1, 2):
        pairs.append((points[i], points[i + 1]))
    incomplete = len(points) % 2 == 1
    return pairs, incomplete


def worked_minutes(pairs):
    total = 0
    for start, end in pairs:
        diff = int((end - start).total_seconds() // 60)
        if 0 <= diff <= 24 * 60:
            total += diff
    return total


def interval_minutes(pairs):
    if len(pairs) < 2:
        return None
    gaps = []
    for i in range(len(pairs) - 1):
        gap = int((pairs[i + 1][0] - pairs[i][1]).total_seconds() // 60)
        if gap >= 0:
            gaps.append(gap)
    return min(gaps) if gaps else None


def _daily_overtime_limit(settings: dict) -> int | None:
    raw = settings.get("overtime_daily_limit_minutes")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return max(0, value)


def _saturday_standard_minutes(settings: dict) -> int | None:
    raw = settings.get("saturday_standard_minutes")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return max(0, value)


def _expected_punch_count(day: date, schedule, is_workday: bool, saturday_standard: int | None) -> int:
    """Quantidade mínima de marcações esperada para identificar esquecimento."""
    if not is_workday or schedule is None:
        return 0
    if day.weekday() == 5 and saturday_standard is not None:
        return 2

    count = 0
    if schedule["start1"] and schedule["end1"]:
        count += 2
    if schedule["start2"] and schedule["end2"]:
        count += 2
    return count


def analyze_day(day: date, punches: list[datetime], schedule, settings: dict, holidays: set[str]):
    pairs, odd_punch_count = pair_punches(punches)
    worked = worked_minutes(pairs)
    min_interval = interval_minutes(pairs)

    schedule_configured = schedule is not None
    saturday_standard = _saturday_standard_minutes(settings)

    if day.weekday() == 5 and saturday_standard is not None and schedule_configured:
        is_workday = True
        expected = saturday_standard
    else:
        is_workday = bool(schedule and int(schedule["is_workday"]))
        expected = int(schedule["expected_minutes"]) if schedule and is_workday else 0

    holiday = settings.get("consider_holidays", "1") == "1" and day.isoformat() in holidays
    expected_punches = _expected_punch_count(day, schedule, is_workday, saturday_standard)

    # Falta só existe em dia efetivamente previsto de trabalho. Feriado sem
    # marcação não é contado como falta; se houver trabalho no feriado, ele é
    # tratado pelas regras de HE 100%.
    absence = bool(is_workday and not holiday and not punches)
    missing_required_punches = bool(
        punches
        and expected_punches > 0
        and len(punches) < expected_punches
    )
    forgotten_punch = bool(
        punches
        and (odd_punch_count or missing_required_punches)
    )
    incomplete = forgotten_punch

    tolerance = int(settings.get("daily_tolerance_minutes", 0) or 0)
    daily_limit = _daily_overtime_limit(settings)

    if schedule_configured:
        raw_delta = worked - expected if is_workday else worked
        delta = 0 if abs(raw_delta) <= tolerance else raw_delta
    else:
        raw_delta = 0
        delta = 0

    overtime_weekday = 0
    overtime_saturday = 0
    overtime_sunday_holiday = 0
    overtime_excess_100 = 0
    shortage = 0
    bank = 0
    review_required = incomplete or absence or not schedule_configured

    if not review_required:
        if delta > 0:
            if daily_limit is None:
                if settings.get("bank_hours_enabled") == "1":
                    bank = delta
                elif holiday or day.weekday() == 6:
                    overtime_sunday_holiday = delta
                elif day.weekday() == 5:
                    overtime_saturday = delta
                else:
                    overtime_weekday = delta
            else:
                standard_part = min(delta, daily_limit)
                overtime_excess_100 = max(0, delta - daily_limit)

                if settings.get("bank_hours_enabled") == "1":
                    bank = standard_part
                    overtime_sunday_holiday = overtime_excess_100
                elif holiday or day.weekday() == 6:
                    overtime_sunday_holiday = delta
                else:
                    overtime_weekday = standard_part
                    overtime_sunday_holiday = overtime_excess_100
        elif delta < 0:
            if settings.get("bank_hours_enabled") == "1":
                bank = delta
            else:
                shortage = abs(delta)

    status = []
    if not schedule_configured:
        status.append("Jornada não configurada")
    if absence:
        status.append("Falta")
    elif incomplete:
        status.append("Batida incompleta")
    if settings.get("min_interval_minutes"):
        req = int(settings.get("min_interval_minutes", 0) or 0)
        if min_interval is not None and req > 0 and min_interval < req:
            status.append(f"Intervalo {min_interval} min")
    if not status:
        status.append("OK")

    return {
        "date": day,
        "weekday": WEEKDAY_NAMES[day.weekday()],
        "punches": punches,
        "pairs": pairs,
        "worked": worked,
        "expected": expected,
        "delta": delta,
        "overtime_weekday": overtime_weekday,
        "overtime_saturday": overtime_saturday,
        "overtime_sunday_holiday": overtime_sunday_holiday,
        "overtime_excess_100": overtime_excess_100,
        "shortage": shortage,
        "bank": bank,
        "review_required": review_required,
        "min_interval": min_interval,
        "incomplete": incomplete,
        "forgotten_punch": forgotten_punch,
        "absence": absence,
        "expected_punches": expected_punches,
        "is_workday": is_workday,
        "holiday": holiday,
        "status": ", ".join(status),
    }


def month_dates(year: int, month: int):
    last = monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, last + 1)]
