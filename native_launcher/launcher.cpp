// Small native Windows UI. Python management runs in hidden child processes.
#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commdlg.h>
#include <commctrl.h>
#include <shellapi.h>
#include <filesystem>
#include <string>
#include <vector>
#include <thread>
#include <memory>
#include <sstream>
#include <cstdint>
#include <cwctype>

static HWND window, originBox, usernameBox, passwordBox, confirmBox, outputBox, privateBox;
static HFONT font;
static bool busy = false;
static bool updatePending = false;
static bool updateRequest = false;
static std::wstring targetVersion, targetRelease;
static std::wstring root;
static bool background = false, trayPresent = false;
static UINT taskbarCreated, exitForUpdate, showConsole;
static NOTIFYICONDATAW tray{};
static std::vector<HWND> controls;
constexpr UINT Finished = WM_APP + 1;
constexpr UINT TrayMessage = WM_APP + 2;
constexpr wchar_t ProtocolProperty[] = L"WeChatAgentBridge.LauncherProtocol";
struct Reply { std::wstring text; };

static void reveal() {
    ShowWindow(window, SW_RESTORE);
    SetForegroundWindow(window);
}
static bool addTray() {
    tray = {}; tray.cbSize = sizeof(tray); tray.hWnd = window; tray.uID = 1;
    tray.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    tray.uCallbackMessage = TrayMessage;
    tray.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
    wcscpy_s(tray.szTip, L"微信 Agent Bridge · 双击打开控制台");
    trayPresent = Shell_NotifyIconW(NIM_ADD, &tray) != FALSE;
    // Duplicate TaskbarCreated notifications must not remove a working icon.
    if (!trayPresent) trayPresent = Shell_NotifyIconW(NIM_MODIFY, &tray) != FALSE;
    if (trayPresent) {
        tray.uVersion = NOTIFYICON_VERSION_4; Shell_NotifyIconW(NIM_SETVERSION, &tray);
        KillTimer(window,3);
    } else SetTimer(window,3,5000,nullptr); // Explorer may still be starting.
    return trayPresent;
}
static void hideToTray() {
    // Never hide the only recovery entry if Explorer cannot host the icon.
    if (trayPresent || addTray()) ShowWindow(window, SW_HIDE);
    else reveal();
}
static bool mayExit() {
    return !busy || updateRequest;
}

static std::wstring readText(HWND h) {
    int n = GetWindowTextLengthW(h);
    std::wstring s(n + 1, L'\0');
    GetWindowTextW(h, s.data(), n + 1); s.resize(n); return s;
}
static std::string utf8(const std::wstring& s) {
    int n = WideCharToMultiByte(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0, nullptr, nullptr);
    std::string v(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, s.data(), (int)s.size(), v.data(), n, nullptr, nullptr); return v;
}
static std::wstring wide(const std::string& s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring v(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), v.data(), n); return v;
}
static std::string quote(const std::wstring& s) {
    std::string out = "\"";
    for (unsigned char c : utf8(s)) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if (c < 32) {
            const char* hex = "0123456789abcdef";
            out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15];
        } else out += c;
    }
    return out + '"';
}
static HWND make(const wchar_t* cls, const wchar_t* text, DWORD style, int x, int y, int w, int h, int id=0) {
    HWND hnd = CreateWindowExW(cls == std::wstring(L"EDIT") ? WS_EX_CLIENTEDGE : 0, cls, text,
        WS_CHILD | WS_VISIBLE | style, x, y, w, h, window, (HMENU)(INT_PTR)id, GetModuleHandleW(nullptr), nullptr);
    SendMessageW(hnd, WM_SETFONT, (WPARAM)font, TRUE); return hnd;
}
static std::wstring chooseFile(bool save) {
    wchar_t name[32768] = L"";
    if (save) wcscpy_s(name, L"Bridge-私人迁移.zip");
    OPENFILENAMEW ofn{}; ofn.lStructSize = sizeof(ofn); ofn.hwndOwner = window;
    ofn.lpstrFilter = L"Bridge 迁移包 (*.zip)\0*.zip\0"; ofn.lpstrFile = name; ofn.nMaxFile = 32768;
    ofn.Flags = OFN_NOCHANGEDIR | OFN_PATHMUSTEXIST | (save ? 0 : OFN_FILEMUSTEXIST);
    ofn.lpstrDefExt = L"zip";
    return (save ? GetSaveFileNameW(&ofn) : GetOpenFileNameW(&ofn)) ? name : L"";
}
static std::wstring invoke(std::string request) {
    HANDLE childInput=nullptr, input=nullptr, output=nullptr, childOutput=nullptr;
    SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
    if (!CreatePipe(&childInput, &input, &sa, 0)) return L"创建本机管道失败。";
    if (!CreatePipe(&output, &childOutput, &sa, 0)) { CloseHandle(childInput); CloseHandle(input); return L"创建本机管道失败。"; }
    SetHandleInformation(input, HANDLE_FLAG_INHERIT, 0); SetHandleInformation(output, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOEXW startup{}; startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    startup.StartupInfo.wShowWindow = SW_HIDE;
    startup.StartupInfo.hStdInput = childInput;
    startup.StartupInfo.hStdOutput = startup.StartupInfo.hStdError = childOutput;
    SIZE_T bytes = 0; InitializeProcThreadAttributeList(nullptr, 1, 0, &bytes);
    std::vector<BYTE> attributes(bytes);
    startup.lpAttributeList = (LPPROC_THREAD_ATTRIBUTE_LIST)attributes.data();
    HANDLE inherited[] = {childInput, childOutput};
    bool attributesReady = InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &bytes) != FALSE;
    bool handlesReady = attributesReady && UpdateProcThreadAttribute(startup.lpAttributeList, 0,
        PROC_THREAD_ATTRIBUTE_HANDLE_LIST, inherited, sizeof(inherited), nullptr, nullptr);
    std::wstring python = root + L"\\runtime\\python.exe";
    std::wstring command = L"\"" + python + L"\" -B -X utf8 -m bridge_portable.rpc";
    PROCESS_INFORMATION process{};
    bool launched = handlesReady && CreateProcessW(python.c_str(), command.data(), nullptr, nullptr, TRUE,
        CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT, nullptr, root.c_str(), &startup.StartupInfo, &process);
    if (attributesReady) DeleteProcThreadAttributeList(startup.lpAttributeList);
    CloseHandle(childInput); CloseHandle(childOutput);
    if (!launched) { CloseHandle(input); CloseHandle(output); return L"无法启动随包运行时。请解压整个 ZIP，确认 runtime/python.exe 存在。"; }
    DWORD written=0; WriteFile(input, request.data(), (DWORD)request.size(), &written, nullptr);
    SecureZeroMemory(request.data(), request.size()); CloseHandle(input);
    std::string result; char buffer[4096]; DWORD count=0;
    while (ReadFile(output, buffer, sizeof(buffer), &count, nullptr) && count) {
        if (result.size() < 65536) result.append(buffer, count);
    }
    CloseHandle(output); WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exitCode=1; GetExitCodeProcess(process.hProcess, &exitCode);
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    if (result.empty()) return L"操作没有返回结果。请检查运行时文件和日志。";
    return (exitCode ? L"操作未完成\r\n" : L"") + wide(result);
}
static void request(const wchar_t* action, const std::wstring& path=L"") {
    if (busy) return;
    bool update = std::wstring(action).find(L"update-") == 0;
    updateRequest = update;
    auto password = update ? L"" : readText(passwordBox), confirmation = update ? L"" : readText(confirmBox);
    std::string body = "{\"action\":" + quote(action) + ",\"origin\":" + quote(readText(originBox)) +
        ",\"username\":" + quote(readText(usernameBox)) + ",\"password\":" + quote(password) +
        ",\"confirmation\":" + quote(confirmation) + ",\"path\":" + quote(path) + ",\"private\":" +
        (SendMessageW(privateBox,BM_GETCHECK,0,0)==BST_CHECKED ? "true" : "false") +
        ",\"version\":" + quote(targetVersion) + ",\"release_id\":" + (targetRelease.empty() ? "0" : utf8(targetRelease)) + "}";
    SecureZeroMemory(password.data(), password.size()*sizeof(wchar_t));
    SecureZeroMemory(confirmation.data(), confirmation.size()*sizeof(wchar_t));
    if (!update) { SetWindowTextW(passwordBox, L""); SetWindowTextW(confirmBox, L""); }
    busy = true; for (HWND h : controls) EnableWindow(h, FALSE);
    SetWindowTextW(outputBox, L"正在处理，请稍候。停止时会等待在途任务正常结束。\r\n结果会显示在这里。");
    std::thread([body=std::move(body)]() mutable {
        auto reply = new Reply{invoke(std::move(body))};
        if (!PostMessageW(window, Finished, 0, (LPARAM)reply)) delete reply;
    }).detach();
}
static LRESULT CALLBACK procedure(HWND h, UINT message, WPARAM wp, LPARAM lp) {
    if (message == taskbarCreated) { trayPresent = false; if (!addTray()) reveal(); return 0; }
    if (message == showConsole) { reveal(); return 0; }
    if (message == exitForUpdate) { if (mayExit()) DestroyWindow(h); return 0; }
    switch (message) {
    case WM_CREATE: {
        window = h;
        font = CreateFontW(-16,0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,OUT_DEFAULT_PRECIS,
            CLIP_DEFAULT_PRECIS,CLEARTYPE_QUALITY,DEFAULT_PITCH,L"Microsoft YaHei UI");
        make(L"STATIC",L"微信 Agent Bridge",0,24,18,600,30);
        make(L"STATIC",L"关闭或最小化后常驻托盘 · 双击托盘图标重新打开",0,24,53,660,24);
        privateBox=make(L"BUTTON",L"私人访问（SSH）",WS_TABSTOP|BS_AUTOCHECKBOX,24,90,180,30,200);
        SendMessageW(privateBox,BM_SETCHECK,BST_CHECKED,0);
        originBox=make(L"EDIT",L"",WS_TABSTOP|ES_AUTOHSCROLL,210,90,454,30);
        SendMessageW(originBox,EM_SETCUEBANNER,TRUE,(LPARAM)L"取消私人访问后填写 HTTPS 域名");
        EnableWindow(originBox,FALSE);
        make(L"STATIC",L"后台用户名",0,24,136,170,24);
        usernameBox=make(L"EDIT",L"admin",WS_TABSTOP|ES_AUTOHSCROLL,210,130,454,30);
        make(L"STATIC",L"密码 / 新密码",0,24,176,170,24);
        passwordBox=make(L"EDIT",L"",WS_TABSTOP|ES_AUTOHSCROLL|ES_PASSWORD,210,170,454,30);
        make(L"STATIC",L"确认新密码",0,24,216,170,24);
        confirmBox=make(L"EDIT",L"",WS_TABSTOP|ES_AUTOHSCROLL|ES_PASSWORD,210,210,454,30);
        make(L"STATIC",L"忘记密码：填写两次新密码并点“重置密码”，无需旧密码或重启。",0,24,251,660,24);
        controls={privateBox,originBox,usernameBox,passwordBox,confirmBox};
        const wchar_t* labels[]={L"保存首次配置",L"环境检查",L"启动后台",L"停止后台",L"打开后台",L"刷新状态",L"查看日志",L"重置密码",L"导出全部数据",L"导入迁移包",L"使用说明",L"仅导出角色/API",L"检查版本更新",L"一键升级"};
        for (int i=0;i<14;++i) {
            int row=i/4,col=i%4;
            controls.push_back(make(L"BUTTON",labels[i],WS_TABSTOP|BS_PUSHBUTTON,24+col*164,288+row*43,148,33,100+i));
        }
        outputBox=make(L"EDIT",L"",ES_MULTILINE|ES_READONLY|ES_AUTOVSCROLL|WS_VSCROLL,24,475,640,150);
        SetTimer(h,1,24*60*60*1000,nullptr);
        SetTimer(h,2,3000,nullptr);
        PostMessageW(h, WM_COMMAND, 114, 0); return 0;
    }
    case WM_SIZE:
        if (wp == SIZE_MINIMIZED) hideToTray();
        return 0;
    case TrayMessage: {
        auto event = LOWORD(lp);
        if (event == WM_LBUTTONDBLCLK || event == NIN_KEYSELECT) { reveal(); return 0; }
        if (event == WM_CONTEXTMENU || event == WM_RBUTTONUP) {
            HMENU menu = CreatePopupMenu();
            AppendMenuW(menu, MF_STRING, 301, L"打开控制台");
            AppendMenuW(menu, MF_STRING, 302, L"打开网页后台");
            AppendMenuW(menu, MF_STRING, 303, L"重置管理员密码");
            AppendMenuW(menu, MF_STRING, 304, L"查看日志");
            AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
            AppendMenuW(menu, MF_STRING, 305, L"退出控制台（后台继续运行）");
            POINT point; GetCursorPos(&point); SetForegroundWindow(h);
            int selected = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                point.x, point.y, 0, h, nullptr);
            DestroyMenu(menu); PostMessageW(h, WM_NULL, 0, 0);
            if (selected) PostMessageW(h, WM_COMMAND, selected, 0);
        }
        return 0;
    }
    case WM_TIMER:
        if (wp==3) { if (!addTray()) reveal(); return 0; }
        if (!busy && wp==1) request(L"update-auto");
        if (!busy && wp==2 && updatePending) request(L"update-status");
        return 0;
    case WM_COMMAND: {
        int id=LOWORD(wp);
        if (id == 301) { reveal(); return 0; }
        if (id == 303) {
            reveal(); SetFocus(passwordBox);
            if (!busy) SetWindowTextW(outputBox,L"填写两次新密码（4–128 字符），再点击“重置密码”。\r\n无需停止后台；重置后所有设备需要重新登录。");
            return 0;
        }
        if (id == 305) {
            if (mayExit()) DestroyWindow(h);
            else { reveal(); MessageBoxW(h,L"请等待当前操作完成。",L"操作进行中",MB_OK); }
            return 0;
        }
        if (busy) return 0;
        if (id == 302 || id == 304) { request(id == 302 ? L"open" : L"logs"); return 0; }
        if (id==200) { EnableWindow(originBox,SendMessageW(privateBox,BM_GETCHECK,0,0)!=BST_CHECKED); return 0; }
        const wchar_t* actions[]={L"setup",L"check",L"start",L"stop",L"open",L"status",L"logs",L"reset",L"export",L"import",L"help",L"export-config"};
        if (id==112 || id==114) { request(id==112 ? L"update-check" : L"update-auto"); return 0; }
        if (id==113) {
            if (targetVersion.empty()) return 0;
            auto confirm=L"升级到 v"+targetVersion+L"？程序将下载并校验附件，备份数据后切换版本。运行中的后台会短暂重启。";
            if (MessageBoxW(h,confirm.c_str(),L"确认升级",MB_OKCANCEL|MB_ICONQUESTION)==IDOK) request(L"update-install");
            return 0;
        }
        if (id<100 || id>111) return 0;
        std::wstring path;
        if (id==108 || id==109 || id==111) { path=chooseFile(id!=109); if(path.empty()) return 0; }
        const wchar_t* warning = id==109 ? L"将停止本包后台，备份当前数据后导入迁移包。旧发送队列不会重放；启动后就绪账号自动恢复。确认源电脑已停止自动运行并导入？" :
            id==107 ? L"将重置本机后台管理员密码并注销所有网页登录会话。确认重置？" : nullptr;
        if(warning && MessageBoxW(h,warning,L"确认操作",MB_OKCANCEL|MB_ICONQUESTION)!=IDOK) return 0;
        request(actions[id-100],path); return 0;
    }
    case Finished: {
        std::unique_ptr<Reply> reply((Reply*)lp);
        if (reply->text.rfind(L"UPDATE:",0)==0) {
            auto end=reply->text.find(L'\n');
            auto token=reply->text.substr(7,end-7);
            if (!token.empty() && token.back()==L'\r') token.pop_back();
            updatePending=token==L"busy";
            targetVersion.clear(); targetRelease.clear();
            auto sep=token.find(L':');
            if (sep!=std::wstring::npos && token.substr(sep+1).find_first_not_of(L"0123456789")==std::wstring::npos) {
                targetVersion=token.substr(0,sep); targetRelease=token.substr(sep+1);
            }
            reply->text.erase(0,end+1);
        }
        SetWindowTextW(outputBox,reply->text.c_str()); busy=false;
        for(HWND control:controls) EnableWindow(control,TRUE);
        EnableWindow(GetDlgItem(h,113),!targetVersion.empty() && !updatePending);
        EnableWindow(originBox,SendMessageW(privateBox,BM_GETCHECK,0,0)!=BST_CHECKED); return 0;
    }
    case WM_CLOSE:
        hideToTray(); return 0;
    case WM_QUERYENDSESSION: return TRUE;
    case WM_DESTROY:
        Shell_NotifyIconW(NIM_DELETE, &tray); trayPresent = false;
        RemovePropW(h, ProtocolProperty); DeleteObject(font); PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(h,message,wp,lp);
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE,LPWSTR,int) {
    SetProcessDPIAware();
    int argc = 0; LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    for (int i=1; argv && i<argc; ++i) {
        if (std::wstring(argv[i]) == L"--background") background = true;
        else { LocalFree(argv); return 2; }
    }
    if (argv) LocalFree(argv);
    wchar_t file[32768]; GetModuleFileNameW(nullptr,file,32768);
    root=file; root.resize(root.find_last_of(L"\\/"));
    auto bootstrap=invoke(background ? "{\"action\":\"bootstrap\",\"background\":true}" : "{\"action\":\"bootstrap\"}");
    if (bootstrap.rfind(L"opened",0)==0) return 0;
    if (bootstrap.rfind(L"continue",0)!=0) { MessageBoxW(nullptr,bootstrap.c_str(),L"启动失败",MB_OK|MB_ICONERROR); return 1; }
    uint64_t hash=1469598103934665603ULL;
    auto installation = std::filesystem::weakly_canonical(root);
    if (installation.parent_path().filename() == L"releases" &&
        std::filesystem::exists(installation.parent_path().parent_path()/L"installation.json"))
        installation = installation.parent_path().parent_path();
    for(wchar_t c:installation.wstring()) { hash^=towlower(c); hash*=1099511628211ULL; }
    auto mutexName=L"Local\\WeChatAgentBridgeLauncher-"+std::to_wstring(hash);
    auto className=L"WeChatAgentBridgeLauncher-"+std::to_wstring(hash);
    taskbarCreated = RegisterWindowMessageW(L"TaskbarCreated");
    exitForUpdate = RegisterWindowMessageW(L"WeChatAgentBridge.ExitForUpdate.v1");
    showConsole = RegisterWindowMessageW(L"WeChatAgentBridge.ShowConsole.v1");
    HANDLE mutex=CreateMutexW(nullptr,FALSE,mutexName.c_str());
    if (!mutex) { MessageBoxW(nullptr,L"无法创建控制台实例锁。",L"启动失败",MB_OK|MB_ICONERROR); return 1; }
    if(GetLastError()==ERROR_ALREADY_EXISTS) {
        if (!background) {
            HWND existing = nullptr;
            for (int i=0; i<100 && !existing; ++i) { existing = FindWindowW(className.c_str(),nullptr); if (!existing) Sleep(50); }
            if (existing) { DWORD pid=0; GetWindowThreadProcessId(existing,&pid); AllowSetForegroundWindow(pid); PostMessageW(existing,showConsole,0,0); }
        }
        CloseHandle(mutex); return 0;
    }
    WNDCLASSW cls{}; cls.lpfnWndProc=procedure; cls.hInstance=instance; cls.lpszClassName=className.c_str();
    cls.hCursor=LoadCursorW(nullptr,IDC_ARROW); cls.hIcon=LoadIconW(nullptr,IDI_APPLICATION); cls.hbrBackground=(HBRUSH)(COLOR_WINDOW+1);
    RegisterClassW(&cls);
    RECT bounds{0,0,690,650}; AdjustWindowRect(&bounds,WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU|WS_MINIMIZEBOX,FALSE);
    window=CreateWindowW(cls.lpszClassName,L"微信 Agent Bridge · 便携控制台",WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU|WS_MINIMIZEBOX,
        CW_USEDEFAULT,CW_USEDEFAULT,bounds.right-bounds.left,bounds.bottom-bounds.top,nullptr,nullptr,instance,nullptr);
    if (!window) { CloseHandle(mutex); return 1; }
    SetPropW(window,ProtocolProperty,(HANDLE)1);
    ChangeWindowMessageFilterEx(window,showConsole,MSGFLT_ALLOW,nullptr);
    bool configured = std::filesystem::exists(installation/L"data"/L"portable-config.json") &&
                      std::filesystem::exists(installation/L"data"/L"auth.sqlite3");
    bool hasTray = addTray();
    // Windows consumes STARTUPINFO.wShowWindow on the first call. Scheduled
    // tasks start us hidden; explicitly apply the fallback on the second call.
    ShowWindow(window,SW_HIDE);
    ShowWindow(window,background && configured && hasTray ? SW_HIDE : SW_SHOWNORMAL); UpdateWindow(window);
    MSG message{};
    while(GetMessageW(&message,nullptr,0,0)>0) { if(!IsDialogMessageW(window,&message)) { TranslateMessage(&message); DispatchMessageW(&message); } }
    CloseHandle(mutex); return 0;
}
