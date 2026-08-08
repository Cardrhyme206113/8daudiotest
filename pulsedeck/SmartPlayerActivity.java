package com.orbit8d.app;

import android.Manifest;
import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.Space;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

public class SmartPlayerActivity extends Activity {
    private static final int PICK_AUDIO = 41;
    private static final int BG = Color.rgb(18, 16, 20);
    private static final int PANEL = Color.rgb(31, 27, 34);
    private static final int PANEL_2 = Color.rgb(39, 33, 43);
    private static final int TEXT = Color.rgb(246, 241, 247);
    private static final int MUTED = Color.rgb(173, 163, 178);
    private static final int ACCENT = Color.rgb(142, 108, 147);
    private static final int ACCENT_2 = Color.rgb(190, 158, 196);

    private final Handler ui = new Handler(Looper.getMainLooper());
    private SmartPlaybackService service;
    private boolean bound;
    private String lastArtKey = "";
    private int lastLibraryVersion = -1;

    private ImageView artwork;
    private TextView artFallback;
    private TextView status;
    private TextView title;
    private TextView artist;
    private TextView time;
    private TextView librarySummary;
    private LinearLayout songs;
    private ProgressBar progress;
    private Button play;

    private final ServiceConnection connection = new ServiceConnection() {
        @Override public void onServiceConnected(ComponentName name, IBinder binder) {
            service = ((SmartPlaybackService.LocalBinder) binder).getService();
            bound = true;
            refreshNow();
        }
        @Override public void onServiceDisconnected(ComponentName name) {
            bound = false;
            service = null;
        }
    };

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Window w = getWindow();
        w.setStatusBarColor(BG);
        w.setNavigationBarColor(BG);
        if (Build.VERSION.SDK_INT >= 23) w.getDecorView().setSystemUiVisibility(0);
        setContentView(buildUi());

        Intent svc = new Intent(this, SmartPlaybackService.class);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(svc); else startService(svc);

        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 9);
        }
        ui.post(poller);
    }

    @Override protected void onStart() {
        super.onStart();
        bindService(new Intent(this, SmartPlaybackService.class), connection, Context.BIND_AUTO_CREATE);
    }

    @Override protected void onStop() {
        if (bound) unbindService(connection);
        bound = false;
        service = null;
        super.onStop();
    }

    @Override protected void onDestroy() {
        ui.removeCallbacks(poller);
        super.onDestroy();
    }

    private final Runnable poller = new Runnable() {
        @Override public void run() {
            refreshNow();
            ui.postDelayed(this, 350);
        }
    };

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(BG);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(18), dp(18), dp(18), dp(28));
        scroll.addView(root, new ScrollView.LayoutParams(-1, -2));

        TextView brand = text("PulseDeck", 26, TEXT, Typeface.BOLD);
        root.addView(brand);
        TextView tag = text("30-second smart stem shuffle", 13, MUTED, Typeface.NORMAL);
        tag.setPadding(0, dp(2), 0, dp(16));
        root.addView(tag);

        LinearLayout playerCard = card();
        playerCard.setPadding(dp(16), dp(16), dp(16), dp(16));
        root.addView(playerCard, lp(-1, -2, 0, dp(12)));

        FrameLayout artBox = new FrameLayout(this);
        artBox.setBackground(round(PANEL_2, 18));
        playerCard.addView(artBox, new LinearLayout.LayoutParams(-1, dp(290)));

        artwork = new ImageView(this);
        artwork.setScaleType(ImageView.ScaleType.CENTER_CROP);
        artwork.setVisibility(View.GONE);
        artBox.addView(artwork, new FrameLayout.LayoutParams(-1, -1));

        artFallback = text("♪", 72, ACCENT_2, Typeface.NORMAL);
        artFallback.setGravity(Gravity.CENTER);
        artBox.addView(artFallback, new FrameLayout.LayoutParams(-1, -1));

        status = text("ADD SOME SONGS", 11, ACCENT_2, Typeface.BOLD);
        status.setLetterSpacing(.12f);
        status.setPadding(0, dp(18), 0, 0);
        playerCard.addView(status);

        title = text("Nothing playing", 26, TEXT, Typeface.BOLD);
        title.setSingleLine(true);
        title.setEllipsize(android.text.TextUtils.TruncateAt.END);
        title.setPadding(0, dp(5), 0, 0);
        playerCard.addView(title);

        artist = text("Select multiple MP3s below", 14, MUTED, Typeface.NORMAL);
        artist.setSingleLine(true);
        artist.setEllipsize(android.text.TextUtils.TruncateAt.END);
        playerCard.addView(artist);

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(1000);
        progress.setProgressTintList(android.content.res.ColorStateList.valueOf(ACCENT_2));
        progress.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(Color.rgb(55, 47, 60)));
        playerCard.addView(progress, lp(-1, dp(7), dp(18), 0));

        time = text("0:00  ·  smart portion", 12, MUTED, Typeface.NORMAL);
        time.setGravity(Gravity.END);
        time.setPadding(0, dp(6), 0, 0);
        playerCard.addView(time);

        LinearLayout controls = new LinearLayout(this);
        controls.setGravity(Gravity.CENTER);
        controls.setPadding(0, dp(17), 0, 0);
        playerCard.addView(controls, new LinearLayout.LayoutParams(-1, -2));

        Button prev = button("↶", false);
        prev.setOnClickListener(v -> { if (service != null) service.previous(); });
        controls.addView(prev, new LinearLayout.LayoutParams(dp(58), dp(54)));

        play = button("Play", true);
        play.setOnClickListener(v -> { if (service != null) service.togglePlay(); });
        LinearLayout.LayoutParams playLp = new LinearLayout.LayoutParams(0, dp(54), 1f);
        playLp.setMargins(dp(10), 0, dp(10), 0);
        controls.addView(play, playLp);

        Button next = button("↷", false);
        next.setOnClickListener(v -> { if (service != null) service.next(true); });
        controls.addView(next, new LinearLayout.LayoutParams(dp(58), dp(54)));

        LinearLayout libraryCard = card();
        libraryCard.setPadding(dp(15), dp(15), dp(15), dp(15));
        root.addView(libraryCard, lp(-1, -2, 0, 0));

        LinearLayout head = new LinearLayout(this);
        head.setGravity(Gravity.CENTER_VERTICAL);
        libraryCard.addView(head, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        head.addView(labels, new LinearLayout.LayoutParams(0, -2, 1f));
        labels.addView(text("Added songs", 17, TEXT, Typeface.BOLD));
        librarySummary = text("Nothing selected", 12, MUTED, Typeface.NORMAL);
        labels.addView(librarySummary);

        Button add = button("+ Add songs", false);
        add.setPadding(dp(14), 0, dp(14), 0);
        add.setOnClickListener(v -> pickSongs());
        head.addView(add, new LinearLayout.LayoutParams(-2, dp(46)));

        songs = new LinearLayout(this);
        songs.setOrientation(LinearLayout.VERTICAL);
        songs.setPadding(0, dp(10), 0, 0);
        libraryCard.addView(songs, new LinearLayout.LayoutParams(-1, -2));

        TextView foot = text("Songs stay local. The ~79 MB Lite stem pack is downloaded once; selections live for this app session.", 11, MUTED, Typeface.NORMAL);
        foot.setPadding(dp(3), dp(12), dp(3), 0);
        root.addView(foot);
        return scroll;
    }

    private void pickSongs() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        i.setType("audio/*");
        i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        startActivityForResult(i, PICK_AUDIO);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != PICK_AUDIO || resultCode != RESULT_OK || data == null || service == null) return;
        ArrayList<Uri> picked = new ArrayList<>();
        if (data.getClipData() != null) {
            for (int i = 0; i < data.getClipData().getItemCount(); i++) picked.add(data.getClipData().getItemAt(i).getUri());
        } else if (data.getData() != null) {
            picked.add(data.getData());
        }
        service.addSongs(picked);
    }

    private void refreshNow() {
        SmartPlaybackService s = service;
        if (!bound || s == null) return;
        SmartPlaybackService.Snapshot snap = s.snapshot();
        status.setText(snap.status.toUpperCase());
        title.setText(snap.title == null || snap.title.isEmpty() ? "Nothing playing" : snap.title);
        artist.setText(snap.artist == null || snap.artist.isEmpty() ? "Unknown artist" : snap.artist);
        play.setText(snap.playing ? "Pause" : "Play");
        progress.setProgress((int) (Math.max(0, Math.min(1, snap.progress)) * 1000));
        time.setText(format(snap.positionMs) + "  ·  " + (snap.preparing ? "preparing next stems" : "smart portion"));

        String artKey = snap.trackKey == null ? "" : snap.trackKey;
        if (!artKey.equals(lastArtKey)) {
            lastArtKey = artKey;
            if (snap.art != null && snap.art.length > 0) {
                Bitmap b = BitmapFactory.decodeByteArray(snap.art, 0, snap.art.length);
                if (b != null) {
                    artwork.setImageBitmap(b);
                    artwork.setVisibility(View.VISIBLE);
                    artFallback.setVisibility(View.GONE);
                } else showFallback();
            } else showFallback();
        }

        if (snap.libraryVersion != lastLibraryVersion) {
            lastLibraryVersion = snap.libraryVersion;
            librarySummary.setText(snap.songNames.size() + (snap.songNames.size() == 1 ? " song" : " songs") + " · least-played random shuffle");
            songs.removeAllViews();
            int shown = Math.min(7, snap.songNames.size());
            for (int i = 0; i < shown; i++) {
                TextView row = text("  ♪  " + snap.songNames.get(i), 13, i == snap.currentIndex ? TEXT : MUTED, i == snap.currentIndex ? Typeface.BOLD : Typeface.NORMAL);
                row.setGravity(Gravity.CENTER_VERTICAL);
                row.setBackground(round(i == snap.currentIndex ? PANEL_2 : Color.TRANSPARENT, 10));
                row.setSingleLine(true);
                row.setEllipsize(android.text.TextUtils.TruncateAt.END);
                songs.addView(row, lp(-1, dp(42), i == 0 ? 0 : dp(3), 0));
            }
            if (snap.songNames.size() > shown) {
                TextView more = text("+ " + (snap.songNames.size() - shown) + " more", 12, ACCENT_2, Typeface.BOLD);
                more.setPadding(dp(12), dp(6), 0, 0);
                songs.addView(more);
            }
        }
    }

    private void showFallback() {
        artwork.setImageDrawable(null);
        artwork.setVisibility(View.GONE);
        artFallback.setVisibility(View.VISIBLE);
    }

    private LinearLayout card() {
        LinearLayout v = new LinearLayout(this);
        v.setOrientation(LinearLayout.VERTICAL);
        v.setBackground(round(PANEL, 18));
        return v;
    }

    private Button button(String label, boolean primary) {
        Button b = new Button(this);
        b.setText(label);
        b.setTextSize(14);
        b.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        b.setTextColor(TEXT);
        b.setAllCaps(false);
        b.setBackground(round(primary ? ACCENT : PANEL_2, 14));
        b.setStateListAnimator(null);
        b.setMinWidth(0);
        b.setMinHeight(0);
        return b;
    }

    private TextView text(String s, int sp, int color, int style) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(color);
        t.setTypeface(Typeface.create("sans", style));
        return t;
    }

    private GradientDrawable round(int color, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(dp(radiusDp));
        return g;
    }

    private LinearLayout.LayoutParams lp(int w, int h, int top, int bottom) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(w, h);
        p.setMargins(0, top, 0, bottom);
        return p;
    }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }
    private static String format(long ms) {
        long s = Math.max(0, ms / 1000);
        return (s / 60) + ":" + String.format(java.util.Locale.US, "%02d", s % 60);
    }
}
