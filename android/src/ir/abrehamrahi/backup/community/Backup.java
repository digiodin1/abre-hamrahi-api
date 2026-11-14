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
    private final Set<String> localDirectories = new HashSet<>();
    private final Set<Long> countedDirectories = new HashSet<>();
    private final Set<Long> prunedDirectories = new HashSet<>();

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
        if (depth > 512) throw new UserError("عمق پوشه بیش از حد قابل پیمایش است.");
        localDirectories.add(relative);
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

    private void remember(SQLiteDatabase database, String path, String hash, long size, long identifier, long parent, String key) throws Exception {
        JSONObject record = new JSONObject().put("hash", hash).put("size", size).put("id", identifier).put("parent", parent).put("key", key);
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
        int separator = path.lastIndexOf('/');
        String name = path.substring(separator + 1);
        long parent = directory(path.substring(0, separator), root);
        JSONObject pending = null;
        try (Cursor cursor = database.rawQuery("SELECT data FROM pending WHERE path=?", new String[]{path})) {
            if (cursor.moveToFirst()) pending = new JSONObject(cursor.getString(0));
        }
        JSONObject existing = cloud.named(parent, name);
        if (previous != null && hash.equals(previous.optString("hash")) && existing != null &&
                previous.getLong("id") == existing.getLong("id") && temporary.length() == existing.optLong("size", -1) &&
                previous.optString("key").equals(existing.optString("key"))) return;
        if (pending != null) {
            if (existing != null && existing.optLong("size", -1) == pending.getLong("size") &&
                    (previous == null || previous.getLong("id") != existing.getLong("id"))) {
                cloud.verify(existing.getLong("id"), pending.getString("hash"), pending.getLong("size"));
                remember(database, path, pending.getString("hash"), pending.getLong("size"), existing.getLong("id"), parent, existing.getString("key"));
                if (pending.getString("hash").equals(hash)) return;
                previous = cached(database, path);
            } else if (existing == null || (previous != null && previous.getLong("id") == existing.getLong("id"))) {
                database.delete("pending", "path=?", new String[]{path});
            } else {
                throw new UserError("نتیجهٔ ارسال قبلی نامعلوم است؛ تعارض مقصد باید بررسی شود.");
            }
        }
        if (existing != null) {
            if (previous == null || existing.getLong("id") != previous.getLong("id")) {
                if (existing.optLong("size", -1) != temporary.length()) throw new UserError("فایل هم‌نام با محتوای متفاوت وجود دارد.");
                cloud.verify(existing.getLong("id"), hash, temporary.length());
                remember(database, path, hash, temporary.length(), existing.getLong("id"), parent, existing.getString("key"));
                return;
            }
            if (existing.optLong("size", -1) != previous.getLong("size")) throw new UserError("فایل مقصد خارج از برنامه تغییر کرده است.");
            if (!previous.has("key")) cloud.verify(existing.getLong("id"), previous.getString("hash"), previous.getLong("size"));
            else if (!previous.getString("key").equals(existing.optString("key"))) throw new UserError("نسخهٔ مقصد خارج از برنامه تغییر کرده است.");
        }
        JSONObject uploaded = cloud.upload(temporary, name, parent, uploadKey -> {
            JSONObject journal = new JSONObject().put("hash", hash).put("size", temporary.length())
                .put("parent", parent).put("key", uploadKey);
            ContentValues values = new ContentValues();
            values.put("path", path); values.put("data", journal.toString());
            database.insertWithOnConflict("pending", null, values, SQLiteDatabase.CONFLICT_REPLACE);
        }, existing);
        remember(database, path, hash, temporary.length(), uploaded.getLong("id"), parent, uploaded.getString("key"));
    }

    private void record(SQLiteDatabase database, Source source, String status, String error) throws Exception {
        JSONObject indexed = cached(database, source.relative);
        JSONObject item = new JSONObject().put("path", source.relative).put("uri", source.uri.toString())
            .put("size", source.size).put("status", status).put("error", error)
            .put("cloudBytes", indexed == null ? JSONObject.NULL : indexed.opt("size"))
            .put("remoteId", indexed == null ? JSONObject.NULL : indexed.opt("id"));
        ContentValues values = new ContentValues();
        values.put("path", source.relative); values.put("data", item.toString());
        database.insertWithOnConflict("run_items", null, values, SQLiteDatabase.CONFLICT_REPLACE);
    }

    private long cloudSize(long parent) throws Exception {
        if (!countedDirectories.add(parent)) throw new UserError("ساختار پوشهٔ ابری چرخه دارد.");
        long bytes = 0;
        for (JSONObject item : cloud.listing(parent)) {
            bytes = Math.addExact(bytes, item.isNull("key") || item.optString("key").isEmpty() ?
                cloudSize(item.getLong("id")) : item.getLong("size"));
        }
        return bytes;
    }

    private void prune(SQLiteDatabase database, long parent, String relative, Set<String> local, Uri tree) throws Exception {
        if (!prunedDirectories.add(parent)) throw new UserError("ساختار پوشهٔ ابری چرخه دارد؛ حذف متوقف شد.");
        for (JSONObject item : new ArrayList<>(cloud.listing(parent))) {
            cloud.check();
            String path = relative + "/" + Policy.name(item.getString("name"));
            boolean folder = item.isNull("key") || item.optString("key").isEmpty();
            if (folder) prune(database, item.getLong("id"), path, local, tree);
            if (local.contains(path)) continue;
            documentName(context, tree);
            if (folder && !cloud.list(item.getLong("id")).isEmpty()) continue;
            cloud.trash(item);
            database.delete("objects", "path=?", new String[]{path});
            database.delete("pending", "path=?", new String[]{path});
            store.report("✓ سطل زباله · " + path);
        }
    }

    public void run(boolean preview) {
        run(preview, false);
    }

    public void run(boolean preview, boolean automatic) {
        run(preview, automatic, null);
    }

    public void run(boolean preview, boolean automatic, String onlyPath) {
        if (!RUNNING.compareAndSet(false, true)) return;
        File temporary = new File(context.getCacheDir(), "snapshot.part");
        try {
            directories.clear();
            localDirectories.clear();
            countedDirectories.clear(); prunedDirectories.clear();
            JSONArray roots = new JSONArray(store.preferences.getString("folders", "[]"));
            List<Source> files = new ArrayList<>();
            Set<String> rootNames = new HashSet<>();
            store.report(preview ? "در حال پیش‌نمایش محلی…" : "در حال بررسی پوشه‌ها…");
            Map<String, Uri> selected = new java.util.LinkedHashMap<>();
            Map<String, JSONObject> selectedPlans = new HashMap<>();
            JSONObject plans = new JSONObject(store.preferences.getString("folderPlans", "{}"));
            int scanFailures = 0;
            for (int index = 0; index < roots.length(); index++) {
                Uri tree = Uri.parse(roots.getString(index));
                JSONObject plan = plans.optJSONObject(tree.toString());
                if (automatic && !SyncPlan.due(plan, System.currentTimeMillis())) continue;
                String name;
                try { name = documentName(context, tree); }
                catch (Exception error) { scanFailures++; store.report("✕ " + friendly(error)); continue; }
                if (!rootNames.add(name)) throw new UserError("دو پوشهٔ اصلی هم‌نام انتخاب شده‌اند؛ یکی را حذف کنید.");
                if (name.equals("_abre-android-data")) throw new UserError("نام پوشه برای تاریخچه رزرو شده است.");
                int before = files.size();
                try { scan(tree, DocumentsContract.getTreeDocumentId(tree), name, 0, files, new HashSet<>()); }
                catch (Exception error) {
                    scanFailures++; store.report("✕ " + name + " · " + friendly(error));
                    files.subList(before, files.size()).clear();
                    localDirectories.removeIf(path -> path.equals(name) || path.startsWith(name + "/"));
                    continue;
                }
                selected.put(name, tree);
                if (plan != null) selectedPlans.put(tree.toString(), new JSONObject(plan.toString()));
            }
            if (selected.isEmpty()) return;
            if (onlyPath != null) {
                files.removeIf(source -> !source.relative.equals(onlyPath));
                if (files.isEmpty()) throw new UserError("فایل برای تلاش مجدد در پوشه‌های انتخاب‌شده موجود نیست.");
            }
            long limit = store.preferences.getInt("maxMiB", 0) == 0 ? Long.MAX_VALUE :
                store.preferences.getInt("maxMiB", 0) * 1024L * 1024;
            int completed = 0, errors = scanFailures;
            long totalBytes = 0, completedBytes = 0;
            for (Source source : files) totalBytes = Math.addExact(totalBytes, Math.max(0, source.size));
            store.preferences.edit().putLong("totalBytes", totalBytes).putLong("doneBytes", 0).apply();
            Set<String> failedRoots = new HashSet<>();
            store.preferences.edit().putInt("total", files.size()).putInt("done", 0).apply();
            String rootName = Policy.name(store.preferences.getString("remoteRoot", "Android"));
            try (SQLiteDatabase database = context.openOrCreateDatabase("backup.db", Context.MODE_PRIVATE, null)) {
                database.enableWriteAheadLogging();
                database.execSQL("CREATE TABLE IF NOT EXISTS objects (path TEXT PRIMARY KEY, data TEXT NOT NULL)");
                database.execSQL("CREATE TABLE IF NOT EXISTS pending (path TEXT PRIMARY KEY, data TEXT NOT NULL)");
                database.execSQL("CREATE TABLE IF NOT EXISTS run_items (path TEXT PRIMARY KEY, data TEXT NOT NULL)");
                for (Source source : files) record(database, source, "queued", "");
                String savedRoot = store.preferences.getString("indexedRoot", "");
                if (!preview && !savedRoot.isEmpty() && !savedRoot.equals(rootName)) throw new UserError("برای حفظ سابقه، نام ریشه پس از اولین ارسال ثابت است.");
                cloud.beginSync();
                long remote = preview ? 0 : cloud.folder(rootName, null);
                if (!preview) {
                    store.preferences.edit().putString("indexedRoot", rootName).commit();
                    for (String directory : localDirectories) directory(directory, remote);
                }
                for (Source source : files) {
                    cloud.check();
                    store.preferences.edit().putString("current", source.relative).apply();
                    try {
                        JSONObject previous = !preview ? cached(database, source.relative) : null;
                        boolean unchanged = false;
                        if (automatic && previous != null && source.modified > 0 && source.size >= 0 &&
                                previous.optLong("modified", -1) == source.modified && previous.getLong("size") == source.size &&
                                source.uri.toString().equals(previous.optString("uri"))) {
                            int separator = source.relative.lastIndexOf('/');
                            JSONObject existing = cloud.named(directory(source.relative.substring(0, separator), remote), source.relative.substring(separator + 1));
                            unchanged = existing != null && existing.getLong("id") == previous.getLong("id") &&
                                existing.optLong("size", -1) == source.size && previous.getString("key").equals(existing.optString("key"));
                        }
                        if (!unchanged) {
                            String hash = snapshot(source, temporary, limit);
                            if (source.size < 0) {
                                source.size = temporary.length();
                                totalBytes = Math.addExact(totalBytes, source.size);
                                store.preferences.edit().putLong("totalBytes", totalBytes).apply();
                            }
                            if (!preview) {
                                send(database, source, temporary, hash, remote);
                                JSONObject saved = cached(database, source.relative);
                                saved.put("modified", source.modified).put("uri", source.uri.toString());
                                ContentValues values = new ContentValues();
                                values.put("data", saved.toString());
                                database.update("objects", values, "path=?", new String[]{source.relative});
                            }
                        }
                        completedBytes = Math.addExact(completedBytes, Math.max(0, source.size));
                        record(database, source, preview ? "preview" : "ok", "");
                    } catch (Exception error) {
                        errors++;
                        failedRoots.add(source.relative.substring(0, source.relative.indexOf('/')));
                        store.report("✕ " + source.relative + " · " + friendly(error));
                        record(database, source, "error", friendly(error));
                    } finally { temporary.delete(); }
                    store.preferences.edit().putInt("done", ++completed).apply();
                    store.preferences.edit().putLong("doneBytes", completedBytes).apply();
                }
                if (!preview && onlyPath == null) {
                    long remoteBytes = 0;
                    for (Map.Entry<String, Uri> entry : selected.entrySet()) {
                        String name = entry.getKey();
                        Uri tree = entry.getValue();
                        long parent = directory(name, remote);
                        if (!failedRoots.contains(name)) {
                            try {
                                List<Source> refreshed = new ArrayList<>();
                                scan(tree, DocumentsContract.getTreeDocumentId(tree), name, 0, refreshed, new HashSet<>());
                                Set<String> present = new HashSet<>(localDirectories);
                                for (Source source : refreshed) present.add(source.relative);
                                prune(database, parent, name, present, tree);
                                JSONObject captured = selectedPlans.get(tree.toString());
                                if (captured != null && "daily".equals(captured.optString("mode"))) {
                                    synchronized (Store.PLAN_LOCK) {
                                        JSONObject latest = new JSONObject(store.preferences.getString("folderPlans", "{}"));
                                        JSONObject active = latest.optJSONObject(tree.toString());
                                        if (active != null && active.toString().equals(captured.toString())) {
                                            active.put("next", SyncPlan.next(active.getString("time"), System.currentTimeMillis()));
                                            store.preferences.edit().putString("folderPlans", latest.toString()).commit();
                                        }
                                    }
                                }
                            } catch (Exception error) { errors++; store.report("✕ " + name + " · " + friendly(error)); }
                        }
                        long folderCloudBytes = cloudSize(parent);
                        remoteBytes = Math.addExact(remoteBytes, folderCloudBytes);
                        long localBytes = 0;
                        for (Source source : files) if (source.relative.startsWith(name + "/")) localBytes += Math.max(0, source.size);
                        store.preferences.edit().putLong("local:" + tree, localBytes).putLong("cloud:" + tree, folderCloudBytes).apply();
                    }
                    store.preferences.edit().putLong("cloudBytes", remoteBytes).apply();
                    try {
                        JSONObject quota = cloud.json("GET", "v5/rgw/get-quota/", null, true);
                        long capacity = Math.addExact(quota.getLong("total_plan"), quota.getLong("total_gift"));
                        long used = quota.getLong("used");
                        if (capacity < 0 || used < 0 || quota.getLong("total_plan") < 0 || quota.getLong("total_gift") < 0) throw new UserError("ظرفیت حساب معتبر نیست.");
                        store.preferences.edit().putLong("capacity", capacity).putLong("used", used).putLong("quotaAt", System.currentTimeMillis()).apply();
                    } catch (Exception error) { store.report("ظرفیت: " + friendly(error)); }
                }
            }
            store.report((preview ? "پیش‌نمایش" : "سینک") + " تمام شد: " + completed + " فایل، " + errors + " خطا.");
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
