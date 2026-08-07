from pathlib import Path

JAVA = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app')


def replace_once(text, old, new, name):
    if old not in text:
        raise SystemExit(f'patch anchor not found: {name}')
    return text.replace(old, new, 1)

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
for filename in ('AudioProject.java', 'ProcessingEngine.java', 'SpatialRenderer.java'):
    src = (JAVA / filename).read_text().splitlines()
    print(f'=== SOURCE {filename} ===')
    for i, line in enumerate(src, 1):
        print(f'{i:04d}: {line}')
