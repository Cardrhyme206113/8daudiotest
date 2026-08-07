from pathlib import Path

JAVA = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app')

# Replace the handmade pre-spatialized MediaPlayer layer with Google's
# Resonance/Google VR sound-object engine. The class name stays the same so the
# rest of the activity lifecycle needs only a tiny constructor change.
(JAVA / 'RealtimeStemPlayer.java').write_text(r'''package com.orbit8d.app;

import android.content.Context;
import android.media.MediaMetadataRetriever;
import android.os.SystemClock;

import com.google.vr.sdk.audio.GvrAudioEngine;

import java.io.File;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Real game-style 3D stem player backed by Google's Resonance Audio / GVR Audio.
 *
 * Each musical stem is a real 3D sound object. Choreography supplies azimuth,
 * elevation and depth; this class converts those into XYZ world coordinates and
 * lets Resonance Audio perform HRTF binaural rendering, distance rolloff and room
 * reflections. The listener remains fixed at the origin looking toward -Z.
 */
public final class RealtimeStemPlayer {
    private final Context context;
    private final Map<String, File> stemFiles;
    private final Map<String, Integer> sourceIds = new LinkedHashMap<>();
    private final Choreography.Plan plan;
    private final GvrAudioEngine engine;

    private Choreography.Settings settings;
    private Runnable completionListener;
    private boolean playing = false;
    private boolean sourcesStarted = false;
    private int durationMs = 0;
    private int pausedPositionMs = 0;
    private long startedAtMs = 0;

    public RealtimeStemPlayer(Context context,
                              Map<String, File> stems,
                              Choreography.Plan plan,
                              Choreography.Settings initialSettings) throws Exception {
        this.context = context.getApplicationContext();
        this.stemFiles = new LinkedHashMap<>(stems);
        this.plan = plan;
        this.settings = initialSettings;

        engine = new GvrAudioEngine(this.context, GvrAudioEngine.RenderingMode.BINAURAL_HIGH_QUALITY);
        engine.setHeadPosition(0f, 0f, 0f);
        engine.setHeadRotation(0f, 0f, 0f, 1f);
        engine.enableSpeakerStereoMode(false);

        // A medium reflective room gives distance something physical to interact
        // with instead of our previous synthetic delay/reverb approximation.
        engine.setRoomProperties(
                14.0f, 6.0f, 14.0f,
                GvrAudioEngine.MaterialName.PLASTER_ROUGH,
                GvrAudioEngine.MaterialName.WOOD_CEILING,
                GvrAudioEngine.MaterialName.PARQUET_ON_CONCRETE);
        engine.setRoomReverbAdjustments(1.0f, 1.15f, 0.92f);
        engine.enableRoom(true);

        int minDuration = Integer.MAX_VALUE;
        for (String stem : Choreography.STEMS) {
            File f = stemFiles.get(stem);
            if (f == null || !f.exists()) throw new IllegalStateException("Missing stem: " + stem);
            minDuration = Math.min(minDuration, durationOf(f));
        }
        durationMs = minDuration == Integer.MAX_VALUE ? 0 : minDuration;
        createSources();
        updateSpatialState(0.0);
        engine.update();
    }

    private static int durationOf(File f) {
        MediaMetadataRetriever mmr = new MediaMetadataRetriever();
        try {
            mmr.setDataSource(f.getAbsolutePath());
            String v = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION);
            return v == null ? Integer.MAX_VALUE : Integer.parseInt(v);
        } catch (Exception e) {
            return Integer.MAX_VALUE;
        } finally {
            try { mmr.release(); } catch (Exception ignored) {}
        }
    }

    private void createSources() {
        destroySources();
        for (String stem : Choreography.STEMS) {
            File f = stemFiles.get(stem);
            engine.preloadSoundFile(f.getAbsolutePath());
            int id = engine.createSoundObject(f.getAbsolutePath());
            if (id == GvrAudioEngine.INVALID_ID) {
                throw new IllegalStateException("Resonance Audio could not create 3D source for " + stem);
            }
            engine.setSoundObjectDistanceRolloffModel(
                    id, GvrAudioEngine.DistanceRolloffModel.LOGARITHMIC, 0.55f, 20.0f);
            sourceIds.put(stem, id);
        }
        sourcesStarted = false;
    }

    private void destroySources() {
        for (Integer id : sourceIds.values()) {
            try { if (engine.isSourceIdValid(id)) engine.stopSound(id); } catch (Exception ignored) {}
        }
        sourceIds.clear();
    }

    public synchronized void setOnCompletionListener(Runnable listener) {
        completionListener = listener;
    }

    public synchronized void setGains(Choreography.Settings newSettings) {
        if (newSettings != null) settings = newSettings;
        updateSpatialState(getCurrentPosition() / 1000.0);
        engine.update();
    }

    // Kept for API compatibility; normal UI uses setGains().
    public synchronized void setGain(String stem, int percent) {
        if (settings == null) return;
        if ("drums".equals(stem)) settings.drumsGain = percent;
        else if ("bass".equals(stem)) settings.bassGain = percent;
        else if ("other".equals(stem)) settings.otherGain = percent;
        else if ("vocals".equals(stem)) settings.vocalsGain = percent;
        updateSpatialState(getCurrentPosition() / 1000.0);
        engine.update();
    }

    public synchronized void start() {
        if (playing) return;
        if (pausedPositionMs >= Math.max(0, durationMs - 100) || sourceIds.isEmpty()) {
            pausedPositionMs = 0;
            createSources();
        }

        if (!sourcesStarted) {
            // GVR has no arbitrary PCM seek API. Playback therefore starts from 0;
            // pause/resume is exact, while the seek bar is disabled by the activity.
            pausedPositionMs = 0;
            updateSpatialState(0.0);
            for (Integer id : sourceIds.values()) engine.playSound(id, false);
            sourcesStarted = true;
        } else {
            for (Integer id : sourceIds.values()) {
                try { if (engine.isSourceIdValid(id)) engine.resumeSound(id); } catch (Exception ignored) {}
            }
        }
        startedAtMs = SystemClock.elapsedRealtime() - pausedPositionMs;
        playing = true;
        engine.resume();
        engine.update();
    }

    public synchronized void pause() {
        if (!playing) return;
        pausedPositionMs = getCurrentPosition();
        for (Integer id : sourceIds.values()) {
            try { if (engine.isSourceIdValid(id)) engine.pauseSound(id); } catch (Exception ignored) {}
        }
        playing = false;
        engine.update();
    }

    public synchronized void stop() {
        playing = false;
        pausedPositionMs = 0;
        destroySources();
        createSources();
        updateSpatialState(0.0);
        engine.update();
    }

    // Resonance sound objects intentionally do not expose seek. Keep this method
    // harmless because MainActivity disables user seeking in Resonance mode.
    public synchronized void seekTo(int ms) {
        if (ms <= 50) stop();
    }

    public synchronized boolean isPlaying() { return playing; }

    public synchronized int getCurrentPosition() {
        if (!playing) return pausedPositionMs;
        long now = SystemClock.elapsedRealtime();
        return (int) Math.max(0, Math.min(durationMs, now - startedAtMs));
    }

    public synchronized int getDuration() { return durationMs; }

    /** Called by the existing ~30 Hz UI ticker on the main thread. */
    public synchronized void syncIfNeeded() {
        if (!playing) {
            engine.update();
            return;
        }
        int pos = getCurrentPosition();
        if (pos >= durationMs - 20) {
            playing = false;
            pausedPositionMs = durationMs;
            engine.update();
            if (completionListener != null) completionListener.run();
            return;
        }
        updateSpatialState(pos / 1000.0);
        engine.update();
    }

    private void updateSpatialState(double t) {
        if (settings == null || plan == null) return;
        Choreography.Event event = plan.eventAt(t);
        String focus = event == null ? null : event.focusStem;
        double intensity = Choreography.clamp(settings.intensity / 100.0, 0.0, 3.0);

        double focusDistance = 0.0;
        for (String stem : Choreography.STEMS) {
            Integer id = sourceIds.get(stem);
            if (id == null || !engine.isSourceIdValid(id)) continue;

            Choreography.Position p = plan.position(stem, t, settings);
            boolean isFocus = focus != null && focus.equals(stem);

            // Translate Orbit8D's normalized depth into actual game-world metres.
            // >100% intensity expands the world, so 300% can make the focus source
            // travel dramatically outside the normal room before it returns.
            double metres = 0.70 + p.distance * (6.5 + 1.8 * Math.max(0.0, intensity - 1.0));
            metres = Choreography.clamp(metres, 0.55, 19.0);
            if (isFocus) focusDistance = metres;

            double az = Math.toRadians(p.azimuthDeg);
            double el = Math.toRadians(p.elevationDeg);
            double cosEl = Math.cos(el);
            float x = (float) (Math.sin(az) * cosEl * metres);
            float y = (float) (Math.sin(el) * metres);
            float z = (float) (-Math.cos(az) * cosEl * metres);
            engine.setSoundObjectPosition(id, x, y, z);

            // Directional spotlight rule: moving focus = 100% algorithmic presence,
            // every other stem <=50%. User 0-200% sliders multiply on top in realtime.
            double scene = focus == null ? 1.0 : (isFocus ? 1.0 : 0.50);
            double manual = settings.manualGainPercent(stem) / 100.0;
            // 100% manual focus -> 0.5 native volume, leaving headroom for four stems;
            // 200% -> 1.0. Supports remain half of that during a spotlight.
            float volume = (float) Choreography.clamp(0.50 * scene * manual, 0.0, 1.0);
            engine.setSoundVolume(id, volume);
        }

        // Let the room tail react gently to the featured source depth. Resonance
        // computes the actual room response; we're only changing its physical feel.
        if (focus != null) {
            float depth = (float) Choreography.clamp(focusDistance / 14.0, 0.0, 1.0);
            engine.setRoomReverbAdjustments(
                    0.86f + 0.34f * depth,
                    1.00f + 0.55f * depth,
                    0.96f - 0.20f * depth);
        } else {
            engine.setRoomReverbAdjustments(0.90f, 1.08f, 0.94f);
        }
    }

    public synchronized void release() {
        playing = false;
        destroySources();
        for (File f : stemFiles.values()) {
            try { engine.unloadSoundFile(f.getAbsolutePath()); } catch (Exception ignored) {}
        }
        try { engine.pause(); } catch (Exception ignored) {}
    }
}
''')

# MainActivity already talks to RealtimeStemPlayer. Point it at the raw separated
# stems and provide the choreography/context so Resonance can spatialize live.
p = JAVA / 'MainActivity.java'
s = p.read_text()
old = 'player = new RealtimeStemPlayer(project.renderedStems);'
new = 'player = new RealtimeStemPlayer(this, project.stems, project.plan, readSettings());'
if old not in s:
    raise SystemExit('patch anchor not found: Resonance player constructor')
s = s.replace(old, new, 1)

# There is no seek API for GVR sound objects. Disable scrubbing rather than letting
# UI and audio lie about position; normal play/pause and progress display remain.
needle = 'play.setEnabled(true);\n            export.setEnabled(true);'
if needle in s:
    s = s.replace(needle, 'play.setEnabled(true);\n            export.setEnabled(true);\n            seek.setEnabled(false);', 1)
else:
    # Alternate source layout used by the original activity.
    needle = 'playPause.setEnabled(true);'
    if needle in s:
        s = s.replace(needle, 'playPause.setEnabled(true);\n                seek.setEnabled(false);', 1)

# Make the status explicit so testing makes it obvious which renderer is active.
s = s.replace('status.setText("Ready");', 'status.setText("Ready · Resonance Audio HRTF 3D");')

p.write_text(s)
print('Orbit8D v0.4: Resonance Audio game-style HRTF playback enabled')
