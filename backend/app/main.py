from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .adapters import AccountProbe, discover_local_accounts
from .models import Account, AccountSnapshot, ApiError, Health, ScanTask, StartScanRequest, ScanQuery, AccountQuery
from .conversation_models import (ConversationAccountRequest, RefreshQuery, ConversationQuery,
                                  SettingsUpdate, ConversationSettings, ConversationRefresh, ConversationPage)
from .conversation_settings import ConversationSettingsStore, SettingsError
from .conversations import ConversationError
from .message_models import MessageRequest, ImageRequest, MessageTaskQuery, MessageReadTask, MessagePage, MessageImage
from .role_models import (JsonRequest, CardQuery, CardCreate, CardUpdate, CardDelete, CardList,
                          RoleCard, CardDeleted, PreferencesQuery, PreferencesUpdate, ConversationPreferences)
from .ai_models import (ProfileQuery, ProfileCreate, ProfileUpdate, ProfileDelete, ApiProfile,
                        ProfileList, ProfileDeleted, AiDefaults, DefaultsUpdate)
from .ai_execution_models import (AiSettings, AiSettingsUpdate, TaskQuery, ProfileTest, ProfileTask,
                                  RunStart, RunQuery, RunRetry, AiRun, RunList)
from .log_models import (LogAccount, LogQuery, LogGet, LogClear, LogPage, LogDetail, LogDeleted,
                         SessionPage, RunMessagesQuery, RunMessagesPage)
from .delivery_models import Delivery, DeliveryAction, DeliveryResolve, SendCapability

ALLOWED_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173",
                   "http://127.0.0.1:8765", "http://localhost:8765"}


def error(code: str, message: str, status: int):
    return JSONResponse(status_code=status, content=dict(code=code, message=message))


def create_app(probe: AccountProbe = discover_local_accounts, *, conversation_reader=None,
               settings_path=None, message_reader=None, database_path=None, monitor=None, ai_provider=None, ai_auto=None,
               send_transport=None, send_auto=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        from .storage import Store, DEFAULT_PATH
        from .runtime import Bridge
        from .events import Events
        from pathlib import Path
        from wechat_conversations.client import LocalConversationReader
        settings = ConversationSettingsStore() if settings_path is None else ConversationSettingsStore(settings_path)
        path = database_path if database_path is not None else (Path(settings_path).with_suffix('.sqlite3') if settings_path else DEFAULT_PATH if probe is discover_local_accounts else ':memory:')
        app.state.events = Events()
        store = Store(path, settings)
        store.notify = app.state.events.publish
        enabled = (probe is discover_local_accounts and os.environ.get('BRIDGE_DISABLE_MONITOR') != '1') if monitor is None else monitor
        bridge = Bridge(store, probe, conversation_reader or LocalConversationReader(), message_reader=message_reader, monitor=enabled)
        app.state.bridge, app.state.store = bridge, store
        app.state.discovery, app.state.conversations, app.state.messages = bridge.discovery, bridge.conversations, bridge.messages
        from .ai_execution import AiExecution
        app.state.ai = AiExecution(store, bridge, ai_provider, enabled if ai_auto is None else ai_auto)
        from .delivery import DeliveryExecution
        app.state.sender = DeliveryExecution(store,bridge,send_transport,enabled if send_auto is None else send_auto)
        def notify(event):
            app.state.events.publish(event)
            bridge.scheduler.notify(event)
            app.state.ai.notify(event)
            app.state.sender.notify(event)
        store.notify = notify
        app.state.ai.wake.set()
        app.state.sender.wake.set()
        try:
            yield
        finally:
            import asyncio
            app.state.ai.stopped.set()
            app.state.sender.stop.set()
            await asyncio.gather(asyncio.to_thread(app.state.ai.close), asyncio.to_thread(app.state.sender.close))
            await asyncio.to_thread(bridge.close)

    app = FastAPI(title="WeChat Agent Bridge", version="1.0.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    failures = {code: {"model": ApiError} for code in (400, 403, 404, 409, 415, 422, 500, 503)}

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.url.hostname not in ("127.0.0.1", "localhost"):
            response = error("host_not_allowed", "仅允许本机控制台访问", 400)
        elif (origin is not None and origin not in ALLOWED_ORIGINS) or (
            request.headers.get("sec-fetch-site") == "cross-site" and origin not in ALLOWED_ORIGINS
        ):
            response = error("origin_not_allowed", "仅允许本机控制台访问", 403)
        elif request.method == "POST" and request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            response = error("json_required", "请求必须使用 JSON", 415)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        return error("invalid_request", "请求格式不正确", 422)

    @app.exception_handler(ConversationError)
    async def conversation_error(_request, exc):
        return error(exc.code, exc.message, exc.status)

    @app.exception_handler(SettingsError)
    async def settings_error(_request, exc):
        return error(str(exc), "会话设置读取或保存失败，原有设置未被覆盖。", 503)

    @app.exception_handler(Exception)
    async def unexpected_error(_request, _exc):
        return error("internal_error", "本地服务暂时无法完成请求，请重试。", 500)

    @app.get("/api/v1/health", response_model=Health)
    async def health():
        return Health()

    @app.post("/api/v1/account-scans", response_model=ScanTask, status_code=202,
              responses=failures)
    def start_scan(_body: StartScanRequest, request: Request):
        try:
            return request.app.state.discovery.start()
        except RuntimeError:
            return error("service_stopping", "服务正在停止", 503)

    @app.post("/api/v1/account-scans/query", response_model=ScanTask,
             responses=failures)
    def get_scan(body: ScanQuery, request: Request):
        return request.app.state.discovery.task(body.scan_id)

    @app.post("/api/v1/accounts/query", response_model=AccountSnapshot,
             responses=failures)
    def get_accounts(_body: StartScanRequest, request: Request):
        return request.app.state.discovery.snapshot()

    @app.post("/api/v1/accounts/get", response_model=Account,
             responses=failures)
    def get_account(body: AccountQuery, request: Request):
        return request.app.state.discovery.account(body.account_id)

    @app.post('/api/v1/wechat-send-status/query', response_model=SendCapability, responses=failures)
    def send_status(body: AccountQuery, request: Request):
        return request.app.state.sender.capability(body.account_id)

    @app.post('/api/v1/ai-deliveries/retry', response_model=Delivery, responses=failures)
    def retry_delivery(body: DeliveryAction, request: Request):
        return request.app.state.store.update_delivery(body.account_id,body.conversation_id,body.run_id)

    @app.post('/api/v1/ai-deliveries/resolve', response_model=Delivery, responses=failures)
    def resolve_delivery(body: DeliveryResolve, request: Request):
        return request.app.state.store.update_delivery(body.account_id,body.conversation_id,body.run_id,body.resolution)

    @app.post("/api/v1/messages/query", response_model=MessagePage, responses=failures)
    def cached_messages(body: MessageRequest, request: Request):
        return request.app.state.messages.page(body)

    @app.post("/api/v1/message-images/query", response_model=MessageImage, responses=failures)
    def cached_image(body: ImageRequest, request: Request):
        return request.app.state.messages.image(body)

    @app.websocket("/api/v1/events")
    async def events(socket: WebSocket):
        if socket.url.hostname not in ("127.0.0.1", "localhost") or socket.headers.get("origin") not in ALLOWED_ORIGINS:
            await socket.close(code=1008)
            return
        try:
            await socket.app.state.events.serve(socket, socket.app.state.store)
        except WebSocketDisconnect:
            pass

    @app.post("/api/v1/message-reads", response_model=MessageReadTask, status_code=202, responses=failures)
    def read_messages(body: MessageRequest, request: Request):
        return request.app.state.messages.start(body)

    @app.post("/api/v1/message-image-reads", response_model=MessageReadTask, status_code=202, responses=failures)
    def read_message_image(body: ImageRequest, request: Request):
        return request.app.state.messages.start(body, "image")

    @app.post("/api/v1/message-read-tasks/query", response_model=MessageReadTask, responses=failures)
    def message_task(body: MessageTaskQuery, request: Request):
        return request.app.state.messages.query(body.account_id, body.task_id)

    @app.post("/api/v1/conversation-refreshes", response_model=ConversationRefresh,
              status_code=202, responses=failures)
    def refresh_conversations(body: ConversationAccountRequest, request: Request):
        return request.app.state.conversations.start(body.account_id)

    @app.post("/api/v1/conversation-refreshes/query", response_model=ConversationRefresh, responses=failures)
    def conversation_refresh(body: RefreshQuery, request: Request):
        return request.app.state.conversations.task(body.account_id, body.refresh_id)

    @app.post("/api/v1/conversations/query", response_model=ConversationPage, responses=failures)
    def conversations(body: ConversationQuery, request: Request):
        return request.app.state.conversations.page(body)

    @app.post("/api/v1/conversation-settings/query", response_model=ConversationSettings, responses=failures)
    def conversation_settings(body: ConversationAccountRequest, request: Request):
        service = request.app.state.conversations
        account = service.account(body.account_id)
        return ConversationSettings(account_id=body.account_id, exclusions=service.settings.get(account.wxid))

    @app.post("/api/v1/conversation-settings/update", response_model=ConversationSettings, responses=failures)
    def update_conversation_settings(body: SettingsUpdate, request: Request):
        return ConversationSettings(account_id=body.account_id, exclusions=
            request.app.state.conversations.save_settings(body.account_id, body.exclusions))

    @app.post("/api/v1/role-cards/query", response_model=CardList, responses=failures)
    def role_cards(_body: JsonRequest, request: Request):
        return request.app.state.store.role_cards()

    @app.post("/api/v1/role-cards/get", response_model=RoleCard, responses=failures)
    def role_card(body: CardQuery, request: Request):
        return request.app.state.store.role_card(body.card_code)

    @app.post("/api/v1/role-cards/create", response_model=RoleCard, responses=failures, status_code=201)
    def create_role_card(body: CardCreate, request: Request):
        return request.app.state.store.save_role_card(body.card.model_dump(exclude_unset=True))

    @app.post("/api/v1/role-cards/update", response_model=RoleCard, responses=failures)
    def update_role_card(body: CardUpdate, request: Request):
        return request.app.state.store.save_role_card(body.card.model_dump(exclude_unset=True), body.card_code, body.expected_revision)

    @app.post("/api/v1/role-cards/delete", response_model=CardDeleted, responses=failures)
    def delete_role_card(body: CardDelete, request: Request):
        return request.app.state.store.delete_role_card(body.card_code, body.expected_revision)

    @app.post("/api/v1/conversation-preferences/query", response_model=ConversationPreferences, responses=failures)
    def preferences(body: PreferencesQuery, request: Request):
        return request.app.state.store.preferences(body.account_id, body.conversation_id)

    @app.post("/api/v1/conversation-preferences/update", response_model=ConversationPreferences, responses=failures)
    def update_preferences(body: PreferencesUpdate, request: Request):
        patch = body.model_dump(exclude_unset=True, include={"ai_enabled", "role_card_code", "api_profile_id", "memory"})
        return request.app.state.store.save_preferences(body.account_id, body.conversation_id, body.expected_revision, patch, body.expected_memory_revision)

    @app.post("/api/v1/api-profiles/query", response_model=ProfileList, responses=failures)
    def api_profiles(_body: JsonRequest, request: Request):
        return request.app.state.store.api_profiles()

    @app.post("/api/v1/api-profiles/get", response_model=ApiProfile, responses=failures)
    def api_profile(body: ProfileQuery, request: Request):
        return request.app.state.store.api_profile(body.profile_id)

    @app.post("/api/v1/api-profiles/create", response_model=ProfileTask, status_code=202, responses=failures)
    def create_profile(body: ProfileCreate, request: Request):
        return request.app.state.ai.start_profile(body.model_dump(exclude={"api_key"}), body.api_key.get_secret_value())

    @app.post("/api/v1/api-profiles/update", response_model=ProfileTask, status_code=202, responses=failures)
    def update_profile(body: ProfileUpdate, request: Request):
        return request.app.state.ai.start_profile(body.model_dump(exclude={"api_key", "profile_id", "expected_revision"}),
            body.api_key.get_secret_value() if body.api_key is not None else None, body.profile_id, body.expected_revision)

    @app.post("/api/v1/api-profiles/test", response_model=ProfileTask, status_code=202, responses=failures)
    def test_profile(body: ProfileTest, request: Request):
        return request.app.state.ai.start_profile(identity=body.profile_id, expected=body.expected_revision)

    @app.post("/api/v1/api-profiles/tasks/query", response_model=ProfileTask, responses=failures)
    def profile_task(body: TaskQuery, request: Request):
        return request.app.state.store.api_task(body.task_id)

    @app.post("/api/v1/ai-settings/query", response_model=AiSettings, responses=failures)
    def ai_settings(_body: JsonRequest, request: Request):
        return request.app.state.store.ai_settings()

    @app.post("/api/v1/ai-settings/update", response_model=AiSettings, responses=failures)
    def ai_settings_update(body: AiSettingsUpdate, request: Request):
        return request.app.state.store.save_ai_settings(body.expected_revision, body.instructions)

    @app.post("/api/v1/ai-runs/start", response_model=AiRun, status_code=202, responses=failures)
    def ai_start(body: RunStart, request: Request):
        return request.app.state.ai.start(body.account_id, body.conversation_id, body.request_id)

    @app.post("/api/v1/ai-runs/retry", response_model=AiRun, status_code=202, responses=failures)
    def ai_retry(body: RunRetry, request: Request):
        return request.app.state.ai.retry(body.account_id, body.conversation_id, body.run_id, body.request_id)

    @app.post("/api/v1/ai-runs/query", response_model=AiRun, responses=failures)
    def ai_query(body: RunQuery, request: Request):
        return request.app.state.store.ai_run(body.account_id, body.conversation_id, body.run_id)

    @app.post("/api/v1/ai-runs/list", response_model=RunList, responses=failures)
    def ai_list(body: PreferencesQuery, request: Request):
        return request.app.state.store.ai_runs(body.account_id, body.conversation_id)

    @app.post("/api/v1/logs/query", response_model=LogPage, responses=failures)
    def logs(body: LogQuery, request: Request):
        return request.app.state.store.logs(body)

    @app.post("/api/v1/logs/get", response_model=LogDetail, responses=failures)
    def log_detail(body: LogGet, request: Request):
        return request.app.state.store.log_detail(body.account_id, body.log_id)

    @app.post("/api/v1/logs/clear", response_model=LogDeleted, responses=failures)
    def clear_logs(body: LogClear, request: Request):
        return request.app.state.store.clear_logs(body.account_id, body.scope)

    @app.post("/api/v1/ai-runs/sessions/query", response_model=SessionPage, responses=failures)
    def ai_sessions(body: LogAccount, request: Request):
        return request.app.state.store.ai_session_list(body.account_id)

    @app.post("/api/v1/ai-runs/messages/query", response_model=RunMessagesPage, responses=failures)
    def ai_messages(body: RunMessagesQuery, request: Request):
        return request.app.state.store.ai_pending_messages(body)

    @app.post("/api/v1/api-profiles/delete", response_model=ProfileDeleted, responses=failures)
    def delete_profile(body: ProfileDelete, request: Request):
        return request.app.state.store.delete_api_profile(body.profile_id, body.expected_revision)

    @app.post("/api/v1/ai-defaults/query", response_model=AiDefaults, responses=failures)
    def ai_defaults(_body: JsonRequest, request: Request):
        return request.app.state.store.ai_defaults()

    @app.post("/api/v1/ai-defaults/update", response_model=AiDefaults, responses=failures)
    def update_defaults(body: DefaultsUpdate, request: Request):
        return request.app.state.store.save_ai_defaults(body.expected_revision,
            body.model_dump(exclude_unset=True, include={"role_card_code", "api_profile_id"}))

    return app


app = create_app()
