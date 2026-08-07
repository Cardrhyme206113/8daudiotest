from pathlib import Path

JAVA = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app')


def replace_once(text, old, new, name):
    if old not in text:
        raise SystemExit(f'patch anchor not found: {name}')
    return text.replace(old, new, 1)

# Only the currently featured/moving stem gets the exaggerated depth throw.
# The other stems keep the normal depth choreography produced by patch_spatial_v03.py.
p = JAVA / 'Choreography.java'
s = p.read_text()
s = replace_once(
    s,
    '''            out.distance = clamp(d * (0.76 + 0.24 * intensity), 0.02, 0.95);\n            return out;''',
    '''            double normalDistance = clamp(d * (0.76 + 0.24 * intensity), 0.02, 0.95);\n            if (focus) {\n                // Make the featured stem leave the normal orbit shell very clearly.\n                // Preserve the old algorithm as reference, then exaggerate the depth\n                // delta by ~3x with a minimum throw so even normally-near sections read.\n                double oldDelta = Math.abs(normalDistance - baseDistance);\n                double minimumThrow = 0.62 + 0.18 * intensity;\n                double tripleThrow = Math.max(oldDelta * 3.0, minimumThrow);\n                double farTarget = clamp(baseDistance + tripleThrow, 0.74, 1.25);\n\n                // Get outward quickly, stay there during the motion, and return to the\n                // expected/base distance during approximately the final second.\n                double attack = smooth(clamp(u / 0.17, 0.0, 1.0));\n                double releaseFrac = clamp(1.0 / e.duration(), 0.055, 0.22);\n                double releaseStart = 1.0 - releaseFrac;\n                double release = 1.0 - smooth(clamp((u - releaseStart) / releaseFrac, 0.0, 1.0));\n                double excursion = attack * release;\n                out.distance = lerp(baseDistance, farTarget, excursion);\n            } else {\n                out.distance = normalDistance;\n            }\n            return out;''',
    'focus depth overshoot')
p.write_text(s)

# Let the acoustic renderer use the overshoot (>1.0) rather than clipping it back
# to the old virtual-room boundary.
p = JAVA / 'SpatialRenderer.java'
s = p.read_text()
s = replace_once(
    s,
    '''            distance = Choreography.clamp(distance, 0.0, 1.0);''',
    '''            distance = Choreography.clamp(distance, 0.0, 1.25); // focus stem may travel beyond the visual room shell''',
    'renderer focus distance ceiling')
p.write_text(s)

print('Applied focus depth excursion: ~3x throw, outside-ring visualization, final-second return.')

# v0.3.2 extends this patch with distinct stem identities and realtime manual gains.
extension = Path('patches/patch_distinct_realtime_v032.py')
if extension.exists():
    exec(compile(extension.read_text(), str(extension), 'exec'), {'__name__': '__main__'})
