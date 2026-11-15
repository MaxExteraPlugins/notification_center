package com.exteraplugins.nc;

import android.util.Log;

import org.json.JSONObject;
import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.UserConfig;
import org.telegram.ui.ActionBar.BaseFragment;
import org.telegram.ui.LaunchActivity;

/**
 * Bridge for the python side. No hooks of its own: the python plugin owns
 * data collection (its notifications JSON is the source of truth) and pushes
 * it here via applyData; user-driven changes inside the screen are reported
 * back through the DataListener.
 */
public class Main {

    private static final String TAG = "NotificationCenter";

    private static volatile boolean confirmClear = true;

    public static void load() {
        Log.d(TAG, "nc dex loaded");
    }

    public static void unload() {
    }

    /** {"confirm_clear": bool} */
    public static void applySettings(String json) {
        try {
            JSONObject o = new JSONObject(json);
            confirmClear = o.optBoolean("confirm_clear", true);
        } catch (Throwable t) {
            Log.w(TAG, "applySettings failed", t);
        }
    }

    public static boolean confirmClearEnabled() {
        return confirmClear;
    }

    public static void setDataListener(MentionStore.DataListener listener) {
        MentionStore.setDataListener(listener);
    }

    /**
     * Python cannot create proxies for interfaces from this dex classloader,
     * so it passes a java.lang.reflect.InvocationHandler (a framework
     * interface it can proxy) and we wrap it into DataListener here.
     */
    public static void setDataListenerProxy(Object handler) {
        try {
            java.lang.reflect.InvocationHandler ih = (java.lang.reflect.InvocationHandler) handler;
            MentionStore.DataListener listener = (MentionStore.DataListener) java.lang.reflect.Proxy.newProxyInstance(
                    Main.class.getClassLoader(),
                    new Class[]{MentionStore.DataListener.class},
                    ih);
            MentionStore.setDataListener(listener);
        } catch (Throwable t) {
            Log.w(TAG, "setDataListenerProxy failed", t);
        }
    }

    /** Replaces the screen contents with the plugin's notifications. */
    public static void applyData(String json) {
        MentionStore.syncFromJson(UserConfig.selectedAccount, json);
    }

    /** Current screen items as JSON (used by python to persist changes). */
    public static String getDataJson() {
        return MentionStore.getItemsJson(UserConfig.selectedAccount);
    }

    public static void openScreen() {
        AndroidUtilities.runOnUIThread(() -> {
            try {
                BaseFragment fragment = LaunchActivity.getLastFragment();
                if (fragment != null) {
                    fragment.presentFragment(new UnansweredFragment());
                }
            } catch (Throwable t) {
                Log.w(TAG, "openScreen failed", t);
            }
        });
    }

    static void log(String message) {
        Log.d(TAG, message);
    }

    static void log(String message, Throwable t) {
        Log.w(TAG, message, t);
    }

    static class Loc {
        static final int TAB_TITLE = 0;
        static final int EMPTY = 1;
        static final int CLEAR_TITLE = 2;
        static final int CLEAR_MESSAGE = 3;
        static final int CLEAR_BTN = 4;
        static final int CANCEL_BTN = 5;

        private static final String[] RU = {
                "Уведомления",
                "Уведомлений пока нет",
                "Очистить список?",
                "Удалить все уведомления из списка?",
                "Очистить",
                "Отмена",
        };

        private static final String[] EN = {
                "Notifications",
                "No notifications yet",
                "Clear list?",
                "Remove all notifications from the list?",
                "Clear",
                "Cancel",
        };

        static String get(int id) {
            String[] table = isRu() ? RU : EN;
            return table[id];
        }
    }

    static boolean isRu() {
        try {
            java.util.Locale locale = java.util.Locale.getDefault();
            if (locale != null && locale.getLanguage() != null) {
                String lang = locale.getLanguage();
                if (lang.equalsIgnoreCase("ru") || lang.equalsIgnoreCase("be") || lang.equalsIgnoreCase("kk")) {
                    return true;
                }
            }
            try {
                java.util.Locale appLocale = org.telegram.messenger.LocaleController.getInstance().getCurrentLocale();
                return appLocale != null && "ru".equalsIgnoreCase(appLocale.getLanguage());
            } catch (Throwable t) {
                return false;
            }
        } catch (Throwable t) {
            return false;
        }
    }
}
