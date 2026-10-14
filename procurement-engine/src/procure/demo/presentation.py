"""Human-facing wording; canonical records and action arguments remain untouched."""
import re


def supplier_display_name(name: str) -> str:
    return name.removesuffix(' [SYNTHETIC]')


def present_turn(turn: dict, supplier_names: list[str]) -> dict:
    """Copy a wire turn for speech/display; exact proposal and evidence stay raw."""
    answer = turn.get('assistant_text')
    if not isinstance(answer, str):
        return turn.copy()
    for raw in supplier_names:
        if isinstance(raw, str):
            answer = answer.replace(raw, supplier_display_name(raw))
    return {**turn, 'assistant_text': answer}


def present_answer(answer: str, question: str, supplier_names: list[str]) -> str:
    """Format the spoken/display answer, never the source snapshot or proposal."""
    question = re.sub(r'\s*\[SYNTHETIC\]', '', question, flags=re.IGNORECASE)
    for raw in supplier_names:
        if isinstance(raw, str):
            answer = answer.replace(raw, supplier_display_name(raw))
    provenance = bool(re.search(r'\b(?:origin|source|provenance|real|verified|synthetic|fictional|sample|simulated|connected)\b|\bwhere\b.*\b(?:from|come)\b',
                                question, re.IGNORECASE) and
                      re.search(r'\b(?:data|records?|stock|inventory|suppliers?|news|figures?|information)\b',
                                question, re.IGNORECASE) and
                      not re.search(r'\b(?:draft|prepare|send)\b.*\b(?:email|rfq|message)\b',
                                    question, re.IGNORECASE))
    if provenance:
        return answer + (' Company inventory and supplier details come from sample company records, '
                         'not a connected company system. News headlines come from connected public '
                         'sources; check their source and retrieval times.')
    return answer.removesuffix(' Source: fictional company profile.')
