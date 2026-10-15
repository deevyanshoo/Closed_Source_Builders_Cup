from decimal import Decimal

from procure.demo.company import list_companies, get_company


def _plain(text):
    return format(Decimal(text).normalize(), 'f')


def test_every_scenario_budget_is_a_plain_decimal_without_trailing_zero():
    for summary in list_companies():
        company = get_company(summary['company_id'])
        budgets = [company['buyer_defaults']['budget']] + [
            scenario['buyer_defaults']['budget'] for scenario in company['quote_coverage']['scenarios']]
        for budget in budgets:
            assert budget == _plain(budget), (company['company_id'], budget)


def test_meridian_primary_scenario_budget_reads_as_a_whole_amount():
    company = get_company('meridian-pharma')
    assert company['buyer_defaults']['budget'] == '382500'
