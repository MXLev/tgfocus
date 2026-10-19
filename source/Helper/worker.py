"""JSON-lines IPC worker. Credentials enter only via stdin, never argv/logs."""
import asyncio
import json
import logging
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone

from rules import decide

logging.disable(logging.CRITICAL)


def emit(event_type, **fields):
    print(json.dumps(dict(type=event_type, **fields), ensure_ascii=False), flush=True)


class Worker:
    def __init__(self):
        self.client = None
        self.config = {}
        self.phone = ""
        self.phone_hash = None
        self.seen = OrderedDict()
        self.monitor_task = None
        self.closing = False
        self.qr_task = None
        self.code_next_type = None
        self.resend_after = 0
        self.auth_retry_after = 0
        self.current_user_id = None
        self.current_usernames = set()
        self.notify_cache = OrderedDict()
        self.notify_generation = 0

    async def cancel_qr(self):
        task, self.qr_task = self.qr_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close_client(self):
        await self.cancel_qr()
        self.current_user_id = None
        self.current_usernames = set()
        self.invalidate_notification_settings()
        self.phone_hash = None
        self.code_next_type = None
        self.resend_after = 0
        if self.monitor_task:
            self.monitor_task.cancel()
            self.monitor_task = None
        if self.client:
            await self.client.disconnect()
            self.client = None

    async def configure(self, request):
        from telethon import TelegramClient, events
        from telethon.tl.types import UpdateNotifySettings
        from telethon.sessions import StringSession
        await self.close_client()
        api_id = int(request.get("apiId", "0"))
        api_hash = request.get("apiHash", "").strip()
        if api_id <= 0 or len(api_hash) != 32 or any(c not in "0123456789abcdefABCDEF" for c in api_hash):
            emit("error", message="Проверь API ID и API Hash с my.telegram.org.")
            return
        self.client = TelegramClient(
            StringSession(request.get("session", "")), api_id, api_hash,
            device_model="Telegram Focus for Mac", system_version="macOS",
            app_version="1.2.0", connection_retries=3, request_retries=2,
            retry_delay=2, timeout=10, flood_sleep_threshold=0,
        )
        self.client.add_event_handler(self.on_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self.on_notify_settings, events.Raw(types=UpdateNotifySettings))
        emit("state", state="connecting", message="Подключаюсь к Telegram…")
        await asyncio.wait_for(self.client.connect(), timeout=25)
        if await self.client.is_user_authorized():
            await self.authorized()
        else:
            emit("state", state="phone", message="Выбери вход по QR-коду или по номеру телефона.")

    def code_sent(self, sent):
        """Use the actual server-selected delivery type, not a guessed destination."""
        self.phone_hash = sent.phone_code_hash
        self.code_next_type = getattr(sent, "next_type", None)
        timeout = max(0, int(getattr(sent, "timeout", None) or 0))
        self.resend_after = time.monotonic() + timeout
        delivery = type(sent.type).__name__
        hints = {
            "SentCodeTypeApp": ("Telegram", "Telegram сообщил об отправке кода в служебный чат Telegram на уже подключённых устройствах. Открой этот чат вручную на телефоне."),
            "SentCodeTypeSms": ("SMS", "Telegram сообщил об отправке SMS на указанный номер. Проверь сообщения на телефоне."),
            "SentCodeTypeSmsWord": ("SMS со словом", "Telegram сообщил об отправке SMS. Введи секретное слово из сообщения целиком."),
            "SentCodeTypeSmsPhrase": ("SMS с фразой", "Telegram сообщил об отправке SMS. Введи секретную фразу из сообщения целиком."),
            "SentCodeTypeCall": ("Звонок", "Telegram выбрал телефонный звонок. Код продиктует автоответчик."),
            "SentCodeTypeFlashCall": ("Входящий звонок", "Telegram выбрал короткий звонок. Код — номер входящего звонка целиком."),
            "SentCodeTypeMissedCall": ("Пропущенный звонок", "Telegram выбрал короткий звонок. Введи последние цифры номера согласно длине кода."),
        }
        if delivery in hints:
            label, message = hints[delivery]
            state = "code"
        else:
            label = {"SentCodeTypeEmailCode": "Электронная почта", "SentCodeTypeSetUpEmailRequired": "Настройка почты",
                     "SentCodeTypeFirebaseSms": "SMS через официальное приложение", "SentCodeTypeFragmentSms": "Fragment"}.get(delivery, "Другой способ")
            message = "Telegram выбрал способ «" + label + "». Этот способ в нашей программе пока не поддерживается. Используй вход по QR-коду."
            state = "authUnsupported"
        emit("delivery", method=label, codeType=delivery, canResend=self.code_next_type is not None,
             resendAt=time.time() + timeout)
        emit("state", state=state, message=message)

    async def start_qr(self):
        from telethon import errors
        await self.cancel_qr()
        qr = await self.client.qr_login()

        async def wait_for_scan():
            try:
                await qr.wait()
                await self.authorized()
            except errors.SessionPasswordNeededError:
                emit("state", state="password", message="QR подтверждён. Введи пароль двухэтапной аутентификации.")
            except asyncio.TimeoutError:
                emit("state", state="qrExpired", message="Срок QR-кода истёк. Нажми «Обновить QR», если хочешь продолжить.")
            except asyncio.CancelledError:
                raise
            except errors.FloodWaitError as error:
                self.auth_retry_after = time.monotonic() + error.seconds
                emit("state", state="qrExpired", message=f"Telegram просит подождать {error.seconds} сек. перед новой попыткой входа.")
            except Exception as error:
                emit("state", state="qrExpired", message="Вход по QR не завершён: " + type(error).__name__ + ". Можно обновить QR.")
        self.qr_task = asyncio.create_task(wait_for_scan())
        # Register Telethon's UpdateLoginToken listener before displaying the token.
        await asyncio.sleep(0)
        emit("state", state="qr", message="В Telegram на телефоне: Настройки → Устройства → Подключить устройство. Отсканируй QR и подтверди вход.")
        emit("qr", url=qr.url, expiresAt=qr.expires.timestamp())

    async def authorized(self):
        me = await self.client.get_me()
        self.current_user_id = getattr(me, "id", None)
        self.current_usernames = {name.casefold() for name in
            [getattr(me, "username", None)] + [getattr(item, "username", None)
                for item in (getattr(me, "usernames", None) or []) if getattr(item, "active", False)] if name}
        emit("session", session=self.client.session.save())
        emit("state", state="connected", message="На связи", name=" ".join(filter(None, [me.first_name, me.last_name])))
        if self.monitor_task:
            self.monitor_task.cancel()
        self.monitor_task = asyncio.create_task(self.monitor())

    async def monitor(self):
        was_connected = True
        while True:
            await asyncio.sleep(5)
            connected = self.client.is_connected() if self.client else False
            if connected != was_connected:
                emit("state", state="connected" if connected else "reconnecting",
                     message="На связи" if connected else "Нет соединения. Telegram переподключается…")
                was_connected = connected

    def invalidate_notification_settings(self):
        self.notify_cache.clear()
        self.notify_generation += 1

    async def on_notify_settings(self, update):
        self.invalidate_notification_settings()

    async def notification_settings(self, key, peer):
        from telethon.tl.functions.account import GetNotifySettingsRequest
        cached = self.notify_cache.get(key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        generation = self.notify_generation
        try:
            settings = await asyncio.wait_for(self.client(GetNotifySettingsRequest(peer)), timeout=5)
        except Exception:
            settings = None
        if generation != self.notify_generation:
            return None  # A live settings update invalidated an in-flight response.
        self.notify_cache[key] = (time.monotonic() + (30 if settings is not None else 5), settings)
        self.notify_cache.move_to_end(key)
        while len(self.notify_cache) > 512:
            self.notify_cache.popitem(last=False)
        return settings

    async def is_chat_muted(self, event, kind):
        from telethon.tl.types import InputNotifyPeer, InputNotifyUsers, InputNotifyChats, InputNotifyBroadcasts, InputNotifyForumTopic
        try:
            peer = await event.get_input_chat()
            header = getattr(getattr(event, "message", None), "reply_to", None)
            topic_id = None
            if kind == "group":
                if header and getattr(header, "forum_topic", False):
                    topic_id = getattr(header, "reply_to_top_id", None) or getattr(header, "reply_to_msg_id", None)
                elif getattr(getattr(event, "chat", None), "forum", False):
                    topic_id = 1  # General topic has no forum reply header.
            if topic_id is not None:
                topic = await self.notification_settings(("topic", event.chat_id, topic_id), InputNotifyForumTopic(peer, topic_id))
                if topic is None:
                    return None
                until = topic.mute_until
                if until is not None:
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=timezone.utc)
                    return until > datetime.now(timezone.utc)
            settings = await self.notification_settings(("peer", event.chat_id), InputNotifyPeer(peer))
            if settings is None:
                return None
            until = settings.mute_until
            if until is None:
                category = {"private": InputNotifyUsers, "group": InputNotifyChats, "channel": InputNotifyBroadcasts}[kind]
                settings = await self.notification_settings(("global", kind), category())
                if settings is None:
                    return None
                until = settings.mute_until
            if until is None:
                return False
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            return until > datetime.now(timezone.utc)
        except Exception:
            return None

    def explicit_mention(self, message):
        from telethon.tl.types import MessageEntityMention, MessageEntityMentionName
        for entity, text in getattr(message, "get_entities_text", lambda: [])():
            if isinstance(entity, MessageEntityMentionName) and entity.user_id == self.current_user_id:
                return True
            if isinstance(entity, MessageEntityMention) and text.lstrip("@").casefold() in self.current_usernames:
                return True
        return False

    async def on_message(self, event):
        try:
            key = (event.chat_id, event.id)
            if key in self.seen:
                return
            self.seen[key] = True
            if len(self.seen) > 4096:
                self.seen.popitem(last=False)
            if event.is_private:
                kind = "private"
            elif event.is_group:
                kind = "group"
            else:
                kind = "channel"
            try:
                chat = await event.get_chat()
            except Exception:
                chat = None
            try:
                sender = await event.get_sender()
            except Exception:
                sender = None
            sender_name = " ".join(filter(None, [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]))
            sender_name = sender_name or getattr(sender, "title", None) or "Telegram"
            title = sender_name if kind == "private" else (getattr(chat, "title", None) or "Группа")
            text = event.raw_text or ""
            display = text or ("Голосовое сообщение" if event.voice else "Видеосообщение" if event.video_note else "Вложение")
            username = getattr(chat, "username", None)
            if username:
                url = "tg://resolve?domain=" + username
                if kind != "private":
                    url += "&post=" + str(event.id)
            elif kind == "private" and getattr(chat, "phone", None):
                url = "tg://resolve?phone=" + str(chat.phone)
            elif str(event.chat_id).startswith("-100"):
                url = "tg://privatepost?channel=" + str(event.chat_id)[4:] + "&post=" + str(event.id)
            else:
                # There is no public desktop deep link for a user without a username/phone,
                # or a legacy basic group. Open Telegram rather than invent an ID link.
                url = "tg://"
            mentioned = bool(getattr(event.message, "mentioned", False))
            reply_to_me = False
            if kind == "group" and getattr(event, "is_reply", False):
                # Telegram's mentioned flag also covers replies. Separate the two
                # before applying independently configurable exceptions.
                personal_mention = self.explicit_mention(event.message)
                if mentioned:
                    try:
                        original = await asyncio.wait_for(event.get_reply_message(), timeout=5)
                        reply_to_me = bool(original and original.out)
                    except Exception:
                        pass  # Unresolved replies must not bypass the mention switch.
                mentioned = personal_mention
            msg = dict(chatId=str(event.chat_id), senderId=str(event.sender_id or ""),
                       kind=kind, text=text, outgoing=bool(event.out),
                       mentioned=mentioned, replyToMe=reply_to_me,
                       service=bool(event.message.action),
                       stale=(datetime.now(timezone.utc) - event.date).total_seconds() > 120)
            result = decide(msg, self.config)
            if result.allowed:
                muted = await self.is_chat_muted(event, kind)
                msg.update(muted=muted is True, muteUnknown=muted is None)
                result = decide(msg, self.config)
            # Only forwarded messages include their body; suppressed messages leave no text in the UI.
            emit("message", allowed=result.allowed, reason=result.reason, title=title,
                 sender=sender_name, kind=kind, text=display[:600] if result.allowed else "",
                 url=url, chatId=str(event.chat_id), messageId=str(event.id))
        except Exception:
            emit("error", message="Не удалось обработать одно сообщение. Следующие продолжают проверяться.", recoverable=True)

    async def dialogs(self):
        result = []
        async for dialog in self.client.iter_dialogs(limit=500):
            if dialog.is_user and getattr(dialog.entity, "is_self", False):
                continue
            kind = "private" if dialog.is_user else "group" if dialog.is_group else "channel"
            result.append(dict(id=str(dialog.id), name=dialog.name or "Без имени", kind=kind))
        emit("dialogs", items=result)

    async def command(self, data):
        from telethon import errors
        action = data.get("command")
        try:
            if action in {"phone", "resend", "qr"} and time.monotonic() < self.auth_retry_after:
                remaining = max(1, int(self.auth_retry_after - time.monotonic()) + 1)
                emit("error", message=f"Telegram просил подождать. Осталось около {remaining} сек.", recoverable=True)
                return True
            if action == "config":
                self.config = data["config"]
                emit("configSaved")
            elif action == "connect":
                await self.configure(data)
            elif action == "phone":
                from telethon import utils
                await self.cancel_qr()
                self.phone = utils.parse_phone(data["phone"])
                if not self.phone:
                    emit("error", message="Проверь номер телефона с кодом страны, например +7…", recoverable=True)
                    return True
                sent = await self.client.send_code_request(self.phone)
                self.code_sent(sent)
            elif action == "resend":
                from telethon import functions
                if not self.phone_hash or self.code_next_type is None:
                    emit("error", message="Telegram не предложил повторную доставку. Можно войти по QR-коду.", recoverable=True)
                elif time.monotonic() < self.resend_after:
                    emit("error", message="Повторная доставка пока недоступна: дождись таймера Telegram.", recoverable=True)
                else:
                    sent = await self.client(functions.auth.ResendCodeRequest(self.phone, self.phone_hash))
                    self.code_sent(sent)
            elif action == "qr":
                await self.start_qr()
            elif action == "cancelAuth":
                await self.cancel_qr()
                emit("state", state="phone", message="Выбери вход по QR-коду или по номеру телефона.")
            elif action == "code":
                try:
                    await self.client.sign_in(self.phone, data["code"].strip(), phone_code_hash=self.phone_hash)
                    await self.authorized()
                except errors.SessionPasswordNeededError:
                    emit("state", state="password", message="Нужен пароль двухэтапной аутентификации.")
            elif action == "password":
                await self.client.sign_in(password=data["password"])
                await self.authorized()
            elif action == "dialogs":
                if self.client and await self.client.is_user_authorized():
                    await self.dialogs()
                else:
                    emit("error", message="Сначала подключи аккаунт Telegram.", recoverable=True)
            elif action == "simulate":
                result = decide(data["message"], self.config)
                emit("simulation", allowed=result.allowed, reason=result.reason)
            elif action == "logout":
                if self.client:
                    await self.client.log_out()
                    await self.close_client()
                emit("loggedOut")
            elif action == "disconnect":
                await self.close_client()
                emit("state", state="disconnected", message="Отключено")
            elif action == "quit":
                return False
        except errors.FloodWaitError as error:
            self.auth_retry_after = time.monotonic() + error.seconds
            emit("error", message=f"Telegram просит подождать {error.seconds} сек. Не запрашивай код повторно до этого времени.", recoverable=True)
        except errors.SessionPasswordNeededError:
            emit("state", state="password", message="Telegram запросил пароль двухэтапной аутентификации.")
        except errors.PhoneCodeInvalidError:
            emit("error", message="Неверный код. Проверь сообщение выбранного способа доставки и повтори.", recoverable=True)
        except errors.PhoneCodeExpiredError:
            emit("state", state="phone", message="Код истёк. Запроси новый.")
        except errors.PasswordHashInvalidError:
            emit("error", message="Неверный пароль двухэтапной аутентификации.", recoverable=True)
        except errors.PhoneNumberInvalidError:
            emit("error", message="Неверный номер телефона. Используй международный формат: +7…", recoverable=True)
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError):
            await self.close_client()
            emit("invalidSession")
        except errors.ApiIdInvalidError:
            emit("error", message="Telegram не принял API ID / API Hash. Проверь данные.")
        except (OSError, asyncio.TimeoutError, ConnectionError):
            emit("error", message="Не удалось связаться с Telegram. Проверь интернет / VPN и нажми «Подключить» ещё раз.")
        except Exception as error:
            # Only the exception class is surfaced; exceptions can contain credentials.
            emit("error", message="Операция не выполнена: " + type(error).__name__ + ". Попробуй подключиться заново.")
        return True

    async def run(self):
        emit("ready")
        try:
            while True:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    break
                try:
                    data = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not await self.command(data):
                    break
        finally:
            await self.close_client()


if __name__ == "__main__":
    asyncio.run(Worker().run())
