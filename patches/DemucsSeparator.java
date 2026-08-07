package com.orbit8d.app;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OnnxValue;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import ai.onnxruntime.OrtSession;
import ai.onnxruntime.providers.NNAPIFlags;

import java.io.File;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.util.Collections;
import java.util.EnumSet;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Lightweight four-stem separator.
 *
 * The class name is retained so the rest of Orbit8D does not need an API migration,
 * but inference is now Spleeter 4-stem ONNX rather than HTDemucs. The neural nets
 * operate on STFT magnitudes; this class implements the exact periodic-Hann STFT,
 * Spleeter soft-ratio masks, average high-band extension and weighted iSTFT.
 */
public final class DemucsSeparator {
    public static final int SAMPLE_RATE = 44100;
    private static final int N_FFT = 4096;
    private static final int HOP = 1024;
    private static final int PAD = N_FFT - HOP;
    private static final int FRAMES_PER_SPLIT = 512;
    private static final int MODEL_BINS = 1024;
    private static final int FFT_BINS = N_FFT / 2 + 1;
    private static final int NET_VALUES = 2 * FRAMES_PER_SPLIT * MODEL_BINS;
    private static final float EPS = 1e-10f;
    private static final String[] MODEL_STEMS = {"vocals", "drums", "bass", "other"};
    public static final String[] OUTPUT_STEMS = {"drums", "bass", "other", "vocals"};
    private static final double[] WINDOW = buildWindow();
    private static final double[] WINDOW_SQ = buildWindowSquared();

    public enum Provider { XNNPACK, NNAPI, CPU }

    public interface Progress {
        void onProgress(double fraction, String message);
        boolean isCancelled();
    }

    public static final class Result {
        public final Map<String, File> stems;
        public final String provider;
        public final double durationSeconds;
        Result(Map<String, File> stems, String provider, double durationSeconds) {
            this.stems = stems;
            this.provider = provider;
            this.durationSeconds = durationSeconds;
        }
    }

    private DemucsSeparator() {}

    public static Result separate(
            File mixWav,
            File modelDir,
            File outputDir,
            Provider requestedProvider,
            Progress progress) throws Exception {
        outputDir.mkdirs();
        WavFile.Info info = WavFile.readInfo(mixWav);
        if (info.sampleRate != SAMPLE_RATE || info.channels != 2) {
            throw new IllegalArgumentException("Separator input must be 44.1 kHz stereo");
        }
        for (String stem : MODEL_STEMS) {
            File model = ModelManager.stemModelFile(modelDir, stem);
            if (!model.exists()) throw new IllegalStateException("Missing Lite model: " + model.getName());
        }

        final long totalFrames = info.frames;
        final int stftFrames = Math.max(1, (int) Math.ceil((PAD + totalFrames) / (double) HOP));
        final int splits = Math.max(1, (stftFrames + FRAMES_PER_SPLIT - 1) / FRAMES_PER_SPLIT);
        final OrtEnvironment env = OrtEnvironment.getEnvironment("Orbit8D-Lite");

        progress.onProgress(0.005, "Starting Lite separator · preparing NPU");
        SessionPack pack = openSessions(env, modelDir, requestedProvider);

        Map<String, File> stemFiles = new LinkedHashMap<>();
        Map<String, WavFile.Writer> writers = new LinkedHashMap<>();
        try {
            for (String stem : OUTPUT_STEMS) {
                File f = new File(outputDir, stem + ".wav");
                if (f.exists()) f.delete();
                stemFiles.put(stem, f);
                writers.put(stem, new WavFile.Writer(f, SAMPLE_RATE, 2));
            }

            float[][] carry = new float[MODEL_STEMS.length * 2][PAD];
            float[] carryWeight = new float[PAD];
            boolean haveCarry = false;

            for (int splitIndex = 0; splitIndex < splits; splitIndex++) {
                if (progress.isCancelled()) throw new InterruptedException("Cancelled");
                int frame0 = splitIndex * FRAMES_PER_SPLIT;
                int validFrames = Math.min(FRAMES_PER_SPLIT, stftFrames - frame0);
                long globalPaddedStart = (long) frame0 * HOP;
                long originalStart = globalPaddedStart - PAD;
                int segmentLength = (validFrames - 1) * HOP + N_FFT;

                progress.onProgress(0.01 + 0.78 * splitIndex / (double) splits,
                        "Lite separation · STFT " + (splitIndex + 1) + "/" + splits + " · " + pack.name);

                float[] sourceL = new float[segmentLength];
                float[] sourceR = new float[segmentLength];
                readPaddedSegment(mixWav, totalFrames, originalStart, sourceL, sourceR);

                float[] specR = new float[2 * validFrames * FFT_BINS];
                float[] specI = new float[2 * validFrames * FFT_BINS];
                FloatBuffer netInput = ByteBuffer.allocateDirect(NET_VALUES * 4)
                        .order(ByteOrder.nativeOrder()).asFloatBuffer();
                buildStft(sourceL, sourceR, validFrames, specR, specI, netInput);

                float[][] estimates = new float[MODEL_STEMS.length][NET_VALUES];
                for (int s = 0; s < MODEL_STEMS.length; s++) {
                    if (progress.isCancelled()) throw new InterruptedException("Cancelled");
                    progress.onProgress(0.01 + 0.78 * (splitIndex + s / 4.0) / splits,
                            "Lite separation · " + MODEL_STEMS[s] + " · " + pack.name);
                    runModel(env, pack.sessions[s], netInput, estimates[s]);
                }

                float[][] raw = new float[MODEL_STEMS.length * 2][segmentLength];
                float[] weight = new float[segmentLength];
                synthesizeSplit(validFrames, specR, specI, estimates, raw, weight);

                if (haveCarry) {
                    int n = Math.min(PAD, segmentLength);
                    for (int i = 0; i < n; i++) weight[i] += carryWeight[i];
                    for (int c = 0; c < raw.length; c++) {
                        for (int i = 0; i < n; i++) raw[c][i] += carry[c][i];
                    }
                }

                boolean last = splitIndex == splits - 1;
                int emitEnd = last ? segmentLength : Math.max(0, segmentLength - PAD);
                emitNormalized(raw, weight, 0, emitEnd, globalPaddedStart, totalFrames, writers);

                if (!last) {
                    int tailStart = segmentLength - PAD;
                    System.arraycopy(weight, tailStart, carryWeight, 0, PAD);
                    for (int c = 0; c < raw.length; c++) {
                        System.arraycopy(raw[c], tailStart, carry[c], 0, PAD);
                    }
                    haveCarry = true;
                }
            }
        } finally {
            for (WavFile.Writer writer : writers.values()) {
                try { writer.close(); } catch (Exception ignored) {}
            }
            pack.close();
        }

        progress.onProgress(1.0, "Four Lite stems separated · " + pack.name);
        return new Result(stemFiles, pack.name, totalFrames / (double) SAMPLE_RATE);
    }

    private static void readPaddedSegment(
            File mixWav, long totalFrames, long originalStart,
            float[] left, float[] right) throws Exception {
        long readStart = Math.max(0L, originalStart);
        long readEnd = Math.min(totalFrames, originalStart + left.length);
        if (readEnd <= readStart) return;
        int dstOffset = (int) (readStart - originalStart);
        int want = (int) Math.min(Integer.MAX_VALUE, readEnd - readStart);
        try (WavFile.Reader reader = new WavFile.Reader(mixWav)) {
            reader.seekFrame(readStart);
            int done = 0;
            while (done < want) {
                int got = reader.readFrames(left, right, dstOffset + done, want - done);
                if (got <= 0) break;
                done += got;
            }
        }
    }

    private static void buildStft(
            float[] sourceL, float[] sourceR, int validFrames,
            float[] specR, float[] specI, FloatBuffer netInput) {
        double[] re = new double[N_FFT];
        double[] im = new double[N_FFT];
        float[] magnitudes = new float[NET_VALUES];
        for (int ch = 0; ch < 2; ch++) {
            float[] src = ch == 0 ? sourceL : sourceR;
            for (int frame = 0; frame < validFrames; frame++) {
                int at = frame * HOP;
                for (int i = 0; i < N_FFT; i++) {
                    re[i] = src[at + i] * WINDOW[i];
                    im[i] = 0.0;
                }
                fft(re, im, false);
                int specBase = (ch * validFrames + frame) * FFT_BINS;
                int netBase = (ch * FRAMES_PER_SPLIT + frame) * MODEL_BINS;
                for (int bin = 0; bin < FFT_BINS; bin++) {
                    specR[specBase + bin] = (float) re[bin];
                    specI[specBase + bin] = (float) im[bin];
                    if (bin < MODEL_BINS) {
                        magnitudes[netBase + bin] = (float) Math.hypot(re[bin], im[bin]);
                    }
                }
            }
        }
        netInput.clear();
        netInput.put(magnitudes);
        netInput.flip();
    }

    private static void runModel(
            OrtEnvironment env, OrtSession session, FloatBuffer inputBuffer, float[] destination) throws Exception {
        inputBuffer.rewind();
        try (OnnxTensor input = OnnxTensor.createTensor(env, inputBuffer,
                new long[]{2, 1, FRAMES_PER_SPLIT, MODEL_BINS});
             OrtSession.Result result = session.run(Collections.singletonMap("x", input))) {
            OnnxValue value = result.get(0);
            if (!(value instanceof OnnxTensor)) throw new IllegalStateException("Unexpected Lite separator output type");
            FloatBuffer out = ((OnnxTensor) value).getFloatBuffer();
            if (out == null) throw new IllegalStateException("Lite separator output was not float-compatible");
            out.rewind();
            if (out.remaining() < destination.length) {
                throw new IllegalStateException("Lite separator output was shorter than expected: " + out.remaining());
            }
            out.get(destination, 0, destination.length);
        }
    }

    private static void synthesizeSplit(
            int validFrames, float[] specR, float[] specI, float[][] estimates,
            float[][] raw, float[] weight) {
        double[] re = new double[N_FFT];
        double[] im = new double[N_FFT];
        double[] meanMask = new double[MODEL_STEMS.length];
        double[] masks = new double[MODEL_STEMS.length];

        for (int frame = 0; frame < validFrames; frame++) {
            int at = frame * HOP;
            for (int i = 0; i < N_FFT; i++) weight[at + i] += (float) WINDOW_SQ[i];

            for (int ch = 0; ch < 2; ch++) {
                java.util.Arrays.fill(meanMask, 0.0);
                for (int bin = 0; bin < MODEL_BINS; bin++) {
                    computeMasks(estimates, ch, frame, bin, masks);
                    for (int s = 0; s < MODEL_STEMS.length; s++) meanMask[s] += masks[s];
                }
                for (int s = 0; s < MODEL_STEMS.length; s++) meanMask[s] /= MODEL_BINS;

                int specBase = (ch * validFrames + frame) * FFT_BINS;
                for (int s = 0; s < MODEL_STEMS.length; s++) {
                    for (int bin = 0; bin < FFT_BINS; bin++) {
                        double mask;
                        if (bin < MODEL_BINS) {
                            computeMasks(estimates, ch, frame, bin, masks);
                            mask = masks[s];
                        } else {
                            mask = meanMask[s];
                        }
                        re[bin] = specR[specBase + bin] * mask;
                        im[bin] = specI[specBase + bin] * mask;
                    }
                    im[0] = 0.0;
                    im[N_FFT / 2] = 0.0;
                    for (int bin = 1; bin < N_FFT / 2; bin++) {
                        re[N_FFT - bin] = re[bin];
                        im[N_FFT - bin] = -im[bin];
                    }
                    fft(re, im, true);
                    float[] target = raw[s * 2 + ch];
                    for (int i = 0; i < N_FFT; i++) {
                        target[at + i] += (float) (re[i] * WINDOW[i]);
                    }
                }
            }
        }
    }

    private static void computeMasks(float[][] estimates, int ch, int frame, int bin, double[] masks) {
        int index = (ch * FRAMES_PER_SPLIT + frame) * MODEL_BINS + bin;
        double denom = EPS;
        for (int s = 0; s < MODEL_STEMS.length; s++) {
            double e = estimates[s][index];
            double sq = e * e;
            masks[s] = sq;
            denom += sq;
        }
        double epsilonPerStem = EPS / MODEL_STEMS.length;
        for (int s = 0; s < MODEL_STEMS.length; s++) {
            masks[s] = (masks[s] + epsilonPerStem) / denom;
        }
    }

    private static void emitNormalized(
            float[][] raw, float[] weight, int localFrom, int localTo,
            long globalPaddedStart, long totalFrames,
            Map<String, WavFile.Writer> writers) throws Exception {
        long cropStart = PAD;
        long cropEnd = PAD + totalFrames;
        long globalFrom = globalPaddedStart + localFrom;
        long globalTo = globalPaddedStart + localTo;
        long useFrom = Math.max(globalFrom, cropStart);
        long useTo = Math.min(globalTo, cropEnd);
        if (useTo <= useFrom) return;

        int start = localFrom + (int) (useFrom - globalFrom);
        int count = (int) (useTo - useFrom);
        final int block = 4096;
        float[] left = new float[block];
        float[] right = new float[block];
        int written = 0;
        for (int s = 0; s < MODEL_STEMS.length; s++) {
            WavFile.Writer writer = writers.get(MODEL_STEMS[s]);
            if (writer == null) throw new IllegalStateException("No writer for " + MODEL_STEMS[s]);
            written = 0;
            while (written < count) {
                int n = Math.min(block, count - written);
                for (int i = 0; i < n; i++) {
                    int p = start + written + i;
                    float w = weight[p];
                    if (w > 1e-8f) {
                        left[i] = raw[s * 2][p] / w;
                        right[i] = raw[s * 2 + 1][p] / w;
                    } else {
                        left[i] = 0f;
                        right[i] = 0f;
                    }
                }
                writer.writeFrames(left, right, 0, n);
                written += n;
            }
        }
    }

    private static SessionPack openSessions(OrtEnvironment env, File modelDir, Provider requested) throws Exception {
        if (requested == Provider.NNAPI) {
            try { return SessionPack.open(env, modelDir, Provider.NNAPI, true, "NPU via NNAPI · FP16"); }
            catch (Exception strictFailure) {
                try { return SessionPack.open(env, modelDir, Provider.NNAPI, false, "NNAPI hybrid · FP16"); }
                catch (Exception relaxedFailure) {
                    try { return SessionPack.open(env, modelDir, Provider.XNNPACK, false, "XNNPACK fallback"); }
                    catch (Exception xnnFailure) { return SessionPack.open(env, modelDir, Provider.CPU, false, "CPU fallback"); }
                }
            }
        }
        if (requested == Provider.XNNPACK) {
            try { return SessionPack.open(env, modelDir, Provider.XNNPACK, false, "XNNPACK"); }
            catch (Exception e) { return SessionPack.open(env, modelDir, Provider.CPU, false, "CPU fallback"); }
        }
        return SessionPack.open(env, modelDir, Provider.CPU, false, "CPU");
    }

    private static OrtSession.SessionOptions createSessionOptions(Provider provider, boolean strictNpu) throws OrtException {
        OrtSession.SessionOptions opts = new OrtSession.SessionOptions();
        if (provider == Provider.NNAPI) {
            EnumSet<NNAPIFlags> flags = EnumSet.of(NNAPIFlags.USE_FP16);
            if (strictNpu) flags.add(NNAPIFlags.CPU_DISABLED);
            opts.addNnapi(flags);
        } else if (provider == Provider.XNNPACK) {
            Map<String, String> config = new HashMap<>();
            int threads = Math.max(2, Math.min(8, Runtime.getRuntime().availableProcessors() - 1));
            config.put("intra_op_num_threads", Integer.toString(threads));
            opts.addXnnpack(config);
        }
        return opts;
    }

    private static final class SessionPack implements AutoCloseable {
        final OrtSession[] sessions = new OrtSession[MODEL_STEMS.length];
        final OrtSession.SessionOptions[] options = new OrtSession.SessionOptions[MODEL_STEMS.length];
        final String name;

        private SessionPack(String name) { this.name = name; }

        static SessionPack open(
                OrtEnvironment env, File modelDir, Provider provider,
                boolean strictNpu, String name) throws Exception {
            SessionPack pack = new SessionPack(name);
            try {
                for (int i = 0; i < MODEL_STEMS.length; i++) {
                    pack.options[i] = createSessionOptions(provider, strictNpu);
                    File file = ModelManager.stemModelFile(modelDir, MODEL_STEMS[i]);
                    pack.sessions[i] = env.createSession(file.getAbsolutePath(), pack.options[i]);
                }
                return pack;
            } catch (Exception e) {
                pack.close();
                throw e;
            }
        }

        @Override public void close() {
            for (OrtSession session : sessions) if (session != null) try { session.close(); } catch (Exception ignored) {}
            for (OrtSession.SessionOptions option : options) if (option != null) try { option.close(); } catch (Exception ignored) {}
        }
    }

    private static double[] buildWindow() {
        double[] w = new double[N_FFT];
        for (int i = 0; i < N_FFT; i++) {
            // np.hanning(N_FFT + 1)[:-1] => periodic Hann.
            w[i] = 0.5 - 0.5 * Math.cos(2.0 * Math.PI * i / N_FFT);
        }
        return w;
    }

    private static double[] buildWindowSquared() {
        double[] out = new double[N_FFT];
        for (int i = 0; i < N_FFT; i++) out[i] = WINDOW[i] * WINDOW[i];
        return out;
    }

    /** In-place radix-2 complex FFT. */
    private static void fft(double[] re, double[] im, boolean inverse) {
        int n = re.length;
        for (int i = 1, j = 0; i < n; i++) {
            int bit = n >>> 1;
            for (; (j & bit) != 0; bit >>>= 1) j ^= bit;
            j ^= bit;
            if (i < j) {
                double tr = re[i]; re[i] = re[j]; re[j] = tr;
                double ti = im[i]; im[i] = im[j]; im[j] = ti;
            }
        }
        for (int len = 2; len <= n; len <<= 1) {
            double angle = 2.0 * Math.PI / len * (inverse ? 1.0 : -1.0);
            double wLenR = Math.cos(angle), wLenI = Math.sin(angle);
            for (int i = 0; i < n; i += len) {
                double wr = 1.0, wi = 0.0;
                int half = len >>> 1;
                for (int j = 0; j < half; j++) {
                    int a = i + j, b = a + half;
                    double vr = re[b] * wr - im[b] * wi;
                    double vi = re[b] * wi + im[b] * wr;
                    double ur = re[a], ui = im[a];
                    re[a] = ur + vr; im[a] = ui + vi;
                    re[b] = ur - vr; im[b] = ui - vi;
                    double nextWr = wr * wLenR - wi * wLenI;
                    wi = wr * wLenI + wi * wLenR;
                    wr = nextWr;
                }
            }
        }
        if (inverse) {
            for (int i = 0; i < n; i++) { re[i] /= n; im[i] /= n; }
        }
    }
}
