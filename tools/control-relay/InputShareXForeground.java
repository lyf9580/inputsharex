import android.content.ComponentName;

/** Query the focused task only for wheel input, with a short cache. */
public final class InputShareXForeground {
    private long checked;
    private boolean touchScroll;

    public boolean needsTouchScroll() {
        long now = System.nanoTime();
        if (now - checked < 250000000L) return touchScroll;
        checked = now; touchScroll = false;
        try {
            Class<?> cls = Class.forName("android.app.ActivityTaskManager");
            Object manager = cls.getMethod("getService").invoke(null);
            Object task = manager.getClass().getMethod("getFocusedRootTaskInfo").invoke(manager);
            if (task != null) {
                ComponentName activity = (ComponentName) task.getClass().getField("topActivity").get(task);
                if (activity != null) touchScroll = isTouchScrollPackage(activity.getPackageName());
            }
        } catch (ReflectiveOperationException | RuntimeException unsupported) { }
        return touchScroll;
    }

    static boolean isTouchScrollPackage(String name) {
        return "com.ss.android.ugc.aweme".equals(name) || "com.ss.android.ugc.aweme.lite".equals(name);
    }
}
