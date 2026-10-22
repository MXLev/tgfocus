"""Exercise the real Windows-Toasts -> WinRT construction path without publishing.

Only the final native notifier's ``show`` call is replaced. XML documents,
ToastNotification, NotificationData maps and event registration remain real.
"""
import sys
import uuid
from xml.etree import ElementTree


def _check(condition, message):
    if not condition:
        raise RuntimeError("Notification construction smoke: " + message)


def run_notification_construction_smoke():
    if sys.platform != "win32":
        raise RuntimeError("Native notification construction smoke requires Windows")

    from platform_services import _ensure_winrt
    _ensure_winrt()
    from windows_toasts import InteractableWindowsToaster, Toast, ToastAudio
    from winrt.windows.ui.notifications import ToastNotification

    class CaptureNotifier:
        def __init__(self):
            self.notifications = []

        def show(self, notification):
            _check(isinstance(notification, ToastNotification), "expected a real WinRT ToastNotification")
            self.notifications.append(notification)

    identity = "MXLev.TGFocus.ConstructionSmoke." + uuid.uuid4().hex
    broker_created = True
    try:
        toaster = InteractableWindowsToaster("TGFocus construction smoke", notifierAUMID=identity)
    except OSError:
        # Server images can lack the shell notification broker. This narrowly
        # bypasses its constructor, not any of the XML/data/event construction.
        # TypeError/import/API errors deliberately propagate instead of falling back.
        broker_created = False
        toaster = InteractableWindowsToaster.__new__(InteractableWindowsToaster)
        toaster.applicationText = "TGFocus construction smoke"
        toaster.notifierAUMID = identity
        toaster.defaultAUMID = False

    capture = CaptureNotifier()
    # No real ToastNotifier.show may be reached from this point onward.
    toaster.toastNotifier = capture
    callback_hits = []
    fields = ["TGFocus <проверка> & 📨", 'Текст A&B <пример> "цитата"']
    protocol = "tg://resolve?domain=tgfocus_smoke&post=7&text=%D0%A2%D0%B5%D1%81%D1%82%20%3C%26%3E"
    normal = Toast(text_fields=fields, audio=ToastAudio(silent=False),
                   on_failed=lambda _args: callback_hits.append("unexpected failure"))
    silent = Toast(text_fields=["Подключение к ИИ не работает", "Используются локальные правила"],
                   audio=ToastAudio(silent=True), on_activated=lambda _args: callback_hits.append("ai"))
    telegram = Toast(text_fields=["Telegram", "Ссылка & уведомление"],
                     audio=ToastAudio(silent=False), launch_action=protocol)

    for toast in (normal, silent, telegram):
        before = len(capture.notifications)
        toaster.show_toast(toast)
        _check(len(capture.notifications) == before + 1, "show_toast did not reach capture exactly once")
        native = capture.notifications[-1]
        document = ElementTree.fromstring(native.content.get_xml())
        binding = document.find("./visual/binding")
        _check(binding is not None and binding.get("template") == "ToastGeneric", "missing ToastGeneric binding")
        for index, text in enumerate(toast.text_fields, start=1):
            # This exercises the actual projected IMap<string,string>, where a
            # fake Toast constructor/notifier would hide marshaling failures.
            _check(native.data.values[f"text{index}"] == text, "Unicode text binding changed")
            _check(binding.findall("text")[index - 1].text == f"{{text{index}}}", "missing adaptive text placeholder")
        _check(native.data.sequence_number == toast.updates and toast.updates > 0, "adaptive data sequence missing")
        _check(native.tag == toast.tag and native.group == (toast.group or toast.tag), "native identity changed")
        _check(not native.suppress_popup, "smoke toast unexpectedly suppresses popup")

        audio = document.find("audio")
        _check(audio is not None, "audio configuration was lost")
        if toast is silent:
            _check(audio.get("silent") == "true" and audio.get("src") is None,
                   "AI failure toast is not explicitly silent")
            _check(document.get("activationType") != "protocol", "callback toast became a protocol activation")
        else:
            _check(audio.get("silent") != "true" and
                   audio.get("src") == "ms-winsoundevent:Notification.Default", "normal sound configuration changed")
        if toast is telegram:
            # Parsing native XML must round-trip ampersands instead of making
            # query parameters into XML markup or truncating the launch URI.
            _check(document.get("launch") == protocol and document.get("activationType") == "protocol",
                   "Telegram protocol/XML escaping changed")

    _check(not callback_hits, "native construction invoked an activation callback")
    # Only invoke our known Python callback; never fabricate/dereference WinRT
    # event-argument pointers or cause a shell protocol activation.
    silent.on_activated(None)
    _check(callback_hits == ["ai"], "AI settings callback was lost")
    _check(len(capture.notifications) == 3, "unexpected number of native toasts")
    return {"notification_construction": "OK", "cases": 3,
            "real_winrt_xml_data_events": True, "native_show_intercepted": True,
            "broker_constructor_available": broker_created}
