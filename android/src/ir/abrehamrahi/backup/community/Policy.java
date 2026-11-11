package ir.abrehamrahi.backup.community;

import java.io.IOException;
import java.net.InetAddress;
import java.net.URI;
import java.util.Locale;

public final class Policy {
    public static final int MAX_FILES = 5000;
    public static final long MAX_BYTES = 100L * 1024 * 1024;

    private Policy() {}

    public static String name(String value) throws IOException {
        if (value == null || value.isEmpty() || value.length() > 240 ||
                value.equals(".") || value.equals("..") || value.matches(".*[\\\\/\\p{Cntrl}].*")) {
            throw new UserError("نام فایل یا پوشه معتبر نیست؛ نام اصلی تغییر داده نشد.");
        }
        return value;
    }

    public static boolean excluded(String value) {
        String lower = value.toLowerCase(Locale.ROOT);
        return lower.equals(".git") || lower.equals("node_modules") || lower.equals(".qa") ||
            lower.equals(".venv") || lower.equals(".backup") || lower.equals("build") ||
            lower.equals("dist") || lower.equals("__pycache__") || lower.startsWith(".env") ||
            lower.equals(".private") || lower.equals(".runtime") || lower.equals("credentials") ||
            lower.equals("credentials.json") || lower.equals(".ssh") || lower.equals(".aws") ||
            lower.equals(".azure") || lower.equals(".kube") || lower.equals(".npmrc") ||
            lower.equals(".pypirc") || lower.equals(".netrc") || lower.startsWith("id_rsa") ||
            lower.startsWith("id_ed25519") || lower.endsWith(".dpapi") || lower.endsWith(".keystore") ||
            lower.startsWith("session.") || lower.endsWith(".key") || lower.endsWith(".pem") ||
            lower.endsWith(".jks") || lower.endsWith(".p12") || lower.endsWith(".pfx") ||
            lower.endsWith(".tmp") || lower.endsWith(".log");
    }

    public static URI transferUri(String value) throws IOException {
        try {
            URI uri = new URI(value);
            if (!"https".equals(uri.getScheme()) || uri.getHost() == null ||
                    uri.getUserInfo() != null || uri.getFragment() != null ||
                    (uri.getPort() != -1 && uri.getPort() != 443)) {
                throw new UserError("نشانی انتقال امن نیست.");
            }
            return uri;
        } catch (java.net.URISyntaxException error) {
            throw new UserError("نشانی انتقال معتبر نیست.");
        }
    }

    public static boolean publicAddress(InetAddress address) {
        byte[] bytes = address.getAddress();
        if (address.isAnyLocalAddress() || address.isLoopbackAddress() ||
                address.isLinkLocalAddress() || address.isSiteLocalAddress() || address.isMulticastAddress()) {
            return false;
        }
        if (bytes.length == 4) {
            int first = bytes[0] & 255, second = bytes[1] & 255;
            return first != 0 && first < 224 && !(first == 100 && second >= 64 && second <= 127) &&
                !(first == 192 && second == 0) && !(first == 198 && (second == 18 || second == 19)) &&
                !(first == 198 && second == 51 && (bytes[2] & 255) == 100) &&
                !(first == 203 && second == 0 && (bytes[2] & 255) == 113);
        }
        if ((bytes[0] & 0xE0) != 0x20 || (bytes[0] == 0x20 && bytes[1] == 0x02)) return false;
        return !(bytes[0] == 0x20 && bytes[1] == 0x01 &&
            ((bytes[2] == 0 && (bytes[3] & 255) < 0x30) || (bytes[2] == 0x0D && (bytes[3] & 255) == 0xB8)));
    }

    public static void safeDestination(String value) throws IOException {
        URI uri = transferUri(value);
        InetAddress[] addresses = InetAddress.getAllByName(uri.getHost());
        InetAddress[] again = InetAddress.getAllByName(uri.getHost());
        if (!new java.util.HashSet<>(java.util.Arrays.asList(addresses)).equals(
                new java.util.HashSet<>(java.util.Arrays.asList(again)))) {
            throw new UserError("میزبان انتقال حین بررسی تغییر کرد.");
        }
        if (addresses.length == 0) throw new UserError("میزبان انتقال پیدا نشد.");
        for (InetAddress address : addresses) {
            if (!publicAddress(address)) throw new UserError("انتقال به شبکهٔ خصوصی مجاز نیست.");
        }
    }

    public static String hex(byte[] bytes) {
        StringBuilder text = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) text.append(String.format(Locale.ROOT, "%02x", value & 255));
        return text.toString();
    }
}
