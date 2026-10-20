"""Pure local rule engine. No Telegram/network/storage dependencies."""
import re
import unicodedata
from dataclasses import dataclass


def normalize(value):
    return unicodedata.normalize("NFKC", str(value)).casefold().replace("ё", "е")


def keyword_match(text, keywords):
    text = normalize(text)
    for keyword in keywords:
        keyword = normalize(keyword).strip()
        if not keyword:
            continue
        # Match complete words/phrases, not 'срочно' inside 'несрочно'.
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(p) for p in keyword.split()) + r"(?!\w)"
        if re.search(pattern, text):
            return keyword
    return None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def decide(message, config):
    if not config.get("enabled", True):
        return Decision(False, "Фильтр на паузе")
    if message.get("outgoing", False):
        return Decision(False, "Исходящее сообщение")
    if message.get("service", False):
        return Decision(False, "Служебное сообщение")
    if message.get("stale", False):
        return Decision(False, "Старое сообщение после переподключения")
    if message.get("muteUnknown", False):
        return Decision(False, "Не удалось проверить уведомления чата в Telegram")
    if message.get("muted", False):
        return Decision(False, "Уведомления чата выключены в Telegram")
    kind = message.get("kind", "private")
    chat_id, sender_id = str(message.get("chatId", "")), str(message.get("senderId", ""))
    match = keyword_match(message.get("text", ""), config.get("keywords", []))
    if kind == "private":
        if sender_id in {str(x["id"]) for x in config.get("people", [])}:
            return Decision(True, "Важный собеседник")
        if config.get("allPrivate", False):
            return Decision(True, "Личные сообщения разрешены")
        if match:
            return Decision(True, "Ключевое слово: " + match)
        return Decision(False, "Личное без ключевых слов")
    if kind == "group" and config.get("allowMentions", True) and message.get("mentioned", False):
        return Decision(True, "Упоминание тебя")
    if kind == "group" and config.get("allowReplies", config.get("allowMentions", True)) and message.get("replyToMe", False):
        return Decision(True, "Ответ на твоё сообщение")
    for chat in config.get("chats", []):
        if str(chat["id"]) == chat_id:
            if not chat.get("keywordsOnly", True):
                return Decision(True, "Чат в исключениях")
            if match:
                return Decision(True, "Исключение + слово: " + match)
            return Decision(False, "В этом чате нужны ключевые слова")
    return Decision(False, "Группа или канал заглушены")
