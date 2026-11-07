import java.net.Socket;
import java.net.InetSocketAddress;
import android.os.SystemClock;
import android.view.InputEvent;
import android.view.InputDevice;
import android.view.MotionEvent;
import java.io.*;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;

/** One absolute mouse authority. No UHID mouse and no InputReader feedback loop. */
public final class InputShareXAbsoluteMouse implements AutoCloseable {
    private final Socket cursor = new Socket();
    private final DataInputStream cursorIn;
    private final DataOutputStream cursorOut;
    private final Object manager;
    private final Method inject;
    private final Method actionButton;
    private int buttons, x, y;
    private long downTime;
    private boolean active;
    private float cursorScale = 1.5f;
    private InputShareXTouchScroll touchScroll;
    private final InputShareXForeground foreground = new InputShareXForeground();
    private InputShareXCursor systemCursor;

    public InputShareXAbsoluteMouse(String token) throws Exception {
        Class<?> cls;
        try { cls = Class.forName("android.hardware.input.InputManagerGlobal"); }
        catch (ClassNotFoundException old) { cls = Class.forName("android.hardware.input.InputManager"); }
        manager = cls.getDeclaredMethod("getInstance").invoke(null);
        inject = cls.getMethod("injectInputEvent", InputEvent.class, int.class);
        actionButton = MotionEvent.class.getDeclaredMethod("setActionButton", int.class);
        touchScroll = new InputShareXTouchScroll(this::touchEvent);
        cursor.connect(new InetSocketAddress("127.0.0.1", 27185), 3000);
        cursor.setTcpNoDelay(true);
        cursor.setSoTimeout(3000);
        cursorIn = new DataInputStream(cursor.getInputStream());
        cursorOut = new DataOutputStream(cursor.getOutputStream());
        cursorOut.write(token.getBytes(StandardCharsets.US_ASCII)); cursorOut.flush();
        if (cursorIn.readUnsignedByte() != 1) throw new IOException("Cursor overlay unavailable or unauthorized");
        try {
            systemCursor = new InputShareXCursor();
            System.out.println("CURSOR compositor");
        } catch (Exception | LinkageError unsupported) {
            System.err.println("Compositor cursor unavailable; using application overlay: " + unsupported);
        }
    }

    public void receive(byte[] frame) throws Exception {
        if (frame.length % 26 != 0) throw new IOException("Invalid absolute frame");
        DataInputStream in = new DataInputStream(new ByteArrayInputStream(frame));
        while (in.available() > 0) {
            if (in.readUnsignedByte() != 18) throw new IOException("Mixed absolute frame");
            int action = in.readUnsignedByte(), nextButtons = in.readInt() & 31;
            int px = in.readInt(), py = in.readInt();
            int w = in.readUnsignedShort(), h = in.readUnsignedShort();
            float horizontal = in.readFloat(), vertical = in.readFloat();
            if (w < 2 || h < 2 || action > 6) throw new IOException("Invalid absolute position");
            if (action == 6) {
                if (Float.isNaN(horizontal) || Float.isInfinite(horizontal) || horizontal < 1 || horizontal > 4)
                    throw new IOException("Invalid cursor scale");
                cursorScale = horizontal;
                if (systemCursor != null) systemCursor.update(x, y, active, cursorScale);
                continue;
            }
            // Keepalive also keeps the cursor IPC alive without rendering or injecting.
            cursorOut.writeInt(px); cursorOut.writeInt(py); cursorOut.writeInt(w); cursorOut.writeInt(h);
            cursorOut.writeBoolean(systemCursor == null && (action == 0 || (active && action != 4))); cursorOut.flush();
            x = cursorIn.readInt(); y = cursorIn.readInt();
            int displayWidth = cursorIn.readInt(), displayHeight = cursorIn.readInt();
            if (systemCursor != null) systemCursor.update(x, y, action == 0 || (active && action != 4), cursorScale);
            if (action == 5) continue;
            if (action == 4) {
                changeButtons(0);
                touchScroll.cancel();
                if (active) event(MotionEvent.ACTION_HOVER_EXIT, 0, 0, 0);
                active = false; continue;
            }
            if (action == 0) {
                System.out.println("CURSOR_POSITION " + x + " " + y + " " + displayWidth + " " + displayHeight);
                active = true;
                event(MotionEvent.ACTION_HOVER_ENTER, 0, 0, 0);
            }
            if (!active) continue;
            changeButtons(nextButtons);
            if (action == 3) {
                if (vertical != 0 && buttons == 0 && foreground.needsTouchScroll()) {
                    touchScroll.wheel(x, y, displayWidth, displayHeight, vertical);
                } else event(MotionEvent.ACTION_SCROLL, 0, horizontal, vertical);
            } else if (action != 2) {
                if ((buttons & 1) != 0) touchScroll.manual(MotionEvent.ACTION_MOVE, x, y);
                else if (!touchScroll.isBusy()) event(buttons == 0 ? MotionEvent.ACTION_HOVER_MOVE : MotionEvent.ACTION_MOVE, 0, 0, 0);
            }
        }
    }

    private void changeButtons(int next) throws Exception {
        int old = buttons;
        if (old == next) return;
        buttons = next;
        // Like scrcpy SDK mode: primary button is a finger; secondary buttons remain mouse input.
        if ((old & 1) == 0 && (next & 1) != 0) touchScroll.manual(MotionEvent.ACTION_DOWN, x, y);
        if ((old & 1) != 0 && (next & 1) == 0) touchScroll.manual(MotionEvent.ACTION_UP, x, y);
        int oldMouse = old & ~1, nextMouse = next & ~1;
        if (oldMouse == 0 && nextMouse != 0) {
            downTime = SystemClock.uptimeMillis();
            event(MotionEvent.ACTION_DOWN, 0, 0, 0);
        }
        for (int bit = 2; bit <= 16; bit <<= 1) {
            if ((oldMouse & bit) != 0 && (nextMouse & bit) == 0) event(MotionEvent.ACTION_BUTTON_RELEASE, bit, 0, 0);
            if ((oldMouse & bit) == 0 && (nextMouse & bit) != 0) event(MotionEvent.ACTION_BUTTON_PRESS, bit, 0, 0);
        }
        if (oldMouse != 0 && nextMouse == 0) event(MotionEvent.ACTION_UP, 0, 0, 0);
    }

    private void touchEvent(int action, int px, int py, long touchDownTime) throws Exception {
        MotionEvent.PointerProperties prop = new MotionEvent.PointerProperties();
        prop.id = 0; prop.toolType = MotionEvent.TOOL_TYPE_FINGER;
        MotionEvent.PointerCoords coords = new MotionEvent.PointerCoords();
        coords.x = px; coords.y = py;
        coords.pressure = action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL ? 0 : 1;
        MotionEvent event = MotionEvent.obtain(touchDownTime, SystemClock.uptimeMillis(), action, 1,
            new MotionEvent.PointerProperties[]{prop}, new MotionEvent.PointerCoords[]{coords},
            0, 0, 1, 1, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);
        try {
            if (!Boolean.TRUE.equals(inject.invoke(manager, event, 0))) throw new IOException("Touch injection rejected");
        } finally { event.recycle(); }
    }

    private void event(int action, int changed, float horizontal, float vertical) throws Exception {
        MotionEvent.PointerProperties prop = new MotionEvent.PointerProperties();
        prop.id = 0; prop.toolType = MotionEvent.TOOL_TYPE_MOUSE;
        MotionEvent.PointerCoords coords = new MotionEvent.PointerCoords();
        coords.x = x; coords.y = y; coords.pressure = (buttons & ~1) == 0 ? 0 : 1;
        coords.setAxisValue(MotionEvent.AXIS_HSCROLL, horizontal);
        coords.setAxisValue(MotionEvent.AXIS_VSCROLL, vertical);
        long now = SystemClock.uptimeMillis();
        MotionEvent event = MotionEvent.obtain(downTime, now, action, 1,
            new MotionEvent.PointerProperties[]{prop}, new MotionEvent.PointerCoords[]{coords},
            0, buttons & ~1, 1, 1, 0, 0, InputDevice.SOURCE_MOUSE, 0);
        try {
            if (changed != 0) actionButton.invoke(event, changed);
            if (!Boolean.TRUE.equals(inject.invoke(manager, event, 0))) throw new IOException("Mouse injection rejected");
        } finally { event.recycle(); }
    }

    @Override public void close() {
        try { changeButtons(0); } catch (Exception ignored) { }
        if (touchScroll != null) touchScroll.close();
        if (systemCursor != null) systemCursor.close();
        try { cursor.close(); } catch (IOException ignored) { }
    }

    public static void forward(InputStream source, OutputStream target, InputShareXAbsoluteMouse mouse) throws Exception {
        DataInputStream in = new DataInputStream(source);
        while (true) {
            int size;
            try { size = in.readInt(); } catch (EOFException end) { return; }
            if (size <= 0 || size > 1048576) throw new IOException("Invalid control frame size");
            byte[] frame = new byte[size]; in.readFully(frame);
            if ((frame[0] & 255) == 18) mouse.receive(frame);
            else target.write(frame);
        }
    }
}
