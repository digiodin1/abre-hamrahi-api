package ir.abrehamrahi.backup.community;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.URL;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

public final class CloudChecks {
    private static int assertions;

    private static void expect(boolean condition, String message) {
        assertions++;
        if (!condition) throw new AssertionError(message);
    }

    private interface Checked { void run() throws Exception; }

    private static void rejected(Checked work) throws Exception {
        boolean failed = false;
        try { work.run(); }
        catch (UserError error) { failed = true; }
        expect(failed, "Unsafe response or input was accepted");
    }

    private static final class Reply extends HttpURLConnection {
        final int status;
        final byte[] body;
        final ByteArrayOutputStream output = new ByteArrayOutputStream();
        Reply(String url, int status, byte[] body) throws Exception {
            super(new URL(url)); this.status = status; this.body = body;
        }
        @Override public void disconnect() {}
        @Override public boolean usingProxy() { return false; }
        @Override public void connect() {}
        @Override public int getResponseCode() { return status; }
        @Override public InputStream getInputStream() { return new ByteArrayInputStream(body); }
        @Override public InputStream getErrorStream() { return new ByteArrayInputStream(body); }
        @Override public OutputStream getOutputStream() { return output; }
        @Override public String getHeaderField(String key) { return "ETag".equals(key) ? "\"synthetic-etag\"" : null; }
        JSONObject sent() throws Exception { return new JSONObject(output.toString("UTF-8")); }
    }

    private static final class Fixture implements Cloud.Sessions, Cloud.Connections {
        final ArrayDeque<Reply> queued = new ArrayDeque<>();
        final List<Reply> called = new ArrayList<>();
        JSONObject tokens = new JSONObject().put("access", "synthetic-access").put("refresh", "synthetic-refresh");
        Fixture() throws Exception {}
        @Override public JSONObject session() { return tokens; }
        @Override public void saveSession(JSONObject value) { tokens = value; }
        @Override public HttpURLConnection open(String url) {
            Reply reply = queued.remove();
            expect(reply.getURL().toString().equals(url), "Unexpected API route or unapproved network destination");
            called.add(reply); return reply;
        }
        Reply add(String path, int status, String body) throws Exception {
            return addBytes(path, status, body.getBytes("UTF-8"));
        }
        Reply addBytes(String path, int status, byte[] body) throws Exception {
            Reply reply = new Reply(path.startsWith("https://") ? path : "https://abrehamrahi.ir/api/" + path, status, body);
            queued.add(reply); return reply;
        }
        Cloud cloud() { return new Cloud(this, this, value -> Policy.transferUri(value)); }
    }

    private static String listing(String items) { return "{\"results\":" + items + ",\"next\":null}"; }

    private static void namesAndDestinations() throws Exception {
        expect(Policy.name("گزارش اصلی ۱.txt").equals("گزارش اصلی ۱.txt"), "Original Unicode name changed");
        for (String name : new String[]{"..", "folder/file", "folder\\file", "bad\nname", ""}) rejected(() -> Policy.name(name));
        for (String url : new String[]{"http://example.com/file", "https://user:pass@example.com/file", "https://example.com:22/file", "https://example.com/file#fragment"}) {
            rejected(() -> Policy.transferUri(url));
        }
        for (String address : new String[]{"127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "198.18.0.1", "198.51.100.1", "203.0.113.1", "::1", "fc00::1", "fe80::1", "2001:db8::1", "2002:7f00:1::"}) {
            expect(!Policy.publicAddress(InetAddress.getByName(address)), "Private or reserved transfer address accepted");
        }
        expect(Policy.publicAddress(InetAddress.getByName("8.8.8.8")), "Public IPv4 rejected");
        expect(Policy.publicAddress(InetAddress.getByName("2606:4700:4700::1111")), "Public IPv6 rejected");
        expect(!Policy.excluded(".env") && !Policy.excluded("release.jks") && !Policy.excluded("session.bin"), "Selected content must not be silently excluded");
        expect(!Policy.excluded("گزارش.pdf"), "Ordinary document excluded");
    }

    private static void folders() throws Exception {
        String list = "v2/flat/list-objects/?is_trash=false&recursive=false&limit=1000&offset=0";
        Fixture existing = new Fixture();
        existing.add(list, 200, listing("[{\"id\":7,\"name\":\"اصل\",\"parent\":null,\"key\":null}]"));
        expect(existing.cloud().folder("اصل", null) == 7, "Null folder key must be accepted");
        Fixture conflict = new Fixture();
        conflict.add(list, 200, listing("[{\"id\":7,\"name\":\"اصل\",\"parent\":null},{\"id\":8,\"name\":\"اصل\",\"parent\":null}]"));
        rejected(() -> conflict.cloud().folder("اصل", null));
        Fixture ambiguous = new Fixture();
        ambiguous.add(list, 200, listing("[]"));
        ambiguous.add("v2/flat/create-folder/", 503, "{}");
        ambiguous.add(list, 200, listing("[{\"id\":9,\"name\":\"اصل\",\"parent\":null,\"key\":null}]"));
        expect(ambiguous.cloud().folder("اصل", null) == 9, "Ambiguous create must reconcile by reading");
        expect(ambiguous.called.size() == 3, "Folder mutation was blindly replayed");
        Fixture renamed = new Fixture();
        renamed.add(list, 200, listing("[]"));
        renamed.add("v2/flat/create-folder/", 200, "{\"id\":10,\"name\":\"اصل (1)\",\"parent\":null}");
        rejected(() -> renamed.cloud().folder("اصل", null));
    }

    private static void transfers() throws Exception {
        File file = File.createTempFile("abre-synthetic-", ".txt");
        byte[] contents = "synthetic bytes".getBytes("UTF-8");
        Files.write(file.toPath(), contents);
        String hash = Policy.hex(MessageDigest.getInstance("SHA-256").digest(contents));
        try {
            Fixture fixture = new Fixture();
            fixture.add("v2/flat/start-upload/", 200, "{\"signed_urls\":[\"https://storage.example.invalid/put\"],\"chunk_size\":1000,\"key\":\"synthetic-key\",\"upload_id\":\"synthetic-upload\"}");
            Reply part = fixture.add("https://storage.example.invalid/put", 200, "");
            String uploaded = "{\"id\":21,\"name\":\"اصل.txt\",\"parent\":7,\"key\":\"final-key\",\"size\":" + contents.length + "}";
            Reply complete = fixture.add("v2/flat/complete-upload/", 200, uploaded);
            fixture.add("v2/flat/get-object/21/", 200, uploaded);
            fixture.add("v2/flat/get-object/21/", 200, "{\"id\":21,\"download_url\":\"https://storage.example.invalid/get\"}");
            Reply download = fixture.addBytes("https://storage.example.invalid/get", 200, contents);
            boolean[] journal = {false};
            Cloud cloud = fixture.cloud();
            cloud.upload(file, "اصل.txt", 7, key -> { expect(key.equals("synthetic-key"), "Journal key mismatch"); journal[0] = true; });
            cloud.verify(21, hash, contents.length);
            expect(journal[0], "Completion has no recovery journal");
            expect(!complete.sent().getBoolean("force_overwrite"), "Overwrite must remain disabled");
            expect(complete.sent().getString("name").equals("اصل.txt"), "Upload changed the original filename");
            expect(part.getRequestProperty("Authorization") == null, "Bearer leaked to signed storage upload");
            expect(download.getRequestProperty("Authorization") == null, "Bearer leaked to signed storage download");
            expect(java.util.Arrays.equals(part.output.toByteArray(), contents), "Transferred bytes differ");
            Fixture overwrite = new Fixture();
            JSONObject original = new JSONObject().put("id", 11).put("name", "اصل.txt").put("parent", 7)
                .put("size", 3).put("key", "previous-key").put("version_group", "synthetic-group");
            overwrite.add("v2/flat/start-upload/", 200, "{\"signed_urls\":[\"https://storage.example.invalid/put\"],\"chunk_size\":1000,\"key\":\"staging-key\",\"upload_id\":\"synthetic-upload\"}");
            overwrite.add("https://storage.example.invalid/put", 200, "");
            overwrite.add("v2/flat/get-object/11/", 200, original.toString());
            Reply replacement = overwrite.add("v2/flat/complete-upload/", 200, uploaded);
            overwrite.add("v2/flat/get-object/21/", 200, uploaded);
            overwrite.cloud().upload(file, "اصل.txt", 7, key -> {}, original);
            expect(replacement.sent().getBoolean("force_overwrite"), "Indexed replacement must use native overwrite");
            expect("synthetic-group".equals(replacement.sent().getString("version_group")), "Native version group lost");
            expect(overwrite.queued.isEmpty(), "Fresh finalized metadata was not checked");
            Fixture corrupt = new Fixture();
            corrupt.add("v2/flat/get-object/21/", 200, "{\"id\":21,\"download_url\":\"https://storage.example.invalid/get\"}");
            corrupt.addBytes("https://storage.example.invalid/get", 200, "corrupt".getBytes("UTF-8"));
            rejected(() -> corrupt.cloud().verify(21, hash, contents.length));
            Fixture interrupted = new Fixture();
            interrupted.add("v2/flat/start-upload/", 200, "{\"signed_urls\":[\"https://storage.example.invalid/put\"],\"chunk_size\":1000,\"key\":\"key\",\"upload_id\":\"upload\"}");
            interrupted.add("https://storage.example.invalid/put", 503, "");
            boolean[] premature = {false};
            rejected(() -> interrupted.cloud().upload(file, "اصل.txt", 7, key -> premature[0] = true));
            expect(!premature[0], "Failed part must not hold an unattempted completion");
            expect(interrupted.called.size() == 2, "Upload mutation was replayed");
            Fixture uncertain = new Fixture();
            uncertain.add("v2/flat/start-upload/", 200, "{\"signed_urls\":[\"https://storage.example.invalid/put\"],\"chunk_size\":1000,\"key\":\"key\",\"upload_id\":\"upload\"}");
            uncertain.add("https://storage.example.invalid/put", 200, "");
            uncertain.add("v2/flat/complete-upload/", 503, "{}");
            boolean[] persisted = {false};
            rejected(() -> uncertain.cloud().upload(file, "اصل.txt", 7, key -> persisted[0] = true));
            expect(persisted[0] && uncertain.called.size() == 3, "Ambiguous completion must retain its journal without another completion");
        } finally { file.delete(); }
    }

    private static void refreshAndAmbiguousMove() throws Exception {
        Fixture refresh = new Fixture();
        refresh.add("v6/profile/auth/get-profile/", 401, "{}");
        refresh.add("v2/profile/auth/token-refresh/", 200, "{\"access\":\"new-access\",\"refresh\":\"new-refresh\"}");
        Reply after = refresh.add("v6/profile/auth/get-profile/", 200, "{}");
        refresh.cloud().json("GET", "v6/profile/auth/get-profile/", null, true);
        expect("Bearer new-access".equals(after.getRequestProperty("Authorization")), "Refreshed session was not used");
        Fixture move = new Fixture();
        move.add("v2/flat/get-object/12/", 200, "{\"id\":12,\"name\":\"اصل.txt\",\"parent\":7,\"key\":\"key\",\"size\":3}");
        move.add("v2/rgw/move-object/", 503, "{}");
        move.add("v2/flat/get-object/12/", 200, "{\"id\":12,\"name\":\"اصل.txt\",\"parent\":8,\"key\":\"key\",\"size\":3}");
        move.cloud().move(12, 8, "اصل.txt");
        expect(move.called.size() == 3, "Ambiguous move must reconcile without another mutation");
    }

    private static void schedulesAndTrash() throws Exception {
        long now = System.currentTimeMillis();
        JSONObject instant = SyncPlan.configure("instant", "02:00", now);
        JSONObject daily = SyncPlan.configure("daily", "02:00", now);
        expect(SyncPlan.due(instant, now), "Instant folder was disabled");
        expect(!SyncPlan.due(daily, now), "Future daily folder ran early");
        expect(SyncPlan.due(new JSONObject(daily.toString()), daily.getLong("next")), "Persisted overdue folder was skipped");
        rejected(() -> SyncPlan.configure("daily", "24:00", now));
        rejected(() -> SyncPlan.configure("unknown", "02:00", now));
        expect(SyncPlan.ratio(10000, 100) == 1, "Volume ratio uses wrong direction");
        expect(SyncPlan.ratio(100, 10004) < 1, "Volume ratio exceeds 100");
        expect(SyncPlan.ratio(0, 0) == 100 && SyncPlan.ratio(1, 0) == 0, "Empty volume rules differ");
        expect(SyncPlan.percent(99.9999).equals("99.9%"), "Unequal volumes rounded to 100");
        expect(SyncPlan.bytes(1048576).equals("1.0 MiB"), "Byte units unclear");
        JSONObject original = new JSONObject().put("id", 12).put("name", "اصل.txt").put("parent", 7).put("key", "key").put("size", 3);
        Fixture trash = new Fixture();
        trash.add("v2/flat/get-object/12/", 200, original.toString());
        Reply deletion = trash.add("v2/rgw/trash-objects/", 500, "{}");
        trash.add("v2/flat/get-object/12/", 200, new JSONObject(original.toString()).put("is_trash", true).toString());
        trash.cloud().trash(original);
        expect("DELETE".equals(deletion.getRequestMethod()), "Wrong soft-trash method");
        expect(deletion.sent().getJSONArray("obj_ids").getLong(0) == 12, "Wrong trash target");
        expect(trash.called.size() == 3, "Ambiguous trash mutation was replayed");
        Fixture changed = new Fixture();
        changed.add("v2/flat/get-object/12/", 200, new JSONObject(original.toString()).put("size", 4).toString());
        rejected(() -> changed.cloud().trash(original));
        expect(changed.called.size() == 1, "Changed cloud file was deleted");
        Fixture cache = new Fixture();
        String list = "v2/flat/list-objects/?is_trash=false&recursive=false&limit=1000&offset=0&parent=7";
        cache.add(list, 200, listing("[" + original + "]"));
        Cloud cloud = cache.cloud(); cloud.beginSync();
        cloud.named(7L, "اصل.txt"); cloud.named(7L, "اصل.txt");
        expect(cache.called.size() == 1, "Per-file listing defeats folder caching");
    }

    public static void main(String[] args) throws Exception {
        namesAndDestinations(); folders(); transfers(); refreshAndAmbiguousMove(); schedulesAndTrash();
        System.out.println("Android policy and cloud adapter checks passed: " + assertions + " assertions; no real account or network.");
    }
}
