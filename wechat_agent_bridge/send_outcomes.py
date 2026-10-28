"""Content-free send outcomes; only proven pre-write interruptions can wait."""

PREWRITE_WAIT_REASONS = frozenset((
    'console_send_waiting_for_idle', 'desktop_event_change_observed',
    'background_input_changed', 'background_focus_changed',
    'background_clipboard_changed', 'existing_draft_or_unknown',
))


def validate_send_outcome(value):
    if (not isinstance(value, dict) or
            value.get('status') not in ('ui_confirmed', 'failed', 'unknown', 'deferred') or
            type(value.get('attempted')) is not bool or
            (value['status'] in ('ui_confirmed', 'unknown') and not value['attempted'])):
        raise ValueError('invalid_send_outcome')
    if value['status'] == 'deferred' and (value['attempted'] or
            value.get('no_write_verified') is not True or
            value.get('reason') not in PREWRITE_WAIT_REASONS):
        raise ValueError('unproven_send_deferral')
    return value
