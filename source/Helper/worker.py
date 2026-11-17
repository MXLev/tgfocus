"""JSON-lines IPC worker. Credentials enter only via stdin, never argv/logs."""
import asyncio
import json
import logging
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone

from rules import decide, Decision
from ai_filter import AIClassifier, eligible as ai_eligible, PRIORITIES, LABELS

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
        self.notify_inflight = {}
        self.notify_slots = asyncio.Semaphore(8)
        self.notify_generation = 0
        self.config_generation = 0
        self.ai = AIClassifier()
        self.ai_key = ""
        self.ai_test_task = None

    async def cancel_qr(self):
        task, self.qr_task = self.qr_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close_client(self):
        self.config_generation += 1
        client, self.client = self.client, None
        await self.stop_monitor()
        await self.cancel_qr()
        if self.ai_test_task:
            self.ai_test_task.cancel()
            self.ai_test_task = None
        self.current_user_id = None
        self.current_usernames = set()
        self.invalidate_notification_settings()
        pending = list(self.notify_inflight.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.notify_inflight.clear()
        self.seen.clear()
        self.phone_hash = None
        self.code_next_type = None
        self.resend_after = 0
        if client:
            await client.disconnect()

    async def stop_monitor(self):
        task, self.monitor_task = self.monitor_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def configure(self, request):
        from telethon import TelegramClient, events, errors
        from telethon.tl.types import UpdateNotifySettings
        from session_cache import BoundedStringSession
        await self.close_client()
        api_id = int(request.get("apiId", "0"))
        api_hash = request.get("apiHash", "").strip()
        if not 0 < api_id < 2**31 or len(api_hash) != 32 or any(c not in "0123456789abcdefABCDEF" for c in api_hash):
            emit("error", message="Проверь API ID и API Hash с my.telegram.org.")
            return
        self.client = TelegramClient(
            BoundedStringSession(request.get("session", "")), api_id, api_hash,
            device_model="TGFocus for Windows" if sys.platform == "win32" else "Telegram Focus for Mac",
            system_version="Windows" if sys.platform == "win32" else "macOS",
            app_version="1.5.0", connection_retries=3, request_retries=2,
            retry_delay=2, timeout=10, flood_sleep_threshold=0,
        )
        self.client.add_event_handler(self.on_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self.on_notify_settings, events.Raw(types=UpdateNotifySettings))
        emit("state", state="connecting", message="Подключаюсь к Telegram…")
        try:
            me = await self.connect_user(self.client)
        except (OSError, asyncio.TimeoutError, errors.ServerError, errors.FloodWaitError) as error:
            # Startup can precede Wi-Fi/VPN. Keep the stored session and retry
            # without forcing the user to re-enter credentials or restart.
            delay = max(2, getattr(error, "seconds", 0))
            self.monitor_task = asyncio.create_task(self.monitor(
                self.client, reconnect=True, require_authorized=bool(request.get("session")), delay=delay))
            return
        if me is not None:
            await self.authorized(me)
        elif request.get("session"):
            await self.close_client()
            emit("invalidSession")
        else:
            emit("state", state="phone", message="Выбери вход по QR-коду или по номеру телефона.")

    @staticmethod
    async def connect_user(client):
        await asyncio.wait_for(client.connect(), timeout=25)
        # is_user_authorized() caches True and masks some RPC errors. A fresh
        # get_me() distinguishes a recovered session from a revoked one.
        return await asyncio.wait_for(client.get_me(), timeout=10)

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

    async def authorized(self, me=None):
        if me is None:
            me = await asyncio.wait_for(self.client.get_me(), timeout=10)
        if me is None:
            await self.close_client()
            emit("invalidSession")
            return
        self.current_user_id = getattr(me, "id", None)
        self.current_usernames = {name.casefold() for name in
            [getattr(me, "username", None)] + [getattr(item, "username", None)
                for item in (getattr(me, "usernames", None) or []) if getattr(item, "active", False)] if name}
        emit("session", session=self.client.session.save())
        emit("state", state="connected", message="На связи", name=" ".join(filter(None, [me.first_name, me.last_name])))
        if self.monitor_task is None or self.monitor_task.done():
            self.monitor_task = asyncio.create_task(self.monitor(self.client))

    async def monitor(self, client, *, reconnect=False, require_authorized=True, delay=2):
        from telethon import errors
        invalid_session = (errors.UnauthorizedError, errors.AuthKeyError, errors.AuthKeyNotFound)
        transient = (OSError, asyncio.TimeoutError, errors.ServerError, errors.FloodWaitError)
        while self.client is client and not self.closing:
            try:
                if not reconnect:
                    # Cancellation of this shielded public future leaves the
                    # connection usable for logout. run_until_disconnected()
                    # would disconnect the client in its cancellation finally.
                    await client.disconnected
                    # Telethon 1.45 reports update-loop failures separately from
                    # transport failures; neither may look like endless retry.
                    error = getattr(client, "_updates_error", None)
                    if error is not None:
                        raise error
                reconnect = True
                self.config_generation += 1
                self.invalidate_notification_settings()
                emit("state", state="reconnecting",
                     message=f"Нет соединения. Следующая попытка через {delay} сек.; можно переподключиться вручную.")
                await asyncio.sleep(delay)
                await client.disconnect()
                me = await self.connect_user(client)
                if self.client is not client or self.closing:
                    return
                if me is None:
                    if require_authorized:
                        await self.end_monitored_connection(client, "invalidSession")
                    else:
                        emit("state", state="phone", message="Выбери вход по QR-коду или по номеру телефона.")
                    return
                await self.authorized(me)
                reconnect, require_authorized, delay = False, True, 2
            except invalid_session:
                await self.end_monitored_connection(client, "invalidSession")
                return
            except transient as error:
                reconnect = True
                delay = max(min(delay * 2, 60), getattr(error, "seconds", 0))
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.end_monitored_connection(client, "error",
                    message="Telegram остановил соединение. Нажми «Переподключить»; данные входа сохранены.")
                return

    async def end_monitored_connection(self, client, event, **fields):
        # Keep ownership until cleanup finishes. A simultaneous manual connect
        # can cancel this task and clean the old client before replacing it;
        # a late invalidSession must never erase a new account's credentials.
        if self.client is not client:
            return
        self.config_generation += 1
        self.invalidate_notification_settings()
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5)
        except Exception:
            pass
        if self.client is client:
            self.client = None
            self.current_user_id = None
            self.current_usernames = set()
            self.seen.clear()
            emit(event, **fields)

    def invalidate_notification_settings(self):
        self.notify_cache.clear()
        self.notify_generation += 1

    async def on_notify_settings(self, update):
        self.invalidate_notification_settings()

    async def notification_settings(self, key, peer):
        cached = self.notify_cache.get(key)
        if cached and time.monotonic() < cached[0]:
            self.notify_cache.move_to_end(key)
            return cached[1]
        generation = self.notify_generation
        pending_key = (generation, key)
        task = self.notify_inflight.get(pending_key)
        if task is None:
            # Bound both active RPCs and waiting distinct peers. Messages for
            # the same peer share one request, including failures/timeouts.
            if len(self.notify_inflight) >= 64:
                return None
            task = asyncio.create_task(self.fetch_notification_settings(key, peer, generation, self.client))
            self.notify_inflight[pending_key] = task
        return await asyncio.shield(task)

    async def fetch_notification_settings(self, key, peer, generation, client):
        from telethon.tl.functions.account import GetNotifySettingsRequest
        async def fetch():
            async with self.notify_slots:
                return await client(GetNotifySettingsRequest(peer))
        try:
            settings = await asyncio.wait_for(fetch(), timeout=5)
        except Exception:
            settings = None
        finally:
            self.notify_inflight.pop((generation, key), None)
        if generation != self.notify_generation or client is not self.client:
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

    def processing_enabled(self):
        return self.config.get("enabled", True) and self.config.get("processingEnabled", True)

    @staticmethod
    def report_ai_status(result):
        # Explicit health signal: capacity limits/missing configuration are not outages.
        connection = "failed" if result.connection_failed else "ready" if result.priority is not None else "unchanged"
        emit("aiStatus", message=result.status, connection=connection)

    async def on_message(self, event):
        # Keep the connection warm, but do not resolve entities, query mute settings,
        # classify messages, or send IPC events while delivery is paused.
        if not self.processing_enabled():
            return
        config, generation = self.config, self.config_generation
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
            result = decide(msg, config)
            use_ai = ai_eligible(msg, config)
            if result.allowed or use_ai:
                muted = await self.is_chat_muted(event, kind)
                msg.update(muted=muted is True, muteUnknown=muted is None)
                result = decide(msg, config)
            mute_generation = self.notify_generation
            if not self.processing_enabled() or generation != self.config_generation:
                return
            # Muted and excluded chats are rejected before any external request.
            if use_ai and ai_eligible(msg, config):
                classified = await self.ai.classify(msg, config, self.ai_key)
                if (not self.processing_enabled() or generation != self.config_generation or
                        mute_generation != self.notify_generation):
                    return  # Never deliver a stale result after a pause/rule/key change.
                self.report_ai_status(classified)
                if classified.priority is not None:
                    threshold = config.get("aiMinimumPriority", "high")
                    allowed = PRIORITIES[classified.priority] >= PRIORITIES.get(threshold, 2)
                    result = Decision(allowed, "ИИ: " + LABELS[classified.priority] + " приоритет")
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
                config = dict(data["config"])
                key = config.pop("aiApiKey", "")
                if config != self.config or key != self.ai_key:
                    self.config_generation += 1
                self.config, self.ai_key = config, key
                emit("configSaved")
            elif action == "testAI":
                if not self.ai_test_task or self.ai_test_task.done():
                    config, generation, key = self.config, self.config_generation, self.ai_key
                    async def test_ai():
                        result = await self.ai.classify({"text": "Нужна помощь с аварией сервера прямо сейчас."}, config, key)
                        if generation == self.config_generation:
                            self.report_ai_status(result)
                    self.ai_test_task = asyncio.create_task(test_ai())
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
                # A background reconnect must not re-authorize while logout is
                # revoking this same session. Failed logout resumes monitoring.
                monitored = self.monitor_task is not None and not self.monitor_task.done()
                await self.stop_monitor()
                try:
                    if not self.client or await asyncio.wait_for(self.client.log_out(), timeout=15) is not True:
                        emit("error", message="Не удалось отозвать сессию в Telegram. Данные входа сохранены; проверь соединение и повтори выход или заверши сеанс в Telegram → Устройства.", recoverable=True)
                        return True
                    await self.close_client()
                    emit("loggedOut")
                finally:
                    if monitored and self.client is not None and not self.closing:
                        self.monitor_task = asyncio.create_task(self.monitor(
                            self.client, reconnect=not self.client.is_connected()))
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
        except (errors.UnauthorizedError, errors.AuthKeyError, errors.AuthKeyNotFound):
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
        commands = asyncio.Queue(maxsize=16)

        async def execute_commands():
            while True:
                request = await commands.get()
                try:
                    await self.command(request)
                finally:
                    commands.task_done()

        command_task = asyncio.create_task(execute_commands())
        discarding = False
        try:
            while True:
                line = await asyncio.to_thread(sys.stdin.readline, 1_048_577)
                if not line:
                    break
                if len(line) > 1_048_576 or discarding:
                    discarding = not line.endswith("\n")
                    continue
                try:
                    data = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                action = data.get("command")
                if action == "quit":
                    break
                if action == "config":
                    # Privacy/pause updates must not wait behind Telegram network
                    # calls such as iter_dialogs/connect/auth. Only config runs
                    # concurrently; account mutations remain sequential and bounded.
                    await self.command(data)
                else:
                    try:
                        commands.put_nowait(data)
                    except asyncio.QueueFull:
                        emit("error", message="Слишком много команд. Дождись завершения текущей операции.", recoverable=True)
        finally:
            self.closing = True
            command_task.cancel()
            await asyncio.gather(command_task, return_exceptions=True)
            await self.close_client()


if __name__ == "__main__":
    # Frozen console workers inherit Windows' code page unless explicitly set.
    # The JSON-lines protocol is UTF-8 on every platform, including redirected pipes.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    if sys.argv[1:] == ["--check-ai-tls"]:
        import ssl
        import certifi
        assert ssl.create_default_context(cafile=certifi.where()).cert_store_stats()["x509_ca"] > 0
        print("AI HTTPS: bundled CA certificates loaded")
    else:
        asyncio.run(Worker().run())
