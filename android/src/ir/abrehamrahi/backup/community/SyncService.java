package ir.abrehamrahi.backup.community;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public final class SyncService extends Service {
    private static final int JOB = 10500;
    private ScheduledExecutorService executor;
    private Backup backup;
    private Store store;
    private volatile boolean monitoring;
    private final Handler handler = new Handler(Looper.getMainLooper());

    public static boolean networkAllowed(Context context, Store store) {
        ConnectivityManager manager = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        NetworkCapabilities network = manager.getNetworkCapabilities(manager.getActiveNetwork());
        return network != null && network.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            (!store.preferences.getBoolean("wifiOnly", true) || network.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED));
    }

    public static void schedule(Context context, boolean enabled) {
        JobScheduler scheduler = (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (!enabled) { scheduler.cancel(JOB); return; }
        Store store = new Store(context);
        JobInfo job = new JobInfo.Builder(JOB, new ComponentName(context, SyncJob.class))
            .setRequiredNetworkType(store.preferences.getBoolean("wifiOnly", true) ? JobInfo.NETWORK_TYPE_UNMETERED : JobInfo.NETWORK_TYPE_ANY)
            .setPeriodic(15 * 60 * 1000L).setPersisted(true).build();
        if (scheduler.schedule(job) != JobScheduler.RESULT_SUCCESS) store.report("زمان‌بندی پس‌زمینه ثبت نشد؛ دوباره برنامه را باز کنید.");
    }

    private Notification notification() {
        int total = store.preferences.getInt("total", 0), done = store.preferences.getInt("done", 0);
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent stop = PendingIntent.getService(this, 1, new Intent(this, SyncService.class).setAction("stop"), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this, "backup")
            .setSmallIcon(R.drawable.cloud).setContentTitle("Abre Hamrahi API")
            .setContentText(Backup.RUNNING.get() ? "در حال بررسی: " + done + " از " + total + " فایل" : "پایش تغییرات پوشه‌های انتخاب‌شده")
            .setContentIntent(open).setOngoing(true).setVisibility(Notification.VISIBILITY_PRIVATE)
            .setProgress(total, done, total == 0 && Backup.RUNNING.get())
            .addAction(new Notification.Action.Builder(null, "توقف", stop).build()).build();
    }

    private final Runnable refreshNotification = new Runnable() {
        @Override public void run() {
            NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            manager.notify(1, notification());
            handler.postDelayed(this, 2000);
        }
    };

    @Override public void onCreate() {
        super.onCreate();
        store = new Store(this);
        backup = new Backup(this);
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        manager.createNotificationChannel(new NotificationChannel("backup", "پشتیبان‌گیری پوشه‌ها", NotificationManager.IMPORTANCE_LOW));
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && "stop".equals(intent.getAction())) {
            store.preferences.edit().putBoolean("automatic", false).apply();
            schedule(this, false);
            stopSelf();
            return START_NOT_STICKY;
        }
        startForeground(1, notification());
        if (intent != null && "once".equals(intent.getAction())) {
            if (executor == null) {
                executor = Executors.newSingleThreadScheduledExecutor();
                handler.post(refreshNotification);
            }
            executor.submit(() -> {
                try { if (networkAllowed(this, store)) backup.run(false); }
                finally { if (!monitoring) stopSelf(startId); }
            });
            return START_NOT_STICKY;
        }
        if (executor == null) {
            executor = Executors.newSingleThreadScheduledExecutor();
            handler.post(refreshNotification);
        }
        if (!monitoring) {
            monitoring = true;
            store.preferences.edit().putBoolean("automatic", true).apply();
            schedule(this, true);
            executor.scheduleWithFixedDelay(() -> {
                if (networkAllowed(this, store)) backup.run(false);
            }, 0, 30, TimeUnit.SECONDS);
        }
        return START_NOT_STICKY;
    }

    @Override public void onTimeout(int startId, int type) {
        store.report("محدودیت زمان سرویس اندروید رسید؛ زمان‌بندی دوره‌ای باقی است. برای پایش سریع، برنامه را باز و شروع را بزنید.");
        backup.cloud.cancel();
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    @Override public void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        backup.cloud.cancel();
        if (executor != null) executor.shutdownNow();
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
