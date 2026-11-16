package ir.abrehamrahi.backup.community;

import java.util.Calendar;
import org.json.JSONObject;

public final class SyncPlan {
    private SyncPlan() {}

    public static JSONObject configure(String mode, String time, long now) throws Exception {
        return configure(mode, time, "23:59", now);
    }

    public static JSONObject configure(String mode, String time, String end, long now) throws Exception {
        if ((!"instant".equals(mode) && !"daily".equals(mode)) || time == null || !time.matches("(?:[01][0-9]|2[0-3]):[0-5][0-9]")) {
            throw new UserError("زمان‌بندی معتبر نیست.");
        }
        if (end == null || !end.matches("(?:[01][0-9]|2[0-3]):[0-5][0-9]")) throw new UserError("invalid time window");
        return new JSONObject().put("mode", mode).put("time", time).put("end", end).put("next", next(time, now));
    }

    public static long next(String time, long now) {
        Calendar next = Calendar.getInstance();
        next.setTimeInMillis(now);
        next.set(Calendar.HOUR_OF_DAY, Integer.parseInt(time.substring(0, 2)));
        next.set(Calendar.MINUTE, Integer.parseInt(time.substring(3)));
        next.set(Calendar.SECOND, 0);
        next.set(Calendar.MILLISECOND, 0);
        if (next.getTimeInMillis() <= now) next.add(Calendar.DAY_OF_MONTH, 1);
        return next.getTimeInMillis();
    }

    public static boolean due(JSONObject plan, long now) {
        return plan == null || !"daily".equals(plan.optString("mode")) || (plan.optLong("next", 0) <= now && inWindow(plan, now));
    }

    public static boolean inWindow(JSONObject plan, long now) {
        if (plan == null || !"daily".equals(plan.optString("mode"))) return true;
        String start = plan.optString("time", "00:00"), end = plan.optString("end", "23:59");
        Calendar cursor = Calendar.getInstance(); cursor.setTimeInMillis(now);
        int current = cursor.get(Calendar.HOUR_OF_DAY) * 60 + cursor.get(Calendar.MINUTE);
        int from = Integer.parseInt(start.substring(0, 2)) * 60 + Integer.parseInt(start.substring(3));
        int to = Integer.parseInt(end.substring(0, 2)) * 60 + Integer.parseInt(end.substring(3));
        if (from == to) return true;
        return from < to ? current >= from && current <= to : current >= from || current <= to;
    }

    public static String bytes(long size) {
        return bytes(size, 1);
    }

    public static String bytes(long size, int digits) {
        if (size < 0) return "—";
        double amount = size;
        String[] units = {"بایت", "KiB", "MiB", "GiB", "TiB"};
        int unit = 0;
        while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit++; }
        return String.format(java.util.Locale.ROOT, unit == 0 ? "%.0f %s" : "%." + digits + "f %s", amount, units[unit]);
    }

    public static double ratio(long local, long remote) {
        if (local < 0 || remote < 0) return 0;
        if (local == 0 && remote == 0) return 100;
        return 100.0 * Math.min(local, remote) / Math.max(local, remote);
    }

    public static String percent(double value) {
        if (value < 0) return "—";
        return String.format(java.util.Locale.ROOT, "%.2f%%", value < 100 ? Math.min(99.99, value) : 100);
    }
}
