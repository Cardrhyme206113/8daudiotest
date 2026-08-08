from pathlib import Path
import shutil

src = Path('Orbit8D-Android')
dst = Path('PulseDeck-Android')
if not src.exists():
    raise SystemExit('Orbit8D-Android source was not decoded')
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst)

src_java = src / 'app/src/main/java/com/orbit8d/app'
dst_java = dst / 'app/src/main/java/com/orbit8d/app'
wav = src_java / 'WavFile.java'
if not wav.exists():
    raise SystemExit('Base Android bundle is missing WavFile.java')

# PulseDeck deliberately keeps only the proven WAV helper plus the Lite separator.
# This means its APK does not drag in Orbit8D's spatial renderer/UI classes.
for p in (dst / 'app/src/main/java').rglob('*.java'):
    p.unlink()
dst_java.mkdir(parents=True, exist_ok=True)
shutil.copy2(wav, dst_java / 'WavFile.java')
shutil.copy2(Path('patches/ModelManager.java'), dst_java / 'ModelManager.java')
shutil.copy2(Path('patches/DemucsSeparator.java'), dst_java / 'DemucsSeparator.java')
shutil.copy2(Path('pulsedeck/SmartPlayerActivity.java'), dst_java / 'SmartPlayerActivity.java')
shutil.copy2(Path('pulsedeck/SmartPlaybackService.java'), dst_java / 'SmartPlaybackService.java')

# Android's document picker can stop the Activity and unbind it from the playback
# service. The picker result may arrive before the async service bind finishes.
# Queue selected URIs until onServiceConnected instead of silently dropping them.
activity = dst_java / 'SmartPlayerActivity.java'
s = activity.read_text()
s = s.replace(
    'private boolean bound;\n    private String lastArtKey',
    'private boolean bound;\n    private final ArrayList<Uri> pendingPicked = new ArrayList<>();\n    private String lastArtKey'
)
s = s.replace(
    'service = ((SmartPlaybackService.LocalBinder) binder).getService();\n            bound = true;\n            refreshNow();',
    'service = ((SmartPlaybackService.LocalBinder) binder).getService();\n            bound = true;\n            if (!pendingPicked.isEmpty()) {\n                service.addSongs(new ArrayList<>(pendingPicked));\n                pendingPicked.clear();\n            }\n            refreshNow();'
)
s = s.replace(
    'if (requestCode != PICK_AUDIO || resultCode != RESULT_OK || data == null || service == null) return;',
    'if (requestCode != PICK_AUDIO || resultCode != RESULT_OK || data == null) return;'
)
s = s.replace(
    'service.addSongs(picked);\n    }\n\n    private void refreshNow()',
    'if (picked.isEmpty()) return;\n        if (service != null) {\n            service.addSongs(picked);\n        } else {\n            pendingPicked.addAll(picked);\n        }\n    }\n\n    private void refreshNow()'
)
for required in ('pendingPicked', 'service.addSongs(new ArrayList<>(pendingPicked))', 'pendingPicked.addAll(picked)'):
    if required not in s:
        raise SystemExit('Song picker fix patch failed: ' + required)
activity.write_text(s)

shutil.copy2(Path('pulsedeck/app-build.gradle'), dst / 'app/build.gradle')
shutil.copy2(Path('pulsedeck/AndroidManifest.xml'), dst / 'app/src/main/AndroidManifest.xml')

settings = dst / 'settings.gradle'
if settings.exists():
    s = settings.read_text()
    if 'rootProject.name' in s:
        import re
        s = re.sub(r"rootProject\.name\s*=\s*['\"][^'\"]+['\"]", "rootProject.name = 'PulseDeck'", s)
    else:
        s += "\nrootProject.name = 'PulseDeck'\n"
    settings.write_text(s)

# Old native/spatial AARs are unnecessary for this app and should not leak into the APK.
libs = dst / 'app/libs'
if libs.exists():
    shutil.rmtree(libs)

print('PulseDeck Android source prepared')
