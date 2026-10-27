from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "reports" / "period-view.js"


def test_management_date_comparison_is_default():
    text = REPORT.read_text(encoding="utf-8")
    selector = (
        '<select id="dateViewMode">'
        '<option value="single">Одна дата</option>'
        '<option value="compare" selected>На эту дату по месяцам</option>'
        '</select>'
    )
    assert selector in text


def test_single_date_mode_remains_available():
    text = REPORT.read_text(encoding="utf-8")
    assert '<option value="single">Одна дата</option>' in text
    assert "const mode=document.getElementById('mode').value,isDate=mode==='date',compare=document.getElementById('dateViewMode').value==='compare';" in text


def test_management_page_loads_new_compare_asset_version():
    index = (REPORT.parent / "index.html").read_text(encoding="utf-8")
    assert './period-view.js?v=20261027-default-compare-1' in index
