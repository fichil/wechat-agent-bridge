"""Bounded metadata-only inspection of one already authorized message list.

Never reads message Name/Value/Text, invokes controls, scrolls or infers sender
from alignment. Semantic hints are observations, not verified own identities.
Optional auxiliary strings may contain text; only their bounded shapes survive.
"""
import re
from wechat_agent_bridge.console_models import ConsoleError

MAX_ROWS, TAIL_ROWS, MAX_NODES, MAX_CHILDREN, MAX_DEPTH = 256, 5, 120, 32, 3
AUXILIARY_PROPERTIES = ('CurrentAutomationId', 'CurrentHelpText', 'CurrentFullDescription',
                        'CurrentItemType', 'CurrentItemStatus', 'CurrentAriaRole', 'CurrentAriaProperties')


def class_label(value):
    return value if isinstance(value, str) and re.fullmatch(r'mmui::[A-Za-z_][A-Za-z0-9_:]{0,90}', value) else 'other'


def direction_hint(value):
    # These are only exact ItemType observations; no message label is read.
    return {'outgoing': 'outgoing', 'incoming': 'incoming', 'self': 'self',
            'OutgoingMessage': 'outgoing', 'IncomingMessage': 'incoming',
            'SelfMessage': 'self'}.get(value, 'unknown') if isinstance(value, str) else 'unknown'


def optional(element, name):
    try:
        return getattr(element, name)
    except Exception:
        return None


def auxiliary_shape(element, name, *, full_description_property_id=None):
    """Discard the value; even a recognizable token cannot authorize a sender."""
    if name not in AUXILIARY_PROPERTIES:
        raise ConsoleError('message_auxiliary_property_forbidden')
    use_property_api = name == 'CurrentFullDescription' and full_description_property_id == 30159
    try:
        # FullDescription belongs to a newer element interface. The base
        # interface can still query its public property ID without that getter.
        value = (element.GetCurrentPropertyValue(30159) if use_property_api
                 else getattr(element, name))
    except Exception:
        return {'state': 'unsupported_or_failed'}
    if value is None:
        return {'state': 'empty_or_default' if use_property_api else 'empty'}
    if not isinstance(value, str):
        return {'state': 'unexpected_type'}
    if value == '':
        return {'state': 'empty_or_default' if use_property_api else 'empty'}
    if len(value) > 4096:
        return {'state': 'over_limit'}
    return dict(state='text_shape', length=len(value), has_line_breaks='\n' in value or '\r' in value,
        has_assignment='=' in value, simple_identifier=bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,127}', value)),
        exact_direction_token=direction_hint(value))


def collect(raw, root, uia, pid, *, auxiliary=False):
    condition = raw.CreatePropertyCondition(uia.UIA_NamePropertyId, '消息')
    for label in ('消息列表', 'Messages'):
        condition = raw.CreateOrCondition(condition, raw.CreatePropertyCondition(uia.UIA_NamePropertyId, label))
    condition = raw.CreateAndCondition(condition,
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_ListControlTypeId))
    lists = root.FindAll(uia.TreeScope_Descendants, condition)
    if type(lists.Length) is not int or lists.Length != 1:
        raise ConsoleError('message_schema_list_ambiguous')
    listing = lists.GetElement(0)
    if listing.CurrentProcessId != pid:
        raise ConsoleError('message_schema_process_mismatch')
    list_id = tuple(listing.GetRuntimeId())
    items = listing.FindAll(uia.TreeScope_Children,
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_ListItemControlTypeId))
    if type(items.Length) is not int or not 0 <= items.Length <= MAX_ROWS:
        raise ConsoleError('message_schema_row_limit')
    count, nodes, seen, truncated, rows = items.Length, [], set(), False, []
    for index in range(max(0, count - TAIL_ROWS), count):
        item = items.GetElement(index)
        item_id = tuple(item.GetRuntimeId())
        queue, row_nodes = [(item, 0)], []
        while queue:
            if len(nodes) >= MAX_NODES:
                truncated = True
                break
            element, depth = queue.pop(0)
            if element.CurrentProcessId != pid:
                raise ConsoleError('message_schema_process_mismatch')
            runtime = tuple(element.GetRuntimeId())
            if not runtime or runtime in seen:
                raise ConsoleError('message_schema_identity_cycle')
            seen.add(runtime)
            control_type = element.CurrentControlType
            if type(control_type) is not int or not 50000 <= control_type <= 50100:
                raise ConsoleError('message_schema_control_type_invalid')
            entry = dict(depth=depth, control_type=control_type,
                class_name=class_label(optional(element, 'CurrentClassName')),
                item_type_direction_hint=direction_hint(optional(element, 'CurrentItemType')))
            if auxiliary:
                entry['auxiliary_property_shapes'] = {
                    name: auxiliary_shape(element, name,
                        full_description_property_id=getattr(uia, 'UIA_FullDescriptionPropertyId', None))
                    for name in AUXILIARY_PROPERTIES}
            nodes.append(entry); row_nodes.append(entry)
            if depth < MAX_DEPTH:
                children = element.FindAll(uia.TreeScope_Children, raw.CreateTrueCondition())
                if type(children.Length) is not int or children.Length < 0:
                    raise ConsoleError('message_schema_children_invalid')
                limit = min(children.Length, MAX_CHILDREN, MAX_NODES - len(nodes) - len(queue))
                truncated |= children.Length > limit
                queue.extend((children.GetElement(i), depth + 1) for i in range(limit))
            else:
                truncated = True  # Children at this boundary are deliberately unknown.
            if tuple(element.GetRuntimeId()) != runtime:
                raise ConsoleError('message_schema_identity_changed')
        if tuple(item.GetRuntimeId()) != item_id:
            raise ConsoleError('message_schema_identity_changed')
        rows.append(dict(nodes=row_nodes))
        if len(nodes) >= MAX_NODES:
            truncated |= index < count - 1
            break
    if listing.CurrentProcessId != pid or tuple(listing.GetRuntimeId()) != list_id:
        raise ConsoleError('message_schema_identity_changed')
    return dict(visible_row_count=count, inspected_tail_rows=len(rows), rows=rows,
        node_count=len(nodes), truncated=truncated, maximum_depth=MAX_DEPTH,
        text_read=bool(auxiliary), message_name_value_text_read=False,
        auxiliary_accessibility_properties_read=bool(auxiliary), property_values_retained=False,
        own_identity_verified=False, paging_verified=False,
        coverage='accessible_loaded_tail_only', sender_decision='unverified')
