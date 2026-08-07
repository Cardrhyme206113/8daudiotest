from pathlib import Path

p = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app/SpatialRenderer.java')
s = p.read_text()

# The independent-stem renderer added by patch_distinct_realtime_v032 is the
# right layer for this: spatial/DJ automation stays baked, while manual sliders
# remain a realtime multiplier in RealtimeStemPlayer.
old = '''        Map<String, WavFile.Writer> writers = new java.util.LinkedHashMap<>();
        long totalFrames = Long.MAX_VALUE;'''
new = '''        Map<String, WavFile.Writer> writers = new java.util.LinkedHashMap<>();
        // Focus/background scene changes are deliberately latched only while the
        // affected stem is locally quiet, so large mix/room changes are hidden.
        SilenceSceneMixer sceneMixer = new SilenceSceneMixer();
        long totalFrames = Long.MAX_VALUE;'''
if old not in s:
    raise SystemExit('patch anchor not found: scene mixer allocation')
s = s.replace(old, new, 1)

old = '''                    boolean isFocus = event != null && stream.name.equals(event.focusStem);
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
                    }'''
new = '''                    boolean isFocus = event != null && stream.name.equals(event.focusStem);
                    double autoGain = stemGain(stream.name, event, settings, focusEnv);
                    double rms = blockRms(stream.left, stream.right, got);
                    double sceneGain = sceneMixer.update(stream.name, event, tc, rms);
                    for (int i = 0; i < got; i++) {
                        double u = got <= 1 ? 0.0 : i / (double) (got - 1);
                        double az = Choreography.lerp(p0.azimuthDeg, p1.azimuthDeg, u);
                        double elevation = Choreography.lerp(p0.elevationDeg, p1.elevationDeg, u);
                        double distance = Choreography.lerp(p0.distance, p1.distance, u);

                        // Scene contrast is more than volume. A background stem is
                        // pushed slightly deeper/darker/wetter; when its new focus
                        // state latches during silence it re-enters nearer and clearer.
                        if (!isFocus) {
                            distance = Choreography.clamp(
                                    distance + (1.0 - sceneGain) * 0.24, 0.0, 1.25);
                        }
                        double room = roomAmount(stream.name, event, settings, focusEnv, distance, az);
                        if (!isFocus) {
                            room = Choreography.clamp(
                                    room + (1.0 - sceneGain) * 0.16, 0.01, 0.82);
                        }
                        stream.panner.process(
                                stream.left[i], stream.right[i], az, elevation, distance,
                                intensity, isFocus ? focusEnv : 0.0, room,
                                outL, outR, i);
                        double sceneAuto = autoGain * sceneGain;
                        outL[i] = softLimit((float) (outL[i] * sceneAuto * LIVE_STEM_RENDER_BOOST));
                        outR[i] = softLimit((float) (outR[i] * sceneAuto * LIVE_STEM_RENDER_BOOST));
                    }'''
if old not in s:
    raise SystemExit('patch anchor not found: independent stem scene processing')
s = s.replace(old, new, 1)

anchor = '    private static final double LIVE_STEM_RENDER_BOOST = 1.55;\n'
if anchor not in s:
    raise SystemExit('patch anchor not found: helper insertion')

helpers = r'''
    private static double blockRms(float[] left, float[] right, int n) {
        if (n <= 0) return 0.0;
        double sum = 0.0;
        // Every second sample is enough for a robust local loudness gate and
        // halves arithmetic cost on mobile while retaining ~23 kHz sampling.
        int count = 0;
        for (int i = 0; i < n; i += 2) {
            double l = left[i], r = right[i];
            sum += 0.5 * (l * l + r * r);
            count++;
        }
        return Math.sqrt(sum / Math.max(1, count));
    }

    /**
     * Silence-gated focus mixer.
     *
     * The desired scene can change immediately when song structure/focus changes,
     * but the *committed* state for each stem changes only after two consecutive
     * quiet 2048-frame blocks (~93 ms at 44.1 kHz). This hides large gain/room
     * changes in natural holes between notes/phrases instead of crossfading them
     * audibly through sustained content.
     */
    private static final class SilenceSceneMixer {
        private static final String[] MUTE_ORDER = {"other", "vocals", "drums"};

        private static final class State {
            double loudRef = 1e-5;
            int quietBlocks = 0;
            double committed = -1.0;
            double desired = 0.55;
            long lastSceneSlot = Long.MIN_VALUE;
        }

        private final Map<String, State> states = new java.util.LinkedHashMap<>();
        private String mutedStem = null;
        private double muteRestoreAfter = Double.POSITIVE_INFINITY;
        private long muteWindow = Long.MIN_VALUE;
        private String muteCandidate = null;
        private boolean muteArmed = false;

        SilenceSceneMixer() {
            for (String stem : Choreography.STEMS) states.put(stem, new State());
        }

        double update(String stem, Choreography.Event event, double t, double rms) {
            State st = states.get(stem);
            if (st == null) return 1.0;

            // Slowly decaying reference loudness makes the gate relative to each
            // stem, so a quiet piano/vocal stem is not judged using drum levels.
            st.loudRef = Math.max(rms, st.loudRef * 0.9975);
            double quietThreshold = Math.max(1e-5, st.loudRef * 0.075);
            boolean quietNow = rms <= quietThreshold;
            st.quietBlocks = quietNow ? Math.min(8, st.quietBlocks + 1) : 0;
            boolean gateOpen = st.quietBlocks >= 2;

            String focus = event == null ? null : event.focusStem;
            String role = event == null || event.role == null ? "groove" : event.role;
            long sceneSlot = (long) Math.floor(t / 5.75);
            if (sceneSlot != st.lastSceneSlot || st.committed < 0.0) {
                st.lastSceneSlot = sceneSlot;
                st.desired = desiredLevel(stem, focus, role, sceneSlot);
            }

            updateRareMutePlan(event, t);

            // A rare mute starts only inside this stem's own silence. Exactly one
            // stem can be muted at a time; bass is intentionally excluded.
            if (mutedStem == null && muteArmed && stem.equals(muteCandidate)
                    && !stem.equals(focus) && gateOpen) {
                mutedStem = stem;
                muteArmed = false;
                int seed = Math.abs((stem + ":" + muteWindow).hashCode());
                muteRestoreAfter = t + 3.2 + (seed % 25) / 10.0; // 3.2..5.6 s
                st.desired = 0.0;
            }

            if (stem.equals(mutedStem)) {
                if (t < muteRestoreAfter) {
                    st.desired = 0.0;
                } else {
                    // Restoration also waits for silence, so its next audible note
                    // simply appears with the new background/focus level.
                    st.desired = desiredLevel(stem, focus, role, sceneSlot);
                    if (gateOpen) {
                        st.committed = st.desired;
                        mutedStem = null;
                        muteRestoreAfter = Double.POSITIVE_INFINITY;
                    }
                }
            }

            // Initial state can be set at time zero; afterwards, large scene changes
            // latch only into a local quiet pocket.
            if (st.committed < 0.0) {
                st.committed = st.desired;
            } else if (gateOpen && Math.abs(st.desired - st.committed) > 0.015) {
                st.committed = st.desired;
            }

            return Choreography.clamp(st.committed, 0.0, 1.0);
        }

        private void updateRareMutePlan(Choreography.Event event, double t) {
            long window = (long) Math.floor(t / 26.0);
            if (window == muteWindow) return;
            muteWindow = window;
            muteCandidate = null;
            muteArmed = false;
            if (window <= 0 || mutedStem != null) return;

            String focus = event == null ? null : event.focusStem;
            String role = event == null || event.role == null ? "groove" : event.role;
            int seed = Math.abs(("orbit8d-silence-mute:" + window).hashCode());

            // Roughly two out of three 26-second windows contain no full mute at all.
            // When one is armed, choose only one non-bass support stem.
            if ((seed % 3) != 0) return;
            for (int i = 0; i < MUTE_ORDER.length; i++) {
                String candidate = MUTE_ORDER[(seed + i) % MUTE_ORDER.length];
                if (candidate.equals(focus)) continue;
                if ("drop".equals(role) && "drums".equals(candidate)) continue;
                muteCandidate = candidate;
                muteArmed = true;
                return;
            }
        }

        private static double desiredLevel(String stem, String focus, String role, long slot) {
            if (stem.equals(focus)) return 1.0; // only the moving/focus stem owns full presence

            double base;
            switch (role) {
                case "drop":
                    base = "drums".equals(stem) ? 0.70 : "bass".equals(stem) ? 0.66
                            : "vocals".equals(stem) ? 0.49 : 0.41;
                    break;
                case "break":
                    base = "vocals".equals(stem) ? 0.65 : "other".equals(stem) ? 0.58
                            : "drums".equals(stem) ? 0.44 : 0.40;
                    break;
                case "build":
                    base = "drums".equals(stem) ? 0.59 : "bass".equals(stem) ? 0.52
                            : "vocals".equals(stem) ? 0.55 : 0.47;
                    break;
                default:
                    base = "drums".equals(stem) ? 0.64 : "bass".equals(stem) ? 0.58
                            : "vocals".equals(stem) ? 0.54 : 0.49;
                    break;
            }

            // Tiny deterministic scene-to-scene variation avoids every background
            // returning to the exact same level while keeping it clearly < focus.
            int h = Math.abs((stem + ":" + slot).hashCode());
            double variation = ((h % 17) - 8) * 0.006; // +/- 4.8 percentage points
            return Choreography.clamp(base + variation, 0.34, 0.76);
        }
    }

'''
s = s.replace(anchor, anchor + helpers, 1)

p.write_text(s)
print('Orbit8D v0.3.4 patch: silence-gated focus/background scene mixer applied')
