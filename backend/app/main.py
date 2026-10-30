from contextlib import asynccontextmanager
import os
import hmac
import asyncio

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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
from .auth import AuthConfig, AuthStore, AuthError, Login, PasswordChange, AuthSession, AuthOk, DEFAULT_AUTH_PATH, SESSION_SECONDS

def error(code: str, message: str, status: int):
    return JSONResponse(status_code=status, content=dict(code=code, message=message))


def create_app(probe: AccountProbe = discover_local_accounts, *, conversation_reader=None,
               settings_path=None, message_reader=None, database_path=None, monitor=None, ai_provider=None, ai_auto=None,
               send_transport=None, send_auto=None, auth_path=None, auth_config=None) -> FastAPI:
    @asynccontextmanager
    async def business_lifespan(app):
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

    @asynccontextmanager
    async def lifespan(app):
        app.state.auth_config = auth_config or AuthConfig.environment()
        app.state.auth = AuthStore(auth_path if auth_path is not None else os.environ.get('BRIDGE_AUTH_DATABASE', str(DEFAULT_AUTH_PATH)))
        try:
            async with business_lifespan(app):
                yield
        finally:
            app.state.auth.close()

    development = (auth_config.mode if auth_config else os.environ.get('BRIDGE_ENV')) == 'development'
    app = FastAPI(title="WeChat Agent Bridge", version="1.0.0", lifespan=lifespan,
                  docs_url='/docs' if development else None, redoc_url=None,
                  openapi_url='/openapi.json' if development else None)
    failures = {code: {"model": ApiError} for code in (400, 401, 403, 404, 409, 415, 422, 429, 500, 503)}

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        config = request.app.state.auth_config
        auth = request.app.state.auth
        origin = request.headers.get("origin")
        public = request.url.path in ('/api/v1/health', '/api/v1/auth/login', '/api/v1/auth/session')
        session = await asyncio.to_thread(auth.session, request.cookies.get(config.cookie))
        request.state.auth_session = session
        if not config.allows_host(request.headers.get('host', ''), health=request.url.path == '/api/v1/health'):
            response = error("host_not_allowed", "不允许的访问地址", 400)
        elif config.secure and request.url.scheme != 'https' and request.url.path != '/api/v1/health':
            response = error('https_required', '请使用 HTTPS 访问后台。', 403)
        elif (origin is not None and origin not in config.origins) or request.headers.get('sec-fetch-site') == 'cross-site':
            response = error("origin_not_allowed", "不允许的请求来源", 403)
        elif request.method == 'POST' and origin not in config.origins:
            response = error('origin_not_allowed', '请求缺少有效来源。', 403)
        elif request.method == "POST" and request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            response = error("json_required", "请求必须使用 JSON", 415)
        elif not public and not session:
            response = error('authentication_required', '请先登录后台。', 401)
        elif not public and request.method == 'POST' and not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), session['csrf'].encode()):
            response = error('csrf_invalid', '登录校验已失效，请刷新页面重试。', 403)
        elif request.url.path.startswith('/api/v1/auth/') and len(await request.body()) > 8192:
            response = error('invalid_request', '请求格式不正确', 422)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(AuthError)
    async def auth_error(_request, exc):
        response = error(exc.code, exc.message, exc.status)
        if exc.status == 429:
            response.headers['Retry-After'] = '900'
        return response

    @app.post('/api/v1/auth/session', response_model=AuthSession, responses=failures)
    def auth_session(_body: JsonRequest, request: Request):
        return request.app.state.auth.view(request.state.auth_session)

    @app.post('/api/v1/auth/login', response_model=AuthSession, responses=failures)
    def login(body: Login, request: Request):
        auth, config = request.app.state.auth, request.app.state.auth_config
        token, session = auth.login(body.username, body.password.get_secret_value(), request.client.host if request.client else 'unknown')
        # Rotation prevents session fixation; another device remains logged in.
        old = request.cookies.get(config.cookie)
        if old:
            auth.logout(old)
        response = JSONResponse(auth.view(session).model_dump())
        response.set_cookie(config.cookie, token, max_age=SESSION_SECONDS, secure=config.secure,
                            httponly=True, samesite='strict', path='/')
        return response

    @app.post('/api/v1/auth/logout', response_model=AuthOk, responses=failures)
    def logout(_body: JsonRequest, request: Request):
        config = request.app.state.auth_config
        request.app.state.auth.logout(request.cookies[config.cookie])
        response = JSONResponse({'ok': True})
        response.delete_cookie(config.cookie, path='/', secure=config.secure, httponly=True, samesite='strict')
        return response

    @app.post('/api/v1/auth/change-password', response_model=AuthOk, responses=failures)
    def change_password(body: PasswordChange, request: Request):
        config = request.app.state.auth_config
        request.app.state.auth.change_password(request.cookies[config.cookie], body.current_password.get_secret_value(),
                                              body.new_password.get_secret_value(), request.client.host if request.client else 'unknown')
        response = JSONResponse({'ok': True})
        response.delete_cookie(config.cookie, path='/', secure=config.secure, httponly=True, samesite='strict')
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
        config, auth = socket.app.state.auth_config, socket.app.state.auth
        token = socket.cookies.get(config.cookie)
        if not config.allows_host(socket.headers.get('host', '')) or socket.headers.get("origin") not in config.origins or (config.secure and socket.url.scheme != 'wss'):
            await socket.close(code=1008)
            return
        if not await asyncio.to_thread(auth.session, token):
            await socket.close(code=4401)
            return
        try:
            await socket.app.state.events.serve(socket, socket.app.state.store, lambda: auth.session(token))
        except (WebSocketDisconnect, asyncio.CancelledError):
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
        patch = body.model_dump(exclude_unset=True, include={"ai_enabled", "role_card_code", "api_profile_id", "memory", "scan_interval_seconds", "group_proactive_message_threshold"})
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

    generate_openapi = app.openapi
    def authenticated_openapi():
        schema = generate_openapi()
        schema['components']['securitySchemes'] = {
            'SessionCookie': {'type': 'apiKey', 'in': 'cookie', 'name': '__Host-bridge_session',
                              'description': 'Seven-day HTTPS session; explicit loopback development uses bridge_dev_session.'},
            'CsrfToken': {'type': 'apiKey', 'in': 'header', 'name': 'X-CSRF-Token',
                          'description': 'Returned by POST /api/v1/auth/session. Exact allowed Origin is also required.'},
        }
        for path, methods in schema['paths'].items():
            if 'post' in methods and path not in ('/api/v1/auth/login', '/api/v1/auth/session'):
                methods['post']['security'] = [{'SessionCookie': [], 'CsrfToken': []}]
        return schema
    app.openapi = authenticated_openapi
    return app


app = create_app()
