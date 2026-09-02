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


def _scheduled_marking_datetimes(day: date, schedule, expected_minutes: int) -> list[datetime]:
    """Monta as marcações contratuais do dia quando a jornada é inequívoca.

    A tolerância legal só é aplicada quando os horários cadastrados representam
    exatamente a carga normal usada na apuração. Isso evita, por exemplo,
    normalizar um sábado de 4h contra um cadastro antigo de 8h.
    """
    if schedule is None:
        return []

    start1 = schedule["start1"]
    end1 = schedule["end1"]
    start2 = schedule["start2"]
    end2 = schedule["end2"]

    if bool(start1) != bool(end1) or bool(start2) != bool(end2):
        return []

    values: list[str] = []
    if start1 and end1:
        values.extend([start1, end1])
    if start2 and end2:
        values.extend([start2, end2])
    if not values:
        return []

    scheduled_minutes = derive_expected_minutes(start1, end1, start2, end2, 0)
    if scheduled_minutes != int(expected_minutes or 0):
        return []

    result: list[datetime] = []
    day_offset = 0
    previous_clock = None
    for value in values:
        try:
            hour, minute = map(int, str(value).split(":"))
        except (TypeError, ValueError):
            return []
        clock = hour * 60 + minute
        if previous_clock is not None and clock < previous_clock:
            day_offset += 1
        result.append(
            datetime.combine(day, time(hour=hour, minute=minute))
            + timedelta(days=day_offset)
        )
        previous_clock = clock
    return result


def _match_timezone(actual: datetime, planned: datetime) -> datetime:
    """Alinha o horário contratual ao tipo de timezone da marcação real.

    O AFD do Control iD é armazenado com offset (ex.: -03:00), enquanto os
    horários de jornada são horários civis sem timezone. Para comparar os dois
    sem alterar o instante registrado no AFD, o horário contratual recebe o
    mesmo tzinfo da marcação correspondente. Se a marcação for naive, o horário
    contratual permanece naive.
    """
    actual_aware = actual.tzinfo is not None and actual.utcoffset() is not None
    planned_aware = planned.tzinfo is not None and planned.utcoffset() is not None

    if actual_aware and not planned_aware:
        return planned.replace(tzinfo=actual.tzinfo)
    if not actual_aware and planned_aware:
        return planned.replace(tzinfo=None)
    return planned


def _apply_clt_marking_tolerance(
    day: date,
    punches: list[datetime],
    schedule,
    expected_minutes: int,
    is_workday: bool,
    holiday: bool,
    settings: dict,
):
    """Aplica a tolerância do art. 58, §1º, da CLT sobre as marcações.

    Regra conservadora adotada:
    - cada variação deve ser de no máximo 5 min (configurável internamente);
    - a soma absoluta das variações do dia deve ser de no máximo 10 min;
    - satisfeitos ambos os limites, as marcações são normalizadas para os
      horários contratuais somente para o cálculo;
    - ultrapassado qualquer limite, as marcações reais são usadas integralmente.

    As marcações originais permanecem preservadas e continuam sendo exibidas.
    """
    if settings.get("tolerance_rule_version") != "clt_marking_v1":
        return punches, False, 0, []
    if not is_workday or holiday or day.weekday() == 6 or not punches:
        return punches, False, 0, []

    expected_points = _scheduled_marking_datetimes(day, schedule, expected_minutes)
    if not expected_points or len(punches) != len(expected_points):
        return punches, False, 0, []

    try:
        per_mark_limit = max(0, int(settings.get("marking_tolerance_minutes", 5) or 5))
        daily_limit = max(0, int(settings.get("daily_marking_tolerance_minutes", 10) or 10))
    except (TypeError, ValueError):
        per_mark_limit = 5
        daily_limit = 10

    ordered = sorted(punches)
    aligned_expected_points = [
        _match_timezone(actual, planned)
        for actual, planned in zip(ordered, expected_points)
    ]
    variations = [
        int((actual - planned).total_seconds() / 60)
        for actual, planned in zip(ordered, aligned_expected_points)
    ]
    total_variation = sum(abs(value) for value in variations)
    within_limits = (
        all(abs(value) <= per_mark_limit for value in variations)
        and total_variation <= daily_limit
    )

    if within_limits:
        return aligned_expected_points, any(variations), total_variation, variations
    return punches, False, total_variation, variations


def analyze_day(day: date, punches: list[datetime], schedule, settings: dict, holidays: set[str]):
    # As batidas originais são sempre preservadas. Pares/intervalo abaixo são
    # usados para conferência operacional e nunca substituem o AFD.
    raw_pairs, odd_punch_count = pair_punches(punches)
    raw_worked = worked_minutes(raw_pairs)
    min_interval = interval_minutes(raw_pairs)

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

    effective_punches, tolerance_applied, tolerance_variation_minutes, tolerance_variations = (
        _apply_clt_marking_tolerance(
            day,
            punches,
            schedule,
            expected,
            is_workday,
            holiday,
            settings,
        )
    )
    calculation_pairs, _ = pair_punches(effective_punches)
    worked = worked_minutes(calculation_pairs)

    daily_limit = _daily_overtime_limit(settings)

    if schedule_configured:
        raw_delta = worked - expected if is_workday else worked
        # Competências fechadas antes da adoção da regra CLT por marcação não
        # possuem tolerance_rule_version no snapshot. Para elas preservamos o
        # cálculo legado, evitando alteração retroativa de mês finalizado.
        if settings.get("tolerance_rule_version") == "clt_marking_v1":
            delta = raw_delta
        else:
            legacy_tolerance = int(settings.get("daily_tolerance_minutes", 0) or 0)
            delta = 0 if abs(raw_delta) <= legacy_tolerance else raw_delta
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
        "pairs": raw_pairs,
        "calculation_pairs": calculation_pairs,
        "raw_worked": raw_worked,
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
        "tolerance_applied": tolerance_applied,
        "tolerance_variation_minutes": tolerance_variation_minutes,
        "tolerance_variations": tolerance_variations,
        "status": ", ".join(status),
    }


def month_dates(year: int, month: int):
    last = monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, last + 1)]
