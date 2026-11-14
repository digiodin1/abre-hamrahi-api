package ir.abrehamrahi.backup.community;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public final class Store implements Cloud.Sessions {
    public static final Object SESSION_LOCK = new Object();
    public static final Object PLAN_LOCK = new Object();
    private final Context context;
    public final SharedPreferences preferences;

    public Store(Context context) {
        this.context = context.getApplicationContext();
        preferences = this.context.getSharedPreferences("backup", Context.MODE_PRIVATE);
    }

    private SecretKey key() throws Exception {
        KeyStore keys = KeyStore.getInstance("AndroidKeyStore");
        keys.load(null);
        String alias = "abre-hamrahi-session-v1";
        if (!keys.containsAlias(alias)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(alias,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            generator.generateKey();
        }
        return (SecretKey) keys.getKey(alias, null);
    }

    public JSONObject session() throws Exception {
        synchronized (SESSION_LOCK) {
            AtomicFile file = new AtomicFile(new File(context.getFilesDir(), "session.enc"));
            if (!file.getBaseFile().exists()) return new JSONObject();
            byte[] data = file.readFully();
            if (data.length < 29 || data.length > 100000) throw new Exception("نشست ذخیره‌شده معتبر نیست؛ دوباره وارد شوید.");
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, data, 0, 12));
            return new JSONObject(new String(cipher.doFinal(data, 12, data.length - 12), StandardCharsets.UTF_8));
        }
    }

    public void saveSession(JSONObject tokens) throws Exception {
        synchronized (SESSION_LOCK) {
            if (tokens.isNull("access") || tokens.isNull("refresh") ||
                    !(tokens.opt("access") instanceof String) || !(tokens.opt("refresh") instanceof String) ||
                    tokens.getString("access").isEmpty() || tokens.getString("refresh").isEmpty()) {
                throw new Exception("ساختار پاسخ ورود معتبر نیست.");
            }
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            JSONObject value = new JSONObject().put("access", tokens.getString("access")).put("refresh", tokens.getString("refresh"));
            byte[] encrypted = cipher.doFinal(value.toString().getBytes(StandardCharsets.UTF_8));
            AtomicFile target = new AtomicFile(new File(context.getFilesDir(), "session.enc"));
            FileOutputStream output = null;
            try {
                output = target.startWrite();
                output.write(cipher.getIV());
                output.write(encrypted);
                target.finishWrite(output);
            } catch (Exception error) {
                if (output != null) target.failWrite(output);
                throw error;
            }
        }
    }

    public void logout() {
        synchronized (SESSION_LOCK) {
            new AtomicFile(new File(context.getFilesDir(), "session.enc")).delete();
        }
    }

    public void report(String message) {
        String previous = preferences.getString("reports", "");
        String entry = new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.ROOT)
            .format(new java.util.Date()) + " · " + message + "\n";
        preferences.edit().putString("status", message).putString("reports",
            (entry + previous).substring(0, Math.min(24000, entry.length() + previous.length()))).apply();
    }
}
