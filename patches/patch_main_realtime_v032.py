from pathlib import Path
import re

p = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app/MainActivity.java')
s = p.read_text()

# Playback object: four synchronized already-spatialized stems instead of one baked mix.
if 'private MediaPlayer player;' in s:
    s = s.replace('private MediaPlayer player;', 'private RealtimeStemPlayer player;', 1)
elif 'private RealtimeStemPlayer player;' not in s:
    raise SystemExit('patch anchor not found: player field')

# Replace the complete upstream preparePlayer() method. The original app has a compact
# one-line completion listener, so matching the whole method is considerably safer
# than trying to recognize one particular lambda formatting style.
prepare_pattern = re.compile(
    r'''    private void preparePlayer\(\) \{.*?\n    \}\n\n    private void togglePlayback\(\) \{''',
    re.S)
prepare_replacement = '''    private void preparePlayer() {
        releasePlayer();
        if (!project.hasStemRender()) return;
        try {
            player = new RealtimeStemPlayer(project.renderedStems);
            player.setGains(readSettings());
            player.setOnCompletionListener(() -> play.setText("Play"));
            play.setEnabled(true);
            export.setEnabled(true);
            seek.setProgress(0);
            time.setText("0:00 / " + formatTime(player.getDuration() / 1000.0));
        } catch (Exception e) {
            showError(e);
        }
    }

    private void togglePlayback() {'''
s, n = prepare_pattern.subn(prepare_replacement, s, count=1)
if n != 1 and 'new RealtimeStemPlayer(project.renderedStems)' not in s:
    raise SystemExit('patch anchor not found: preparePlayer method')

# Apply slider changes to active stem players *on every user drag event*. Persistence
# remains SharedPreferences, but there is now zero DSP/render work on this path.
old_listener = '''                if (fromUser && (seekBar == drumsGain || seekBar == bassGain || seekBar == otherGain || seekBar == vocalsGain)) {
                    saveStemGains();
                }'''
new_listener = '''                if (fromUser && (seekBar == drumsGain || seekBar == bassGain || seekBar == otherGain || seekBar == vocalsGain)) {
                    saveStemGains();
                    if (player != null) player.setGains(readSettings());
                }'''
if old_listener in s:
    s = s.replace(old_listener, new_listener, 1)
elif 'if (player != null) player.setGains(readSettings());' not in s:
    raise SystemExit('patch anchor not found: realtime manual gain listener')

s = s.replace(
    "0–200%. Multiplies the algorithm's boosts/cuts; re-render does not rerun separation.",
    "0–200%. LIVE while playing · multiplies AUTO instantly; no re-render needed.",
    1)

# Existing UI ticker is our cheap synchronization heartbeat. It only seeks a slave
# player if Android lets it drift by >42 ms, so ordinary playback is uninterrupted.
needle = 'double t = player.getCurrentPosition() / 1000.0;'
if needle in s and 'player.syncIfNeeded();' not in s:
    s = s.replace(needle, 'player.syncIfNeeded();\n            ' + needle, 1)

p.write_text(s)
print('MainActivity uses four-stem realtime playback; manual stem gains are now immediate.')
