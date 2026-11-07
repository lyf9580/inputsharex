import java.util.ArrayDeque;

/** Serializes synthetic wheel swipes and real left-button touch; movement never waits. */
public final class InputShareXTouchScroll implements AutoCloseable {
    public interface Sink { void send(int action, int x, int y, long downTime) throws Exception; }
    private final Sink sink;
    private final ArrayDeque<int[]> pending = new ArrayDeque<>();
    private final Thread worker;
    private boolean closed, wheelActive, manualActive;
    private int generation, lastX, lastY;
    private long downTime;

    public InputShareXTouchScroll(Sink sink) {
        this.sink = sink;
        worker = new Thread(this::run, "inputsharex-wheel-touch");
        worker.setDaemon(true); worker.start();
    }

    static long now() { return System.nanoTime() / 1000000; }

    public synchronized void wheel(int x, int y, int width, int height, float vertical) {
        if (closed || manualActive || vertical == 0 || width < 16 || height < 64) return;
        int distance = Math.max(32, Math.min(600, Math.round(height * .22f)));
        int start = Math.max(4, Math.min(height - 5, y));
        int end = Math.max(4, Math.min(height - 5, start + (vertical > 0 ? distance : -distance)));
        if (Math.abs(end - start) < 24) return;
        // Bound burst latency; mouse wheel drivers may send dozens of ticks at once.
        int steps = Math.min(3, Math.max(1, Math.round(Math.abs(vertical))));
        while (steps-- > 0 && pending.size() < 3)
            pending.add(new int[]{Math.max(4, Math.min(width - 5, x)), start, end});
        notifyAll();
    }

    public synchronized void manual(int action, int x, int y) throws Exception {
        if (closed) return;
        if (action == 0) {
            cancelWheel();
            manualActive = true; downTime = now();
        } else if (!manualActive) return;
        lastX = x; lastY = y;
        sink.send(action, x, y, downTime);
        if (action == 1 || action == 3) manualActive = false;
    }

    public synchronized boolean isBusy() { return wheelActive || manualActive; }

    private void cancelWheel() throws Exception {
        pending.clear(); generation++;
        if (wheelActive) {
            sink.send(3, lastX, lastY, downTime);
            wheelActive = false;
        }
    }

    public synchronized void cancel() throws Exception {
        cancelWheel();
        if (manualActive) {
            sink.send(3, lastX, lastY, downTime); manualActive = false;
        }
    }

    private void run() {
        try {
            while (true) {
                int[] path; int ticket;
                synchronized (this) {
                    while (!closed && pending.isEmpty()) wait();
                    if (closed) return;
                    path = pending.remove(); ticket = generation;
                    downTime = now(); lastX = path[0]; lastY = path[1];
                    wheelActive = true; sink.send(0, lastX, lastY, downTime);
                }
                for (int step = 1; step <= 8; step++) {
                    Thread.sleep(18);
                    synchronized (this) {
                        if (closed || ticket != generation) break;
                        lastY = path[1] + Math.round((path[2] - path[1]) * step / 8f);
                        sink.send(2, lastX, lastY, downTime);
                        if (step == 8) {
                            sink.send(1, lastX, lastY, downTime); wheelActive = false;
                        }
                    }
                }
            }
        } catch (InterruptedException done) {
            Thread.currentThread().interrupt();
        } catch (Exception failure) {
            System.err.println("Touch scroll failed: " + failure);
        } finally {
            synchronized (this) {
                try { cancel(); } catch (Exception ignored) { }
                closed = true;
            }
        }
    }

    @Override public void close() {
        synchronized (this) {
            try { cancel(); } catch (Exception ignored) { }
            closed = true; notifyAll();
        }
        worker.interrupt();
        try { worker.join(500); } catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
    }
}
