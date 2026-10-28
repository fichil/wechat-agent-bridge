"""Candidate semantic UIA reader; no untested live sending profile is enabled.

Only the selected Weixin process is searched. Never dumps the desktop tree,
reads an account database, inspects other applications, or changes the clipboard.
"""

import ctypes
from ctypes import wintypes
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import re

from ..console_models import Account, ChatMessage, Conversation, ConsoleError


# An entry requires synthetic contract tests AND recorded exact-build live
# target, draft, send and readback evidence. Discovery never populates this.
VERIFIED_SEND_PROFILES: frozenset[str] = frozenset()
VERIFIED_READ_PROFILES: frozenset[str] = frozenset()


def identifier(*parts):
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:32]


class WindowsUIA:
    def __init__(self):
        self.instances = {}
        self.accounts = {}
        self.chat_rows = {}
        self.profile_verified_at = 0
        self.own_account_discovery = None
        self.read_only_account_discovery = None

    def discover_read_only_accounts(self):
        from .readonly_account import ReadOnlyAccountDiscovery
        from .own_account_windows import WindowsAccountMetadata
        if self.read_only_account_discovery is None:
            self.read_only_account_discovery = ReadOnlyAccountDiscovery(WindowsAccountMetadata())
        return self.read_only_account_discovery.discover()

    def discover_own_accounts(self, checkpoint=lambda receipt: None):
        from .account_discovery import OwnAccountDiscovery
        from .own_account_windows import WindowsOwnAccountBackend
        if self.own_account_discovery is None:
            self.own_account_discovery = OwnAccountDiscovery(WindowsOwnAccountBackend(checkpoint))
        else:
            self.own_account_discovery.backend.checkpoint = checkpoint
        return self.own_account_discovery.discover()

    @staticmethod
    def _processes():
        import win32api
        import win32process
        candidates = []
        for pid in win32process.EnumProcesses():
            handle = None
            try:
                handle = win32api.OpenProcess(0x1000, False, pid)
                # QueryFullProcessImageName does not read process memory.
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
                size = wintypes.DWORD(32768)
                path = ctypes.create_unicode_buffer(size.value)
                if not kernel.QueryFullProcessImageNameW(int(handle), 0, path, ctypes.byref(size)):
                    continue
                if path.value.rsplit("\\", 1)[-1].lower() not in ("weixin.exe", "wechat.exe"):
                    continue
                info = win32api.GetFileVersionInfo(path.value, "\\")
                ms, ls = info["FileVersionMS"], info["FileVersionLS"]
                version = f"{ms>>16}.{ms&65535}.{ls>>16}.{ls&65535}"
                candidates.append((pid, path.value, version))
            except Exception:
                continue
            finally:
                if handle:
                    handle.Close()
        return candidates

    def discover(self):
        from pywinauto import Desktop
        found = {}
        for pid, path, version in self._processes():
            for window in Desktop(backend="uia").windows(process=pid, visible_only=True):
                # Do not read window text: it may be a chat title.
                instance = f"{pid}:{window.handle}"
                found[instance] = (window, version)
        self.instances = found
        result, known_instances = [], set()
        for account in self.accounts.values():
            if account.instance_id in found:
                result.append(asdict(account)); known_instances.add(account.instance_id)
        for instance, (_, version) in found.items():
            if instance not in known_instances:
                result.append(asdict(Account("candidate-"+identifier(instance), "待核验微信窗口", instance_id=instance,
                                             version=version, identity_certain=False)))
        return result

    def structure(self, instance_id):
        """Non-content diagnostic: bounded immediate child control types only."""
        if instance_id not in self.instances:
            raise ConsoleError("client_instance_missing")
        root, version = self.instances[instance_id]
        def describe(control):
            info = control.element_info
            return {"type": info.control_type, "class": info.class_name, "automation_id": info.automation_id}
        return {"version": version, "root": describe(root),
                "children": [{**describe(child), "children": [describe(item) for item in child.children()[:30]]}
                             for child in root.children()[:30]]}

    def bind_window(self, pid, hwnd):
        """Bind only a user-selected window, without enumerating chat content."""
        from pywinauto import Desktop
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        actual = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(actual))
        candidates = [v for p, _, v in self._processes() if p == pid]
        if actual.value != pid or len(candidates) != 1:
            raise ConsoleError("client_instance_mismatch")
        root = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
        instance = f"{pid}:{hwnd}"
        self.instances = {instance: (root, candidates[0])}
        return instance

    def background_structure(self, pid, hwnd):
        """Bounded metadata; no Name/Value/text/preview properties requested."""
        instance = self.bind_window(pid, hwnd)
        root, version = self.instances[instance]
        queue, records, depth_limited = [(root, 0)], [], False
        patterns = ("SelectionItem", "Value", "Invoke", "Text", "Scroll", "RangeValue")
        while queue and len(records) < 600:
            control, depth = queue.pop(0)
            info = control.element_info
            available = []
            for pattern in patterns:
                try:
                    from pywinauto.uia_defines import get_elem_interface
                    get_elem_interface(info.element, pattern)
                    available.append(pattern)
                except Exception:
                    pass
            aid = info.automation_id
            # Some AutomationIds embed contact display names. Diagnostic logs do not.
            if aid.startswith(("session_item_", "search_item_")):
                aid = aid.split("_item_")[0]+"_item_<redacted>"
            records.append({"depth": depth, "type": info.control_type, "class": info.class_name,
                            "automation_id": aid, "patterns": available})
            children = control.children()
            if depth < 24:
                queue.extend((child, depth+1) for child in children[:100])
                depth_limited |= len(children) > 100
            else:
                depth_limited |= bool(children)
        exposed = any(r["type"] in ("Edit", "List", "ListItem", "Document") for r in records)
        from pywinauto import Desktop
        windows = [{"hwnd": w.handle, "class": w.element_info.class_name} for w in
                   Desktop(backend="uia").windows(process=pid, visible_only=True)]
        return {"version": version, "controls_exposed": exposed, "truncated": bool(queue) or depth_limited,
                "windows": windows, "controls": records,
                "background_read": "unverified", "selection": "unverified", "set_value": "unverified",
                "invoke_send": "unverified", "send_readback": "unverified", "attachments": "unsupported"}

    @staticmethod
    def _unique(root, control_type, names):
        matched = []
        for name in names:
            matched.extend(root.descendants(control_type=control_type, title=name))
        unique = {tuple(item.element_info.runtime_id): item for item in matched}
        if len(unique) != 1:
            raise ConsoleError("uia_control_missing_or_ambiguous")
        return next(iter(unique.values()))

    def semantic_candidates(self, pid, hwnd):
        """Only selected-window input metadata; no value or underlying model calls."""
        from .semantic_probe import inspect_patterns, candidate_decision, inspect_legacy_origin
        instance = self.bind_window(pid, hwnd)
        root, version = self.instances[instance]
        inputs = [c for c in root.descendants(control_type="Edit")
                  if c.element_info.automation_id == "chat_input_field"]
        if len(inputs) != 1:
            raise ConsoleError("background_input_ambiguous")
        element = inputs[0].element_info.element
        patterns = inspect_patterns(element)
        legacy_origin = {"status": "not_checked"}
        if patterns["LegacyIAccessible"]["status"] == "available":
            try:
                from pywinauto.uia_defines import get_elem_interface
                legacy_origin = inspect_legacy_origin(get_elem_interface(element, "LegacyIAccessible"))
            except Exception:
                legacy_origin = {"status": "probe_failed"}
        metadata = {}
        for name, prop in (("framework", 30024), ("provider", 30107), ("native_hwnd", 30020)):
            try:
                value = element.GetCurrentPropertyValue(prop)
                metadata[name] = value if isinstance(value, (str, int)) else None
            except Exception:
                metadata[name] = None
        native_pid = wintypes.DWORD()
        if metadata.get("native_hwnd"):
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user.GetWindowThreadProcessId(metadata["native_hwnd"], ctypes.byref(native_pid))
        metadata["native_window_matches_process"] = native_pid.value == pid if native_pid.value else None
        return {"version": version, "metadata": metadata, "patterns": patterns, "legacy_origin": legacy_origin,
                "decision": candidate_decision(patterns, legacy_origin), "write_attempted": False,
                "Value": "blocked_focus_changed", "Text": "no_text_mutation_interface"}

    def probe_account(self, instance_id):
        """User explicitly chooses this window and opens their own profile panel."""
        if instance_id not in self.instances:
            raise ConsoleError("client_instance_missing")
        root, version = self.instances[instance_id]
        panel = self._unique(root, "Pane", ("个人信息", "我的资料", "My Profile"))
        values = [item.window_text() for item in panel.descendants(control_type="Text")[:30]]
        ids = {match.group(1) for text in values if (match := re.fullmatch(r"(?:微信号|WeChat ID)\s*[:：]\s*([A-Za-z0-9_\-]{6,64})", text))}
        if len(ids) != 1:
            raise ConsoleError("account_identity_uncertain_open_own_profile")
        wechat_id = next(iter(ids))
        from .account_discovery import profile_fields
        try:
            nickname = profile_fields(values)['name']
        except ConsoleError:
            nickname = '昵称未获取'
        account = Account("wechat-"+identifier(wechat_id), nickname, wechat_id, instance_id, version, verified=False)
        self.accounts[account.id] = account
        return asdict(account)

    def profile_window_metadata(self, pid, hwnd):
        """Only window/class metadata while the user opens their own profile."""
        from pywinauto import Desktop
        self.bind_window(pid, hwnd)
        result = []
        for window in Desktop(backend="uia").windows(process=pid, visible_only=True):
            queue, classes = [(window, 0)], set()
            visited = 0
            while queue and visited < 800:
                control, depth = queue.pop(0); visited += 1
                cls = control.element_info.class_name
                if cls:
                    classes.add(cls)
                # Only inspect types here, never text in chat or contact cards.
                if depth < 40:
                    queue.extend((child, depth+1) for child in control.children()[:100])
            result.append({"hwnd": window.handle, "class": window.element_info.class_name,
                           "classes": sorted(classes), "truncated": bool(queue)})
        return result

    @staticmethod
    def _visible_process_window_handles(pid):
        """Native HWND metadata only, including windows on another task desktop."""
        if type(pid) is not int or not 0 < pid < 2**32:
            raise ConsoleError("account_window_identity_ambiguous")
        try:
            user = ctypes.WinDLL("user32", use_last_error=True)
            callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user.GetWindowThreadProcessId.restype = wintypes.DWORD
            user.IsWindowVisible.argtypes, user.IsWindowVisible.restype = [wintypes.HWND], wintypes.BOOL
            user.EnumWindows.argtypes, user.EnumWindows.restype = [callback_type, wintypes.LPARAM], wintypes.BOOL
            handles, failed = [], []

            def collect(handle, _argument):
                try:
                    owner = wintypes.DWORD()
                    thread = user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
                    # Never request Name, text, children or a UIA object here.
                    if thread and owner.value == pid and user.IsWindowVisible(handle):
                        value = int(handle)
                        if value not in handles:
                            if len(handles) >= 64:
                                raise ValueError
                            handles.append(value)
                    return True
                except BaseException:
                    failed.append(True)
                    return False

            callback = callback_type(collect)
            if not user.EnumWindows(callback, 0) or failed:
                raise ValueError
            return tuple(handles)
        except Exception:
            raise ConsoleError("account_window_enumeration_failed") from None

    @staticmethod
    def _profile_identity_class(window, pid, hwnd):
        """Validate the direct HWND binding before requesting its UIA class."""
        try:
            info = window.element_info
            if window.handle != hwnd or info.process_id != pid:
                raise ValueError
            value = info.class_name
            if not isinstance(value, str) or not value:
                raise ValueError
            return value
        except Exception:
            raise ConsoleError("account_window_identity_ambiguous") from None

    def _own_profile_popup(self, pid, hwnd, root):
        from pywinauto import Desktop
        if self._profile_identity_class(root, pid, hwnd) != "mmui::MainWindow":
            raise ConsoleError("account_window_identity_ambiguous")
        handles = self._visible_process_window_handles(pid)
        if hwnd not in handles:
            raise ConsoleError("account_window_identity_ambiguous")
        desktop, profiles, mains = Desktop(backend="uia"), [], []
        for handle in handles:
            try:
                window = root if handle == hwnd else desktop.window(handle=handle).wrapper_object()
            except Exception:
                # A vanished/unreadable candidate cannot establish uniqueness.
                raise ConsoleError("account_window_identity_ambiguous") from None
            classname = self._profile_identity_class(window, pid, handle)
            if classname == "mmui::MainWindow":
                mains.append(handle)
            elif classname == "mmui::ProfileUniquePop":
                profiles.append(window)
        if mains != [hwnd]:
            raise ConsoleError("account_window_identity_ambiguous")
        if not profiles:
            # Existing callers may wait for the user to open their own profile.
            raise ConsoleError("account_identity_uncertain_open_own_profile")
        if len(profiles) != 1:
            raise ConsoleError("account_profile_identity_ambiguous")
        return profiles[0]

    def read_own_profile(self, pid, hwnd, expected_wechat_id):
        """Enrollment observation only; never publishes a verified account."""
        return self.verify_own_profile(pid,hwnd,expected_wechat_id,save=False)

    def verify_own_profile(self, pid, hwnd, expected_wechat_id, *, save=True):
        """Read only the own-profile popup the user explicitly opened for binding."""
        instance = self.bind_window(pid, hwnd)
        root, version = self.instances[instance]
        profile = self._own_profile_popup(pid, hwnd, root)
        values = []
        queue, visited = [profile], 0
        while queue and visited < 400:
            item = queue.pop(0)
            visited += 1
            # Scope is the explicit own-profile popup, never a chat subtree.
            value = item.window_text().strip()
            if value:
                values.append(value)
            children = item.children()
            if len(children) > 30:
                # Truncating here could hide a conflicting labeled identity
                # while the partial tree still appears successfully drained.
                raise ConsoleError("account_profile_tree_incomplete")
            queue.extend(children)
        if queue:
            raise ConsoleError("account_profile_tree_incomplete")
        joined = "\n".join(values)
        labeled = re.findall(r"微信号\s*[:：]?\s*([A-Za-z0-9_-]{6,64})", joined)
        if set(labeled) != {expected_wechat_id}:
            raise ConsoleError("account_profile_id_mismatch")
        from .account_discovery import profile_fields
        try:
            nickname = profile_fields(values)['name']
        except ConsoleError:
            nickname = '昵称未获取'
        account = Account("wechat-"+identifier(expected_wechat_id), nickname, expected_wechat_id,
                          instance, version, identity_source="uia_own_profile_exact_id_user_selected_window")
        if save:
            self.accounts = {account.id: account}
            import time
            self.profile_verified_at = time.monotonic()
        return asdict(account)

    def current_target_metadata(self, account_id, allowed_names):
        """No chat bodies: only compare input title and exact selected row."""
        account = self.accounts.get(account_id)
        if not account:
            raise ConsoleError("account_identity_uncertain")
        root, _ = self.instances[account.instance_id]
        return self._target_metadata(root, allowed_names)

    def target_control_probe(self, pid, hwnd, allowed_names):
        """Only title/selection/draft metadata; does not grant account read/send."""
        instance = self.bind_window(pid, hwnd)
        root, _ = self.instances[instance]
        return self._target_metadata(root, allowed_names)

    def _target_metadata(self, root, allowed_names):
        inputs = [c for c in root.descendants(control_type="Edit") if c.element_info.automation_id == "chat_input_field"]
        if len(inputs) != 1:
            raise ConsoleError("background_input_ambiguous")
        # window_text() may invoke TextPattern.GetText(-1), exposing a draft
        # before the conversation is known. Only UIA Name is an input label.
        input_name = getattr(inputs[0].element_info, "name", "")
        if input_name is None:
            input_name = ""
        if not isinstance(input_name, str):
            raise ConsoleError("background_input_name_unavailable")
        name = re.sub(r"(?:(?:长按|按住)鼠标\s*)?语音输入(?:文字)?$", "", input_name).strip()
        if name not in allowed_names:
            # Identity diagnostics only, not message/draft values. Report whether
            # the label contains an authorized target, not an unrelated name.
            titles, property_names, header_counts = [], [], []
            for cls in ("mmui::ChatTitleBarChatRoomView", "mmui::ChatTitleBarChatSingleView"):
                headers=root.descendants(class_name=cls)
                header_counts.append(len(headers))
                for header in headers:
                    nodes=[header]+header.descendants()
                    titles.extend(c.window_text().strip() for c in nodes)
                    # Diagnostic comparison only. window_text() may use Text
                    # instead of Name; neither field alone grants identity.
                    for control in nodes[:128]:
                        value=control.element_info.name
                        if isinstance(value,str):property_names.append(value.strip())
            header_matches = [any(re.fullmatch(re.escape(n)+r"(?:\s*[（(]\d+[）)])?", label) for label in titles) for n in allowed_names]
            header_name_matches = [any(re.fullmatch(re.escape(n)+r"(?:\s*[（(]\d+[）)])?", label) for label in property_names) for n in allowed_names]
            selected, candidate_counts = [], []
            session_rows=root.descendants(control_type="ListItem")
            for target in allowed_names:
                candidates = [c for c in session_rows if c.element_info.automation_id == "session_item_"+target]
                candidate_counts.append(len(candidates))
                selected.append(len(candidates) == 1 and bool(candidates[0].is_selected()))
            matches = [i for i in range(len(allowed_names)) if header_matches[i] and selected[i]]
            if len(matches) != 1:
                return {"identity_verified": False, "input_label_length": len(name), "header_matches": header_matches,
                        "selected_target_rows": selected, "reason": "acceptance_target_not_bound",
                        "header_class_counts":header_counts,"header_name_matches":header_name_matches,
                        "session_candidate_counts":candidate_counts,"list_item_count":len(session_rows)}
            name = allowed_names[matches[0]]
        rows = [c for c in root.descendants(control_type="ListItem") if c.element_info.automation_id == "session_item_"+name]
        if len(rows) != 1 or not rows[0].is_selected():
            raise ConsoleError("conversation_identity_uncertain")
        from pywinauto.uia_defines import get_elem_interface
        try:
            draft = inputs[0].get_value()
            draft_empty = draft == ""
            from ..acceptance import FIXED_TEXT
            draft_is_probe = draft == FIXED_TEXT
        except Exception:
            draft_empty = None
            draft_is_probe = None
        # UIA may advertise Value for read-only controls; record IsReadOnly too.
        value = get_elem_interface(inputs[0].element_info.element, "Value")
        buttons = []
        containers = root.descendants(class_name="mmui::ChatInputView")
        if len(containers) != 1:
            raise ConsoleError("background_input_container_ambiguous")
        input_parent = containers[0]
        for button in input_parent.descendants(control_type="Button"):
            label = button.window_text()
            if re.fullmatch(r"发送(?:\s*\([Ss]\))?|Send", label):
                buttons.append(button)
        return {"target_index": allowed_names.index(name), "identity_verified": True, "selected_row_verified": True,
                "session_runtime_id": list(rows[0].element_info.runtime_id),
                "input_runtime_id": list(inputs[0].element_info.runtime_id),
                "draft_empty": draft_empty, "draft_is_fixed_probe": draft_is_probe, "value_readonly": bool(value.CurrentIsReadOnly),
                "send_button_count": len(buttons), "readback": "unverified",
                "conversation_identity_source": "exact_selected_row_and_title_same_process_lifetime"}

    def probe_background_draft(self, account_id, allowed_names, text):
        """Explicit acceptance experiment: SetValue/readback/clear, NEVER Invoke.

        Requires a freshly verified own profile in this same worker lifetime.
        This does not enable a production send profile or consume a send slot.
        """
        import time
        from .background import snapshot, unchanged
        from ..acceptance import FIXED_TEXT
        if text != FIXED_TEXT or time.monotonic()-self.profile_verified_at > 60:
            raise ConsoleError("fresh_profile_verification_required")
        account = self.accounts.get(account_id)
        if not account:
            raise ConsoleError("account_identity_uncertain")
        pid, hwnd = map(int, account.instance_id.split(":"))
        root, _ = self.instances[account.instance_id]
        before = snapshot(pid)
        unchanged(before, before)
        target = self._target_metadata(root, allowed_names)
        if not target.get("identity_verified") or target["target_index"] != 0:
            raise ConsoleError("acceptance_fixed_requires_verified_group")
        if target["draft_empty"] is not True or target["value_readonly"]:
            raise ConsoleError("existing_draft_or_unknown")
        edits = [c for c in root.descendants(control_type="Edit") if c.element_info.automation_id == "chat_input_field"]
        if len(edits) != 1:
            raise ConsoleError("background_input_ambiguous")
        edit = edits[0]
        def check():
            unchanged(before, snapshot(pid))
            current = self._target_metadata(root, allowed_names)
            if (current.get("session_runtime_id") != target["session_runtime_id"]
                    or current.get("input_runtime_id") != target["input_runtime_id"]):
                raise ConsoleError("target_mismatch")
        result = {"set_value": "unverified", "set_value_invoked": False, "send_invoked": False, "draft_restored": False}
        try:
            check()
            if edit.get_value() != "":
                raise ConsoleError("existing_draft_or_unknown")
            result["set_value_invoked"] = True
            edit.iface_value.SetValue(text)  # Direct COM only. No library fallback.
            check()
            result["set_value"] = "passed" if edit.get_value() == text else "unsupported_no_exact_readback"
        except ConsoleError as exc:
            result["reason"] = str(exc)
        except Exception:
            result["reason"] = "background_set_value_failed"
        finally:
            try:
                check()
                if edit.get_value() == text:
                    edit.iface_value.SetValue("")
                    check()
                result["draft_restored"] = edit.get_value() == ""
                result["focus_and_clipboard_unchanged"] = True
            except Exception as exc:
                result["focus_and_clipboard_unchanged"] = False
                result["cleanup_reason"] = str(exc) if isinstance(exc, ConsoleError) else "cleanup_uncertain"
        return result

    def _root(self, account_id):
        account = self.accounts.get(account_id)
        if not account or account.instance_id not in self.instances:
            raise ConsoleError("account_identity_uncertain")
        if account.version not in VERIFIED_READ_PROFILES:
            raise ConsoleError("client_read_profile_unverified")
        root, version = self.instances[account.instance_id]
        if not root.is_visible():
            raise ConsoleError("client_disconnected")
        if version != account.version:
            raise ConsoleError("client_version_changed")
        return root, account

    @staticmethod
    def _idle():
        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user.OpenInputDesktop.restype = wintypes.HANDLE
        user.CloseDesktop.argtypes = [wintypes.HANDLE]
        desktop = user.OpenInputDesktop(0, False, 0x0100)
        if not desktop:
            raise ConsoleError("desktop_locked_or_unavailable")
        user.CloseDesktop(desktop)
        info = LastInput(ctypes.sizeof(LastInput), 0)
        user.GetLastInputInfo(ctypes.byref(info))
        elapsed = (kernel.GetTickCount()-info.dwTime) & 0xFFFFFFFF
        if elapsed < 3000:
            raise ConsoleError("human_takeover")

    def bound_group_capture(self, pid, hwnd, names, expected, cursor=None):
        """Read the already verified selected group, never enumerate other chats."""
        from .uia_client import BackgroundUIAClient
        from .bound_read import collect_labels, check_read_target
        before = self.current_group_target(pid, hwnd, names)
        check_read_target(expected, before)
        if before.get('group_header_runtime_id') != expected.get('group_header_runtime_id'):
            raise ConsoleError('bound_console_group_changed')
        client = BackgroundUIAClient()
        from comtypes.gen import UIAutomationClient as uia
        root = client.element_from_handle(hwnd)
        if root.CurrentProcessId != pid or root.CurrentNativeWindowHandle != hwnd:
            raise ConsoleError('inspection_root_mismatch')
        value = collect_labels(client._client, root, uia, pid, cursor)
        after = self.current_group_target(pid, hwnd, names)
        check_read_target(before, after)
        client.require_background_mode()
        return value

    def current_group_target(self, pid, hwnd, names):
        instance = self.bind_window(pid, hwnd)
        root, _ = self.instances[instance]
        headers = root.descendants(class_name='mmui::ChatTitleBarChatRoomView')
        if len(headers) != 1 or root.descendants(class_name='mmui::ChatTitleBarChatSingleView'):
            raise ConsoleError('bound_console_current_group_required')
        info = headers[0].element_info
        if info.process_id != pid:
            raise ConsoleError('bound_console_group_changed')
        result = self._target_metadata(root, names)
        result['group_header_runtime_id'] = list(info.runtime_id)
        return result

    def private_catalog_metadata(self, pid, hwnd):
        from .uia_client import BackgroundUIAClient
        from .private_catalog import collect_candidates
        from comtypes.gen import UIAutomationClient as uia
        return collect_candidates(BackgroundUIAClient(), uia, pid, hwnd)

    def detached_group_metadata(self, pid, hwnd, root_id, name):
        from .uia_client import BackgroundUIAClient
        from .detached_read import metadata
        from comtypes.gen import UIAutomationClient as uia
        return metadata(BackgroundUIAClient(), uia, pid, hwnd, root_id, name)

    def detached_group_capture(self, pid, hwnd, root_id, name, expected, cursor=None):
        from .uia_client import BackgroundUIAClient
        from .detached_read import metadata, check_metadata
        from .bound_read import collect_labels
        from comtypes.gen import UIAutomationClient as uia
        client = BackgroundUIAClient()
        before = metadata(client, uia, pid, hwnd, root_id, name)
        check_metadata(expected, before)
        value = collect_labels(client._client, client.element_from_handle(hwnd), uia, pid, cursor)
        check_metadata(before, metadata(client, uia, pid, hwnd, root_id, name))
        client.require_background_mode()
        return value

    def conversations(self, account_id):
        root, account = self._root(account_id)
        listing = self._unique(root, "List", ("会话", "会话列表", "聊天", "Chats"))
        chats = []
        for row in listing.children(control_type="ListItem")[:200]:
            runtime_id = tuple(row.element_info.runtime_id)
            # Runtime ids are scoped to this window lifetime, never reused as a
            # persistent contact identifier across process restarts.
            chat_id = "session-"+identifier(account.instance_id, runtime_id)
            labels = row.children(control_type="Text")
            name = labels[0].window_text() if labels else row.window_text().split("\n")[0]
            if not name:
                continue
            self.chat_rows[(account_id, chat_id)] = row
            chats.append(asdict(Conversation(account_id, chat_id, name[:200], "unknown", identity_certain=True)))
        return chats

    def inspect(self, account_id, conversation_id):
        root, account = self._root(account_id)
        self._idle()
        row = self.chat_rows.get((account_id, conversation_id))
        if row is None:
            raise ConsoleError("target_mismatch")
        # SelectionItem operates on the exact UI element, not a nickname lookup.
        if not row.is_selected():
            row.select()
        if not row.is_selected():
            raise ConsoleError("target_mismatch")
        edits = root.descendants(control_type="Edit", title="输入") + root.descendants(control_type="Edit", title="消息输入框")
        has_draft = None
        if len(edits) == 1:
            try:
                has_draft = bool(edits[0].get_value())
            except Exception:
                pass
        return {"account_id": account_id, "conversation_id": conversation_id, "connected": True,
                "identity_certain": True, "has_draft": has_draft, "user_active": False}

    def messages(self, account_id, conversation_id, cursor=None):
        self.inspect(account_id, conversation_id)
        root, account = self._root(account_id)
        listing = self._unique(root, "List", ("消息", "消息列表", "Messages"))
        if cursor is not None:
            raise ConsoleError("history_loading_unverified")
        result = []
        for row in listing.children(control_type="ListItem")[-50:]:
            text = row.window_text()
            if not text:
                continue
            message_id = identifier(account.instance_id, conversation_id, tuple(row.element_info.runtime_id), text)
            # This candidate profile has no verified sender/@/attachment schema.
            # Preserve readable content, but never feed it into auto-reply triggers.
            message = ChatMessage(account_id, conversation_id, message_id, "发送者未核验", text[:16000],
                                  "", historical=True, mentions_self=None)
            result.append(message.to_dict())
        return {"messages": result, "cursor": None, "complete": False,
                "limitation": "candidate_profile: visible_text_only; sender_mentions_media_and_cross_restart_identity_unverified"}

    def attachment(self, *args):
        raise ConsoleError("client_attachment_acquisition_unverified")

    def send(self, account_id, conversation_id, text, request_id):
        _, account = self._root(account_id)
        if account.version not in VERIFIED_SEND_PROFILES:
            return {"status": "failed", "reason": "exact_build_send_profile_unverified", "attempted": False}
        # Promotion requires an implementation whose target/draft checks and UI
        # readback are validated for that build. Never fall back to send_keys.
        return {"status": "failed", "reason": "send_profile_unavailable", "attempted": False}
