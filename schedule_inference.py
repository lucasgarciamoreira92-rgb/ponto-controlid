from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _round_five(minutes: float) -> int:
    rounded = int(round(minutes / 5.0) * 5)
    return max(0, min(23 * 60 + 55, rounded))


def _hhmm(minutes: int | None) -> str:
    if minutes is None:
        return ""
    h, m = divmod(int(minutes), 60)
    return f"{h:02d}:{m:02d}"


def _confidence(sample_count: int, avg_deviation: float) -> tuple[int, str]:
    # Indicador operacional. A confirmação humana continua obrigatória.
    score = 40 + min(sample_count, 10) * 5
    score -= min(avg_deviation, 60) * 0.6
    score = int(max(20, min(99, round(score))))
    if sample_count >= 6 and avg_deviation <= 15:
        label = "Alta"
    elif sample_count >= 3 and avg_deviation <= 30:
        label = "Média"
    else:
        label = "Baixa"
    return score, label


def infer_employee_schedule(punched_at_values, lookback_days: int = 90) -> dict:
    """Infere uma jornada semanal a partir de dias com exatamente 4 batidas.

    O observado no AFD é apenas uma sugestão, não a jornada contratual.
    Dias com 1, 2, 3 ou 5+ batidas são ignorados na inferência.
    """
    datetimes: list[datetime] = []
    for value in punched_at_values:
        try:
            dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            continue
        datetimes.append(dt)

    empty_days = {
        weekday: {
            "weekday": weekday,
            "active": False,
            "start1": "",
            "end1": "",
            "start2": "",
            "end2": "",
            "expected_minutes": 0,
            "sample_count": 0,
            "confidence_score": 0,
            "confidence_label": "Sem dados",
        }
        for weekday in range(7)
    }

    if not datetimes:
        return {
            "has_suggestion": False,
            "days": empty_days,
            "valid_days": 0,
            "ignored_days": 0,
            "analysis_start": None,
            "analysis_end": None,
            "confidence_score": 0,
            "confidence_label": "Sem dados",
        }

    latest = max(datetimes)
    cutoff = latest - timedelta(days=max(1, int(lookback_days)) - 1)
    recent = [dt for dt in datetimes if dt >= cutoff]

    by_date = defaultdict(list)
    for dt in recent:
        by_date[dt.date()].append(dt)

    valid_by_weekday = defaultdict(list)
    ignored_days = 0
    for day, points in by_date.items():
        ordered = sorted(points)
        if len(ordered) != 4:
            ignored_days += 1
            continue
        mins = [_minute_of_day(p) for p in ordered]
        if not (mins[0] <= mins[1] <= mins[2] <= mins[3]):
            ignored_days += 1
            continue
        valid_by_weekday[day.weekday()].append(mins)

    days = {}
    weighted_scores = []
    weighted_samples = 0

    for weekday in range(7):
        samples = valid_by_weekday.get(weekday, [])
        if not samples:
            days[weekday] = empty_days[weekday]
            continue

        medians_raw = [median(sample[i] for sample in samples) for i in range(4)]
        medians_rounded = [_round_five(value) for value in medians_raw]
        deviations = []
        for sample in samples:
            deviations.extend(abs(sample[i] - medians_raw[i]) for i in range(4))
        avg_deviation = sum(deviations) / len(deviations) if deviations else 0.0
        score, label = _confidence(len(samples), avg_deviation)

        expected = max(0, medians_rounded[1] - medians_rounded[0]) + max(
            0, medians_rounded[3] - medians_rounded[2]
        )
        days[weekday] = {
            "weekday": weekday,
            "active": True,
            "start1": _hhmm(medians_rounded[0]),
            "end1": _hhmm(medians_rounded[1]),
            "start2": _hhmm(medians_rounded[2]),
            "end2": _hhmm(medians_rounded[3]),
            "expected_minutes": expected,
            "sample_count": len(samples),
            "confidence_score": score,
            "confidence_label": label,
            "avg_deviation": round(avg_deviation, 1),
        }
        weighted_scores.append(score * len(samples))
        weighted_samples += len(samples)

    valid_days = sum(len(v) for v in valid_by_weekday.values())
    if weighted_samples:
        overall_score = int(round(sum(weighted_scores) / weighted_samples))
        if overall_score >= 80:
            overall_label = "Alta"
        elif overall_score >= 60:
            overall_label = "Média"
        else:
            overall_label = "Baixa"
    else:
        overall_score = 0
        overall_label = "Sem dados"

    return {
        "has_suggestion": valid_days > 0,
        "days": days,
        "valid_days": valid_days,
        "ignored_days": ignored_days,
        "analysis_start": min((dt.date() for dt in recent), default=None),
        "analysis_end": max((dt.date() for dt in recent), default=None),
        "confidence_score": overall_score,
        "confidence_label": overall_label,
    }
