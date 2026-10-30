import pytest

import naming

MONTH_PRESET = "{{#if y}}{{y}}{{else}}Unknown year{{/if}}/{{#if MM}}{{MM}} - {{/if}}{{album}}/{{id}}"


def render(template, year=1994, month=7, album="Crete", pid="00012"):
    return naming.render(naming.parse(template), naming.values_for(year, month, None, album, pid, "crete"))


def test_default_template():
    assert render(naming.DEFAULT) == (["1994", "Crete"], "1994-07_crete_00012")
    assert render(naming.DEFAULT, year=None, month=None) == (["Unknown year", "Crete"], "undated_crete_00012")


def test_month_preset_keeps_the_dash():  # 1.12.1: " - " had been collapsed to a space
    assert MONTH_PRESET in naming.PRESETS
    assert render(MONTH_PRESET, 2007, 1, "Γάμος Βασίλη") == (["2007", "01 - Γάμος Βασίλη"], "00012")
    assert render(MONTH_PRESET, 2007, None, "Γάμος Βασίλη") == (["2007", "Γάμος Βασίλη"], "00012")


def test_photo_number_is_always_in_the_name():
    folders, name = render("{{y}}/{{album}}")
    assert (folders, name) == (["1994"], "Crete_00012")


def test_empty_parts_collapse():
    assert render("{{y}}-{{MM}}-{{dd}}/{{id}}", month=None) == (["1994"], "00012")


def test_never_in_reserved_or_top_folder():
    assert render("_inbox/{{id}}")[0] == ["inbox"]
    assert render("{{id}}") == (["Photos"], "00012")
    assert render("{{id}}", year=None) == (["Unknown year"], "00012")


def test_slash_and_bad_characters_in_values():
    assert render("{{album}}/{{id}}", album='A/B: "C"?') == (["A-B C"], "00012")


@pytest.mark.parametrize("template, problem", [
    ("{{nope}}/{{id}}", "Unknown variable"),
    ("{{#if y}}{{y}}/{{id}}", "missing its {{/if}}"),
    ("{{/if}}", "without a matching"),
    ("{{y}}/{oops}", "brace"),
    ("", "empty"),
])
def test_bad_templates(template, problem):
    folders, error = naming.example(template)
    assert folders is None and problem in error
