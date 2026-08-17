from __future__ import annotations

from dataclasses import dataclass
import csv
import io
import re


@dataclass
class EmployeeImportRow:
    name: str
    external_id: str | None
    line_no: int


@dataclass
class EmployeeImportResult:
    rows: list[EmployeeImportRow]
    warnings: list[str]
    total_lines: int


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", errors="replace")


def _normalize_id(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _looks_like_id(value: str) -> bool:
    digits = _normalize_id(value)
    return bool(digits) and len(digits) >= 4 and len(re.sub(r"[\d\s.\-/]", "", value or "")) == 0


def _is_header(parts: list[str]) -> bool:
    text = " ".join(p.strip().lower() for p in parts)
    words = ("nome", "colaborador", "funcionario", "funcionário", "cpf", "pis", "matricula", "matrícula", "identificador", "id")
    return any(word in text for word in words)


def _split_line(line: str) -> list[str]:
    line = line.strip()
    if not line:
        return []

    # Detecta os delimitadores mais comuns de exportações TXT/CSV.
    delimiters = [";", "\t", "|", ","]
    delimiter = max(delimiters, key=line.count)
    if line.count(delimiter) == 0:
        return [line]

    reader = csv.reader(io.StringIO(line), delimiter=delimiter)
    return [part.strip() for part in next(reader, []) if part.strip()]


def parse_employee_list(raw: bytes) -> EmployeeImportResult:
    text = _decode(raw)
    lines = text.splitlines()
    rows: list[EmployeeImportRow] = []
    warnings: list[str] = []

    for line_no, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            continue

        parts = _split_line(line)
        if not parts:
            continue
        if _is_header(parts):
            continue

        name = ""
        external_id: str | None = None

        if len(parts) == 1:
            # Uma coluna é interpretada como nome. Um valor só numérico não é suficiente.
            if _looks_like_id(parts[0]):
                warnings.append(f"Linha {line_no}: identificador sem nome ignorado")
                continue
            name = parts[0].strip()
        else:
            # Aceita tanto ID;NOME quanto NOME;ID.
            if _looks_like_id(parts[0]) and not _looks_like_id(parts[1]):
                external_id = _normalize_id(parts[0])
                name = parts[1].strip()
            elif _looks_like_id(parts[1]) and not _looks_like_id(parts[0]):
                name = parts[0].strip()
                external_id = _normalize_id(parts[1])
            else:
                # Se houver mais colunas, procura primeiro uma coluna de identificador e usa
                # como nome a primeira coluna textual diferente dela.
                id_index = next((i for i, value in enumerate(parts) if _looks_like_id(value)), None)
                if id_index is not None:
                    external_id = _normalize_id(parts[id_index])
                textual = [value for i, value in enumerate(parts) if i != id_index and not _looks_like_id(value)]
                name = textual[0].strip() if textual else ""

        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            warnings.append(f"Linha {line_no}: nome não identificado")
            continue
        if len(name) < 2:
            warnings.append(f"Linha {line_no}: nome muito curto ignorado")
            continue

        rows.append(EmployeeImportRow(name=name, external_id=external_id or None, line_no=line_no))

    return EmployeeImportResult(rows=rows, warnings=warnings, total_lines=len(lines))
