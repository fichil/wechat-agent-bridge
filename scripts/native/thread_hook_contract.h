#pragma once
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>

// Synthetic-only protocol. No WeChat addresses, text, keys, or arbitrary calls.
static const uint64_t BRIDGE_MAGIC = 0x4252494447454831ULL;
static const wchar_t *BRIDGE_MESSAGE = L"WeChatAgentBridge.SyntheticThreadHook.v1";
static const wchar_t *BRIDGE_CLASS = L"BridgeM1SyntheticThreadHookWindow";
enum Phase : LONG { Idle=0, Ready=1, Entered=2, Returned=3, Denied=4 };
enum Status : LONG { None=0, Done=1, BadIdentity=2, Cancelled=3, Expired=4,
                     Duplicate=5, WrongObject=6, UnsupportedTarget=7, BadProtocol=8 };

struct Contract {
    uint64_t magic;
    DWORD version, bytes;
    DWORD controller_pid, owner_pid, target_pid, target_tid;
    uint64_t controller_created, owner_created, target_created, hwnd, nonce, deadline;
    volatile LONG phase, stop, request, consumed, entries, active, status;
    volatile LONG owner_ready, owner_stop, owner_unhooked, owner_drained, owner_error;
    volatile LONG target_ready, target_stop, heartbeat, target_calls, target_thread;
    volatile LONG generation, object_alive, expected_generation;
    volatile LONG block_before_dispatch, callback_arrived, release_callback;
    volatile LONG helper_loaded, wake, wake_done, wake_result;
    volatile LONG block_inside_call, operation_entered, release_operation;
    volatile LONG block_before_install, install_reached, release_install;
};
static_assert(sizeof(Contract) == 216, "protocol size");

inline uint64_t Created(HANDLE process) {
    FILETIME a{}, b{}, c{}, d{};
    if (!GetProcessTimes(process, &a, &b, &c, &d)) return 0;
    return (uint64_t(a.dwHighDateTime) << 32) | a.dwLowDateTime;
}
inline bool Alive(DWORD pid, uint64_t created) {
    HANDLE p = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid);
    if (!p) return false;
    bool result = Created(p) == created && WaitForSingleObject(p, 0) == WAIT_TIMEOUT;
    CloseHandle(p); return result;
}
inline bool ValidContract(const Contract *s) {
    return s && s->magic == BRIDGE_MAGIC && s->version == 1 && s->bytes == sizeof(Contract);
}
