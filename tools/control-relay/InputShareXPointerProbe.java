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
    private static final Pattern DEVICE_STATE = Pattern.compile("(?:DeviceId\\()?(\\d+)\\)?:\\[");
    private static final Pattern POINT = Pattern.compile("hoveringPointers=\\[Pointer\\(id=\\d+, MOUSE\\) at \\(([-0-9.]+), ([-0-9.]+)\\)");

    public static void main(String[] args) throws Exception {
        IBinder input = (IBinder) Class.forName("android.os.ServiceManager")
            .getMethod("getService", String.class).invoke(null, "input");
        if (input == null) throw new IOException("Input service unavailable");
        Thread control = new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.US_ASCII))) {
                String line;
                while ((line = reader.readLine()) != null) command = line.trim();
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
        while (running) {
            String current = command;
            if (!current.equals(previous)) {
                previous = current;
                interiorSeen = false;
                sent = false;
                positionReported = false;
            }
            if (current.startsWith("1 ") && !sent) {
                String[] parts = current.split(" ");
                if (parts.length == 3) {
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
                            float[] point = findPoint(dumpInput(input, dumper), mouse.getId());
                            if (point != null && current.equals(command)) {
                                float x = point[0], y = point[1], maxX = rx.getMax(), maxY = ry.getMax();
                                if (!positionReported) {
                                    System.out.println("POSITION " + parts[1] + " " + mouse.getId() + " " + x + " " + y + " " + maxX + " " + maxY);
                                    System.out.flush();
                                    positionReported = true;
                                }
                                boolean boundary = edge == 0 ? x <= 0.5f : edge == 1 ? x >= maxX - 0.5f
                                    : edge == 2 ? y <= 0.5f : y >= maxY - 0.5f;
                                if (!boundary) interiorSeen = true;
                                if (boundary && interiorSeen) {
                                    System.out.println("EDGE " + parts[1] + " " + edge + " " + x + " " + y + " " + maxX + " " + maxY);
                                    System.out.flush();
                                    sent = true;
                                }
                            }
                        }
                    }
                }
            }
            Thread.sleep(100);
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

    private static float[] findPoint(String dump, int id) {
        String[] lines = dump.split("\n");
        for (int i = 0; i < lines.length; i++) {
            int at = lines[i].indexOf("mDeviceStates=");
            if (at < 0) continue;
            // Android 12–16: mDeviceStates=296:[...]
            // Android 17:    mDeviceStates=
            //                  DeviceId(15):[...]
            StringBuilder block = new StringBuilder(lines[i].substring(at + "mDeviceStates=".length()));
            int j = i + 1;
            while (block.indexOf("[") < 0 && j < lines.length && j <= i + 2) {
                block.append('\n').append(lines[j].trim());
                j++;
            }
            Matcher state = DEVICE_STATE.matcher(block);
            int regionStart = 0, lastId = -1, lastEnd = 0;
            while (true) {
                state.region(regionStart, block.length());
                if (!state.find()) break;
                if (lastId == id) {
                    Matcher point = POINT.matcher(block.substring(lastEnd, state.start()));
                    if (point.find()) return new float[]{Float.parseFloat(point.group(1)), Float.parseFloat(point.group(2))};
                }
                lastId = Integer.parseInt(state.group(1));
                lastEnd = state.end();
                regionStart = lastEnd;
            }
            if (lastId == id) {
                Matcher point = POINT.matcher(block.substring(lastEnd));
                if (point.find()) return new float[]{Float.parseFloat(point.group(1)), Float.parseFloat(point.group(2))};
            }
        }
        return null;
    }
}
