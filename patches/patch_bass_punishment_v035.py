from pathlib import Path

p = Path('Orbit8D-Android/app/src/main/java/com/orbit8d/app/Choreography.java')
s = p.read_text()

old = '''            String focus = "vocals";
            double maxScore = -1.0;
            double[] scoreVec = new double[order.size()];
            for (int i = 0; i < order.size(); i++) {
                String stem = order.get(i);
                double[] arr = perSectionStemScores.get(stem);
                double score = arr != null && si < arr.length ? arr[si] : 0.0;
                scoreVec[i] = score;
                e.stemScores.put(stem, score);
                if (score > maxScore) {
                    maxScore = score;
                    focus = stem;
                }
            }
            e.focusStem = focus;'''

new = '''            // Bass punishment: bass is a poor perceptual candidate for the big fast
            // 8D lap, so choose the strongest NON-bass stem by default. Bass may only
            // become focus in an actually sparse/bass-led passage where every other
            // stem is effectively quiet compared with it.
            String focus = "vocals";
            double maxScore = -1.0;
            double bestNonBassScore = -1.0;
            String bestNonBassStem = "vocals";
            double bassScore = 0.0;
            double[] scoreVec = new double[order.size()];
            for (int i = 0; i < order.size(); i++) {
                String stem = order.get(i);
                double[] arr = perSectionStemScores.get(stem);
                double score = arr != null && si < arr.length ? arr[si] : 0.0;
                scoreVec[i] = score;
                e.stemScores.put(stem, score);
                maxScore = Math.max(maxScore, score);
                if ("bass".equals(stem)) {
                    bassScore = score;
                } else if (score > bestNonBassScore) {
                    bestNonBassScore = score;
                    bestNonBassStem = stem;
                }
            }
            focus = bestNonBassStem;
            // Roughly: bass must be meaningfully present AND the loudest non-bass
            // activity must be <=20% of it (or near the analyzer noise floor).
            double bassQuietGate = Math.max(0.018, bassScore * 0.20);
            boolean sparseBassMoment = bassScore >= 0.025 && bestNonBassScore <= bassQuietGate;
            if (sparseBassMoment) focus = "bass";
            e.focusStem = focus;'''

if old not in s:
    raise SystemExit('patch anchor not found: focus selection block')
s = s.replace(old, new, 1)

old = '''            out.baseAzimuthDeg = sharedDrift + slotAngle;
            out.focusExtraDeg = extra;
            out.azimuthDeg = out.baseAzimuthDeg + extra;'''

new = '''            out.baseAzimuthDeg = sharedDrift + slotAngle;
            // Background bass should feel like a foundation, not waste an obvious
            // orbit slot. Keep it almost front/center with only a tiny slow sway.
            // If the section is sparse enough that bass legitimately becomes focus,
            // it gets the normal full moving/lap behavior like any other focus stem.
            if ("bass".equals(stem) && !focus) {
                double bassSway = 7.0 * Math.sin(2.0 * Math.PI * t / 31.0 + 0.7);
                out.baseAzimuthDeg = bassSway;
            }
            out.focusExtraDeg = extra;
            out.azimuthDeg = out.baseAzimuthDeg + extra;'''

if old not in s:
    raise SystemExit('patch anchor not found: bass background anchor')
s = s.replace(old, new, 1)

p.write_text(s)
print('Orbit8D v0.3.5 patch: bass focus punishment + anchored background bass applied')

# Chain the optional 300% spatial-overdrive revision so the existing build workflow
# remains the single source of truth for patch ordering.
extra = Path('patches/patch_intensity_300_v036.py')
if extra.exists():
    exec(compile(extra.read_text(), str(extra), 'exec'), {'__name__': '__main__'})
