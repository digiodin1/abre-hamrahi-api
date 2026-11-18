package ir.abrehamrahi.backup.community;

import org.json.JSONObject;
import org.json.JSONArray;
import java.text.SimpleDateFormat;
import java.util.Locale;
import java.util.TimeZone;

public final class ReleasePolicy {
    public static final String VERSION = "1.7.7";
    public static final long GRACE = 30L * 86400000L;
    private ReleasePolicy() {}
    public static int compare(String left, String right) {
        if (!left.matches("v?[0-9]+\\.[0-9]+\\.[0-9]+") || !right.matches("v?[0-9]+\\.[0-9]+\\.[0-9]+")) throw new IllegalArgumentException("Invalid version");
        String[] a = left.replaceFirst("^v", "").split("\\."), b = right.replaceFirst("^v", "").split("\\.");
        for(int i=0;i<3;i++) { int result = Integer.compare(Integer.parseInt(a[i]), Integer.parseInt(b[i])); if(result!=0) return result; }
        return 0;
    }
    public static JSONObject select(JSONObject release, String current, long now) throws Exception {
        String tag = release.getString("tag_name");
        if (release.optBoolean("draft") || release.optBoolean("prerelease") || compare(tag,current)<=0) return new JSONObject().put("available",false);
        SimpleDateFormat format=new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'",Locale.ROOT); format.setLenient(false); format.setTimeZone(TimeZone.getTimeZone("UTC"));
        String published=release.getString("published_at");
        if (!published.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")) throw new Exception("Invalid date");
        long at=format.parse(published).getTime(); if(at>now+86400000L)throw new Exception("Future release");
        String version=tag.replaceFirst("^v",""), name="AbreHamrahiAPI-"+version+"-android.apk";
        String url="https://github.com/digiodin1/abre-hamrahi-api/releases/download/"+tag+"/"+name;
        JSONArray assets=release.getJSONArray("assets");
        for(int i=0;i<assets.length();i++) {
            JSONObject asset=assets.getJSONObject(i);
            if(!name.equals(asset.optString("name")) || !url.equals(asset.optString("browser_download_url")) || !"uploaded".equals(asset.optString("state")))continue;
            String digest=asset.optString("digest"); long size=asset.optLong("size");
            if(!digest.matches("sha256:[a-fA-F0-9]{64}") || size<10000 || size>500L*1024*1024)throw new UserError("اطلاعات فایل نصب معتبر نیست.");
            return new JSONObject().put("available",true).put("version",version).put("url",url).put("size",size).put("sha256",digest.substring(7).toLowerCase(Locale.ROOT)).put("deadline",at+GRACE);
        }
        return new JSONObject().put("available",false).put("error","فایل نصب اندروید هنوز منتشر نشده است.");
    }
    public static boolean required(JSONObject value,long now) {
        try { return value.optBoolean("available") && compare(value.getString("version"),VERSION)>0 && value.has("deadline") && now>=value.getLong("deadline"); }
        catch(Exception error){return false;}
    }
}
