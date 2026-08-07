from pathlib import Path
import re

ROOT = Path('Orbit8D-Android/app/src/main')
JAVA = ROOT / 'java/com/orbit8d/app'


def replace_once(text, old, new, name):
    if old not in text:
        raise SystemExit(f'patch anchor not found: {name}')
    return text.replace(old, new, 1)

# -----------------------------------------------------------------------------
# Choreography: add manual stem gains + true 3-axis choreography metadata.
# -----------------------------------------------------------------------------
p = JAVA / 'Choreography.java'
s = p.read_text()
s = replace_once(s,
'''    public static final class Settings {
        public int intensity = 85;
        public int drift = 25;
        public int focus = 90;
        public int dj = 84;
    }''',
'''    public static final class Settings {
        public int intensity = 92;
        public int drift = 25;
        public int focus = 92;
        public int dj = 84;
        // Manual overrides are multiplicative on top of the algorithmic gain rides.
        public int drumsGain = 100;
        public int bassGain = 100;
        public int otherGain = 100;
        public int vocalsGain = 100;

        public int manualGainPercent(String stem) {
            if ("drums".equals(stem)) return drumsGain;
            if ("bass".equals(stem)) return bassGain;
            if ("other".equals(stem)) return otherGain;
            if ("vocals".equals(stem)) return vocalsGain;
            return 100;
        }
    }''', 'settings')

s = replace_once(s,
'''    public static final class Position {
        public double azimuthDeg;
        public double baseAzimuthDeg;
        public double focusExtraDeg;
        public boolean focus;
        public boolean swapping;
        public Event event;
    }''',
'''    public static final class Position {
        public double azimuthDeg;
        public double baseAzimuthDeg;
        public double focusExtraDeg;
        // Positive elevation is above the listener, negative is below.
        public double elevationDeg;
        // 0 = intimate/near, 1 = deep in the virtual room.
        public double distance;
        public boolean focus;
        public boolean swapping;
        public Event event;
    }''', 'position fields')

s = replace_once(s,
'''            if (e == null) {
                out.azimuthDeg = anchors.getOrDefault(stem, 0.0);
                out.baseAzimuthDeg = out.azimuthDeg;
                return out;
            }''',
'''            if (e == null) {
                out.azimuthDeg = anchors.getOrDefault(stem, 0.0);
                out.baseAzimuthDeg = out.azimuthDeg;
                out.elevationDeg = baseElevation(stem, t, settings);
                out.distance = baseDistance(stem, t, settings);
                return out;
            }''', 'null event 3d pose')

s = replace_once(s,
'''            out.azimuthDeg = out.baseAzimuthDeg + extra;
            out.focus = focus;
            out.swapping = swapping;
            return out;''',
'''            out.azimuthDeg = out.baseAzimuthDeg + extra;
            out.focus = focus;
            out.swapping = swapping;

            // Slow vertical movement is intentionally much slower than azimuth. It
            // gives the pinna/elevation renderer time to become perceptually obvious.
            double intensity = clamp(settings.intensity / 100.0, 0.0, 1.0);
            double elev = baseElevation(stem, t, settings);
            double focusArc = Math.sin(Math.PI * u);
            int seed = Math.abs((e.sectionLabel + stem + e.index).hashCode());
            double verticalDirection = (seed & 1) == 0 ? 1.0 : -1.0;
            if (focus) elev += verticalDirection * (14.0 + 22.0 * intensity) * focusArc;
            if ("bass".equals(stem)) elev *= 0.42; // keep sub movement grounded
            out.elevationDeg = clamp(elev, -48.0, 48.0);

            // Near/far movement is section-shaped, deterministic and smooth. Most
            // sections return to their starting depth so there are no random jumps.
            double baseDistance = baseDistance(stem, t, settings);
            double sectionArc = Math.sin(Math.PI * u);
            sectionArc *= sectionArc;
            double d = baseDistance;
            switch (e.role) {
                case "build":
                    if (focus) d = baseDistance + (0.64 - baseDistance) * sectionArc;
                    else if ("other".equals(stem) || "vocals".equals(stem)) d += 0.34 * sectionArc;
                    else d += 0.14 * sectionArc;
                    break;
                case "drop":
                    if ("bass".equals(stem)) d = 0.045 + 0.035 * sectionArc;
                    else if ("drums".equals(stem)) d = 0.065 + 0.07 * sectionArc;
                    else if (focus) d = 0.08 + 0.12 * sectionArc;
                    else d += 0.24 * sectionArc;
                    break;
                case "break":
                    if (focus || "vocals".equals(stem)) d = 0.07 + 0.15 * sectionArc;
                    else if ("bass".equals(stem)) d = 0.10 + 0.08 * sectionArc;
                    else d = 0.48 + 0.27 * sectionArc;
                    break;
                default:
                    if (focus) d += 0.20 * sectionArc;
                    else if ("other".equals(stem)) d += 0.40 * sectionArc;
                    else if ("vocals".equals(stem)) d += 0.26 * sectionArc;
                    else if ("drums".equals(stem)) d += 0.16 * sectionArc;
                    else d += 0.06 * sectionArc;
                    break;
            }
            out.distance = clamp(d * (0.76 + 0.24 * intensity), 0.02, 0.95);
            return out;''', '3d position output')

# Add helper methods before focusEnvelope.
anchor = '    public static double focusEnvelope(Event e, double t) {'
if anchor not in s:
    raise SystemExit('patch anchor not found: choreography helpers')
helpers = '''    private static double stemPhase(String stem) {
        if ("vocals".equals(stem)) return 0.15;
        if ("drums".equals(stem)) return 1.75;
        if ("other".equals(stem)) return 3.35;
        if ("bass".equals(stem)) return 4.85;
        return 0.0;
    }

    private static double baseElevation(String stem, double t, Settings settings) {
        double intensity = clamp(settings.intensity / 100.0, 0.0, 1.0);
        double amp = "other".equals(stem) ? 27.0 : "vocals".equals(stem) ? 23.0
                : "drums".equals(stem) ? 15.0 : 8.0;
        double period = "other".equals(stem) ? 23.0 : "vocals".equals(stem) ? 27.0
                : "drums".equals(stem) ? 20.0 : 31.0;
        return amp * (0.35 + 0.65 * intensity)
                * Math.sin(2.0 * Math.PI * t / period + stemPhase(stem));
    }

    private static double baseDistance(String stem, double t, Settings settings) {
        double intensity = clamp(settings.intensity / 100.0, 0.0, 1.0);
        double center = "other".equals(stem) ? 0.25 : "vocals".equals(stem) ? 0.16
                : "drums".equals(stem) ? 0.12 : 0.06;
        double amp = "other".equals(stem) ? 0.16 : "vocals".equals(stem) ? 0.11
                : "drums".equals(stem) ? 0.08 : 0.035;
        double period = "other".equals(stem) ? 34.0 : "vocals".equals(stem) ? 38.0
                : "drums".equals(stem) ? 29.0 : 42.0;
        double wave = 0.5 + 0.5 * Math.sin(2.0 * Math.PI * t / period + stemPhase(stem) * 0.73);
        return clamp(center + amp * wave * (0.45 + 0.55 * intensity), 0.02, 0.72);
    }

'''
s = s.replace(anchor, helpers + anchor, 1)
p.write_text(s)

# -----------------------------------------------------------------------------
# Spatial renderer: near/far clarity, elevation cues and proper late reverb.
# -----------------------------------------------------------------------------
p = JAVA / 'SpatialRenderer.java'
s = p.read_text()
s = replace_once(s,
'''                        double stemGain = stemGain(stream.name, event, settings, focusEnv);
                        double intensity = Choreography.clamp(settings.intensity / 100.0, 0.0, 1.0);
                        double room = roomAmount(stream.name, event, settings, focusEnv);

                        for (int i = 0; i < got; i++) {
                            double u = got <= 1 ? 0.0 : i / (double) (got - 1);
                            double az = Choreography.lerp(p0.azimuthDeg, p1.azimuthDeg, u);
                            stream.panner.process(
                                    stream.left[i], stream.right[i], az,
                                    intensity, isFocus ? focusEnv : 0.0, room,
                                    outL, outR, i);''',
'''                        double stemGain = stemGain(stream.name, event, settings, focusEnv);
                        double intensity = Choreography.clamp(settings.intensity / 100.0, 0.0, 1.0);

                        for (int i = 0; i < got; i++) {
                            double u = got <= 1 ? 0.0 : i / (double) (got - 1);
                            double az = Choreography.lerp(p0.azimuthDeg, p1.azimuthDeg, u);
                            double elevation = Choreography.lerp(p0.elevationDeg, p1.elevationDeg, u);
                            double distance = Choreography.lerp(p0.distance, p1.distance, u);
                            double room = roomAmount(stream.name, event, settings, focusEnv, distance, az);
                            stream.panner.process(
                                    stream.left[i], stream.right[i], az, elevation, distance,
                                    intensity, isFocus ? focusEnv : 0.0, room,
                                    outL, outR, i);''', 'renderer 3d process args')

s = replace_once(s,
'''        return Choreography.dbToGain(db);''',
'''        double autoGain = Choreography.dbToGain(db);
        double manual = Choreography.clamp(settings.manualGainPercent(stem) / 100.0, 0.0, 2.0);
        return autoGain * manual;''', 'manual gain layering')

room_pattern = re.compile(r'''    private static double roomAmount\(String stem, Choreography.Event event, Choreography.Settings settings, double focusEnv\) \{.*?\n    \}\n\n    private static float softLimit''', re.S)
room_replacement = '''    private static double roomAmount(String stem, Choreography.Event event,
                                     Choreography.Settings settings, double focusEnv,
                                     double distance, double azimuthDeg) {
        double intensity = Choreography.clamp(settings.intensity / 100.0, 0.0, 1.0);
        double side = Math.abs(Math.sin(Math.toRadians(azimuthDeg)));
        double base = 0.035 + 0.055 * intensity;
        // Distance is the main room cue. Far sources become substantially wetter.
        base += distance * (0.23 + 0.24 * intensity);
        if (event != null && stem.equals(event.focusStem)) {
            base += (event.role.equals("break") ? 0.08 : 0.045) * focusEnv;
        }
        // At hard L/R, preserve direct clarity so positional motion is obvious.
        base *= 1.0 - side * (0.10 + 0.12 * (1.0 - distance));
        if ("bass".equals(stem)) base *= 0.52;
        return Choreography.clamp(base, 0.015, 0.56);
    }

    private static float softLimit'''
s, n = room_pattern.subn(room_replacement, s, count=1)
if n != 1:
    raise SystemExit('patch anchor not found: roomAmount')

# Replace the full panner implementation. This remains lightweight enough for offline
# mobile rendering while adding clear vertical, depth, and late-room cues.
start = s.find('    private static final class BinauralPanner {')
if start < 0:
    raise SystemExit('patch anchor not found: BinauralPanner')
prefix = s[:start]
new_panner = r'''    /**
     * Lightweight 3D binaural renderer. Azimuth uses ITD/ILD; elevation uses a
     * slow pinna-like spectral tilt plus asymmetric early reflections; distance
     * controls direct level, air absorption, pre-delay and a damped feedback room.
     */
    private static final class BinauralPanner {
        private final int sampleRate;
        private final float[] delayRing;
        private final float[] roomRing;
        private int delayWrite = 0;
        private int roomWrite = 0;
        private double lpL = 0.0, lpR = 0.0;
        private double elevLpL = 0.0, elevLpR = 0.0;
        private double verbLpL = 0.0, verbLpR = 0.0;
        private final double maxItdSamples;

        BinauralPanner(int sampleRate) {
            this.sampleRate = sampleRate;
            this.maxItdSamples = sampleRate * 0.00072; // strong but plausible head-width ITD
            this.delayRing = new float[160];
            this.roomRing = new float[Math.max(12288, (int) (sampleRate * 0.24))];
        }

        void process(
                float nativeL, float nativeR, double azimuthDeg, double elevationDeg, double distance,
                double intensity, double focus, double room,
                float[] outL, float[] outR, int index) {
            intensity = Choreography.clamp(intensity, 0.0, 1.0);
            distance = Choreography.clamp(distance, 0.0, 1.0);
            double rad = Math.toRadians(azimuthDeg);
            double pan = Math.sin(rad); // -1 left, +1 right
            double absPan = Math.abs(pan);
            double side = absPan;
            double rear = (1.0 - Math.cos(rad)) * 0.5; // 0 front, 1 back
            double elev = Choreography.clamp(elevationDeg / 48.0, -1.0, 1.0);
            float mono = (nativeL + nativeR) * 0.5f;

            // ITD: the far ear receives a sub-millisecond delay, increased slightly at
            // high intensity so hard-side passages remain unmistakably lateral.
            delayRing[delayWrite] = mono;
            double farDelay = maxItdSamples * absPan * (0.62 + 0.38 * intensity);
            float delayed = readFractional(delayRing, delayWrite, farDelay);
            delayWrite = (delayWrite + 1) % delayRing.length;

            float earL = pan > 0 ? delayed : mono;
            float earR = pan < 0 ? delayed : mono;
            double leftGain = Math.sqrt((1.0 - pan) * 0.5);
            double rightGain = Math.sqrt((1.0 + pan) * 0.5);
            // Stronger far-ear shadow than the old renderer. Keep a small floor so
            // side images remain external rather than collapsing into one ear.
            double shadow = 1.0 - (0.34 + 0.10 * intensity) * absPan;
            if (pan > 0) leftGain = Math.max(0.055, leftGain * shadow);
            else if (pan < 0) rightGain = Math.max(0.055, rightGain * shadow);

            double l = earL * leftGain;
            double r = earR * rightGain;

            // Distance and rear position darken the direct sound. Side-facing close
            // sources get a little clarity back, which makes left/right motion crisp.
            double cutoff = 19500.0
                    - rear * intensity * 11500.0
                    - distance * (6500.0 + 4200.0 * intensity)
                    + side * (1.0 - distance) * 2100.0 * intensity;
            cutoff = Choreography.clamp(cutoff, 3800.0, 20000.0);
            double alpha = Math.exp(-2.0 * Math.PI * cutoff / sampleRate);
            lpL = (1.0 - alpha) * l + alpha * lpL;
            lpR = (1.0 - alpha) * r + alpha * lpR;
            l = lpL;
            r = lpR;

            // Pseudo-pinna elevation cue. Above adds a small presence ridge; below
            // suppresses it. This is intentionally moderate because elevation HRTFs
            // vary by listener, but the slow movement is clearly audible on headphones.
            double elevCut = 3600.0;
            double elevAlpha = Math.exp(-2.0 * Math.PI * elevCut / sampleRate);
            elevLpL = (1.0 - elevAlpha) * l + elevAlpha * elevLpL;
            elevLpR = (1.0 - elevAlpha) * r + elevAlpha * elevLpR;
            double highL = l - elevLpL;
            double highR = r - elevLpR;
            double elevPresence = elev >= 0 ? (0.18 * elev) : (0.13 * elev);
            l += highL * elevPresence * intensity;
            r += highR * elevPresence * intensity;

            // Direct-to-reverberant ratio is one of the strongest distance cues.
            double directGain = 1.04 - distance * (0.40 + 0.10 * intensity);
            directGain += side * (1.0 - distance) * 0.06 * intensity;
            l *= directGain;
            r *= directGain;

            // Keep very little unspatialized stereo; less still when the source is far.
            double nativeKeep = (0.12 * (1.0 - intensity) + 0.022) * (1.0 - 0.62 * distance);
            l += nativeL * nativeKeep;
            r += nativeR * nativeKeep;

            // Damped feedback room. Four decorrelated taps create an audible late tail
            // without an expensive convolution reverb. Elevation changes the early
            // reflection balance, helping above/below positions separate perceptually.
            int tap17 = tap(0.017);
            int tap31 = tap(0.031);
            int tap53 = tap(0.053);
            int tap79 = tap(0.079);
            int tap113 = tap(0.113);
            int tap149 = tap(0.149);
            double a = roomRing[tap17], b = roomRing[tap31];
            double c = roomRing[tap53], d = roomRing[tap79];
            double e = roomRing[tap113], f = roomRing[tap149];

            double feedback = Choreography.clamp(0.08 + room * 0.34 + distance * 0.10, 0.08, 0.34);
            double fb = (c * 0.34 + d * 0.29 + e * 0.22 + f * 0.15) * feedback;
            roomRing[roomWrite] = (float) Choreography.clamp(mono + fb, -1.8, 1.8);

            double up = Math.max(0.0, elev), down = Math.max(0.0, -elev);
            double earlyL = a * (0.52 + 0.12 * up) + b * (0.22 + 0.10 * down);
            double earlyR = a * (0.22 + 0.10 * down) + b * (0.52 + 0.12 * up);
            double lateL = c * 0.28 + d * 0.20 + e * 0.24 + f * 0.16;
            double lateR = c * 0.17 + d * 0.29 + e * 0.16 + f * 0.25;

            // Air/damping in the room. Farther sources have a darker, denser tail.
            double verbCut = 9200.0 - distance * 4700.0 - rear * 1600.0;
            double verbAlpha = Math.exp(-2.0 * Math.PI * Choreography.clamp(verbCut, 3200.0, 10500.0) / sampleRate);
            verbLpL = (1.0 - verbAlpha) * (earlyL + lateL) + verbAlpha * verbLpL;
            verbLpR = (1.0 - verbAlpha) * (earlyR + lateR) + verbAlpha * verbLpR;

            // At ±90° reduce diffuse energy slightly, preserving side clarity.
            double sideClarity = 1.0 - side * (0.10 + 0.18 * (1.0 - distance));
            double wet = room * (0.72 + distance * 0.88 + rear * 0.18 + focus * 0.08) * sideClarity;
            wet = Choreography.clamp(wet, 0.0, 0.82);
            l += verbLpL * wet;
            r += verbLpR * wet;
            roomWrite = (roomWrite + 1) % roomRing.length;

            outL[index] = (float) l;
            outR[index] = (float) r;
        }

        private int tap(double seconds) {
            int offset = Math.max(1, (int) Math.round(sampleRate * seconds));
            int idx = roomWrite - offset;
            while (idx < 0) idx += roomRing.length;
            return idx % roomRing.length;
        }

        private static float readFractional(float[] ring, int writeIndex, double delay) {
            double pos = writeIndex - delay;
            while (pos < 0) pos += ring.length;
            int i0 = (int) Math.floor(pos) % ring.length;
            int i1 = (i0 + 1) % ring.length;
            double f = pos - Math.floor(pos);
            return (float) (ring[i0] + (ring[i1] - ring[i0]) * f);
        }
    }
}'''
s = prefix + new_panner + '\n'
p.write_text(s)

# -----------------------------------------------------------------------------
# MainActivity: programmatic per-stem volume controls, persistent across updates.
# -----------------------------------------------------------------------------
p = JAVA / 'MainActivity.java'
s = p.read_text()
s = replace_once(s,
'''    private TextView intensityLabel, driftLabel, focusLabel, djLabel;
    private ProgressBar progress;
    private SeekBar intensity, drift, focus, dj, seek;''',
'''    private TextView intensityLabel, driftLabel, focusLabel, djLabel;
    private TextView drumsGainLabel, bassGainLabel, otherGainLabel, vocalsGainLabel;
    private ProgressBar progress;
    private SeekBar intensity, drift, focus, dj, seek;
    private SeekBar drumsGain, bassGain, otherGain, vocalsGain;''', 'main fields')

s = replace_once(s,
'''        bindViews();
        importModel.setVisibility(View.GONE);
        setupProviderSpinner();''',
'''        bindViews();
        importModel.setVisibility(View.GONE);
        setupStemGainControls();
        setupProviderSpinner();''', 'setup gain controls')

# Attach manual sliders to the existing common listener.
s = replace_once(s,
'''        dj.setOnSeekBarChangeListener(listener);
        updateSliderLabels();''',
'''        dj.setOnSeekBarChangeListener(listener);
        drumsGain.setOnSeekBarChangeListener(listener);
        bassGain.setOnSeekBarChangeListener(listener);
        otherGain.setOnSeekBarChangeListener(listener);
        vocalsGain.setOnSeekBarChangeListener(listener);
        updateSliderLabels();''', 'gain listeners')

s = replace_once(s,
'''            @Override public void onProgressChanged(SeekBar seekBar, int value, boolean fromUser) {
                updateSliderLabels();
                orbitView.setSettings(readSettings());
            }''',
'''            @Override public void onProgressChanged(SeekBar seekBar, int value, boolean fromUser) {
                updateSliderLabels();
                orbitView.setSettings(readSettings());
                if (fromUser && (seekBar == drumsGain || seekBar == bassGain || seekBar == otherGain || seekBar == vocalsGain)) {
                    saveStemGains();
                }
            }''', 'persist gain listener')

s = replace_once(s,
'''        djLabel.setText("DJ stem automation · " + dj.getProgress() + "%");
    }''',
'''        djLabel.setText("DJ stem automation · " + dj.getProgress() + "%");
        if (drumsGainLabel != null) drumsGainLabel.setText("Drums manual · " + drumsGain.getProgress() + "% × AUTO");
        if (bassGainLabel != null) bassGainLabel.setText("Bass manual · " + bassGain.getProgress() + "% × AUTO");
        if (otherGainLabel != null) otherGainLabel.setText("Other manual · " + otherGain.getProgress() + "% × AUTO");
        if (vocalsGainLabel != null) vocalsGainLabel.setText("Vocals manual · " + vocalsGain.getProgress() + "% × AUTO");
    }''', 'gain labels')

s = replace_once(s,
'''        s.dj = dj != null ? dj.getProgress() : 84;
        return s;''',
'''        s.dj = dj != null ? dj.getProgress() : 84;
        s.drumsGain = drumsGain != null ? drumsGain.getProgress() : 100;
        s.bassGain = bassGain != null ? bassGain.getProgress() : 100;
        s.otherGain = otherGain != null ? otherGain.getProgress() : 100;
        s.vocalsGain = vocalsGain != null ? vocalsGain.getProgress() : 100;
        return s;''', 'read manual gains')

# Include elevation/distance in the live readout.
s = replace_once(s,
'''                + " EXTRA LAP " + lap + "% · " + Math.round(((pose.azimuthDeg % 360) + 360) % 360) + "°" + swap);''',
'''                + " EXTRA LAP " + lap + "% · " + Math.round(((pose.azimuthDeg % 360) + 360) % 360) + "°"
                + " · " + (pose.elevationDeg >= 0 ? "↑" : "↓") + Math.round(Math.abs(pose.elevationDeg)) + "°"
                + " · DEPTH " + Math.round(pose.distance * 100) + "%" + swap);''', 'live 3d readout')

# Add helper methods immediately before setupProviderSpinner.
anchor = '    private void setupProviderSpinner() {'
if anchor not in s:
    raise SystemExit('patch anchor not found: MainActivity helper insertion')
helpers = r'''    private void setupStemGainControls() {
        android.widget.LinearLayout parent = (android.widget.LinearLayout) dj.getParent();
        TextView title = new TextView(this);
        title.setText("Manual stem volume overrides");
        title.setTextColor(0xfff5eff8);
        title.setTextSize(14);
        android.widget.LinearLayout.LayoutParams tp = new android.widget.LinearLayout.LayoutParams(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT, android.view.ViewGroup.LayoutParams.WRAP_CONTENT);
        tp.topMargin = (int) (12 * getResources().getDisplayMetrics().density);
        title.setLayoutParams(tp);
        parent.addView(title);

        TextView hint = new TextView(this);
        hint.setText("0–200%. Multiplies the algorithm's boosts/cuts; re-render does not rerun separation.");
        hint.setTextColor(0xffb9aebe);
        hint.setTextSize(11);
        parent.addView(hint);

        android.content.SharedPreferences prefs = getSharedPreferences("orbit8d_mix", MODE_PRIVATE);
        drumsGainLabel = makeGainLabel(parent); drumsGain = makeGainBar(parent, prefs.getInt("gain_drums", 100));
        bassGainLabel = makeGainLabel(parent); bassGain = makeGainBar(parent, prefs.getInt("gain_bass", 100));
        otherGainLabel = makeGainLabel(parent); otherGain = makeGainBar(parent, prefs.getInt("gain_other", 100));
        vocalsGainLabel = makeGainLabel(parent); vocalsGain = makeGainBar(parent, prefs.getInt("gain_vocals", 100));
    }

    private TextView makeGainLabel(android.widget.LinearLayout parent) {
        TextView label = new TextView(this);
        label.setTextColor(0xfff5eff8);
        label.setTextSize(12);
        android.widget.LinearLayout.LayoutParams lp = new android.widget.LinearLayout.LayoutParams(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT, android.view.ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = (int) (5 * getResources().getDisplayMetrics().density);
        label.setLayoutParams(lp);
        parent.addView(label);
        return label;
    }

    private SeekBar makeGainBar(android.widget.LinearLayout parent, int value) {
        SeekBar bar = new SeekBar(this);
        bar.setMax(200);
        bar.setProgress(Math.max(0, Math.min(200, value)));
        bar.setLayoutParams(new android.widget.LinearLayout.LayoutParams(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT, android.view.ViewGroup.LayoutParams.WRAP_CONTENT));
        parent.addView(bar);
        return bar;
    }

    private void saveStemGains() {
        if (drumsGain == null) return;
        getSharedPreferences("orbit8d_mix", MODE_PRIVATE).edit()
                .putInt("gain_drums", drumsGain.getProgress())
                .putInt("gain_bass", bassGain.getProgress())
                .putInt("gain_other", otherGain.getProgress())
                .putInt("gain_vocals", vocalsGain.getProgress())
                .apply();
    }

'''
s = s.replace(anchor, helpers + anchor, 1)
p.write_text(s)

# -----------------------------------------------------------------------------
# OrbitView: radius now represents depth; labels expose elevation and depth.
# -----------------------------------------------------------------------------
p = JAVA / 'OrbitView.java'
s = p.read_text()
s = replace_once(s,
'''            float rr = radius * (info.focus ? 0.96f : 0.83f);''',
'''            float rr = radius * (0.34f + 0.62f * (float) info.distance);''', 'view depth radius')
s = replace_once(s,
'''            canvas.drawCircle(x, y, info.focus ? dp(10) : dp(7), paint);''',
'''            float dot = (info.focus ? 10f : 7f) * (float) (1.18 - 0.35 * info.distance);
            canvas.drawCircle(x, y, dp(dot), paint);''', 'view depth dot')
s = replace_once(s,
'''            if (info.swapping) {
                text.setColor(0xffe6be7a);
                canvas.drawText("SWAP", x, y + dp(25), text);
            } else if (info.focus) {
                text.setColor(0xffc6a7d0);
                canvas.drawText("EXTRA LAP", x, y + dp(25), text);
            }''',
'''            String height = (info.elevation >= 0 ? "↑" : "↓") + Math.round(Math.abs(info.elevation)) + "°";
            String depth = Math.round(info.distance * 100) + "% D";
            text.setColor(info.focus ? 0xffc6a7d0 : 0xffb9aebe);
            canvas.drawText(height + " · " + depth, x, y + dp(25), text);
            if (info.swapping) {
                text.setColor(0xffe6be7a);
                canvas.drawText("SWAP", x, y + dp(38), text);
            } else if (info.focus) {
                text.setColor(0xffc6a7d0);
                canvas.drawText("EXTRA LAP", x, y + dp(38), text);
            }''', 'view 3d label')
# Trail radius should reflect historical distance too.
s = replace_once(s,
'''            float rr = radius * (p.focus ? 0.96f : 0.83f);''',
'''            float rr = radius * (0.34f + 0.62f * (float) p.distance);''', 'trail depth radius')
s = replace_once(s,
'''            out.azimuth = p.azimuthDeg;
            out.focus = p.focus;
            out.swapping = p.swapping;''',
'''            out.azimuth = p.azimuthDeg;
            out.elevation = p.elevationDeg;
            out.distance = p.distance;
            out.focus = p.focus;
            out.swapping = p.swapping;''', 'view position 3d')
s = replace_once(s,
'''        out.azimuth = base + extra;
        out.focus = focus == index;
        return out;''',
'''        out.azimuth = base + extra;
        out.focus = focus == index;
        out.elevation = 24.0 * Math.sin(t / 4.2 + index * 1.3);
        out.distance = Choreography.clamp(0.18 + 0.52 * (0.5 + 0.5 * Math.sin(t / 5.5 + index * 1.7)), 0.05, 0.90);
        return out;''', 'preview 3d')
s = replace_once(s,
'''    private static final class PositionInfo {
        double azimuth;
        boolean focus;
        boolean swapping;
    }''',
'''    private static final class PositionInfo {
        double azimuth;
        double elevation;
        double distance = 0.45;
        boolean focus;
        boolean swapping;
    }''', 'view info fields')
p.write_text(s)

print('Orbit8D spatial v0.3 patch applied')
