package ir.abrehamrahi.backup.community;

import android.app.job.JobParameters;
import android.app.job.JobService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class SyncJob extends JobService {
    private Backup backup;
    private ExecutorService executor;
    private volatile int generation;

    @Override public boolean onStartJob(JobParameters parameters) {
        Store store = new Store(this);
        if (!store.preferences.getBoolean("automatic", false) || Backup.RUNNING.get()) return false;
        backup = new Backup(this);
        executor = Executors.newSingleThreadExecutor();
        Backup task = backup;
        ExecutorService owner = executor;
        int attempt = ++generation;
        owner.submit(() -> {
            try { task.run(false, true); }
            finally {
                if (generation == attempt) jobFinished(parameters, false);
                owner.shutdown();
            }
        });
        return true;
    }

    @Override public boolean onStopJob(JobParameters parameters) {
        generation++;
        if (backup != null) backup.cloud.cancel();
        if (executor != null) executor.shutdownNow();
        return new Store(this).preferences.getBoolean("automatic", false);
    }
}
