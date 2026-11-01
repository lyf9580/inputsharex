import android.net.LocalSocket;
import android.net.LocalSocketAddress;
import android.os.Process;
import android.util.Base64;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import javax.net.ssl.*;

/** One authenticated, encrypted control connection; credentials arrive over adb stdin. */
public final class InputShareXRelay {
    public static void main(String[] args) throws Exception {
        BufferedReader setup = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.US_ASCII));
        String encoded = setup.readLine();
        String password = setup.readLine();
        String token = setup.readLine();
        if (encoded == null || encoded.length() > 32768 || password == null || token == null || token.length() != 64) {
            throw new IOException("Invalid relay setup");
        }
        KeyStore store = KeyStore.getInstance("PKCS12");
        store.load(new ByteArrayInputStream(Base64.decode(encoded, Base64.DEFAULT)), password.toCharArray());
        KeyManagerFactory managers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
        managers.init(store, password.toCharArray());
        SSLContext context = SSLContext.getInstance("TLS");
        context.init(managers.getKeyManagers(), null, null);
        try (SSLServerSocket listener = (SSLServerSocket) context.getServerSocketFactory()
                .createServerSocket(0, 4, InetAddress.getByName(args[0]))) {
            listener.setEnabledProtocols(new String[]{"TLSv1.2", "TLSv1.3"});
            listener.setSoTimeout(15000);
            System.out.println("READY " + listener.getLocalPort() + " " + Process.myPid());
            System.out.flush();
            long deadline = System.nanoTime() + 15000000000L;
            while (System.nanoTime() < deadline) {
                try (SSLSocket tcp = (SSLSocket) listener.accept()) {
                    tcp.setTcpNoDelay(true);
                    tcp.setSoTimeout(3000);
                    tcp.startHandshake();
                    byte[] received = new byte[64];
                    new DataInputStream(tcp.getInputStream()).readFully(received);
                    if (!MessageDigest.isEqual(received, token.getBytes(StandardCharsets.US_ASCII))) continue;
                    listener.close();
                    // Keepalive reports maintain active connections; failed peers expire.
                    tcp.setSoTimeout(30000);
                    try (LocalSocket local = new LocalSocket()) {
                        local.connect(new LocalSocketAddress("scrcpy", LocalSocketAddress.Namespace.ABSTRACT));
                        Thread reverse = new Thread(() -> {
                            try { copy(local.getInputStream(), tcp.getOutputStream()); }
                            catch (IOException ignored) { }
                            finally { try { tcp.close(); } catch (IOException ignored) { } }
                        }, "relay-replies");
                        reverse.setDaemon(true);
                        reverse.start();
                        copy(tcp.getInputStream(), local.getOutputStream());
                    }
                    return;
                } catch (IOException failure) {
                    if (listener.isClosed()) return;
                    System.err.println("Relay connection rejected: " + failure.getClass().getSimpleName());
                }
            }
        }
    }

    private static void copy(InputStream source, OutputStream target) throws IOException {
        byte[] buffer = new byte[8192];
        int count;
        while ((count = source.read(buffer)) >= 0) {
            if (count > 0) target.write(buffer, 0, count);
        }
    }
}
