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

public final class ModelManager {
    private ModelManager() {}

    // ~83 MB INT8 HTDemucs v4 ONNX. Same 4-stem target (drums/bass/other/vocals),
    // substantially smaller than the previous ~166 MB fp16-weights graph.
    public static final String MODEL_NAME = "demucs_v4_quantized.onnx";
    public static final String MODEL_URL = "https://huggingface.co/Joker5514/VoiceIsolate-Models/resolve/main/demucs_v4_quantized.onnx?download=true";
    // Filled after CI verifies the exact remote artifact. Empty means size-only validation.
    public static final String MODEL_SHA256 = "";
    public static final long EXPECTED_MIN_BYTES = 75L * 1024L * 1024L;
    private static final String LEGACY_MODEL_NAME = "htdemucs_fp16weights.onnx";

    public interface Progress {
        void onProgress(double fraction, String message);
        boolean isCancelled();
    }

    public static File modelFile(Context context) {
        // Internal app-private storage survives normal APK updates as long as package name
        // and signing certificate stay unchanged.
        File dir = new File(context.getFilesDir(), "models");
        dir.mkdirs();
        File target = new File(dir, MODEL_NAME);
        if (target.exists() && target.length() >= EXPECTED_MIN_BYTES) cleanupLegacy(dir);
        return target;
    }

    public static boolean isModelPresent(Context context) {
        File f = modelFile(context);
        return f.exists() && f.length() >= EXPECTED_MIN_BYTES;
    }

    public static void download(Context context, Progress progress) throws Exception {
        File target = modelFile(context);
        File part = new File(target.getAbsolutePath() + ".part");
        if (part.exists()) part.delete();

        URL url = new URL(MODEL_URL);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setInstanceFollowRedirects(true);
        connection.setConnectTimeout(20_000);
        connection.setReadTimeout(30_000);
        connection.setRequestProperty("User-Agent", "Orbit8D-Android/0.2");
        connection.connect();
        int code = connection.getResponseCode();
        if (code < 200 || code >= 300) throw new IllegalStateException("Model download HTTP " + code);
        long total = connection.getContentLengthLong();
        long read = 0;
        try (InputStream in = new BufferedInputStream(connection.getInputStream(), 1024 * 1024);
             BufferedOutputStream out = new BufferedOutputStream(new FileOutputStream(part), 1024 * 1024)) {
            byte[] buffer = new byte[1024 * 1024];
            int n;
            while ((n = in.read(buffer)) >= 0) {
                if (progress.isCancelled()) throw new InterruptedException("Cancelled");
                if (n == 0) continue;
                out.write(buffer, 0, n);
                read += n;
                double fraction = total > 0 ? read / (double) total : 0.0;
                progress.onProgress(fraction, String.format("Downloading Lite Demucs · %.1f / %.1f MB", read / 1048576.0, total / 1048576.0));
            }
        } finally {
            connection.disconnect();
        }
        if (part.length() < EXPECTED_MIN_BYTES) throw new IllegalStateException("Downloaded model is unexpectedly small");
        if (!MODEL_SHA256.isEmpty()) {
            progress.onProgress(0.995, "Verifying model SHA-256");
            String sha = sha256(part);
            if (!MODEL_SHA256.equalsIgnoreCase(sha)) {
                part.delete();
                throw new IllegalStateException("Model checksum mismatch: " + sha);
            }
        }
        if (target.exists() && !target.delete()) throw new IllegalStateException("Could not replace old model");
        if (!part.renameTo(target)) {
            copyFile(part, target);
            part.delete();
        }
        cleanupLegacy(target.getParentFile());
        progress.onProgress(1.0, "Lite Demucs ONNX ready");
    }

    public static void importModel(Context context, Uri uri, Progress progress) throws Exception {
        File target = modelFile(context);
        File part = new File(target.getAbsolutePath() + ".import");
        if (part.exists()) part.delete();
        long read = 0;
        try (InputStream in = new BufferedInputStream(context.getContentResolver().openInputStream(uri), 1024 * 1024);
             BufferedOutputStream out = new BufferedOutputStream(new FileOutputStream(part), 1024 * 1024)) {
            if (in == null) throw new IllegalStateException("Could not open selected model");
            byte[] buffer = new byte[1024 * 1024];
            int n;
            while ((n = in.read(buffer)) >= 0) {
                if (progress.isCancelled()) throw new InterruptedException("Cancelled");
                if (n == 0) continue;
                out.write(buffer, 0, n);
                read += n;
                progress.onProgress(Math.min(0.96, read / (90.0 * 1024.0 * 1024.0)), String.format("Importing model · %.1f MB", read / 1048576.0));
            }
        }
        if (part.length() < EXPECTED_MIN_BYTES) {
            part.delete();
            throw new IllegalStateException("Selected ONNX file is too small for the Lite Demucs model");
        }
        if (!MODEL_SHA256.isEmpty()) {
            progress.onProgress(0.98, "Verifying model");
            String sha = sha256(part);
            if (!MODEL_SHA256.equalsIgnoreCase(sha)) {
                part.delete();
                throw new IllegalStateException("This is not the expected demucs_v4_quantized.onnx model");
            }
        }
        if (target.exists()) target.delete();
        if (!part.renameTo(target)) {
            copyFile(part, target);
            part.delete();
        }
        cleanupLegacy(target.getParentFile());
        progress.onProgress(1.0, "Lite model imported");
    }

    private static void cleanupLegacy(File modelDir) {
        if (modelDir == null) return;
        File old = new File(modelDir, LEGACY_MODEL_NAME);
        if (old.exists()) old.delete();
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
