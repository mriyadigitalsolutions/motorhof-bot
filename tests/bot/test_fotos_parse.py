import pytest

from modules.photos.handlers import BadRequest, CODE_HINT, parse_request

HINT = "Укажи номер машины с префиксом: /fotos MH_1022 или /fotos KO_2001"


@pytest.mark.parametrize("raw,code", [
    ("MH_1022", "MH_1022"), ("mh1022", "MH_1022"), ("MH1022", "MH_1022"),
    ("KO 2001", "KO_2001"), ("ko_2001", "KO_2001"), ("  mh_1022  ", "MH_1022"),
])
def test_code_forms_normalized(raw, code):
    assert parse_request(raw).code == code
    assert parse_request(raw).extra == []


@pytest.mark.parametrize("raw", ["1022", "", None, "XX_12", "MH_", "MH_10a22"])
def test_bad_code_gives_hint(raw):
    with pytest.raises(BadRequest) as e:
        parse_request(raw)
    assert e.value.text == HINT
    assert CODE_HINT == HINT


def test_known_variant():
    r = parse_request("KO 2001 full")
    assert (r.code, r.extra) == ("KO_2001", ["full"])
    assert parse_request("mh1022 FULL").extra == ["full"]


@pytest.mark.parametrize("word", ["что-то", "listing"])
def test_unknown_variant_lists_available(word):
    with pytest.raises(BadRequest) as e:
        parse_request(f"MH_1022 {word}")
    assert e.value.text == f'Неизвестный вариант "{word}". Доступно: full'
