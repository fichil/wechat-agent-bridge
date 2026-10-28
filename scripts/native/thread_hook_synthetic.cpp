#include "thread_hook_contract.h"
#include <stdio.h>
#include <wchar.h>

static Contract *shared = nullptr;
static HWND owned_window = nullptr;
static DWORD owned_thread = 0;
static uint64_t owned_nonce = 0;

static bool AllowedWindowClass(const wchar_t *name) {
    if (!wcscmp(name,BRIDGE_CLASS)) return true;
#ifdef BRIDGE_BOUND_INSPECTOR
    // This build only performs metadata validation, never a real Send call.
    return !wcscmp(name,L"Qt51514QWindowIcon");
#else
    return false;
#endif
}

// Only this self-built executable exports the semantic operation. The helper
// has no fallback to a caller-supplied pointer or any real application's API.
extern "C" __declspec(dllexport) LONG BridgeSyntheticInvoke(HWND hwnd, uint64_t nonce, LONG generation) {
    if (!shared || hwnd != owned_window || nonce != owned_nonce ||
        GetCurrentThreadId() != owned_thread || !shared->object_alive ||
        generation != shared->generation) return WrongObject;
    if (shared->stop) return Cancelled;
    InterlockedExchange(&shared->target_thread, LONG(GetCurrentThreadId()));
    InterlockedIncrement(&shared->target_calls);
    InterlockedExchange(&shared->operation_entered,1);
    while (shared->block_inside_call && !shared->release_operation && GetTickCount64()<shared->deadline) Sleep(2);
    return Done;
}

static LRESULT CALLBACK WindowProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_TIMER && shared) {
        InterlockedIncrement(&shared->heartbeat);
        shared->helper_loaded = GetModuleHandleW(L"bridge_thread_hook.dll") ? 1 : 0;
        if (shared->target_stop) DestroyWindow(hwnd);
        return 0;
    }
    if (msg == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static int Target() {
    WNDCLASSW wc{}; wc.hInstance=GetModuleHandleW(nullptr); wc.lpfnWndProc=WindowProc; wc.lpszClassName=BRIDGE_CLASS;
    if (!RegisterClassW(&wc)) return 20;
    owned_thread=GetCurrentThreadId(); owned_nonce=shared->nonce;
    owned_window=CreateWindowExW(0, BRIDGE_CLASS, L"Synthetic only", WS_OVERLAPPED, 0,0,200,100,
                                nullptr,nullptr,wc.hInstance,nullptr);
    if (!owned_window) return 21;
    shared->target_pid=GetCurrentProcessId(); shared->target_tid=owned_thread;
    shared->target_created=Created(GetCurrentProcess()); shared->hwnd=uint64_t(owned_window);
    shared->generation=1; shared->object_alive=1;
    if (!SetTimer(owned_window, 1, 10, nullptr)) return 22;
    InterlockedExchange(&shared->target_ready, 1);
    MSG msg{};
    while (GetMessageW(&msg,nullptr,0,0)>0) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    owned_window=nullptr; UnregisterClassW(BRIDGE_CLASS,wc.hInstance); return 0;
}

static int Owner(const wchar_t *dll) {
    shared->owner_pid=GetCurrentProcessId(); shared->owner_created=Created(GetCurrentProcess());
    DWORD pid=0; HWND hwnd=reinterpret_cast<HWND>(shared->hwnd);
    DWORD tid=GetWindowThreadProcessId(hwnd,&pid);
    wchar_t name[96]{}; GetClassNameW(hwnd,name,96);
    if (!tid || tid != shared->target_tid || pid != shared->target_pid || !AllowedWindowClass(name) ||
        !Alive(pid,shared->target_created)) return 33;
    HANDLE target_thread=OpenThread(THREAD_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,tid);
    if (!target_thread || GetProcessIdOfThread(target_thread) != pid || WaitForSingleObject(target_thread,0) != WAIT_TIMEOUT) {
        if (target_thread) CloseHandle(target_thread);
        return 34;
    }
    HMODULE library=LoadLibraryExW(dll,nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!library) { CloseHandle(target_thread); return 30; }
    InterlockedExchange(&shared->install_reached,1);
    uint64_t install_deadline=GetTickCount64()+2500;
    while (shared->block_before_install && !shared->release_install && !shared->owner_stop &&
           Alive(shared->controller_pid,shared->controller_created) && GetTickCount64()<install_deadline) Sleep(2);
    if (shared->owner_stop || !Alive(shared->controller_pid,shared->controller_created) ||
        GetTickCount64()>=install_deadline) { FreeLibrary(library); CloseHandle(target_thread); return 35; }
    auto callback=reinterpret_cast<HOOKPROC>(GetProcAddress(library,"BridgeHook"));
    HHOOK hook=callback ? SetWindowsHookExW(WH_CALLWNDPROC,callback,library,tid) : nullptr;
    if (!hook) { shared->owner_error=LONG(GetLastError()); FreeLibrary(library); CloseHandle(target_thread); return 31; }
    UINT message=RegisterWindowMessageW(BRIDGE_MESSAGE);
    InterlockedExchange(&shared->owner_ready,1);
    const uint64_t owner_deadline=GetTickCount64()+20000;
    while (!shared->owner_stop && Alive(shared->controller_pid,shared->controller_created) && GetTickCount64()<owner_deadline) {
        if (shared->wake > shared->wake_done) {
            LONG request=shared->wake;
            DWORD_PTR result=0;
            shared->wake_result = SendMessageTimeoutW(reinterpret_cast<HWND>(shared->hwnd),message,
                WPARAM(shared->controller_pid),LPARAM(shared->nonce),SMTO_ABORTIFHUNG|SMTO_ERRORONEXIT,500,&result) ? 1 : 0;
            InterlockedExchange(&shared->wake_done,request);
        }
        MSG msg{}; while (PeekMessageW(&msg,nullptr,0,0,PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
        Sleep(2);
    }
    if (GetTickCount64()>=owner_deadline) shared->owner_error=ERROR_TIMEOUT;
    InterlockedExchange(&shared->stop,1);
    shared->owner_unhooked = UnhookWindowsHookEx(hook) ? 1 : 0;
    // A held thread handle proves this exact target thread has ended; a reused
    // numeric TID cannot satisfy this observation. Do not claim API success.
    if (!shared->owner_unhooked && WaitForSingleObject(target_thread,0) == WAIT_OBJECT_0)
        shared->owner_unhooked=2;
    uint64_t deadline=GetTickCount64()+2000;
    while (shared->active && GetTickCount64()<deadline) Sleep(2);
    shared->owner_drained = shared->active == 0;
    if (shared->owner_drained) FreeLibrary(library);
    CloseHandle(target_thread);
    return shared->owner_unhooked && shared->owner_drained && !shared->owner_error ? 0 : 32;
}

static int Probe() {
    HWND hwnd=reinterpret_cast<HWND>(shared->hwnd);
    DWORD pid=0; wchar_t name[96]{};
    DWORD tid=GetWindowThreadProcessId(hwnd,&pid);
    GetClassNameW(hwnd,name,96);
    if (pid != shared->target_pid || tid != shared->target_tid || !AllowedWindowClass(name) ||
        !Alive(pid,shared->target_created)) return 40;
    DWORD_PTR result=0;
    return SendMessageTimeoutW(hwnd,RegisterWindowMessageW(BRIDGE_MESSAGE),WPARAM(shared->controller_pid),
        LPARAM(shared->nonce),SMTO_ABORTIFHUNG|SMTO_ERRORONEXIT,500,&result) ? 0 : 41;
}

int wmain(int argc, wchar_t **argv) {
    if (argc != 5 || (wcscmp(argv[1],L"target") && wcscmp(argv[1],L"owner") && wcscmp(argv[1],L"probe"))) return 2;
    HDESK original=GetThreadDesktop(GetCurrentThreadId());
    HDESK desktop=OpenDesktopW(argv[3],0,FALSE,DESKTOP_READOBJECTS|DESKTOP_WRITEOBJECTS|DESKTOP_CREATEWINDOW|DESKTOP_HOOKCONTROL);
    if (!desktop || !SetThreadDesktop(desktop)) return 3;
    HANDLE mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,argv[2]);
    if (!mapping) return 4;
    shared=static_cast<Contract*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,sizeof(Contract)));
    if (!ValidContract(shared)) return 5;
    int result=!wcscmp(argv[1],L"target") ? Target() : !wcscmp(argv[1],L"owner") ? Owner(argv[4]) : Probe();
    UnmapViewOfFile(shared); shared=nullptr; CloseHandle(mapping);
    SetThreadDesktop(original); CloseDesktop(desktop); return result;
}
