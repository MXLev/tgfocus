"""Windows-local settings and current-user DPAPI credentials; no plaintext secrets on disk."""
import copy
import ctypes
import json
import os
import re
import sys
import tempfile
from pathlib import Path


DEFAULT_CONFIG = {
    "enabled": False, "allPrivate": False,
    "keywords": ["срочно", "позвони", "перезвони", "нужна помощь", "экстренно"],
    "people": [], "chats": [], "showPreview": True, "playSound": True,
    "allowMentions": True, "allowReplies": True, "windowsFocusAutomation": False,
    "aiEnabled": False, "aiProvider": "openrouter", "aiModel": "openrouter/free",
    "aiMinimumPriority": "high", "aiInstruction": "", "aiHourlyLimit": 30,
    "aiExcludedChats": [],
    "startupOfferShown": False,
}
_SECRET_FIELDS = {"apikey", "apikeys", "aiapikey", "apihash", "apiid", "session", "credentials", "password", "token"}
_MAX_CONFIG_BYTES = 1_048_576
_MAX_VAULT_BYTES = 131_072
_VAULT_HEADER = b"TGFocus-DPAPI\x00\x01"


class StorageError(Exception):
    """Fixed public messages deliberately exclude OS errors and serialized values."""


def _reject_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if re.sub(r"[^a-z]", "", str(key).lower()) in _SECRET_FIELDS:
                raise StorageError("Секретные поля нельзя сохранять в настройках. Используй защищённое хранилище.")
            _reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secrets(child)


def normalize_config(config):
    if not isinstance(config, dict):
        raise StorageError("Неверный формат настроек.")
    _reject_secrets(config)
    result = copy.deepcopy(DEFAULT_CONFIG)
    for key in ("enabled", "allPrivate", "showPreview", "playSound", "allowMentions", "allowReplies",
                "windowsFocusAutomation", "aiEnabled", "startupOfferShown"):
        value = config.get(key, result[key])
        if not isinstance(value, bool):
            raise StorageError("Переключатели настроек должны содержать логические значения.")
        result[key] = value
    for key in ("people", "chats", "aiExcludedChats"):
        values = config.get(key, [])
        if not isinstance(values, list):
            raise StorageError("Неверный формат списка диалогов.")
        seen = set()
        for item in values[:500]:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("id", ""))
            if not re.fullmatch(r"-?\d{1,20}", identifier) or identifier in seen:
                continue
            row = {"id": identifier, "name": str(item.get("name", identifier))[:128]}
            if key == "chats":
                row["keywordsOnly"] = item.get("keywordsOnly", True) is not False
            result[key].append(row)
            seen.add(identifier)
    words = config.get("keywords", result["keywords"])
    if not isinstance(words, list):
        raise StorageError("Неверный формат ключевых слов.")
    result["keywords"] = list(dict.fromkeys(str(word).strip()[:200] for word in words[:500] if str(word).strip()))
    provider = config.get("aiProvider", "openrouter")
    result["aiProvider"] = provider if provider in ("openrouter", "deepseek") else "openrouter"
    result["aiModel"] = str(config.get("aiModel", result["aiModel"]))[:200].strip()
    threshold = config.get("aiMinimumPriority", "high")
    result["aiMinimumPriority"] = threshold if threshold in ("normal", "high", "urgent") else "high"
    result["aiInstruction"] = str(config.get("aiInstruction", ""))[:2000]
    try:
        result["aiHourlyLimit"] = max(1, min(300, int(config.get("aiHourlyLimit", 30))))
    except (ValueError, TypeError, OverflowError):
        result["aiHourlyLimit"] = 30
    return result


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".tgfocus-", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_bounded(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Oversized storage file")
    return data


class ConfigStore:
    def __init__(self, directory):
        self.path = Path(directory) / "rules.json"

    def load(self):
        try:
            raw = _read_bounded(self.path, _MAX_CONFIG_BYTES)
        except FileNotFoundError:
            return copy.deepcopy(DEFAULT_CONFIG)
        except Exception:
            raise StorageError("Не удалось прочитать настройки TGFocus.") from None
        try:
            return normalize_config(json.loads(raw))
        except Exception:
            raise StorageError("Не удалось прочитать настройки TGFocus. Исходный файл не изменён.") from None

    def save(self, config):
        normalized = normalize_config(config)
        try:
            data = json.dumps(normalized, ensure_ascii=False).encode("utf-8")
            if len(data) > _MAX_CONFIG_BYTES:
                raise ValueError("Oversized configuration")
            _atomic_write(self.path, data)
        except Exception:
            raise StorageError("Не удалось сохранить настройки TGFocus.") from None


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, *, protect):
    if sys.platform != "win32":
        raise StorageError("Защищённое хранилище DPAPI доступно только на Windows.")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    crypt.CryptProtectData.argtypes = [blob_pointer, ctypes.c_wchar_p, blob_pointer, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_uint32, blob_pointer]
    crypt.CryptProtectData.restype = ctypes.c_int
    crypt.CryptUnprotectData.argtypes = [blob_pointer, ctypes.c_void_p, blob_pointer, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_uint32, blob_pointer]
    crypt.CryptUnprotectData.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    incoming = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = _DataBlob()
    # CRYPTPROTECT_UI_FORBIDDEN; no LOCAL_MACHINE flag => current Windows user.
    if protect:
        ok = crypt.CryptProtectData(ctypes.byref(incoming), "TGFocus credentials", None, None, None, 1,
                                    ctypes.byref(outgoing))
    else:
        ok = crypt.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    try:
        if not ok or not outgoing.data:
            raise StorageError("Windows не разрешила доступ к защищённым данным TGFocus.")
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        if outgoing.data:
            kernel.LocalFree(outgoing.data)


class CredentialVault:
    def __init__(self, directory):
        self.path = Path(directory) / "credentials.dpapi"

    def load(self):
        try:
            encrypted = _read_bounded(self.path, _MAX_VAULT_BYTES + 4096)
        except FileNotFoundError:
            return {}
        except Exception:
            raise StorageError("Не удалось прочитать защищённые данные TGFocus.") from None
        try:
            if not encrypted.startswith(_VAULT_HEADER):
                raise ValueError("Invalid vault")
            data = _dpapi(encrypted[len(_VAULT_HEADER):], protect=False)
            if len(data) > _MAX_VAULT_BYTES:
                raise ValueError("Oversized vault")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Invalid vault root")
            return result
        except Exception:
            raise StorageError("Не удалось открыть защищённые данные TGFocus для этого пользователя Windows.") from None

    def save(self, credentials):
        try:
            if not isinstance(credentials, dict):
                raise ValueError("Invalid credentials")
            data = json.dumps(credentials, ensure_ascii=False).encode("utf-8")
            if len(data) > _MAX_VAULT_BYTES:
                raise ValueError("Oversized credentials")
            encrypted = _dpapi(data, protect=True)
            _atomic_write(self.path, _VAULT_HEADER + encrypted)
        except Exception:
            raise StorageError("Не удалось сохранить защищённые данные TGFocus.") from None
