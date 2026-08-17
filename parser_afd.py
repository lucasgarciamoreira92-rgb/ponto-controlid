from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import hashlib
import re


@dataclass
class EmployeeRecord:
    nsr: int
    timestamp: datetime
    operation: str
    external_id: str
    name: str
    raw_line: str


@dataclass
class PunchRecord:
    nsr: int
    timestamp: datetime
    external_id: str
    timezone_offset: str
    raw_line: str
    raw_hash: str


@dataclass
class AFDParseResult:
    device_model: str | None
    employees: list[EmployeeRecord]
    punches: list[PunchRecord]
    total_lines: int
    warnings: list[str]


def _decode(raw: bytes) -> str:
    # Exportações AFD da Control iD podem vir em ANSI/Latin-1.
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", errors="replace")


def normalize_external_id(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def parse_afd(path: str | Path) -> AFDParseResult:
    path = Path(path)
    raw = path.read_bytes()
    text = _decode(raw)
    lines = text.splitlines()
    employees: list[EmployeeRecord] = []
    punches: list[PunchRecord] = []
    warnings: list[str] = []
    device_model = None

    if lines:
        m = re.search(r"(iDClass\s+Bio\s+Prox)", lines[0], re.I)
        if m:
            device_model = m.group(1)

    for line_no, line in enumerate(lines, 1):
        if len(line) < 10 or not line[:9].isdigit():
            continue
        record_type = line[9]
        try:
            nsr = int(line[:9])
        except ValueError:
            continue

        if record_type == "3":
            # Layout observado no AFD enviado:
            # 0:9 NSR | 9 tipo | 10:34 datetime ISO | 34:46 identificador | 46: hash
            if len(line) < 46:
                warnings.append(f"Linha {line_no}: registro de ponto curto")
                continue
            dt_raw = line[10:34]
            ext = normalize_external_id(line[34:46])
            try:
                ts = datetime.strptime(dt_raw, "%Y-%m-%dT%H:%M:%S%z")
            except ValueError:
                warnings.append(f"Linha {line_no}: data/hora inválida: {dt_raw}")
                continue
            punches.append(PunchRecord(
                nsr=nsr,
                timestamp=ts,
                external_id=ext,
                timezone_offset=dt_raw[-5:],
                raw_line=line,
                raw_hash=hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest(),
            ))

        elif record_type == "5":
            # 0:9 NSR | 9 tipo | 10:34 datetime | 34 operação | 35:47 identificador | 47:99 nome
            if len(line) < 99:
                continue
            dt_raw = line[10:34]
            operation = line[34:35]
            ext = normalize_external_id(line[35:47])
            name = line[47:99].strip()
            if not ext or not name:
                continue
            try:
                ts = datetime.strptime(dt_raw, "%Y-%m-%dT%H:%M:%S%z")
            except ValueError:
                continue
            employees.append(EmployeeRecord(
                nsr=nsr,
                timestamp=ts,
                operation=operation,
                external_id=ext,
                name=name,
                raw_line=line,
            ))

    return AFDParseResult(
        device_model=device_model,
        employees=employees,
        punches=punches,
        total_lines=len(lines),
        warnings=warnings,
    )
