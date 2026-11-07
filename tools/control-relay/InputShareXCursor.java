import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.PorterDuff;
import android.view.Surface;
import android.view.SurfaceControl;

/** A session-owned compositor cursor; no app window, focus, or touch target. */
public final class InputShareXCursor implements AutoCloseable {
    private final SurfaceControl layer;
    private final SurfaceControl.Transaction transaction = new SurfaceControl.Transaction();
    private boolean closed;
    private int x = -1, y = -1;
    private float scale;
    private boolean visible;

    public InputShareXCursor() throws Exception {
        layer = new SurfaceControl.Builder().setName("InputShareX system cursor")
            .setBufferSize(36, 48).setFormat(PixelFormat.TRANSLUCENT).setHidden(true).build();
        try (SurfaceOwner owner = new SurfaceOwner(layer)) {
            Canvas canvas = owner.surface.lockCanvas(null);
            try {
                canvas.drawColor(Color.TRANSPARENT, PorterDuff.Mode.CLEAR);
                Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
                Path arrow = new Path();
                arrow.moveTo(1, 1); arrow.lineTo(1, 31); arrow.lineTo(9, 24);
                arrow.lineTo(16, 39); arrow.lineTo(22, 36); arrow.lineTo(15, 22);
                arrow.lineTo(26, 22); arrow.close();
                paint.setShadowLayer(1.5f, 0.75f, 1.25f, 0x55000000);
                paint.setStyle(Paint.Style.FILL); paint.setColor(Color.WHITE); canvas.drawPath(arrow, paint);
                paint.clearShadowLayer();
                paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(0.8f);
                paint.setStrokeJoin(Paint.Join.ROUND); paint.setColor(0xff7a7a7a);
                canvas.drawPath(arrow, paint);
            } finally { owner.surface.unlockCanvasAndPost(canvas); }
        } catch (Exception failure) { close(); throw failure; }
        try {
        // The cursor must not count as an obscuring application surface for secure Views.
        // Shell already owns ACCESS_SURFACE_FLINGER; do not request new app permissions.
        SurfaceControl.Transaction.class.getMethod("setTrustedOverlay", SurfaceControl.class, boolean.class)
            .invoke(transaction, layer, true);
        transaction.setLayer(layer, Integer.MAX_VALUE - 10).apply();
        } catch (Exception | LinkageError failure) { close(); throw failure; }
    }

    public synchronized void update(int px, int py, boolean show, float size) {
        if (closed || (x == px && y == py && visible == show && scale == size)) return;
        x = px; y = py; visible = show; scale = size;
        transaction.setPosition(layer, x, y).setScale(layer, scale, scale).setVisibility(layer, visible).apply();
    }

    @Override public synchronized void close() {
        if (closed) return;
        closed = true;
        try {
            transaction.setVisibility(layer, false).apply();
            SurfaceControl.Transaction.class.getMethod("remove", SurfaceControl.class).invoke(transaction, layer);
            transaction.apply();
        } catch (Exception ignored) { }
        finally { transaction.close(); layer.release(); }
    }

    private static final class SurfaceOwner implements AutoCloseable {
        final Surface surface;
        SurfaceOwner(SurfaceControl layer) { surface = new Surface(layer); }
        @Override public void close() { surface.release(); }
    }

    public static void main(String[] args) throws Exception {
        try (InputShareXCursor cursor = new InputShareXCursor()) {
            cursor.update(700, 650, true, 2);
            System.out.println("CURSOR_READY");
            Thread.sleep(10000);
        }
    }
}
