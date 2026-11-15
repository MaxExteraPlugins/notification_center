package com.exteraplugins.nc;

import org.json.JSONArray;
import org.json.JSONObject;
import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.MessageObject;
import org.telegram.messenger.MessagesController;
import org.telegram.tgnet.ConnectionsManager;
import org.telegram.tgnet.TLObject;
import org.telegram.tgnet.TLRPC;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * In-memory item list fed by the python plugin (which owns data collection).
 * MessageObjects for rendering are cached here; ones missing from the cache
 * are fetched from the server by exact message ids.
 */
public final class MentionStore {

    public static class Item {
        public final long dialogId;
        public final long senderId;
        public final int msgId;
        public final int date;
        public final String senderName;
        public final String text;
        public final boolean mention;

        Item(long dialogId, long senderId, int msgId, int date, String senderName, String text, boolean mention) {
            this.dialogId = dialogId;
            this.senderId = senderId;
            this.msgId = msgId;
            this.date = date;
            this.senderName = senderName;
            this.text = text;
            this.mention = mention;
        }
    }

    public interface Listener {
        void onMentionsChanged();
    }

    /** Implemented by the python side; notified on user-driven changes. */
    public interface DataListener {
        void onItemsChanged();
    }

    private static final Object lock = new Object();
    private static final Map<Integer, List<Item>> itemsByAccount = new HashMap<>();
    private static final Map<String, MessageObject> objectCache = new HashMap<>();
    private static final AtomicBoolean fetchingObjects = new AtomicBoolean(false);
    private static final List<Listener> listeners = new ArrayList<>();
    private static volatile DataListener dataListener;

    private MentionStore() {
    }

    public static void addListener(Listener listener) {
        synchronized (listeners) {
            if (!listeners.contains(listener)) {
                listeners.add(listener);
            }
        }
    }

    public static void removeListener(Listener listener) {
        synchronized (listeners) {
            listeners.remove(listener);
        }
    }

    public static void setDataListener(DataListener listener) {
        dataListener = listener;
    }

    public static int size() {
        return getItems(UserConfigSelectedAccount()).size();
    }

    private static int UserConfigSelectedAccount() {
        return org.telegram.messenger.UserConfig.selectedAccount;
    }

    public static List<Item> getItems(int account) {
        synchronized (lock) {
            List<Item> items = itemsByAccount.get(account);
            return items == null ? new ArrayList<>() : new ArrayList<>(items);
        }
    }

    /** Replaces the whole item list with the plugin's notifications. */
    public static void syncFromJson(int account, String json) {
        List<Item> parsed = new ArrayList<>();
        try {
            JSONArray arr = new JSONArray(json);
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.optJSONObject(i);
                if (o == null) {
                    continue;
                }
                parsed.add(new Item(
                        o.optLong("dialog_id", 0),
                        o.optLong("sender_id", 0),
                        o.optInt("msg_id", 0),
                        o.optInt("date", 0),
                        o.optString("sender_name", ""),
                        o.optString("text", ""),
                        o.optBoolean("mention", false)));
            }
        } catch (Throwable t) {
            Main.log("syncFromJson parse failed", t);
            return;
        }
        parsed.sort((a, b) -> Integer.compare(b.date, a.date));
        synchronized (lock) {
            itemsByAccount.put(account, parsed);
        }
        notifyChanged();
    }

    public static String getItemsJson(int account) {
        JSONArray arr = new JSONArray();
        synchronized (lock) {
            List<Item> items = itemsByAccount.get(account);
            if (items != null) {
                for (Item item : items) {
                    try {
                        JSONObject o = new JSONObject();
                        o.put("dialog_id", item.dialogId);
                        o.put("sender_id", item.senderId);
                        o.put("msg_id", item.msgId);
                        o.put("date", item.date);
                        o.put("sender_name", item.senderName == null ? "" : item.senderName);
                        o.put("text", item.text == null ? "" : item.text);
                        o.put("mention", item.mention);
                        arr.put(o);
                    } catch (Throwable ignored) {
                    }
                }
            }
        }
        return arr.toString();
    }

    public static void hide(int account, long dialogId, int msgId) {
        removeInternal(account, dialogId, msgId);
    }

    public static void clearAll(int account) {
        synchronized (lock) {
            itemsByAccount.put(account, new ArrayList<>());
        }
        notifyChanged();
        notifyDataChanged();
    }

    private static void removeInternal(int account, long dialogId, int msgId) {
        boolean changed = false;
        synchronized (lock) {
            List<Item> items = itemsByAccount.get(account);
            if (items != null) {
                for (int i = 0; i < items.size(); i++) {
                    Item item = items.get(i);
                    if (item.msgId == msgId && item.dialogId == dialogId) {
                        items.remove(i);
                        changed = true;
                        break;
                    }
                }
            }
        }
        synchronized (objectCache) {
            objectCache.remove(account + ":" + dialogId + ":" + msgId);
        }
        if (changed) {
            notifyChanged();
            notifyDataChanged();
        }
    }

    // ---- message object cache ----

    public static MessageObject getObject(int account, long dialogId, int msgId) {
        synchronized (objectCache) {
            return objectCache.get(account + ":" + dialogId + ":" + msgId);
        }
    }

    private static void putObject(int account, long dialogId, int msgId, MessageObject mo) {
        synchronized (objectCache) {
            if (objectCache.size() > 800) {
                objectCache.clear();
            }
            objectCache.put(account + ":" + dialogId + ":" + msgId, mo);
        }
    }

    /**
     * Cached real MessageObject, or a minimal text-only one built from the
     * item fields (used until the real message is fetched). The synthetic
     * object is not cached, so the real one still gets fetched (bringing
     * media previews along).
     */
    public static MessageObject getObjectOrSynthetic(int account, Item item) {
        MessageObject cached = getObject(account, item.dialogId, item.msgId);
        if (cached != null) {
            return cached;
        }
        try {
            TLRPC.TL_message msg = new TLRPC.TL_message();
            msg.id = item.msgId;
            msg.date = item.date;
            msg.message = item.text == null ? "" : item.text;
            if (item.dialogId > 0) {
                TLRPC.TL_peerUser peer = new TLRPC.TL_peerUser();
                peer.user_id = item.dialogId;
                msg.peer_id = peer;
            } else {
                TLRPC.Chat chat = MessagesController.getInstance(account).getChat(-item.dialogId);
                if (chat != null && chat.megagroup) {
                    TLRPC.TL_peerChannel peer = new TLRPC.TL_peerChannel();
                    peer.channel_id = -item.dialogId;
                    msg.peer_id = peer;
                } else {
                    TLRPC.TL_peerChat peer = new TLRPC.TL_peerChat();
                    peer.chat_id = -item.dialogId;
                    msg.peer_id = peer;
                }
            }
            if (item.senderId > 0) {
                TLRPC.TL_peerUser fromPeer = new TLRPC.TL_peerUser();
                fromPeer.user_id = item.senderId;
                msg.from_id = fromPeer;
            }
            return new MessageObject(account, msg, false, false);
        } catch (Throwable t) {
            Main.log("getObjectOrSynthetic failed", t);
            return null;
        }
    }

    /**
     * Items restored from a sync before their MessageObject was cached:
     * fetch exactly those messages by id (senders arrive with them, fixing
     * names and avatars).
     */
    public static void fetchMissingObjects(int account) {
        if (!fetchingObjects.compareAndSet(false, true)) {
            return;
        }
        try {
            Map<Long, List<Integer>> missing = new HashMap<>();
            List<Item> snapshot;
            synchronized (lock) {
                snapshot = new ArrayList<>(getItems(account));
            }
            for (Item item : snapshot) {
                if (getObject(account, item.dialogId, item.msgId) == null) {
                    List<Integer> ids = missing.computeIfAbsent(item.dialogId, k -> new ArrayList<>());
                    if (!ids.contains(item.msgId)) {
                        ids.add(item.msgId);
                    }
                }
            }
            if (missing.isEmpty()) {
                fetchingObjects.set(false);
                return;
            }
            Map<Long, List<Integer>> trimmed = new HashMap<>();
            for (Map.Entry<Long, List<Integer>> entry : missing.entrySet()) {
                if (trimmed.size() >= 20) {
                    break;
                }
                trimmed.put(entry.getKey(), entry.getValue());
            }
            missing = trimmed;
            MessagesController mc = MessagesController.getInstance(account);
            java.util.concurrent.atomic.AtomicInteger remaining =
                    new java.util.concurrent.atomic.AtomicInteger(missing.size());
            for (Map.Entry<Long, List<Integer>> entry : missing.entrySet()) {
                long dialogId = entry.getKey();
                List<Integer> wantedIds = entry.getValue();
                TLObject request = null;
                if (dialogId < 0) {
                    TLRPC.Chat chat = mc.getChat(-dialogId);
                    TLRPC.InputChannel inputChannel = null;
                    if (chat != null) {
                        inputChannel = mc.getInputChannel(chat);
                    }
                    if (inputChannel != null) {
                        TLRPC.TL_channels_getMessages req = new TLRPC.TL_channels_getMessages();
                        req.channel = inputChannel;
                        req.id.addAll(wantedIds);
                        request = req;
                    }
                } else {
                    TLRPC.TL_messages_getMessages req = new TLRPC.TL_messages_getMessages();
                    req.id.addAll(wantedIds);
                    request = req;
                }
                if (request == null) {
                    if (remaining.decrementAndGet() == 0) {
                        fetchingObjects.set(false);
                    }
                    continue;
                }
                ConnectionsManager.getInstance(account).sendRequest(request, (response, error) -> {
                    try {
                        if (response instanceof TLRPC.messages_Messages) {
                            cacheOnlyResponse(account, dialogId, wantedIds, (TLRPC.messages_Messages) response);
                        }
                    } catch (Throwable t) {
                        Main.log("cacheOnlyResponse failed", t);
                    }
                    if (remaining.decrementAndGet() == 0) {
                        fetchingObjects.set(false);
                    }
                });
            }
        } catch (Throwable t) {
            fetchingObjects.set(false);
            Main.log("fetchMissingObjects failed", t);
        }
    }

    private static void cacheOnlyResponse(int account, long dialogId, List<Integer> wantedIds, TLRPC.messages_Messages res) {
        if (res.messages == null || res.messages.isEmpty()) {
            return;
        }
        MessagesController mc = MessagesController.getInstance(account);
        try {
            if (res.users != null) {
                mc.putUsers(res.users, true);
            }
            if (res.chats != null) {
                mc.putChats(res.chats, true);
            }
        } catch (Throwable ignored) {
        }
        HashMap<Long, TLRPC.User> users = new HashMap<>();
        if (res.users != null) {
            for (TLRPC.User u : res.users) {
                users.put(u.id, u);
            }
        }
        HashMap<Long, TLRPC.Chat> chats = new HashMap<>();
        if (res.chats != null) {
            for (TLRPC.Chat c : res.chats) {
                chats.put(c.id, c);
            }
        }
        boolean changed = false;
        for (TLRPC.Message msg : res.messages) {
            if (msg == null || !wantedIds.contains(msg.id)) {
                continue;
            }
            try {
                MessageObject mo = new MessageObject(account, msg, users, chats, true, true);
                putObject(account, dialogId, msg.id, mo);
                changed = true;
            } catch (Throwable ignored) {
            }
        }
        if (changed) {
            notifyChanged();
        }
    }

    // ---- notifications ----

    private static void notifyChanged() {
        AndroidUtilities.runOnUIThread(() -> {
            List<Listener> copy;
            synchronized (listeners) {
                copy = new ArrayList<>(listeners);
            }
            for (Listener l : copy) {
                try {
                    l.onMentionsChanged();
                } catch (Throwable ignored) {
                }
            }
        });
    }

    private static void notifyDataChanged() {
        DataListener listener = dataListener;
        if (listener == null) {
            return;
        }
        AndroidUtilities.runOnUIThread(() -> {
            try {
                listener.onItemsChanged();
            } catch (Throwable ignored) {
            }
        });
    }

    static void log(String message, Throwable t) {
        Main.log(message, t);
    }
}
