from __future__ import annotations


def test_high_risk_labels_link_to_glossary_anchors(client):
    home = client.get("/")
    assert home.status_code == 200
    assert '/glossary#est-net-liquidity-sellable' in home.text
    assert '/glossary#locked-capital' in home.text
    assert '/glossary#forfeitable-capital' in home.text
    assert '/glossary#employment-tax' in home.text

    net_value = client.get("/net-value")
    assert net_value.status_code == 200
    assert '/glossary#hypothetical-full-liquidation' in net_value.text
    assert '/glossary#employment-tax' in net_value.text
    assert '/glossary#cost-basis' in net_value.text

    tax_plan = client.get("/tax-plan")
    assert tax_plan.status_code == 200
    assert '/glossary#aea' in tax_plan.text
    assert '/glossary#ani-adjusted-net-income' in tax_plan.text


def test_net_value_employment_tax_label_keeps_sellable_context(client):
    page = client.get("/net-value")
    assert page.status_code == 200
    assert "Estimated Employment Tax (Sellable Lots; Sell-All Context)" in page.text


def test_net_value_surfaces_deployable_delta_and_capital_stack_link(client):
    page = client.get("/net-value")
    assert page.status_code == 200
    assert "Net Value vs Deployable Today Delta" in page.text
    assert "Deployable Today (From Capital Stack)" in page.text
    assert 'href="/capital-stack"' in page.text
