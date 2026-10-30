// Small native Windows UI. Python management runs in hidden child processes.
#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commdlg.h>
#include <commctrl.h>
#include <string>
#include <vector>
#include <thread>
#include <memory>
#include <sstream>
#include <cstdint>
#include <cwctype>

static HWND window, originBox, usernameBox, passwordBox, confirmBox, outputBox;
static HFONT font;
static bool busy = false;
static std::wstring root;
static std::vector<HWND> controls;
constexpr UINT Finished = WM_APP + 1;
struct Reply { std::wstring text; };

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
    auto password = readText(passwordBox), confirmation = readText(confirmBox);
    std::string body = "{\"action\":" + quote(action) + ",\"origin\":" + quote(readText(originBox)) +
        ",\"username\":" + quote(readText(usernameBox)) + ",\"password\":" + quote(password) +
        ",\"confirmation\":" + quote(confirmation) + ",\"path\":" + quote(path) + ",\"confirmed\":true}";
    SecureZeroMemory(password.data(), password.size()*sizeof(wchar_t));
    SecureZeroMemory(confirmation.data(), confirmation.size()*sizeof(wchar_t));
    SetWindowTextW(passwordBox, L""); SetWindowTextW(confirmBox, L"");
    busy = true; for (HWND h : controls) EnableWindow(h, FALSE);
    SetWindowTextW(outputBox, L"正在处理，请稍候。停止时会等待在途任务正常结束。\r\n发送验收期间请保持鼠标、键盘及微信窗口不动。查验结果会显示在这里。");
    std::thread([body=std::move(body)]() mutable {
        auto reply = new Reply{invoke(std::move(body))};
        if (!PostMessageW(window, Finished, 0, (LPARAM)reply)) delete reply;
    }).detach();
}
static LRESULT CALLBACK procedure(HWND h, UINT message, WPARAM wp, LPARAM lp) {
    switch (message) {
    case WM_CREATE: {
        window = h;
        font = CreateFontW(-16,0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,OUT_DEFAULT_PRECIS,
            CLIP_DEFAULT_PRECIS,CLEARTYPE_QUALITY,DEFAULT_PITCH,L"Microsoft YaHei UI");
        make(L"STATIC",L"微信 Agent Bridge",0,24,18,600,30);
        make(L"STATIC",L"解压即用 · 当前 Windows 用户运行 · 关闭此窗口不停止后台",0,24,53,660,24);
        make(L"STATIC",L"HTTPS 域名",0,24,96,170,24);
        originBox=make(L"EDIT",L"",WS_TABSTOP|ES_AUTOHSCROLL,210,90,454,30);
        SendMessageW(originBox,EM_SETCUEBANNER,TRUE,(LPARAM)L"例如 bridge.example.com（首次配置时填写）");
        make(L"STATIC",L"后台用户名",0,24,136,170,24);
        usernameBox=make(L"EDIT",L"admin",WS_TABSTOP|ES_AUTOHSCROLL,210,130,454,30);
        make(L"STATIC",L"密码 / 新密码",0,24,176,170,24);
        passwordBox=make(L"EDIT",L"",WS_TABSTOP|ES_AUTOHSCROLL|ES_PASSWORD,210,170,454,30);
        make(L"STATIC",L"确认新密码",0,24,216,170,24);
        confirmBox=make(L"EDIT",L"",WS_TABSTOP|ES_AUTOHSCROLL|ES_PASSWORD,210,210,454,30);
        make(L"STATIC",L"初始化/重置：填写两次新密码。验收/恢复：只填写当前后台密码。",0,24,251,660,24);
        controls={originBox,usernameBox,passwordBox,confirmBox};
        const wchar_t* labels[]={L"保存首次配置",L"环境检查",L"启动后台",L"停止后台",L"打开后台",L"刷新状态",L"查看日志",L"重置密码",L"导出迁移包",L"导入迁移包",L"发送验收",L"恢复迁移",L"使用说明"};
        for (int i=0;i<13;++i) {
            int row=i/4,col=i%4;
            controls.push_back(make(L"BUTTON",labels[i],WS_TABSTOP|BS_PUSHBUTTON,24+col*164,288+row*43,148,33,100+i));
        }
        outputBox=make(L"EDIT",L"",ES_MULTILINE|ES_READONLY|ES_AUTOVSCROLL|WS_VSCROLL,24,475,640,150);
        PostMessageW(h, WM_COMMAND, 105, 0); return 0;
    }
    case WM_COMMAND: {
        if (busy) return 0;
        int id=LOWORD(wp);
        const wchar_t* actions[]={L"setup",L"check",L"start",L"stop",L"open",L"status",L"logs",L"reset",L"export",L"import",L"acceptance",L"resume",L"help"};
        if (id<100 || id>112) return 0;
        std::wstring path;
        if (id==108 || id==109) { path=chooseFile(id==108); if(path.empty()) return 0; }
        const wchar_t* warning = id==110 ? L"将向每个已识别账号的文件传输助手发送一条 BRIDGE_TEST_ 测试消息。\n请先在每个微信窗口保留一段未发送草稿，保持窗口可观察；测试期间不要操作电脑。\n无法观察的项目不会判为通过。确认开始？" :
            id==109 ? L"将停止本包后台，备份当前数据后导入迁移包。旧发送队列不会自动重放。确认导入？" :
            id==107 ? L"将重置本机后台管理员密码并注销所有网页登录会话。确认重置？" :
            id==111 ? L"确认源电脑已停止自动运行，并已在本机验证账号、API 密钥和发送能力？恢复后仅新的实时触发可以发送。" : nullptr;
        if(warning && MessageBoxW(h,warning,L"确认操作",MB_OKCANCEL|MB_ICONQUESTION)!=IDOK) return 0;
        request(actions[id-100],path); return 0;
    }
    case Finished: {
        std::unique_ptr<Reply> reply((Reply*)lp);
        SetWindowTextW(outputBox,reply->text.c_str()); busy=false;
        for(HWND control:controls) EnableWindow(control,TRUE); return 0;
    }
    case WM_CLOSE:
        if(busy) { MessageBoxW(h,L"请等待当前操作完成。",L"操作进行中",MB_OK); return 0; }
        DestroyWindow(h); return 0;
    case WM_DESTROY: DeleteObject(font); PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(h,message,wp,lp);
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE,LPWSTR,int show) {
    SetProcessDPIAware();
    wchar_t file[32768]; GetModuleFileNameW(nullptr,file,32768);
    root=file; root.resize(root.find_last_of(L"\\/"));
    uint64_t hash=1469598103934665603ULL;
    for(wchar_t c:root) { hash^=towlower(c); hash*=1099511628211ULL; }
    auto mutexName=L"Local\\WeChatAgentBridgeLauncher-"+std::to_wstring(hash);
    HANDLE mutex=CreateMutexW(nullptr,FALSE,mutexName.c_str());
    if(GetLastError()==ERROR_ALREADY_EXISTS) { MessageBoxW(nullptr,L"此目录的启动器已经打开。",L"微信 Agent Bridge",MB_OK); CloseHandle(mutex); return 0; }
    WNDCLASSW cls{}; cls.lpfnWndProc=procedure; cls.hInstance=instance; cls.lpszClassName=L"WeChatAgentBridgeLauncher";
    cls.hCursor=LoadCursorW(nullptr,IDC_ARROW); cls.hIcon=LoadIconW(nullptr,IDI_APPLICATION); cls.hbrBackground=(HBRUSH)(COLOR_WINDOW+1);
    RegisterClassW(&cls);
    RECT bounds{0,0,690,650}; AdjustWindowRect(&bounds,WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU|WS_MINIMIZEBOX,FALSE);
    window=CreateWindowW(cls.lpszClassName,L"微信 Agent Bridge · 便携启动器",WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU|WS_MINIMIZEBOX,
        CW_USEDEFAULT,CW_USEDEFAULT,bounds.right-bounds.left,bounds.bottom-bounds.top,nullptr,nullptr,instance,nullptr);
    ShowWindow(window,show); UpdateWindow(window);
    MSG message{};
    while(GetMessageW(&message,nullptr,0,0)>0) { if(!IsDialogMessageW(window,&message)) { TranslateMessage(&message); DispatchMessageW(&message); } }
    CloseHandle(mutex); return 0;
}
