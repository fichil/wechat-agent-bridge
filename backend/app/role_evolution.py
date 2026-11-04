"""Bounded, field-level AI edits; the host owns identity and card metadata."""
from copy import deepcopy
import json

from pydantic import ValidationError
from .conversations import ConversationError
from .role_models import CharacterCard, CharacterBook, CharacterBookEntry


ROLE_FIELDS = frozenset(('description', 'personality', 'scenario', 'first_mes', 'mes_example',
                        'system_prompt', 'post_history_instructions', 'alternate_greetings',
                        'character_book', 'tags'))
ROLE_LIMIT = 64 * 1024


def invalid_update():
    return ConversationError('ai_response_invalid', 'AI 角色卡修改无效或超出本轮权限；角色卡和记忆未更新。', 422)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(value)


def read_role_patch(raw):
    if not isinstance(raw, str):
        raise invalid_update()
    if not raw.strip():
        return {}
    try:
        if len(raw.encode('utf-8')) > ROLE_LIMIT:
            raise ValueError('too large')
        patch = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        if not isinstance(patch, dict) or not patch.keys() <= ROLE_FIELDS:
            raise ValueError('invalid fields')
        return patch
    except (ValueError, TypeError, RecursionError):
        raise invalid_update() from None


def merge_role_patch(card, raw, allowed):
    patch = read_role_patch(raw)
    if not patch:
        return deepcopy(card), []
    if not allowed:
        raise invalid_update()
    merged = deepcopy(card)
    # A supplied field replaces that field. Fields omitted from the patch,
    # including unknown V2 extensions, retain their original JSON representation.
    merged['data'].update(patch)
    if isinstance(patch.get('character_book'), dict):
        merged['data']['character_book'] = preserve_book_metadata(card['data'].get('character_book'), patch['character_book'])
    validate_role_size(merged)
    changed = sorted(key for key in patch if card['data'].get(key) != merged['data'][key])
    return merged, changed


def validate_role_size(card):
    try:
        CharacterCard.model_validate(card)
        if len(json.dumps(card, ensure_ascii=False, allow_nan=False).encode('utf-8')) > ROLE_LIMIT:
            raise ValueError('too large')
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise invalid_update() from None


def preserve_book_metadata(old, new):
    """Book replacement may edit content, but cannot rewrite vendor metadata.

    Entries with IDs retain that identity; ID-less entries use their position.
    Omitting a metadata key preserves it, while changing or inventing it fails.
    """
    old = old if isinstance(old, dict) else {}
    result = deepcopy(new)

    def retain(previous, current, fields):
        protected = (previous.keys() | current.keys()) - (fields - {'extensions'})
        for key in protected:
            if key in current and ((key not in previous and key != 'extensions')
                                   or current[key] != previous.get(key, {} if key == 'extensions' else None)):
                raise invalid_update()
            if key in previous:
                current[key] = deepcopy(previous[key])
        current.setdefault('extensions', {})

    retain(old, result, set(CharacterBook.model_fields))
    old_entries = old.get('entries', [])
    if isinstance(result.get('entries'), list):
        for index, entry in enumerate(result['entries']):
            if not isinstance(entry, dict):
                continue  # CharacterCard validation reports the invalid type.
            previous = next((item for item in old_entries if item.get('id') == entry['id']), {}) if entry.get('id') is not None else (
                old_entries[index] if index < len(old_entries) and old_entries[index].get('id') is None else {})
            retain(previous, entry, set(CharacterBookEntry.model_fields))
    return result


def derived_name(initial, conversation):
    if len(initial) + len(conversation) + 3 <= 200:
        return initial + ' - ' + conversation
    initial_budget = min(len(initial), max(98, 197 - len(conversation)))
    return initial[:initial_budget] + ' - ' + conversation[:197 - initial_budget]
