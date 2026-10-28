"""Bounded contact-card identity checks; no account grant or message reading."""
import hashlib
import re

from wechat_agent_bridge.console_models import ConsoleError

PRIVATE_HEADER='mmui::ChatTitleBarChatSingleView'


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def labeled_identity(values,expected):
    if not re.fullmatch(r'[A-Za-z0-9_-]{6,64}',expected):
        raise ConsoleError('contact_expected_id_invalid')
    found=[]
    for index,value in enumerate(values):
        match=re.fullmatch(r'微信号\s*[:：]\s*([A-Za-z0-9_-]{6,64})',value)
        if match:
            found.append(match.group(1))
        elif re.fullmatch(r'微信号\s*[:：]?',value):
            if index+1>=len(values) or not re.fullmatch(r'[A-Za-z0-9_-]{6,64}',values[index+1]):
                raise ConsoleError('contact_id_field_incomplete')
            found.append(values[index+1])
    if set(found)!={expected}:
        raise ConsoleError('contact_profile_id_mismatch')
    return True


def read_card(profile,pid,expected):
    queue=[profile]; seen=set(); values=[]; total=0
    while queue:
        if len(seen)>=160: raise ConsoleError('contact_profile_node_limit')
        node=queue.pop(0); info=node.element_info
        rid=tuple(info.runtime_id)
        if not rid or rid in seen or info.process_id!=pid:
            raise ConsoleError('contact_profile_node_identity_changed')
        seen.add(rid)
        value=node.window_text().strip()
        if len(value)>4096: raise ConsoleError('contact_profile_text_limit')
        total+=len(value)
        if total>16384: raise ConsoleError('contact_profile_text_limit')
        if value: values.append(value)
        children=node.children()
        if len(children)>30: raise ConsoleError('contact_profile_child_limit')
        queue.extend(children)
        if tuple(info.runtime_id)!=rid or info.process_id!=pid:
            raise ConsoleError('contact_profile_node_identity_changed')
    labeled_identity(values,expected)
    return dict(exact_id_verified=True,expected_id_sha256=sha(expected),nodes_read=len(seen),
        profile_hwnd=profile.handle,profile_runtime_id=list(profile.element_info.runtime_id))


def require_target(backend,pid,hwnd,label):
    instance=backend.bind_window(pid,hwnd); root,_version=backend.instances[instance]
    headers=root.descendants(class_name=PRIVATE_HEADER)
    if len(headers)!=1 or root.descendants(class_name='mmui::ChatTitleBarChatRoomView'):
        raise ConsoleError('contact_private_header_required')
    info=headers[0].element_info
    header_id=list(info.runtime_id)
    if info.process_id!=pid or info.class_name!=PRIVATE_HEADER or not header_id:
        raise ConsoleError('contact_private_header_identity_changed')
    current=backend.target_control_probe(pid,hwnd,[label])
    if (current.get('identity_verified') is not True or current.get('target_index')!=0 or
        current.get('selected_row_verified') is not True or current.get('draft_empty') is not True or
        current.get('value_readonly') is not False or not current.get('input_runtime_id') or
        not current.get('session_runtime_id')):
        error=ConsoleError('contact_target_or_empty_draft_unverified')
        error.target_metadata={key:current[key] for key in (
            'identity_verified','target_index','selected_row_verified','draft_empty','value_readonly',
            'input_runtime_id','session_runtime_id','input_label_length','header_matches','selected_target_rows')
            if key in current}
        raise error
    return root,{**current,'private_header_class':PRIVATE_HEADER,'private_header_runtime_id':header_id}


def inspect_recovery_card(backend,pid,hwnd,label,expected,target,checkpoint):
    """Read a current exact-ID card before the selected chat's draft.

    The caller verifies the original selected row at each checkpoint. No
    history, sender/contact grants or immutable baseline records are changed.
    """
    instance=backend.bind_window(pid,hwnd);root,_version=backend.instances[instance]
    def structure():
        if (root.handle!=hwnd or root.element_info.process_id!=pid or
            root.element_info.class_name!='mmui::MainWindow'):
            raise ConsoleError('recovery_root_changed')
        headers=root.descendants(class_name=PRIVATE_HEADER)
        if len(headers)!=1 or root.descendants(class_name='mmui::ChatTitleBarChatRoomView'):
            raise ConsoleError('recovery_private_header_required')
        info=headers[0].element_info
        if (info.process_id!=pid or info.class_name!=PRIVATE_HEADER or
            list(info.runtime_id)!=target.get('private_header_runtime_id')):
            raise ConsoleError('recovery_private_header_changed')
    checkpoint();structure()
    profile=backend._own_profile_popup(pid,hwnd,root)
    first=read_card(profile,pid,expected)
    checkpoint();structure()
    _root,current=require_target(backend,pid,hwnd,label)
    from wechat_agent_bridge.adapters.focus_diagnostics import check_draft_target
    check_draft_target(target,current)
    if current['private_header_runtime_id']!=target.get('private_header_runtime_id'):
        raise ConsoleError('recovery_private_header_changed')
    checkpoint();structure()
    final_profile=backend._own_profile_popup(pid,hwnd,root)
    second=read_card(final_profile,pid,expected)
    if first!=second:raise ConsoleError('recovery_contact_card_changed')
    checkpoint();structure()
    return dict(recipient_exact_id_reverified=True,current_contact_identity=first,
        current_target_controls=current,identity_source='current_uia_contact_card_during_selection_recovery')


def require_card_context(backend,pid,hwnd,label):
    """Selected row/header metadata only; no draft, text, history or action."""
    instance=backend.bind_window(pid,hwnd);root,_version=backend.instances[instance]
    info=root.element_info
    if root.handle!=hwnd or info.process_id!=pid or info.class_name!='mmui::MainWindow':
        raise ConsoleError('contact_root_identity_changed')
    headers=root.descendants(class_name=PRIVATE_HEADER)
    if len(headers)!=1 or root.descendants(class_name='mmui::ChatTitleBarChatRoomView'):
        raise ConsoleError('contact_private_header_required')
    header=headers[0].element_info
    if header.process_id!=pid or header.class_name!=PRIVATE_HEADER or not header.runtime_id:
        raise ConsoleError('contact_private_header_identity_changed')
    rows=[r for r in root.descendants(control_type='ListItem')
          if r.element_info.automation_id=='session_item_'+label]
    if (len(rows)!=1 or rows[0].element_info.process_id!=pid or not rows[0].is_selected() or
            not rows[0].element_info.runtime_id):
        raise ConsoleError('contact_target_selection_unverified')
    return root,dict(header_runtime_id=list(header.runtime_id),
                     session_runtime_id=list(rows[0].element_info.runtime_id))


def inspect(backend,pid,hwnd,label,expected,checkpoint=lambda:None):
    """Read the card explicitly opened by CUA from this selected private chat.

    The selector shared with own-profile reading finds a unique popup only.
    Never call verify_own_profile: a contact cannot become the sender account.
    """
    checkpoint()
    root,context=require_card_context(backend,pid,hwnd,label)
    profile=backend._own_profile_popup(pid,hwnd,root)
    first=read_card(profile,pid,expected)
    checkpoint()
    second=read_card(profile,pid,expected)
    if first!=second:raise ConsoleError('contact_profile_or_target_changed')
    checkpoint()
    _root,current_context=require_card_context(backend,pid,hwnd,label)
    if current_context!=context:raise ConsoleError('contact_profile_or_target_changed')
    checkpoint()
    # Exact recipient has now been read twice. Only now may the draft be read.
    _root,before=require_target(backend,pid,hwnd,label)
    if (before['session_runtime_id']!=context['session_runtime_id'] or
            before['private_header_runtime_id']!=context['header_runtime_id']):
        raise ConsoleError('contact_profile_or_target_changed')
    checkpoint()
    _root,after=require_target(backend,pid,hwnd,label)
    final_profile=backend._own_profile_popup(pid,hwnd,root)
    final=read_card(final_profile,pid,expected)
    checkpoint()
    if (first!=final or before!=after or final_profile.handle!=profile.handle or
        tuple(final_profile.element_info.runtime_id)!=tuple(first['profile_runtime_id'])):
        raise ConsoleError('contact_profile_or_target_changed')
    return dict(state='contact_profile_verified',identity=first,target_controls=before,
        label_sha256=sha(label),source='uia_contact_card_exact_id_after_cua_navigation',
        account_binding_modified=False,messages_read=False,send_invoked=False,api_called=False,
        persistent_conversation_id_verified=False)


def validate_visual_evidence(report,fingerprint,expected,label):
    if (report.get('kind')!='development_cua_private_identity' or report.get('identity_verified') is not True or
        report.get('source')!='computer_use_profile_card' or report.get('window_id')!=fingerprint['hwnd'] or
        report.get('binding_reference')!=fingerprint or report.get('expected_id_sha256')!=sha(expected) or
        report.get('label_sha256')!=sha(label) or report.get('send_invoked') is not False or
        report.get('api_called') is not False):
        raise ConsoleError('contact_visual_scope_evidence_required')


def private_baseline(verified,collect,protect,reverify):
    """Capture only after exact identity, recheck before saving; never upload."""
    if (verified.get('state')!='contact_profile_verified' or
        verified.get('identity',{}).get('exact_id_verified') is not True or
        verified.get('account_binding_modified') is not False):
        raise ConsoleError('private_baseline_exact_identity_required')
    value=collect()
    if reverify()!=verified: raise ConsoleError('private_baseline_identity_changed')
    if value.get('historical') is not True or value.get('coverage')!='accessible_loaded_tail_only':
        raise ConsoleError('private_baseline_coverage_unknown')
    from wechat_agent_bridge.console_store import digest
    hashes=[digest(str(row['runtime_id']),row['label']) for row in value['rows']]
    result=protect(value)
    return dict(**result,baseline_event_hashes=hashes,upload_eligible=False,
        baseline_source='exact_private_identity_initial_history',reverified_before_save=True)


def unchanged_readonly(before,after):
    """A profile may be foreground for this read-only development inspection."""
    if (before.locked or after.locked or before.foreground!=after.foreground or
        before.focus!=after.focus or before.clipboard_sequence!=after.clipboard_sequence or
        before.target_active!=after.target_active):
        raise ConsoleError('contact_readonly_desktop_changed')
