"""Validation for the small choice-card wire contract."""
import re


def normalize(card):
    if not isinstance(card, dict) or not re.fullmatch(r'[a-f0-9]{32}', str(card.get('id', ''))):
        return None
    question, options = card.get('question'), card.get('options')
    if not isinstance(question, str) or not 1 <= len(question) <= 200:
        return None
    if not isinstance(options, list) or not 2 <= len(options) <= 4:
        return None
    result = []
    for option in options:
        if not isinstance(option, dict):
            return None
        oid, label, description = option.get('id'), option.get('label'), option.get('description', '')
        if not isinstance(oid, str) or not 1 <= len(oid) <= 40 or not isinstance(label, str) or not 1 <= len(label) <= 60:
            return None
        if not isinstance(description, str) or len(description) > 160:
            return None
        result.append({'id': oid, 'label': label, 'description': description})
    if len({option['id'] for option in result}) != len(result):
        return None
    return {'id': card['id'], 'question': question, 'options': result,
            'selected': card.get('selected') if isinstance(card.get('selected'), str) else None,
            'expired': bool(card.get('expired')), 'expires': card.get('expires') if isinstance(card.get('expires'), (int, float)) else 0}
