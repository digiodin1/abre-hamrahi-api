package ir.abrehamrahi.backup.community;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.View;

public final class GaugeView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final String title;
    private final Typeface font;
    private double percent = -1;
    private String detail = "—";

    public GaugeView(Context context, Typeface font, String title) {
        super(context);
        this.title = title;
        this.font = font;
        setMinimumHeight((int)(210 * getResources().getDisplayMetrics().density));
    }

    public void set(double percent, String detail) {
        this.percent = percent;
        this.detail = detail;
        setContentDescription(title + ": " + SyncPlan.percent(percent) + " · " + detail);
        invalidate();
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float unit = getResources().getDisplayMetrics().density;
        float middle = getWidth() / 2f, radius = Math.min(getWidth() / 2f - 20 * unit, 75 * unit), center = 110 * unit;
        RectF arc = new RectF(middle - radius, center - radius, middle + radius, center + radius);
        paint.setTypeface(font); paint.setTextAlign(Paint.Align.CENTER); paint.setTextSize(15 * unit);
        paint.setStyle(Paint.Style.FILL); paint.setColor(Color.rgb(25, 45, 67));
        canvas.drawText(title, middle, 23 * unit, paint);
        paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(10 * unit); paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setColor(Color.rgb(223, 231, 241)); canvas.drawArc(arc, 135, 270, false, paint);
        paint.setColor(Color.rgb(0, 135, 169));
        canvas.drawArc(arc, 135, (float)(270 * Math.max(0, Math.min(100, percent)) / 100), false, paint);
        paint.setStyle(Paint.Style.FILL); paint.setTextSize(25 * unit);
        canvas.drawText(SyncPlan.percent(percent), middle, center + 6 * unit, paint);
        paint.setTextSize(13 * unit); canvas.drawText(detail, middle, 202 * unit, paint);
    }
}
