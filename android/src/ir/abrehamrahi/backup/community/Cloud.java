package ir.abrehamrahi.backup.community;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;

public final class Cloud {
    public interface BeforeComplete { void record(String key) throws Exception; }
    public interface Sessions {
        JSONObject session() throws Exception;
        void saveSession(JSONObject tokens) throws Exception;
    }
    public interface Connections { HttpURLConnection open(String url) throws Exception; }
    public interface Destinations { void check(String url) throws Exception; }
    private static final String API = "https://abrehamrahi.ir/api/";
    private static final Object TOKEN_LOCK = new Object();
    private final Sessions store;
    private final Connections connections;
    private final Destinations destinations;
    private volatile boolean cancelled;
    private volatile HttpURLConnection active;

    public Cloud(Sessions store) {
        this(store, url -> (HttpURLConnection) new URL(url).openConnection(), Policy::safeDestination);
    }

    Cloud(Sessions store, Connections connections, Destinations destinations) {
        this.store = store;
        this.connections = connections;
        this.destinations = destinations;
    }

    public void cancel() {
        cancelled = true;
        HttpURLConnection connection = active;
        if (connection != null) connection.disconnect();
    }

    public void check() throws IOException {
        if (cancelled || Thread.currentThread().isInterrupted()) throw new UserError("عملیات متوقف شد.");
    }

    private HttpURLConnection connect(String url, String method) throws Exception {
        check();
        HttpURLConnection connection = connections.open(url);
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(20000);
        connection.setReadTimeout(45000);
        connection.setRequestMethod(method);
        active = connection;
        return connection;
    }

    private byte[] read(InputStream input, long limit) throws Exception {
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[65536];
            int length;
            while ((length = stream.read(buffer)) != -1) {
                check();
                if (output.size() + length > limit) throw new UserError("پاسخ سرویس بیش از حد مجاز است.");
                output.write(buffer, 0, length);
            }
            return output.toByteArray();
        }
    }

    public JSONObject json(String method, String path, JSONObject payload, boolean authenticated) throws Exception {
        return request(method, path, payload, authenticated, true);
    }

    private JSONObject request(String method, String path, JSONObject payload, boolean authenticated, boolean refresh) throws Exception {
        check();
        if (!path.matches("v[0-9]+/[a-zA-Z0-9_/?=&.%-]+")) throw new UserError("مسیر API معتبر نیست.");
        HttpURLConnection connection = connect(API + path, method);
        try {
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("Accept-Language", "fa");
            connection.setRequestProperty("user-device", "web-desktop");
            String usedToken = "";
            if (authenticated) {
                String token = store.session().optString("access");
                if (token.isEmpty()) throw new UserError("ابتدا وارد حساب شوید.");
                connection.setRequestProperty("Authorization", "Bearer " + token);
                usedToken = token;
            }
            if (payload != null) {
                byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setFixedLengthStreamingMode(data.length);
                try (OutputStream output = connection.getOutputStream()) { output.write(data); }
            }
            int status = connection.getResponseCode();
            if (status == 401 && authenticated && refresh) {
                connection.disconnect();
                synchronized (TOKEN_LOCK) {
                    JSONObject previous = store.session();
                    if (usedToken.equals(previous.optString("access"))) {
                        JSONObject renewed = request("POST", "v2/profile/auth/token-refresh/",
                            new JSONObject().put("refresh", previous.optString("refresh")), false, false);
                        if (!renewed.has("refresh")) renewed.put("refresh", previous.optString("refresh"));
                        store.saveSession(renewed);
                    }
                }
                return request(method, path, payload, true, false);
            }
            if (status < 200 || status >= 300) {
                if (connection.getErrorStream() != null) {
                    try {
                        JSONObject error = new JSONObject(new String(read(connection.getErrorStream(), 131072), StandardCharsets.UTF_8));
                        if (error.optBoolean("captcha_required")) throw new UserError("کپچا لازم است؛ تصویر را دریافت و پاسخ را وارد کنید.");
                    } catch (org.json.JSONException ignored) {}
                }
                throw new UserError("درخواست ابر ناموفق بود (HTTP " + status + "). دوباره تلاش کنید؛ در خطای ورود، دوباره وارد شوید.");
            }
            byte[] raw = read(connection.getInputStream(), 4 * 1024 * 1024);
            return raw.length == 0 ? new JSONObject() : new JSONObject(new String(raw, StandardCharsets.UTF_8));
        } finally {
            connection.disconnect();
            if (active == connection) active = null;
        }
    }

    public List<JSONObject> list(Long parent) throws Exception {
        List<JSONObject> items = new ArrayList<>();
        for (int offset = 0; offset <= 1000000; offset += 1000) {
            String path = "v2/flat/list-objects/?is_trash=false&recursive=false&limit=1000&offset=" + offset;
            if (parent != null) path += "&parent=" + URLEncoder.encode(parent.toString(), "UTF-8");
            JSONObject result = json("GET", path, null, true);
            JSONArray page = result.getJSONArray("results");
            for (int index = 0; index < page.length(); index++) {
                JSONObject item = page.getJSONObject(index);
                item.getString("name");
                item.getLong("id");
                items.add(item);
            }
            if (result.isNull("next") || Boolean.FALSE.equals(result.opt("next")) || result.optString("next").isEmpty()) return items;
            if (page.length() == 0) break;
        }
        throw new UserError("صفحه‌بندی ابر معتبر نیست.");
    }

    public JSONObject named(Long parent, String name) throws Exception {
        JSONObject match = null;
        for (JSONObject item : list(parent)) {
            if (name.equals(item.getString("name"))) {
                if (match != null) throw new UserError("چند شیء با نام یکسان در مقصد وجود دارد: " + name);
                match = item;
            }
        }
        return match;
    }

    private void exact(JSONObject item, String name, Long parent) throws Exception {
        if (!name.equals(item.getString("name")) ||
                (parent == null ? !item.isNull("parent") : item.optLong("parent", -1) != parent)) {
            throw new UserError("ابر نام یا محل اصلی را حفظ نکرد؛ عملیات تأیید نشد.");
        }
    }

    public long folder(String name, Long parent) throws Exception {
        Policy.name(name);
        JSONObject item = named(parent, name);
        if (item == null) {
            try {
                item = json("POST", "v2/flat/create-folder/", new JSONObject().put("name", name)
                    .put("parent", parent == null ? JSONObject.NULL : parent), true);
            } catch (Exception error) {
                item = named(parent, name);
                if (item == null) throw error;
            }
        }
        if (!item.isNull("key") && !item.optString("key").isEmpty()) throw new UserError("فایل با نام پوشه تعارض دارد: " + name);
        exact(item, name, parent);
        return item.getLong("id");
    }

    public JSONObject object(long identifier) throws Exception {
        JSONObject result = json("GET", "v2/flat/get-object/" + identifier + "/", null, true);
        if (result.getLong("id") != identifier) throw new UserError("شناسهٔ شیء ابری تأیید نشد.");
        return result;
    }

    public void move(long identifier, long destination, String name) throws Exception {
        JSONObject before = object(identifier);
        if (before.optLong("parent", -1) == destination) return;
        Exception failure = null;
        try {
            json("POST", "v2/rgw/move-object/", new JSONObject().put("source_obj_id", identifier)
                .put("target_parent_id", destination), true);
        } catch (Exception error) { failure = error; }
        JSONObject after = object(identifier);
        exact(after, name, destination);
        if (!before.optString("key").equals(after.optString("key")) || before.optLong("size", -1) != after.optLong("size", -1)) {
            throw new UserError("انتقال نسخهٔ قبلی تأیید نشد.");
        }
        if (after.getLong("parent") != destination && failure != null) throw failure;
    }

    public void verify(long identifier, String hash, long size) throws Exception {
        JSONObject item = object(identifier);
        String url = item.getString("download_url");
        destinations.check(url);
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        HttpURLConnection connection = connect(url, "GET");
        if ("abrehamrahi.ir".equals(new URL(url).getHost())) {
            connection.setRequestProperty("Authorization", "Bearer " + store.session().optString("access"));
        }
        try {
            if (connection.getResponseCode() != 200) throw new UserError("دریافت فایل برای بررسی صحت ناموفق بود.");
            long length = 0;
            try (InputStream input = connection.getInputStream()) {
                byte[] buffer = new byte[65536];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    check();
                    length += count;
                    if (length > size) throw new UserError("اندازهٔ بازیابی‌شده صحیح نیست.");
                    digest.update(buffer, 0, count);
                }
            }
            if (length != size || !Policy.hex(digest.digest()).equals(hash)) throw new UserError("SHA-256 فایل ابری برابر نیست.");
        } finally { connection.disconnect(); if (active == connection) active = null; }
    }

    public JSONObject upload(File file, String name, long parent, BeforeComplete beforeComplete) throws Exception {
        long size = file.length();
        JSONObject info = json("POST", "v2/flat/start-upload/", new JSONObject().put("obj_size", size), true);
        JSONArray urls = info.getJSONArray("signed_urls"), parts = new JSONArray();
        long chunk = info.getLong("chunk_size");
        if (chunk <= 0 || urls.length() != Math.max(1, (size + chunk - 1) / chunk)) throw new UserError("قرارداد بخش‌های آپلود معتبر نیست.");
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[65536];
            for (int index = 0; index < urls.length(); index++) {
                String url = urls.getString(index);
                destinations.check(url);
                long length = Math.min(chunk, Math.max(0, size - index * chunk));
                HttpURLConnection connection = connect(url, "PUT");
                try {
                    connection.setDoOutput(true);
                    connection.setRequestProperty("Content-Type", "application/octet-stream");
                    connection.setFixedLengthStreamingMode(length);
                    try (OutputStream output = connection.getOutputStream()) {
                        long remaining = length;
                        while (remaining > 0) {
                            check();
                            int count = input.read(buffer, 0, (int) Math.min(buffer.length, remaining));
                            if (count < 0) throw new UserError("نسخهٔ موقت فایل ناقص است.");
                            output.write(buffer, 0, count);
                            remaining -= count;
                        }
                    }
                    int status = connection.getResponseCode();
                    String etag = connection.getHeaderField("ETag");
                    if (status < 200 || status >= 300 || etag == null) throw new UserError("بخشی از فایل ارسال نشد (HTTP " + status + ").");
                    parts.put(new JSONObject().put("ETag", etag).put("PartNumber", index + 1).put("size", length));
                } finally { connection.disconnect(); if (active == connection) active = null; }
            }
        }
        beforeComplete.record(info.getString("key"));
        JSONObject result = json("POST", "v2/flat/complete-upload/", new JSONObject().put("key", info.get("key"))
            .put("upload_id", info.get("upload_id")).put("name", name).put("parent", parent)
            .put("parts", parts).put("force_overwrite", false), true);
        exact(result, name, parent);
        if (result.getLong("size") != size) throw new UserError("اندازهٔ فایل ارسال‌شده صحیح نیست.");
        return result;
    }
}
