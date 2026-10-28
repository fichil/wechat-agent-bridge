"use strict";
const $ = id => document.getElementById(id);
const state = {csrf:"",accounts:[],account:null,chats:[],chat:null,before:null,mock:true,refreshing:false,lastMessages:"",selection:0,rows:new Map(),epoch:null,hasOlder:false};
state.discovering=false;
state.metadataOnly=false;
const ownSourcesNote=node("p","note");ownSourcesNote.id="own-account-sources";$("selected-account").after(ownSourcesNote);
const discoveryNote=node("p","caption");discoveryNote.id="account-discovery-status";$("accounts").before(discoveryNote);
function clearChat(){state.selection++;state.chat=null;state.before=null;state.hasOlder=false;state.rows.clear();state.lastMessages="";state.epoch=null;$("messages").replaceChildren(node("div","empty","选择一个会话查看记录"));$("chat-name").textContent="会话记录";$("chat-meta").textContent="尚未选择会话";$("coverage").textContent="尚未同步";$("memory-text").textContent="打开会话后查看。";$("memory-meta").textContent="";$("older").hidden=true;}
const reasons = {startup_paused:"重启后待恢复",manual_pause:"手动暂停",acceptance_api_budget_exhausted:"本项目累计模型测试额度已用完",acceptance_send_budget_exhausted:"本轮发送测试额度已用完",read_not_authorized:"请先授权读取这个账号",account_identity_uncertain:"账号身份尚未核验",upload_not_authorized_for_provider:"请为当前 AI 服务重新保存上传授权",reply_not_authorized:"未授权自动回复",paused:"当前已暂停",operation_cancelled:"设置已改变，本次操作取消",provider_text_not_verified:"请先测试文本接口",provider_budget_exhausted:"已达到模型调用上限",existing_draft_or_unknown:"微信中已有草稿或无法确认草稿状态",human_takeover:"主窗口发送等待微信离开前台（读取与 AI 继续）",background_focus_unavailable:"暂时无法确认后台焦点，回复保留待发送",isolated_send_unverified:"独立发送通道尚未验证；读取与 AI 处理继续运行",isolated_channel_draft_conflict:"专用发送窗口存在草稿冲突",send_channel_unavailable:"发送通道暂不可用",target_mismatch:"会话身份发生变化，已停止",windows_dependency_missing:"缺少 Windows 适配依赖，见安装说明",windows_operation_timeout:"微信界面检测超时，已终止工作进程",uia_control_missing_or_ambiguous:"当前微信界面不支持已知定位规则，未继续操作",account_identity_uncertain_open_own_profile:"请在所选微信窗口打开自己的个人资料，再检测",authentication_required:"会话失效，请从命令行给出的完整地址重新打开",vision_not_verified:"视觉接口尚未通过测试",transcription_not_verified:"语音转写接口尚未通过测试",provider_auth_failed:"API 认证失败，请检查密钥",provider_unreachable_or_timeout:"模型服务无法连接或超时",client_attachment_acquisition_unverified:"此微信版本的附件获取尚未验证"};
Object.assign(reasons,{provider_api_key_missing:"缺少 OpenAI API 密钥",provider_quota_exhausted:"OpenAI 可用额度不足，请检查项目余额",provider_rate_limited:"模型服务限流；未自动重试",provider_forbidden:"当前项目无权访问此模型",provider_invalid_response:"模型响应无效或已截断",invalid_reply_output:"模型未返回有效的 JSON 回复",provider_http_failed:"模型接口请求失败；未自动重试",provider_request_failed:"模型请求失败；未自动重试"});
Object.assign(reasons,{account_avatar_not_exposed:"此微信界面尚未暴露可确认的本人头像入口，未操作其他区域",account_discovery_existing_profile:"微信已有资料卡打开，请关闭后刷新",account_profile_identity_ambiguous:"无法确认资料卡属于本人，已停止",account_profile_id_missing_or_ambiguous:"本人资料卡的微信号缺失或存在冲突",account_profile_nickname_missing_or_ambiguous:"本人资料卡的昵称缺失或存在冲突",account_discovery_profile_not_opened:"未确认本人资料卡已打开",account_discovery_instance_changed:"微信实例已变化，请刷新重新识别",account_discovery_restore_failed:"窗口恢复未确认，请检查微信界面",account_discovery_human_takeover:"检测到人工操作，已停止自动识别",account_discovery_failed:"账号信息读取失败，请稍后刷新"});
Object.assign(reasons,{account_discovery_client_busy:"稳定服务正在使用此微信窗口，开发端已跳过自动开卡"});
Object.assign(reasons,{account_nickname_not_exposed:"微信号已识别，昵称未获取：本人公开字段暂未提供昵称",account_wechat_id_not_exposed:"本人公开字段暂未提供微信号",account_own_fields_not_exposed:"本人公开字段暂未提供昵称和微信号",account_identity_record_stale:"本人身份记录与当前微信实例不符，已弃用",account_identity_record_unverified:"现有身份记录未经核验，未使用",account_identity_record_unreadable:"本人身份记录暂无法只读读取",account_identity_sources_conflict:"本人身份来源存在冲突，未发布识别结果",account_discovery_read_only:"当前仅识别本人账号信息，不读取聊天或调用模型"});
let toastTimer;
Object.assign(reasons,{acceptance_target_not_bound:"验收账号和两个会话尚未完成身份绑定，不能读取或运行",acceptance_provider_locked:"本轮验收固定使用指定 OpenAI 地址和模型",acceptance_api_budget_exhausted:"本项目累计模型测试额度已用完，已暂停",acceptance_attempt_already_reserved:"本轮该项验证已预留，禁止重复执行",accessibility_patch_not_enabled:"启动命令未显式允许临时无障碍开关",patch_window_not_bound:"请先绑定当前选定的微信窗口",patch_controls_not_exposed:"临时开关未开放必要控件，已尝试恢复",patch_fingerprint_mismatch:"客户端指纹不匹配，未写入开关",background_focus_changed:"焦点发生变化，后台操作已停止",background_clipboard_changed:"剪贴板序号发生变化，后台操作已停止"});
const backgroundPanel=node("section","identity-panel");backgroundPanel.hidden=true;
backgroundPanel.append(node("h3","","微信账号核验与接入"));
const identityLabel=node("label","field","资料页微信号"),identityInput=node("input");identityInput.id="binding-wechat-id";identityInput.autocomplete="off";identityLabel.append(identityInput);backgroundPanel.append(identityLabel);
for(const [id,label] of [["bind-window","绑定所选窗口"],["enable-accessibility","启用临时无障碍开关"],["restore-accessibility","恢复原值"]]){const button=node("button","wide",label);button.id=id;button.type="button";backgroundPanel.append(button);}
const backgroundState=node("pre","memory-text");backgroundState.id="background-state";backgroundPanel.append(backgroundState,node("p","note","选择并核验账号后接入会话。当前试运行支持已打开群的文本同步和 AI 记忆，可选择全部新文字或前缀模式；发送前逐次核验。"));$("tab-access").append(backgroundPanel);
const identityProbeState=node("p","note","资料核验尚未开始");backgroundPanel.append(identityProbeState);
for(const [id,label] of [["start-identity-probe","开始等待本人资料卡"],["cancel-identity-probe","取消资料核验"]]){const button=node("button","wide",label);button.id=id;button.type="button";backgroundPanel.append(button);}
backgroundPanel.append(node("p","note","绑定窗口并启用临时开关后，先开始等待，再到该微信打开自己的资料卡。最多等待 60 秒；结束后恢复开关并保持暂停，不读取聊天。"));
function renderIdentityProbe(probe){const p=probe||{state:"idle"};const active=["waiting","verifying","restoring"].includes(p.state);$("start-identity-probe").disabled=active;$("cancel-identity-probe").disabled=!active;identityProbeState.textContent=({idle:"尚未开始",waiting:"正在等待本人资料卡",verifying:"正在核对资料",restoring:"正在恢复临时开关",verified:"身份已核验",cancelled:"核验已取消",failed:"核验未完成"}[p.state]||p.state)+(p.reason?" · "+(reasons[p.reason]||p.reason):"");}
Object.assign(reasons,{identity_probe_in_progress:"请先完成或取消本人资料核验",identity_probe_requires_enabled_bound_window:"请先绑定窗口并启用临时开关",identity_probe_timeout:"等待超时，已尝试恢复临时开关",identity_probe_restore_failed:"恢复失败，请保留当前状态以便检查",identity_probe_observation_changed:"核验期间检测到输入、焦点或资料变化，已停止"});
bind("start-identity-probe",async()=>{if(!state.account)throw new Error("请先选择微信窗口");await api("/api/identity-probe",{action:"start",instance_id:state.account.instance_id});await refresh();toast("已开始等待，现在可到微信打开自己的资料卡。");});
bind("cancel-identity-probe",async()=>{await api("/api/identity-probe",{action:"cancel"});await refresh();});
function renderBackground(status){renderExecution(status);renderPrivateScope(status);prefixTrialButton.hidden=!status.prefix_trial_available;prefixTrialNote.hidden=!status.prefix_trial_available;const allNew=status.prefix_trial?.trigger_mode==="all_new_text";allReplyButton.hidden=!status.prefix_trial_available;allReplyButton.disabled=!!status.prefix_trial&&allNew&&!status.paused;prefixTrialButton.disabled=!!status.prefix_trial&&!allNew&&!status.paused;prefixTrialNote.textContent=status.prefix_trial?(status.paused?"自动回复已暂停。":(status.execution?.channels?.some(c=>!c.send_ready)?"AI 处理运行中，独立发送尚不可用。":"自动回复试运行中。"))+(allNew?"当前群的所有新文字都会触发 AI，可综合群聊上下文回复或沉默；连续消息合并，排除历史和 AI 回显。图片/语音内容仍未接入。":"仅 M1测试： 开头的新文本触发，普通聊天不触发。"):"请选择全部新文字或前缀模式；历史不补发。";$("grant-reply").disabled=!!status.acceptance?.read_only;currentGroupButton.hidden=!status.background?.patch_allowed;groupInput.disabled=!!status.test_group;if(status.test_group)groupInput.value=status.test_group;currentGroupButton.disabled=!!status.acceptance?.scope_bound;currentGroupHint.textContent=status.test_group?"指定测试群："+status.test_group+"。先在微信打开此群，保持空草稿，再绑定。":"可选择任一已核验账号。填写当前打开的群聊名称后绑定，程序会复核选中会话。";backgroundPanel.hidden=!status.background?.patch_allowed;$("acceptance-panel").hidden=!status.acceptance;if(status.acceptance){const a=status.acceptance;$("acceptance-quota").textContent=(a.test_budget_enforced===false?`累计测试额度已取消 · 模型请求 ${a.api_used} 次 · 发送预留 ${a.send_used} 次`:`模型请求 ${a.api_used}/${a.api_limit} · 发送预留 ${a.send_used}/${a.send_limit}`)+` · ${a.scope_bound?"范围已绑定":"等待身份及会话核验"}`;}if(status.background){const b=status.background;backgroundState.textContent=JSON.stringify({绑定:b.bound,身份来源:b.identity_source,开关:b.patch.state,需要恢复:b.patch.recovery_required,版本:b.fingerprint?.version,DLL指纹:b.fingerprint?.dll_sha256,当前通道:status.execution?.channels,旧诊断记录:b.capabilities},null,2);}}
bind("bind-window",async()=>{if(!state.account)throw new Error("请先选择微信窗口");await api("/api/accessibility",{action:"bind",instance_id:state.account.instance_id,wechat_id:identityInput.value.trim()});await refresh();});
bind("enable-accessibility",async()=>{await api("/api/accessibility",{action:"enable"});await refresh();toast("控件探测完成；请核验自己的个人资料，读发能力仍按实测结果限制。");});
bind("restore-accessibility",async()=>{await api("/api/accessibility",{action:"restore"});await refresh();});
const currentGroupButton=node("button","wide","接入当前群聊");currentGroupButton.id="bind-current-group";currentGroupButton.type="button";currentGroupButton.hidden=true;backgroundPanel.append(currentGroupButton);
const groupLabel=node("label","field","当前群聊名称"),groupInput=node("input");groupInput.id="binding-group-name";groupInput.autocomplete="off";groupLabel.append(groupInput);backgroundPanel.insertBefore(groupLabel,currentGroupButton);
const currentGroupHint=node("p","note");backgroundPanel.append(currentGroupHint);
bind("bind-current-group",async()=>{if(!state.account)throw new Error("请先选择已核验账号");await api("/api/bind-current-group",{account:state.account.id,group_name:groupInput.value.trim()});await discover();await refresh();toast("当前群已绑定。请保存读取权限后打开会话；上传和发送仍需分别授权。");});
Object.assign(reasons,{bound_console_startup_scope_required:"启动时尚未指定测试账号和群",bound_console_current_group_required:"请在目标微信中打开指定测试群",bound_console_identity_required:"请先绑定并核验发送账号",bound_console_group_changed:"当前会话已变化，请停止操作后重新绑定",acceptance_read_only_scope:"当前仅接通读取，发送尚未开放",client_read_profile_unverified:"该通用读取入口尚未适配；请使用当前测试群绑定"});
function updateTestButtons(config){for(const button of document.querySelectorAll("[data-test]")){const capability=button.dataset.test;button.disabled=config.provider!=="mock"&&((capability==="vision"&&!config.vision_model)||(capability==="transcription"&&!config.transcription_model));}}
const deepseekPreset=node("button","wide","使用 DeepSeek");deepseekPreset.id="use-deepseek";deepseekPreset.type="button";$("tab-ai").insertBefore(deepseekPreset,$("config-form"));
bind("use-deepseek",async()=>{await api("/api/provider-preset",{name:"deepseek"});await loadConfig();await refresh();toast("已选择 DeepSeek。请填写密钥并保存，再测试文本；自动运行保持暂停。");});
Object.assign(reasons,{provider_api_key_missing:"请在 AI 设置中填写当前服务的 API Key 并保存",acceptance_provider_locked:"当前验收仅允许启动时指定的模型服务和模型",unknown_provider_preset:"未知模型服务配置"});
function toast(message){$("toast").textContent=reasons[message]||message;$("toast").hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$("toast").hidden=true,6500);}
async function api(path,body){const options={headers:{},credentials:"same-origin",cache:"no-store"};if(body!==undefined){options.method="POST";options.headers={"Content-Type":"application/json","X-CSRF-Token":state.csrf};options.body=JSON.stringify(body);}const response=await fetch(path,options);const value=await response.json();if(!response.ok)throw new Error(value.error||"请求失败");return value;}
function bind(id,fn){$(id).addEventListener("click",()=>Promise.resolve().then(fn).catch(e=>toast(e.message)));}
function query(extra={}){return new URLSearchParams({account:state.account?.id||"",conversation:state.chat?.id||"",...extra}).toString();}
function target(){if(!state.account||!state.chat)throw new Error("请先选择账号和会话");return {account:state.account.id,conversation:state.chat.id};}
function node(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
function timeLabel(value){if(!value)return "时间未获取";const date=new Date(typeof value==="number"?value*1000:value);return Number.isNaN(date.valueOf())?"时间未获取":date.toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});}
function accountDiscoveryLabel(account){
  if(account.discovery_state==="partial")return account.wechat_id?"微信号已识别，昵称未获取":"昵称已识别，微信号未获取";
  if(account.discovery_state==="identified")return "昵称与微信号已识别";
  if(account.discovery_state==="unavailable")return "暂无法读取账号信息";
  return account.verified?"合成测试":account.identity_certain?"身份已核验 · 适配中":"等待身份核验";
}
function accountSourceText(account){
  const labels={current_process_verified_own_identity_record:"当前实例已核验的本人资料记录（只读）",uia_own_navigation_labeled_fields:"本人导航栏公开字段"};
  return Object.entries(account.discovery_sources||{}).map(([field,source])=>(field==="name"?"昵称":"微信号")+"来源："+(labels[source]||"已核验的本人资料")).join("；");
}
function renderAccountSelection(){
  ownSourcesNote.textContent=state.account?accountSourceText(state.account):"";
  if(state.account)$("selected-account").textContent=state.account.name+" · "+(state.account.wechat_id||"微信号未获取");
}
function renderAccounts(){
  const host=$("accounts");host.replaceChildren();
  if(!state.accounts.length)host.append(node("div","empty small","未发现已登录的微信主窗口。请打开微信并登录，再刷新。"));
  for(const account of state.accounts){
    const card=node("button","account-card"+(state.account?.id===account.id?" selected":""));
    card.append(node("strong","",account.name),node("small","","微信号："+(account.wechat_id||"未获取")),node("small","","窗口："+account.instance_id),node("small","",account.version+" · "+accountDiscoveryLabel(account)));
    if(account.discovery_reason)card.append(node("small","",reasons[account.discovery_reason]||"暂无法读取，请稍后刷新"));
    const sources=accountSourceText(account);if(sources)card.append(node("small","",sources));
    card.addEventListener("click",()=>selectAccount(account).catch(e=>toast(e.message)));host.append(card);
  }
  renderAccountSelection();
}
async function discover(){
  if(state.discovering)return;state.discovering=true;$("discover").disabled=true;discoveryNote.textContent="正在识别本机微信账号…";
  try{
    state.accounts=await api("/api/discover",{});
    if(state.account){state.account=state.accounts.find(a=>a.id===state.account.id)||null;if(!state.account){clearChat();state.chats=[];renderChats();renderExclusions();}}
    renderAccounts();
    discoveryNote.textContent=state.accounts.some(a=>a.discovery_state==="partial")?"部分信息已取得，双字段识别尚未完成。":state.accounts.some(a=>a.discovery_state==="identified")?"昵称与微信号已读取。":state.accounts.some(a=>a.discovery_state==="unavailable")?"已发现微信窗口，账号信息暂无法读取。":"";
  }catch(error){discoveryNote.textContent="账号发现未完成："+(reasons[error.message]||error.message);throw error;}
  finally{state.discovering=false;$("discover").disabled=false;}
}
async function selectAccount(account){
  clearChat();state.account=account;state.chats=[];
  const grant=account.grant||{};for(const flag of ["read","upload","reply","paused"])$("grant-"+flag).checked=!!grant[flag];
  $("probe-account").hidden=state.metadataOnly||account.identity_certain||!!account.discovery_state;$("probe-hint").hidden=$("probe-account").hidden;
  renderAccounts();renderChats();renderExclusions();if(grant.read&&!state.metadataOnly)await loadChats();
}
function renderDiscoveryMode(status){
  state.metadataOnly=status.account_discovery?.metadata_only===true;
  $("grant-form").hidden=state.metadataOnly;$("grant-form").nextElementSibling.hidden=state.metadataOnly;
  $("resume").hidden=state.metadataOnly;
  const access=$("tab-access");access.querySelector("h2").textContent=state.metadataOnly?"本人账号信息":"账号权限";
  document.querySelector('[data-tab="access"]').textContent=state.metadataOnly?"账号信息":"授权";
  document.querySelector(".accounts > .caption").textContent=state.metadataOnly?"自动读取本人昵称和微信号，无需手填或新增授权。":"选择账号，分别授权读取、AI 上传和自动回复。";
  if(state.metadataOnly){
    $("probe-account").hidden=true;$("probe-hint").hidden=true;backgroundPanel.hidden=true;
    privateScopeButton.hidden=true;privateScopeOff.hidden=true;privateScopeNote.hidden=true;
    $("mode").textContent="微信账号后台只读 · 不调用模型";$("run-state").textContent="仅识别账号信息";
    executionNote.textContent="后台只读：不操作微信界面，不读取聊天，不调用模型";
    for(const tab of document.querySelectorAll('[data-tab="ai"], [data-tab="memory"]'))tab.hidden=true;
    $("refresh-chats").hidden=true;$("sync").hidden=true;loadAllButton.hidden=true;
    $("chat-meta").textContent="只读取本人账号信息，不读取会话";$("coverage").textContent="后台只读模式";
    $("chats").replaceChildren(node("div","empty small","当前只识别本人账号信息"));
  }
}
async function loadChats(){if(!state.account)throw new Error("请先选择账号");const selection=state.selection;const chats=await api("/api/conversations",{account:state.account.id});if(selection!==state.selection)return;state.chats=chats;renderChats();renderExclusions();}
function renderExclusions(){const list=$("excluded");list.replaceChildren();const excluded=state.account?.grant?.excluded||[];const known=new Map(state.chats.map(c=>[c.id,c.name]));for(const id of excluded)if(!known.has(id))known.set(id,"已排除会话 "+id.slice(0,8));for(const [id,name] of known){const option=node("option","",name);option.value=id;option.selected=excluded.includes(id);list.append(option);}}
function renderChats(){const host=$("chats");host.replaceChildren();const search=$("search").value.toLowerCase();for(const chat of state.chats.filter(c=>c.name.toLowerCase().includes(search))){const button=node("button","chat-item"+(state.chat?.id===chat.id?" selected":""));button.append(node("strong","",chat.name),node("div","preview",chat.preview||"暂无消息预览"),node("div","chat-label",(chat.kind==="group"?"群聊":chat.kind==="private"?"私聊":"类型未核验")+" · "+(chat.unread==null?"未读数未获取":chat.unread+" 条未读")),node("div","chat-label",timeLabel(chat.updated_at)));button.addEventListener("click",()=>selectChat(chat).catch(e=>toast(e.message)));host.append(button);}if(!host.children.length)host.append(node("div","empty small","暂无可读取的会话"));}
async function selectChat(chat){clearChat();state.chat=chat;const selection=state.selection;$("chat-name").textContent=chat.name;$("chat-meta").textContent="微信 ID："+(chat.wechat_id||"未获取")+" · "+state.account.name;renderChats();await api("/api/sync",target());if(selection!==state.selection)return;await loadMessages();await loadMemory();}
async function loadMessages(append=false){
  if(!state.chat)return;
  const selection=state.selection;
  let data;
  try{data=await api("/api/messages?"+query(append&&state.before?{before:state.before}:{}));}
  catch(error){if(selection===state.selection&&error.message==="read_not_authorized")clearChat();throw error;}
  if(selection!==state.selection)return;
  if(data.epoch!==state.epoch){state.rows.clear();state.lastMessages="";state.epoch=data.epoch;}
  for(const message of data.messages)state.rows.set(message.seq,message);
  const messages=Array.from(state.rows.values()).sort((a,b)=>(a.display_order??a.seq)-(b.display_order??b.seq));
  state.hasOlder=!!data.coverage.has_older;
  state.before=messages[0]?.seq||null;
  $("older").hidden=!messages.length;
  const age=data.coverage.synced_at?Math.max(0,Math.floor(Date.now()/1000-data.coverage.synced_at)):null;
  $("coverage").textContent="最近读取："+timeLabel(data.coverage.synced_at)+(age!==null?"（"+age+" 秒前）":"")+" · "+(data.coverage.complete?"当前可访问记录已读取":"部分记录，非完整历史")+(data.coverage.limitation?" · "+((data.coverage.limitation.startsWith("loaded_text_only")||data.coverage.limitation.startsWith("loaded_rows_only"))?"当前已加载记录；备注/微信号未提供，更早历史无法自动加载":data.coverage.limitation):"");
  if(data.coverage.loaded_count!=null)$("coverage").textContent+=" · 微信已加载 "+data.coverage.loaded_count+" 项，本页显示 "+messages.length+" 项";
  const signature=JSON.stringify(messages);
  if(signature===state.lastMessages)return;
  state.lastMessages=signature;
  const host=$("messages"),oldHeight=host.scrollHeight,oldTop=host.scrollTop,wasNearBottom=host.scrollHeight-host.scrollTop-host.clientHeight<100;
  host.replaceChildren();
  for(const message of messages){
    const wrap=node("article","message"+(message.own?" own":""));
    if(message.own===null)wrap.style.marginInline="auto";
    wrap.append(node("div","message-meta",(message.own===null?"发送者未核验":message.sender)+" · 备注："+(message.sender_remark||"未获取")+" · 微信号："+(message.sender_identity_source&&message.sender_wechat_id?message.sender_wechat_id:"未获取")+" · "+timeLabel(message.timestamp)));
    const bubble=node("div","bubble",message.text);
    if(message.content_status==="client_label_only")bubble.append(node("p","note","微信显示的消息标签；实际内容未获取，不上传 AI。"));
    for(const attachment of message.attachments||[]){
      const card=node("div","attachment");
      card.append(node("strong","",attachment.name),node("small","",Math.ceil(attachment.size/1024)+" KB · "+attachment.mime+" · "+attachment.status));
      card.append(node("small","",attachment.parse_status==="parsed"?"已解析":"尚未解析"));
      if(attachment.parsed_text)card.append(node("p","note",attachment.parsed_text));
      const url="/api/attachment?"+query({id:attachment.id});
      if(attachment.status==="available"){
        if(["image/png","image/jpeg","image/webp"].includes(attachment.mime)){
          const img=node("img");img.src=url;img.alt=attachment.name;img.loading="lazy";
          img.addEventListener("error",()=>{img.hidden=true;card.append(node("small","","图片无法读取"));});card.append(img);
        }else if(["audio/wav","audio/mpeg","audio/mp4"].includes(attachment.mime)){
          const audio=node("audio");audio.controls=true;audio.preload="none";audio.src=url;card.append(audio);
        }else{const link=node("a","","下载附件");link.href=url;link.download=attachment.name;card.append(link);}
      }
      bubble.append(card);
    }
    wrap.append(bubble);host.append(wrap);
  }
  if(!host.children.length)host.append(node("div","empty","当前没有可访问的消息"));
  host.scrollTop=append?oldTop+host.scrollHeight-oldHeight:wasNearBottom?host.scrollHeight:oldTop;
}
async function loadMemory(){if(!state.chat)return;const selection=state.selection;const memory=await api("/api/memory?"+query());if(selection!==state.selection)return;$("memory-text").textContent=memory.summary||"尚未生成摘要。运行后每 3 分钟检查新增内容；没有新增消息不会调用 AI。";$("memory-meta").textContent="更新时间："+timeLabel(memory.updated_at)+" · 已处理位置："+memory.watermark;}
async function loadConfig(){const c=await api("/api/config");updateTestButtons(c);const fields={provider:"provider","base-url":"base_url","model-name":"model","vision-model":"vision_model","transcription-model":"transcription_model","system-prompt":"system_prompt","hourly-limit":"hourly_limit","daily-limit":"daily_limit","reply-limit":"max_reply_chars","send-interval":"send_interval"};for(const [id,key] of Object.entries(fields))$(id).value=c[key];$("api-key").value="";$("api-key").placeholder=c.has_api_key?"已安全保存；留空保留现有密钥":"未配置";$("capabilities").textContent="文本："+(c.capabilities.text?"已通过":"未测试")+" / 图片："+(c.capabilities.vision?"已通过":"未测试")+" / 语音："+(c.capabilities.transcription?"已通过":"未测试");}
const executionNote=node("p","note");executionNote.id="execution-note";$("run-state").parentElement.append(executionNote);
function renderExecution(status){const e=status.execution;if(!e){executionNote.textContent="";return;}const channels=e.channels||[];const readReason=e.lanes?.read?.reason;const parts=[status.paused?("已暂停"+(status.pause_reason?"："+(reasons[status.pause_reason]||status.pause_reason):"")):readReason?"同步受阻："+(reasons[readReason]||readReason):channels.some(c=>c.independent_read)?"独立群窗口同步中":"消息同步运行中",e.generating?"正在生成回复":"AI 待命",`已生成待发送 ${e.ready||0} 条`];if(channels.some(c=>c.send_mode==="main-window"&&c.send_ready))parts.push("主窗口自动回复：请保持目标群打开；已有草稿时等待，不覆盖");else if(channels.some(c=>!c.send_ready))parts.push("独立发送通道未通过，保留人工草稿；不会占用主聊天输入框");for(const reason of e.waiting||[])parts.push(reasons[reason]||reason);if(status.acceptance){const a=status.acceptance;parts.push(a.test_budget_enforced===false?`累计测试额度已取消 · 已调用 ${a.api_used} 次 / 已预留发送 ${a.send_used} 次`:`试运行剩余：模型 ${Math.max(0,a.api_limit-a.api_used)} 次 / 发送 ${a.trial_send_remaining??Math.max(0,a.send_limit-a.send_used)} 次`);}for(const b of e.baselines||[])parts.push(`启动基线 ${timeLabel(b.established_at)} · ${b.message_count} 条历史消息不自动补答`);for(const r of e.reply_recoveries||[])parts.push(`补答 ${r.message_count} 条：${reasons[r.status]||r.status}${r.reason?"（"+(reasons[r.reason]||r.reason)+"）":""}`);executionNote.textContent=parts.join(" · ");}
async function refresh(){if(state.refreshing)return;state.refreshing=true;try{const status=await api("/api/status");connectionBanner.hidden=true;renderBackground(status);renderIdentityProbe(status.identity_probe);state.mock=status.simulation_only;$("mode").textContent=(state.mock?"微信模拟":"微信候选适配")+" · "+(status.provider==="mock"?"模型模拟":"模型真实调用");$("run-state").textContent=status.paused?("已暂停"+(status.pause_reason?"："+(reasons[status.pause_reason]||status.pause_reason):"")):status.execution?.channels?.some(c=>!c.send_ready)?"读取与 AI 运行 · 发送未就绪":status.prefix_trial?(status.prefix_trial.trigger_mode==="all_new_text"?(status.execution?.channels?.some(c=>c.send_mode==="main-window")?"主窗口自动回复 · 全部新文字":"全部新文字自动回复"):"前缀自动回复试运行"):status.acceptance?.read_only?"同步与记忆试运行":"运行中";$("run-state").className="tag"+(status.paused?" muted":"");$("inject-form").hidden=!state.mock;$("hour-usage").textContent=status.usage.hour;$("day-usage").textContent=status.usage.day;$("pending").textContent=status.pending;for(const [id,items] of [["outcomes",status.outcomes],["errors",status.errors]]){const host=$(id);host.replaceChildren();for(const item of (id==="outcomes"?items.slice(0,12):items.slice(-12).reverse())){const el=node("div","event",(item.status?item.status+" · ":"")+(reasons[item.reason]||item.reason));el.append(node("small","",timeLabel(item.created||item.time)));host.append(el);}if(!items.length)host.append(node("p","note","暂无记录"));}if(state.chat)await loadMessages();renderDiscoveryMode(status);}finally{state.refreshing=false;}}
for(const button of document.querySelectorAll("[data-tab]"))button.addEventListener("click",()=>{for(const b of document.querySelectorAll("[data-tab]"))b.classList.toggle("active",b===button);for(const pane of document.querySelectorAll(".tab-content"))pane.hidden=pane.id!=="tab-"+button.dataset.tab;if(button.dataset.tab==="memory")loadMemory().catch(e=>toast(e.message));});
$("search").addEventListener("input",renderChats);
bind("discover",discover);bind("refresh-chats",loadChats);bind("sync",async()=>{await api("/api/sync",target());state.lastMessages="";await loadMessages();});bind("pause",async()=>{await api("/api/pause",{});await refresh();toast("全部暂停；已开始的外部请求不能撤回，返回结果不会继续发送。");});bind("resume",async()=>{await api("/api/resume",{});await refresh();toast("开始处理已授权且未暂停的账号；首次读取只建立历史基线。");});bind("refresh-memory",loadMemory);
const loadAllButton=node("button","older","读取全部已加载记录");loadAllButton.id="load-all-records";loadAllButton.type="button";$("older").before(loadAllButton);
bind("load-all-records",async()=>{
  const scope=target(),selection=state.selection;loadAllButton.disabled=true;
  const check=()=>{if(selection!==state.selection)throw new Error("会话已切换，读取停止");};
  try{
    let coverage=await api("/api/sync",scope);check();
    for(let page=0;coverage.has_older&&page<11;page++){coverage=await api("/api/sync",{...scope,older:true});check();}
    state.rows.clear();state.lastMessages="";await loadMessages();check();
    for(let page=0;page<40;page++){const before=state.before;await loadMessages(true);check();if(before===state.before)break;}
    toast(coverage.has_older?"仍有已加载记录，可继续读取":"已读取微信当前开放的记录。更早未加载历史及发送者资料仍需客户端提供。");
  }finally{loadAllButton.disabled=false;}
});
Object.assign(reasons,{history_cursor_changed:"微信的历史列表已变化，本次分页停止，请重新同步",client_history_loading_unsupported:"已到达当前可访问记录的起点；微信未提供后台加载更早历史的接口"});
bind("older",async()=>{const prior=state.before,selection=state.selection;await loadMessages(true);if(selection===state.selection&&state.before===prior){await api("/api/sync",{...target(),older:true});if(selection!==state.selection)return;await loadMessages(true);if(state.before===prior)toast("没有更多可访问记录");}});
bind("probe-account",async()=>{if(!state.account)throw new Error("请先选择窗口");state.accounts=await api("/api/probe-account",{instance_id:state.account.instance_id});clearChat();state.account=null;state.chats=[];renderAccounts();renderChats();toast("检测完成，请重新选择已核验账号。");});
$("grant-form").addEventListener("submit",async e=>{e.preventDefault();try{if(!state.account)throw new Error("请先选择账号");const account=state.account;const data={account:account.id,excluded:Array.from($("excluded").selectedOptions,o=>o.value)};for(const flag of ["read","upload","reply","paused"])data[flag]=$("grant-"+flag).checked;const grant=await api("/api/grant",data);account.grant=grant;if(state.account?.id!==account.id)return;toast("权限已保存，运行保持暂停。");if(!grant.read||grant.excluded.includes(state.chat?.id)){clearChat();state.chats=[];renderChats();}if(grant.read)await loadChats();await refresh();}catch(error){toast(error.message);}});
$("config-form").addEventListener("submit",async e=>{e.preventDefault();try{await api("/api/config",{provider:$("provider").value,base_url:$("base-url").value.trim(),api_key:$("api-key").value,model:$("model-name").value.trim(),vision_model:$("vision-model").value.trim(),transcription_model:$("transcription-model").value.trim(),system_prompt:$("system-prompt").value,hourly_limit:Number($("hourly-limit").value),daily_limit:Number($("daily-limit").value),max_reply_chars:Number($("reply-limit").value),send_interval:Number($("send-interval").value)});await loadConfig();await refresh();toast("配置已保存，请测试接口；密钥未回显。");}catch(error){toast(error.message);}});
for(const button of document.querySelectorAll("[data-test]"))button.addEventListener("click",async()=>{for(const item of document.querySelectorAll("[data-test]"))item.disabled=true;try{await api("/api/provider-test",{capability:button.dataset.test});toast("能力测试通过，自动处理保持暂停");}catch(error){toast(error.message);}finally{try{await loadConfig();await refresh();}catch(error){toast(error.message);}}});
$("inject-form").addEventListener("submit",async e=>{e.preventDefault();try{await api("/api/mock-inject",{...target(),text:$("inject-text").value,mentions:$("inject-mention").checked});$("inject-text").value="";toast("已注入，运行中的调度器将在下一次同步时处理。");}catch(error){toast(error.message);}});
bind("clear-conversation",async()=>{const scope=target();if(!confirm("删除此会话的本地记录、摘要和附件？微信中的记录不受影响。"))return;await api("/api/clear-memory",scope);state.rows.clear();state.lastMessages="";await loadMessages();await loadMemory();await refresh();});bind("clear-account",async()=>{if(!state.account)throw new Error("请先选择账号");if(!confirm("删除此账号的所有本地记录、摘要和附件？此操作不能撤销。"))return;await api("/api/clear-memory",{account:state.account.id});state.rows.clear();state.lastMessages="";await loadMessages();await loadMemory();await refresh();});
bind("acknowledge-unknown",async()=>{const scope=target();if(!confirm("确认已在微信中核对当前会话的未知发送结果？解除后仍保持全局暂停，只允许后续新消息，绝不重发旧任务。"))return;await api("/api/acknowledge-unknown",{...scope,reviewed:true});await refresh();toast("核对已记录。需要时点击开始运行；旧任务不会重发。");});
const connectionBanner=node("div","coverage");connectionBanner.id="connection-status";connectionBanner.hidden=true;document.querySelector("main").before(connectionBanner);
let connectionToken=location.hash.slice(1),refreshTimer=null;
function connectionError(error){connectionBanner.hidden=false;connectionBanner.textContent=error.message==="authentication_required"?"控制台尚未认证。请使用本次服务的完整启动地址重新打开；仅打开端口地址不会自动登录。":"控制台连接失败："+(reasons[error.message]||error.message);$("mode").textContent="未连接";$("run-state").textContent="页面未连接；后台状态待核实";}
async function connectConsole(){try{const session=await api("/api/session",connectionToken?{token:connectionToken}:undefined);state.csrf=session.csrf;connectionToken="";history.replaceState(null,"",location.pathname);await Promise.all([loadConfig(),discover()]);await refresh();const status=await api("/api/status");if(!state.account&&status.prefix_trial){const account=state.accounts.find(a=>a.id===status.prefix_trial.account);if(account){await selectAccount(account);const chat=state.chats.find(c=>c.id===status.prefix_trial.conversation);if(chat)await selectChat(chat);}}connectionBanner.hidden=true;if(!refreshTimer)refreshTimer=setInterval(()=>refresh().catch(connectionError),2500);}catch(error){connectionError(error);}}
connectConsole();

const prefixTrialButton=node("button","primary wide","开启 M1测试：自动回复");prefixTrialButton.id="prefix-reply-trial";prefixTrialButton.type="button";prefixTrialButton.hidden=true;$("tab-access").prepend(prefixTrialButton);
const prefixTrialNote=node("p","note");prefixTrialNote.hidden=true;prefixTrialButton.after(prefixTrialNote);
bind("prefix-reply-trial",async()=>{if(!state.account||!state.chat)throw new Error("请先选择已绑定的群聊");await api("/api/prefix-trial",{...target(),trigger_mode:"prefix"});await discover();await refresh();toast("已开启前缀自动回复。请从另一账号向测试群发送 M1测试：加问题。");});

const allReplyButton=node("button","primary wide","开启所有新文字自动回复");allReplyButton.id="all-reply-trial";allReplyButton.type="button";allReplyButton.hidden=true;prefixTrialButton.before(allReplyButton);
bind("all-reply-trial",async()=>{if(!state.account||!state.chat)throw new Error("请先选择已绑定的群聊");await api("/api/prefix-trial",{...target(),trigger_mode:"all_new_text"});await discover();await refresh();toast("已开启：当前群全部新文字触发 AI，不需要前缀或 @。");});

const privateScopeButton=node("button","wide","开启全部私聊回复（仅新消息）"),privateScopeOff=node("button","wide","关闭私聊回复"),privateScopeNote=node("p","note");
$("tab-access").prepend(privateScopeButton,privateScopeOff,privateScopeNote);
async function changePrivateScope(enabled){if(!state.account)throw new Error("请先选择已核验账号");await api("/api/reply-scope",{account:state.account.id,all_private:enabled,history:"new_only"});await api("/api/conversations",{account:state.account.id});await api("/api/resume",{});await refresh();}
privateScopeButton.onclick=()=>changePrivateScope(true).catch(e=>toast(e.message));privateScopeOff.onclick=()=>changePrivateScope(false).catch(e=>toast(e.message));
function renderPrivateScope(status){const wanted=(status.reply_scopes||[]).some(s=>s.account_id===state.account?.id&&s.all_private);const p=status.private_discovery?.[state.account?.id];privateScopeNote.textContent=!wanted?"私聊自动接入未开启。":p?.state==="blocked"?`已请求全部私聊；真实后台接入未通过（候选 ${p.candidates||0}，可用 ${p.ready||0}）。现有群回复继续；不读取或上传未核验私聊正文。`:"私聊新消息自动接入已开启；历史不上传。";}

Object.assign(reasons,{console_send_waiting_for_idle:"等待输入空闲，已保留回复",desktop_event_change_observed:"检测到桌面操作，已保留回复等待安全发送",background_input_changed:"输入发生变化，已保留回复",background_clipboard_changed:"剪贴板发生变化，已保留回复",pending:"等待生成",processing:"正在生成",ready:"已生成待发送",ui_confirmed:"客户端发送已确认",cancelled_on_restart:"重启已取消，未重发",cancelled:"已取消",silent:"模型选择不回复",unknown:"结果未知，禁止自动重试",failed:"发送失败，未自动重试",blocked:"处理受阻",deferred:"尚未写入，等待安全发送"});
