from pathlib import Path

p = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app/SpatialRenderer.java')
s = p.read_text()

# v0.3.4's focus/silence patch expects the first scene-aware renderer revision.
# The current realtime renderer starts from the simpler autoGain-only block, so
# normalize it to that intermediate form before patch_silence_scene_v034.py runs.
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

intermediate = '''                    boolean isFocus = event != null && stream.name.equals(event.focusStem);
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

if old in s:
    s = s.replace(old, intermediate, 1)
elif intermediate not in s:
    raise SystemExit('patch anchor not found: prepare silence scene block')

p.write_text(s)
print('Prepared realtime renderer for silence-gated spotlight patch')
