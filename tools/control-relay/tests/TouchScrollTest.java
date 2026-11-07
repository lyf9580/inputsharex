import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public final class TouchScrollTest {
    public static void main(String[] args) throws Exception {
        for (int direction : new int[]{-1, 1}) {
            List<int[]> events = Collections.synchronizedList(new ArrayList<>());
            CountDownLatch up = new CountDownLatch(1);
            try (InputShareXTouchScroll scroll = new InputShareXTouchScroll((a, x, y, t) -> {
                events.add(new int[]{a, x, y}); if (a == 1) up.countDown();
            })) {
                scroll.wheel(100, 500, 1000, 1000, direction);
                check(up.await(2, TimeUnit.SECONDS), "wheel did not finish");
                check(events.get(0)[0] == 0, "missing touch down");
                int[] last = events.get(events.size() - 1);
                check(last[0] == 1 && last[2] == 500 + direction * 220, "wrong swipe endpoint");
            }
        }
        List<Integer> events = Collections.synchronizedList(new ArrayList<>());
        CountDownLatch down = new CountDownLatch(1);
        try (InputShareXTouchScroll scroll = new InputShareXTouchScroll((a, x, y, t) -> {
            events.add(a); if (a == 0) down.countDown();
        })) {
            scroll.wheel(100, 500, 1000, 1000, -1);
            check(down.await(2, TimeUnit.SECONDS), "wheel did not start");
            scroll.manual(0, 200, 600);
            scroll.wheel(100, 500, 1000, 1000, -1); // Must not interrupt a held left button.
            scroll.manual(2, 200, 300);
            scroll.manual(1, 200, 300);
        }
        int cancelled = events.indexOf(3);
        check(cancelled >= 0, "manual press must cancel synthetic swipe");
        check(events.subList(cancelled + 1, events.size()).equals(java.util.Arrays.asList(0, 2, 1)),
              "synthetic events interleaved with manual drag");

        CountDownLatch started = new CountDownLatch(1);
        events.clear();
        InputShareXTouchScroll scroll = new InputShareXTouchScroll((a, x, y, t) -> {
            events.add(a); if (a == 0) started.countDown();
        });
        scroll.wheel(100, 500, 1000, 1000, -1);
        check(started.await(2, TimeUnit.SECONDS), "close test never started");
        scroll.close();
        check(events.get(events.size() - 1) == 3, "close left a finger down");
        System.out.println("Touch scroll direction, manual priority, and cleanup passed");
    }
    private static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }
}
