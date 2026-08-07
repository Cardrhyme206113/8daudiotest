from pathlib import Path

JAVA = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app')

# -----------------------------------------------------------------------------
# UI: 8D intensity becomes a real 0-300% control. 100% keeps the old tuning;
# 101-300% is intentionally an exaggerated spatial "overdrive" range.
# -----------------------------------------------------------------------------
p = JAVA / 'MainActivity.java'
s = p.read_text()
anchor = '''        setupStemGainControls();
        setupProviderSpinner();'''
replacement = '''        setupStemGainControls();
        // 100% is the previous full-strength tuning. 101-300% deliberately
        // exaggerates binaural/depth/elevation cues for an "8D overdrive" mode.
        intensity.setMax(300);
        setupProviderSpinner();'''
if anchor not in s:
    raise SystemExit('patch anchor not found: 300% intensity UI')
s = s.replace(anchor, replacement, 1)
p.write_text(s)

# -----------------------------------------------------------------------------
# Choreography: let intensity exceed 1.0 and make the featured stem travel still
# farther at >100%, while preserving the exact <=100% behaviour.
# -----------------------------------------------------------------------------
p = JAVA / 'Choreography.java'
s = p.read_text()
old = 'clamp(settings.intensity / 100.0, 0.0, 1.0)'
count = s.count(old)
if count < 2:
    raise SystemExit(f'expected multiple choreography intensity clamps, found {count}')
s = s.replace(old, 'clamp(settings.intensity / 100.0, 0.0, 3.0)')

old = '''                double farTarget = clamp(baseDistance + tripleThrow, 0.74, 1.25);'''
new = '''                // Above 100%, let the focus stem move visibly/audibly beyond the
                // old room shell. 100% remains capped at 1.25; 300% reaches 1.55.
                double focusDistanceCeiling = 1.25 + 0.15 * Math.max(0.0, intensity - 1.0);
                double farTarget = clamp(baseDistance + tripleThrow, 0.74, focusDistanceCeiling);'''
if old not in s:
    raise SystemExit('patch anchor not found: focus distance overdrive')
s = s.replace(old, new, 1)
p.write_text(s)

# -----------------------------------------------------------------------------
# Renderer: carry 0-300% through the binaural DSP instead of clipping at 100%.
# Keep >1 room distances meaningful, add a safe direct-sound floor, and allow
# stronger late-room feedback only in the >100% overdrive range.
# -----------------------------------------------------------------------------
p = JAVA / 'SpatialRenderer.java'
s = p.read_text()
old = 'Choreography.clamp(settings.intensity / 100.0, 0.0, 1.0)'
count = s.count(old)
if count < 1:
    raise SystemExit('patch anchor not found: renderer settings intensity clamp')
s = s.replace(old, 'Choreography.clamp(settings.intensity / 100.0, 0.0, 3.0)')

old = '            intensity = Choreography.clamp(intensity, 0.0, 1.0);'
new = '            intensity = Choreography.clamp(intensity, 0.0, 3.0);'
if old not in s:
    raise SystemExit('patch anchor not found: panner intensity overdrive clamp')
s = s.replace(old, new, 1)

old = '            distance = Choreography.clamp(distance, 0.0, 1.25); // focus stem may travel beyond the visual room shell'
new = '''            // Focus choreography can intentionally move outside the old visual room.
            distance = Choreography.clamp(distance, 0.0, 1.55);'''
if old not in s:
    raise SystemExit('patch anchor not found: panner distance overdrive clamp')
s = s.replace(old, new, 1)

old = '''            double elev = Choreography.clamp(elevationDeg / 48.0, -1.0, 1.0);'''
new = '''            // Overdrive also exaggerates the spectral elevation cue. Keep this
            // bounded so 300% stays dramatic without turning into an unstable EQ.
            double elevOverdrive = 1.0 + 0.16 * Math.max(0.0, intensity - 1.0);
            double elev = Choreography.clamp((elevationDeg / 48.0) * elevOverdrive, -1.30, 1.30);'''
if old not in s:
    raise SystemExit('patch anchor not found: elevation overdrive')
s = s.replace(old, new, 1)

old = '''            double directGain = 1.04 - distance * (0.40 + 0.10 * intensity);
            directGain += side * (1.0 - distance) * 0.06 * intensity;'''
new = '''            double directGain = 1.04 - distance * (0.40 + 0.10 * intensity);
            directGain += side * Math.max(0.0, 1.0 - distance) * 0.06 * intensity;
            // Deep 200-300% excursions may be >1 room radius; never invert phase.
            directGain = Math.max(0.12, directGain);'''
if old not in s:
    raise SystemExit('patch anchor not found: safe deep direct gain')
s = s.replace(old, new, 1)

old = '''            double feedback = Choreography.clamp(0.08 + room * 0.34 + distance * 0.10, 0.08, 0.34);'''
new = '''            // Preserve the old 0.34 ceiling at 100%; progressively permit a longer,
            // denser tail up to 0.46 feedback at 300%.
            double feedbackCeiling = 0.34 + 0.06 * Math.max(0.0, intensity - 1.0);
            double feedback = Choreography.clamp(0.08 + room * 0.34 + distance * 0.10,
                    0.08, feedbackCeiling);'''
if old not in s:
    raise SystemExit('patch anchor not found: room feedback overdrive')
s = s.replace(old, new, 1)

# Distinct-stem v0.3.2 raised the normal room-send ceiling to 0.72. Preserve that
# at 100%, then allow a progressively wetter send up to 0.91 at 300%.
old = '''        return Choreography.clamp(base, 0.012, 0.72);'''
new = '''        double roomCeiling = 0.72 + 0.095 * Math.max(0.0, intensity - 1.0);
        return Choreography.clamp(base, 0.012, roomCeiling);'''
if old not in s:
    raise SystemExit('patch anchor not found: room send ceiling')
s = s.replace(old, new, 1)
p.write_text(s)

print('Orbit8D v0.3.6 patch: 0-300% 8D intensity overdrive applied')
