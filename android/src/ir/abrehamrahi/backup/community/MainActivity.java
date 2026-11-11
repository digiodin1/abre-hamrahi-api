package ir.abrehamrahi.backup.community;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.BitmapFactory;
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
    private TextView status, reports, current, counts;
    private ProgressBar progress;
    private String page = "status";
    private JSONObject captcha = new JSONObject();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private boolean operation;
    private Backup activeBackup;

    private interface Task { String run() throws Exception; }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        store = new Store(this);
        if (!store.preferences.contains("remoteRoot")) {
            String model = Build.MODEL.replaceAll("[^\\p{L}\\p{N} _-]", "_");
            store.preferences.edit().putString("remoteRoot", "Android " + model).apply();
        }
        show("status");
    }

    @Override public void onResume() {
        super.onResume();
        handler.post(update);
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
            if (progress != null) {
                progress.setIndeterminate(Backup.RUNNING.get() && total == 0);
                progress.setProgress(total == 0 ? 0 : Math.min(100, done * 100 / total));
            }
            if (counts != null) counts.setText(done + " / " + total + " فایل · " + (total == 0 ? 0 : done * 100 / total) + "٪");
            if (current != null) current.setText(store.preferences.getString("current", ""));
            handler.postDelayed(this, 1000);
        }
    };

    private TextView text(String value) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(16);
        view.setPadding(8, 12, 8, 12);
        content.addView(view);
        return view;
    }

    private EditText field(String hint, int type) {
        EditText view = new EditText(this);
        view.setHint(hint);
        view.setInputType(type);
        view.setSaveEnabled(false);
        view.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        content.addView(view);
        return view;
    }

    private Button button(String title, Runnable action) {
        Button button = new Button(this);
        button.setText(title);
        button.setOnClickListener(view -> action.run());
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

    private void show(String selected) {
        page = selected;
        status = null; reports = null; progress = null; counts = null; current = null;
        LinearLayout screen = new LinearLayout(this);
        screen.setOrientation(LinearLayout.VERTICAL);
        screen.setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        TextView title = new TextView(this);
        title.setText("Abre Hamrahi API · 1.5.0");
        title.setTextSize(22); title.setPadding(16, 32, 16, 16);
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
            tab.setEnabled(!key.equals(selected));
            tab.setMinWidth(0); tab.setMinimumWidth(0); tab.setPadding(0, 0, 0, 0);
            tab.setOnClickListener(view -> show(key));
            tabs.addView(tab, new LinearLayout.LayoutParams(0, -2, 1));
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
        } else {
            status = text(store.preferences.getString("status", "آماده"));
            progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
            progress.setMax(100); content.addView(progress);
            counts = text(""); current = text("");
            button("پیش‌نمایش محلی", () -> start(true));
            button("بکاپ / تلاش مجدد", () -> start(false));
            button("شروع پایش تغییرات", this::automatic);
            button("توقف همگام‌سازی", this::stop);
            button("بازکردن ابر", () -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://abrehamrahi.ir/drive/files"))));
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
                store.saveSession(tokens);
                return "ورود انجام شد؛ نشست در این گوشی رمزگذاری شد.";
            });
        });
        button("بررسی اتصال", () -> run(() -> {
            new Cloud(store).json("GET", "v6/profile/auth/get-profile/", null, true);
            return "اتصال حساب موفق بود.";
        }));
        button("خروج از حساب", () -> {
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
        text("فقط پوشه‌های انتخاب‌شده خوانده می‌شوند؛ فایل‌های کلید، نشست و موقت کنار گذاشته می‌شوند.");
        try {
            JSONArray roots = new JSONArray(store.preferences.getString("folders", "[]"));
            for (int index = 0; index < roots.length(); index++) {
                String value = roots.getString(index);
                String name;
                try { name = Backup.documentName(this, Uri.parse(value)); }
                catch (Exception error) { name = "پوشهٔ نیازمند اجازهٔ مجدد"; }
                text(name);
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
        EditText root = field("پوشهٔ اصلی در ابر", InputType.TYPE_CLASS_TEXT);
        root.setText(store.preferences.getString("remoteRoot", "Android"));
        root.setEnabled(store.preferences.getString("indexedRoot", "").isEmpty());
        EditText limit = field("حداکثر اندازهٔ هر فایل (۱ تا ۱۰۰ مگابایت)", InputType.TYPE_CLASS_NUMBER);
        limit.setText(Integer.toString(store.preferences.getInt("maxMiB", 50)));
        CheckBox wifi = new CheckBox(this);
        wifi.setText("فقط اتصال بدون هزینهٔ حجمی (معمولاً Wi-Fi)");
        wifi.setChecked(store.preferences.getBoolean("wifiOnly", true)); content.addView(wifi);
        button("ذخیرهٔ تنظیمات", () -> {
            if (Backup.RUNNING.get()) { alert("ابتدا همگام‌سازی را متوقف کنید."); return; }
            try {
                String name = Policy.name(root.getText().toString());
                int maximum = Integer.parseInt(digits(limit.getText().toString()));
                if (maximum < 1 || maximum > 100) throw new NumberFormatException();
                store.preferences.edit().putString("remoteRoot", name).putInt("maxMiB", maximum)
                    .putBoolean("wifiOnly", wifi.isChecked()).commit();
                SyncService.schedule(this, store.preferences.getBoolean("automatic", false));
                alert("ذخیره شد.");
            } catch (Exception error) { alert("نام ریشه یا اندازهٔ فایل معتبر نیست."); }
        });
        text("پایش سریع: بررسی تغییرات هر ۳۰ ثانیه با اعلان فعال. زمان‌بندی پس‌زمینه: حداقل ۱۵ دقیقه و تابع محدودیت باتری اندروید. اندروید ۱۵ ممکن است سرویس ارسال داده را پس از سقف زمانی متوقف کند. برای ادامهٔ پایش سریع، برنامه را باز و شروع را بزنید.");
        text("بازیابی: از ابر، فایل اصلی یا نسخهٔ قبلی در _abre-android-data/history را دانلود کنید. حذف فایل گوشی، نسخهٔ ابری را حذف نمی‌کند.");
    }

    private void start(boolean preview) {
        if (Backup.RUNNING.get()) { alert("عملیات دیگری در حال اجراست."); return; }
        if (!preview && !SyncService.networkAllowed(this, store)) { alert("اتصال مطابق تنظیمات شبکه برقرار نیست."); return; }
        show("status");
        if (!preview) {
            try { startForegroundService(new Intent(this, SyncService.class).setAction("once")); }
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
