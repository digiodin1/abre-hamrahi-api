package ir.abrehamrahi.backup.community;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.TimePickerDialog;
import android.content.Intent;
import android.content.ActivityNotFoundException;
import android.content.pm.PackageManager;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.Base64;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private Store store;
    private LinearLayout content;
    private TextView status, reports, current, counts, quotaDetail;
    private ProgressBar progress;
    private String page = "status";
    private JSONObject captcha = new JSONObject();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private boolean operation;
    private Backup activeBackup;
    private Typeface regular, bold;
    private GaugeView capacityGauge, volumeGauge;

    private interface Task { String run() throws Exception; }

    private int dp(int value) { return (int)(value * getResources().getDisplayMetrics().density + 0.5f); }

    private GradientDrawable surface(int color, int radius) {
        GradientDrawable shape = new GradientDrawable(); shape.setColor(color); shape.setCornerRadius(dp(radius));
        shape.setStroke(dp(1), Color.argb(24, 20, 65, 95)); return shape;
    }

    private void styleButton(Button button, boolean primary) {
        button.setAllCaps(false); button.setTextSize(14); button.setMinHeight(dp(48));
        button.setTypeface(primary ? bold : regular); button.setTextColor(primary ? Color.WHITE : Color.rgb(22, 76, 112));
        button.setBackground(surface(primary ? Color.rgb(0, 145, 185) : Color.WHITE, 14));
        button.setPadding(dp(14), dp(8), dp(14), dp(8));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2); params.setMargins(0, dp(5), 0, dp(5));
        button.setLayoutParams(params);
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        store = new Store(this);
        regular = Typeface.createFromAsset(getAssets(), "fonts/iransansx-regular.ttf");
        bold = Typeface.createFromAsset(getAssets(), "fonts/iransansx-bold.ttf");
        if (!store.preferences.contains("remoteRoot")) {
            String model = Build.MODEL.replaceAll("[^\\p{L}\\p{N} _-]", "_");
            store.preferences.edit().putString("remoteRoot", "Android " + model).apply();
        }
        show("status");
    }

    @Override public void onResume() {
        super.onResume();
        handler.post(update);
        worker.submit(() -> {
            JSONObject info = Update.check(this, false);
            if (info.optBoolean("available")) handler.post(() -> {
                if (isFinishing() || isDestroyed()) return;
                boolean required = Update.required(this);
                new AlertDialog.Builder(this).setTitle(required ? "به‌روزرسانی اجباری" : "نسخهٔ جدید آماده است")
                    .setMessage("نسخهٔ " + info.optString("version") + (required ? "؛ مهلت ۳۰روزه پایان یافته و شروع سینک جدید متوقف است." : "؛ مهلت نصب: " + android.text.format.DateFormat.format("yyyy/MM/dd", info.optLong("deadline"))))
                    .setCancelable(!required).setPositiveButton("به‌روزرسانی", (dialog, which) -> show("settings"))
                    .setNegativeButton(required ? "بستن" : "بعداً", (dialog, which) -> { if(required) finish(); }).show();
            });
        });
    }

    @Override public void onPause() {
        handler.removeCallbacks(update);
        super.onPause();
    }

    @Override public void onDestroy() {
        if (activeBackup != null) activeBackup.cloud.cancel();
        worker.shutdownNow();
        super.onDestroy();
    }

    private final Runnable update = new Runnable() {
        @Override public void run() {
            if (status != null) status.setText(store.preferences.getString("status", "آماده"));
            if (reports != null) reports.setText(store.preferences.getString("reports", "هنوز گزارشی نیست."));
            int total = store.preferences.getInt("total", 0), done = store.preferences.getInt("done", 0);
            long totalBytes = store.preferences.getLong("totalBytes", 0), doneBytes = store.preferences.getLong("doneBytes", 0);
            int percent = totalBytes == 0 ? (total > 0 && done == total ? 100 : 0) : (int)Math.min(100, 100.0 * doneBytes / totalBytes);
            if (progress != null) {
                progress.setIndeterminate(Backup.RUNNING.get() && total == 0);
                progress.setProgress(percent);
            }
            if (counts != null) counts.setText(SyncPlan.bytes(doneBytes) + " / " + SyncPlan.bytes(totalBytes) + " · " + percent + "٪ · " + done + " / " + total + " فایل");
            if (volumeGauge != null) {
                long local = 0, remote = 0;
                boolean known = true;
                try {
                    JSONArray roots = new JSONArray(store.preferences.getString("folders", "[]"));
                    known = roots.length() > 0;
                    for (int index = 0; index < roots.length(); index++) {
                        String tree = roots.getString(index);
                        known &= store.preferences.contains("local:" + tree) && store.preferences.contains("cloud:" + tree);
                        local += store.preferences.getLong("local:" + tree, 0);
                        remote += store.preferences.getLong("cloud:" + tree, 0);
                    }
                } catch (Exception error) { known = false; }
                volumeGauge.set(known ? SyncPlan.ratio(local, remote) : -1, SyncPlan.bytes(remote) + " / " + SyncPlan.bytes(local));
            }
            if (capacityGauge != null) {
                long capacity = store.preferences.getLong("capacity", -1), used = store.preferences.getLong("used", 0);
                capacityGauge.set(capacity < 0 ? -1 : capacity == 0 ? 0 : 100.0 * Math.max(0, capacity - used) / capacity,
                    capacity < 0 ? "—" : SyncPlan.bytes(Math.max(0, capacity - used), 2));
                if (quotaDetail != null) {
                    long checkedAt = store.preferences.getLong("quotaAt", 0);
                    quotaDetail.setText(capacity < 0 ? "ظرفیت هنوز دریافت نشده." :
                        "مصرف‌شده: " + SyncPlan.bytes(used, 2) + " از " + SyncPlan.bytes(capacity, 2) +
                        " · آخرین بررسی: " + (checkedAt > 0 ? android.text.format.DateFormat.format("HH:mm:ss", checkedAt) : "—"));
                }
            }
            if (current != null) current.setText(store.preferences.getString("current", ""));
            handler.postDelayed(this, 1000);
        }
    };

    private TextView text(String value) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(16);
        view.setTypeface(regular);
        view.setPadding(8, 12, 8, 12);
        content.addView(view);
        return view;
    }

    private EditText field(String hint, int type) {
        EditText view = new EditText(this);
        view.setHint(hint);
        view.setTypeface(regular);
        view.setInputType(type);
        view.setTextSize(15); view.setPadding(dp(14), dp(10), dp(14), dp(10));
        view.setBackground(surface(Color.WHITE, 12));
        view.setSaveEnabled(false);
        view.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        content.addView(view);
        return view;
    }

    private Button button(String title, Runnable action) {
        Button button = new Button(this);
        button.setText(title);
        button.setTypeface(regular);
        button.setOnClickListener(view -> action.run());
        styleButton(button, title.contains("سینک") || title.contains("ورود") || title.contains("ذخیره") || title.contains("انتخاب"));
        content.addView(button);
        return button;
    }

    private void run(Task task) {
        if (operation) { alert("عملیات قبلی هنوز تمام نشده است."); return; }
        operation = true;
        worker.submit(() -> {
            String result;
            try { result = task.run(); }
            catch (Exception error) { result = Backup.friendly(error); }
            String message = result;
            handler.post(() -> {
                operation = false;
                if (!isFinishing() && !isDestroyed()) alert(message);
            });
        });
    }

    private void alert(String message) {
        if (!isFinishing() && !isDestroyed()) new AlertDialog.Builder(this).setMessage(message).setPositiveButton("باشه", null).show();
    }

    private void openCloud() {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://abrehamrahi.ir/drive/files")));
        } catch (ActivityNotFoundException error) {
            alert("مرورگری برای بازکردن سایت پیدا نشد.");
        }
    }

    private void show(String selected) {
        page = selected;
        status = null; reports = null; progress = null; counts = null; current = null; quotaDetail = null;
        capacityGauge = null; volumeGauge = null;
        LinearLayout screen = new LinearLayout(this);
        screen.setOrientation(LinearLayout.VERTICAL);
        screen.setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        screen.setBackgroundColor(Color.rgb(246, 249, 252));
        TextView title = new TextView(this);
        title.setText("Abre Hamrahi API · " + ReleasePolicy.VERSION);
        title.setTypeface(bold);
        title.setTextColor(Color.WHITE); title.setTextSize(21); title.setPadding(dp(20), dp(24), dp(20), dp(18));
        title.setBackground(surface(Color.rgb(16, 92, 137), 0));
        android.graphics.drawable.Drawable logo = getDrawable(R.drawable.cloud);
        logo.setBounds(0, 0, dp(36), dp(36));
        title.setCompoundDrawablesRelative(logo, null, null, null);
        title.setCompoundDrawablePadding(dp(12));
        title.setContentDescription("Abre Hamrahi API؛ بازکردن سایت ابرهمراهی");
        title.setOnClickListener(view -> openCloud());
        screen.addView(title);
        ScrollView navigation = new ScrollView(this);
        LinearLayout tabs = new LinearLayout(this);
        tabs.setOrientation(LinearLayout.HORIZONTAL);
        String[] keys = {"status", "account", "folders", "settings", "reports"};
        String[] names = {"پردازش", "حساب", "پوشه‌ها", "تنظیمات", "گزارشات"};
        for (int index = 0; index < keys.length; index++) {
            String key = keys[index];
            Button tab = new Button(this);
            tab.setText(names[index]); tab.setTextSize(12); tab.setAllCaps(false);
            tab.setTypeface(regular);
            tab.setEnabled(!key.equals(selected)); tab.setTextColor(key.equals(selected) ? Color.WHITE : Color.rgb(28, 83, 116));
            tab.setBackground(surface(key.equals(selected) ? Color.rgb(0, 145, 185) : Color.WHITE, 12));
            tab.setMinWidth(0); tab.setMinimumWidth(0); tab.setPadding(0, 0, 0, 0);
            tab.setOnClickListener(view -> show(key));
            LinearLayout.LayoutParams tabParams = new LinearLayout.LayoutParams(0, dp(48), 1); tabParams.setMargins(dp(4), dp(8), dp(4), dp(8));
            tabs.addView(tab, tabParams);
        }
        screen.addView(tabs);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(16, 12, 16, 24);
        navigation.addView(content);
        screen.addView(navigation, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(screen);
        if ("account".equals(selected)) account();
        else if ("folders".equals(selected)) folders();
        else if ("settings".equals(selected)) settings();
        else if ("reports".equals(selected)) {
            reports = text(store.preferences.getString("reports", "هنوز گزارشی نیست."));
            reports.setTextIsSelectable(true);
            button("تلاش مجدد", () -> start(false));
            button("فایل‌ها و خطاها", () -> files(0));
        } else {
            capacityGauge = new GaugeView(this, regular, "فضای باقی‌ماندهٔ ابر");
            volumeGauge = new GaugeView(this, regular, "تطابق حجم ابر و محلی");
            LinearLayout gauges = new LinearLayout(this); gauges.setOrientation(LinearLayout.HORIZONTAL); gauges.setWeightSum(2);
            gauges.setBackground(surface(Color.WHITE, 18)); gauges.setPadding(dp(4), dp(6), dp(4), dp(6));
            gauges.addView(capacityGauge, new LinearLayout.LayoutParams(0, dp(220), 1));
            gauges.addView(volumeGauge, new LinearLayout.LayoutParams(0, dp(220), 1));
            content.addView(gauges);
            quotaDetail = text("");
            button("بررسی ظرفیت", () -> run(() -> {
                long[] quota = store.updateQuota(new Cloud(store));
                return "ظرفیت به‌روز شد؛ " + SyncPlan.bytes(Math.max(0, quota[1] - quota[0]), 2) + " فضای آزاد.";
            }));
            status = text(store.preferences.getString("status", "آماده"));
            status.setTypeface(bold); status.setTextColor(Color.rgb(22, 76, 112)); status.setBackground(surface(Color.WHITE, 14));
            progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
            progress.setMax(100); progress.setPadding(0, dp(12), 0, dp(8)); content.addView(progress);
            counts = text(""); current = text("");
            button("فایل‌ها و خطاها", () -> files(0));
            button("پیش‌نمایش محلی", () -> start(true));
            button("سینک / تلاش مجدد", () -> start(false));
            button("شروع پایش تغییرات", this::automatic);
            button("توقف همگام‌سازی", this::stop);
            button("بازکردن ابر", this::openCloud);
        }
    }

    private void account() {
        EditText phone = field("شماره همراه", InputType.TYPE_CLASS_PHONE);
        EditText code = field("کد شش‌رقمی", InputType.TYPE_CLASS_NUMBER);
        EditText password = field("یا رمز حساب", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        ImageView image = new ImageView(this);
        image.setAdjustViewBounds(true); content.addView(image);
        EditText answer = field("پاسخ کپچا (در صورت درخواست ابر)", InputType.TYPE_CLASS_TEXT);
        button("دریافت کپچا", () -> run(() -> {
            JSONObject value = new Cloud(store).json("GET", "v1/captcha/get-captcha/", null, false);
            String encoded = value.getString("image");
            if (encoded.contains(",")) encoded = encoded.substring(encoded.indexOf(',') + 1);
            if (encoded.length() > 1500000) throw new Exception("تصویر کپچا بزرگ است.");
            byte[] raw = Base64.decode(encoded, Base64.DEFAULT);
            android.graphics.Bitmap bitmap = BitmapFactory.decodeByteArray(raw, 0, raw.length);
            if (bitmap == null) throw new Exception("تصویر کپچا خوانده نشد.");
            handler.post(() -> { captcha = value; image.setImageBitmap(bitmap); });
            return "پاسخ تصویر را وارد کنید.";
        }));
        button("ارسال کد", () -> {
            String number = phone.getText().toString(), captchaAnswer = answer.getText().toString();
            run(() -> {
                new Cloud(store).json("POST", "v6/profile/auth/generate-code/", credentials(number, captchaAnswer), false);
                return "کد ورود ارسال شد.";
            });
        });
        button("ورود", () -> {
            String number = phone.getText().toString(), otp = digits(code.getText().toString()),
                secret = password.getText().toString(), captchaAnswer = answer.getText().toString();
            password.setText(""); code.setText("");
            run(() -> {
                if (Backup.RUNNING.get()) return "ابتدا همگام‌سازی را متوقف کنید.";
                Cloud cloud = new Cloud(store);
                JSONObject payload = credentials(number, captchaAnswer);
                if (!otp.isEmpty()) {
                    if (!otp.matches("[0-9]{6}")) return "کد باید شش رقم باشد.";
                    cloud.json("POST", "v6/profile/auth/validate-otp/", new JSONObject().put("phone", payload.get("phone"))
                        .put("prefix", "+98").put("code", otp), false);
                    payload.put("code", otp);
                } else if (!secret.isEmpty()) payload.put("password", secret);
                else return "رمز یا کد ورود را وارد کنید.";
                payload.put("country", "IR");
                JSONObject tokens = cloud.json("POST", "v6/profile/auth/login/", payload, false);
                if (tokens.optBoolean("is_registered")) return "ثبت‌نام را در سایت رسمی کامل کنید.";
                store.login(tokens);
                return "ورود انجام شد؛ نشست در این گوشی رمزگذاری شد.";
            });
        });
        button("بررسی اتصال", () -> run(() -> {
            new Cloud(store).json("GET", "v6/profile/auth/get-profile/", null, true);
            return "اتصال حساب موفق بود.";
        }));
        button("خروج از حساب", () -> {
            if (operation) { alert("تا پایان بررسی جاری صبر کنید."); return; }
            stop();
            if (Backup.RUNNING.get()) { alert("پس از توقف عملیات، دوباره خروج را بزنید."); return; }
            store.logout(); alert("نشست محلی حذف شد؛ فایل‌های ابری باقی هستند.");
        });
    }

    private static String digits(String text) {
        String source = "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", target = "01234567890123456789";
        StringBuilder result = new StringBuilder();
        for (char character : text.toCharArray()) {
            int position = source.indexOf(character);
            result.append(position < 0 ? character : target.charAt(position));
        }
        return result.toString();
    }

    private JSONObject credentials(String number, String answer) throws Exception {
        String phone = digits(number).replace(" ", "");
        if (phone.startsWith("+98")) phone = phone.substring(3);
        else if (phone.startsWith("0098")) phone = phone.substring(4);
        else if (phone.startsWith("0")) phone = phone.substring(1);
        if (!phone.matches("9[0-9]{9}")) throw new java.io.IOException("شماره همراه معتبر نیست.");
        JSONObject payload = new JSONObject().put("phone", phone).put("prefix", "+98");
        if (captcha.has("id") && !answer.isEmpty()) payload.put("captcha_id", captcha.get("id"))
            .put("captcha_provider", captcha.opt("provider")).put("captcha_value", answer);
        return payload;
    }

    private void folders() {
        button("انتخاب پوشه", () -> {
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
            startActivityForResult(intent, 10);
        });
        try {
            JSONArray roots = new JSONArray(store.preferences.getString("folders", "[]"));
            for (int index = 0; index < roots.length(); index++) {
                String value = roots.getString(index);
                String name;
                try { name = Backup.documentName(this, Uri.parse(value)); }
                catch (Exception error) { name = "پوشهٔ نیازمند اجازهٔ مجدد"; }
                text(name);
                text("محلی: " + SyncPlan.bytes(store.preferences.getLong("local:" + value, -1)) +
                    " · ابر: " + SyncPlan.bytes(store.preferences.getLong("cloud:" + value, -1)));
                JSONObject plan = new JSONObject(store.preferences.getString("folderPlans", "{}")).optJSONObject(value);
                String mode = plan == null ? "instant" : plan.optString("mode", "instant");
                String time = plan == null ? "02:00" : plan.optString("time", "02:00");
                String end = plan == null ? "23:59" : plan.optString("end", "23:59");
                button(("instant".equals(mode) ? "✓ " : "") + "سینک آنی", () -> savePlan(value, "instant", time, end));
                button(("daily".equals(mode) ? "✓ " : "") + "سینک روزانه · " + time + " تا " + end, () ->
                    new TimePickerDialog(this, (picker, hour, minute) -> {
                        String start = String.format(java.util.Locale.ROOT, "%02d:%02d", hour, minute);
                        new TimePickerDialog(this, (picker2, hour2, minute2) -> savePlan(value, "daily", start,
                            String.format(java.util.Locale.ROOT, "%02d:%02d", hour2, minute2)),
                            Integer.parseInt(end.substring(0, 2)), Integer.parseInt(end.substring(3)), true).show();
                    }, Integer.parseInt(time.substring(0, 2)), Integer.parseInt(time.substring(3)), true).show());
                button("حذف از فهرست", () -> {
                    if (Backup.RUNNING.get()) { alert("ابتدا همگام‌سازی را متوقف کنید."); return; }
                    try {
                        JSONArray updated = new JSONArray();
                        for (int position = 0; position < roots.length(); position++) {
                            if (!value.equals(roots.getString(position))) updated.put(roots.getString(position));
                        }
                        store.preferences.edit().putString("folders", updated.toString()).apply();
                        getContentResolver().releasePersistableUriPermission(Uri.parse(value), Intent.FLAG_GRANT_READ_URI_PERMISSION);
                        show("folders");
                    } catch (Exception error) { alert(Backup.friendly(error)); }
                });
            }
        } catch (Exception error) { alert(Backup.friendly(error)); }
    }

    private void savePlan(String tree, String mode, String time, String end) {
        try {
            synchronized (Store.PLAN_LOCK) {
                JSONObject plans = new JSONObject(store.preferences.getString("folderPlans", "{}"));
                plans.put(tree, SyncPlan.configure(mode, time, end, System.currentTimeMillis()));
                if (!store.preferences.edit().putString("folderPlans", plans.toString()).commit()) throw new UserError("ذخیره ناموفق بود.");
            }
            show("folders");
        } catch (Exception error) { alert(Backup.friendly(error)); }
    }

    private void files(int offset) {
        try (android.database.sqlite.SQLiteDatabase database = openOrCreateDatabase("backup.db", MODE_PRIVATE, null)) {
            database.enableWriteAheadLogging();
            database.execSQL("CREATE TABLE IF NOT EXISTS run_items (path TEXT PRIMARY KEY, data TEXT NOT NULL)");
            LinearLayout rows = new LinearLayout(this); rows.setOrientation(LinearLayout.VERTICAL);
            rows.setPadding(16, 8, 16, 8);
            ScrollView scroll = new ScrollView(this); scroll.addView(rows);
            AlertDialog dialog = new AlertDialog.Builder(this).setTitle("فایل‌ها · " + (offset / 100 + 1))
                .setView(scroll).setPositiveButton("بستن", null).create();
            int count = 0;
            try (android.database.Cursor cursor = database.rawQuery(
                    "SELECT data FROM run_items ORDER BY path LIMIT 100 OFFSET ?",
                    new String[]{Integer.toString(offset)})) {
                while (cursor.moveToNext()) {
                    JSONObject item = new JSONObject(cursor.getString(0));
                    count++;
                    TextView label = new TextView(this); label.setTypeface(regular);
                    boolean failed = "error".equals(item.optString("status"));
                    label.setText(item.getString("path") + "\nمحلی: " + SyncPlan.bytes(item.optLong("size", -1)) +
                        " · ابر: " + SyncPlan.bytes(item.optLong("cloudBytes", -1)) + "\n" +
                        (failed ? "✕ " + item.optString("error") : "queued".equals(item.optString("status")) ? "در صف" : "✓"));
                    label.setPadding(8, 14, 8, 10); rows.addView(label);
                    Button open = new Button(this); open.setTypeface(regular); open.setText("بازکردن فایل");
                    String uri = item.getString("uri");
                    open.setOnClickListener(view -> {
                        try {
                            Uri source = Uri.parse(uri);
                            String type = getContentResolver().getType(source);
                            startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(source, type == null ? "application/octet-stream" : type)
                                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION));
                        } catch (Exception error) { alert("فایل یا برنامهٔ مناسب برای بازکردن آن در دسترس نیست."); }
                    });
                    rows.addView(open);
                    if (failed) {
                        Button retry = new Button(this); retry.setText("تلاش مجدد"); retry.setTypeface(regular);
                        String retryPath = item.getString("path");
                        retry.setOnClickListener(view -> { dialog.dismiss(); start(false, retryPath); }); rows.addView(retry);
                    }
                }
            }
            if (offset > 0) {
                Button previous = new Button(this); previous.setText("قبلی"); previous.setTypeface(regular);
                previous.setOnClickListener(view -> { dialog.dismiss(); files(offset - 100); }); rows.addView(previous);
            }
            if (count == 100) {
                Button next = new Button(this); next.setText("بعدی"); next.setTypeface(regular);
                next.setOnClickListener(view -> { dialog.dismiss(); files(offset + 100); }); rows.addView(next);
            }
            dialog.show();
        } catch (Exception error) { alert(Backup.friendly(error)); }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != 10 || result != RESULT_OK || data == null || data.getData() == null) return;
        try {
            Uri tree = data.getData();
            getContentResolver().takePersistableUriPermission(tree, Intent.FLAG_GRANT_READ_URI_PERMISSION);
            Backup.documentName(this, tree);
            JSONArray roots = new JSONArray(store.preferences.getString("folders", "[]"));
            for (int index = 0; index < roots.length(); index++) if (tree.toString().equals(roots.getString(index))) return;
            roots.put(tree.toString());
            store.preferences.edit().putString("folders", roots.toString()).apply();
            show("folders");
        } catch (Exception error) { alert(Backup.friendly(error)); }
    }

    private void settings() {
        JSONObject cached = Update.cached(this);
        TextView updateState = text(cached.optBoolean("available") ? "نسخهٔ " + cached.optString("version") + " · مهلت نصب: " + android.text.format.DateFormat.format("yyyy/MM/dd", cached.optLong("deadline")) : "نسخهٔ " + ReleasePolicy.VERSION);
        button("بررسی به‌روزرسانی", () -> run(() -> {
            JSONObject info = Update.check(this, true);
            handler.post(() -> updateState.setText(info.optBoolean("available") ? "نسخهٔ " + info.optString("version") + " آماده است." : "نسخهٔ شما به‌روز است."));
            return info.has("error") ? info.getString("error") : info.optBoolean("available") ? "نسخهٔ جدید پیدا شد؛ دریافت آن را تأیید کنید." : "نسخهٔ شما به‌روز است.";
        }));
        button("دریافت و نصب", () -> run(() -> {
            if (Backup.RUNNING.get()) return "ابتدا همگام‌سازی را متوقف کنید.";
            handler.post(() -> updateState.setText("در حال دریافت و کنترل فایل به‌روزرسانی…"));
            String result = Update.downloadAndInstall(this);
            handler.post(() -> updateState.setText(result));
            return result;
        }));
        EditText root = field("پوشهٔ اصلی در ابر", InputType.TYPE_CLASS_TEXT);
        root.setText(store.preferences.getString("remoteRoot", "Android"));
        root.setEnabled(store.preferences.getString("indexedRoot", "").isEmpty());
        EditText limit = field("حداکثر اندازهٔ فایل (MiB؛ صفر: بدون محدودیت)", InputType.TYPE_CLASS_NUMBER);
        limit.setText(Integer.toString(store.preferences.getInt("maxMiB", 0)));
        CheckBox wifi = new CheckBox(this);
        wifi.setText("فقط اتصال بدون هزینهٔ حجمی (معمولاً Wi-Fi)");
        wifi.setTypeface(regular);
        wifi.setChecked(store.preferences.getBoolean("wifiOnly", true)); content.addView(wifi);
        button("ذخیرهٔ تنظیمات", () -> {
            if (Backup.RUNNING.get()) { alert("ابتدا همگام‌سازی را متوقف کنید."); return; }
            try {
                String name = Policy.name(root.getText().toString());
                int maximum = Integer.parseInt(digits(limit.getText().toString()));
                if (maximum < 0) throw new NumberFormatException();
                store.preferences.edit().putString("remoteRoot", name).putInt("maxMiB", maximum)
                    .putBoolean("wifiOnly", wifi.isChecked()).commit();
                SyncService.schedule(this, store.preferences.getBoolean("automatic", false));
                alert("ذخیره شد.");
            } catch (Exception error) { alert("نام ریشه یا اندازهٔ فایل معتبر نیست."); }
        });
    }

    private void start(boolean preview) {
        start(preview, null);
    }

    private void start(boolean preview, String retryPath) {
        if (!preview && Update.required(this)) { show("settings"); alert("ابتدا به‌روزرسانی اجباری را نصب کنید."); return; }
        if (Backup.RUNNING.get()) { alert("عملیات دیگری در حال اجراست."); return; }
        if (!preview && !SyncService.networkAllowed(this, store)) { alert("اتصال مطابق تنظیمات شبکه برقرار نیست."); return; }
        show("status");
        if (!preview) {
            try { startForegroundService(new Intent(this, SyncService.class).setAction("once").putExtra("retryPath", retryPath)); }
            catch (Exception error) { alert("شروع سرویس ارسال ممکن نشد؛ دوباره برنامه را باز کنید."); }
            return;
        }
        run(() -> {
            activeBackup = new Backup(this);
            try { activeBackup.run(preview); }
            finally { activeBackup = null; }
            return store.preferences.getString("status", "تمام شد.");
        });
    }

    private void automatic() {
        if (Update.required(this)) { show("settings"); alert("ابتدا به‌روزرسانی اجباری را نصب کنید."); return; }
        try {
            if (store.session().optString("access").isEmpty()) { alert("ابتدا وارد حساب شوید."); return; }
            if (new JSONArray(store.preferences.getString("folders", "[]")).length() == 0) {
                alert("ابتدا پوشه را انتخاب کنید."); return;
            }
        } catch (Exception error) { alert("نشست یا انتخاب پوشه معتبر نیست؛ دوباره وارد حساب شوید."); return; }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 11);
        }
        try {
            startForegroundService(new Intent(this, SyncService.class));
            alert("پایش شروع شد. بستن صفحه، سرویس دارای اعلان را نمی‌بندد.");
        } catch (Exception error) { alert("شروع سرویس ممکن نشد؛ تنظیمات اعلان و محدودیت باتری را بررسی کنید."); }
    }

    private void stop() {
        store.preferences.edit().putBoolean("automatic", false).apply();
        SyncService.schedule(this, false);
        stopService(new Intent(this, SyncService.class));
        if (activeBackup != null) activeBackup.cloud.cancel();
        store.report("توقف درخواست شد؛ فایل‌های تأییدشده حفظ شدند.");
    }
}
