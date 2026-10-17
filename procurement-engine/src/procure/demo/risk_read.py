"""Pure intent and spoken summary for current company-owned risk reports."""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .company import list_companies
from .research import _EXPLICIT as _EXTERNAL_RESEARCH


_RISK = re.compile(r'\b(?:risks?|alerts?)\b', re.I)
_DOMAIN = re.compile(r'\b(?:inventory|stock|sourcing|suppliers?|procurement)\b', re.I)
_OWNED = re.compile(r'\b(?:our|my|current|this|workspace)\b', re.I)
_PUBLIC = re.compile(r'\b(?:news|headlines?|latest|fresh|recent|today|world|global|market|public)\b', re.I)
_ACTION = re.compile(r'\b(?:send|draft|prepare|approve|apply|save|update|change|edit|'
                     r'load|place|buy|purchase|execute|submit|create|watch|email|rfq|'
                     r'cancel|remove|delete|confirm|discard|reset|clear|withdraw)\b', re.I)
_SCENARIO = re.compile(r'\b(?:what[- ]if|suppose|simulate|preview|assume|assuming|'
                       r'how would|what would)\b', re.I)
_SOURCE_LABELS = {
    'synthetic_profile': 'company profile',
    'synthetic_delay_scenario': 'planning scenario',
    'synthetic_plan': 'reviewed recovery scenario',
    'observed_news': 'public headline',
}
_OPERATIONAL_IMPACTS = {
    'Potential stockout before a new delivery under the stated fictional use and lead assumptions; verify the current inventory and supplier schedule.':
        'Potential stockout before a new delivery under the stated use and lead assumptions; verify the current inventory and supplier schedule.',
    'Potential lateness applies only if this fictional supplier option is used; faster listed options meet the date and are available for review. No supplier selection or actual shipment delay is confirmed.':
        'Potential lateness applies only if this supplier option is used; faster listed options meet the date and are available for review. No supplier selection or actual shipment delay is confirmed.',
    'Potential late arrival under fictional order and supplier assumptions; no actual shipment delay has been observed.':
        'Potential late arrival under order and supplier assumptions; no actual shipment delay has been observed.',
    'The hypothetical delay may change sourcing feasibility or cost; this is a synthetic projection, not an observed shipment event.':
        'The hypothetical delay may change sourcing feasibility or cost; no shipment event is confirmed.',
}


def _spoken_alert_text(alert):
    """Clean only known generated operational copy; retain the source row verbatim."""
    title, impact = alert['title'], alert['impact']
    category, provenance = alert['category'], alert['provenance']
    if category == 'inventory' and provenance == 'synthetic_profile':
        impact = _OPERATIONAL_IMPACTS.get(impact, impact)
    elif category == 'supplier_delay' and provenance in {'synthetic_delay_scenario', 'synthetic_plan'}:
        suffixes = (
            (' has a potentially late fictional supplier option', ' has a potentially late supplier option'),
            (' order has synthetic lead-time exposure', ' order has lead-time exposure'),
        ) if provenance == 'synthetic_delay_scenario' else (
            (' recorded synthetic recovery', ' recorded recovery'),
        )
        for old, new in suffixes:
            if title.endswith(old):
                title = title[:-len(old)] + new
                break
        impact = _OPERATIONAL_IMPACTS.get(impact, impact)
    return title, impact


def has_non_read_intent(message):
    return isinstance(message, str) and bool(
        _EXTERNAL_RESEARCH.search(message) or _ACTION.search(message) or _SCENARIO.search(message))


def is_external_risk_research(message):
    return (isinstance(message, str) and bool(_EXTERNAL_RESEARCH.search(message))
            and bool(_RISK.search(message)) and bool(_DOMAIN.search(message))
            and bool(_OWNED.search(message)))


def is_public_risk_question(message):
    return isinstance(message, str) and bool(_PUBLIC.search(message))


def is_current_risk_question(message, company_id):
    """Claim only an explicit owned factual risk read, never an action or public brief."""
    if (not isinstance(message, str) or not _RISK.search(message)
            or not _DOMAIN.search(message) or not _OWNED.search(message)
            or is_public_risk_question(message) or has_non_read_intent(message)):
        return False
    for company in list_companies():
        if company['company_id'] == company_id:
            continue
        if any(re.search(r'(?<!\w)' + re.escape(label) + r'(?!\w)', message, re.I)
               for label in (company['company_id'], company['name'])):
            return False
    return True


def summarize_risks(projection, *, include_news=False):
    """Speak only selected alert impacts and reviewed next steps from the projection."""
    companies = projection['companies']
    if len(companies) != 1:
        return (f"Scanned {len(companies)} companies. Choose a company to hear its "
                'current inventory and supplier risk impacts and reviewed options.')
    company = companies[0]
    alerts = company['alerts']
    if not alerts:
        return (f"{company['company_name']}: no current inventory or supplier alert is "
                'identified in the available company evidence. Check data gaps before acting.')
    selected = []
    categories = ('inventory', 'supplier_delay', 'news') if include_news else ('inventory', 'supplier_delay')
    for category in categories:
        alert = next((row for row in alerts if row['category'] == category), None)
        if alert:
            selected.append(alert)
    if not selected:
        selected = alerts[:1]
    lines = [f"{company['company_name']}: {sum(company['counts'].values())} current risk and watch signals. "
             'Priority impacts from the company workspace:']
    for alert in selected:
        title, impact = _spoken_alert_text(alert)
        source = _SOURCE_LABELS.get(alert['provenance'], 'workspace evidence')
        line = f"{title} ({alert['severity']}; {source}). {impact}"
        metrics = alert.get('metrics') or {}
        if alert['category'] == 'inventory':
            try:
                cover = Decimal(str(metrics['stock_cover_days']))
                lead = Decimal(str(metrics['shortest_lead_days']))
                if cover.is_finite() and lead.is_finite():
                    rounded = cover.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                    line += f' Stock covers about {rounded} days against a {lead:g}-day supplier lead.'
            except (KeyError, InvalidOperation, TypeError, ValueError):
                pass
        elif alert['category'] == 'supplier_delay':
            days = metrics.get('lateness_days')
            if isinstance(days, int) and days > 0:
                line += f' This listed option would be {days} days late for the stated need date.'
        if alert['recovery_actions']:
            line += f" Next for review: {alert['recovery_actions'][0]['label']}."
        lines.append(line)
    lines.append('These are conditional signals; review current evidence before changing a plan or contacting a supplier. No action was applied.')
    return '\n'.join(lines)
