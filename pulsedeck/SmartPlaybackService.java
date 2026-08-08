package com.orbit8d.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.MediaCodec;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaMetadataRetriever;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.Binder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.provider.OpenableColumns;

import java.io.File;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class SmartPlaybackService extends Service {
    public static final String ACTION_PLAY_PAUSE = "pulsedeck.playpause";
    public static final String ACTION_NEXT = "pulsedeck.next";
    private static final int NOTIFICATION_ID = 77;
    private static final String CHANNEL = "pulsedeck_playback";
    private static final long PORTION_MS = 30_000;
    private static final long CLIP_MS = 36_500;
    private static final long AUTO_FADE_MS = 6_000;
    private static final long MANUAL_FADE_MS = 3_300;

    public final class LocalBinder extends Binder {
        public SmartPlaybackService getService() { return SmartPlaybackService.this; }
    }

    public static final class Snapshot {
        public String status = "Add some songs";
        public String title = "";
        public String artist = "";
        public String trackKey = "";
        public byte[] art;
        public boolean playing;
        public boolean preparing;
        public double progress;
        public long positionMs;
        public int libraryVersion;
        public int currentIndex = -1;
        public final ArrayList<String> songNames = new ArrayList<>();
    }

    private static final class Track {
        final Uri uri;
        final String key;
        final String title;
        final String artist;
        final byte[] art;
        int plays;
        Track(Uri uri, String key, String title, String artist, byte[] art) {
            this.uri = uri; this.key = key; this.title = title; this.artist = artist; this.art = art;
        }
    }

    private static final class Prepared {
        final Track track;
        final File dir;
        final Map<String, File> stems;
        final long sourceStartMs;
        Prepared(Track track, File dir, Map<String, File> stems, long sourceStartMs) {
            this.track = track; this.dir = dir; this.stems = stems; this.sourceStartMs = sourceStartMs;
        }
    }

    private static final class Deck {
        final Prepared prepared;
        final MediaPlayer[] players;
        Deck(Prepared p, MediaPlayer[] players) { this.prepared = p; this.players = players; }
        int position() {
            try { return players.length == 0 ? 0 : players[0].getCurrentPosition(); } catch (Exception e) { return 0; }
        }
        int duration() {
            try { return players.length == 0 ? 0 : players[0].getDuration(); } catch (Exception e) { return 0; }
        }
    }

    private final IBinder binder = new LocalBinder();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Random random = new Random();
    private final ArrayList<Track> tracks = new ArrayList<>();
    private final ArrayList<Track> recent = new ArrayList<>();
    private final LinkedHashMap<String, Prepared> preparedCache = new LinkedHashMap<>(8, .75f, true);

    private AudioManager audioManager;
    private AudioFocusRequest focusRequest;
    private PowerManager.WakeLock wakeLock;
    private Deck deck;
    private Deck incoming;
    private Prepared readyStart;
    private Prepared nextPrepared;
    private Track queuedStart;
    private Track forcedNext;
    private boolean playing;
    private boolean playRequested;
    private boolean preparing;
    private boolean prepBusy;
    private boolean transitioning;
    private boolean skipPending;
    private double prepProgress;
    private String status = "Add some songs";
    private int libraryVersion;

    private final Runnable autoNext = () -> next(false);

    @Override public void onCreate() {
        super.onCreate();
        audioManager = (AudioManager) getSystemService(AUDIO_SERVICE);
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "PulseDeck:Playback");
        createNotificationChannel();
        startForeground(NOTIFICATION_ID, notification());
        clearDirectory(new File(getCacheDir(), "pulsedeck"));
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_PLAY_PAUSE.equals(intent.getAction())) togglePlay();
        else if (intent != null && ACTION_NEXT.equals(intent.getAction())) next(true);
        return START_NOT_STICKY;
    }

    @Override public IBinder onBind(Intent intent) { return binder; }

    @Override public void onDestroy() {
        main.removeCallbacksAndMessages(null);
        stopDeck(deck); stopDeck(incoming);
        worker.shutdownNow();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        abandonFocus();
        clearDirectory(new File(getCacheDir(), "pulsedeck"));
        super.onDestroy();
    }

    public synchronized void addSongs(List<Uri> uris) {
        if (uris == null) return;
        for (Uri uri : uris) {
            if (uri == null) continue;
            String key = uri.toString();
            boolean exists = false;
            for (Track t : tracks) if (t.key.equals(key)) { exists = true; break; }
            if (exists) continue;
            tracks.add(readTrack(uri));
        }
        libraryVersion++;
        if (queuedStart == null && deck == null && !tracks.isEmpty()) queuedStart = chooseNext(null);
        if (!tracks.isEmpty()) setStatusLocked("Preparing smart queue");
        prime();
    }

    public synchronized void togglePlay() {
        if (playing) { pauseInternal(); return; }
        playRequested = true;
        if (deck != null) { resumeInternal(); return; }
        if (readyStart != null) {
            Prepared p = readyStart; readyStart = null;
            main.post(() -> startDeck(p));
            return;
        }
        if (queuedStart == null && !tracks.isEmpty()) queuedStart = chooseNext(null);
        if (tracks.isEmpty()) setStatusLocked("Add some songs first");
        else setStatusLocked("Preparing first smart portion");
        prime();
    }

    public synchronized void next(boolean manual) {
        if (tracks.size() < 2 && deck == null) { togglePlay(); return; }
        if (transitioning) return;
        if (deck == null) {
            playRequested = true;
            queuedStart = chooseNext(queuedStart);
            readyStart = null;
            prime();
            return;
        }
        if (nextPrepared != null) {
            Prepared p = nextPrepared;
            main.post(() -> beginCrossfade(p, manual ? MANUAL_FADE_MS : AUTO_FADE_MS));
        } else {
            skipPending = true;
            setStatusLocked(manual ? "Next · finishing stem prep" : "Holding until next stems are ready");
            prime();
        }
    }

    public synchronized void previous() {
        if (recent.size() < 2 || deck == null) return;
        forcedNext = recent.get(recent.size() - 2);
        Prepared cached = preparedCache.get(forcedNext.key);
        if (cached != null) {
            nextPrepared = cached;
            main.post(() -> beginCrossfade(cached, MANUAL_FADE_MS));
        } else {
            nextPrepared = null;
            skipPending = true;
            setStatusLocked("Preparing previous song");
            prime();
        }
    }

    public synchronized Snapshot snapshot() {
        Snapshot s = new Snapshot();
        Track current = deck != null ? deck.prepared.track : (readyStart != null ? readyStart.track : queuedStart);
        s.status = status;
        s.playing = playing;
        s.preparing = preparing;
        s.positionMs = deck == null ? 0 : deck.position();
        double playProgress = deck == null ? 0 : Math.min(1.0, s.positionMs / (double) PORTION_MS);
        s.progress = preparing && deck == null ? prepProgress : playProgress;
        s.libraryVersion = libraryVersion;
        if (current != null) {
            s.title = current.title; s.artist = current.artist; s.art = current.art; s.trackKey = current.key;
            s.currentIndex = tracks.indexOf(current);
        }
        for (Track t : tracks) s.songNames.add(t.title);
        return s;
    }

    private Track readTrack(Uri uri) {
        String display = "Song";
        try (Cursor c = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) display = c.getString(0);
        } catch (Exception ignored) {}
        if (display != null) display = display.replaceFirst("\\.[^.]+$", "");
        String title = display == null ? "Song" : display;
        String artist = "";
        byte[] art = null;
        MediaMetadataRetriever mmr = new MediaMetadataRetriever();
        try {
            mmr.setDataSource(this, uri);
            String v = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_TITLE);
            if (v != null && !v.trim().isEmpty()) title = v.trim();
            v = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ARTIST);
            if (v != null) artist = v.trim();
            art = mmr.getEmbeddedPicture();
        } catch (Exception ignored) {
        } finally {
            try { mmr.release(); } catch (Exception ignored) {}
        }
        return new Track(uri, uri.toString(), title, artist, art);
    }

    private synchronized Track chooseNext(Track exclude) {
        ArrayList<Track> pool = new ArrayList<>();
        for (Track t : tracks) if (t != exclude) pool.add(t);
        if (pool.isEmpty()) return exclude != null ? exclude : (tracks.isEmpty() ? null : tracks.get(0));

        if (pool.size() > 2 && !recent.isEmpty()) {
            Track last = recent.get(recent.size() - 1);
            Track before = recent.size() > 1 ? recent.get(recent.size() - 2) : null;
            ArrayList<Track> fresh = new ArrayList<>();
            for (Track t : pool) if (t != last && t != before) fresh.add(t);
            if (!fresh.isEmpty()) pool = fresh;
        }

        int min = Integer.MAX_VALUE;
        for (Track t : pool) min = Math.min(min, t.plays);
        ArrayList<Track> near = new ArrayList<>();
        for (Track t : pool) if (t.plays <= min + 1) near.add(t);

        double total = 0;
        double[] weights = new double[near.size()];
        for (int i = 0; i < near.size(); i++) {
            int gap = near.get(i).plays - min;
            weights[i] = Math.exp(-1.25 * gap) * (0.65 + random.nextDouble() * 0.70);
            total += weights[i];
        }
        double r = random.nextDouble() * total;
        for (int i = 0; i < near.size(); i++) {
            r -= weights[i];
            if (r <= 0) return near.get(i);
        }
        return near.get(near.size() - 1);
    }

    private void prime() {
        final Track target;
        synchronized (this) {
            if (prepBusy || tracks.isEmpty()) return;
            if (deck == null) {
                if (readyStart != null) return;
                if (queuedStart == null) queuedStart = chooseNext(null);
                target = queuedStart;
            } else {
                if (nextPrepared != null) return;
                target = forcedNext != null ? forcedNext : chooseNext(deck.prepared.track);
            }
            if (target == null) return;
            Prepared cached = preparedCache.get(target.key);
            if (cached != null) {
                if (deck == null) readyStart = cached; else nextPrepared = cached;
                if (deck == null && playRequested) main.post(() -> startDeck(cached));
                else if (deck != null && skipPending) main.post(() -> beginCrossfade(cached, MANUAL_FADE_MS));
                return;
            }
            prepBusy = true;
            preparing = true;
            prepProgress = 0;
        }

        worker.submit(() -> {
            Prepared p = null;
            Exception failure = null;
            try {
                ensureModel();
                p = prepareTrack(target);
            } catch (Exception e) {
                failure = e;
            }
            final Prepared done = p;
            final Exception error = failure;
            main.post(() -> {
                synchronized (SmartPlaybackService.this) {
                    prepBusy = false;
                    preparing = false;
                    if (error != null) {
                        setStatusLocked("Prep failed · " + shortMessage(error));
                        return;
                    }
                    preparedCache.put(target.key, done);
                    trimCache();
                    if (deck == null) {
                        readyStart = done;
                        queuedStart = target;
                        setStatusLocked(playRequested ? "Starting" : "Ready to play");
                        if (playRequested) {
                            readyStart = null;
                            main.post(() -> startDeck(done));
                        }
                    } else if (target != deck.prepared.track) {
                        nextPrepared = done;
                        setStatusLocked(playing ? "Playing · next stems ready" : "Paused · next stems ready");
                        if (skipPending) main.post(() -> beginCrossfade(done, MANUAL_FADE_MS));
                    }
                }
            });
        });
    }

    private void ensureModel() throws Exception {
        if (ModelManager.isModelPresent(this)) return;
        setStatus("Downloading Lite stem model · first run only");
        ModelManager.download(this, new ModelManager.Progress() {
            @Override public void onProgress(double fraction, String message) {
                synchronized (SmartPlaybackService.this) {
                    prepProgress = fraction;
                    status = message;
                }
                updateNotification();
            }
            @Override public boolean isCancelled() { return Thread.currentThread().isInterrupted(); }
        });
    }

    private Prepared prepareTrack(Track t) throws Exception {
        setStatus("Scanning " + t.title + " for the exciting part");
        long startMs = AudioPrep.findExcitingStart(this, t.uri, CLIP_MS, (f, m) -> {
            synchronized (SmartPlaybackService.this) { prepProgress = .08 + f * .17; status = m; }
        });

        File root = new File(getCacheDir(), "pulsedeck"); root.mkdirs();
        File dir = new File(root, Integer.toHexString(t.key.hashCode()) + "_" + System.nanoTime());
        dir.mkdirs();
        File mix = new File(dir, "mix.wav");
        setStatus("Decoding smart portion · " + t.title);
        AudioPrep.decodeClip(this, t.uri, startMs, CLIP_MS, mix, (f, m) -> {
            synchronized (SmartPlaybackService.this) { prepProgress = .25 + f * .14; status = m; }
        });

        File stemDir = new File(dir, "stems");
        DemucsSeparator.Result result = DemucsSeparator.separate(
                mix, ModelManager.modelFile(this), stemDir, DemucsSeparator.Provider.NNAPI,
                new DemucsSeparator.Progress() {
                    @Override public void onProgress(double fraction, String message) {
                        synchronized (SmartPlaybackService.this) { prepProgress = .39 + fraction * .61; status = message; }
                    }
                    @Override public boolean isCancelled() { return Thread.currentThread().isInterrupted(); }
                });
        mix.delete();
        return new Prepared(t, dir, result.stems, startMs);
    }

    private void startDeck(Prepared p) {
        if (p == null || deck != null) return;
        try {
            requestFocus();
            Deck d = createDeck(p, 1f);
            for (MediaPlayer mp : d.players) mp.start();
            synchronized (this) {
                deck = d;
                playing = true;
                playRequested = false;
                p.track.plays++;
                addRecent(p.track);
                status = "Playing · smart stem portion";
                nextPrepared = null;
                skipPending = false;
            }
            holdWakeLock(true);
            scheduleAuto();
            updateNotification();
            prime();
        } catch (Exception e) {
            setStatus("Playback failed · " + shortMessage(e));
        }
    }

    private Deck createDeck(Prepared p, float initialVolume) throws Exception {
        String[] order = {"drums", "bass", "other", "vocals"};
        MediaPlayer[] players = new MediaPlayer[order.length];
        for (int i = 0; i < order.length; i++) {
            File f = p.stems.get(order[i]);
            if (f == null || !f.exists()) throw new IllegalStateException("Missing stem " + order[i]);
            MediaPlayer mp = new MediaPlayer();
            mp.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build());
            mp.setWakeMode(this, PowerManager.PARTIAL_WAKE_LOCK);
            mp.setDataSource(f.getAbsolutePath());
            mp.setVolume(initialVolume, initialVolume);
            mp.prepare();
            players[i] = mp;
        }
        players[0].setOnCompletionListener(mp -> main.post(() -> {
            if (!transitioning && playing) next(false);
        }));
        return new Deck(p, players);
    }

    private void beginCrossfade(Prepared p, long durationMs) {
        synchronized (this) {
            if (transitioning || deck == null || p == null || p.track == deck.prepared.track) return;
            transitioning = true;
            skipPending = false;
            nextPrepared = p;
            status = "Stem crossfade";
        }
        main.removeCallbacks(autoNext);
        final Deck out = deck;
        try {
            incoming = createDeck(p, 0f);
            for (MediaPlayer mp : incoming.players) mp.start();
        } catch (Exception e) {
            synchronized (this) { transitioning = false; incoming = null; status = "Crossfade failed · " + shortMessage(e); }
            prime();
            return;
        }
        final long began = SystemClock.uptimeMillis();
        final Runnable[] tick = new Runnable[1];
        tick[0] = () -> {
            if (!transitioning || incoming == null) return;
            double x = Math.min(1.0, (SystemClock.uptimeMillis() - began) / (double) durationMs);
            applyStemCrossfade(out, incoming, x);
            if (x < 1.0) {
                main.postDelayed(tick[0], 45);
            } else {
                stopDeck(out);
                synchronized (SmartPlaybackService.this) {
                    deck = incoming;
                    incoming = null;
                    deck.prepared.track.plays++;
                    addRecent(deck.prepared.track);
                    transitioning = false;
                    nextPrepared = null;
                    forcedNext = null;
                    playing = true;
                    status = "Playing · smart stem portion";
                }
                scheduleAuto();
                updateNotification();
                prime();
            }
        };
        main.post(tick[0]);
        updateNotification();
    }

    private void applyStemCrossfade(Deck out, Deck in, double x) {
        double eqOut = Math.cos(x * Math.PI * .5);
        double eqIn = Math.sin(x * Math.PI * .5);

        double outDrums = eqOut * (1.0 - .25 * smooth(x));
        double inDrums = eqIn * smooth(clamp((x - .12) / .88));
        double outBass = x < .54 ? 1.0 : Math.cos(clamp((x - .54) / .46) * Math.PI * .5);
        double inBass = x < .54 ? 0.0 : Math.sin(clamp((x - .54) / .46) * Math.PI * .5);
        double outOther = eqOut;
        double inOther = Math.sin(clamp((x + .08) / 1.08) * Math.PI * .5);
        double outVocals = Math.pow(eqOut, 1.15);
        double inVocals = Math.pow(eqIn, .92);

        setVol(out.players[0], outDrums); setVol(in.players[0], inDrums);
        setVol(out.players[1], outBass);  setVol(in.players[1], inBass);
        setVol(out.players[2], outOther); setVol(in.players[2], inOther);
        setVol(out.players[3], outVocals);setVol(in.players[3], inVocals);
    }

    private synchronized void pauseInternal() {
        if (deck == null) return;
        for (MediaPlayer mp : deck.players) safePause(mp);
        if (incoming != null) for (MediaPlayer mp : incoming.players) safePause(mp);
        playing = false;
        main.removeCallbacks(autoNext);
        status = "Paused";
        holdWakeLock(false);
        updateNotification();
    }

    private synchronized void resumeInternal() {
        if (deck == null) { prime(); return; }
        requestFocus();
        for (MediaPlayer mp : deck.players) safeStart(mp);
        if (incoming != null) for (MediaPlayer mp : incoming.players) safeStart(mp);
        playing = true;
        status = "Playing · smart stem portion";
        holdWakeLock(true);
        scheduleAuto();
        updateNotification();
    }

    private void scheduleAuto() {
        main.removeCallbacks(autoNext);
        Deck d = deck;
        if (d == null || !playing) return;
        long remaining = PORTION_MS - d.position();
        main.postDelayed(autoNext, Math.max(2_000, remaining));
    }

    private synchronized void addRecent(Track t) {
        recent.remove(t);
        recent.add(t);
        while (recent.size() > 6) recent.remove(0);
        libraryVersion++;
    }

    private synchronized void trimCache() {
        if (preparedCache.size() <= 4) return;
        ArrayList<String> keys = new ArrayList<>(preparedCache.keySet());
        for (String key : keys) {
            if (preparedCache.size() <= 4) break;
            Prepared p = preparedCache.get(key);
            boolean protectedEntry = (deck != null && deck.prepared.track.key.equals(key)) ||
                    (nextPrepared != null && nextPrepared.track.key.equals(key)) ||
                    (readyStart != null && readyStart.track.key.equals(key));
            if (!protectedEntry && p != null) {
                preparedCache.remove(key);
                clearDirectory(p.dir);
            }
        }
    }

    private void requestFocus() {
        if (audioManager == null) return;
        if (Build.VERSION.SDK_INT >= 26) {
            if (focusRequest == null) {
                AudioAttributes aa = new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build();
                focusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
                        .setAudioAttributes(aa)
                        .setOnAudioFocusChangeListener(change -> {
                            if (change <= AudioManager.AUDIOFOCUS_LOSS_TRANSIENT && change != AudioManager.AUDIOFOCUS_GAIN) main.post(this::pauseInternal);
                        }).build();
            }
            audioManager.requestAudioFocus(focusRequest);
        } else {
            audioManager.requestAudioFocus(change -> { if (change < 0) main.post(this::pauseInternal); }, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN);
        }
    }

    private void abandonFocus() {
        if (audioManager == null) return;
        if (Build.VERSION.SDK_INT >= 26 && focusRequest != null) audioManager.abandonAudioFocusRequest(focusRequest);
    }

    private void holdWakeLock(boolean hold) {
        try {
            if (hold && !wakeLock.isHeld()) wakeLock.acquire(60 * 60 * 1000L);
            else if (!hold && wakeLock.isHeld()) wakeLock.release();
        } catch (Exception ignored) {}
    }

    private synchronized void setStatusLocked(String s) { status = s; updateNotification(); }
    private void setStatus(String s) { synchronized (this) { status = s; } updateNotification(); }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel c = new NotificationChannel(CHANNEL, "PulseDeck playback", NotificationManager.IMPORTANCE_LOW);
            c.setDescription("Background smart stem playback");
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).createNotificationChannel(c);
        }
    }

    private Notification notification() {
        Track t;
        String st;
        boolean isPlaying;
        synchronized (this) {
            t = deck != null ? deck.prepared.track : queuedStart;
            st = status;
            isPlaying = playing;
        }
        Intent open = new Intent(this, SmartPlayerActivity.class);
        PendingIntent content = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent pp = PendingIntent.getService(this, 1, new Intent(this, SmartPlaybackService.class).setAction(ACTION_PLAY_PAUSE), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent nx = PendingIntent.getService(this, 2, new Intent(this, SmartPlaybackService.class).setAction(ACTION_NEXT), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        return b.setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(t == null ? "PulseDeck" : t.title)
                .setContentText(st)
                .setContentIntent(content)
                .setOngoing(isPlaying || preparing)
                .setOnlyAlertOnce(true)
                .addAction(new Notification.Action.Builder(0, isPlaying ? "Pause" : "Play", pp).build())
                .addAction(new Notification.Action.Builder(0, "Next", nx).build())
                .build();
    }

    private void updateNotification() {
        try { ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).notify(NOTIFICATION_ID, notification()); } catch (Exception ignored) {}
    }

    private static void safePause(MediaPlayer p) { try { if (p.isPlaying()) p.pause(); } catch (Exception ignored) {} }
    private static void safeStart(MediaPlayer p) { try { p.start(); } catch (Exception ignored) {} }
    private static void setVol(MediaPlayer p, double v) { float f = (float) Math.max(0, Math.min(1, v)); try { p.setVolume(f, f); } catch (Exception ignored) {} }
    private static void stopDeck(Deck d) {
        if (d == null) return;
        for (MediaPlayer p : d.players) {
            try { p.stop(); } catch (Exception ignored) {}
            try { p.release(); } catch (Exception ignored) {}
        }
    }
    private static double clamp(double x) { return Math.max(0, Math.min(1, x)); }
    private static double smooth(double x) { x = clamp(x); return x * x * (3 - 2 * x); }
    private static String shortMessage(Exception e) {
        String s = e.getMessage();
        if (s == null || s.trim().isEmpty()) s = e.getClass().getSimpleName();
        return s.length() > 72 ? s.substring(0, 72) : s;
    }
    private static void clearDirectory(File f) {
        if (f == null || !f.exists()) return;
        if (f.isDirectory()) {
            File[] children = f.listFiles();
            if (children != null) for (File c : children) clearDirectory(c);
        }
        f.delete();
    }

    private interface PrepProgress { void update(double fraction, String message); }

    private static final class AudioPrep {
        private static final long BUCKET_US = 250_000L;

        static long findExcitingStart(Context context, Uri uri, long clipMs, PrepProgress progress) throws Exception {
            EnergyCollector collector = new EnergyCollector();
            decode(context, uri, 0, Long.MAX_VALUE, collector);
            if (collector.rms.isEmpty()) return 0;
            progress.update(1.0, "Exciting section found");

            double[] r = normalize(collector.rms);
            double[] d = normalize(collector.diff);
            int window = Math.max(1, (int) Math.round(30_000_000.0 / BUCKET_US));
            int margin = Math.max(0, (int) Math.round(4_000_000.0 / BUCKET_US));
            double running = 0, best = -1;
            int bestStart = 0;
            for (int i = 0; i < r.length; i++) {
                double score = .72 * r[i] + .28 * d[i];
                running += score;
                if (i >= window) running -= .72 * r[i - window] + .28 * d[i - window];
                if (i >= window - 1) {
                    int start = i - window + 1;
                    boolean inside = r.length <= window + margin * 2 || (start >= margin && i < r.length - margin);
                    if (inside && running > best) { best = running; bestStart = start; }
                }
            }
            long startMs = bestStart * BUCKET_US / 1000L;
            long durationMs = collector.totalUs / 1000L;
            if (durationMs > clipMs) startMs = Math.min(startMs, durationMs - clipMs);
            else startMs = 0;
            return Math.max(0, startMs);
        }

        static void decodeClip(Context context, Uri uri, long startMs, long lengthMs, File output, PrepProgress progress) throws Exception {
            ClipCollector collector = new ClipCollector(startMs * 1000L, (startMs + lengthMs) * 1000L);
            long seek = Math.max(0, startMs * 1000L - 800_000L);
            decode(context, uri, seek, (startMs + lengthMs + 800) * 1000L, collector);
            if (collector.left.size == 0) throw new IllegalStateException("No PCM decoded from selected portion");
            int inRate = collector.sampleRate;
            int outRate = DemucsSeparator.SAMPLE_RATE;
            int outFrames = Math.max(1, (int) Math.round(collector.left.size * outRate / (double) inRate));
            output.getParentFile().mkdirs();
            try (WavFile.Writer writer = new WavFile.Writer(output, outRate, 2)) {
                final int block = 4096;
                float[] l = new float[block], rr = new float[block];
                int done = 0;
                while (done < outFrames) {
                    int n = Math.min(block, outFrames - done);
                    for (int i = 0; i < n; i++) {
                        double pos = (done + i) * inRate / (double) outRate;
                        int a = Math.min(collector.left.size - 1, (int) pos);
                        int b = Math.min(collector.left.size - 1, a + 1);
                        float f = (float) (pos - a);
                        l[i] = collector.left.data[a] * (1 - f) + collector.left.data[b] * f;
                        rr[i] = collector.right.data[a] * (1 - f) + collector.right.data[b] * f;
                    }
                    writer.writeFrames(l, rr, 0, n);
                    done += n;
                    progress.update(done / (double) outFrames, "Decoding smart portion");
                }
            }
        }

        private interface PcmConsumer {
            void format(int sampleRate, int channels, int encoding);
            void samples(ByteBuffer data, MediaCodec.BufferInfo info, long ptsUs, int sampleRate, int channels, int encoding);
            boolean shouldStop(long ptsUs);
            void end();
        }

        private static void decode(Context context, Uri uri, long seekUs, long stopUs, PcmConsumer consumer) throws Exception {
            MediaExtractor extractor = new MediaExtractor();
            MediaCodec codec = null;
            try {
                extractor.setDataSource(context, uri, null);
                int track = -1;
                MediaFormat fmt = null;
                for (int i = 0; i < extractor.getTrackCount(); i++) {
                    MediaFormat f = extractor.getTrackFormat(i);
                    String mime = f.getString(MediaFormat.KEY_MIME);
                    if (mime != null && mime.startsWith("audio/")) { track = i; fmt = f; break; }
                }
                if (track < 0 || fmt == null) throw new IllegalStateException("No audio track");
                extractor.selectTrack(track);
                if (seekUs > 0) extractor.seekTo(seekUs, MediaExtractor.SEEK_TO_PREVIOUS_SYNC);
                String mime = fmt.getString(MediaFormat.KEY_MIME);
                codec = MediaCodec.createDecoderByType(mime);
                codec.configure(fmt, null, null, 0);
                codec.start();

                boolean inputDone = false, outputDone = false;
                MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
                int sampleRate = fmt.containsKey(MediaFormat.KEY_SAMPLE_RATE) ? fmt.getInteger(MediaFormat.KEY_SAMPLE_RATE) : 44100;
                int channels = fmt.containsKey(MediaFormat.KEY_CHANNEL_COUNT) ? fmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT) : 2;
                int encoding = AudioFormat.ENCODING_PCM_16BIT;
                consumer.format(sampleRate, channels, encoding);

                while (!outputDone) {
                    if (!inputDone) {
                        int inIndex = codec.dequeueInputBuffer(10_000);
                        if (inIndex >= 0) {
                            ByteBuffer in = codec.getInputBuffer(inIndex);
                            int size = extractor.readSampleData(in, 0);
                            long pts = extractor.getSampleTime();
                            if (size < 0 || pts < 0 || pts > stopUs) {
                                codec.queueInputBuffer(inIndex, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                                inputDone = true;
                            } else {
                                codec.queueInputBuffer(inIndex, 0, size, pts, 0);
                                extractor.advance();
                            }
                        }
                    }

                    int outIndex = codec.dequeueOutputBuffer(info, 10_000);
                    if (outIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                        MediaFormat outFmt = codec.getOutputFormat();
                        if (outFmt.containsKey(MediaFormat.KEY_SAMPLE_RATE)) sampleRate = outFmt.getInteger(MediaFormat.KEY_SAMPLE_RATE);
                        if (outFmt.containsKey(MediaFormat.KEY_CHANNEL_COUNT)) channels = outFmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                        if (Build.VERSION.SDK_INT >= 24 && outFmt.containsKey(MediaFormat.KEY_PCM_ENCODING)) encoding = outFmt.getInteger(MediaFormat.KEY_PCM_ENCODING);
                        consumer.format(sampleRate, channels, encoding);
                    } else if (outIndex >= 0) {
                        ByteBuffer out = codec.getOutputBuffer(outIndex);
                        if (out != null && info.size > 0) {
                            ByteBuffer view = out.duplicate().order(ByteOrder.LITTLE_ENDIAN);
                            view.position(info.offset);
                            view.limit(info.offset + info.size);
                            consumer.samples(view.slice().order(ByteOrder.LITTLE_ENDIAN), info, info.presentationTimeUs, sampleRate, channels, encoding);
                        }
                        if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) outputDone = true;
                        codec.releaseOutputBuffer(outIndex, false);
                        if (consumer.shouldStop(info.presentationTimeUs)) outputDone = true;
                    }
                }
                consumer.end();
            } finally {
                if (codec != null) {
                    try { codec.stop(); } catch (Exception ignored) {}
                    try { codec.release(); } catch (Exception ignored) {}
                }
                try { extractor.release(); } catch (Exception ignored) {}
            }
        }

        private static final class EnergyCollector implements PcmConsumer {
            final ArrayList<Double> rms = new ArrayList<>();
            final ArrayList<Double> diff = new ArrayList<>();
            int sampleRate = 44100, channels = 2, encoding = AudioFormat.ENCODING_PCM_16BIT;
            int bucketSamples = 11025, count;
            double sq, delta, prev;
            long totalUs;
            @Override public void format(int sr, int ch, int enc) { sampleRate = sr; channels = Math.max(1, ch); encoding = enc; bucketSamples = Math.max(256, sr / 4); }
            @Override public void samples(ByteBuffer data, MediaCodec.BufferInfo info, long pts, int sr, int ch, int enc) {
                int bytes = enc == AudioFormat.ENCODING_PCM_FLOAT ? 4 : 2;
                int frames = data.remaining() / Math.max(1, bytes * ch);
                for (int i = 0; i < frames; i++) {
                    float l = sample(data, i * ch, enc);
                    float r = ch > 1 ? sample(data, i * ch + 1, enc) : l;
                    double x = (l + r) * .5;
                    sq += x * x;
                    delta += Math.abs(x - prev);
                    prev = x;
                    count++;
                    if (count >= bucketSamples) flush();
                }
                totalUs = Math.max(totalUs, pts + Math.round(frames * 1_000_000.0 / sr));
            }
            void flush() { if (count == 0) return; rms.add(Math.sqrt(sq / count)); diff.add(delta / count); sq = delta = 0; count = 0; }
            @Override public boolean shouldStop(long pts) { return false; }
            @Override public void end() { flush(); }
        }

        private static final class ClipCollector implements PcmConsumer {
            final long fromUs, toUs;
            final FloatList left = new FloatList(), right = new FloatList();
            int sampleRate = 44100, channels = 2, encoding = AudioFormat.ENCODING_PCM_16BIT;
            ClipCollector(long fromUs, long toUs) { this.fromUs = fromUs; this.toUs = toUs; }
            @Override public void format(int sr, int ch, int enc) { sampleRate = sr; channels = Math.max(1, ch); encoding = enc; }
            @Override public void samples(ByteBuffer data, MediaCodec.BufferInfo info, long pts, int sr, int ch, int enc) {
                int bytes = enc == AudioFormat.ENCODING_PCM_FLOAT ? 4 : 2;
                int frames = data.remaining() / Math.max(1, bytes * ch);
                for (int i = 0; i < frames; i++) {
                    long t = pts + Math.round(i * 1_000_000.0 / sr);
                    if (t < fromUs) continue;
                    if (t >= toUs) break;
                    float l = sample(data, i * ch, enc);
                    float r = ch > 1 ? sample(data, i * ch + 1, enc) : l;
                    left.add(l); right.add(r);
                }
            }
            @Override public boolean shouldStop(long pts) { return pts > toUs + 250_000; }
            @Override public void end() {}
        }

        private static float sample(ByteBuffer b, int sampleIndex, int encoding) {
            if (encoding == AudioFormat.ENCODING_PCM_FLOAT) {
                int at = sampleIndex * 4;
                if (at + 4 > b.limit()) return 0;
                float v = b.getFloat(at);
                return Float.isFinite(v) ? Math.max(-1f, Math.min(1f, v)) : 0f;
            }
            int at = sampleIndex * 2;
            if (at + 2 > b.limit()) return 0;
            return b.getShort(at) / 32768f;
        }

        private static double[] normalize(ArrayList<Double> values) {
            int n = values.size();
            double[] sorted = new double[n];
            for (int i = 0; i < n; i++) sorted[i] = values.get(i);
            java.util.Arrays.sort(sorted);
            double lo = sorted[Math.min(n - 1, (int) (n * .10))];
            double hi = sorted[Math.min(n - 1, (int) (n * .95))];
            double[] out = new double[n];
            for (int i = 0; i < n; i++) out[i] = clamp((values.get(i) - lo) / (hi - lo + 1e-12));
            return out;
        }

        private static final class FloatList {
            float[] data = new float[65536];
            int size;
            void add(float v) {
                if (size == data.length) data = java.util.Arrays.copyOf(data, data.length * 2);
                data[size++] = v;
            }
        }
    }
}
