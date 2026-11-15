from typing import Any, Dict, List
import json
import os
import time
import threading

from base_plugin import BasePlugin, HookResult, MenuItemData, MenuItemType, MethodHook
from ui.settings import Header, Switch, Divider, Text, Selector
from ui.alert import AlertDialogBuilder
from ui.bulletin import BulletinHelper
from android_utils import run_on_ui_thread, OnClickListener
from client_utils import get_last_fragment, run_on_queue, GLOBAL_QUEUE
from file_utils import get_plugins_dir, ensure_dir_exists
from java import jclass, jint, dynamic_proxy
from java.util import Locale
from hook_utils import find_class, get_private_field
import weakref

from android.graphics import Typeface
from android.os import Bundle
from android.text import TextUtils
from android.util import TypedValue
from android.view import Gravity, View
from android.widget import LinearLayout, ScrollView, TextView

from org.telegram.messenger import AndroidUtilities, MessagesController, UserConfig
from org.telegram.messenger import R as R_tg
from org.telegram.ui import ChatActivity
from org.telegram.ui.ActionBar import Theme
from org.telegram.ui.Components import AvatarDrawable, BackupImageView, LayoutHelper

__id__ = "notification_center"
__name__ = "Notification Center"
__description__ = "Discord-style notification center: saves replies to your messages and mentions of you."
__author__ = "@itNotMax - @MaxExteraPlugins"
__version__ = "5.0.0"
__icon__ = "MaxExteraPluginsStickers/10"
__min_version__ = "12.5.1"
__app_version__ = ">=12.5.1"
__sdk_version__ = ">=1.4.3.3"

DEX_ENTRY_CLASS = "com.exteraplugins.nc.Main"
DEX_BEGIN = "__DEX_BEGIN__"
DEX_END = "__DEX_END__"


def _read_dex_payload():
    import base64
    import zlib
    with open(__file__, "r", encoding="utf-8") as f:
        text = f.read()
    body = text.split("# " + DEX_BEGIN, 1)[1].split("# " + DEX_END, 1)[0]
    payload = "".join(
        line.strip()[1:].strip()
        for line in body.splitlines()
        if line.strip().startswith("#")
    )
    if not payload:
        raise RuntimeError("embedded dex payload is empty")
    return zlib.decompress(base64.b64decode(payload))


class DexBridge:
    def __init__(self):
        self.clazz = None
        self.methods = {}
        self.loaded = False

    def load(self):
        if self.loaded:
            return
        ctx = jclass("org.telegram.messenger.ApplicationLoader").applicationContext
        if not ctx:
            raise RuntimeError("application context is not available")
        byte_buffer = jclass("java.nio.ByteBuffer")
        loader = jclass("dalvik.system.InMemoryDexClassLoader")(
            byte_buffer.wrap(_read_dex_payload()),
            ctx.getClassLoader(),
        )
        self.clazz = loader.loadClass(DEX_ENTRY_CLASS)
        for m in self.clazz.getDeclaredMethods():
            m.setAccessible(True)
            self.methods[str(m.getName())] = m
        self.methods["load"].invoke(None)
        self.loaded = True

    def call(self, name, *args):
        try:
            if self.loaded and name in self.methods:
                return self.methods[name].invoke(None, *args)
        except Exception as e:
            pass
        return None

    def unload(self):
        try:
            if self.loaded and "unload" in self.methods:
                self.methods["unload"].invoke(None)
        except Exception:
            pass
        self.loaded = False

STRINGS = {
    "en": {
        "unknown": "Unknown",
        "unknown_chat": "Unknown chat",
        "just_now": "just now",
        "min_ago": "{} min. ago",
        "hours_ago": "{} h. ago",
        "days_ago": "{} d. ago",
        "user_prefix": "User",
        "chat_prefix": "Chat",
        "archive_empty": "Archive is empty\n\nNotifications older than 3 days will be moved here automatically",
        "no_notifications": "No notifications yet\n\nReplies to your messages and @mentions will appear here",
        "notification_1": "notification",
        "notification_few": "notifications",
        "notification_many": "notifications",
        "media_placeholder": "[media]",
        "notification_center": "Notification Center",
        "notifications_tab": "Notifications",
        "archive_tab": "Archive",
        "clear_all": "Clear all",
        "close": "Close",
        "clear_all_title": "Clear all",
        "clear_all_confirm": "Are you sure you want to delete all notifications?",
        "btn_delete": "Delete",
        "btn_cancel": "Cancel",
        "archive_cleared": "Archive cleared",
        "all_cleared": "All notifications cleared",
        "pill_unavailable": "Pill Stack unavailable: missing {}",
        "pill_already": "Already added to Pill Stack",
        "link_error": "Failed to open link",
        "header_localization": "Localization",
        "language": "Language",
        "header_info": "Information",
        "docs": "Documentation",
        "author_channel": "Author's channel",
        "header_nc": "Notification Center",
        "open_nc": "Open Notification Center",
        "header_button": "Button placement",
        "side_menu": "Side menu",
        "side_menu_sub": "Add button to side menu",
        "main_screen": "Main screen",
        "main_screen_sub": "Add icon to main screen",
        "header_archiving": "Archiving",
        "auto_archive": "Auto archiving",
        "auto_archive_sub": "Move notifications older than 3 days to archive. Removed from archive after 7 days",
        "archive_replied": "Archive after reply",
        "archive_replied_sub": "Automatically archive notifications you replied to or reacted to",
        "header_clearing": "Clearing notifications",
        "confirm_clear": "Clear confirmation",
        "confirm_clear_sub": "Ask for confirmation when clearing all notifications",
        "close_after_clear": "Close after clearing",
        "close_after_clear_sub": "Close notification center after clearing all",
        "header_pill": "Pill Stack",
        "show_pill": "Show in Pill Stack",
        "show_pill_sub": "Show notification count in search bar",
        "header_navbar": "Navigation bar",
        "nav_button": "Navigation bar button",
        "nav_button_sub": "Show a Notification Center button on the bottom navigation bar",
        "pill_1": "ping",
        "pill_many": "pings",
    },
    "ru": {
        "unknown": "Неизвестно",
        "unknown_chat": "Неизвестный чат",
        "just_now": "только что",
        "min_ago": "{} мин. назад",
        "hours_ago": "{} ч. назад",
        "days_ago": "{} дн. назад",
        "user_prefix": "Пользователь",
        "chat_prefix": "Чат",
        "archive_empty": "Архив пуст\n\nУведомления старше 3 дней будут перемещены сюда автоматически",
        "no_notifications": "Уведомлений пока нет\n\nОтветы на ваши сообщения и @упоминания появятся здесь",
        "notification_1": "уведомление",
        "notification_few": "уведомления",
        "notification_many": "уведомлений",
        "media_placeholder": "[медиа]",
        "notification_center": "Центр уведомлений",
        "notifications_tab": "Уведомления",
        "archive_tab": "Архив",
        "clear_all": "Очистить все",
        "close": "Закрыть",
        "clear_all_title": "Очистить все",
        "clear_all_confirm": "Вы уверены, что хотите удалить все уведомления?",
        "btn_delete": "Удалить",
        "btn_cancel": "Отмена",
        "archive_cleared": "Архив очищен",
        "all_cleared": "Все уведомления очищены",
        "pill_unavailable": "Pill Stack недоступен: отсутствует {}",
        "pill_already": "Уже добавлено в Pill Stack",
        "link_error": "Не удалось открыть ссылку",
        "header_localization": "Локализация",
        "language": "Язык",
        "header_info": "Информация",
        "docs": "Документация",
        "author_channel": "Канал автора",
        "header_nc": "Центр уведомлений",
        "open_nc": "Открыть центр уведомлений",
        "header_button": "Расположение кнопки",
        "side_menu": "Боковое меню",
        "side_menu_sub": "Добавить кнопку в боковое меню",
        "main_screen": "Главный экран",
        "main_screen_sub": "Добавить иконку на главный экран",
        "header_archiving": "Архивирование",
        "auto_archive": "Автоматическое архивирование",
        "auto_archive_sub": "Перемещать уведомления старше 3 дней в архив. Из архива удаляются через 7 дней",
        "archive_replied": "Архивировать после ответа",
        "archive_replied_sub": "Автоматически перемещать в архив уведомления, на которые вы ответили или поставили реакцию",
        "header_clearing": "Очистка уведомлений",
        "confirm_clear": "Подтверждение очистки",
        "confirm_clear_sub": "Запрашивать подтверждение при очистке всех уведомлений",
        "close_after_clear": "Закрыть после очистки",
        "close_after_clear_sub": "Закрыть центр уведомлений после очистки всех",
        "header_pill": "Pill Stack",
        "show_pill": "Показать в Pill Stack",
        "show_pill_sub": "Показывать количество уведомлений в строке поиска",
        "header_navbar": "Панель навигации",
        "nav_button": "Кнопка на панели навигации",
        "nav_button_sub": "Добавить кнопку центра уведомлений на нижнюю панель навигации",
        "pill_1": "пинг",
        "pill_many": "пингов",
    },
}

_lock = threading.Lock()
_expanded_dialogs = set()
_expanded_archive_dialogs = set()

def _get_expanded_set(is_archive):
    return _expanded_archive_dialogs if is_archive else _expanded_dialogs

MATCH_PARENT = -1
WRAP_CONTENT = -2

NC_PILL_ID = 23000001
NC_PILL_TAG = "nc_pill"
NC_BUTTON_ID = 230
NC_NAV_TAB_TAG = "nc_nav_tab"

THREE_DAYS = 3 * 86400
SEVEN_DAYS = 7 * 86400


def _safe_color(color_val):
    try:
        c = int(color_val)
        c = c & 0xFFFFFFFF
        if c == 0:
            return jint(0xFF000000)
        if (c & 0xFF000000) == 0 and c != 0:
            c = c | 0xFF000000
        if c > 0x7FFFFFFF:
            c = c - 0x100000000
        return jint(int(c))
    except:
        return jint(0xFF000000)


def _safe_alpha_color(color_val, alpha):
    try:
        c = int(color_val) & 0xFFFFFFFF
        c = (c & 0x00FFFFFF) | ((alpha & 0xFF) << 24)
        if c > 0x7FFFFFFF:
            c = c - 0x100000000
        return jint(int(c))
    except:
        return jint(0x1A000000)


def _has_sliding_fragment(fragment):
    try:
        container = fragment.rightSlidingDialogContainer
        if container is not None:
            try:
                if container.hasFragment():
                    return True
            except:
                cf = get_private_field(container, "currentFragment")
                if cf is not None:
                    return True
    except:
        pass
    return False


def _data_dir():
    d = os.path.join(get_plugins_dir(), "notification_center_data")
    ensure_dir_exists(d)
    return d


def _data_path(account):
    return os.path.join(_data_dir(), f"notifications_{account}.json")


def _archive_path(account):
    return os.path.join(_data_dir(), f"archive_{account}.json")


def _replied_path(account):
    return os.path.join(_data_dir(), f"replied_{account}.json")


def _load_json(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def _save_json(path, items):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
    except:
        pass


def _load_notifications(account):
    return _load_json(_data_path(account))


def _save_notifications(account, items):
    _save_json(_data_path(account), items)


def _load_archive(account):
    return _load_json(_archive_path(account))


def _save_archive(account, items):
    _save_json(_archive_path(account), items)


def _load_replied(account):
    return set(_load_json(_replied_path(account)))


def _save_replied(account, items):
    _save_json(_replied_path(account), list(items))


def _get_user_name(user, default="Unknown"):
    if user is None:
        return default
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    name = f"{first} {last}".strip()
    return name if name else default


def _get_chat_title(chat, default="Unknown chat"):
    if chat is None:
        return default
    return getattr(chat, "title", "") or default


def _navigate_to_message(dialog_id, msg_id):
    fragment = get_last_fragment()
    if not fragment:
        return
    args = Bundle()
    if dialog_id > 0:
        args.putLong("user_id", int(dialog_id))
    else:
        args.putLong("chat_id", int(-dialog_id))
    args.putInt("message_id", int(msg_id))
    chat_activity = ChatActivity(args)
    fragment.presentFragment(chat_activity)


def _run_archiving(account, auto_archive_enabled, auto_archive_replied_enabled):
    now = time.time()
    with _lock:
        notifs = _load_notifications(account)
        if not notifs:
            return

        if auto_archive_replied_enabled:
            replied_set = _load_replied(account)
            archive = _load_archive(account)
            keep = []
            for n in notifs:
                msg_id = n.get("msg_id", 0)
                if msg_id in replied_set:
                    archive.insert(0, n)
                else:
                    keep.append(n)
            if len(keep) != len(notifs):
                _save_notifications(account, keep)
                notifs = keep
                archive = [a for a in archive if now - a.get("timestamp", 0) <= SEVEN_DAYS]
                if len(archive) > 500:
                    archive = archive[:500]
                _save_archive(account, archive)

        if auto_archive_enabled:
            archive = _load_archive(account)
            keep = []
            for n in notifs:
                if now - n.get("timestamp", 0) > THREE_DAYS:
                    archive.insert(0, n)
                else:
                    keep.append(n)
            if len(keep) != len(notifs):
                _save_notifications(account, keep)
                archive = [a for a in archive if now - a.get("timestamp", 0) <= SEVEN_DAYS]
                if len(archive) > 500:
                    archive = archive[:500]
                _save_archive(account, archive)
        else:
            archive = _load_archive(account)

        if archive:
            cleaned = [a for a in archive if now - a.get("timestamp", 0) <= SEVEN_DAYS]
            if len(cleaned) != len(archive):
                _save_archive(account, cleaned)


class ProcessNewMessagesHook:
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        try:
            messages = param.args[0]
            if messages is None:
                return
            account = param.thisObject.currentAccount
            for i in range(messages.size()):
                msg_obj = messages.get(i)
                if msg_obj is None:
                    continue
                msg = msg_obj.messageOwner
                if msg is None:
                    continue
                self.plugin._process_message(msg, account)
        except:
            pass


class ChatActivitySendHook:
    def __init__(self, plugin):
        self.plugin = plugin

    def before_hooked_method(self, param):
        try:
            self.plugin._track_reply_from_chat_activity(param.thisObject)
        except:
            pass


class HideFieldPanelHook:
    def __init__(self, plugin):
        self.plugin = plugin

    def before_hooked_method(self, param):
        try:
            self.plugin._track_reply_from_chat_activity(param.thisObject)
        except:
            pass


class ReactionSendHook:
    def __init__(self, plugin):
        self.plugin = plugin

    def before_hooked_method(self, param):
        try:
            if not self.plugin.get_setting("auto_archive_replied", False):
                return
            args = param.args
            if args is None:
                return
            msg_id = None
            try:
                arg_count = len(args)
            except:
                try:
                    arg_count = args.length
                except:
                    arg_count = 0
            for i in range(min(arg_count, 4)):
                try:
                    arg = args[i]
                    if arg is None:
                        continue
                    try:
                        mid = arg.getId()
                        if mid and int(mid) != 0:
                            msg_id = int(mid)
                            break
                    except:
                        pass
                    try:
                        mid = get_private_field(arg, "id")
                        if mid and int(mid) != 0:
                            msg_id = int(mid)
                            break
                    except:
                        pass
                    try:
                        owner = get_private_field(arg, "messageOwner")
                        if owner is not None:
                            mid = getattr(owner, "id", 0)
                            if mid and int(mid) != 0:
                                msg_id = int(mid)
                                break
                    except:
                        pass
                except:
                    continue
            if msg_id is None or msg_id == 0:
                return
            account = UserConfig.selectedAccount
            with _lock:
                replied_set = _load_replied(account)
                replied_set.add(msg_id)
                if len(replied_set) > 1000:
                    replied_list = list(replied_set)
                    replied_set = set(replied_list[-1000:])
                _save_replied(account, replied_set)
            try:
                auto_archive = self.plugin.get_setting("auto_archive", True)
                auto_archive_replied = self.plugin.get_setting("auto_archive_replied", False)
                if auto_archive_replied:
                    _run_archiving(account, auto_archive, auto_archive_replied)
            except:
                pass
        except:
            pass


class NotificationCenterActionBarHook:
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        try:
            fragment = param.thisObject
            if not self.plugin._is_main_dialogs_screen(fragment):
                return
            if _has_sliding_fragment(fragment):
                return
            self.plugin._add_icon_to_fragment(fragment)
        except:
            pass


class DialogsCreateViewHook:
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        try:
            fragment = param.thisObject
            if not self.plugin._is_main_dialogs_screen(fragment):
                return
            self.plugin._sliding_open = False
            self.plugin._injected_btn = None
            run_on_ui_thread(lambda: self.plugin._add_icon_to_fragment(fragment), 300)
        except:
            pass


class DialogsFullyVisibleHook:
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        try:
            fragment = param.thisObject
            if not self.plugin._is_main_dialogs_screen(fragment):
                return
            if _has_sliding_fragment(fragment):
                return
            run_on_ui_thread(lambda: self.plugin._add_icon_to_fragment(fragment), 200)
        except:
            pass


class SlidingPresentHook:
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        try:
            self.plugin._on_sliding_opened()
        except:
            pass


class SlidingFinishPreviewHook:
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        try:
            self.plugin._on_sliding_closed()
        except:
            pass


class NavTabHook:
    def __init__(self, plugin):
        self.plugin = plugin

    def after_hooked_method(self, param):
        try:
            if not self.plugin.get_setting("show_in_navbar", True):
                return
            activity = param.thisObject
            run_on_ui_thread(lambda: self.plugin._inject_nav_tab(activity), 150)
        except:
            pass



# ============================================================
# MaxExteraPlugins Shared Author & Channel Subscription Module
# ============================================================
class AuthorChannelHelper:
    CONFIG_URL = "https://raw.githubusercontent.com/MaxExteraPlugins/config/main/links.json"

    DEFAULT_LINKS = {
        "channel_username": "MaxExteraPlugins",
        "channel_url": "https://t.me/MaxExteraPlugins",
        "trial_days": 7,
        "header": {"ru": "MaxExteraPlugins", "en": "MaxExteraPlugins"},
        "buttons": [
            {"title": {"ru": "Канал автора", "en": "Author's Channel"}, "icon": "msg_channel", "url": "https://t.me/MaxExteraPlugins"},
            {"title": {"ru": "Поддержать автора", "en": "Support author"}, "icon": "msg_share", "url": "https://t.me/itNotMax"},
            {"title": {"ru": "Сообщить о баге", "en": "Report a bug"}, "icon": "msg_help", "url": "https://t.me/itNotMax"}
        ]
    }

    _TEXTS = {
        "install_card_title": {"ru": "Спасибо за установку!", "en": "Thank you for installing!"},
        "install_card_msg": {
            "ru": "Надеемся, что плагин будет вам полезен!\n\nМы делаем плагины бесплатными и доступными для всех: пожалуйста, поддержите нас подпиской. Там будут только важные новости, обновления и розыгрыши.",
            "en": "We hope this plugin will be useful to you!\n\nWe keep our plugins free and open for everyone: please support us with a subscription. We only post key news, updates, and giveaways."
        },
        "subscribe_btn": {"ru": "Подписаться", "en": "Subscribe"},
        "remind_later_btn": {"ru": "Напомнить позже", "en": "Remind later"},
        "check_sub_btn": {"ru": "Проверить подписку", "en": "Check subscription"},
        "trial_expired_title": {"ru": "Пожалуйста, подпишитесь на канал", "en": "Please subscribe to the channel"},
        "trial_expired_msg": {
            "ru": "Пожалуйста, подпишитесь на наш официальный канал.\n\nМы делаем плагины бесплатно, ничего не требуя взамен. До подписки функции плагина временно приостановлены.",
            "en": "Please subscribe to our official channel.\n\nWe develop plugins for free and ask nothing in return. Plugin features are paused until you subscribe."
        },
        "sub_thank_you": {"ru": "Спасибо за подписку на наш канал!", "en": "Thank you for subscribing to our channel!"},
        "sub_thank_you_after_expired": {"ru": "Спасибо за подписку на наш канал!\nПлагин продолжит работу!", "en": "Thank you for subscribing to our channel!\nThe plugin will continue working!"},
        "sub_not_found": {"ru": "Подписка не обнаружена. Пожалуйста, подпишитесь на канал.", "en": "Subscription not found. Please subscribe to the channel."},
        "settings_channel": {"ru": "Канал автора", "en": "Author's Channel"},
        "settings_donate": {"ru": "Поддержать автора", "en": "Support author"},
        "settings_bug_report": {"ru": "Сообщить о баге", "en": "Report a bug"}
    }

    @classmethod
    def is_ru(cls) -> bool:
        try:
            from org.telegram.messenger import LocaleController
            cur = str(LocaleController.getInstance().getCurrentLocale().getLanguage() or "").lower()
            if cur.startswith(("ru", "uk", "be")): return True
            if cur: return False
        except Exception:
            pass
        try:
            from java.util import Locale
            code = str(Locale.getDefault().getLanguage() or "").lower()
            if code.startswith(("ru", "uk", "be")): return True
        except Exception:
            pass
        return False

    @classmethod
    def tr(cls, key: str) -> str:
        entry = cls._TEXTS.get(key, {})
        return entry.get("ru" if cls.is_ru() else "en", entry.get("en", key))

    def __init__(self, plugin, icon_sticker=None):
        self.plugin = plugin
        self.icon_sticker = icon_sticker or getattr(plugin, "__icon__", "MaxExteraPluginsStickers/1")
        self._stopped = False
        self._active_dialog = None
        self._last_known_sub = None

    def on_load(self):
        self._stopped = False
        self._active_dialog = None
        self._last_known_sub = None
        self._load_remote_links_async()
        self._check_initial_subscription_flow()
        self._start_subscription_watcher()

    def on_event(self, event_type):
        try:
            name = str(event_type).upper()
            if name.endswith("START") or name.endswith("RESUME"):
                self._check_subscription_change_instant()
        except Exception:
            pass

    def on_unload(self):
        self._stopped = True
        self._dismiss_active_dialog()

    def _get_links(self) -> dict:
        raw = self.plugin.get_setting("remote_links_cache", None)
        if raw:
            try:
                import json
                data = json.loads(raw)
                if isinstance(data, dict): return data
            except Exception:
                pass
        return self.DEFAULT_LINKS

    def _load_remote_links_async(self):
        def worker():
            try:
                import urllib.request, json
                req = urllib.request.Request(self.CONFIG_URL, headers={"User-Agent": "MaxExteraPlugins"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if isinstance(data, dict):
                            self.plugin.set_setting("remote_links_cache", json.dumps(data))
                            self._resolve_channel_in_telegram(data.get("channel_username", "MaxExteraPlugins"))
            except Exception:
                pass
        import threading
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _open_url(self, url: str, view=None):
        try:
            from org.telegram.messenger.browser import Browser
            from org.telegram.messenger import ApplicationLoader
            from client_utils import get_last_fragment
            ctx = None
            if view is not None:
                ctx = view.getContext()
            if ctx is None:
                frag = get_last_fragment()
                if frag is not None:
                    ctx = frag.getParentActivity()
            if ctx is None:
                ctx = ApplicationLoader.applicationContext
            Browser.openUrl(ctx, url)
        except Exception:
            pass

    def _resolve_channel_in_telegram(self, username: str):
        try:
            from org.telegram.messenger import UserConfig, MessagesController
            from org.telegram.tgnet import TLRPC, ConnectionsManager, RequestDelegate
            clean_user = username.lstrip("@")
            acc = int(UserConfig.selectedAccount)
            mc = MessagesController.getInstance(acc)
            if not mc: return
            req = TLRPC.TL_contacts_resolveUsername()
            req.username = clean_user
            helper_ref = self
            class Delegate(RequestDelegate):
                def run(self, response, error):
                    if error is None and response is not None:
                        chats = getattr(response, "chats", None)
                        if chats is not None and chats.size() > 0:
                            mc.putChats(chats, False)
                            c = chats.get(0)
                            cid = getattr(c, "id", 0)
                            if cid > 0:
                                helper_ref.plugin.set_setting("resolved_channel_id", int(cid))
            ConnectionsManager.getInstance(acc).sendRequest(req, Delegate())
        except Exception:
            pass

    def is_subscribed(self) -> bool:
        try:
            from org.telegram.messenger import UserConfig, MessagesController
            links = self._get_links()
            target = str(links.get("channel_username", "MaxExteraPlugins") or "MaxExteraPlugins").lower().lstrip("@")
            saved_cid = self.plugin.get_setting("resolved_channel_id", 0)

            for acc in range(UserConfig.MAX_ACCOUNT_COUNT):
                uconf = UserConfig.getInstance(acc)
                if not uconf.isClientActivated(): continue
                mc = MessagesController.getInstance(acc)
                if not mc: continue

                if saved_cid:
                    try:
                        from java.lang import Long as JLong
                        c = mc.getChat(JLong(int(saved_cid)))
                        if c is not None:
                            left = bool(getattr(c, "left", True))
                            kicked = bool(getattr(c, "kicked", False))
                            if not left and not kicked: return True
                    except Exception:
                        pass

                try:
                    chat = mc.getUserOrChat(target)
                    if chat is not None:
                        cid = getattr(chat, "id", 0)
                        if cid and not saved_cid:
                            self.plugin.set_setting("resolved_channel_id", int(cid))
                        left = bool(getattr(chat, "left", True))
                        kicked = bool(getattr(chat, "kicked", False))
                        if not left and not kicked: return True
                except Exception:
                    pass

                try:
                    dialogs = mc.allDialogs
                    if dialogs is not None:
                        limit = min(dialogs.size(), 300)
                        for i in range(limit):
                            d = dialogs.get(i)
                            did = getattr(d, "id", 0)
                            if did < 0:
                                from java.lang import Long as JLong
                                c = mc.getChat(JLong(-did))
                                if c is not None:
                                    uname = str(getattr(c, "username", "") or "").lower()
                                    if uname == target:
                                        cid = getattr(c, "id", 0)
                                        if cid and not saved_cid:
                                            self.plugin.set_setting("resolved_channel_id", int(cid))
                                        left = bool(getattr(c, "left", True))
                                        kicked = bool(getattr(c, "kicked", False))
                                        if not left and not kicked: return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def is_allowed(self) -> bool:
        import time
        if self.is_subscribed():
            if not self.plugin.get_setting("was_subscribed", False):
                was_exp = bool(self.plugin.get_setting("was_expired_state", False))
                self.plugin.set_setting("was_subscribed", True)
                self.plugin.set_setting("was_expired_state", False)
                self._notify_subscribed(was_expired=was_exp)
            return True

        if self.plugin.get_setting("was_expired_state", False) or self.plugin.get_setting("was_subscribed", False):
            self.plugin.set_setting("was_subscribed", False)
            self.plugin.set_setting("was_expired_state", True)
            self._show_expired_card()
            return False

        now = time.time()
        install_time = float(self.plugin.get_setting("install_time", 0.0) or 0.0)
        if install_time <= 0:
            install_time = now
            self.plugin.set_setting("install_time", install_time)

        trial_days = float(self._get_links().get("trial_days", 7))
        trial_sec = trial_days * 86400.0

        if (now - install_time) < trial_sec:
            return True

        self.plugin.set_setting("was_expired_state", True)
        self._show_expired_card()
        return False

    def prepend_settings(self, original_items=None) -> list:
        from ui.settings import Header, Text, Divider
        links = self._get_links()
        buttons_config = links.get("buttons")

        author_items = []
        if isinstance(buttons_config, list):
            for btn in buttons_config:
                if not isinstance(btn, dict): continue
                url = str(btn.get("url") or "").strip()
                if not url: continue

                title_data = btn.get("title")
                if isinstance(title_data, dict):
                    title = title_data.get("ru" if self.is_ru() else "en") or title_data.get("en") or title_data.get("ru") or ""
                elif isinstance(title_data, str) and title_data:
                    title = title_data
                else:
                    title = btn.get("title_ru" if self.is_ru() else "title_en") or btn.get("title_ru") or btn.get("title_en") or ""

                if not title: continue
                icon = str(btn.get("icon") or "msg_channel").strip()

                def make_click(u):
                    return lambda v=None: self._open_url(u, v)

                author_items.append(Text(text=title, icon=icon, on_click=make_click(url)))
        else:
            for k, text_key, icon_name in [
                ("channel_url", "settings_channel", "msg_channel"),
                ("donate_url", "settings_donate", "msg_share"),
                ("bug_report_url", "settings_bug_report", "msg_help")
            ]:
                u = str(links.get(k) or "").strip()
                if u:
                    author_items.append(Text(text=self.tr(text_key), icon=icon_name, on_click=lambda v=None, target_u=u: self._open_url(target_u, v)))

        res = []
        if author_items:
            hdr = links.get("header") or "MaxExteraPlugins"
            if isinstance(hdr, dict):
                hdr_text = hdr.get("ru" if self.is_ru() else "en") or hdr.get("ru") or hdr.get("en") or "MaxExteraPlugins"
            else:
                hdr_text = str(hdr)
            res.append(Header(text=hdr_text))
            res.extend(author_items)
            res.append(Divider())

        if original_items:
            res.extend(original_items)
        return res

    def _dismiss_active_dialog(self):
        try:
            if self._active_dialog is not None:
                self._active_dialog.dismiss()
        except Exception:
            pass
        self._active_dialog = None

    def _is_dialog_active(self) -> bool:
        try:
            if self._active_dialog is not None:
                return bool(self._active_dialog.isShowing())
        except Exception:
            pass
        return False

    def _notify_subscribed(self, was_expired=False):
        self.plugin.set_setting("was_subscribed", True)
        from ui.bulletin import BulletinHelper
        msg_key = "sub_thank_you_after_expired" if was_expired else "sub_thank_you"
        BulletinHelper.show_success(self.tr(msg_key))

    def _check_subscription_change_instant(self):
        try:
            from android_utils import run_on_ui_thread
            cur_sub = self.is_subscribed()
            prev_sub = self._last_known_sub
            if prev_sub is not None and prev_sub != cur_sub:
                self._last_known_sub = cur_sub
                if cur_sub:
                    was_exp = bool(self.plugin.get_setting("was_expired_state", False))
                    self.plugin.set_setting("was_subscribed", True)
                    self.plugin.set_setting("was_expired_state", False)
                    self._dismiss_active_dialog()
                    self._notify_subscribed(was_expired=was_exp)
                else:
                    self.plugin.set_setting("was_subscribed", False)
                    self.plugin.set_setting("was_expired_state", True)
                    self._dismiss_active_dialog()
                    self._show_expired_card()
            else:
                self._last_known_sub = cur_sub
        except Exception:
            pass

    def _start_subscription_watcher(self):
        def watcher_loop():
            import time
            from android_utils import run_on_ui_thread
            time.sleep(2.0)
            if self._stopped: return
            self._last_known_sub = self.is_subscribed()

            while not self._stopped:
                try:
                    time.sleep(2.0)
                    if self._stopped: break
                    cur_sub = self.is_subscribed()
                    prev_sub = self._last_known_sub

                    if prev_sub is not None and prev_sub != cur_sub:
                        self._last_known_sub = cur_sub
                        if cur_sub:
                            was_exp = bool(self.plugin.get_setting("was_expired_state", False))
                            self.plugin.set_setting("was_subscribed", True)
                            self.plugin.set_setting("was_expired_state", False)
                            def on_sub():
                                self._dismiss_active_dialog()
                                self._notify_subscribed(was_expired=was_exp)
                            run_on_ui_thread(on_sub)
                        else:
                            self.plugin.set_setting("was_subscribed", False)
                            self.plugin.set_setting("was_expired_state", True)
                            def on_unsub():
                                self._dismiss_active_dialog()
                                self._show_expired_card()
                            run_on_ui_thread(on_unsub)
                    else:
                        self._last_known_sub = cur_sub
                except Exception:
                    pass
        import threading
        t = threading.Thread(target=watcher_loop, daemon=True)
        t.start()

    def _check_initial_subscription_flow(self):
        def flow():
            import time
            try:
                now = time.time()
                install_time = float(self.plugin.get_setting("install_time", 0.0) or 0.0)
                if install_time <= 0:
                    install_time = now
                    self.plugin.set_setting("install_time", install_time)

                is_sub = self.is_subscribed()
                self._last_known_sub = is_sub
                was_sub = bool(self.plugin.get_setting("was_subscribed", False))
                was_exp = bool(self.plugin.get_setting("was_expired_state", False))

                if is_sub:
                    if not was_sub:
                        self.plugin.set_setting("was_subscribed", True)
                        self.plugin.set_setting("was_expired_state", False)
                        self._notify_subscribed(was_expired=was_exp)
                    return

                if was_sub or was_exp:
                    self.plugin.set_setting("was_subscribed", False)
                    self.plugin.set_setting("was_expired_state", True)
                    self._show_expired_card()
                    return

                trial_days = float(self._get_links().get("trial_days", 7))
                trial_sec = trial_days * 86400.0
                last_remind = float(self.plugin.get_setting("last_remind_time", 0.0) or 0.0)

                if (now - install_time) < trial_sec:
                    if last_remind <= 0 or (now - last_remind) >= trial_sec:
                        self._show_welcome_card()
                else:
                    self.plugin.set_setting("was_expired_state", True)
                    self._show_expired_card()
            except Exception:
                pass
        from android_utils import run_on_ui_thread
        run_on_ui_thread(flow, 1500)

    def _create_card_content(self, act, title_text, message_text):
        try:
            from android.widget import LinearLayout, TextView
            from android.view import Gravity
            from android.graphics import Typeface
            from org.telegram.messenger import AndroidUtilities, UserConfig, MediaDataController
            from org.telegram.ui.ActionBar import Theme
            from org.telegram.ui.Components import BackupImageView

            dp = AndroidUtilities.dp
            layout = LinearLayout(act)
            layout.setOrientation(LinearLayout.VERTICAL)
            layout.setGravity(Gravity.CENTER_HORIZONTAL)
            layout.setPadding(dp(24), dp(16), dp(24), dp(6))

            size = dp(76)
            iv = BackupImageView(act)
            iv.setRoundRadius(dp(18))
            lp_iv = LinearLayout.LayoutParams(size, size)
            lp_iv.gravity = Gravity.CENTER_HORIZONTAL
            lp_iv.setMargins(0, dp(4), 0, dp(16))
            iv.setLayoutParams(lp_iv)

            try:
                acc = int(UserConfig.selectedAccount)
                mdc = MediaDataController.getInstance(acc)
                raw_icon = str(self.icon_sticker)
                if "/" in raw_icon:
                    pack, idx_s = raw_icon.split("/", 1)
                    mdc.setPlaceholderImageByIndex(iv, pack, int(idx_s), "150_150")
            except Exception:
                pass
            layout.addView(iv)

            if title_text:
                tt = TextView(act)
                tt.setText(title_text)
                tt.setTextSize(19.0)
                tt.setTypeface(None, Typeface.BOLD)
                tt.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                tt.setGravity(Gravity.CENTER_HORIZONTAL)
                lp_t = LinearLayout.LayoutParams(-1, -2)
                lp_t.setMargins(0, 0, 0, dp(12))
                tt.setLayoutParams(lp_t)
                layout.addView(tt)

            if message_text:
                mt = TextView(act)
                mt.setText(message_text)
                mt.setTextSize(15.0)
                mt.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                mt.setLineSpacing(float(dp(3)), 1.0)
                mt.setGravity(Gravity.CENTER_HORIZONTAL)
                lp_m = LinearLayout.LayoutParams(-1, -2)
                mt.setLayoutParams(lp_m)
                layout.addView(mt)

            return layout
        except Exception:
            return None

    def _show_welcome_card(self):
        def show():
            try:
                if self._is_dialog_active(): return
                from client_utils import get_last_fragment
                frag = get_last_fragment()
                if not frag: return
                act = frag.getParentActivity()
                if not act: return

                links = self._get_links()
                url = links.get("channel_url", "https://t.me/MaxExteraPlugins")

                from ui.alert import AlertDialogBuilder
                b = AlertDialogBuilder(act)
                content = self._create_card_content(act, self.tr("install_card_title"), self.tr("install_card_msg"))
                if content is not None:
                    b.set_view(content)
                else:
                    b.set_title(self.tr("install_card_title"))
                    b.set_message(self.tr("install_card_msg"))

                def on_sub(d, w):
                    self._dismiss_active_dialog()
                    self._open_url(url)
                    def check_after():
                        if self.is_subscribed():
                            self._notify_subscribed(was_expired=False)
                    from android_utils import run_on_ui_thread
                    run_on_ui_thread(check_after, 3000)

                def on_later(d, w):
                    import time
                    self._dismiss_active_dialog()
                    self.plugin.set_setting("last_remind_time", time.time())

                b.set_positive_button(self.tr("subscribe_btn"), on_sub)
                b.set_negative_button(self.tr("remind_later_btn"), on_later)
                self._active_dialog = b.show()
            except Exception:
                pass
        from android_utils import run_on_ui_thread
        run_on_ui_thread(show)

    def _show_expired_card(self):
        def show():
            try:
                if self._is_dialog_active(): return
                from client_utils import get_last_fragment
                frag = get_last_fragment()
                if not frag: return
                act = frag.getParentActivity()
                if not act: return

                links = self._get_links()
                url = links.get("channel_url", "https://t.me/MaxExteraPlugins")

                from ui.alert import AlertDialogBuilder
                from ui.bulletin import BulletinHelper
                b = AlertDialogBuilder(act)
                content = self._create_card_content(act, self.tr("trial_expired_title"), self.tr("trial_expired_msg"))
                if content is not None:
                    b.set_view(content)
                else:
                    b.set_title(self.tr("trial_expired_title"))
                    b.set_message(self.tr("trial_expired_msg"))

                def on_sub(d, w):
                    self._dismiss_active_dialog()
                    self._open_url(url)
                    def check_after():
                        if self.is_subscribed():
                            self.plugin.set_setting("was_subscribed", True)
                            self.plugin.set_setting("was_expired_state", False)
                            self._notify_subscribed(was_expired=True)
                    from android_utils import run_on_ui_thread
                    run_on_ui_thread(check_after, 3000)

                def on_check(d, w):
                    self._dismiss_active_dialog()
                    if self.is_subscribed():
                        self.plugin.set_setting("was_subscribed", True)
                        self.plugin.set_setting("was_expired_state", False)
                        self._notify_subscribed(was_expired=True)
                    else:
                        BulletinHelper.show_error(self.tr("sub_not_found"))
                        from android_utils import run_on_ui_thread
                        run_on_ui_thread(self._show_expired_card, 500)

                b.set_positive_button(self.tr("subscribe_btn"), on_sub)
                b.set_negative_button(self.tr("check_sub_btn"), on_check)
                self._active_dialog = b.show()
            except Exception:
                pass
        from android_utils import run_on_ui_thread
        run_on_ui_thread(show)


class NotificationCenterPlugin(BasePlugin):

    def on_app_event(self, event_type):
        if hasattr(self, "_author_helper"):
            self._author_helper.on_event(event_type)

    def __init__(self):
        super().__init__()
        self._pill_registered = False
        self._pill_hooks_installed = False
        self._active_pills = weakref.WeakSet()
        self._pill_tag_key = jint(0x4E43)
        self._nav_tab = None
        self._bridge = None
        self._dex_listener = None
        self._sliding_open = False
        self._injected_btn = None
        self._home_fragment_ref = None
        self.PillRegistry = find_class("com.exteragram.messenger.pillstack.core.PillRegistry")
        self.PillInfo = find_class("com.exteragram.messenger.pillstack.core.PillRegistry$PillInfo")
        self.PillCreator = find_class("com.exteragram.messenger.pillstack.core.PillRegistry$PillCreator")
        self.PillStackConfig = find_class("com.exteragram.messenger.pillstack.core.PillStackConfig")
        self.WeatherPill = find_class("com.exteragram.messenger.pillstack.ui.pills.weather.WeatherPill")
        self.Browser = find_class("org.telegram.messenger.browser.Browser")

    def _detect_lang(self):
        try:
            code = str(Locale.getDefault().getLanguage()).lower()
            if code in ("ru", "be", "kk"):
                return "ru"
        except Exception:
            pass
        return "en"

    def _t(self, key):
        lang_idx = self.get_setting("language", 0)
        if lang_idx == 0:
            lang = self._detect_lang()
        elif lang_idx == 1:
            lang = "en"
        elif lang_idx == 2:
            lang = "ru"
        else:
            lang = self._detect_lang()
        strings = STRINGS.get(lang, STRINGS["en"])
        return strings.get(key, STRINGS["en"].get(key, key))

    def _plural_notif(self, count):
        lang_idx = self.get_setting("language", 0)
        if lang_idx == 0:
            lang = self._detect_lang()
        elif lang_idx == 1:
            lang = "en"
        elif lang_idx == 2:
            lang = "ru"
        else:
            lang = self._detect_lang()
        if lang == "ru":
            n = abs(count) % 100
            n1 = n % 10
            if 11 <= n <= 19:
                return self._t("notification_many")
            elif n1 == 1:
                return self._t("notification_1")
            elif 2 <= n1 <= 4:
                return self._t("notification_few")
            else:
                return self._t("notification_many")
        else:
            return self._t("notification_1") if count == 1 else self._t("notification_many")

    def _timestamp_text(self, ts):
        diff = time.time() - ts
        if diff < 60:
            return self._t("just_now")
        elif diff < 3600:
            return self._t("min_ago").format(int(diff // 60))
        elif diff < 86400:
            return self._t("hours_ago").format(int(diff // 3600))
        else:
            return self._t("days_ago").format(int(diff // 86400))

    def on_plugin_load(self):
        self._author_helper = AuthorChannelHelper(self, icon_sticker=globals().get("__icon__", "MaxExteraPluginsStickers/1"))
        self._author_helper.on_load()
        self._load_dex()
        if self.get_setting("show_in_drawer", True):
            self._add_drawer_item()
        try:
            dialogs_clazz = find_class("org.telegram.ui.DialogsActivity")
            if dialogs_clazz:
                try:
                    self.hook_all_methods(dialogs_clazz, "createView", DialogsCreateViewHook(self))
                except:
                    pass
                try:
                    self.hook_all_methods(dialogs_clazz, "onResume", NotificationCenterActionBarHook(self))
                except:
                    pass
                try:
                    self.hook_all_methods(dialogs_clazz, "onBecomeFullyVisible", DialogsFullyVisibleHook(self))
                except:
                    pass
        except:
            pass
        try:
            rsc_clazz = find_class("org.telegram.ui.RightSlidingDialogContainer")
            if rsc_clazz:
                self.hook_all_methods(rsc_clazz, "presentFragment", SlidingPresentHook(self))
                self.hook_all_methods(rsc_clazz, "finishPreview", SlidingFinishPreviewHook(self))
        except:
            pass
        try:
            mta_clazz = find_class("org.telegram.ui.MainTabsActivity")
            if mta_clazz:
                self.hook_all_methods(mta_clazz, "createView", NavTabHook(self))
                self.hook_all_methods(mta_clazz, "onResume", NavTabHook(self))
        except:
            pass
        self._hook_process_new_messages()
        self._hook_send_message()
        self._hook_reactions()
        self._inject_icon_now()
        if self.get_setting("show_in_navbar", True):
            self._inject_nav_button_now()
        self._push_dex_settings()
        self._sync_to_dex()

    # ---- dex bridge (chat-style screen) ----

    def _load_dex(self):
        try:
            bridge = DexBridge()
            bridge.load()
            self._bridge = bridge
            self._dex_listener = self._make_dex_listener()
            self._bridge.methods["setDataListenerProxy"].invoke(None, self._dex_listener)
            self.log("NC: dex module loaded")
        except Exception as e:
            self._bridge = None
            self._dex_listener = None
            self.log(f"NC: dex module load failed: {e}")

    def _make_dex_listener(self):
        handler_cls = jclass("java.lang.reflect.InvocationHandler")
        plugin = self

        class Handler(dynamic_proxy(handler_cls)):
            def __init__(self):
                super().__init__()

            def invoke(self, proxy, method, args):
                try:
                    if str(method.getName()) == "onItemsChanged":
                        plugin._on_dex_data_changed()
                except Exception:
                    pass
                return None

        return Handler()

    def _push_dex_settings(self):
        if self._bridge is None:
            return
        try:
            self._bridge.call("applySettings", json.dumps({
                "confirm_clear": bool(self.get_setting("confirm_clear", True)),
            }))
        except Exception as e:
            self.log(f"NC: push dex settings failed: {e}")

    def _sync_to_dex(self):
        if self._bridge is None or not self._bridge.loaded:
            return
        try:
            account = UserConfig.selectedAccount
            with _lock:
                notifs = _load_notifications(account)
            arr = []
            for n in notifs:
                arr.append({
                    "dialog_id": int(n.get("dialog_id", 0)),
                    "sender_id": int(n.get("from_id", 0)),
                    "msg_id": int(n.get("msg_id", 0)),
                    "date": int(n.get("timestamp", 0)),
                    "sender_name": n.get("sender_name", "") or "",
                    "text": n.get("text", "") or "",
                    "mention": n.get("type") == "mention",
                })
            self._bridge.call("applyData", json.dumps(arr))
        except Exception as e:
            self.log(f"NC: dex sync failed: {e}")

    def _on_dex_data_changed(self):
        try:
            if self._bridge is None or not self._bridge.loaded:
                return
            account = UserConfig.selectedAccount
            raw = self._bridge.call("getDataJson")
            if raw is None:
                return
            items = json.loads(str(raw))
            out = []
            for it in items:
                out.append({
                    "msg_id": int(it.get("msg_id", 0)),
                    "dialog_id": int(it.get("dialog_id", 0)),
                    "from_id": int(it.get("sender_id", 0)),
                    "sender_name": it.get("sender_name", "") or "",
                    "text": it.get("text", "") or "",
                    "type": "mention" if it.get("mention") else "reply",
                    "timestamp": int(it.get("date", 0)),
                })
            with _lock:
                _save_notifications(account, out)
            self._update_all_pills()
        except Exception as e:
            self.log(f"NC: dex data changed failed: {e}")

    def _open_dex_screen(self):
        if self._bridge is not None and self._bridge.loaded:
            self._bridge.call("openScreen")
        else:
            self._show_center()
        if self._pill_supported():
            if self.get_setting("pill_enabled", False):
                self._register_nc_pill()

    def _hook_process_new_messages(self):
        try:
            nc_cls = find_class("org.telegram.messenger.NotificationsController")
            if nc_cls is None:
                return
            hook = ProcessNewMessagesHook(self)
            for m in nc_cls.getClass().getDeclaredMethods():
                if m.getName() == "processNewMessages":
                    m.setAccessible(True)
                    self.hook_method(m, hook)
                    break
        except:
            pass
        try:
            nc_cls = find_class("org.telegram.messenger.NotificationsController")
            if nc_cls is None:
                return
            hook = ProcessNewMessagesHook(self)
            for m in nc_cls.getClass().getMethods():
                if m.getName() == "processNewMessages":
                    m.setAccessible(True)
                    self.hook_method(m, hook)
                    break
        except:
            pass

    def _hook_send_message(self):
        send_hook = ChatActivitySendHook(self)
        hide_hook = HideFieldPanelHook(self)
        try:
            chat_activity_cls = find_class("org.telegram.ui.ChatActivity")
            if chat_activity_cls is not None:
                for name in ["sendMessage", "sendSticker", "sendMedia"]:
                    try:
                        self.hook_all_methods(chat_activity_cls, name, send_hook)
                    except:
                        pass
                for name in ["hideFieldPanel", "showFieldPanel"]:
                    try:
                        self.hook_all_methods(chat_activity_cls, name, hide_hook)
                    except:
                        pass
        except:
            pass

        class EnterViewSendHook:
            def __init__(hook_self, plugin):
                hook_self.plugin = plugin

            def before_hooked_method(hook_self, param):
                try:
                    enter_view = param.thisObject
                    for field in ["delegate", "parentFragment"]:
                        try:
                            obj = get_private_field(enter_view, field)
                            if obj is not None:
                                hook_self.plugin._track_reply_from_chat_activity(obj)
                        except:
                            pass
                except:
                    pass

        try:
            enter_view_cls = find_class("org.telegram.ui.Components.ChatActivityEnterView")
            if enter_view_cls is not None:
                ev_hook = EnterViewSendHook(self)
                for name in ["onSendButtonPressed", "sendMessage"]:
                    try:
                        self.hook_all_methods(enter_view_cls, name, ev_hook)
                    except:
                        pass
        except:
            pass

    def _hook_reactions(self):
        hook = ReactionSendHook(self)
        for class_name in [
            "org.telegram.messenger.SendMessagesHelper",
            "org.telegram.messenger.MessagesController",
        ]:
            try:
                cls = find_class(class_name)
                if cls is not None:
                    try:
                        self.hook_all_methods(cls, "sendReaction", hook)
                    except:
                        pass
            except:
                pass
        try:
            chat_cls = find_class("org.telegram.ui.ChatActivity")
            if chat_cls is not None:
                try:
                    self.hook_all_methods(chat_cls, "selectReaction", hook)
                except:
                    pass
        except:
            pass

    def _track_reply_from_chat_activity(self, chat_activity):
        try:
            if not self.get_setting("auto_archive_replied", False):
                return
            if chat_activity is None:
                return

            replying_msg = None
            for field_name in ["replyingMessageObject", "replyMessageObject"]:
                try:
                    replying_msg = get_private_field(chat_activity, field_name)
                    if replying_msg is not None:
                        break
                except:
                    pass
            if replying_msg is None:
                return

            msg_id = None
            try:
                msg_id = replying_msg.getId()
            except:
                pass

            if msg_id is None or msg_id == 0:
                try:
                    msg_id = get_private_field(replying_msg, "id")
                except:
                    pass

            if msg_id is None or msg_id == 0:
                try:
                    owner = get_private_field(replying_msg, "messageOwner")
                    if owner is not None:
                        msg_id = getattr(owner, "id", 0)
                except:
                    pass

            if msg_id is None or msg_id == 0:
                return

            account = UserConfig.selectedAccount
            with _lock:
                replied_set = _load_replied(account)
                replied_set.add(int(msg_id))
                if len(replied_set) > 1000:
                    replied_list = list(replied_set)
                    replied_set = set(replied_list[-1000:])
                _save_replied(account, replied_set)
            try:
                auto_archive = self.get_setting("auto_archive", True)
                auto_archive_replied = self.get_setting("auto_archive_replied", False)
                if auto_archive_replied:
                    _run_archiving(account, auto_archive, auto_archive_replied)
            except:
                pass
        except:
            pass

    def on_plugin_unload(self):
        if hasattr(self, "_author_helper"):
            self._author_helper.on_unload()
        self._remove_nav_tab()
        if self._bridge is not None:
            self._bridge.unload()
            self._bridge = None
            self._dex_listener = None
        self._injected_btn = None
        self._home_fragment_ref = None

    def _pill_supported(self):
        try:
            return self.PillRegistry is not None and self.PillInfo is not None and self.WeatherPill is not None
        except:
            return False

    def _register_nc_pill(self):
        if not self._pill_supported():
            return
        if self._pill_registered:
            return

        plugin = self

        class NCPillCreator(dynamic_proxy(plugin.PillCreator)):
            def __init__(self):
                super().__init__()

            def create(self, context, resourcesProvider):
                try:
                    pill = plugin.WeatherPill(context, resourcesProvider)
                    pill.setTag(NC_PILL_TAG)
                    plugin._active_pills.add(pill)
                    plugin._apply_nc_pill_ui(pill)
                    return pill
                except Exception as e:
                    return None

        try:
            info = self.PillInfo(
                jint(NC_PILL_ID),
                "Notification Center",
                jint(R_tg.drawable.msg_notifications),
                jint(-11565578),
                jint(-13276952),
                NCPillCreator()
            )
            self.PillRegistry.register(info)
            self._install_nc_pill_hooks()
            self._ensure_nc_pill_visible()
            self._pill_registered = True
        except Exception as e:
            self._pill_registered = False

    def _install_nc_pill_hooks(self):
        if self._pill_hooks_installed or not self.WeatherPill:
            return
        plugin = self

        class NCGetIdHook(MethodHook):
            def before_hooked_method(self, param):
                try:
                    tag = param.thisObject.getTag()
                    if tag == NC_PILL_TAG:
                        plugin._active_pills.add(param.thisObject)
                        param.setResult(jint(NC_PILL_ID))
                except:
                    pass

        class NCInteractionHook(MethodHook):
            def before_hooked_method(self, param):
                try:
                    tag = param.thisObject.getTag()
                    if tag != NC_PILL_TAG:
                        return
                    plugin._active_pills.add(param.thisObject)
                    method_name = param.method.getName()
                    if method_name == "onPillClicked":
                        plugin._show_center()
                        param.setResult(None)
                    elif method_name == "onPillLongClicked":
                        plugin._show_center()
                        param.setResult(True)
                    elif method_name in ["onUpdateData", "setData"]:
                        plugin._apply_nc_pill_ui(param.thisObject)
                        param.setResult(None)
                except:
                    pass

        methods_to_hook = {
            "getPillId": NCGetIdHook(),
            "onPillClicked": NCInteractionHook(),
            "onPillLongClicked": NCInteractionHook(),
            "onUpdateData": NCInteractionHook(),
            "setData": NCInteractionHook(),
        }

        hooked = set()
        try:
            for m in self.WeatherPill.getClass().getDeclaredMethods():
                name = m.getName()
                if name in methods_to_hook and name not in hooked:
                    m.setAccessible(True)
                    self.hook_method(m, methods_to_hook[name])
                    hooked.add(name)
        except:
            pass
        try:
            for m in self.WeatherPill.getClass().getMethods():
                name = m.getName()
                if name in methods_to_hook and name not in hooked:
                    m.setAccessible(True)
                    self.hook_method(m, methods_to_hook[name])
                    hooked.add(name)
        except:
            pass

        self._pill_hooks_installed = True

    def _ensure_nc_pill_visible(self):
        try:
            active = self.PillStackConfig.activePills
            for i in range(active.size()):
                if int(active.get(i)) == NC_PILL_ID:
                    return
            active.add(jint(NC_PILL_ID))
            self.PillStackConfig.savePillsLayout()
            run_on_ui_thread(self.PillStackConfig.notifySettingsChanged)
        except:
            pass

    def _apply_nc_pill_ui(self, pill):
        try:
            account = UserConfig.selectedAccount
            with _lock:
                notifs = _load_notifications(account)
            count = len(notifs)
            text_view = get_private_field(pill, "textView")
            if text_view:
                label = self._t("pill_1") if count == 1 else self._t("pill_many")
                text_view.setText(f"{count} {label}")
            icon_view = get_private_field(pill, "iconView")
            if icon_view:
                try:
                    icon_view.setImageResource(R_tg.drawable.msg_notifications)
                except:
                    pass
        except:
            pass

    def _update_all_pills(self):
        try:
            self._update_nav_badge()
            self._sync_to_dex()
            for pill in list(self._active_pills):
                try:
                    self._apply_nc_pill_ui(pill)
                except:
                    pass
        except:
            pass

    def _is_main_dialogs_screen(self, fragment):
        try:
            if fragment is None:
                return False
            class_name = fragment.getClass().getName()
            if class_name != "org.telegram.ui.DialogsActivity":
                return False
            try:
                folder_id = int(fragment.folderId)
                if folder_id != 0:
                    return False
            except:
                pass
            try:
                only_select = bool(fragment.onlySelect)
                if only_select:
                    return False
            except:
                pass
            return True
        except:
            return False

    def _has_nc_button(self, menu):
        for i in range(menu.getChildCount()):
            child = menu.getChildAt(i)
            try:
                tag = child.getTag()
                if tag is not None and int(tag) == NC_BUTTON_ID:
                    return True
            except:
                continue
        return False

    def _on_sliding_opened(self):
        self._sliding_open = True
        try:
            btn = self._injected_btn
            if btn is not None:
                btn.setVisibility(View.GONE)
        except:
            pass
        self._start_sliding_watcher()

    def _on_sliding_closed(self):
        self._sliding_open = False
        self._start_sliding_watcher()

    def _start_sliding_watcher(self):
        plugin = self

        def watch():
            frag = plugin._home_fragment_ref
            if frag is None:
                plugin._sliding_open = False
                return
            if _has_sliding_fragment(frag):
                plugin._sliding_open = True
                try:
                    btn = plugin._injected_btn
                    if btn is not None:
                        btn.setVisibility(View.GONE)
                except:
                    pass
                run_on_ui_thread(watch, 300)
            else:
                plugin._sliding_open = False
                try:
                    btn = plugin._injected_btn
                    if btn is not None and plugin.get_setting("show_in_actionbar", False):
                        btn.setVisibility(View.VISIBLE)
                except:
                    pass

        run_on_ui_thread(watch, 350)

    def _add_icon_to_fragment(self, fragment):
        try:
            if not self.get_setting("show_in_actionbar", False):
                return True
            if not self._is_main_dialogs_screen(fragment):
                return False
            actionBar = fragment.getActionBar()
            if not actionBar:
                return False
            menu = actionBar.createMenu()
            if menu is None:
                return False
            existing = menu.getItem(NC_BUTTON_ID)
            if existing is not None:
                self._injected_btn = existing
                self._home_fragment_ref = fragment
                if self._sliding_open:
                    existing.setVisibility(View.GONE)
                return True
            R_drawable = jclass("org.telegram.messenger.R$drawable")
            icon_res = getattr(R_drawable, "msg_notifications", getattr(R_drawable, "msg_markread", 0))
            item = menu.addItem(NC_BUTTON_ID, icon_res)
            menu.removeView(item)
            idx = max(0, menu.getChildCount() - 1)
            menu.addView(item, idx)
            item.setOnClickListener(OnClickListener(lambda v: self._show_center()))
            self._injected_btn = item
            self._home_fragment_ref = fragment
            if self._sliding_open:
                item.setVisibility(View.GONE)
            return True
        except:
            return False

    def _inject_icon_now(self):
        plugin = self

        def do_inject():
            try:
                fragment = get_last_fragment()
                if fragment is None:
                    return
                if not plugin._is_main_dialogs_screen(fragment):
                    return
                plugin._add_icon_to_fragment(fragment)
            except:
                pass

        run_on_ui_thread(do_inject, 500)

    # ---- bottom navigation bar button ----

    def _inject_nav_button_now(self, attempt=0):
        if attempt > 25:
            return

        def try_inject():
            fragment = None
            try:
                fragment = get_last_fragment()
            except:
                pass
            if fragment is not None and str(fragment.getClass().getName()) == "org.telegram.ui.MainTabsActivity":
                if self._inject_nav_tab(fragment):
                    return
            run_on_ui_thread(lambda: self._inject_nav_button_now(attempt + 1), 400)

        run_on_ui_thread(try_inject, 250 if attempt == 0 else 0)

    def _find_nav_tab(self, tabs_view):
        try:
            for i in range(tabs_view.getChildCount()):
                child = tabs_view.getChildAt(i)
                if child is None:
                    continue
                tag = child.getTag()
                if tag is not None and str(tag) == NC_NAV_TAB_TAG:
                    return child
        except:
            pass
        return None

    def _inject_nav_tab(self, activity):
        try:
            if not self.get_setting("show_in_navbar", True):
                return False
            if activity is None:
                return False
            if str(activity.getClass().getName()) != "org.telegram.ui.MainTabsActivity":
                return False
            tabs_view = get_private_field(activity, "tabsView")
            if tabs_view is None:
                return False
            existing = self._find_nav_tab(tabs_view)
            if existing is not None:
                # Tab survived a plugin reload - rebind handler/text so it
                # points at the fresh plugin instance.
                self._nav_tab = existing
                try:
                    existing.setText(self._t("notifications_tab"))
                    existing.setOnClickListener(OnClickListener(lambda v: self._open_dex_screen()))
                except Exception as e:
                    self.log(f"NC: nav tab rebind failed: {e}")
                self._update_nav_badge()
                return True
            glass_tab_view = jclass("org.telegram.ui.Components.glass.GlassTabView")
            tab_animation = jclass("org.telegram.ui.Components.glass.GlassTabView$TabAnimation")
            tab = glass_tab_view.createMainNavigationTab(
                activity.getContext(),
                activity.getResourceProvider(),
                tab_animation.REPLIES,
                jint(R_tg.string.Feed),
            )
            tab.setText(self._t("notifications_tab"))
            tab.setTag(NC_NAV_TAB_TAG)
            tab.setOnClickListener(OnClickListener(lambda v: self._open_dex_screen()))
            tabs_view.addTabToIgnoreClick(tab)
            tabs_view.addView(tab, jint(0))
            tabs_view.setViewVisible(tab, True, False)
            self._nav_tab = tab
            self._update_nav_badge()
            self.log("NC: nav tab injected")
            return True
        except Exception as e:
            self.log(f"NC: inject nav tab failed: {e}")
            return False

    def _remove_nav_tab(self):
        self._nav_tab = None
        try:
            fragment = get_last_fragment()
            if fragment is None or str(fragment.getClass().getName()) != "org.telegram.ui.MainTabsActivity":
                return
            tabs_view = get_private_field(fragment, "tabsView")
            if tabs_view is None:
                return
            tab = self._find_nav_tab(tabs_view)
            if tab is not None:
                tabs_view.removeView(tab)
        except:
            pass

    def _update_nav_badge(self):
        try:
            tab = getattr(self, "_nav_tab", None)
            if tab is None:
                return
            with _lock:
                count = len(_load_notifications(UserConfig.selectedAccount))
            if count > 0:
                tab.setCounter(str(count), False, True)
            else:
                tab.setCounter(None, False, True)
        except:
            pass

    def _on_navbar_toggle(self, enabled):
        if enabled:
            self._inject_nav_button_now()
        else:
            self._remove_nav_tab()

    def _on_confirm_clear_change(self, enabled):
        self._push_dex_settings()

    def _add_drawer_item(self):
        self.add_menu_item(MenuItemData(
            menu_type=MenuItemType.DRAWER_MENU,
            text=self._t("notification_center"),
            on_click=self._on_nc_drawer_click,
            icon="msg_notifications",
            item_id="nc_drawer_btn"
        ))

    def _remove_drawer_item(self):
        self.remove_menu_item("nc_drawer_btn")

    def _on_drawer_toggle(self, enabled):
        if enabled:
            self._add_drawer_item()
        else:
            self._remove_drawer_item()

    def _on_nc_drawer_click(self, context):
        self._show_center()

    def _on_settings_open_click(self, view):
        self._show_center()

    def _on_add_to_pill_stack(self, view):
        if not self._pill_supported():
            missing = []
            if not self.PillRegistry:
                missing.append("PillRegistry")
            if not self.PillInfo:
                missing.append("PillInfo")
            if not self.PillCreator:
                missing.append("PillCreator")
            if not self.WeatherPill:
                missing.append("WeatherPill")
            BulletinHelper.show_error(self._t("pill_unavailable").format(", ".join(missing)))
            return
        if self._pill_registered:
            BulletinHelper.show_info(self._t("pill_already"))
            return
        self.set_setting("pill_enabled", True)
        self._register_nc_pill()

    def _open_url_internal(self, url):
        def handler(view):
            try:
                if self.Browser is not None:
                    context = view.getContext()
                    self.Browser.openUrl(context, url, True, True)
                else:
                    from android.content import Intent
                    from android.net import Uri
                    intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
                    ctx = view.getContext()
                    ctx.startActivity(intent)
            except:
                try:
                    from android.content import Intent
                    from android.net import Uri
                    intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
                    ctx = view.getContext()
                    ctx.startActivity(intent)
                except:
                    BulletinHelper.show_error(self._t("link_error"))
        return handler

    def _build_notification_view(self, context, notifications, dialog_ref, scroll_ref, is_archive=False):
        account = UserConfig.selectedAccount
        mc = MessagesController.getInstance(account)

        root = LinearLayout(context)
        root.setOrientation(LinearLayout.VERTICAL)
        root.setPadding(0, AndroidUtilities.dp(4), 0, AndroidUtilities.dp(8))

        if not notifications:
            empty = TextView(context)
            if is_archive:
                empty.setText(self._t("archive_empty"))
            else:
                empty.setText(self._t("no_notifications"))
            empty.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteGrayText)))
            empty.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 15)
            empty.setGravity(Gravity.CENTER)
            empty.setPadding(AndroidUtilities.dp(16), AndroidUtilities.dp(48), AndroidUtilities.dp(16), AndroidUtilities.dp(48))
            root.addView(empty, LayoutHelper.createLinear(MATCH_PARENT, WRAP_CONTENT))
            return root

        grouped = {}
        order = []
        for n in notifications:
            did = n.get("dialog_id", 0)
            if did not in grouped:
                grouped[did] = []
                order.append(did)
            grouped[did].append(n)

        for dialog_id in order:
            self._add_dialog_group(context, root, dialog_id, grouped[dialog_id], mc, account, dialog_ref, scroll_ref, is_archive)

        return root

    def _add_dialog_group(self, context, parent, dialog_id, items, mc, account, dialog_ref, scroll_ref, is_archive=False):
        container = LinearLayout(context)
        container.setOrientation(LinearLayout.VERTICAL)
        container.setPadding(AndroidUtilities.dp(16), AndroidUtilities.dp(6), AndroidUtilities.dp(16), AndroidUtilities.dp(6))

        header_row = LinearLayout(context)
        header_row.setOrientation(LinearLayout.HORIZONTAL)
        header_row.setGravity(Gravity.CENTER_VERTICAL)
        header_row.setPadding(0, AndroidUtilities.dp(4), 0, AndroidUtilities.dp(4))

        avatar_view = BackupImageView(context)
        avatar_view.setRoundRadius(AndroidUtilities.dp(20))
        avatar_drawable = AvatarDrawable()

        is_user = dialog_id > 0
        title_text = ""
        if is_user:
            user = mc.getUser(dialog_id)
            if user is not None:
                title_text = _get_user_name(user, self._t("unknown"))
                avatar_drawable.setInfo(account, user)
                avatar_view.setForUserOrChat(user, avatar_drawable)
            else:
                title_text = f"{self._t('user_prefix')} {dialog_id}"
                avatar_drawable.setInfo(0, "", "")
                avatar_view.setImageDrawable(avatar_drawable)
        else:
            chat = mc.getChat(-dialog_id)
            if chat is not None:
                title_text = _get_chat_title(chat, self._t("unknown_chat"))
                avatar_drawable.setInfo(account, chat)
                avatar_view.setForUserOrChat(chat, avatar_drawable)
            else:
                title_text = f"{self._t('chat_prefix')} {dialog_id}"
                avatar_drawable.setInfo(0, "", "")
                avatar_view.setImageDrawable(avatar_drawable)

        header_row.addView(avatar_view, LayoutHelper.createLinear(40, 40, Gravity.CENTER_VERTICAL, 0, 0, 10, 0))

        title_container = LinearLayout(context)
        title_container.setOrientation(LinearLayout.VERTICAL)
        title_container.setGravity(Gravity.CENTER_VERTICAL)

        title_tv = TextView(context)
        title_tv.setText(title_text)
        title_tv.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteBlackText)))
        title_tv.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 15)
        title_tv.setTypeface(Typeface.DEFAULT_BOLD)
        title_tv.setSingleLine(True)
        title_tv.setEllipsize(TextUtils.TruncateAt.END)
        title_container.addView(title_tv, LayoutHelper.createLinear(WRAP_CONTENT, WRAP_CONTENT))

        count_label = self._plural_notif(len(items))
        count_tv = TextView(context)
        count_tv.setText(f"{len(items)} {count_label}")
        count_tv.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteGrayText)))
        count_tv.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 12)
        title_container.addView(count_tv, LayoutHelper.createLinear(WRAP_CONTENT, WRAP_CONTENT))

        header_row.addView(title_container, LayoutHelper.createLinear(MATCH_PARENT, WRAP_CONTENT, 1.0))

        del_group_btn = TextView(context)
        del_group_btn.setText("✕")
        del_group_btn.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13)
        del_group_btn.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteGrayIcon)))
        del_group_btn.setGravity(Gravity.CENTER)
        del_group_btn.setPadding(AndroidUtilities.dp(6), AndroidUtilities.dp(6), AndroidUtilities.dp(6), AndroidUtilities.dp(6))

        del_group_bg = jclass("android.graphics.drawable.GradientDrawable")()
        del_group_bg.setCornerRadius(AndroidUtilities.dp(8))
        del_group_bg.setColor(_safe_alpha_color(Theme.getColor(Theme.key_windowBackgroundWhiteGrayIcon), 0x1A))
        del_group_btn.setBackground(del_group_bg)

        d_id = int(dialog_id)
        d_is_archive = is_archive

        def on_del_group(v):
            self._delete_dialog_notifications(d_id, scroll_ref, dialog_ref, d_is_archive)

        del_group_btn.setOnClickListener(OnClickListener(on_del_group))
        header_row.addView(del_group_btn, LayoutHelper.createLinear(28, 28, Gravity.CENTER_VERTICAL, 4, 0, 4, 0))

        is_expanded = int(dialog_id) in _get_expanded_set(is_archive)

        arrow = TextView(context)
        arrow.setText("▲" if is_expanded else "▼")
        arrow.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteGrayIcon)))
        arrow.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 12)
        arrow.setGravity(Gravity.CENTER)
        header_row.addView(arrow, LayoutHelper.createLinear(24, 24, Gravity.CENTER_VERTICAL, 0, 0, 0, 0))

        container.addView(header_row, LayoutHelper.createLinear(MATCH_PARENT, WRAP_CONTENT))

        messages_container = LinearLayout(context)
        messages_container.setOrientation(LinearLayout.VERTICAL)
        messages_container.setVisibility(View.VISIBLE if is_expanded else View.GONE)
        messages_container.setPadding(AndroidUtilities.dp(50), 0, 0, 0)

        for item in items:
            msg_row = self._build_message_row(context, item, dialog_ref, scroll_ref, is_archive)
            messages_container.addView(msg_row, LayoutHelper.createLinear(MATCH_PARENT, WRAP_CONTENT))

        container.addView(messages_container, LayoutHelper.createLinear(MATCH_PARENT, WRAP_CONTENT))

        def toggle_click(v):
            if messages_container.getVisibility() == View.GONE:
                messages_container.setVisibility(View.VISIBLE)
                arrow.setText("▲")
                _get_expanded_set(is_archive).add(int(dialog_id))
            else:
                messages_container.setVisibility(View.GONE)
                arrow.setText("▼")
                _get_expanded_set(is_archive).discard(int(dialog_id))

        header_row.setOnClickListener(OnClickListener(toggle_click))

        div = View(context)
        div.setBackgroundColor(_safe_color(Theme.getColor(Theme.key_divider)))

        parent.addView(container, LayoutHelper.createLinear(MATCH_PARENT, WRAP_CONTENT))
        parent.addView(div, LayoutHelper.createLinear(MATCH_PARENT, 1))

    def _build_message_row(self, context, item, dialog_ref, scroll_ref, is_archive=False):
        outer = LinearLayout(context)
        outer.setOrientation(LinearLayout.HORIZONTAL)
        outer.setGravity(Gravity.CENTER_VERTICAL)
        outer.setPadding(0, AndroidUtilities.dp(6), 0, AndroidUtilities.dp(6))

        text_col = LinearLayout(context)
        text_col.setOrientation(LinearLayout.VERTICAL)

        top_row = LinearLayout(context)
        top_row.setOrientation(LinearLayout.HORIZONTAL)
        top_row.setGravity(Gravity.CENTER_VERTICAL)

        sender_tv = TextView(context)
        sender_tv.setText(item.get("sender_name", self._t("unknown")))
        sender_tv.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteBlueText)))
        sender_tv.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13)
        sender_tv.setTypeface(Typeface.DEFAULT_BOLD)
        sender_tv.setSingleLine(True)
        top_row.addView(sender_tv, LayoutHelper.createLinear(WRAP_CONTENT, WRAP_CONTENT, 1.0))

        time_tv = TextView(context)
        time_tv.setText(self._timestamp_text(item.get("timestamp", 0)))
        time_tv.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteGrayText)))
        time_tv.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 11)
        top_row.addView(time_tv, LayoutHelper.createLinear(WRAP_CONTENT, WRAP_CONTENT))

        text_col.addView(top_row, LayoutHelper.createLinear(MATCH_PARENT, WRAP_CONTENT))

        msg_tv = TextView(context)
        text = item.get("text", "")
        msg_tv.setText(text if text else self._t("media_placeholder"))
        msg_tv.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteBlackText)))
        msg_tv.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 14)
        msg_tv.setMaxLines(3)
        msg_tv.setEllipsize(TextUtils.TruncateAt.END)
        text_col.addView(msg_tv, LayoutHelper.createLinear(MATCH_PARENT, WRAP_CONTENT))

        outer.addView(text_col, LayoutHelper.createLinear(0, WRAP_CONTENT, 1.0))

        del_btn = TextView(context)
        del_btn.setText("✕")
        del_btn.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13)
        del_btn.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteGrayIcon)))
        del_btn.setGravity(Gravity.CENTER)
        del_btn.setPadding(AndroidUtilities.dp(6), AndroidUtilities.dp(6), AndroidUtilities.dp(6), AndroidUtilities.dp(6))

        del_bg = jclass("android.graphics.drawable.GradientDrawable")()
        del_bg.setCornerRadius(AndroidUtilities.dp(8))
        del_bg.setColor(_safe_alpha_color(Theme.getColor(Theme.key_windowBackgroundWhiteGrayIcon), 0x1A))
        del_btn.setBackground(del_bg)

        del_msg_id = item.get("msg_id", 0)
        del_dialog_id = item.get("dialog_id", 0)
        del_is_archive = is_archive

        def on_del(v):
            self._delete_single_notification(del_msg_id, del_dialog_id, scroll_ref, dialog_ref, del_is_archive)

        del_btn.setOnClickListener(OnClickListener(on_del))
        outer.addView(del_btn, LayoutHelper.createLinear(28, 28, Gravity.CENTER_VERTICAL, 4, 0, 0, 0))

        goto_btn = TextView(context)
        goto_btn.setText("→")
        goto_btn.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 16)
        goto_btn.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteBlueText)))
        goto_btn.setGravity(Gravity.CENTER)
        goto_btn.setPadding(AndroidUtilities.dp(8), AndroidUtilities.dp(8), AndroidUtilities.dp(8), AndroidUtilities.dp(8))

        goto_bg = jclass("android.graphics.drawable.GradientDrawable")()
        goto_bg.setCornerRadius(AndroidUtilities.dp(8))
        goto_bg.setColor(_safe_alpha_color(Theme.getColor(Theme.key_windowBackgroundWhiteBlueText), 0x1A))
        goto_btn.setBackground(goto_bg)

        did = item.get("dialog_id", 0)
        mid = item.get("msg_id", 0)

        def on_goto(v):
            if dialog_ref[0] is not None:
                dialog_ref[0].dismiss()
            run_on_ui_thread(lambda: _navigate_to_message(did, mid), 200)

        goto_btn.setOnClickListener(OnClickListener(on_goto))
        outer.addView(goto_btn, LayoutHelper.createLinear(36, 36, Gravity.CENTER_VERTICAL, 4, 0, 0, 0))

        return outer

    def _delete_single_notification(self, msg_id, dialog_id, scroll_ref, dialog_ref, is_archive=False):
        account = UserConfig.selectedAccount
        with _lock:
            if is_archive:
                data = _load_archive(account)
                data = [n for n in data if n.get("msg_id") != msg_id]
                _save_archive(account, data)
                if not any(n.get("dialog_id") == dialog_id for n in data):
                    _expanded_archive_dialogs.discard(int(dialog_id))
            else:
                data = _load_notifications(account)
                data = [n for n in data if n.get("msg_id") != msg_id]
                _save_notifications(account, data)
                if not any(n.get("dialog_id") == dialog_id for n in data):
                    _expanded_dialogs.discard(int(dialog_id))
        self._refresh_center_content(scroll_ref, dialog_ref, is_archive=is_archive)
        self._update_all_pills()

    def _delete_dialog_notifications(self, dialog_id, scroll_ref, dialog_ref, is_archive=False):
        account = UserConfig.selectedAccount
        with _lock:
            if is_archive:
                data = _load_archive(account)
                data = [n for n in data if n.get("dialog_id") != dialog_id]
                _save_archive(account, data)
                _expanded_archive_dialogs.discard(int(dialog_id))
            else:
                data = _load_notifications(account)
                data = [n for n in data if n.get("dialog_id") != dialog_id]
                _save_notifications(account, data)
                _expanded_dialogs.discard(int(dialog_id))
        self._refresh_center_content(scroll_ref, dialog_ref, is_archive=is_archive)
        self._update_all_pills()

    def _refresh_center_content(self, scroll_ref, dialog_ref, is_archive=False):
        if scroll_ref[0] is None:
            return
        scroll = scroll_ref[0]
        context = scroll.getContext()
        account = UserConfig.selectedAccount
        with _lock:
            if is_archive:
                data = _load_archive(account)
            else:
                data = _load_notifications(account)
        scroll.removeAllViews()
        content = self._build_notification_view(context, data, dialog_ref, scroll_ref, is_archive=is_archive)
        scroll.addView(content)

    def _show_center(self):
        plugin = self

        def do_show():
            fragment = get_last_fragment()
            if not fragment:
                return
            activity = fragment.getParentActivity()
            if not activity:
                return

            account = UserConfig.selectedAccount

            auto_archive = plugin.get_setting("auto_archive", True)
            auto_archive_replied = plugin.get_setting("auto_archive_replied", False)
            if auto_archive or auto_archive_replied:
                _run_archiving(account, auto_archive, auto_archive_replied)

            with _lock:
                notifs = _load_notifications(account)

            dialog_ref = [None]
            scroll_ref = [None]
            tab_state = [False]

            builder = AlertDialogBuilder(activity)
            builder.set_title(plugin._t("notification_center"))

            outer_layout = LinearLayout(activity)
            outer_layout.setOrientation(LinearLayout.VERTICAL)

            scroll = ScrollView(activity)
            scroll_ref[0] = scroll
            content = plugin._build_notification_view(activity, notifs, dialog_ref, scroll_ref, is_archive=False)
            scroll.addView(content)
            scroll.setMinimumHeight(AndroidUtilities.dp(300))

            if auto_archive:
                tab_bar = LinearLayout(activity)
                tab_bar.setOrientation(LinearLayout.HORIZONTAL)
                tab_bar.setGravity(Gravity.CENTER)
                tab_bar.setPadding(AndroidUtilities.dp(8), AndroidUtilities.dp(4), AndroidUtilities.dp(8), AndroidUtilities.dp(4))

                tab_notifs = TextView(activity)
                tab_notifs.setText(plugin._t("notifications_tab"))
                tab_notifs.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 14)
                tab_notifs.setTypeface(Typeface.DEFAULT_BOLD)
                tab_notifs.setGravity(Gravity.CENTER)
                tab_notifs.setPadding(AndroidUtilities.dp(16), AndroidUtilities.dp(8), AndroidUtilities.dp(16), AndroidUtilities.dp(8))

                tab_archive = TextView(activity)
                tab_archive.setText(plugin._t("archive_tab"))
                tab_archive.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 14)
                tab_archive.setGravity(Gravity.CENTER)
                tab_archive.setPadding(AndroidUtilities.dp(16), AndroidUtilities.dp(8), AndroidUtilities.dp(16), AndroidUtilities.dp(8))

                def _apply_tab_style():
                    if not tab_state[0]:
                        tab_notifs.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteBlueText)))
                        tab_notifs.setTypeface(Typeface.DEFAULT_BOLD)
                        tab_archive.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteGrayText)))
                        tab_archive.setTypeface(Typeface.DEFAULT)
                    else:
                        tab_archive.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteBlueText)))
                        tab_archive.setTypeface(Typeface.DEFAULT_BOLD)
                        tab_notifs.setTextColor(_safe_color(Theme.getColor(Theme.key_windowBackgroundWhiteGrayText)))
                        tab_notifs.setTypeface(Typeface.DEFAULT)

                _apply_tab_style()

                def on_tab_notifs(v):
                    if not tab_state[0]:
                        return
                    tab_state[0] = False
                    _apply_tab_style()
                    plugin._refresh_center_content(scroll_ref, dialog_ref, is_archive=False)

                def on_tab_archive(v):
                    if tab_state[0]:
                        return
                    tab_state[0] = True
                    _apply_tab_style()
                    plugin._refresh_center_content(scroll_ref, dialog_ref, is_archive=True)

                tab_notifs.setOnClickListener(OnClickListener(on_tab_notifs))
                tab_archive.setOnClickListener(OnClickListener(on_tab_archive))

                tab_bar.addView(tab_notifs, LayoutHelper.createLinear(0, WRAP_CONTENT, 1.0))
                tab_bar.addView(tab_archive, LayoutHelper.createLinear(0, WRAP_CONTENT, 1.0))

                tab_div = View(activity)
                tab_div.setBackgroundColor(_safe_color(Theme.getColor(Theme.key_divider)))

                outer_layout.addView(tab_bar, LayoutHelper.createLinear(MATCH_PARENT, WRAP_CONTENT))
                outer_layout.addView(tab_div, LayoutHelper.createLinear(MATCH_PARENT, 1))

            outer_layout.addView(scroll, LayoutHelper.createLinear(MATCH_PARENT, WRAP_CONTENT))

            builder.set_view(outer_layout)

            def do_clear():
                with _lock:
                    if auto_archive and tab_state[0]:
                        _save_archive(account, [])
                        _expanded_archive_dialogs.clear()
                    else:
                        _save_notifications(account, [])
                        _expanded_dialogs.clear()
                if auto_archive and tab_state[0]:
                    BulletinHelper.show_success(plugin._t("archive_cleared"))
                else:
                    BulletinHelper.show_success(plugin._t("all_cleared"))
                if plugin.get_setting("close_after_clear", False):
                    if dialog_ref[0] is not None:
                        dialog_ref[0].dismiss()
                else:
                    plugin._refresh_center_content(scroll_ref, dialog_ref, is_archive=(auto_archive and tab_state[0]))
                plugin._update_all_pills()

            def on_clear(bld, which):
                if plugin.get_setting("confirm_clear", True):
                    confirm = AlertDialogBuilder(activity)
                    confirm.set_title(plugin._t("clear_all_title"))
                    confirm.set_message(plugin._t("clear_all_confirm"))

                    def on_yes(cbld, cwhich):
                        cbld.dismiss()
                        do_clear()

                    confirm.set_positive_button(plugin._t("btn_delete"), on_yes)
                    confirm.set_negative_button(plugin._t("btn_cancel"), lambda cbld, cw: cbld.dismiss())
                    confirm.make_button_red(AlertDialogBuilder.BUTTON_POSITIVE)
                    confirm.show()
                else:
                    do_clear()

            builder.set_negative_button(plugin._t("clear_all"), on_clear)
            builder.set_positive_button(plugin._t("close"), lambda bld, w: bld.dismiss())
            builder.make_button_red(AlertDialogBuilder.BUTTON_NEGATIVE)
            builder.create()

            try:
                dialog = builder.get_dialog()
                if dialog is None:
                    dialog = builder._dialog
                if dialog is not None:
                    neg_button = dialog.getButton(AlertDialogBuilder.BUTTON_NEGATIVE)
                    if neg_button is not None:
                        def on_clear_click(v):
                            on_clear(builder, AlertDialogBuilder.BUTTON_NEGATIVE)
                        neg_button.setOnClickListener(OnClickListener(on_clear_click))
            except:
                pass

            dialog_ref[0] = builder
            builder.show()

            try:
                dialog = builder.get_dialog() if hasattr(builder, 'get_dialog') else getattr(builder, '_dialog', None)
                if dialog is not None:
                    neg_button = dialog.getButton(AlertDialogBuilder.BUTTON_NEGATIVE)
                    if neg_button is not None:
                        def on_clear_click2(v):
                            on_clear(builder, AlertDialogBuilder.BUTTON_NEGATIVE)
                        neg_button.setOnClickListener(OnClickListener(on_clear_click2))
            except:
                pass

        run_on_ui_thread(do_show, 300)

    def _get_from_id(self, msg):
        from_id_peer = getattr(msg, "from_id", None)
        if from_id_peer is not None:
            uid = getattr(from_id_peer, "user_id", 0)
            if uid:
                return int(uid)
        return 0

    def _get_dialog_id(self, msg):
        peer = getattr(msg, "peer_id", None)
        if peer is None:
            return 0
        user_id = getattr(peer, "user_id", 0)
        chat_id = getattr(peer, "chat_id", 0)
        channel_id = getattr(peer, "channel_id", 0)
        if user_id:
            return int(user_id)
        if chat_id:
            return int(-chat_id)
        if channel_id:
            return int(-channel_id)
        return 0

    def _is_mention(self, msg, current_user_id, mc):
        if getattr(msg, "mentioned", False):
            return True
        entities = getattr(msg, "entities", None)
        if entities is not None:
            for ent in entities:
                ent_class = ent.getClass().getSimpleName()
                if ent_class == "TL_messageEntityMentionName":
                    if getattr(ent, "user_id", 0) == current_user_id:
                        return True
                elif ent_class == "TL_messageEntityMention":
                    text = getattr(msg, "message", "") or ""
                    offset = getattr(ent, "offset", 0)
                    length = getattr(ent, "length", 0)
                    mention_text = text[offset:offset + length].lstrip("@").lower()
                    user = mc.getUser(current_user_id)
                    if user is not None:
                        username = (getattr(user, "username", "") or "").lower()
                        if username and mention_text == username:
                            return True
        return False

    def _is_reply_to_me(self, msg, current_user_id, account):
        reply_to = getattr(msg, "reply_to", None)
        if reply_to is None:
            return False
        reply_to_msg_id = getattr(reply_to, "reply_to_msg_id", 0)
        if not reply_to_msg_id:
            return False
        reply_to_peer_id = getattr(reply_to, "reply_to_peer_id", None)
        if reply_to_peer_id is not None:
            return False
        try:
            reply_from = getattr(reply_to, "reply_from", None)
            if reply_from is not None:
                from_id = getattr(reply_from, "from_id", None)
                if from_id is not None:
                    uid = getattr(from_id, "user_id", 0)
                    if uid == current_user_id:
                        return True
        except:
            pass
        try:
            reply_header = getattr(msg, "replyMessage", None)
            if reply_header is not None:
                reply_msg = getattr(reply_header, "messageOwner", reply_header)
                reply_from_id = self._get_from_id(reply_msg)
                if reply_from_id == current_user_id:
                    return True
        except:
            pass
        dialog_id = self._get_dialog_id(msg)
        try:
            mc = MessagesController.getInstance(account)
            dialog_messages = mc.dialogMessage
            if dialog_messages is not None:
                arr = dialog_messages.get(dialog_id)
                if arr is not None:
                    for i in range(arr.size()):
                        m = arr.get(i)
                        if m is not None and m.getId() == reply_to_msg_id:
                            sender = self._get_from_id(m.messageOwner)
                            return sender == current_user_id
        except:
            pass
        return False

    def _process_message(self, msg, account):
        current_user_id = UserConfig.getInstance(account).getClientUserId()
        from_id = self._get_from_id(msg)

        if from_id == current_user_id:
            return

        dialog_id = self._get_dialog_id(msg)
        if dialog_id == 0:
            return

        if dialog_id > 0:
            return

        mc = MessagesController.getInstance(account)

        is_mention = self._is_mention(msg, current_user_id, mc)
        is_reply = self._is_reply_to_me(msg, current_user_id, account)

        if not is_mention and not is_reply:
            return

        user = mc.getUser(from_id) if from_id > 0 else None
        sender_name = _get_user_name(user, self._t("unknown")) if user is not None else f"{self._t('user_prefix')} {from_id}"

        text = getattr(msg, "message", "") or ""
        msg_id = getattr(msg, "id", 0)

        notif = {
            "msg_id": int(msg_id),
            "dialog_id": int(dialog_id),
            "from_id": int(from_id),
            "sender_name": sender_name,
            "text": text[:500],
            "type": "mention" if is_mention else "reply",
            "timestamp": time.time(),
        }

        with _lock:
            notifs = _load_notifications(account)
            existing_ids = {n.get("msg_id") for n in notifs}
            if msg_id not in existing_ids:
                notifs.insert(0, notif)
                if len(notifs) > 500:
                    notifs = notifs[:500]
                _save_notifications(account, notifs)
                run_on_ui_thread(self._update_nav_badge, 0)
                run_on_ui_thread(self._sync_to_dex, 0)

    def _on_language_change(self, new_index):
        self.set_setting("language", new_index, reload_settings=True)
        if self.get_setting("show_in_drawer", True):
            self._remove_drawer_item()
            self._add_drawer_item()

    def create_settings(self):
        settings = [
            Header(text=self._t("header_localization")),
            Selector(
                key="language",
                text=self._t("language"),
                default=0,
                items=["Auto", "English", "Русский"],
                icon="msg_language",
                on_change=self._on_language_change,
            ),
            Divider(),
            Header(text=self._t("header_info")),
            Text(
                text=self._t("docs"),
                icon="msg_link",
                on_click=self._open_url_internal("https://telegra.ph/Dokumentaciya-k-plaginu-Notification-Center-06-27"),
            ),
            Text(
                text=self._t("author_channel"),
                icon="msg_channel",
                on_click=self._open_url_internal("https://t.me/MaxExteraPlugins"),
            ),
            Divider(),
            Header(text=self._t("header_nc")),
            Text(
                text=self._t("open_nc"),
                icon="msg_notifications",
                on_click=self._on_settings_open_click,
            ),
            Divider(),
            Header(text=self._t("header_button")),
            Switch(
                key="show_in_drawer",
                text=self._t("side_menu"),
                default=True,
                subtext=self._t("side_menu_sub"),
                icon="msg_menu",
                on_change=self._on_drawer_toggle,
            ),
            Switch(
                key="show_in_actionbar",
                text=self._t("main_screen"),
                default=False,
                subtext=self._t("main_screen_sub"),
                icon="msg_header",
            ),
            Divider(),
            Header(text=self._t("header_navbar")),
            Switch(
                key="show_in_navbar",
                text=self._t("nav_button"),
                default=True,
                subtext=self._t("nav_button_sub"),
                icon="msg_header",
                on_change=self._on_navbar_toggle,
            ),
            Switch(
                key="confirm_clear",
                text=self._t("confirm_clear"),
                default=True,
                subtext=self._t("confirm_clear_sub"),
                icon="msg_delete",
                on_change=self._on_confirm_clear_change,
            ),
            Divider(),
            Header(text=self._t("header_archiving")),
            Switch(
                key="auto_archive",
                text=self._t("auto_archive"),
                default=True,
                subtext=self._t("auto_archive_sub"),
                icon="msg_archive",
            ),
            Switch(
                key="auto_archive_replied",
                text=self._t("archive_replied"),
                default=False,
                subtext=self._t("archive_replied_sub"),
                icon="msg_reply",
            ),
            Divider(),
            Header(text=self._t("header_clearing")),
            Switch(
                key="close_after_clear",
                text=self._t("close_after_clear"),
                default=False,
                subtext=self._t("close_after_clear_sub"),
                icon="msg_close",
            ),
            Divider(),
        ]

        if self._pill_supported():
            settings.extend([
                Divider(),
                Header(text=self._t("header_pill")),
                Switch(
                    key="pill_enabled",
                    text=self._t("show_pill"),
                    default=False,
                    subtext=self._t("show_pill_sub"),
                    icon="msg_search",
                    on_change=self._on_pill_toggle,
                ),
            ])

        return settings

    def _on_pill_toggle(self, enabled):
        if enabled:
            self._register_nc_pill()
        else:
            self._pill_registered = False

# __DEX_BEGIN__
# PLACEHOLDER
# __DEX_END__
