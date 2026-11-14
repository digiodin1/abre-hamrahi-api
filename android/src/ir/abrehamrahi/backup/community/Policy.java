package ir.abrehamrahi.backup.community;

import java.io.IOException;
import java.net.InetAddress;
import java.net.URI;
import java.util.Locale;

public final class Policy {
    private Policy() {}

    public static String name(String value) throws IOException {
        if (value == null || value.isEmpty() || value.length() > 240 ||
                value.equals(".") || value.equals("..") || value.matches(".*[\\\\/\\p{Cntrl}].*")) {
            throw new UserError("نام فایل یا پوشه معتبر نیست؛ نام اصلی تغییر داده نشد.");
        }
        return value;
    }

    public static boolean excluded(String value) {
        return false;
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
