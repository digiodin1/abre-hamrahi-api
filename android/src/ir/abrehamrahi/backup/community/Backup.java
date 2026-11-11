package ir.abrehamrahi.backup.community;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.net.Uri;
import android.provider.DocumentsContract;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

public final class Backup {
    public static final AtomicBoolean RUNNING = new AtomicBoolean();
    private final Context context;
    private final Store store;
    public final Cloud cloud;
    private final Map<String, Long> directories = new HashMap<>();

    private static final class Source {
        Uri uri;
        String relative;
        long size;
        long modified;
        Source(Uri uri, String relative, long size, long modified) {
            this.uri = uri; this.relative = relative; this.size = size; this.modified = modified;
        }
    }

    public Backup(Context context) {
        this.context = context.getApplicationContext();
        store = new Store(context);
        cloud = new Cloud(store);
    }

    public static String documentName(Context context, Uri tree) throws Exception {
        Uri document = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree));
        try (Cursor cursor = context.getContentResolver().query(document,
                new String[]{DocumentsContract.Document.COLUMN_DISPLAY_NAME}, null, null, null)) {
            if (cursor == null || !cursor.moveToFirst()) throw new UserError("پوشهٔ انتخاب‌شده در دسترس نیست.");
            return Policy.name(cursor.getString(0));
        }
    }

    private void scan(Uri tree, String identifier, String relative, int depth, List<Source> files, Set<String> seen) throws Exception {
        cloud.check();
        if (depth > 32) throw new UserError("عمق پوشه بیش از ۳۲ است.");
        if (!seen.add(identifier)) throw new UserError("ساختار چرخه‌ای در پوشه دیده شد.");
        Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, identifier);
        String[] columns = {DocumentsContract.Document.COLUMN_DOCUMENT_ID, DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE, DocumentsContract.Document.COLUMN_SIZE, DocumentsContract.Document.COLUMN_LAST_MODIFIED};
        try (Cursor cursor = context.getContentResolver().query(children, columns, null, null, null)) {
            if (cursor == null) throw new UserError("خواندن فهرست پوشه ممکن نشد؛ دسترسی را دوباره بدهید.");
            Set<String> names = new HashSet<>();
            while (cursor.moveToNext()) {
                cloud.check();
                String name = Policy.name(cursor.getString(1));
                if (Policy.excluded(name)) continue;
                if (!names.add(name)) throw new UserError("چند فایل هم‌نام در یک پوشه وجود دارد.");
                String child = cursor.getString(0), path = relative + "/" + name;
                if (DocumentsContract.Document.MIME_TYPE_DIR.equals(cursor.getString(2))) {
                    scan(tree, child, path, depth + 1, files, seen);
                } else {
                    if (files.size() >= Policy.MAX_FILES) throw new UserError("حداکثر ۵۰۰۰ فایل در هر نوبت پشتیبانی می‌شود.");
                    files.add(new Source(DocumentsContract.buildDocumentUriUsingTree(tree, child), path,
                        cursor.isNull(3) ? -1 : cursor.getLong(3), cursor.isNull(4) ? 0 : cursor.getLong(4)));
                }
            }
        }
    }

    private String snapshot(Source source, File target, long limit) throws Exception {
        ContentResolver resolver = context.getContentResolver();
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[65536];
        long length = 0;
        try (InputStream input = resolver.openInputStream(source.uri); FileOutputStream output = new FileOutputStream(target)) {
            if (input == null) throw new UserError("فایل خواندنی نیست.");
            int count;
            while ((count = input.read(buffer)) != -1) {
                cloud.check();
                length += count;
                if (length > limit) throw new UserError("فایل از سقف اندازهٔ انتخاب‌شده بزرگ‌تر است.");
                output.write(buffer, 0, count);
                digest.update(buffer, 0, count);
            }
            output.getFD().sync();
        }
        if (source.size >= 0 && source.size != length) throw new UserError("فایل حین خواندن تغییر کرد؛ نوبت بعد دوباره بررسی می‌شود.");
        String first = Policy.hex(digest.digest());
        digest.reset();
        long secondLength = 0;
        try (InputStream input = resolver.openInputStream(source.uri)) {
            if (input == null) throw new UserError("بازخوانی فایل ممکن نشد.");
            int count;
            while ((count = input.read(buffer)) != -1) {
                cloud.check();
                secondLength += count;
                if (secondLength > limit) throw new UserError("فایل حین خواندن بزرگ شد.");
                digest.update(buffer, 0, count);
            }
        }
        if (length != secondLength || !first.equals(Policy.hex(digest.digest()))) {
            throw new UserError("فایل هنوز در حال تغییر است؛ نوبت بعد تلاش می‌شود.");
        }
        return first;
    }

    private long directory(String relative, long root) throws Exception {
        long parent = root;
        String key = "";
        if (!relative.isEmpty()) {
            for (String component : relative.split("/")) {
                key += "/" + component;
                Long cached = directories.get(key);
                if (cached == null) {
                    cached = cloud.folder(component, parent);
                    directories.put(key, cached);
                }
                parent = cached;
            }
        }
        return parent;
    }

    private JSONObject cached(SQLiteDatabase database, String path) throws Exception {
        try (Cursor cursor = database.rawQuery("SELECT data FROM objects WHERE path=?", new String[]{path})) {
            return cursor.moveToFirst() ? new JSONObject(cursor.getString(0)) : null;
        }
    }

    private void remember(SQLiteDatabase database, String path, String hash, long size, long identifier, long parent) throws Exception {
        JSONObject record = new JSONObject().put("hash", hash).put("size", size).put("id", identifier).put("parent", parent);
        ContentValues values = new ContentValues();
        values.put("path", path); values.put("data", record.toString());
        database.beginTransaction();
        try {
            database.insertWithOnConflict("objects", null, values, SQLiteDatabase.CONFLICT_REPLACE);
            database.delete("pending", "path=?", new String[]{path});
            database.setTransactionSuccessful();
        } finally { database.endTransaction(); }
    }

    private void send(SQLiteDatabase database, Source source, File temporary, String hash, long root) throws Exception {
        String path = source.relative;
        JSONObject previous = cached(database, path);
        if (previous != null && hash.equals(previous.optString("hash"))) return;
        int separator = path.lastIndexOf('/');
        String name = path.substring(separator + 1);
        long parent = directory(path.substring(0, separator), root);
        JSONObject pending = null;
        try (Cursor cursor = database.rawQuery("SELECT data FROM pending WHERE path=?", new String[]{path})) {
            if (cursor.moveToFirst()) pending = new JSONObject(cursor.getString(0));
        }
        JSONObject existing = cloud.named(parent, name);
        if (pending != null) {
            if (existing == null || !pending.getString("key").equals(existing.optString("key"))) {
                throw new UserError("نتیجهٔ آپلود قبلی نامعلوم است؛ برای جلوگیری از فایل تکراری توقف شد. ابر و گزارش را بررسی کنید.");
            }
            cloud.verify(existing.getLong("id"), pending.getString("hash"), pending.getLong("size"));
            remember(database, path, pending.getString("hash"), pending.getLong("size"), existing.getLong("id"), parent);
            if (pending.getString("hash").equals(hash)) return;
            previous = cached(database, path);
        }
        if (existing != null) {
            if (previous == null || existing.getLong("id") != previous.getLong("id")) {
                throw new UserError("فایل هم‌نام خارج از سابقهٔ این نصب وجود دارد؛ برای حفظ آن، ارسال متوقف شد.");
            }
            cloud.verify(existing.getLong("id"), previous.getString("hash"), previous.getLong("size"));
            long history = cloud.folder("_abre-android-data", root);
            history = cloud.folder("history", history);
            history = cloud.folder(Long.toString(existing.getLong("id")), history);
            JSONObject conflict = cloud.named(history, name);
            if (conflict != null && conflict.getLong("id") != existing.getLong("id")) {
                throw new UserError("نام نسخهٔ تاریخی تعارض دارد؛ هیچ فایل حذف نشد.");
            }
            cloud.move(existing.getLong("id"), history, name);
        }
        JSONObject uploaded = cloud.upload(temporary, name, parent, uploadKey -> {
            JSONObject journal = new JSONObject().put("hash", hash).put("size", temporary.length())
                .put("parent", parent).put("key", uploadKey);
            ContentValues values = new ContentValues();
            values.put("path", path); values.put("data", journal.toString());
            database.insertOrThrow("pending", null, values);
        });
        cloud.verify(uploaded.getLong("id"), hash, temporary.length());
        remember(database, path, hash, temporary.length(), uploaded.getLong("id"), parent);
    }

    public void run(boolean preview) {
        if (!RUNNING.compareAndSet(false, true)) return;
        File temporary = new File(context.getCacheDir(), "snapshot.part");
        try {
            JSONArray roots = new JSONArray(store.preferences.getString("folders", "[]"));
            List<Source> files = new ArrayList<>();
            Set<String> rootNames = new HashSet<>();
            store.report(preview ? "در حال پیش‌نمایش محلی…" : "در حال بررسی پوشه‌ها…");
            for (int index = 0; index < roots.length(); index++) {
                Uri tree = Uri.parse(roots.getString(index));
                String name = documentName(context, tree);
                if (!rootNames.add(name)) throw new UserError("دو پوشهٔ اصلی هم‌نام انتخاب شده‌اند؛ یکی را حذف کنید.");
                if (name.equals("_abre-android-data")) throw new UserError("نام پوشه برای تاریخچه رزرو شده است.");
                scan(tree, DocumentsContract.getTreeDocumentId(tree), name, 0, files, new HashSet<>());
            }
            long limit = Math.min(100, Math.max(1, store.preferences.getInt("maxMiB", 50))) * 1024L * 1024;
            int completed = 0, errors = 0;
            store.preferences.edit().putInt("total", files.size()).putInt("done", 0).apply();
            String rootName = Policy.name(store.preferences.getString("remoteRoot", "Android"));
            try (SQLiteDatabase database = context.openOrCreateDatabase("backup.db", Context.MODE_PRIVATE, null)) {
                database.execSQL("CREATE TABLE IF NOT EXISTS objects (path TEXT PRIMARY KEY, data TEXT NOT NULL)");
                database.execSQL("CREATE TABLE IF NOT EXISTS pending (path TEXT PRIMARY KEY, data TEXT NOT NULL)");
                String savedRoot = store.preferences.getString("indexedRoot", "");
                if (!preview && !savedRoot.isEmpty() && !savedRoot.equals(rootName)) throw new UserError("برای حفظ سابقه، نام ریشه پس از اولین ارسال ثابت است.");
                long remote = preview || files.isEmpty() ? 0 : cloud.folder(rootName, null);
                if (!preview && !files.isEmpty()) store.preferences.edit().putString("indexedRoot", rootName).commit();
                for (Source source : files) {
                    cloud.check();
                    store.preferences.edit().putString("current", source.relative).apply();
                    try {
                        String hash = snapshot(source, temporary, limit);
                        if (!preview) send(database, source, temporary, hash, remote);
                    } catch (Exception error) {
                        errors++;
                        store.report("✕ " + source.relative + " · " + friendly(error));
                    } finally { temporary.delete(); }
                    store.preferences.edit().putInt("done", ++completed).apply();
                }
            }
            store.report((preview ? "پیش‌نمایش" : "بررسی و بکاپ") + " تمام شد: " + completed + " فایل، " + errors + " خطا. «تلاش مجدد» فایل‌های موفق را رد می‌کند.");
        } catch (Exception error) {
            store.report("✕ " + friendly(error));
        } finally {
            temporary.delete();
            RUNNING.set(false);
            store.preferences.edit().putString("current", "").apply();
        }
    }

    public static String friendly(Exception error) {
        if (error instanceof SecurityException) return "دسترسی پوشه لغو شده است؛ دوباره پوشه را انتخاب کنید.";
        if (error instanceof org.json.JSONException) return "ساختار پاسخ ابر تغییر کرده است؛ عملیات تأیید نشد.";
        if (error instanceof java.net.SocketTimeoutException) return "زمان ارتباط تمام شد؛ نوبت بعد دوباره بررسی می‌شود.";
        if (error instanceof UserError) return error.getMessage();
        if (error instanceof IOException) return "خواندن فایل یا ارتباط ناموفق بود؛ دوباره تلاش کنید.";
        return "عملیات ناموفق بود (" + error.getClass().getSimpleName() + ")؛ گزارش را بررسی کنید.";
    }
}
