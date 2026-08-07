from pathlib import Path

JAVA = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app')


def replace_once(text, old, new, name):
    if old not in text:
        raise SystemExit(f'patch anchor not found: {name}')
    return text.replace(old, new, 1)

# Only the currently featured/moving stem gets the exaggerated depth throw.
p = JAVA / 'Choreography.java'
s = p.read_text()
s = replace_once(
    s,
    '''            out.distance = clamp(d * (0.76 + 0.24 * intensity), 0.02, 0.95);\n            return out;''',
    '''            double normalDistance = clamp(d * (0.76 + 0.24 * intensity), 0.02, 0.95);\n            if (focus) {\n                double oldDelta = Math.abs(normalDistance - baseDistance);\n                double minimumThrow = 0.62 + 0.18 * intensity;\n                double tripleThrow = Math.max(oldDelta * 3.0, minimumThrow);\n                double farTarget = clamp(baseDistance + tripleThrow, 0.74, 1.25);\n                double attack = smooth(clamp(u / 0.17, 0.0, 1.0));\n                double releaseFrac = clamp(1.0 / e.duration(), 0.055, 0.22);\n                double releaseStart = 1.0 - releaseFrac;\n                double release = 1.0 - smooth(clamp((u - releaseStart) / releaseFrac, 0.0, 1.0));\n                double excursion = attack * release;\n                out.distance = lerp(baseDistance, farTarget, excursion);\n            } else {\n                out.distance = normalDistance;\n            }\n            return out;''',
    'focus depth overshoot')
p.write_text(s)

p = JAVA / 'SpatialRenderer.java'
s = p.read_text()
s = replace_once(
    s,
    '''            distance = Choreography.clamp(distance, 0.0, 1.0);''',
    '''            distance = Choreography.clamp(distance, 0.0, 1.25); // focus stem may travel beyond the visual room shell''',
    'renderer focus distance ceiling')
p.write_text(s)

print('Applied focus depth excursion: ~3x throw, outside-ring visualization, final-second return.')

# Apply the larger v0.3.2 extension. Its first revision used an overly strict
# MainActivity source anchor; if that one anchor trips, all DSP/project changes have
# already been written, so finish MainActivity with the whitespace-tolerant patch.
extension = Path('patches/patch_distinct_realtime_v032.py')
if extension.exists():
    try:
        exec(compile(extension.read_text(), str(extension), 'exec'), {'__name__': '__main__'})
    except SystemExit as exc:
        if 'playback creation' not in str(exc):
            raise
        print('Using tolerant realtime-player MainActivity patch:', exc)

main_patch = Path('patches/patch_main_realtime_v032.py')
if main_patch.exists():
    exec(compile(main_patch.read_text(), str(main_patch), 'exec'), {'__name__': '__main__'})

# Normalize the generated realtime renderer to the intermediate scene-aware form
# expected by patch_silence_scene_v034.py. This keeps the historical patch chain
# deterministic while the final playback renderer is replaced by Resonance later.
prepare_scene = Path('patches/patch_prepare_silence_v04.py')
if prepare_scene.exists():
    exec(compile(prepare_scene.read_text(), str(prepare_scene), 'exec'), {'__name__': '__main__'})
