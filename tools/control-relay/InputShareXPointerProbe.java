import android.os.IBinder;
import android.os.ParcelFileDescriptor;
import android.os.Process;
import android.view.InputDevice;
import android.view.MotionEvent;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.regex.*;

/** Reads authoritative InputDispatcher cursor positions, including system gesture areas. */
public final class InputShareXPointerProbe {
    private static volatile boolean running = true;
    private static volatile String command = "0";
    private static volatile int dumpIntervalMs = 100;
    private static final Pattern DEVICE_STATE = Pattern.compile("(?:DeviceId\\()?(-?\\d+)\\)?:\\[");
    private static final Pattern POINT = Pattern.compile("hoveringPointers=\\[Pointer\\(id=\\d+, MOUSE\\) at \\(([-0-9.]+), ([-0-9.]+)\\)");
    // InputDispatcher 里的鼠标坐标是“未旋转的物理/自然坐标”，而窗口/显示已经按 ROTATION_* 旋转；
    // 直接拿它和放置方向比较，设备一转屏（横竖切换）边缘就判断错了。这里从同一份 dump 中解析
    // 当前显示的逻辑尺寸与旋转矩阵，把指针坐标换算成逻辑坐标后再判断边缘。
    private static final Pattern DISPLAY_INFO = Pattern.compile(
        "logicalSize=(\\d+)x(\\d+)[^\\n]*\\n\\s*transform[^\\n]*\\n"
        + "\\s*([-0-9.]+)\\s+([-0-9.]+)\\s+([-0-9.]+)\\s*\\n"
        + "\\s*([-0-9.]+)\\s+([-0-9.]+)\\s+([-0-9.]+)");

    public static void main(String[] args) throws Exception {
        IBinder input = (IBinder) Class.forName("android.os.ServiceManager")
            .getMethod("getService", String.class).invoke(null, "input");
        if (input == null) throw new IOException("Input service unavailable");
        Thread control = new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.US_ASCII))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    line = line.trim();
                    if (line.startsWith("i ")) {
                        try {
                            dumpIntervalMs = Math.max(10, Math.min(500,
                                Integer.parseInt(line.substring(2).trim())));
                        } catch (NumberFormatException ignored) { }
                        continue;
                    }
                    command = line;
                }
            } catch (IOException ignored) { }
            finally { running = false; Process.killProcess(Process.myPid()); }
        }, "pointer-control");
        control.setDaemon(true);
        control.start();
        ExecutorService dumper = Executors.newSingleThreadExecutor();
        System.out.println("READY " + Process.myPid());
        System.out.flush();
        String previous = "";
        boolean interiorSeen = false, sent = false, positionReported = false;
        float lastX = Float.NaN, lastY = Float.NaN;
        while (running) {
            String current = command;
            if (!current.equals(previous)) {
                previous = current;
                interiorSeen = false;
                sent = false;
                positionReported = false;
                lastX = Float.NaN;
                lastY = Float.NaN;
            }
            if (current.startsWith("1 ") && !sent) {
                String[] parts = current.split(" ");
                if (parts.length == 3 || (parts.length == 4 && parts[3].equals("align"))) {
                    boolean aligning = parts.length == 4;
                    int edge = Integer.parseInt(parts[2]);
                    InputDevice mouse = null;
                    for (int id : InputDevice.getDeviceIds()) {
                        InputDevice candidate = InputDevice.getDevice(id);
                        if (candidate != null && candidate.getName().startsWith("inputsharex")
                                && candidate.supportsSource(InputDevice.SOURCE_MOUSE)
                                && (mouse == null || id > mouse.getId())) mouse = candidate;
                    }
                    if (mouse != null) {
                        InputDevice.MotionRange rx = mouse.getMotionRange(MotionEvent.AXIS_X);
                        InputDevice.MotionRange ry = mouse.getMotionRange(MotionEvent.AXIS_Y);
                        if (rx != null && ry != null && rx.getMax() > 0 && ry.getMax() > 0) {
                            String dump = dumpInput(input, dumper);
                            float[] point = findPoint(dump, mouse.getId());
                            if (point != null && current.equals(command)) {
                                float[] logical = logicalPoint(dump, point[0], point[1], rx.getMax(), ry.getMax());
                                float x = logical[0], y = logical[1], maxX = logical[2], maxY = logical[3];
                                boolean changed = !positionReported
                                    || Float.isNaN(lastX) || Math.abs(x - lastX) > 0.5f
                                    || Float.isNaN(lastY) || Math.abs(y - lastY) > 0.5f;
                                if (changed) {
                                    System.out.println("POSITION " + parts[1] + " " + mouse.getId() + " " + x + " " + y + " " + maxX + " " + maxY);
                                    System.out.flush();
                                    lastX = x;
                                    lastY = y;
                                    positionReported = true;
                                }
                                boolean boundary = edge == 0 ? x <= 0.5f : edge == 1 ? x >= maxX - 0.5f
                                    : edge == 2 ? y <= 0.5f : y >= maxY - 0.5f;
                                if (!boundary) interiorSeen = true;
                                if (boundary && interiorSeen && !aligning) {
                                    System.out.println("EDGE " + parts[1] + " " + edge + " " + x + " " + y + " " + maxX + " " + maxY);
                                    System.out.flush();
                                    sent = true;
                                }
                            }
                        }
                    }
                }
            }
            Thread.sleep(dumpIntervalMs);
        }
        dumper.shutdownNow();
    }

    private static String dumpInput(IBinder service, ExecutorService executor) throws Exception {
        ParcelFileDescriptor[] pipe = ParcelFileDescriptor.createPipe();
        Future<?> writing = executor.submit(() -> {
            try { service.dump(pipe[1].getFileDescriptor(), new String[0]); }
            catch (Exception ignored) { }
            finally { try { pipe[1].close(); } catch (IOException ignored) { } }
        });
        ByteArrayOutputStream data = new ByteArrayOutputStream();
        try (InputStream stream = new ParcelFileDescriptor.AutoCloseInputStream(pipe[0])) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = stream.read(buffer)) != -1 && data.size() < 1048576) data.write(buffer, 0, count);
        }
        writing.get(2, TimeUnit.SECONDS);
        return data.toString("UTF-8");
    }

    /**
     * 把 InputDispatcher 的自然方向指针坐标换算成屏幕逻辑坐标。
     * 设备旋转后 dump 会给出 display 的 logicalSize 和 3x3 变换矩阵（自然坐标 -> 逻辑坐标），
     * 解析失败时退回原始坐标与 MotionRange，保持旧行为。
     */
    private static float[] logicalPoint(String dump, float nx, float ny, float rawMaxX, float rawMaxY) {
        try {
            Matcher m = DISPLAY_INFO.matcher(dump);
            if (!m.find()) return new float[]{nx, ny, rawMaxX, rawMaxY};
            int w = Integer.parseInt(m.group(1));
            int h = Integer.parseInt(m.group(2));
            float a = Float.parseFloat(m.group(3)), b = Float.parseFloat(m.group(4)), c = Float.parseFloat(m.group(5));
            float d = Float.parseFloat(m.group(6)), e = Float.parseFloat(m.group(7)), f = Float.parseFloat(m.group(8));
            float maxX = w - 1f, maxY = h - 1f;
            float lx = a * nx + b * ny + c;
            float ly = d * nx + e * ny + f;
            lx = Math.max(0f, Math.min(maxX, lx));
            ly = Math.max(0f, Math.min(maxY, ly));
            return new float[]{lx, ly, maxX, maxY};
        } catch (RuntimeException ignored) {
            return new float[]{nx, ny, rawMaxX, rawMaxY};
        }
    }

    private static float[] findPoint(String dump, int id) {
        // Device states can span several lines, including when injected input
        // and the UHID mouse share the same window on Android 16.
        Matcher state = DEVICE_STATE.matcher(dump);
        int lastId = Integer.MIN_VALUE, lastEnd = 0;
        while (state.find()) {
            if (lastId == id) {
                Matcher point = POINT.matcher(dump.substring(lastEnd, state.start()));
                if (point.find()) {
                    return new float[]{Float.parseFloat(point.group(1)), Float.parseFloat(point.group(2))};
                }
            }
            lastId = Integer.parseInt(state.group(1));
            lastEnd = state.end();
        }
        if (lastId == id) {
            Matcher point = POINT.matcher(dump.substring(lastEnd));
            if (point.find()) return new float[]{Float.parseFloat(point.group(1)), Float.parseFloat(point.group(2))};
        }
        return null;
    }
}
