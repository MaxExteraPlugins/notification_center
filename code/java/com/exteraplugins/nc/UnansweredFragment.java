package com.exteraplugins.nc;

import android.content.Context;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.ChatMessageSharedResources;
import org.telegram.messenger.MessageObject;
import org.telegram.messenger.MessagesController;
import org.telegram.messenger.R;
import org.telegram.tgnet.TLObject;
import org.telegram.tgnet.TLRPC;
import org.telegram.ui.ActionBar.ActionBar;
import org.telegram.ui.ActionBar.ActionBarMenu;
import org.telegram.ui.ActionBar.AlertDialog;
import org.telegram.ui.ActionBar.BaseFragment;
import org.telegram.ui.ActionBar.Theme;
import org.telegram.ui.Cells.ChatMessageCell;
import org.telegram.ui.ChatActivity;
import org.telegram.ui.Components.AvatarDrawable;
import org.telegram.ui.Components.BackupImageView;
import org.telegram.ui.Components.LayoutHelper;
import org.telegram.ui.Components.RecyclerListView;

import java.util.ArrayList;
import java.util.List;

public class UnansweredFragment extends BaseFragment implements MentionStore.Listener {

    private static final int MP = -1;
    private static final int WC = -2;
    private static final int MENU_CLEAR_ID = 100;
    private static final long OPEN_DELAY = 250L;
    private static final long DOUBLE_TAP_MS = 300L;

    private static final int TYPE_CHAT = 0;

    private RecyclerListView listView;
    private MentionAdapter adapter;
    private TextView emptyView;
    private ChatMessageSharedResources sharedResources;

    private int pendingPosition = -1;
    private long pendingTime;
    private final Runnable openRunnable = new Runnable() {
        @Override
        public void run() {
            if (pendingPosition < 0 || adapter == null) {
                return;
            }
            List<MentionStore.Item> items = adapter.getItems();
            if (pendingPosition < items.size()) {
                openChat(items.get(pendingPosition));
            }
            pendingPosition = -1;
        }
    };

    @Override
    public boolean onFragmentCreate() {
        MentionStore.addListener(this);
        // data comes from the python plugin (sync mode) - no server backfill
        MentionStore.fetchMissingObjects(currentAccount);
        return super.onFragmentCreate();
    }

    @Override
    public void onFragmentDestroy() {
        AndroidUtilities.cancelRunOnUIThread(openRunnable);
        pendingPosition = -1;
        MentionStore.removeListener(this);
        super.onFragmentDestroy();
    }

    @Override
    public View createView(Context context) {
        actionBar.setBackButtonImage(R.drawable.ic_ab_back);
        actionBar.setTitle(Main.Loc.get(Main.Loc.TAB_TITLE));
        ActionBarMenu menu = actionBar.createMenu();
        if (menu.getItem(MENU_CLEAR_ID) == null) {
            menu.addItem(MENU_CLEAR_ID, R.drawable.msg_delete);
        }
        actionBar.setActionBarMenuOnItemClick(new ActionBar.ActionBarMenuOnItemClick() {
            @Override
            public void onItemClick(int id) {
                if (id == -1) {
                    finishFragment();
                } else if (id == MENU_CLEAR_ID) {
                    confirmClearAll();
                }
            }
        });

        FrameLayout rootView = new FrameLayout(context);
        rootView.setBackgroundColor(Theme.getColor(Theme.key_windowBackgroundGray));
        fragmentView = rootView;

        listView = new RecyclerListView(context);
        listView.setLayoutManager(new LinearLayoutManager(context));
        listView.setClipToPadding(false);
        listView.setPadding(0, AndroidUtilities.dp(6), 0, AndroidUtilities.dp(6));
        adapter = new MentionAdapter();
        listView.setAdapter(adapter);
        listView.setOnItemClickListener((view, position) -> onItemClicked(position));
        rootView.addView(listView, LayoutHelper.createFrame(MP, MP));

        emptyView = new TextView(context);
        emptyView.setText(Main.Loc.get(Main.Loc.EMPTY));
        emptyView.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteGrayText));
        emptyView.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 15);
        emptyView.setGravity(Gravity.CENTER);
        rootView.addView(emptyView, LayoutHelper.createFrame(WC, WC, Gravity.CENTER, 40, 0, 40, 0));
        listView.setEmptyView(emptyView);

        adapter.swapItems(MentionStore.getItems(currentAccount));
        return fragmentView;
    }

    @Override
    public void onResume() {
        super.onResume();
        // Retry the history fetch for items that are still placeholder rows.
        List<MentionStore.Item> items = MentionStore.getItems(currentAccount);
        for (MentionStore.Item item : items) {
            if (MentionStore.getObject(currentAccount, item.dialogId, item.msgId) == null) {
                MentionStore.fetchMissingObjects(currentAccount);
                break;
            }
        }
    }

    @Override
    public void onMentionsChanged() {
        if (adapter != null) {
            adapter.swapItems(MentionStore.getItems(currentAccount));
        }
    }

    private void onItemClicked(int position) {
        List<MentionStore.Item> items = adapter.getItems();
        if (position < 0 || position >= items.size()) {
            return;
        }
        MentionStore.Item item = items.get(position);
        long now = SystemClock.elapsedRealtime();
        if (position == pendingPosition && now - pendingTime < DOUBLE_TAP_MS) {
            AndroidUtilities.cancelRunOnUIThread(openRunnable);
            pendingPosition = -1;
            MentionStore.hide(currentAccount, item.dialogId, item.msgId);
        } else {
            AndroidUtilities.cancelRunOnUIThread(openRunnable);
            pendingPosition = position;
            pendingTime = now;
            AndroidUtilities.runOnUIThread(openRunnable, OPEN_DELAY);
        }
    }

    private void openChat(MentionStore.Item item) {
        try {
            Bundle args = new Bundle();
            if (item.dialogId > 0) {
                args.putLong("user_id", item.dialogId);
            } else {
                args.putLong("chat_id", -item.dialogId);
            }
            args.putInt("message_id", item.msgId);
            presentFragment(new ChatActivity(args));
        } catch (Throwable t) {
            Main.log("openChat failed", t);
        }
    }

    private void confirmClearAll() {
        if (!Main.confirmClearEnabled()) {
            MentionStore.clearAll(currentAccount);
            return;
        }
        try {
            if (getParentActivity() == null) {
                return;
            }
            AlertDialog.Builder builder = new AlertDialog.Builder(getParentActivity(), getResourceProvider());
            builder.setTitle(Main.Loc.get(Main.Loc.CLEAR_TITLE));
            builder.setMessage(Main.Loc.get(Main.Loc.CLEAR_MESSAGE));
            builder.setPositiveButton(Main.Loc.get(Main.Loc.CLEAR_BTN), (dialog, which) -> {
                dialog.dismiss();
                MentionStore.clearAll(currentAccount);
            });
            builder.setNegativeButton(Main.Loc.get(Main.Loc.CANCEL_BTN), (dialog, which) -> dialog.dismiss());
            AlertDialog dialog = builder.create();
            showDialog(dialog);
        } catch (Throwable t) {
            Main.log("confirmClearAll failed", t);
        }
    }

    private class MentionAdapter extends RecyclerView.Adapter<RecyclerListView.Holder> {

        private final List<MentionStore.Item> items = new ArrayList<>();

        void swapItems(List<MentionStore.Item> newItems) {
            items.clear();
            if (newItems != null) {
                items.addAll(newItems);
            }
            notifyDataSetChanged();
        }

        List<MentionStore.Item> getItems() {
            return items;
        }

        @Override
        public int getItemViewType(int position) {
            return TYPE_CHAT;
        }

        @Override
        public RecyclerListView.Holder onCreateViewHolder(ViewGroup parent, int viewType) {
            Context context = parent.getContext();
            if (sharedResources == null) {
                sharedResources = new ChatMessageSharedResources(context);
            }
            ChatMessageCell cell = new ChatMessageCell(context, currentAccount, true, sharedResources, null);
            // The cell reserves space for the sender avatar but draws none in
            // a standalone context (its avatar path is tied to ChatActivity),
            // and its onLayout never lays out child views - so a child view
            // is positioned manually on every cell layout.
            final BackupImageView avatarView = new BackupImageView(context);
            avatarView.setRoundRadius(AndroidUtilities.dp(21));
            avatarView.setEnabled(false);
            avatarView.setClickable(false);
            cell.addView(avatarView, new FrameLayout.LayoutParams(AndroidUtilities.dp(42), AndroidUtilities.dp(42)));
            cell.addOnLayoutChangeListener((v, l, t, r, b, ol, ot, or2, ob) ->
                    layoutAvatar(avatarView));
            layoutAvatar(avatarView);
            CellHolder holder = new CellHolder(cell);
            holder.avatarView = avatarView;
            return holder;
        }

        private void layoutAvatar(BackupImageView avatarView) {
            int size = AndroidUtilities.dp(42);
            int left = AndroidUtilities.dp(9);
            int top = AndroidUtilities.dp(2);
            if (avatarView.getLeft() != left || avatarView.getTop() != top
                    || avatarView.getRight() != left + size || avatarView.getBottom() != top + size) {
                avatarView.layout(left, top, left + size, top + size);
            }
        }

        @Override
        public void onBindViewHolder(RecyclerListView.Holder holder, int position) {
            MentionStore.Item item = items.get(position);
            CellHolder cellHolder = (CellHolder) holder;
            MessageObject mo = MentionStore.getObjectOrSynthetic(currentAccount, item);
            if (mo != null) {
                ChatMessageCell cell = (ChatMessageCell) holder.itemView;
                mo.forceAvatar = true;
                cell.isChat = true;
                cell.isAvatarVisible = true;
                cell.setMessageObject(mo, null, false, false, true, true);
            }
            layoutAvatar(cellHolder.avatarView);
            bindAvatar(cellHolder.avatarView, item);
        }

        private void bindAvatar(BackupImageView avatarView, MentionStore.Item item) {
            try {
                MessagesController mc = MessagesController.getInstance(currentAccount);
                TLObject peer = null;
                if (item.senderId > 0) {
                    peer = mc.getUser(item.senderId);
                } else if (item.senderId < 0) {
                    peer = mc.getChat(-item.senderId);
                }
                if (peer == null) {
                    if (item.dialogId > 0) {
                        peer = mc.getUser(item.dialogId);
                    } else {
                        peer = mc.getChat(-item.dialogId);
                    }
                }
                if (peer != null) {
                    AvatarDrawable avatarDrawable = new AvatarDrawable();
                    avatarDrawable.setInfo(currentAccount, peer);
                    avatarView.setForUserOrChat(peer, avatarDrawable);
                } else {
                    // Sender not in the cache yet (restored from disk before
                    // the history fetch): show a colored initials avatar from
                    // the stored name instead of nothing.
                    String name = item.senderName != null && !item.senderName.isEmpty()
                            ? item.senderName
                            : "—";
                    AvatarDrawable avatarDrawable = new AvatarDrawable();
                    avatarDrawable.setInfo(item.dialogId, name, "");
                    avatarView.setImageDrawable(avatarDrawable);
                }
            } catch (Throwable t) {
                Main.log("bindAvatar failed", t);
            }
        }

        @Override
        public int getItemCount() {
            return items.size();
        }
    }

    static class CellHolder extends RecyclerListView.Holder {
        BackupImageView avatarView;

        CellHolder(View itemView) {
            super(itemView);
        }
    }
}
