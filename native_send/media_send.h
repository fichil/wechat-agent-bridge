#pragma once
// Adapted from helper image_send.h / emoticon_send.h at the commit in NOTICE.md.
// The registry locks every address to the production 4.2.0.9 fingerprint.
// This reports native completion evidence, never delivery success on its own.
struct QtStaticUtf16 {
    int32_t references, length;
    uint32_t allocation, padding;
    int64_t offset;
    wchar_t characters[1024];
};
static_assert(offsetof(QtStaticUtf16, characters) == 24, "Unexpected Qt array ABI");
struct BorrowedWide { const wchar_t* data; uint64_t unused, length, capacity; };
struct WideView { wchar_t data[8]; uint64_t length, capacity; };
struct GifProbe { uint8_t valid, padding[7]; unsigned char image[16]; uint32_t width, height; };
static_assert(sizeof(GifProbe) == 32 && sizeof(BorrowedWide) == 32, "Unexpected media ABI");

static void media_free_string(SmallString* text, TestResult* report) {
    __try {
        if (text->capacity > 15 && text->capacity < 1024 * 1024) {
            void* allocation = *reinterpret_cast<void**>(text->data);
            size_t size = static_cast<size_t>(text->capacity + 1);
            if (size >= 4096) { allocation = *(reinterpret_cast<void**>(allocation) - 1); size += 39; }
            using Free = void (__fastcall*)(void*, size_t);
            reinterpret_cast<Free>(core_address(core_base, CoreField::free))(allocation, size);
        }
    } __except (record_exception(GetExceptionInformation(), report)) {}
}

static void media_report(const TextConfig& config, const TestResult& report, const char* state, bool sticker, uint64_t key_first, uint64_t key_second, const char* key_source) {
    FILE* log = nullptr;
    if (_wfopen_s(&log, config.base.log_path, L"wb") != 0 || !log) return;
    std::fputs("{\"token\":", log); write_json_string(log, config.base.token);
    std::fputs(",\"status\":", log); write_json_string(log, state);
    std::fprintf(log, ",\"stage\":%lu,\"exception\":%lu", report.stage, report.exception);
    std::fprintf(log, ",\"message_type\":%u,\"target\":", sticker ? 47u : 3u);
    write_json_string(log, config.target);
    std::fprintf(log, ",\"local_id\":%u,\"create_time\":%u,\"timestamp_ms\":%llu,\"source_uuid\":", static_cast<uint32_t>(key_first >> 32), static_cast<uint32_t>(key_first), key_second);
    write_json_string(log, key_source);
    std::fputs("}", log); std::fclose(log);
}

static bool media_context_matches(const SharedMessage& context, const TextConfig& config) {
    if (!identity_matches(config.base) || !context.object || !context.control) return false;
    const auto app = *reinterpret_cast<uintptr_t*>(core_address(core_base, CoreField::application));
    return *reinterpret_cast<void**>(app + core_offset(CoreField::app_context)) == context.object;
}

static void send_media_on_task(const TextConfig& config, bool sticker) {
    SharedMessage context{}, provider{}, manager{}, message{};
    SmallString roundtrip{}; roundtrip.capacity = 15;
    alignas(16) unsigned char result[128]{};
    QtStaticUtf16 utf16{}; GifProbe probe{}; TestResult report{};
    bool probe_initialized = false, submitted = false, completed = false;
    char key_scope[128]{}, key_source[128]{};
    uint64_t key_first = 0, key_second = 0;
    __try {
        __try {
            report.stage = 1;
            if (!identity_matches(config.base)) __leave;
            report.stage = 2;
            size_t length = strnlen_s(config.base.text, sizeof(config.base.text));
            if (!length || length >= sizeof(config.base.text)) __leave;
            int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, config.base.text,
                static_cast<int>(length), utf16.characters, 1023);
            if (count <= 0) __leave;
            utf16.references = -1; utf16.length = count; utf16.offset = 24;
            report.stage = 3;
            WIN32_FILE_ATTRIBUTE_DATA info{};
            if (!GetFileAttributesExW(utf16.characters, GetFileExInfoStandard, &info) ||
                (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) ||
                info.nFileSizeHigh || !info.nFileSizeLow || info.nFileSizeLow > (sticker ? 1048576u : 8388608u)) __leave;
            QtStaticUtf16* qstring = &utf16;
            report.stage = 4;
            using ToUtf8 = SmallString* (__fastcall*)(void*, SmallString*);
            reinterpret_cast<ToUtf8>(core_address(core_base, CoreField::qstring_utf8))(&qstring, &roundtrip);
            const char* bytes = roundtrip.capacity > 15 ? *reinterpret_cast<const char**>(roundtrip.data) : roundtrip.data;
            if (roundtrip.length != length || std::memcmp(bytes, config.base.text, length)) __leave;
            using GetContext = SharedMessage* (__fastcall*)(SharedMessage*);
            using GetService = SharedMessage* (__fastcall*)(void*, SharedMessage*);
            report.stage = 5;
            reinterpret_cast<GetContext>(core_address(core_base, CoreField::context_shared))(&context);
            if (!media_context_matches(context, config)) __leave;
            report.stage = 6;
            reinterpret_cast<GetService>(core_address(core_base, CoreField::context_provider))(context.object, &provider);
            if (!provider.object || !provider.control) __leave;
            report.stage = 7;
            reinterpret_cast<GetService>(core_address(core_base, CoreField::message_manager))(provider.object, &manager);
            if (!manager.object || !manager.control ||
                *static_cast<uintptr_t*>(manager.object) != core_address(core_base, CoreField::manager_vtable)) __leave;
            if (sticker) {
                report.stage = 8;
                BorrowedWide path{utf16.characters, 0, static_cast<uint64_t>(count), 1023};
                using Validate = int (__fastcall*)(const BorrowedWide*);
                if (reinterpret_cast<Validate>(core_address(core_base, CoreField::gif_validate))(&path) != 2) __leave;
                report.stage = 9;
                using Probe = GifProbe* (__fastcall*)(GifProbe*, const BorrowedWide*);
                probe_initialized = true;
                reinterpret_cast<Probe>(core_address(core_base, CoreField::gif_probe))(&probe, &path);
                if (!probe.valid || !probe.width || !probe.height || probe.width > 1024 || probe.height > 1024) __leave;
                report.stage = 10;
                using Factory = SharedMessage* (__fastcall*)(SharedMessage*);
                reinterpret_cast<Factory>(core_address(core_base, CoreField::sticker_factory))(&message);
                if (!message.object || !message.control ||
                    *static_cast<uintptr_t*>(message.object) != core_address(core_base, CoreField::sticker_vtable)) __leave;
                auto object = static_cast<unsigned char*>(message.object);
                *reinterpret_cast<uint32_t*>(object + 0x118) = 47;
                *reinterpret_cast<uint64_t*>(object + 0x778) = static_cast<uint64_t>(probe.width) | (static_cast<uint64_t>(probe.height) << 32);
                using CopyWide = void* (__fastcall*)(void*, const BorrowedWide*);
                reinterpret_cast<CopyWide>(core_address(core_base, CoreField::copy_wide))(object + 0x120, &path);
            } else {
                report.stage = 11;
                using Factory = SharedMessage* (__fastcall*)(void*, SharedMessage*, void*);
                reinterpret_cast<Factory>(core_address(core_base, CoreField::image_factory))(nullptr, &message, &qstring);
                if (!message.object || !message.control) __leave;
                report.stage = 12;
                auto object = static_cast<unsigned char*>(message.object);
                if (*reinterpret_cast<uint32_t*>(object + 0x118) != 3) __leave;
                report.stage = 13;
                auto prepared = reinterpret_cast<const WideView*>(object + 0x120);
                if (!prepared->length || prepared->length >= 1024 || prepared->capacity < prepared->length) __leave;
                report.stage = 14;
                const wchar_t* destination = prepared->capacity > 7 ? *reinterpret_cast<const wchar_t* const*>(prepared->data) : prepared->data;
                if (!CopyFileW(utf16.characters, destination, TRUE)) __leave;
            }
            auto object = static_cast<unsigned char*>(message.object);
            report.stage = 15;
            if (!set_native_string(reinterpret_cast<SmallString*>(object + 0xb0), config.target) ||
                !media_context_matches(context, config)) __leave;
            SharedMessage transferred = message;
            _InterlockedIncrement(reinterpret_cast<volatile LONG*>(static_cast<unsigned char*>(message.control) + 8));
            using Send = void* (__fastcall*)(void*, void*, SharedMessage*, uint32_t);
            submitted = true;
            report.stage = 16;
            reinterpret_cast<Send>(core_address(core_base, CoreField::message_send))(manager.object, result, &transferred, 1);
            // The native future owns the retained argument. A result alone is not a delivery receipt.
            if (*reinterpret_cast<uint64_t*>(result) != 0) __leave;
            report.stage = 17;
            auto returned = reinterpret_cast<SharedMessage*>(result + 40);
            if (!returned->object || !returned->control) __leave;
            report.stage = 18;
            // Result payload is an optional native message key, not the factory message.
            // Its 0x50-byte value contains strings at 0 / 0x30 and identifiers at 0x20.
            auto key = static_cast<unsigned char*>(returned->object);
            if (key[0x50] == 1) {
                auto scope = reinterpret_cast<const SmallString*>(key);
                auto source = reinterpret_cast<const SmallString*>(key + 0x30);
                if (scope->length < sizeof(key_scope) && scope->capacity >= scope->length && scope->capacity < 1024) {
                    std::memcpy(key_scope, scope->capacity > 15 ? *reinterpret_cast<const char* const*>(scope->data) : scope->data, static_cast<size_t>(scope->length));
                }
                if (source->length < sizeof(key_source) && source->capacity >= source->length && source->capacity < 1024) {
                    std::memcpy(key_source, source->capacity > 15 ? *reinterpret_cast<const char* const*>(source->data) : source->data, static_cast<size_t>(source->length));
                }
                key_first = *reinterpret_cast<const uint64_t*>(key + 0x20);
                key_second = *reinterpret_cast<const uint64_t*>(key + 0x28);
            }
            auto sent_object = static_cast<unsigned char*>(message.object);
            if (*reinterpret_cast<uint32_t*>(sent_object + 0x118) != (sticker ? 47u : 3u)) __leave;
            auto receiver = reinterpret_cast<const SmallString*>(sent_object + 0xb0);
            size_t target_length = std::strlen(config.target);
            if (receiver->length != target_length || receiver->capacity < target_length || receiver->capacity > 1024) __leave;
            const char* target = receiver->capacity > 15 ? *reinterpret_cast<const char* const*>(receiver->data) : receiver->data;
            if (std::memcmp(target, config.target, target_length)) __leave;
            report.stage = 19;
            // +0x50 of the factory object is NOT a server ID. Native completion
            // returns a scoped message key; the archive supplies the server ID.
            completed = !std::strcmp(key_scope, config.target) && (key_first >> 32) != 0 &&
                static_cast<uint32_t>(key_first) != 0 && key_second / 1000 == static_cast<uint32_t>(key_first) &&
                std::strlen(key_source) == 36;
        } __except (record_exception(GetExceptionInformation(), &report)) {}
        guarded_release_shared(reinterpret_cast<SharedMessage*>(result + 40), &report);
        media_free_string(reinterpret_cast<SmallString*>(result + 8), &report);
        media_free_string(&roundtrip, &report);
        guarded_release_shared(&message, &report);
        if (probe_initialized) {
            __try {
                using Release = void (__fastcall*)(void*);
                reinterpret_cast<Release>(core_address(core_base, CoreField::gif_probe_release))(probe.image);
            } __except (record_exception(GetExceptionInformation(), &report)) {}
        }
        guarded_release_shared(&manager, &report);
        guarded_release_shared(&provider, &report);
        guarded_release_shared(&context, &report);
        media_report(config, report, completed && !report.exception ? "accepted" : submitted || report.exception ? "unknown" : "failed", sticker, key_first, key_second, key_source);
    } __finally {}
}

static DWORD schedule_media(void* parameter, bool sticker) {
    if (!parameter || live_tasks || live_completions) return ERROR_BUSY;
    const TextConfig config = *static_cast<const TextConfig*>(parameter);
    if (config.base.size != sizeof(TextConfig) || config.base.pid != GetCurrentProcessId() || !valid_target(config.target)) return ERROR_INVALID_PARAMETER;
    HMODULE core = GetModuleHandleW(L"Weixin.dll");
    if (!core || !core_fingerprint(core) || !core_offset(CoreField::image_factory)) return 4;
    core_base = reinterpret_cast<uintptr_t>(core);
    if (!identity_matches(config.base)) return ERROR_NOT_READY;
    try {
        auto lease = std::make_shared<TextTaskLease>(config);
        std::function<void()> task = [lease, sticker]() {
            _InterlockedIncrement(&active_callbacks);
            __try { send_media_on_task(lease->config, sticker); }
            __finally { _InterlockedDecrement(&active_callbacks); }
        };
        return schedule_task(config.base, &task);
    } catch (...) { return ERROR_NOT_ENOUGH_MEMORY; }
}
extern "C" __declspec(dllexport) DWORD WINAPI WxSendImage(void* parameter) { return schedule_media(parameter, false); }
extern "C" __declspec(dllexport) DWORD WINAPI WxSendSticker(void* parameter) { return schedule_media(parameter, true); }
