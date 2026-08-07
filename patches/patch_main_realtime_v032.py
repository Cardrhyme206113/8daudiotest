from pathlib import Path
import re

p = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app/MainActivity.java')
s = p.read_text()

# Playback object.
s = s.replace('private MediaPlayer player;', 'private RealtimeStemPlayer player;', 1)
s = s.replace('if (project == null || !project.hasRender()) {', 'if (project == null || !project.hasStemRender()) {', 1)
s = s.replace('status.setText("Process or re-render first");', 'status.setText("Process the song first");', 1)

# Replace the old single premixed WAV player creation regardless of whitespace.
pat = re.compile(
    r'''player\s*=\s*new\s+MediaPlayer\(\);\s*'''
    r'''player\.setDataSource\(project\.renderedWav\.getAbsolutePath\(\)\);\s*'''
    r'''player\.prepare\(\);\s*'''
    r'''player\.setOnCompletionListener\(mp\s*->\s*\{(.*?)\}\);''',
    re.S)
m = pat.search(s)
if m:
    body = m.group(1)
    replacement = '''player = new RealtimeStemPlayer(project.renderedStems);\n                player.setGains(readSettings());\n                player.setOnCompletionListener(() -> {''' + body + '''});'''
    s = s[:m.start()] + replacement + s[m.end():]
elif 'new RealtimeStemPlayer(project.renderedStems)' not in s:
    # Print useful nearby source if the upstream app changes again.
    idx = s.find('new MediaPlayer')
    print(s[max(0, idx-500):idx+1600] if idx >= 0 else 'No MediaPlayer construction found')
    raise SystemExit('patch anchor not found: realtime playback creation')

# Apply slider changes to the active players immediately, while still persisting them.
old = '''                if (fromUser && (seekBar == drumsGain || seekBar == bassGain || seekBar == otherGain || seekBar == vocalsGain)) {\n                    saveStemGains();\n                }'''
new = '''                if (fromUser && (seekBar == drumsGain || seekBar == bassGain || seekBar == otherGain || seekBar == vocalsGain)) {\n                    saveStemGains();\n                    if (player != null) player.setGains(readSettings());\n                }'''
if old in s:
    s = s.replace(old, new, 1)
elif 'player.setGains(readSettings());' not in s:
    raise SystemExit('patch anchor not found: realtime manual gain listener')

s = s.replace(
    '0–200%. Multiplies the algorithm\'s boosts/cuts; re-render does not rerun separation.',
    '0–200%. LIVE while playing · multiplies AUTO instantly; no re-render needed.',
    1)

# The four MediaPlayers are started together and gently re-aligned only if Android
# lets one drift by more than ~42 ms.
needle = 'double t = player.getCurrentPosition() / 1000.0;'
if needle in s and 'player.syncIfNeeded();' not in s:
    s = s.replace(needle, 'player.syncIfNeeded();\n            ' + needle, 1)

p.write_text(s)
print('MainActivity now uses realtime four-stem playback; manual gains apply instantly.')
