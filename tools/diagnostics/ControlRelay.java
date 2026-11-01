import android.net.LocalSocket;
import android.net.LocalSocketAddress;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

// Short-lived authenticated diagnostic relay; no installation or persistent service.
public class ControlRelay {
    public static void main(String[] args) throws Exception {
        byte[] token = args[0].getBytes(StandardCharsets.US_ASCII);
        ServerSocket listener = new ServerSocket(Integer.parseInt(args[1]));
        listener.setSoTimeout(15000);
        Thread expiry = new Thread(() -> {
            try { Thread.sleep(60000); } catch (Exception ignored) {}
            System.exit(0);
        });
        expiry.setDaemon(true);
        expiry.start();
        try (Socket tcp = listener.accept()) {
            listener.close();
            tcp.setTcpNoDelay(true);
            tcp.setSoTimeout(5000);
            byte[] received = new byte[token.length];
            new DataInputStream(tcp.getInputStream()).readFully(received);
            if (!Arrays.equals(received, token)) return;
            tcp.setSoTimeout(0);
            try (LocalSocket local = new LocalSocket()) {
                local.connect(new LocalSocketAddress("scrcpy", LocalSocketAddress.Namespace.ABSTRACT));
                Thread back = new Thread(() -> {
                    try { copy(local.getInputStream(), tcp.getOutputStream()); }
                    catch (IOException ignored) {}
                });
                back.setDaemon(true);
                back.start();
                copy(tcp.getInputStream(), local.getOutputStream());
            }
        } finally { listener.close(); }
    }
    static void copy(InputStream in, OutputStream out) throws IOException {
        byte[] data = new byte[8192];
        int n;
        while ((n = in.read(data)) != -1) { out.write(data, 0, n); }
    }
}
