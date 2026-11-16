package ir.abrehamrahi.backup.community;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import java.io.File;

public final class UpdateProvider extends ContentProvider {
    public boolean onCreate(){ return true; }
    public String getType(Uri uri){ return "application/vnd.android.package-archive"; }
    public ParcelFileDescriptor openFile(Uri uri,String mode) throws java.io.FileNotFoundException { if(!"r".equals(mode)||!"/update.apk".equals(uri.getPath()))throw new java.io.FileNotFoundException(); return ParcelFileDescriptor.open(Update.target(getContext()), ParcelFileDescriptor.MODE_READ_ONLY); }
    public Cursor query(Uri uri,String[] p,String a,String[] b,String s){ MatrixCursor c=new MatrixCursor(new String[]{"_display_name","_size"}); if(!"/update.apk".equals(uri.getPath()))return c; File f=Update.target(getContext()); c.addRow(new Object[]{f.getName(),f.length()}); return c; }
    public int delete(Uri u,String a,String[] b){return 0;} public int update(Uri u,ContentValues v,String a,String[] b){return 0;} public Uri insert(Uri u,ContentValues v){return null;}
}
