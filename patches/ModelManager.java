package com.orbit8d.app;

import android.content.Context;
import android.net.Uri;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.Map;

/** Manages the lightweight four-file Spleeter model pack. */
public final class ModelManager {
    private ModelManager() {}

    public static final String[] STEMS = {"vocals", "drums", "bass", "other"};
    private static final String BASE_URL = "https://huggingface.co/Best-Practice/spleeter-4stems-onnx/resolve/main/";
    private static final long EXPECTED_MIN_BYTES = 19_000_000L;

    private static final Map<String, String> SHA256 = new LinkedHashMap<>();
    static {
        SHA256.put("vocals", "db47148ab1c52709ce694893f532c91abfe3edc4d46238939570e036a22878ca");
        SHA256.put("drums",  "7ae4002e5633634674f74dc3356d5875b0da894d59ce0f60e844bb8f9cb8aa92");
        SHA256.put("bass",   "ba4c4949a27222492cca49859901a873b4b71461dc48c7c5a51f93d31eb11f55");
        SHA256.put("other",  "3cc59116cb7195946ab9596d8ca25984d09c0f8a70db8cf85d063132f97bc61d");
    }

    public interface Progress {
        void onProgress(double fraction, String message);
        boolean isCancelled();
    }

    /** Compatibility with ProcessingEngine: this now returns the model directory. */
    public static File modelFile(Context context) {
        File dir = new File(new File(context.getFilesDir(), "models"), "spleeter4-fp16");
        dir.mkdirs();
        return dir;
    }

    public static File stemModelFile(File modelDir, String stem) {
        return new File(modelDir, stem + ".fp16.onnx");
    }

    public static File stemModelFile(Context context, String stem) {
        return stemModelFile(modelFile(context), stem);
    }

    public static long totalBytes(Context context) {
        long total = 0L;
        for (String stem : STEMS) {
            File f = stemModelFile(context, stem);
            if (f.exists()) total += f.length();
        }
        return total;
    }

    public static boolean isModelPresent(Context context) {
        for (String stem : STEMS) {
            File f = stemModelFile(context, stem);
            if (!f.exists() || f.length() < EXPECTED_MIN_BYTES) return false;
        }
        return true;
    }

    public static void download(Context context, Progress progress) throws Exception {
        File dir = modelFile(context);
        long finishedBytes = 0L;
        final long expectedTotal = 78_856_560L;

        for (int stemIndex = 0; stemIndex < STEMS.length; stemIndex++) {
            String stem = STEMS[stemIndex];
            File target = stemModelFile(dir, stem);
            String expectedSha = SHA256.get(stem);

            if (target.exists() && target.length() >= EXPECTED_MIN_BYTES) {
                String have = sha256(target);
                if (expectedSha.equalsIgnoreCase(have)) {
                    finishedBytes += target.length();
                    progress.onProgress(Math.min(0.99, finishedBytes / (double) expectedTotal),
                            "Lite model pack · " + stem + " already verified");
                    continue;
                }
                target.delete();
            }

            File part = new File(target.getAbsolutePath() + ".part");
            if (part.exists()) part.delete();
            URL url = new URL(BASE_URL + stem + ".fp16.onnx");
            HttpURLConnection connection = (HttpURLConnection) url.openConnection();
            connection.setInstanceFollowRedirects(true);
            connection.setConnectTimeout(20_000);
            connection.setReadTimeout(45_000);
            connection.setRequestProperty("User-Agent", "Orbit8D-Android/0.2");
            connection.connect();
            int code = connection.getResponseCode();
            if (code < 200 || code >= 300) {
                connection.disconnect();
                throw new IllegalStateException("Model download HTTP " + code + " for " + stem);
            }
            long remoteBytes = connection.getContentLengthLong();
            long read = 0L;
            try (InputStream in = new BufferedInputStream(connection.getInputStream(), 1024 * 1024);
                 BufferedOutputStream out = new BufferedOutputStream(new FileOutputStream(part), 1024 * 1024)) {
                byte[] buffer = new byte[1024 * 1024];
                int n;
                while ((n = in.read(buffer)) >= 0) {
                    if (progress.isCancelled()) throw new InterruptedException("Cancelled");
                    if (n == 0) continue;
                    out.write(buffer, 0, n);
                    read += n;
                    double denominator = remoteBytes > 0 ? remoteBytes : 19_714_140.0;
                    double perFile = Math.min(1.0, read / denominator);
                    progress.onProgress((stemIndex + perFile) / STEMS.length * 0.97,
                            String.format("Downloading Lite Spleeter · %s · %.1f MB", stem, read / 1048576.0));
                }
            } finally {
                connection.disconnect();
            }

            if (part.length() < EXPECTED_MIN_BYTES) {
                part.delete();
                throw new IllegalStateException("Downloaded " + stem + " model is unexpectedly small");
            }
            progress.onProgress((stemIndex + 0.98) / STEMS.length, "Verifying " + stem + " model");
            String gotSha = sha256(part);
            if (!expectedSha.equalsIgnoreCase(gotSha)) {
                part.delete();
                throw new IllegalStateException(stem + " model checksum mismatch: " + gotSha);
            }
            if (target.exists() && !target.delete()) throw new IllegalStateException("Could not replace " + stem + " model");
            if (!part.renameTo(target)) {
                copyFile(part, target);
                part.delete();
            }
            finishedBytes += target.length();
        }

        // Keep the previous Demucs file until the complete new pack is verified, then reclaim it.
        if (isModelPresent(context)) cleanupLegacyDemucs(context);
        progress.onProgress(1.0, "Spleeter 4-stem Lite pack ready · ~79 MB");
    }

    /**
     * The Lite backend is a four-file pack, so a single arbitrary ONNX import is ambiguous.
     * Download is resumable/persistent and is the supported path for this build.
     */
    public static void importModel(Context context, Uri uri, Progress progress) throws Exception {
        throw new IllegalStateException("Lite separator uses four ONNX files. Use Download model pack instead.");
    }

    private static void cleanupLegacyDemucs(Context context) {
        File internal = new File(new File(context.getFilesDir(), "models"), "htdemucs_fp16weights.onnx");
        if (internal.exists()) internal.delete();
        File externalRoot = context.getExternalFilesDir(null);
        if (externalRoot != null) {
            File external = new File(new File(externalRoot, "models"), "htdemucs_fp16weights.onnx");
            if (external.exists()) external.delete();
        }
    }

    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream in = new BufferedInputStream(new FileInputStream(file), 1024 * 1024)) {
            byte[] buffer = new byte[1024 * 1024];
            int n;
            while ((n = in.read(buffer)) >= 0) if (n > 0) digest.update(buffer, 0, n);
        }
        StringBuilder sb = new StringBuilder();
        for (byte b : digest.digest()) sb.append(String.format("%02x", b & 0xff));
        return sb.toString();
    }

    private static void copyFile(File src, File dst) throws Exception {
        try (InputStream in = new FileInputStream(src); FileOutputStream out = new FileOutputStream(dst)) {
            byte[] buffer = new byte[1024 * 1024];
            int n;
            while ((n = in.read(buffer)) >= 0) if (n > 0) out.write(buffer, 0, n);
        }
    }
}
