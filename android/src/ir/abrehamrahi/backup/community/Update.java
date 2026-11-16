package ir.abrehamrahi.backup.community;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import java.io.*;
import java.net.*;
import java.security.MessageDigest;
import org.json.JSONObject;

public final class Update {
    private static final Object LOCK=new Object();
    private static final String API="https://api.github.com/repos/digiodin1/abre-hamrahi-api/releases/latest";
    private Update() {}
    private static HttpURLConnection open(String value) throws Exception {
        for(int i=0;i<6;i++) {
            URL url=new URL(value); String host=url.getHost();
            if(!"https".equals(url.getProtocol()) || url.getUserInfo()!=null || (url.getPort()!=-1 && url.getPort()!=443) || !(host.equals("api.github.com")||host.equals("github.com")||host.equals("release-assets.githubusercontent.com")||host.equals("objects.githubusercontent.com")))throw new IOException("Untrusted update URL");
            HttpURLConnection connection=(HttpURLConnection)url.openConnection(); connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(10000); connection.setReadTimeout(30000); connection.setRequestProperty("User-Agent","AbreHamrahiAPI-updater");
            int status=connection.getResponseCode();
            if(status>=300&&status<400){String location=connection.getHeaderField("Location");connection.disconnect();value=new URL(url,location).toString();continue;}
            if(status!=200){connection.disconnect();throw new IOException("GitHub HTTP "+status);}
            return connection;
        }
        throw new IOException("Too many redirects");
    }
    public static JSONObject cached(Context context) {
        try{
            JSONObject info=new JSONObject(new Store(context).preferences.getString("updateRelease","{}"));
            if(info.optBoolean("available") && ReleasePolicy.compare(info.getString("version"),ReleasePolicy.VERSION)<=0)return new JSONObject();
            return info;
        }catch(Exception error){return new JSONObject();}
    }
    public static boolean required(Context context){return ReleasePolicy.required(cached(context),System.currentTimeMillis());}
    public static JSONObject check(Context context, boolean force) {
        synchronized(LOCK) {
            Store store=new Store(context); long now=System.currentTimeMillis();
            if(!force && now-store.preferences.getLong("updateChecked",0)<21600000L)return cached(context);
            HttpURLConnection connection=null;
            try {
                connection=open(API); ByteArrayOutputStream bytes=new ByteArrayOutputStream(); byte[] b=new byte[8192]; int n;
                try(InputStream input=connection.getInputStream()){while((n=input.read(b))>=0){if(bytes.size()+n>2*1024*1024)throw new IOException("Release too large");bytes.write(b,0,n);}}
                JSONObject info=ReleasePolicy.select(new JSONObject(new String(bytes.toByteArray(),"UTF-8")),ReleasePolicy.VERSION,now);
                store.preferences.edit().putString("updateRelease",info.toString()).putLong("updateChecked",now).apply();
                return info;
            } catch(Exception error) {
                JSONObject info=cached(context);
                try{info.put("error","بررسی به‌روزرسانی ناموفق بود؛ دوباره تلاش کنید.");}catch(Exception ignored){}
                store.preferences.edit().putLong("updateChecked",now).apply(); return info;
            } finally {if(connection!=null)connection.disconnect();}
        }
    }
    static File target(Context context){return new File(context.getFilesDir(),"updates/update.apk");}
    private static String hex(byte[] data){StringBuilder result=new StringBuilder();for(byte b:data)result.append(String.format(java.util.Locale.ROOT,"%02x",b&255));return result.toString();}
    @SuppressWarnings("deprecation")
    public static String downloadAndInstall(Context context) throws Exception {
        synchronized(LOCK) {
            JSONObject info=check(context,true);
            if(info.has("error"))return info.getString("error");
            if(!info.optBoolean("available"))return "نسخهٔ شما به‌روز است.";
            if(!context.getPackageManager().canRequestPackageInstalls()){
                new Handler(Looper.getMainLooper()).post(()->context.startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+context.getPackageName())).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)));
                return "اجازهٔ نصب به‌روزرسانی را فعال کنید، سپس دوباره «دریافت و نصب» را بزنید.";
            }
            File target=target(context), part=new File(target.getParentFile(),"update.part"); target.getParentFile().mkdirs();
            HttpURLConnection connection=null;
            try {
                connection=open(info.getString("url")); MessageDigest digest=MessageDigest.getInstance("SHA-256");long total=0;
                try(InputStream input=connection.getInputStream();FileOutputStream output=new FileOutputStream(part)){
                    byte[] b=new byte[65536];int n;while((n=input.read(b))>=0){total+=n;if(total>info.getLong("size"))throw new IOException("Invalid APK size");digest.update(b,0,n);output.write(b,0,n);}output.getFD().sync();
                }
                if(total!=info.getLong("size") || !hex(digest.digest()).equals(info.getString("sha256")))throw new IOException("APK checksum mismatch");
                PackageManager manager=context.getPackageManager(); PackageInfo apk=manager.getPackageArchiveInfo(part.getAbsolutePath(),PackageManager.GET_SIGNATURES), installed=manager.getPackageInfo(context.getPackageName(),PackageManager.GET_SIGNATURES);
                if(apk==null || !context.getPackageName().equals(apk.packageName) || !info.getString("version").equals(apk.versionName) || apk.versionCode<=installed.versionCode || apk.signatures==null || installed.signatures==null || apk.signatures.length!=1 || installed.signatures.length!=1 || !apk.signatures[0].equals(installed.signatures[0]))throw new IOException("APK identity or signature mismatch");
                if(!part.renameTo(target))throw new IOException("Cannot finalize APK");
                new Handler(Looper.getMainLooper()).post(()->{
                    try{context.startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(Uri.parse("content://ir.abrehamrahi.backup.community.updates/update.apk"),"application/vnd.android.package-archive").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_GRANT_READ_URI_PERMISSION));}
                    catch(Exception error){new Store(context).report("بازکردن نصب‌کننده انجام نشد؛ دوباره تلاش کنید.");}
                });
                return "فایل بررسی شد؛ نصب را در پنجرهٔ اندروید تأیید کنید.";
            } finally {if(connection!=null)connection.disconnect();part.delete();}
        }
    }
}
