"""Single SMTP worker; independent of source reading, model work and SQL."""
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
import json
import smtplib
import ssl
import threading
from . import secret_protection


def send_mail(job):
    fields = job['settings']
    message = EmailMessage()
    message['From'], message['To'] = fields['sender'], fields['recipient']
    message['Message-ID'] = '<'+job['notification_id']+'@wechat-agent-bridge.local>'
    if job['kind'] == 'test':
        message['Subject'] = '微信工作台：测试邮件'
        text = '邮件通知配置测试。此邮件不包含聊天内容。'
    else:
        data = json.loads(job['payload'])
        label = '确认离线' if job['state']=='offline' else '状态异常，可能需要重新扫码'
        message['Subject'] = '微信工作台：'+label
        def local(value):
            return datetime.fromtimestamp(value, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') if value else '未记录'
        text = f"账号：{data.get('nickname') or '未取得昵称'}（{data.get('wxid') or '未取得标识'}）\n状态：{label}\n最后在线：{local(data['last_online_at'])}\n异常发现：{local(data['abnormal_since'])}\n时间均为北京时间。请检查服务器上的微信；若出现二维码，请重新扫码登录。"
    message.set_content(text)
    client, data_started = None, False
    stage = 'credentials'
    def result(status, message, retry=False, code=None, *, safe=False):
        job['diagnostic'] = dict(stage=stage, error_code=code, retryable=retry, safe_to_retry=safe)
        return status, message, retry
    try:
        password = secret_protection.reveal(job['encrypted_password'])
        context = ssl.create_default_context()
        stage = 'connect'
        client = (smtplib.SMTP_SSL(fields['host'], fields['port'], timeout=20, context=context)
                  if fields['security']=='ssl' else smtplib.SMTP(fields['host'], fields['port'], timeout=20))
        stage = 'greeting'
        client.ehlo()
        if fields['security']=='starttls':
            stage = 'tls'
            client.starttls(context=context); client.ehlo()
        stage = 'authentication'
        client.login(fields['username'], password)
        stage = 'sender'
        code, _ = client.mail(fields['sender'])
        if code != 250: raise smtplib.SMTPSenderRefused(code, b'', fields['sender'])
        stage = 'recipient'
        code, _ = client.rcpt(fields['recipient'])
        if code not in (250,251): raise smtplib.SMTPResponseException(code,b'')
        if not job.get('can_send', lambda: True)():
            return result('cancelled', '账号或邮件设置已变化，邮件未提交。', code='settings_or_account_changed', safe=True)
        stage = 'data'
        data_started = True
        code, _ = client.data(message.as_bytes())
        if code != 250: raise smtplib.SMTPDataError(code,b'')
        return result('sent', 'SMTP 服务器已接受邮件，请查看收件箱。')
    except smtplib.SMTPResponseException as exc:
        return result('failed', f'邮件服务器拒绝请求（SMTP {exc.smtp_code}）。',
                      400 <= exc.smtp_code < 500, f'smtp_{exc.smtp_code}', safe=True)
    except ssl.SSLCertVerificationError:
        stage = 'tls'
        return result('failed', '邮件服务器的 TLS 证书验证失败，请检查配置。', code='tls_certificate_invalid', safe=True)
    except smtplib.SMTPNotSupportedError:
        return result('failed', '邮件服务器不支持所需的加密或认证方式，请检查配置。',
                      code='smtp_feature_unsupported', safe=True)
    except (OSError, smtplib.SMTPException) as exc:
        code = ('connection_timeout' if isinstance(exc, TimeoutError) else
                'tls_connection_failed' if isinstance(exc, ssl.SSLError) else
                'smtp_disconnected' if isinstance(exc, smtplib.SMTPServerDisconnected) else 'connection_failed')
        if data_started:
            return result('unknown', '邮件投递结果不确定，请核对邮箱；不会自动重复发送。', code='delivery_uncertain')
        return result('failed', '邮件连接中断或超时，尚未提交邮件，将按计划重试。', True, code, safe=True)
    except Exception:
        if data_started:
            return result('unknown', '邮件投递结果不确定，请核对邮箱；不会自动重复发送。', code='delivery_uncertain')
        return result('failed', '邮件配置或授权码不可用，请重新保存。', code='credentials_unavailable', safe=True)
    finally:
        if client:
            try: client.close()
            except Exception: pass


class Notifications:
    def __init__(self, store, transport=None):
        self.store, self.transport = store, transport or send_mail
        self.stop = threading.Event()
        self.start_lock = threading.Lock()
        self.thread = None

    def start(self):
        with self.start_lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self.loop, name='smtp-notifications', daemon=True)
                self.thread.start()

    def tick(self):
        job = self.store.claim_notification()
        if job:
            job['can_send'] = lambda: self.store.notification_may_send(job)
            status, message, retry = self.transport(job)
            self.store.finish_notification(job, status, message, retry)

    def loop(self):
        while not self.stop.is_set():
            try: self.tick()
            except Exception: pass  # Never publish SMTP exception text or credentials.
            self.stop.wait(1)

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join()
