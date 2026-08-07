from pathlib import Path
import re

JAVA = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app')


def require_sub(text, pattern, repl, name, flags=0):
    out, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if n != 1:
        raise SystemExit(f'patch anchor not found: {name}')
    return out

# -----------------------------------------------------------------------------
# AudioProject: keep the four spatialized stems so playback can mix them live.
# -----------------------------------------------------------------------------
(JAVA / 'AudioProject.java').write_text(r'''package com.orbit8d.app;

import android.net.Uri;

import java.io.File;
import java.util.LinkedHashMap;
import java.util.Map;

public final class AudioProject {
    public Uri sourceUri;
    public String displayName = "track";
    public File workDir;
    public File mixWav;
    public File renderedWav;
    public final Map<String, File> stems = new LinkedHashMap<>();
    // Spatial/DJ processing is baked here, but manual stem gains are deliberately not.
    // This lets the four volume sliders work instantly during playback.
    public final Map<String, File> renderedStems = new LinkedHashMap<>();
    public StructureAnalyzer.Analysis analysis;
    public Choreography.Plan plan;
    public String providerUsed = "";

    public boolean hasStems() {
        for (String name : Choreography.STEMS) {
            File f = stems.get(name);
            if (f == null || !f.exists() || f.length() < 44) return false;
        }
        return true;
    }

    public boolean hasStemRender() {
        for (String name : Choreography.STEMS) {
            File f = renderedStems.get(name);
            if (f == null || !f.exists() || f.length() < 44) return false;
        }
        return true;
    }

    public boolean hasRender() {
        return hasStemRender() || (renderedWav != null && renderedWav.exists() && renderedWav.length() > 44);
    }
}
''')

# -----------------------------------------------------------------------------
# Choreography: give every stem a recognizable spatial identity.
# -----------------------------------------------------------------------------
p = JAVA / 'Choreography.java'
s = p.read_text()

s = require_sub(s,
    r'''    private static double baseElevation\(String stem, double t, Settings settings\) \{.*?\n    \}\n\n    private static double baseDistance''',
    r'''    private static double baseElevation(String stem, double t, Settings settings) {
        double intensity = clamp(settings.intensity / 100.0, 0.0, 1.0);
        // Deliberately different vertical signatures: vocals travel most, "other"
        // floats broadly, drums stay near the horizontal plane, bass is grounded.
        double amp = "vocals".equals(stem) ? 38.0 : "other".equals(stem) ? 31.0
                : "drums".equals(stem) ? 11.0 : 4.5;
        double period = "vocals".equals(stem) ? 25.0 : "other".equals(stem) ? 33.0
                : "drums".equals(stem) ? 18.5 : 43.0;
        return amp * (0.32 + 0.68 * intensity)
                * Math.sin(2.0 * Math.PI * t / period + stemPhase(stem));
    }

    private static double baseDistance''',
    'distinct elevation', re.S)

s = require_sub(s,
    r'''    private static double baseDistance\(String stem, double t, Settings settings\) \{.*?\n    \}\n\n    public static double focusEnvelope''',
    r'''    private static double baseDistance(String stem, double t, Settings settings) {
        double intensity = clamp(settings.intensity / 100.0, 0.0, 1.0);
        // Separate the normal room shells before any focus throw is applied.
        // Bass = intimate/anchored, drums = near/punchy, vocals = medium/variable,
        // other = naturally wider and farther into the room.
        double center = "other".equals(stem) ? 0.43 : "vocals".equals(stem) ? 0.18
                : "drums".equals(stem) ? 0.095 : 0.035;
        double amp = "other".equals(stem) ? 0.22 : "vocals".equals(stem) ? 0.16
                : "drums".equals(stem) ? 0.055 : 0.018;
        double period = "other".equals(stem) ? 37.0 : "vocals".equals(stem) ? 29.0
                : "drums".equals(stem) ? 21.0 : 47.0;
        double wave = 0.5 + 0.5 * Math.sin(2.0 * Math.PI * t / period + stemPhase(stem) * 0.73);
        return clamp(center + amp * wave * (0.42 + 0.58 * intensity), 0.015, 0.78);
    }

    public static double focusEnvelope''',
    'distinct distance', re.S)

old = '''            if (focus) elev += verticalDirection * (14.0 + 22.0 * intensity) * focusArc;\n            if ("bass".equals(stem)) elev *= 0.42; // keep sub movement grounded\n            out.elevationDeg = clamp(elev, -48.0, 48.0);'''
new = '''            double verticalFocusScale = "vocals".equals(stem) ? 1.38 : "other".equals(stem) ? 1.12\n                    : "drums".equals(stem) ? 0.62 : 0.24;\n            if (focus) elev += verticalDirection * (14.0 + 22.0 * intensity) * focusArc * verticalFocusScale;\n            double elevLimit = "vocals".equals(stem) ? 56.0 : "other".equals(stem) ? 49.0\n                    : "drums".equals(stem) ? 22.0 : 9.0;\n            out.elevationDeg = clamp(elev, -elevLimit, elevLimit);'''
if old not in s:
    raise SystemExit('patch anchor not found: focus elevation identity')
s = s.replace(old, new, 1)

# Keep non-focus stems in more strongly separated depth shells. The focus branch
# below remains the v0.3.39 ~3x overshoot and final-second return.
old = '''            double normalDistance = clamp(d * (0.76 + 0.24 * intensity), 0.02, 0.95);\n            if (focus) {'''
new = '''            double normalDistance = clamp(d * (0.76 + 0.24 * intensity), 0.02, 0.95);\n            if (!focus) {\n                if ("bass".equals(stem)) normalDistance = clamp(normalDistance * 0.50, 0.015, 0.15);\n                else if ("drums".equals(stem)) normalDistance = clamp(0.035 + normalDistance * 0.72, 0.04, 0.34);\n                else if ("vocals".equals(stem)) normalDistance = clamp(0.055 + normalDistance * 1.04, 0.06, 0.58);\n                else if ("other".equals(stem)) normalDistance = clamp(0.17 + normalDistance * 1.08, 0.28, 0.90);\n            }\n            if (focus) {'''
if old not in s:
    raise SystemExit('patch anchor not found: non-focus depth shells')
s = s.replace(old, new, 1)
p.write_text(s)

# -----------------------------------------------------------------------------
# SpatialRenderer: render four aligned spatial WAVs with AUTO gain only.
# Manual gain is mixed live in RealtimeStemPlayer instead of being baked into them.
# -----------------------------------------------------------------------------
p = JAVA / 'SpatialRenderer.java'
s = p.read_text()

# The normal renderer's gain helper becomes AUTO-only. This helper is also used by
# the new per-stem renderer below.
old = '''        double autoGain = Choreography.dbToGain(db);\n        double manual = Choreography.clamp(settings.manualGainPercent(stem) / 100.0, 0.0, 2.0);\n        return autoGain * manual;'''
new = '''        return Choreography.dbToGain(db);'''
if old not in s:
    raise SystemExit('patch anchor not found: auto-only gain')
s = s.replace(old, new, 1)

# More distinct room signatures between stems.
old = '''        if ("bass".equals(stem)) base *= 0.52;\n        return Choreography.clamp(base, 0.015, 0.56);'''
new = '''        if ("bass".equals(stem)) base *= 0.34;\n        else if ("drums".equals(stem)) base *= 0.66;\n        else if ("vocals".equals(stem)) base *= 1.08;\n        else if ("other".equals(stem)) base *= 1.32;\n        return Choreography.clamp(base, 0.012, 0.72);'''
if old not in s:
    raise SystemExit('patch anchor not found: distinct room signatures')
s = s.replace(old, new, 1)

anchor = '    private static double stemGain(String stem, Choreography.Event event, Choreography.Settings settings, double focusEnv) {'
if anchor not in s:
    raise SystemExit('patch anchor not found: renderer method insertion')

methods = r'''    // A controlled boost is baked into each independent preview stream so 100%
    // realtime volume (= 0.5 MediaPlayer gain) remains close to the legacy mix level.
    private static final double LIVE_STEM_RENDER_BOOST = 1.55;

    public static void renderStemSet(
            Map<String, File> stems,
            Choreography.Plan plan,
            Choreography.Settings settings,
            File outputDir,
            Map<String, File> outputs,
            Progress progress) throws Exception {
        final int sr = 44100;
        final int block = 2048;
        outputDir.mkdirs();
        outputs.clear();
        List<StemStream> streams = new ArrayList<>();
        Map<String, WavFile.Writer> writers = new java.util.LinkedHashMap<>();
        long totalFrames = Long.MAX_VALUE;
        try {
            for (String stem : Choreography.STEMS) {
                File input = stems.get(stem);
                if (input == null || !input.exists()) throw new IllegalStateException("Missing stem: " + stem);
                StemStream stream = new StemStream(stem, input, block, sr);
                streams.add(stream);
                totalFrames = Math.min(totalFrames, stream.reader.info().frames);
                File output = new File(outputDir, "spatial_" + stem + ".wav");
                if (output.exists()) output.delete();
                outputs.put(stem, output);
                writers.put(stem, new WavFile.Writer(output, sr, 2));
            }
            if (totalFrames == Long.MAX_VALUE) throw new IllegalStateException("No stems to render");
            float[] outL = new float[block];
            float[] outR = new float[block];
            long frame = 0;
            while (frame < totalFrames) {
                if (progress.isCancelled()) throw new InterruptedException("Cancelled");
                int n = (int) Math.min(block, totalFrames - frame);
                double t0 = frame / (double) sr;
                double t1 = (frame + Math.max(0, n - 1)) / (double) sr;
                double tc = (t0 + t1) * 0.5;
                Choreography.Event event = plan.eventAt(tc);
                double focusEnv = Choreography.focusEnvelope(event, tc);
                double intensity = Choreography.clamp(settings.intensity / 100.0, 0.0, 1.0);

                for (StemStream stream : streams) {
                    int got = stream.reader.readFrames(stream.left, stream.right, 0, n);
                    if (got <= 0) continue;
                    Choreography.Position p0 = plan.position(stream.name, t0, settings);
                    Choreography.Position p1 = plan.position(stream.name, t1, settings);
                    boolean isFocus = event != null && stream.name.equals(event.focusStem);
                    double autoGain = stemGain(stream.name, event, settings, focusEnv);
                    for (int i = 0; i < got; i++) {
                        double u = got <= 1 ? 0.0 : i / (double) (got - 1);
                        double az = Choreography.lerp(p0.azimuthDeg, p1.azimuthDeg, u);
                        double elevation = Choreography.lerp(p0.elevationDeg, p1.elevationDeg, u);
                        double distance = Choreography.lerp(p0.distance, p1.distance, u);
                        double room = roomAmount(stream.name, event, settings, focusEnv, distance, az);
                        stream.panner.process(
                                stream.left[i], stream.right[i], az, elevation, distance,
                                intensity, isFocus ? focusEnv : 0.0, room,
                                outL, outR, i);
                        outL[i] = softLimit((float) (outL[i] * autoGain * LIVE_STEM_RENDER_BOOST));
                        outR[i] = softLimit((float) (outR[i] * autoGain * LIVE_STEM_RENDER_BOOST));
                    }
                    writers.get(stream.name).writeFrames(outL, outR, 0, got);
                }
                frame += n;
                progress.onProgress(frame / (double) totalFrames,
                        "Rendering independent 8D stems · " + Math.round(frame / (double) totalFrames * 100) + "%");
            }
        } finally {
            for (WavFile.Writer writer : writers.values()) try { writer.close(); } catch (Exception ignored) {}
            for (StemStream stream : streams) try { stream.close(); } catch (Exception ignored) {}
        }
        progress.onProgress(1.0, "Independent 8D stems ready");
    }

    public static void mixRenderedStems(
            Map<String, File> renderedStems,
            Choreography.Settings settings,
            File output,
            Progress progress) throws Exception {
        final int sr = 44100;
        final int block = 2048;
        Map<String, WavFile.Reader> readers = new java.util.LinkedHashMap<>();
        Map<String, float[]> left = new java.util.LinkedHashMap<>();
        Map<String, float[]> right = new java.util.LinkedHashMap<>();
        long totalFrames = Long.MAX_VALUE;
        try {
            for (String stem : Choreography.STEMS) {
                File f = renderedStems.get(stem);
                if (f == null || !f.exists()) throw new IllegalStateException("Missing spatial stem: " + stem);
                WavFile.Reader r = new WavFile.Reader(f);
                readers.put(stem, r);
                left.put(stem, new float[block]);
                right.put(stem, new float[block]);
                totalFrames = Math.min(totalFrames, r.info().frames);
            }
            if (output.exists()) output.delete();
            try (WavFile.Writer writer = new WavFile.Writer(output, sr, 2)) {
                float[] mixL = new float[block];
                float[] mixR = new float[block];
                long frame = 0;
                while (frame < totalFrames) {
                    if (progress.isCancelled()) throw new InterruptedException("Cancelled");
                    int n = (int) Math.min(block, totalFrames - frame);
                    java.util.Arrays.fill(mixL, 0, n, 0f);
                    java.util.Arrays.fill(mixR, 0, n, 0f);
                    for (String stem : Choreography.STEMS) {
                        float[] l = left.get(stem), r = right.get(stem);
                        int got = readers.get(stem).readFrames(l, r, 0, n);
                        double manual = Choreography.clamp(settings.manualGainPercent(stem) / 100.0, 0.0, 2.0);
                        // Undo the preview-only boost before making an export/reference mix.
                        double gain = manual / LIVE_STEM_RENDER_BOOST;
                        for (int i = 0; i < got; i++) {
                            mixL[i] += l[i] * gain;
                            mixR[i] += r[i] * gain;
                        }
                    }
                    for (int i = 0; i < n; i++) {
                        mixL[i] = softLimit(mixL[i] * 0.92f);
                        mixR[i] = softLimit(mixR[i] * 0.92f);
                    }
                    writer.writeFrames(mixL, mixR, 0, n);
                    frame += n;
                    progress.onProgress(frame / (double) totalFrames,
                            "Mixing export reference · " + Math.round(frame / (double) totalFrames * 100) + "%");
                }
            }
        } finally {
            for (WavFile.Reader reader : readers.values()) try { reader.close(); } catch (Exception ignored) {}
        }
        progress.onProgress(1.0, "Reference mix ready");
    }

'''
s = s.replace(anchor, methods + anchor, 1)
p.write_text(s)

# -----------------------------------------------------------------------------
# ProcessingEngine: expensive spatial render once -> four cached spatial stems.
# A cheap reference/export mix is generated after that.
# -----------------------------------------------------------------------------
(JAVA / 'ProcessingEngine.java').write_text(r'''package com.orbit8d.app;

import android.content.Context;

import java.io.File;
import java.util.Map;

public final class ProcessingEngine {
    private ProcessingEngine() {}

    public interface Callback {
        void onProgress(double fraction, String message);
        boolean isCancelled();
    }

    public static void processAll(
            Context context,
            AudioProject project,
            File model,
            DemucsSeparator.Provider provider,
            Choreography.Settings settings,
            Callback callback) throws Exception {
        if (project.sourceUri == null) throw new IllegalStateException("Choose a song first");
        if (!model.exists()) throw new IllegalStateException("Download the Lite model pack first");
        project.workDir.mkdirs();

        project.mixWav = AudioDecoder.decodeTo44100Stereo(context, project.sourceUri, project.workDir,
                audioProgress(callback, 0.00, 0.10));
        project.analysis = StructureAnalyzer.analyzeMix(project.mixWav,
                structureProgress(callback, 0.10, 0.19));

        File stemDir = new File(project.workDir, "stems");
        DemucsSeparator.Result separation = DemucsSeparator.separate(
                project.mixWav, model, stemDir, provider,
                demucsProgress(callback, 0.19, 0.73));
        project.stems.clear();
        project.stems.putAll(separation.stems);
        project.providerUsed = separation.provider;

        Map<String, double[]> stemScores = StructureAnalyzer.measureStemScores(
                project.stems, project.analysis.sections,
                structureProgress(callback, 0.73, 0.80));
        project.plan = Choreography.build(
                project.analysis.sections, stemScores,
                project.analysis.duration, project.analysis.tempo);

        File spatialStemDir = new File(project.workDir, "spatial_stems");
        SpatialRenderer.renderStemSet(project.stems, project.plan, settings,
                spatialStemDir, project.renderedStems,
                spatialProgress(callback, 0.80, 0.97));

        project.renderedWav = new File(project.workDir, "orbit8d_render.wav");
        SpatialRenderer.mixRenderedStems(project.renderedStems, settings, project.renderedWav,
                spatialProgress(callback, 0.97, 1.00));
        callback.onProgress(1.0, "Ready · realtime stem mixer · " + project.plan.sections.size()
                + " parts · " + project.providerUsed);
    }

    public static void rerender(
            AudioProject project,
            Choreography.Settings settings,
            Callback callback) throws Exception {
        if (!project.hasStems() || project.plan == null) throw new IllegalStateException("No cached stems yet");
        File spatialStemDir = new File(project.workDir, "spatial_stems");
        SpatialRenderer.renderStemSet(project.stems, project.plan, settings,
                spatialStemDir, project.renderedStems,
                spatialProgress(callback, 0.0, 0.94));
        project.renderedWav = new File(project.workDir, "orbit8d_render.wav");
        SpatialRenderer.mixRenderedStems(project.renderedStems, settings, project.renderedWav,
                spatialProgress(callback, 0.94, 1.0));
        callback.onProgress(1.0, "Re-render ready · manual volume remains realtime");
    }

    private static AudioDecoder.Progress audioProgress(Callback cb, double a, double b) {
        return new AudioDecoder.Progress() {
            @Override public void onProgress(double fraction, String message) { cb.onProgress(a + (b - a) * fraction, message); }
            @Override public boolean isCancelled() { return cb.isCancelled(); }
        };
    }

    private static DemucsSeparator.Progress demucsProgress(Callback cb, double a, double b) {
        return new DemucsSeparator.Progress() {
            @Override public void onProgress(double fraction, String message) { cb.onProgress(a + (b - a) * fraction, message); }
            @Override public boolean isCancelled() { return cb.isCancelled(); }
        };
    }

    private static StructureAnalyzer.Progress structureProgress(Callback cb, double a, double b) {
        return new StructureAnalyzer.Progress() {
            @Override public void onProgress(double fraction, String message) { cb.onProgress(a + (b - a) * fraction, message); }
            @Override public boolean isCancelled() { return cb.isCancelled(); }
        };
    }

    private static SpatialRenderer.Progress spatialProgress(Callback cb, double a, double b) {
        return new SpatialRenderer.Progress() {
            @Override public void onProgress(double fraction, String message) { cb.onProgress(a + (b - a) * fraction, message); }
            @Override public boolean isCancelled() { return cb.isCancelled(); }
        };
    }
}
''')

# -----------------------------------------------------------------------------
# Four synchronized MediaPlayers: spatial movement is already baked per stem;
# manual volume is the only last-stage control and therefore changes instantly.
# -----------------------------------------------------------------------------
(JAVA / 'RealtimeStemPlayer.java').write_text(r'''package com.orbit8d.app;

import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.os.SystemClock;

import java.io.File;
import java.util.LinkedHashMap;
import java.util.Map;

public final class RealtimeStemPlayer {
    private final Map<String, MediaPlayer> players = new LinkedHashMap<>();
    private MediaPlayer master;
    private Runnable completionListener;
    private boolean playing = false;
    private int pausedPositionMs = 0;
    private int durationMs = 0;
    private long lastSyncAt = 0;

    public RealtimeStemPlayer(Map<String, File> renderedStems) throws Exception {
        AudioAttributes attrs = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                .build();
        int minDuration = Integer.MAX_VALUE;
        for (String stem : Choreography.STEMS) {
            File file = renderedStems.get(stem);
            if (file == null || !file.exists()) throw new IllegalStateException("Missing spatial stem: " + stem);
            MediaPlayer mp = new MediaPlayer();
            mp.setAudioAttributes(attrs);
            mp.setDataSource(file.getAbsolutePath());
            mp.prepare();
            mp.setLooping(false);
            players.put(stem, mp);
            minDuration = Math.min(minDuration, mp.getDuration());
            if (master == null || "drums".equals(stem)) master = mp;
        }
        durationMs = minDuration == Integer.MAX_VALUE ? 0 : minDuration;
        if (master != null) {
            master.setOnCompletionListener(mp -> {
                synchronized (RealtimeStemPlayer.this) {
                    playing = false;
                    pausedPositionMs = durationMs;
                    for (MediaPlayer p : players.values()) {
                        if (p == master) continue;
                        try { if (p.isPlaying()) p.pause(); } catch (Exception ignored) {}
                    }
                }
                if (completionListener != null) completionListener.run();
            });
        }
    }

    public synchronized void setOnCompletionListener(Runnable listener) {
        this.completionListener = listener;
    }

    public synchronized void setGains(Choreography.Settings settings) {
        setGain("drums", settings.drumsGain);
        setGain("bass", settings.bassGain);
        setGain("other", settings.otherGain);
        setGain("vocals", settings.vocalsGain);
    }

    public synchronized void setGain(String stem, int percent) {
        MediaPlayer mp = players.get(stem);
        if (mp == null) return;
        // 100% = 0.5 and 200% = 1.0. This preserves true 2x amplitude range while
        // leaving enough mixer headroom for four simultaneously playing sources.
        float v = Math.max(0f, Math.min(1f, percent / 200f));
        try { mp.setVolume(v, v); } catch (Exception ignored) {}
    }

    public synchronized void start() {
        if (playing || master == null) return;
        if (pausedPositionMs >= Math.max(0, durationMs - 80)) pausedPositionMs = 0;
        for (MediaPlayer mp : players.values()) {
            try {
                if (Math.abs(mp.getCurrentPosition() - pausedPositionMs) > 18) mp.seekTo(pausedPositionMs);
            } catch (Exception ignored) {}
        }
        // Start all four immediately after alignment. Drift is corrected unobtrusively
        // by syncIfNeeded(), called by the existing ~30 Hz UI ticker.
        for (MediaPlayer mp : players.values()) {
            try { mp.start(); } catch (Exception ignored) {}
        }
        playing = true;
        lastSyncAt = SystemClock.uptimeMillis();
    }

    public synchronized void pause() {
        if (master == null) return;
        try { pausedPositionMs = master.getCurrentPosition(); } catch (Exception ignored) {}
        for (MediaPlayer mp : players.values()) {
            try { if (mp.isPlaying()) mp.pause(); } catch (Exception ignored) {}
        }
        playing = false;
    }

    public synchronized void stop() {
        for (MediaPlayer mp : players.values()) {
            try { mp.stop(); } catch (Exception ignored) {}
        }
        playing = false;
        pausedPositionMs = 0;
    }

    public synchronized void seekTo(int ms) {
        int target = Math.max(0, Math.min(durationMs, ms));
        pausedPositionMs = target;
        for (MediaPlayer mp : players.values()) {
            try { mp.seekTo(target); } catch (Exception ignored) {}
        }
    }

    public synchronized boolean isPlaying() {
        return playing && master != null && master.isPlaying();
    }

    public synchronized int getCurrentPosition() {
        if (master == null) return pausedPositionMs;
        try { return master.getCurrentPosition(); } catch (Exception e) { return pausedPositionMs; }
    }

    public synchronized int getDuration() { return durationMs; }

    public synchronized void syncIfNeeded() {
        if (!playing || master == null) return;
        long now = SystemClock.uptimeMillis();
        if (now - lastSyncAt < 1100) return;
        lastSyncAt = now;
        int ref;
        try { ref = master.getCurrentPosition(); } catch (Exception e) { return; }
        for (MediaPlayer mp : players.values()) {
            if (mp == master) continue;
            try {
                int drift = mp.getCurrentPosition() - ref;
                if (Math.abs(drift) > 42) mp.seekTo(ref);
            } catch (Exception ignored) {}
        }
    }

    public synchronized void release() {
        playing = false;
        for (MediaPlayer mp : players.values()) {
            try { mp.release(); } catch (Exception ignored) {}
        }
        players.clear();
        master = null;
    }
}
''')

# -----------------------------------------------------------------------------
# MainActivity: swap one premixed MediaPlayer for RealtimeStemPlayer and apply
# manual slider values on every user drag event.
# -----------------------------------------------------------------------------
p = JAVA / 'MainActivity.java'
s = p.read_text()

if 'private MediaPlayer player;' not in s:
    raise SystemExit('patch anchor not found: MediaPlayer field')
s = s.replace('private MediaPlayer player;', 'private RealtimeStemPlayer player;', 1)

s = s.replace('if (project == null || !project.hasRender()) {',
              'if (project == null || !project.hasStemRender()) {', 1)
s = s.replace('status.setText("Process or re-render first");',
              'status.setText("Process the song first");', 1)

old = '''                player = new MediaPlayer();
                player.setDataSource(project.renderedWav.getAbsolutePath());
                player.prepare();
                player.setOnCompletionListener(mp -> {
                    playPause.setText("PLAY");
                    ui.removeCallbacks(ticker);
                    seek.setProgress(seek.getMax());
                });'''
new = '''                player = new RealtimeStemPlayer(project.renderedStems);
                player.setGains(readSettings());
                player.setOnCompletionListener(() -> {
                    playPause.setText("PLAY");
                    ui.removeCallbacks(ticker);
                    seek.setProgress(seek.getMax());
                });'''
if old not in s:
    raise SystemExit('patch anchor not found: playback creation')
s = s.replace(old, new, 1)

old = '''                if (fromUser && (seekBar == drumsGain || seekBar == bassGain || seekBar == otherGain || seekBar == vocalsGain)) {
                    saveStemGains();
                }'''
new = '''                if (fromUser && (seekBar == drumsGain || seekBar == bassGain || seekBar == otherGain || seekBar == vocalsGain)) {
                    saveStemGains();
                    // No render: four spatial stem players receive the new gain now.
                    if (player != null) player.setGains(readSettings());
                }'''
if old not in s:
    raise SystemExit('patch anchor not found: realtime gain listener')
s = s.replace(old, new, 1)

s = s.replace(
    '"0–200%. Multiplies the algorithm\'s boosts/cuts; re-render does not rerun separation."',
    '"0–200%. LIVE while playing · multiplies AUTO instantly; no re-render needed."',
    1)

old = '''            double t = player.getCurrentPosition() / 1000.0;'''
new = '''            player.syncIfNeeded();
            double t = player.getCurrentPosition() / 1000.0;'''
if old not in s:
    raise SystemExit('patch anchor not found: playback sync ticker')
s = s.replace(old, new, 1)

p.write_text(s)
print('Orbit8D v0.3.2 patch: distinct stem identities + realtime manual stem mixer applied')
