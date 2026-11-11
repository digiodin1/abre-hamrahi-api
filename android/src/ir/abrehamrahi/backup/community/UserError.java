package ir.abrehamrahi.backup.community;

import java.io.IOException;

public final class UserError extends IOException {
    public UserError(String message) { super(message); }
}
