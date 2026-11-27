import argparse
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
import logging
from pathlib import Path
import re
import unicodedata
from zoneinfo import ZoneInfo

logger = logging.getLogger("cleaning")
BRASILIA_TZ = ZoneInfo("America/Sao_Paulo")


def normalize_case_number(text):
    # aceita o CNJ com mascara ou 20 digitos, qualquer outra coisa vira None
    text = str(text or "").strip()
    if re.fullmatch(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", text) or re.fullmatch(r"\d{20}", text):
        return re.sub(r"\D", "", text)
    return None


def format_cnj(case_number):
    # Volta os 20 dígitos pro formato com máscara
    p = case_number
    return f"{p[:7]}-{p[7:9]}.{p[9:13]}.{p[13]}.{p[14:16]}.{p[16:]}"


def parse_amount(value):
    # Aceita número, ponto decimal (31000.00) e vírgula decimal com ou sem ponto de milhar (45.000,00 e 2,50)
    # Recusa um separador único seguido de 3 dígitos (2.500 e 1,234), que não da pra distinguir se é milhar ou decimal
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = Decimal(str(value))
    else:
        text = str(value).strip()
        if re.fullmatch(r"-?\d+(\.\d{1,2})?", text):
            value = Decimal(text)
        elif re.fullmatch(r"-?(\d{1,3}(\.\d{3})+|\d+),\d{1,2}", text):
            value = Decimal(text.replace(".", "").replace(",", "."))
        else:
            return None
    if value != round(value, 2):
        return None
    return round(value, 2)


def parse_date(text):
    # Aceita data ISO (2025-03-10) ou BR (10/03/2025), na duvida None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(text).strip(), fmt).date()
        except ValueError:
            pass
    return None


def parse_timestamp(text):
    # data e hora com fuso obrigatorio, sem fuso nao da pra comparar versoes com seguranca
    text = str(text or "").strip().replace("Z", "+00:00")
    try:
        timestamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo else None


def calculate_deadline(submitted_on):
    # Apresentado até 02/04 paga até o fim do ano seguinte, depois disso ganha mais um ano
    if submitted_on <= date(submitted_on.year, 4, 2):
        return date(submitted_on.year + 1, 12, 31)
    return date(submitted_on.year + 2, 12, 31)


def strip_accents(text):
    # Tira acento, maiúscula e espaço extra, pra comparação segura
    text = unicodedata.normalize("NFKD", text)
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).casefold().split())


def add_to_report(report, category, file, entry, reason):
    # Faz um registro no relatório e no log
    report.append({"categoria": category, "arquivo": file, "registro": entry, "motivo": reason})
    logger.info("%s | %s | %s | %s", category, file, entry, reason)


def read_csv(path, columns):
    # Tenta fazer a leitura do arquivo csv, com fallback pra codificação e separador

    content = path.read_bytes()

    try:
        text, encoding = content.decode("utf-8-sig"), "UTF-8"
    except UnicodeDecodeError:
        text, encoding = content.decode("latin-1"), "ISO-8859-1"

    if not text.strip():
        raise ValueError(f"{path.name} vazio")

    header = text.splitlines()[0]
    delimiter = ";" if header.count(";") > header.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    try:
        rows = list(reader)
    except csv.Error:
        raise ValueError(f"{path.name} em formato desconhecido")
    
    missing = set(columns) - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"{path.name} em formato desconhecido. "
                         f"colunas nao encontradas: {', '.join(sorted(missing))}")
    
    return rows, encoding, delimiter


def read_court(path, report):
    rows, encoding, delimiter = read_csv(path, ["numero_processo", "ente_devedor", "valor_face"])

    # Registra o que estiver fora do layout documentado
    deviations = []
    if encoding != "UTF-8":
        deviations.append(f"codificacao {encoding}")
    if delimiter != ",":
        deviations.append(f"separador '{delimiter}'")
    if any("," in row["valor_face"] for row in rows):
        deviations.append("valores com virgula decimal")
    if deviations:
        add_to_report(report, "corrigido", "tribunal.csv", "arquivo inteiro",
                      "fora do layout documentado: " + ", ".join(deviations))

    # Le o arquivo
    court = {}
    for line_number, row in enumerate(rows, start=2):
        case_number = normalize_case_number(row["numero_processo"])
        if not case_number:
            add_to_report(report, "descartado", "tribunal.csv", f"linha {line_number}",
                          f"processo invalido: {row['numero_processo']!r}")
            continue
        # valor ilegivel entra como None, o build_precatorios decide se o hub cobre
        court[case_number] = {"ente": row["ente_devedor"].strip(), "valor_face": parse_amount(row["valor_face"])}
    return court


def read_hub(path, report):
    # Lê o hub.csv e normaliza o numero do processo
    rows, _, _ = read_csv(path, ["processo", "ente", "valor_face", "prazo_pagamento"])
    hub = {}

    for line_number, row in enumerate(rows, start=2):
        case_number = normalize_case_number(row["processo"])
        deadline = parse_date(row["prazo_pagamento"])

        # Reporta a normalização e dados invalidos
        if not case_number:
            add_to_report(report, "descartado", "hub.csv", f"linha {line_number}",
                          f"processo invalido: {row['processo']!r}")
            continue
        if "-" not in row["processo"]:
            add_to_report(report, "corrigido", "hub.csv", f"linha {line_number}",
                          f"processo normalizado para {format_cnj(case_number)}")
        if row["prazo_pagamento"].strip() and deadline is None:
            add_to_report(report, "corrigido", "hub.csv", f"linha {line_number}",
                          f"prazo ilegivel ({row['prazo_pagamento']!r}) ignorado")

        hub[case_number] = {
            "ente": row["ente"].strip(),
            "valor_face": parse_amount(row["valor_face"]),
            "prazo": deadline,
        }

    return hub


def read_submissions(submissions, report):
    # Pega a data de apresentação de cada processo, com fuso horario de Brasília
    dates = {}

    for position, item in enumerate(submissions, start=1):
        case_number = normalize_case_number(item.get("processo"))
        raw = str(item.get("apresentado_em") or "")

        # data sem horario é mantida sem horario pra evitar discrepancias
        timestamp = None

        day = parse_date(raw)
        if day is None:
            timestamp = parse_timestamp(raw)
            day = timestamp.astimezone(BRASILIA_TZ).date() if timestamp else None

        if not case_number or not day:
            add_to_report(report, "descartado", "apresentacoes.json", f"posicao {position}",
                          "processo ou data invalidos")
            continue
        if "/" in raw:
            add_to_report(report, "corrigido", "apresentacoes.json", format_cnj(case_number),
                          f"data {raw!r} -> {day}")
        if timestamp and day != timestamp.astimezone(timezone.utc).date():
            add_to_report(report, "em_duvida", "apresentacoes.json", format_cnj(case_number),
                          f"o fuso muda o dia ({raw}), usada a data de Brasilia: {day}")
        dates[case_number] = day
    return dates


def build_precatorios(court, hub, submissions, report):
    # o tribunal prevalece, quem so aparece de um lado entra, mas fica registrado como duvida
    precatorios = {}
    for case_number in sorted(court.keys() | hub.keys()):
        t, h = court.get(case_number), hub.get(case_number)

        # o valor do tribunal é priorizado, o valor do hub é usado como backup
        court_value = t["valor_face"] if t else None
        hub_value = h["valor_face"] if h else None
        face_value = court_value if court_value is not None else hub_value
        if face_value is None:
            add_to_report(report, "descartado", "tribunal.csv" if t else "hub.csv", format_cnj(case_number),
                          "sem valor de face valido")
            continue
        if t and court_value is None:
            add_to_report(report, "em_duvida", "tribunal.csv", format_cnj(case_number),
                          f"valor de face ilegivel no tribunal, usado o do hub, {hub_value}")
        elif h and hub_value is None:
            add_to_report(report, "corrigido", "hub.csv", format_cnj(case_number),
                          f"valor de face ilegivel no hub, usado o do tribunal, {court_value}")
        elif t and h and court_value != hub_value:
            add_to_report(report, "em_duvida", "hub.csv", format_cnj(case_number),
                          f"valor de face diverge entre hub ({hub_value}) e tribunal ({court_value}), utilizando do tribunal")

        if t and h:
            if strip_accents(t["ente"]) != strip_accents(h["ente"]):
                add_to_report(report, "em_duvida", "hub.csv", format_cnj(case_number),
                              f"ente diverge entre hub '{h['ente']}' e tribunal '{t['ente']}', utilizando do tribunal")
        elif t:
            add_to_report(report, "em_duvida", "tribunal.csv", format_cnj(case_number),
                          "ausente no hub, incluido com os dados do tribunal")
        else:
            add_to_report(report, "em_duvida", "hub.csv", format_cnj(case_number),
                          "ausente no tribunal, mantido com os dados do hub")

        base = t or h
        submitted_on = submissions.get(case_number)
        deadline = calculate_deadline(submitted_on) if submitted_on else None
        if not submitted_on:
            add_to_report(report, "em_duvida", "apresentacoes.json", format_cnj(case_number),
                          "sem data de apresentacao, prazo nao calculado")
        elif h and h["prazo"] and h["prazo"] != deadline:
            add_to_report(report, "corrigido", "hub.csv", format_cnj(case_number),
                          f"prazo {h['prazo']} recalculado para {deadline} (apresentado em {submitted_on})")

        precatorios[case_number] = {
            "processo": case_number,
            "cnj": format_cnj(case_number),
            "ente_devedor": base["ente"],
            "valor_face": face_value,
            "apresentado_em": submitted_on,
            "prazo_pagamento": deadline,
            "fontes": [name for name, item in (("tribunal", t), ("hub", h)) if item],
        }
    return precatorios


def clean_payments(events, precatorios, report):
    # Descarta os eventos inválidos e fica com a versão mais recente de cada evento_id
    seen = set()
    by_id = {}

    for position, event in enumerate(events, start=1):
        entry = f"{event.get('evento_id')} (posicao {position})"

        key = json.dumps(event, sort_keys=True)
        if key in seen:
            add_to_report(report, "descartado", "pagamentos.json", entry, "duplicata identica de um registro anterior")
            continue
        seen.add(key)

        case_number = normalize_case_number(event.get("processo"))
        value = parse_amount(event.get("valor"))
        paid_on = parse_date(event.get("data_pagamento"))
        updated_at = parse_timestamp(event.get("atualizado_em"))

        if not event.get("evento_id"):
            reason = "evento_id ausente"
        elif not case_number:
            reason = f"processo invalido: {event.get('processo')!r}"
        elif case_number not in precatorios:
            reason = f"processo {format_cnj(case_number)} fora da base de precatorios"
        elif value is None:
            reason = f"valor invalido: {event.get('valor')!r}"
        elif value <= 0:
            reason = f"valor não positivo ({value}); confirmar com o tribunal"
        elif event.get("tipo") not in ("pagamento", "estorno"):
            reason = f"tipo invalido: {event.get('tipo')!r}"
        elif paid_on is None:
            reason = f"data de pagamento invalida: {event.get('data_pagamento')!r}"
        elif updated_at is None:
            reason = "atualizado_em ausente ou invalido"
        else:
            reason = None
        if reason:
            add_to_report(report, "descartado", "pagamentos.json", entry, reason)
            continue

        fixes = []
        if "-" not in event["processo"]:
            fixes.append(f"processo sem mascara -> {format_cnj(case_number)}")
        if isinstance(event["valor"], str):
            fixes.append(f"valor {event['valor']!r} -> {value}")
        if "/" in event["data_pagamento"]:
            fixes.append(f"data {event['data_pagamento']!r} -> {paid_on}")
        if fixes:
            add_to_report(report, "corrigido", "pagamentos.json", entry, "; ".join(fixes))

        by_id.setdefault(event["evento_id"], []).append({
            "evento_id": event["evento_id"],
            "processo": case_number,
            "tipo": event["tipo"],
            "valor": value,
            "data_pagamento": paid_on,
            "atualizado_em": updated_at,
        })

    # mesmo evento_id mantem a versao com atualizado_em mais recente
    payments = []
    for event_id, versions in by_id.items():
        versions.sort(key=lambda v: v["atualizado_em"])
        latest = versions[-1]

        if any(v["atualizado_em"] == latest["atualizado_em"] and v != latest for v in versions):
            add_to_report(report, "em_duvida", "pagamentos.json", event_id,
                          "versoes diferentes com o mesmo atualizado_em")
            continue
        for old in versions[:-1]:
            if old == latest:
                reason = "mesmo evento repetido em outro formato"
            else:
                reason = (f"versao de {old['atualizado_em']} (valor {old['valor']}) substituida pela de "
                          f"{latest['atualizado_em']} (valor {latest['valor']})")
            add_to_report(report, "corrigido", "pagamentos.json", event_id, reason)

        submitted_on = precatorios[latest["processo"]]["apresentado_em"]
        if submitted_on and latest["data_pagamento"] < submitted_on:
            add_to_report(report, "em_duvida", "pagamentos.json", event_id,
                          f"pago em {latest['data_pagamento']}, antes da apresentacao em {submitted_on}")
        payments.append(latest)

    return sorted(payments, key=lambda p: p["evento_id"])


def calculate_totals(precatorios, payments, report):
    # total derivado dos eventos, nunca acumulado
    for case_number, prec in precatorios.items():
        events = [p for p in payments if p["processo"] == case_number]
        prec["total_pagamentos"] = sum((p["valor"] for p in events if p["tipo"] == "pagamento"), Decimal("0.00"))
        prec["total_estornos"] = sum((p["valor"] for p in events if p["tipo"] == "estorno"), Decimal("0.00"))
        prec["total_liquido"] = prec["total_pagamentos"] - prec["total_estornos"]
        prec["saldo"] = prec["valor_face"] - prec["total_liquido"]

        if prec["total_liquido"] < 0:
            add_to_report(report, "em_duvida", "pagamentos.json", prec["cnj"], "estornos maiores que os pagamentos")
        elif prec["total_liquido"] > prec["valor_face"]:
            add_to_report(report, "em_duvida", "pagamentos.json", prec["cnj"], "total pago maior que o valor de face")


def process(folder):
    # Precatórios primeiro, porque os pagamentos dependem deles pra validar o processo
    report = []
    court = read_court(folder / "tribunal.csv", report)
    hub = read_hub(folder / "hub.csv", report)
    submissions = read_submissions(json.loads((folder / "apresentacoes.json").read_text("utf-8")), report)
    precatorios = build_precatorios(court, hub, submissions, report)
    payments = clean_payments(json.loads((folder / "pagamentos.json").read_text("utf-8")), precatorios, report)
    calculate_totals(precatorios, payments, report)
    return precatorios, payments, report


def write_output(output_dir, precatorios, payments, report, hashes):
    # Grava a base limpa e o relatório em JSON
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {c: sum(1 for o in report if o["categoria"] == c) for c in ("corrigido", "descartado", "em_duvida")}
    outputs = {
        "precatorios.json": list(precatorios.values()),
        "pagamentos.json": payments,
        "relatorio.json": {"arquivos_sha256": hashes, "resumo": summary, "ocorrencias": report},
    }
    for name, content in outputs.items():
        text = json.dumps(content, indent=2, ensure_ascii=False, default=str)
        (output_dir / name).write_text(text, encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Limpeza dos arquivos de precatorios.")
    parser.add_argument("--data-dir", type=Path, default=Path("dados"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")

    if args.output_dir.resolve() == args.data_dir.resolve():
        logger.error("Os novos arquivos nao podem ser salvos na mesma pasta dos arquivos originais")
        return 1

    # Cria uma hash pra cada arquivo, permite rastreio para deduplicação
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.data_dir.glob("*.*"))}

    try:
        precatorios, payments, report = process(args.data_dir)
    except ValueError as error:
        logger.error(error)
        return 1
    
    summary = write_output(args.output_dir, precatorios, payments, report, hashes)
    logger.info("concluido: %d precatorios, %d pagamentos, %s", len(precatorios), len(payments), summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())