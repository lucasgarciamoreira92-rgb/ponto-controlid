from __future__ import annotations

from dataclasses import dataclass
import csv
import io
import re


CONTROL_ID_COLUMNS = {
    "cpf", "nome", "administrador", "matricula", "rfid",
    "codigo", "senha", "barras", "digitais",
}


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
    source_format: str = "generico"


def _decode(raw: bytes) -> str:
    # O export usuarios.txt da Control iD costuma vir em Windows-1252.
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", errors="replace")


def _normalize_id(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _control_id_afd_id(value: str) -> str:
    """Converte o campo cpf do usuarios.txt no identificador de 12 dígitos do AFD."""
    digits = _normalize_id(value)
    return digits.zfill(12) if digits else ""


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

    delimiters = [";", "\t", "|", ","]
    delimiter = max(delimiters, key=line.count)
    if line.count(delimiter) == 0:
        return [line]

    reader = csv.reader(io.StringIO(line), delimiter=delimiter)
    return [part.strip() for part in next(reader, []) if part.strip()]


def _parse_control_id_export(text: str) -> EmployeeImportResult | None:
    """Lê o usuarios.txt exportado diretamente pelo relógio/software Control iD."""
    lines = text.splitlines()
    first_nonempty = next((line for line in lines if line.strip()), "")
    if not first_nonempty:
        return None

    try:
        header = next(csv.reader(io.StringIO(first_nonempty), delimiter=";"))
    except Exception:
        return None

    normalized_header = [h.strip().lower() for h in header]
    if not {"cpf", "nome"}.issubset(set(normalized_header)):
        return None

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows: list[EmployeeImportRow] = []
    warnings: list[str] = []

    for line_no, record in enumerate(reader, 2):
        cpf = str(record.get("cpf") or "").strip()
        name = re.sub(r"\s+", " ", str(record.get("nome") or "")).strip()

        if not cpf and not name:
            continue
        if not name:
            warnings.append(f"Linha {line_no}: nome vazio; usuário ignorado")
            continue

        # No AFD da Control iD o identificador é o valor do campo cpf com 12 dígitos.
        # Exemplos: 88060586068 -> 088060586068; 3526693064 -> 003526693064.
        external_id = _control_id_afd_id(cpf)
        if not external_id:
            warnings.append(f"Linha {line_no}: CPF/identificador vazio; usuário importado sem vínculo AFD")
            external_id = None

        # Campos administrador, matrícula, RFID, código, senha, barras e digitais
        # não são necessários para a apuração e não são armazenados no aplicativo.
        rows.append(EmployeeImportRow(name=name, external_id=external_id, line_no=line_no))

    return EmployeeImportResult(
        rows=rows,
        warnings=warnings,
        total_lines=len(lines),
        source_format="control_id_usuarios",
    )


def _parse_generic(text: str) -> EmployeeImportResult:
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
            if _looks_like_id(parts[0]):
                warnings.append(f"Linha {line_no}: identificador sem nome ignorado")
                continue
            name = parts[0].strip()
        else:
            if _looks_like_id(parts[0]) and not _looks_like_id(parts[1]):
                external_id = _normalize_id(parts[0])
                name = parts[1].strip()
            elif _looks_like_id(parts[1]) and not _looks_like_id(parts[0]):
                name = parts[0].strip()
                external_id = _normalize_id(parts[1])
            else:
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

    return EmployeeImportResult(
        rows=rows,
        warnings=warnings,
        total_lines=len(lines),
        source_format="generico",
    )


def parse_employee_list(raw: bytes) -> EmployeeImportResult:
    text = _decode(raw)
    control_id = _parse_control_id_export(text)
    if control_id is not None:
        return control_id
    return _parse_generic(text)
