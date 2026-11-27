from datetime import date
from decimal import Decimal
import hashlib
from pathlib import Path
import shutil
import pytest

from limpeza import (
    build_precatorios,
    calculate_deadline,
    calculate_totals,
    clean_payments,
    main,
    normalize_case_number,
    parse_amount,
    process,
    read_hub,
    read_submissions,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "dados"
P1 = "0012345-10.2019.8.26.0053"
P1_DIGITS = "00123451020198260053"


def precatorios():
    return {P1_DIGITS: {"processo": P1_DIGITS, "cnj": P1, "valor_face": Decimal("45000.00"),
                        "apresentado_em": date(2025, 1, 1)}}


def event(event_id="ev-1", value=100, updated_at="2025-03-10T10:00:00-03:00", **extra):
    return {"evento_id": event_id, "processo": P1, "valor": value, "data_pagamento": "2025-03-10",
            "tipo": "pagamento", "atualizado_em": updated_at, **extra}


def clean(events):
    report = []
    payments = clean_payments(events, precatorios(), report)
    return payments, [o["categoria"] for o in report]


# valores e processo

@pytest.mark.parametrize("raw, expected", [
    (15000.0, Decimal("15000.00")),
    ("2.500,50", Decimal("2500.50")),
    ("45.000,00", Decimal("45000.00")),
    ("31000.00", Decimal("31000.00")),
])
def test_accepted_amounts(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", [None, True, "", "abc", "2.500", "R$ 1.000", "1,234", "10.001", 10.001])
def test_ambiguous_or_invalid_amounts(raw):
    assert parse_amount(raw) is None


def test_case_number():
    assert normalize_case_number(P1) == P1_DIGITS
    assert normalize_case_number(P1_DIGITS) == P1_DIGITS
    assert normalize_case_number("xx" + P1) is None
    assert normalize_case_number("") is None


# prazo e fuso

@pytest.mark.parametrize("submitted_on, deadline", [
    (date(2025, 4, 2), date(2026, 12, 31)),
    (date(2025, 4, 3), date(2027, 12, 31)),
    (date(2023, 12, 31), date(2025, 12, 31)),
    (date(2024, 2, 29), date(2025, 12, 31)),
])
def test_deadline(submitted_on, deadline):
    assert calculate_deadline(submitted_on) == deadline


@pytest.mark.parametrize("raw, expected, doubtful", [
    ("2025-04-02", date(2025, 4, 2), False),                  # data pura nao ganha horario
    ("2025-04-02T23:30:00-03:00", date(2025, 4, 2), True),    # em UTC ja e 03/04
    ("2025-04-03T01:00:00Z", date(2025, 4, 2), True),         # em Brasilia ainda e 02/04
    ("2025-04-03T12:00:00-03:00", date(2025, 4, 3), False),
])
def test_submission_uses_brasilia_date(raw, expected, doubtful):
    report = []
    dates = read_submissions([{"processo": P1, "apresentado_em": raw}], report)
    assert dates[P1_DIGITS] == expected
    assert bool(report) == doubtful


def test_submission_br_date_is_converted_and_reported():
    report = []
    dates = read_submissions([{"processo": P1, "apresentado_em": "02/04/2025"}], report)
    assert dates[P1_DIGITS] == date(2025, 4, 2)
    assert [o["categoria"] for o in report] == ["corrigido"]


# pagamentos

def test_latest_version_wins_even_if_it_comes_first():
    payments, categories = clean([
        event(value=15500.0, updated_at="2025-03-11T09:00:00-03:00"),
        event(value=15000.0, updated_at="2025-03-10T10:00:00-03:00"),
    ])
    assert [p["valor"] for p in payments] == [Decimal("15500.00")]
    assert categories == ["corrigido"]


def test_compares_instant_not_text():
    # 10:00-03:00 e 13:00 UTC, entao e mais recente que 12:00Z
    payments, _ = clean([
        event(value=1, updated_at="2025-03-10T10:00:00-03:00"),
        event(value=2, updated_at="2025-03-10T12:00:00Z"),
    ])
    assert payments[0]["valor"] == Decimal("1.00")


def test_identical_duplicate():
    payments, categories = clean([event(), event()])
    assert len(payments) == 1
    assert categories == ["descartado"]


def test_same_event_in_different_formats_counts_once():
    payments, _ = clean([event(value="2.500,50"), event(value=2500.5)])
    assert [p["valor"] for p in payments] == [Decimal("2500.50")]


def test_updated_at_tie_is_doubtful():
    payments, categories = clean([event(value=1), event(value=2)])
    assert payments == []
    assert categories == ["em_duvida"]


def test_missing_updated_at_does_not_replace_valid_version():
    payments, categories = clean([event(value=100), event(value=999, updated_at=None)])
    assert [p["valor"] for p in payments] == [Decimal("100.00")]
    assert categories == ["descartado"]


@pytest.mark.parametrize("field, value", [
    ("valor", None), ("valor", -200), ("valor", 0), ("processo", ""),
    ("processo", "0099999-99.2020.8.26.0053"), ("tipo", "ajuste"), ("data_pagamento", "2025-02-30"),
])
def test_invalid_events_are_discarded(field, value):
    payments, categories = clean([event(**{field: value})])
    assert payments == []
    assert categories == ["descartado"]


def test_br_format_is_converted_and_reported():
    payments, categories = clean([event(value="2.500,50", data_pagamento="10/04/2025")])
    assert payments[0]["valor"] == Decimal("2500.50")
    assert payments[0]["data_pagamento"] == date(2025, 4, 10)
    assert categories == ["corrigido"]


def test_reversal_subtracts_and_reprocessing_keeps_total():
    events = [event("ev-6", value=5000), event("ev-7", value=5000, tipo="estorno"), event("ev-8", value=100)]
    once, twice = precatorios(), precatorios()
    calculate_totals(once, clean_payments(events, once, []), [])
    calculate_totals(twice, clean_payments(events + events, twice, []), [])
    assert once[P1_DIGITS]["total_liquido"] == twice[P1_DIGITS]["total_liquido"] == Decimal("100.00")


# precatorios

def test_court_prevails_and_ente_spelling_is_not_a_divergence():
    report = []
    court = {P1_DIGITS: {"ente": "Município de São Paulo", "valor_face": Decimal("45000.00")}}
    hub = {P1_DIGITS: {"ente": "MUNICIPIO DE SAO PAULO", "valor_face": Decimal("46000.00"), "prazo": date(2027, 12, 31)}}
    precs = build_precatorios(court, hub, {P1_DIGITS: date(2025, 4, 2)}, report)
    assert precs[P1_DIGITS]["valor_face"] == Decimal("45000.00")
    assert precs[P1_DIGITS]["prazo_pagamento"] == date(2026, 12, 31)
    assert len(report) == 2  # valor de face e prazo; o ente nao conta


@pytest.mark.parametrize("court_value, hub_value, expected, category", [
    (None, Decimal("46000.00"), Decimal("46000.00"), "em_duvida"),   # tribunal ilegivel, usa o hub
    (Decimal("45000.00"), None, Decimal("45000.00"), "corrigido"),   # hub ilegivel, tribunal prevalece
])
def test_illegible_face_value_uses_the_other_source(court_value, hub_value, expected, category):
    report = []
    court = {P1_DIGITS: {"ente": "Município de São Paulo", "valor_face": court_value}}
    hub = {P1_DIGITS: {"ente": "Município de São Paulo", "valor_face": hub_value, "prazo": None}}
    precs = build_precatorios(court, hub, {P1_DIGITS: date(2025, 4, 2)}, report)
    assert precs[P1_DIGITS]["valor_face"] == expected
    assert [o["categoria"] for o in report] == [category]


def test_no_valid_face_value_is_discarded():
    report = []
    court = {P1_DIGITS: {"ente": "Município de São Paulo", "valor_face": None}}
    precs = build_precatorios(court, {}, {P1_DIGITS: date(2025, 4, 2)}, report)
    assert precs == {}
    assert [o["categoria"] for o in report] == ["descartado"]


def test_illegible_hub_deadline_enters_and_is_reported(tmp_path):
    path = tmp_path / "hub.csv"
    path.write_text("processo,ente,valor_face,apresentado_em,prazo_pagamento\n"
                    f"{P1},Município de São Paulo,45000.00,2025-04-02,31-12-2026\n", encoding="utf-8")
    report = []
    hub = read_hub(path, report)
    assert hub[P1_DIGITS]["valor_face"] == Decimal("45000.00")
    assert hub[P1_DIGITS]["prazo"] is None
    assert [o["categoria"] for o in report] == ["corrigido"]


# arquivos reais

def test_real_files():
    precs, _, report = process(DATA_DIR)
    assert precs["00123451020198260053"]["total_liquido"] == Decimal("18000.50")  # ev-001 novo + ev-002 uma vez
    assert precs["00234562120208260053"]["total_liquido"] == Decimal("9200.00")   # ev-003 + ev-005
    assert precs["00345673220188260405"]["total_liquido"] == Decimal("0.00")      # estorno
    assert precs["00456784320215020011"]["ente_devedor"] == "União"
    assert precs["00567895420174036100"]["prazo_pagamento"] == date(2026, 12, 31)
    discarded = {o["registro"].split()[0] for o in report if o["categoria"] == "descartado"}
    assert discarded == {"ev-002", "ev-004", "ev-009", "ev-010"}


def test_originals_are_not_modified(tmp_path):
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA_DIR.iterdir()}
    assert main(["--data-dir", str(DATA_DIR), "--output-dir", str(tmp_path)]) == 0
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA_DIR.iterdir()}
    assert before == after
    assert main(["--data-dir", str(DATA_DIR), "--output-dir", str(DATA_DIR)]) == 1


@pytest.mark.parametrize("encoding, delimiter", [
    ("utf-16", ";"),     # nem chega a ser lido como CSV
    ("latin-1", "|"),    # lido, mas as colunas nao aparecem
])
def test_unknown_csv_format_stops_without_output(tmp_path, encoding, delimiter):
    data_dir, output_dir = tmp_path / "dados", tmp_path / "saida"
    shutil.copytree(DATA_DIR, data_dir)
    text = (DATA_DIR / "tribunal.csv").read_bytes().decode("latin-1").replace(";", delimiter)
    (data_dir / "tribunal.csv").write_bytes(text.encode(encoding))
    assert main(["--data-dir", str(data_dir), "--output-dir", str(output_dir)]) == 1
    assert not output_dir.exists()

def test_divergent_face_value_and_ente_are_doubtful():
    report = []
    court = {P1_DIGITS: {"ente": "União", "valor_face": Decimal("30000.00")}}
    hub = {P1_DIGITS: {"ente": "Município de Santos", "valor_face": Decimal("31000.00"), "prazo": None}}
    precs = build_precatorios(court, hub, {P1_DIGITS: date(2025, 4, 3)}, report)
    assert precs[P1_DIGITS]["valor_face"] == Decimal("30000.00")
    assert precs[P1_DIGITS]["ente_devedor"] == "União"
    assert [o["categoria"] for o in report] == ["em_duvida", "em_duvida"]


def test_empty_csv_stops_without_output(tmp_path):
    data_dir, output_dir = tmp_path / "dados", tmp_path / "saida"
    shutil.copytree(DATA_DIR, data_dir)
    (data_dir / "tribunal.csv").write_bytes(b"")
    assert main(["--data-dir", str(data_dir), "--output-dir", str(output_dir)]) == 1
    assert not output_dir.exists()