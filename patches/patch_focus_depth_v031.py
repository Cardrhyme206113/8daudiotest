from pathlib import Path

JAVA = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app')


def replace_once(text, old, new, name):
    if old not in text:
        raise SystemExit(f'patch anchor not found: {name}')
    return text.replace(old, new, 1)

# Only the currently featured/moving stem gets the exaggerated depth throw.
# The other stems keep the exact depth behavior produced by patch_spatial_v03.py.
p = JAVA / 'Choreography.java'
s = p.read_text()
s = replace_once(
    s,
    '''            out.distance = clamp(d * (0.76 + 0.24 * intensity), 0.02, 0.95);\n            return out;''',
    '''            double normalDistance = clamp(d * (0.76 + 0.24 * intensity), 0.02, 0.95);\n            if (focus) {\n                // Make the featured stem leave the normal orbit shell very clearly.\n                // We preserve the old algorithm as the reference, then exaggerate\n                // its depth delta by ~3x with a strong minimum throw so even sections\n                // that previously kept the focus stem near the listener now separate.\n                double oldDelta = Math.abs(normalDistance - baseDistance);\n                double minimumThrow = 0.62 + 0.18 * intensity;\n                double tripleThrow = Math.max(oldDelta * 3.0, minimumThrow);\n                double farTarget = clamp(baseDistance + tripleThrow, 0.74, 1.25);\n\n                // Get it outward quickly, hold it there for the actual movement, then\n                // return it to the expected/base distance during approximately the\n                // final second of the section instead of drifting home too early.\n                double attack = smooth(clamp(u / 0.17, 0.0, 1.0));\n                double releaseFrac = clamp(1.0 / e.duration(), 0.055, 0.22);\n                double releaseStart = 1.0 - releaseFrac;\n                double release = 1.0 - smooth(clamp((u - releaseStart) / releaseFrac, 0.0, 1.0));\n                double excursion = attack * release;\n                out.distance = lerp(baseDistance, farTarget, excursion);\n            } else {\n                out.distance = normalDistance;\n            }\n            return out;''',
    'focus depth overshoot')
p.write_text(s)

# Let the acoustic renderer use the overshoot value (> 1.0) instead of clipping it
# back to the old room boundary. Existing distance formulas naturally become much
# wetter/darker/quieter at the far end, which makes the motion substantially easier
# to distinguish without changing the other stems.
p = JAVA / 'SpatialRenderer.java'
s = p.read_text()
s = replace_once(
    s,
    '''            distance = Choreography.clamp(distance, 0.0, 1.0);''',
    '''            distance = Choreography.clamp(distance, 0.0, 1.25); // focus stem may travel beyond the visual room shell''',
    'renderer focus distance ceiling')
p.write_text(s)

# OrbitView already maps radius directly from distance with no upper clamp, so a
# focus distance around 1.1-1.25 naturally appears slightly outside the outer ring.
print('Applied focus depth excursion: ~3x throw, outside-ring visualization, final-second return.')
