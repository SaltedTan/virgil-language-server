// Copyright 2008 Google Inc. All rights reserved.
// See LICENSE for details of Apache 2.0 license.
//
// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0
//
// Adapted from Virgil's rt/jvm/src/V3S_System.java at revision
// b86f43f102406e94dc2ac4a85909c38ef414e214, under the Apache License 2.0 (see
// THIRD_PARTY_NOTICES.md). The methods after "Unchanged from upstream" are
// copied verbatim, and test/jvm/V3SSystemTest.java compares their behavior with
// the compiled pinned copy. The I/O methods and the host bridge before it are this
// repository's.

import java.io.*;
import java.lang.reflect.Field;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
import java.nio.channels.SeekableByteChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.BasicFileAttributeView;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The runtime class of Virgil programs compiled to Java bytecode (the jar
 * target), extended with the host operations that the language server and its
 * analysis worker need.
 *
 * <p>Aeneas compiles each Virgil {@code System} call to an invokestatic of the
 * method of the same name in this class, and a Virgil program can't call any
 * other Java code. So host operations travel through two of those calls:
 * {@code System.fileOpen(name, _)} runs a command that returns an int, and
 * {@code System.fileLoad(name)} runs one that returns bytes. A command name
 * is byte 1, the operation, and each argument after a 0 byte, as in
 * {@code "\x01openAt\x0012\x00src\x000"}. Arguments are UTF-8 and numbers are
 * decimal. POSIX paths and arguments can't contain a 0 byte, so any of them
 * can be passed. An int reply is the result, or a negated Linux errno on every
 * host. A byte reply starts with a status byte: 0 and then the payload, or an
 * errno and nothing else. Any other name is an ordinary file name, as in
 * upstream's runtime.
 *
 * <p>JVM limitation: {@code openRead} and regular-file {@code openAt} check
 * the file type before opening, then check the opened channel's size and
 * seekability. Public Java APIs expose no channel file-type query. A local
 * process racing the server can swap a checked regular file for a FIFO before
 * open; that open can block until a writer appears, before channel validation.
 *
 * <p>Int commands:
 * <ul>
 * <li>{@code pipe}: a pipe's read end r; its write end is r + 1. A pipe
 *     carries data only once {@code spawn} connects it to a child.
 * <li>{@code null}: a descriptor that reads as empty, as /dev/null.
 * <li>{@code openRead path}: a regular file opened for reading, following
 *     symlinks; EISDIR for a directory, EINVAL for other kinds of file.
 * <li>{@code openDirectory path}: a directory, following symlinks.
 * <li>{@code openAt fd name directory}: the entry {@code name} of directory
 *     {@code fd}, never following a symlink, as openat with O_NOFOLLOW (and
 *     O_DIRECTORY if {@code directory} is 1): ELOOP for a symlink, or ENOTDIR
 *     if a directory was asked for. Regular files and directories are opened
 *     at once; other kinds of file are not opened when the metadata check
 *     identifies them. See the JVM check/open race limitation above.
 * <li>{@code nonblock fd}, {@code close fd}: 0.
 * <li>{@code access path}: 0 if {@code path} is executable.
 * <li>{@code spawn n fd0 .. fd(n-1) arg0 arg1 ..}: starts {@code arg0} with the
 *     arguments and returns its process ID. In the child, descriptor i becomes
 *     the parent's descriptor {@code fdi}. Each must be a {@code null}
 *     descriptor, standard error, or an unconnected pipe end; at most one read
 *     end and one write end. See {@link Spawn}.
 * <li>{@code kill pid signal}: signal 9 or 15 to a child that hasn't been
 *     waited for.
 * <li>{@code wait pid}: waits for a child to end and returns its wait status:
 *     the exit status times 256, or the signal number. Java collapses normal
 *     exits 129–159 and signal exits, so those values mean signal (status - 128);
 *     the server worker never exits normally in that range.
 * <li>{@code alarm ms}: halts this process as if by SIGALRM (exit status 142)
 *     after {@code ms} milliseconds, unless called again first; 0 cancels.
 * <li>{@code gc}: runs the collector.
 * </ul>
 *
 * <p>Byte commands:
 * <ul>
 * <li>{@code poll timeoutMs fd events ..}: one revents byte for each
 *     descriptor, once any has an event or the timeout passes; a negative
 *     timeout waits indefinitely, and a negative descriptor is ignored.
 * <li>{@code stat fd}, {@code statAt fd name}: two little-endian longs, the
 *     file type bits of st_mode and the size; {@code statAt} doesn't follow a
 *     symlink.
 * <li>{@code readDirectory fd size}: at most {@code size} bytes of
 *     linux_dirent64 records, without "." and ".."; empty at the end.
 * <li>{@code random}: 16 random bytes.
 * <li>{@code executable}: this program's path: the {@code virgil.lsp.executable}
 *     system property, which a launcher sets, or else the jar's path.
 * <li>{@code memory}: two little-endian longs, the maximum heap size and the
 *     heap in use, in bytes.
 * </ul>
 *
 * <p>{@code System.read} and {@code System.write} use the same descriptor
 * table, return a negated errno on failure, and {@code read} returns 0 at
 * the end of the input. Descriptors 0 to 2 are standard input, output, and
 * error; the others are numbered from {@link #MIN_FD}, lowest free first.
 */
public class V3S_System {
    private static final int PRINT_SIZE = 128;

    private static final int INT_MAX = 0x7fffffff;
    private static final long LONG_MAX = 0x7fffffffffffffffL;
    private static final long U64_MAX = 0xffffffffffffffffL;

    // Linux errno values, which the bridge reports on every host.
    static final int ENOENT = 2, ESRCH = 3, EIO = 5, EBADF = 9, ECHILD = 10, EAGAIN = 11,
        EACCES = 13, ENOTDIR = 20, EISDIR = 21, EINVAL = 22, ENFILE = 23, EMFILE = 24,
        ENOSPC = 28, EROFS = 30, EPIPE = 32, ENAMETOOLONG = 36, ENOSYS = 38, ELOOP = 40, ENOTSUP = 95;
    // Bits of poll's events and revents.
    static final int POLLIN = 0x1, POLLOUT = 0x4, POLLERR = 0x8, POLLHUP = 0x10, POLLNVAL = 0x20;
    // File type bits of st_mode.
    static final long S_IFIFO = 0x1000, S_IFCHR = 0x2000, S_IFDIR = 0x4000, S_IFREG = 0x8000, S_IFLNK = 0xA000;
    // Linux d_type values.
    static final int DT_UNKNOWN = 0, DT_DIR = 4, DT_REG = 8, DT_LNK = 10;
    // The first descriptor that the table assigns, as HostOs.MIN_FD natively.
    static final int MIN_FD = 10;
    // A child's descriptor layout, from spawn: a comma-separated kind for each
    // descriptor from 0 (null, err, in, or out).
    static final String LAYOUT_ENV = "VIRGIL_LSP_JVM_FDS";
    // The exit status of a process that its alarm ended: 128 + SIGALRM.
    static final int ALARM_STATUS = 128 + 14;
    // How many bytes a readable stream buffers before its pump thread waits.
    static final int INPUT_LIMIT = 1 << 20;

    // Guards all of the state below, and is notified whenever a descriptor's
    // readiness may have changed.
    static final Object LOCK = new Object();
    static final HashMap<Integer, End> fds = new HashMap<Integer, End>();
    static final HashMap<Integer, Process> children = new HashMap<Integer, Process>();
    static int nextSyntheticPid = 1 << 30;
    static Timer alarm;
    static java.security.SecureRandom random;

    static {
        String layout = System.getenv(LAYOUT_ENV);
        if (layout == null) {
            fds.put(0, new Input(new FileInputStream(FileDescriptor.in), false));
            fds.put(1, new Output(new FileOutputStream(FileDescriptor.out), false, false, false));
            fds.put(2, new Output(new FileOutputStream(FileDescriptor.err), false, false, true));
        } else {
            // A child of spawn. Its standard output may be a pipe to the
            // parent, so Java's System.out goes to standard error instead.
            System.setOut(System.err);
            String[] kinds = layout.split(",", -1);
            for (int i = 0; i < kinds.length && i < MIN_FD; i++) {
                String k = kinds[i];
                if (k.equals("in")) fds.put(i, new Input(new FileInputStream(FileDescriptor.in), true));
                else if (k.equals("out")) fds.put(i, new Output(new FileOutputStream(FileDescriptor.out), true, false, false));
                else if (k.equals("err")) fds.put(i, new Output(new FileOutputStream(FileDescriptor.err), false, false, true));
                else if (k.equals("null")) fds.put(i, new Null());
            }
        }
    }

    // ---- Descriptors ---------------------------------------------------

    // An entry of the descriptor table. Methods other than the I/O ones run
    // with LOCK held.
    abstract static class End {
        boolean nonblocking;
        int read(byte[] b, int off, int len) { return -EBADF; }
        int write(byte[] b, int off, int len) { return -EBADF; }
        int available() { return 0; }
        int revents(int events) { return 0; }
        long[] stat() { return new long[] {S_IFIFO, 0}; }
        // Called once, without LOCK, after the descriptor is removed from the table.
        void close() { }
    }

    // A readable stream, read by a daemon pump thread so that poll can see
    // when it has data or has ended.
    static final class Input extends End {
        final InputStream in;
        final boolean owned;
        final ArrayDeque<byte[]> chunks = new ArrayDeque<byte[]>();
        int headPos, buffered, errno;
        boolean eof, closed;
        Thread pump;
        Input(InputStream in, boolean owned) { this.in = in; this.owned = owned; }
        // With LOCK held.
        void start() {
            if (pump != null) return;
            pump = new Thread(new Runnable() { public void run() { pump(); } }, "V3S_System-input");
            pump.setDaemon(true);
            pump.start();
        }
        void pump() {
            byte[] buf = new byte[65536];
            int err = 0;
            while (true) {
                synchronized (LOCK) {
                    while (buffered >= INPUT_LIMIT && !closed) {
                        try { LOCK.wait(); } catch (InterruptedException e) { }
                    }
                    if (closed) return;
                }
                int n;
                try {
                    n = in.read(buf, 0, buf.length);
                } catch (IOException e) {
                    err = EIO;
                    break;
                }
                if (n < 0) break;
                if (n == 0) continue;
                synchronized (LOCK) {
                    chunks.add(Arrays.copyOf(buf, n));
                    buffered += n;
                    LOCK.notifyAll();
                }
            }
            synchronized (LOCK) {
                eof = true;
                errno = err;
                LOCK.notifyAll();
            }
        }
        int read(byte[] b, int off, int len) {
            synchronized (LOCK) {
                start();
                while (chunks.isEmpty() && !eof) {
                    if (nonblocking) return -EAGAIN;
                    try { LOCK.wait(); } catch (InterruptedException e) { return -4; }
                }
                if (chunks.isEmpty()) return errno != 0 ? -errno : 0;
                int n = 0;
                while (n < len && !chunks.isEmpty()) {
                    byte[] head = chunks.peek();
                    int k = Math.min(len - n, head.length - headPos);
                    System.arraycopy(head, headPos, b, off + n, k);
                    n += k;
                    headPos += k;
                    if (headPos == head.length) {
                        chunks.poll();
                        headPos = 0;
                    }
                }
                buffered -= n;
                LOCK.notifyAll();
                return n;
            }
        }
        int available() {
            synchronized (LOCK) { return buffered; }
        }
        int revents(int events) {
            start();
            if (!chunks.isEmpty()) return events & POLLIN;
            return eof ? POLLHUP : 0;
        }
        void close() {
            synchronized (LOCK) {
                closed = true;
                LOCK.notifyAll();
            }
            if (owned) {
                try { in.close(); } catch (IOException e) { }
            }
        }
    }

    // A writable stream. An asynchronous one, a pipe to a child, is written by
    // a daemon thread, so that a write never blocks this program; closing it
    // closes the stream once the queued bytes are written.
    static final class Output extends End {
        final OutputStream out;
        final boolean owned, async, stderr;
        final ArrayDeque<byte[]> queue = new ArrayDeque<byte[]>();
        boolean failed, closed;
        Output(OutputStream out, boolean owned, boolean async, boolean stderr) {
            this.out = out;
            this.owned = owned;
            this.async = async;
            this.stderr = stderr;
            if (async) {
                Thread writer = new Thread(new Runnable() { public void run() { drain(); } }, "V3S_System-output");
                writer.setDaemon(true);
                writer.start();
            }
        }
        void drain() {
            while (true) {
                byte[] b;
                synchronized (LOCK) {
                    while (queue.isEmpty() && !closed) {
                        try { LOCK.wait(); } catch (InterruptedException e) { }
                    }
                    if (queue.isEmpty()) break;
                    b = queue.poll();
                }
                try {
                    out.write(b);
                    out.flush();
                } catch (IOException e) {
                    synchronized (LOCK) {
                        failed = true;
                        queue.clear();
                        LOCK.notifyAll();
                    }
                }
            }
            try { out.close(); } catch (IOException e) { }
        }
        int write(byte[] b, int off, int len) {
            if (!async) {
                try {
                    out.write(b, off, len);
                    out.flush();
                    return len;
                } catch (IOException e) {
                    return -EPIPE;
                }
            }
            synchronized (LOCK) {
                if (failed) return -EPIPE;
                queue.add(Arrays.copyOfRange(b, off, off + len));
                LOCK.notifyAll();
                return len;
            }
        }
        int revents(int events) {
            return (events & POLLOUT) | (failed ? POLLERR : 0);
        }
        void close() {
            if (async) {
                synchronized (LOCK) {
                    closed = true;
                    LOCK.notifyAll();
                }
            } else if (owned) {
                try { out.close(); } catch (IOException e) { }
            }
        }
    }

    // Reads as empty, as /dev/null opened for reading.
    static final class Null extends End {
        int read(byte[] b, int off, int len) { return 0; }
        int revents(int events) { return events & POLLIN; }
        long[] stat() { return new long[] {S_IFCHR, 0}; }
    }

    // A regular file opened for reading.
    static final class FileEnd extends End {
        final SeekableByteChannel channel;
        FileEnd(SeekableByteChannel channel) { this.channel = channel; }
        int read(byte[] b, int off, int len) {
            try {
                int n = channel.read(ByteBuffer.wrap(b, off, len));
                return n < 0 ? 0 : n;
            } catch (IOException e) {
                return -errno(e);
            }
        }
        int available() {
            try {
                return (int) Math.min(INT_MAX, Math.max(0, channel.size() - channel.position()));
            } catch (IOException e) {
                return 0;
            }
        }
        int revents(int events) { return events & (POLLIN | POLLOUT); }
        long[] stat() {
            try {
                return new long[] {S_IFREG, channel.size()};
            } catch (IOException e) {
                return new long[] {-errno(e), -1};
            }
        }
        void close() {
            try { channel.close(); } catch (IOException e) { }
        }
    }

    // A directory, listed through a stream opened with it.
    static final class DirEnd extends End {
        final SecureDirectoryStream<Path> stream;
        final Iterator<Path> entries;
        byte[] pending;
        DirEnd(SecureDirectoryStream<Path> stream) {
            this.stream = stream;
            this.entries = stream.iterator();
        }
        int read(byte[] b, int off, int len) { return -EISDIR; }
        int revents(int events) { return events & (POLLIN | POLLOUT); }
        long[] stat() {
            try {
                BasicFileAttributes a = stream.getFileAttributeView(BasicFileAttributeView.class).readAttributes();
                return new long[] {kind(a), a.size()};
            } catch (Exception e) {
                return new long[] {-errno(e), -1};
            }
        }
        void close() {
            try { stream.close(); } catch (IOException e) { }
        }
    }

    // A file that is neither regular nor a directory, such as a FIFO. It is
    // never opened, so it can only be examined.
    static final class OtherEnd extends End {
        final long kind;
        OtherEnd(long kind) { this.kind = kind; }
        int read(byte[] b, int off, int len) { return -EINVAL; }
        long[] stat() { return new long[] {kind, 0}; }
    }

    // A pipe, before spawn connects it to a child.
    static final class Pipe {
        PipeEnd readEnd, writeEnd;
        boolean connected;
    }
    static final class PipeEnd extends End {
        final Pipe pipe;
        final int fd;
        final boolean readSide;
        boolean closed;
        PipeEnd(Pipe pipe, int fd, boolean readSide) {
            this.pipe = pipe;
            this.fd = fd;
            this.readSide = readSide;
        }
        int read(byte[] b, int off, int len) { return -EINVAL; }
        int write(byte[] b, int off, int len) { return -EINVAL; }
        void close() {
            synchronized (LOCK) { closed = true; }
        }
    }

    // With LOCK held: the lowest free descriptor from MIN_FD such that
    // {span} descriptors in a row are free.
    static int lowestFree(int span) {
        int fd = MIN_FD;
        while (true) {
            boolean free = true;
            for (int i = 0; i < span; i++) {
                if (fds.containsKey(fd + i)) { free = false; break; }
            }
            if (free) return fd;
            fd++;
        }
    }
    static int add(End e) {
        synchronized (LOCK) {
            int fd = lowestFree(1);
            fds.put(fd, e);
            return fd;
        }
    }
    static End get(int fd) {
        synchronized (LOCK) { return fds.get(fd); }
    }
    static int close(int fd) {
        End e;
        synchronized (LOCK) {
            e = fds.remove(fd);
            if (e == null) return -EBADF;
        }
        e.close();
        synchronized (LOCK) { LOCK.notifyAll(); }
        return 0;
    }
    // For tests: how many descriptors and unwaited children there are.
    static int descriptorCount() {
        synchronized (LOCK) { return fds.size(); }
    }
    static int childCount() {
        synchronized (LOCK) { return children.size(); }
    }

    // ---- Virgil System calls -------------------------------------------

    public static void putc(char ch) {
        write(1, new byte[] {(byte) ch}, 0, 1);
    }

    public static void puts(byte[] str) {
        write(1, str, 0, str.length);
    }

    public static void puti(int i) {
        puts(Integer.toString(i).getBytes(StandardCharsets.US_ASCII));
    }

    public static void ln() {
        putc('\n');
    }

    // A byte, or -1 at the end of the input or on an error.
    public static int fileRead(int fd) {
        byte[] b = new byte[1];
        return read(fd, b, 0, 1) == 1 ? b[0] & 0xFF : -1;
    }

    public static void fileWriteK(int fd, byte[] b, int offset, int length) {
        write(fd, b, offset, length);
    }

    // The bytes read, or 0 at the end of the input or on an error.
    public static int fileReadK(int fd, byte[] b, int offset, int length) {
        return Math.max(0, read(fd, b, offset, length));
    }

    public static int write(int fd, byte[] b, int offset, int length) {
        End e = get(fd);
        return e == null ? -EBADF : e.write(b, offset, length);
    }

    public static int read(int fd, byte[] b, int offset, int length) {
        End e = get(fd);
        return e == null ? -EBADF : e.read(b, offset, length);
    }

    public static int fileOpen(byte[] name, boolean input) {
        if (Commands.isCommand(name)) return Commands.run(Commands.split(name));
        try {
            Path p = Paths.get(new String(name, StandardCharsets.UTF_8));
            if (input) return add(new FileEnd(FileChannel.open(p, StandardOpenOption.READ)));
            return add(new Output(new FileOutputStream(p.toFile()), true, false, false));
        } catch (Exception e) {
            return -1;
        }
    }

    public static int fileLeft(int fd) {
        End e = get(fd);
        return e == null ? 0 : e.available();
    }

    public static void fileClose(int fd) {
        close(fd);
    }

    public static byte[] fileLoad(byte[] fname) {
        if (Commands.isCommand(fname)) return Commands.query(Commands.split(fname));
        try {
            return Files.readAllBytes(Paths.get(new String(fname, StandardCharsets.UTF_8)));
        } catch (Exception e) {
            return null;
        }
    }

    // ---- Host operations -----------------------------------------------

    static int errno(Exception e) {
        if (e instanceof UnsupportedOperationException) return ENOTSUP;
        if (e instanceof NoSuchFileException) return ENOENT;
        if (e instanceof AccessDeniedException) return EACCES;
        if (e instanceof NotDirectoryException) return ENOTDIR;
        if (e instanceof FileSystemLoopException) return ELOOP;
        if (e instanceof DirectoryIteratorException) return errno(((DirectoryIteratorException) e).getCause());
        if (e instanceof FileSystemException) {
            String reason = ((FileSystemException) e).getReason();
            if (reason != null) {
                if (reason.startsWith("File name too long")) return ENAMETOOLONG;
                if (reason.startsWith("Too many open files in system")) return ENFILE;
                if (reason.startsWith("Too many open files")) return EMFILE;
                if (reason.startsWith("No space left on device")) return ENOSPC;
                if (reason.startsWith("Read-only file system")) return EROFS;
                if (reason.startsWith("Not a directory")) return ENOTDIR;
                if (reason.startsWith("Is a directory")) return EISDIR;
                if (reason.startsWith("Too many levels of symbolic links")) return ELOOP;
                if (reason.startsWith("Permission denied")) return EACCES;
                if (reason.startsWith("No such file")) return ENOENT;
            }
        }
        if (e instanceof IOException) {
            // ProcessBuilder reports "error=2, No such file or directory" on
            // Java 8 and "Exec failed, error: 2 (No such file or directory)" later.
            Matcher m = Pattern.compile("error(?:=|: )(\\d+)").matcher(String.valueOf(e.getMessage()));
            if (m.find()) return nativeErrno(Integer.parseInt(m.group(1)));
        }
        return EIO;
    }

    static int nativeErrno(int value) {
        String host = System.getProperty("os.name", "");
        if (host.startsWith("Linux")) return value > 0 && value <= 133 && value != 41 && value != 58 ? value : EIO;
        if (!host.startsWith("Mac") && !host.startsWith("Darwin")) return EIO;
        switch (value) {
            case 1: return 1;
            case 2: return ENOENT;
            case 3: return ESRCH;
            case 4: return 4;
            case 5: return EIO;
            case 7: return 7;
            case 8: return 8;
            case 9: return EBADF;
            case 10: return ECHILD;
            case 12: return 12;
            case 13: return EACCES;
            case 17: return 17;
            case 20: return ENOTDIR;
            case 21: return EISDIR;
            case 22: return EINVAL;
            case 23: return 23;
            case 24: return 24;
            case 28: return 28;
            case 32: return EPIPE;
            case 35: return EAGAIN;
            case 45: return ENOTSUP;
            case 62: return ELOOP;
            case 63: return 36;
            case 66: return 39;
            default: return EIO;
        }
    }

    // The file type bits and size of {path}, or a negated errno and -1.
    static long[] metadata(Path path) {
        try {
            BasicFileAttributes a = Files.readAttributes(path, BasicFileAttributes.class);
            return new long[] {kind(a), a.size()};
        } catch (Exception e) {
            return new long[] {-errno(e), -1};
        }
    }
    static long kind(BasicFileAttributes a) {
        if (a.isDirectory()) return S_IFDIR;
        if (a.isRegularFile()) return S_IFREG;
        if (a.isSymbolicLink()) return S_IFLNK;
        return S_IFIFO; // or a device or socket: Java doesn't say which
    }

    static long[] metadata(DirEnd directory, Path name) {
        try {
            BasicFileAttributes a = directory.stream.getFileAttributeView(name,
                BasicFileAttributeView.class, LinkOption.NOFOLLOW_LINKS).readAttributes();
            return new long[] {kind(a), a.size()};
        } catch (Exception e) {
            return new long[] {-errno(e), -1};
        }
    }
    static int openDirectory(DirectoryStream<Path> stream) {
        if (!(stream instanceof SecureDirectoryStream<?>)) {
            try { stream.close(); } catch (IOException e) { }
            return -ENOTSUP;
        }
        return add(new DirEnd((SecureDirectoryStream<Path>) stream));
    }

    static int openRegular(SeekableByteChannel channel) {
        try {
            channel.size();
            channel.position(0);
        } catch (IOException | UnsupportedOperationException e) {
            try { channel.close(); } catch (IOException ignored) { }
            return -EINVAL;
        }
        return add(new FileEnd(channel));
    }

    // The int commands.
    // JVM limitation: a local process racing the server can replace the checked
    // regular file with a FIFO; open can block until a writer appears, before
    // the opened channel's size and seekability can be checked.
    static int openRead(Path p) throws IOException {
        long[] m = metadata(p);
        if (m[0] < 0) return (int) m[0];
        if (m[0] == S_IFDIR) return -EISDIR;
        if (m[0] != S_IFREG) return -EINVAL;
        return openRegular(FileChannel.open(p, StandardOpenOption.READ));
    }
    static int openDirectoryPath(Path p) throws IOException {
        return openDirectory(Files.newDirectoryStream(p));
    }
    // JVM limitation: a local process racing the server can replace the checked
    // regular file with a FIFO; open can block until a writer appears, before
    // the opened channel's size and seekability can be checked.
    static int openAt(int fd, String name, boolean directory) throws IOException {
        if (name.isEmpty() || name.indexOf('/') >= 0) return -EINVAL;
        End e = get(fd);
        if (e == null) return -EBADF;
        if (!(e instanceof DirEnd)) return -ENOTDIR;
        DirEnd d = (DirEnd) e;
        Path p = Paths.get(name);
        long[] m = metadata(d, p);
        if (m[0] < 0) return (int) m[0];
        if (m[0] == S_IFLNK) return directory ? -ENOTDIR : -ELOOP;
        if (directory && m[0] != S_IFDIR) return -ENOTDIR;
        if (m[0] == S_IFDIR) return openDirectory(d.stream.newDirectoryStream(p, LinkOption.NOFOLLOW_LINKS));
        if (m[0] == S_IFREG) {
            Set<OpenOption> options = new HashSet<OpenOption>();
            options.add(StandardOpenOption.READ);
            options.add(LinkOption.NOFOLLOW_LINKS);
            return openRegular(d.stream.newByteChannel(p, options));
        }
        return add(new OtherEnd(m[0]));
    }
    static int pipe() {
        synchronized (LOCK) {
            int fd = lowestFree(2);
            Pipe pipe = new Pipe();
            pipe.readEnd = new PipeEnd(pipe, fd, true);
            pipe.writeEnd = new PipeEnd(pipe, fd + 1, false);
            fds.put(fd, pipe.readEnd);
            fds.put(fd + 1, pipe.writeEnd);
            return fd;
        }
    }
    static int nonblock(int fd) {
        synchronized (LOCK) {
            End e = fds.get(fd);
            if (e == null) return -EBADF;
            e.nonblocking = true;
            return 0;
        }
    }
    static int access(Path p) {
        if (!Files.exists(p)) return -ENOENT;
        return Files.isExecutable(p) ? 0 : -EACCES;
    }

    // Starting a child. Java can connect only a child's descriptors 0 to 2,
    // so the child's runtime builds its table from LAYOUT_ENV instead: its
    // descriptor given a pipe's read end reads the process's standard input,
    // the one given a pipe's write end writes its standard output, a null
    // descriptor reads as empty, and one given standard error writes the
    // inherited standard error. As ADR-0004 says of the native worker, the
    // child's environment is otherwise empty, so a launcher names its java by
    // an absolute path. A program that can't be started is reported here, not
    // by an exit status of 127. Java collapses normal exits 129–159 and
    // signal exits: wait treats that range as signal (status - 128). The server
    // worker never exits normally in that range.
    static final class Spawn {
        static int run(int[] layout, List<String> argv) {
            if (argv.isEmpty() || layout.length > MIN_FD) return -EINVAL;
            String[] kinds = new String[layout.length];
            PipeEnd in = null, out = null;
            synchronized (LOCK) {
                for (int i = 0; i < layout.length; i++) {
                    End e = fds.get(layout[i]);
                    if (e instanceof Null) {
                        kinds[i] = "null";
                    } else if (e instanceof Output && ((Output) e).stderr) {
                        kinds[i] = "err";
                    } else if (e instanceof PipeEnd && !((PipeEnd) e).pipe.connected) {
                        PipeEnd p = (PipeEnd) e;
                        if (p.readSide ? in != null : out != null) return -EINVAL;
                        if (p.readSide) in = p;
                        else out = p;
                        kinds[i] = p.readSide ? "in" : "out";
                    } else {
                        return e == null ? -EBADF : -EINVAL;
                    }
                }
            }
            ProcessBuilder pb = new ProcessBuilder(argv);
            StringBuilder s = new StringBuilder();
            for (int i = 0; i < kinds.length; i++) s.append(i == 0 ? "" : ",").append(kinds[i]);
            pb.environment().clear();
            pb.environment().put(LAYOUT_ENV, s.toString());
            pb.redirectError(ProcessBuilder.Redirect.INHERIT);
            File devNull = new File("/dev/null");
            if (in == null) pb.redirectInput(devNull);
            if (out == null) pb.redirectOutput(devNull);
            Process p;
            try {
                p = pb.start();
            } catch (Exception e) {
                return -errno(e);
            }
            synchronized (LOCK) {
                if (in != null) {
                    in.pipe.connected = true;
                    PipeEnd w = in.pipe.writeEnd;
                    if (w.closed) closeQuietly(p.getOutputStream());
                    else replace(w, new Output(p.getOutputStream(), true, true, false));
                }
                if (out != null) {
                    out.pipe.connected = true;
                    PipeEnd r = out.pipe.readEnd;
                    if (r.closed) closeQuietly(p.getInputStream());
                    else replace(r, new Input(p.getInputStream(), true));
                }
                int pid = pid(p);
                children.put(pid, p);
                LOCK.notifyAll();
                return pid;
            }
        }
        static void replace(PipeEnd old, End e) {
            e.nonblocking = old.nonblocking;
            fds.put(old.fd, e);
        }
        static void closeQuietly(Closeable c) {
            try { c.close(); } catch (IOException e) { }
        }
        // Process.pid() is Java 9 or later; Java 8 keeps it in a field.
        static int pid(Process p) {
            try {
                return ((Long) Process.class.getMethod("pid").invoke(p)).intValue();
            } catch (Exception e) {
            }
            try {
                Field f = p.getClass().getDeclaredField("pid");
                f.setAccessible(true);
                return f.getInt(p);
            } catch (Exception e) {
            }
            while (children.containsKey(nextSyntheticPid)) nextSyntheticPid++;
            return nextSyntheticPid++;
        }
        static int kill(int pid, int signal) {
            Process p;
            synchronized (LOCK) { p = children.get(pid); }
            if (p == null) return -ESRCH;
            if (signal == 9) p.destroyForcibly();
            else if (signal == 15) p.destroy();
            else if (signal != 0) return -EINVAL;
            return 0;
        }
        static int wait(int pid) {
            Process p;
            synchronized (LOCK) { p = children.remove(pid); }
            if (p == null) return -ECHILD;
            int v;
            while (true) {
                try {
                    v = p.waitFor();
                    break;
                } catch (InterruptedException e) {
                }
            }
            // Java reports a child that a signal ended as 128 plus the signal.
            if (v > 128 && v < 128 + 32) return v - 128;
            return (v & 0xFF) << 8;
        }
    }

    static int alarm(int ms) {
        if (ms < 0) return -EINVAL;
        synchronized (LOCK) {
            if (alarm != null) alarm.cancel();
            alarm = null;
            if (ms > 0) {
                alarm = new Timer("V3S_System-alarm", true);
                alarm.schedule(new TimerTask() {
                    public void run() { Runtime.getRuntime().halt(ALARM_STATUS); }
                }, ms);
            }
        }
        return 0;
    }

    // The byte commands, each returning the payload or throwing Failure.
    static final class Failure extends Exception {
        private static final long serialVersionUID = 1L;
        final int errno;
        Failure(int errno) {
            super(null, null, false, false);
            this.errno = errno;
        }
    }

    static byte[] poll(int timeoutMs, int[] fd, int[] events) {
        int count = fd.length;
        long deadline = timeoutMs < 0 ? Long.MAX_VALUE : System.nanoTime() + timeoutMs * 1000000L;
        byte[] r = new byte[count];
        synchronized (LOCK) {
            while (true) {
                boolean any = false;
                for (int i = 0; i < count; i++) {
                    int re = 0;
                    if (fd[i] >= 0) {
                        End e = fds.get(fd[i]);
                        re = e == null ? POLLNVAL : e.revents(events[i]);
                    }
                    r[i] = (byte) re;
                    if (re != 0) any = true;
                }
                if (any) return r;
                long left = deadline - System.nanoTime();
                if (left <= 0) return r;
                try {
                    LOCK.wait(Math.max(1, Math.min(left / 1000000L, INT_MAX)));
                } catch (InterruptedException e) {
                    return r;
                }
            }
        }
    }
    static byte[] longs(long a, long b) {
        return ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN).putLong(a).putLong(b).array();
    }
    static byte[] stat(int fd) throws Failure {
        End e = get(fd);
        if (e == null) throw new Failure(EBADF);
        long[] m;
        synchronized (LOCK) { m = e.stat(); }
        if (m[0] < 0) throw new Failure((int) -m[0]);
        return longs(m[0], m[1]);
    }
    static byte[] statAt(int fd, String name) throws Failure {
        if (name.isEmpty() || name.indexOf('/') >= 0) throw new Failure(EINVAL);
        End e = get(fd);
        if (e == null) throw new Failure(EBADF);
        if (!(e instanceof DirEnd)) throw new Failure(ENOTDIR);
        long[] m = metadata((DirEnd) e, Paths.get(name));
        if (m[0] < 0) throw new Failure((int) -m[0]);
        return longs(m[0], m[1]);
    }
    static int dtype(DirEnd directory, Path name) {
        long k = metadata(directory, name)[0];
        return k == S_IFDIR ? DT_DIR : k == S_IFREG ? DT_REG : k == S_IFLNK ? DT_LNK : DT_UNKNOWN;
    }
    // linux_dirent64: d_ino (8 bytes), d_off (8), d_reclen (2), d_type (1),
    // then the name and a 0 byte, padded to a multiple of 8.
    static byte[] readDirectory(int fd, int size) throws Failure {
        if (size <= 0) throw new Failure(EINVAL);
        End e = get(fd);
        if (e == null) throw new Failure(EBADF);
        if (!(e instanceof DirEnd)) throw new Failure(ENOTDIR);
        DirEnd d = (DirEnd) e;
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try {
            while (true) {
                byte[] rec = d.pending;
                d.pending = null;
                if (rec == null) {
                    if (!d.entries.hasNext()) break;
                    Path p = d.entries.next();
                    byte[] name = p.getFileName().toString().getBytes(StandardCharsets.UTF_8);
                    int len = (19 + name.length + 1 + 7) & ~7;
                    rec = ByteBuffer.allocate(len).order(ByteOrder.LITTLE_ENDIAN)
                        .putLong(1).putLong(0).putShort((short) len).put((byte) dtype(d, p.getFileName())).put(name).array();
                }
                if (out.size() + rec.length > size) {
                    if (out.size() == 0) {
                        d.pending = rec;
                        throw new Failure(EINVAL);
                    }
                    d.pending = rec;
                    break;
                }
                out.write(rec, 0, rec.length);
            }
        } catch (DirectoryIteratorException ex) {
            throw new Failure(errno(ex));
        }
        return out.toByteArray();
    }
    static byte[] executable() throws Failure {
        String path = System.getProperty("virgil.lsp.executable");
        if (path == null) {
            try {
                path = Paths.get(V3S_System.class.getProtectionDomain().getCodeSource().getLocation().toURI()).toString();
            } catch (Exception e) {
                throw new Failure(ENOENT);
            }
        }
        return path.getBytes(StandardCharsets.UTF_8);
    }
    static byte[] random() {
        synchronized (LOCK) {
            if (random == null) random = new java.security.SecureRandom();
            byte[] r = new byte[16];
            random.nextBytes(r);
            return r;
        }
    }
    static byte[] memory() {
        Runtime r = Runtime.getRuntime();
        return longs(r.maxMemory(), r.totalMemory() - r.freeMemory());
    }

    // Parsing and dispatching commands.
    static final class Commands {
        static boolean isCommand(byte[] name) {
            return name != null && name.length > 0 && name[0] == 1;
        }
        static String[] split(byte[] name) {
            return new String(name, 1, name.length - 1, StandardCharsets.UTF_8).split("\0", -1);
        }
        static void count(String[] a, int n) {
            if (a.length != n + 1) throw new IllegalArgumentException(a[0]);
        }
        static int num(String s) {
            return Integer.parseInt(s);
        }
        static Path path(String s) {
            if (s.isEmpty()) throw new IllegalArgumentException("empty path");
            return Paths.get(s);
        }
        static int run(String[] a) {
            try {
                switch (a[0]) {
                    case "pipe": count(a, 0); return pipe();
                    case "null": count(a, 0); return add(new Null());
                    case "openRead": count(a, 1); return openRead(path(a[1]));
                    case "openDirectory": count(a, 1); return openDirectoryPath(path(a[1]));
                    case "openAt": {
                        count(a, 3);
                        if (!a[3].equals("0") && !a[3].equals("1")) return -EINVAL;
                        return openAt(num(a[1]), a[2], a[3].equals("1"));
                    }
                    case "nonblock": count(a, 1); return nonblock(num(a[1]));
                    case "close": count(a, 1); return close(num(a[1]));
                    case "access": count(a, 1); return access(path(a[1]));
                    case "spawn": {
                        if (a.length < 2) return -EINVAL;
                        int n = num(a[1]);
                        if (n < 0 || a.length < 2 + n + 1) return -EINVAL;
                        int[] layout = new int[n];
                        for (int i = 0; i < n; i++) layout[i] = num(a[2 + i]);
                        return Spawn.run(layout, Arrays.asList(a).subList(2 + n, a.length));
                    }
                    case "kill": count(a, 2); return Spawn.kill(num(a[1]), num(a[2]));
                    case "wait": count(a, 1); return Spawn.wait(num(a[1]));
                    case "alarm": count(a, 1); return alarm(num(a[1]));
                    case "gc": count(a, 0); System.gc(); return 0;
                    default: return -ENOSYS;
                }
            } catch (IllegalArgumentException e) {
                return -EINVAL;
            } catch (Exception e) {
                return -errno(e);
            }
        }
        static byte[] query(String[] a) {
            try {
                byte[] payload = answer(a);
                byte[] r = new byte[payload.length + 1];
                System.arraycopy(payload, 0, r, 1, payload.length);
                return r;
            } catch (Failure f) {
                return new byte[] {(byte) f.errno};
            } catch (IllegalArgumentException e) {
                return new byte[] {(byte) EINVAL};
            } catch (Exception e) {
                return new byte[] {(byte) errno(e)};
            }
        }
        static byte[] answer(String[] a) throws Failure {
            switch (a[0]) {
                case "poll": {
                    if (a.length < 2 || a.length % 2 != 0) throw new Failure(EINVAL);
                    int count = (a.length - 2) / 2;
                    int[] fd = new int[count], events = new int[count];
                    for (int i = 0; i < count; i++) {
                        fd[i] = num(a[2 + 2 * i]);
                        events[i] = num(a[3 + 2 * i]);
                    }
                    return poll(num(a[1]), fd, events);
                }
                case "stat": count(a, 1); return stat(num(a[1]));
                case "statAt": count(a, 2); return statAt(num(a[1]), a[2]);
                case "readDirectory": count(a, 2); return readDirectory(num(a[1]), num(a[2]));
                case "random": count(a, 0); return random();
                case "executable": count(a, 0); return executable();
                case "memory": count(a, 0); return memory();
                default: throw new Failure(ENOSYS);
            }
        }
    }

    // ---- Unchanged from upstream ---------------------------------------

    public static int ticksMs() {
        return (int) System.currentTimeMillis();
    }

    public static int ticksUs() {
        return (int) (System.nanoTime() / 1000);
    }

    public static int ticksNs() {
        return (int) System.nanoTime();
    }

    public static void chmod(byte[] fname, int mode) {
        String fileName = new String(fname);
        String modeString = Integer.toString(mode, 8);
	try {
            Runtime.getRuntime().exec(new String[] {"chmod", modeString, fileName}).waitFor();
        } catch (Throwable t) {
	    // do nothing.
	}
    }

    public static void error(byte[] type, byte[] message) throws Exception {
        throw new Exception(new String(type) + ": " + new String(message));
    }

    public static boolean equals(Object a, Object b) {
        return a == b || (a == null ? b.equals(a) : a.equals(b));
    }

    public static int shl(int a, int b) {
	if ((b & 31) == b) return a << b;
	return 0;
    }

    public static int shr(int a, int b) {
	if ((b & 31) == b) return a >>> b;
	return 0;
    }

    public static int sar(int a, int b) {
	if (b >= 31) return a >> 31;
	return a >> b;
    }

    public static long lshl(long a, long b) {
	if ((b & 63) == b) return a << b;
	return 0;
    }

    public static long lshr(long a, long b) {
	if ((b & 63) == b) return a >>> b;
	return 0;
    }

    public static long lshr(long a, byte b, byte rem) {
        if (b == 0) return a;
        if (b >= 63) return 0;
        return (a << rem) >>> rem >>> b;
    }

    public static long lsar(long a, long b) {
	if (b >= 63) return a >> 63;
	return a >> b;
    }

    public static int natLt(int x, int y) {
        if (x < 0) return y < 0 ? (y > x ? 1 : 0) : 0;
        return (y < 0 || x < y) ? 1 : 0;
    }

    public static int natLteq(int x, int y) {
        if (x < 0) return y < 0 ? (y >= x ? 1 : 0) : 0;
        return (y < 0 || x <= y) ? 1 : 0;
    }

    public static int natDiv(int x, int y) {
	return (int)((x & 0xffffffffL) / (y & 0xffffffffL));
    }

    public static int natMod(int x, int y) {
	return (int)((x & 0xffffffffL) % (y & 0xffffffffL));
    }

    public static int natGt(int x, int y) {
        return 1 ^ natLteq(x, y);
    }

    public static int natGteq(int x, int y) {
        return 1 ^ natLt(x, y);
    }

    public static int natlLt(long x, long y) {
        if (x < 0) return y < 0 ? (y > x ? 1 : 0) : 0;
        return (y < 0 || x < y) ? 1 : 0;
    }

    public static int natlLteq(long x, long y) {
        if (x < 0) return y < 0 ? (y >= x ? 1 : 0) : 0;
        return (y < 0 || x <= y) ? 1 : 0;
    }

    public static int natlGt(long x, long y) {
        return 1 ^ natlLteq(x, y);
    }

    public static int natlGteq(long x, long y) {
        return 1 ^ natlLt(x, y);
    }

    public static long natlDiv(long x, long y) {
	if (y == 1) return x;
	if (y < 0) return natlGteq(x, y);
	long xs = x >>> 1, q = (xs / y) << 1;
	while (natlGteq(x - q*y, y) != 0) q++;
	return q;
    }

    public static long natlMod(long x, long y) {
	return x - (y * natlDiv(x, y));
    }

    public static int satInt(int v, int min, int max) {
	if (v < min) return min;
	if (v > max) return max;
	return v;
    }

    public static long satLong(long v, long min, long max) {
	if (v < min) return min;
	if (v > max) return max;
	return v;
    }

    public static long d2ul(double f) {
	if (f <= 0) return 0;
	if (f >= 18446744073709551616d) return 0xFFFFFFFFFFFFFFFFL;
	if (f > 9223372036854775000d) {
	    return ((long)(f / 2)) * 2;
	}
	return (long)f;
    }

    public static float cast_i2f(int v) {
	if (v != INT_MAX) {
            float f = (float)v;
            if (v == (int)f) return f;
        }
        throw new ClassCastException();
    }

    public static float cast_l2f(long v) {
	if (v != LONG_MAX) {
            float f = (float)v;
            if (v == (long)f) return f;
        }
        throw new ClassCastException();
    }

    public static double cast_l2d(long v) {
	if (v != LONG_MAX) {
            double d = (double)v;
            if (v == (long)d) return d;
        }
        throw new ClassCastException();
    }

    public static float cast_ul2f(long v) {
	if (v != U64_MAX) {
            if (v >= 0) return cast_l2f(v);
            // Manually check if any bits would be rounded off.
            if ((v & 0x0FFFFFFFFFFL) == 0) return 2.0f * (float)(v >>> 1);
        }
        throw new ClassCastException();
    }

    public static double cast_ul2d(long v) {
	if (v != U64_MAX) {
            if (v >= 0) return cast_l2d(v);
            // Manually check if any bits would be rounded off.
            if ((v & 0x7FFL) == 0) return 2.0d * (double)(v >>> 1);
        }
        throw new ClassCastException();
    }

    public static boolean query_i2f(int v) {
	return v != INT_MAX && v == (int)(float)v;
    }

    public static boolean query_l2f(long v) {
	return v != LONG_MAX && v == (long)(float)v;
    }

    public static boolean query_l2d(long v) {
	return v != LONG_MAX && v == (long)(double)v;
    }

    public static boolean query_ul2f(long v) {
	if (v == U64_MAX || v == LONG_MAX) return false;
	if (v < 0) {
	    if ((v & 1) != 0) return false;
	    v >>>= 1;
	}
	return v == (long)(float)v;
    }

    public static boolean query_ul2d(long v) {
	if (v == U64_MAX || v == LONG_MAX) return false;
	if (v < 0) {
	    if ((v & 1) != 0) return false;
	    v >>>= 1;
	}
	return v == (long)(double)v;
    }

    public static boolean query_d2f(double v) {
	long before = Double.doubleToRawLongBits(v);
	float f = (float)v;
	long after = Double.doubleToRawLongBits((double)f);
	return (before == after);
    }

    public static float cast_d2f(double v) {
	long before = Double.doubleToRawLongBits(v);
	float f = (float)v;
	long after = Double.doubleToRawLongBits((double)f);
	if (before == after) return f;
	throw new ClassCastException();
    }

    public static double round_ul2d(long v) {
	double r = (double)v;
	if (v < 0) return 2.0d * (double)((v >>> 1) | (v & 1));
	return r;
    }

    public static float round_ul2f(long v) {
	float r = (float)v;
	if (v < 0) return 2.0f * (float)((v >>> 1) | (v & 1));
	return r;
    }

    public static long cast_d2l(double v, double min, double max) {
	error: {
	    if (Double.doubleToLongBits(v) == 0x8000000000000000L) break error;
	    if (v < min || v >= max) break error;
	    if (v >= 0x1p63d) {  // XXX: only matters in u64 case
		long r = (long)(v/2);
		if (v != 2*(double)r) break error;
		return r << 1;
	    }
	    long r = (long)v;
	    if (v != (double)r) break error;
	    return r;
	}
	throw new ClassCastException();
    }

    public static boolean query_d2l(double v, double min, double max) {
	if (Double.doubleToLongBits(v) == 0x8000000000000000L) return false;
	if (v < min || v >= max) return false;
	if (v >= 0x1p63d) {  // XXX: only matters in u64 case
	    long r = (long)(v/2);
	    return v == 2*(double)r;
	}
	return v == (double)(long)v;
    }

    public static float fabs(float f) {
	int x = Float.floatToRawIntBits(f);
	return Float.intBitsToFloat((x << 1) >>> 1);
    }

    public static double dabs(double f) {
	long x = Double.doubleToRawLongBits(f);
	return Double.longBitsToDouble((x << 1) >>> 1);
    }

    public static boolean feq(float a, float b) {
	return Float.floatToRawIntBits(a) == Float.floatToRawIntBits(b);
    }

    public static boolean deq(double a, double b) {
	return Double.doubleToRawLongBits(a) == Double.doubleToRawLongBits(b);
    }

    public static byte readBytes1(byte[] bytes, int offset, int staticOffset) {
        return bytes[offset + staticOffset];
    }

    public static short readBytes2(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        int byte0 = ((int)bytes[fullOffset] & 0xFF);
        int byte1 = ((int)bytes[fullOffset + 1] & 0xFF) << 8;
        return ((short)(byte0 | byte1));
    }

    public static int readBytes3(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        int byte0 = ((int)bytes[fullOffset] & 0xFF);
        int byte1 = ((int)bytes[fullOffset + 1] & 0xFF) << 8;
        int byte2 = ((int)bytes[fullOffset + 2] & 0xFF) << 16;
        return byte0 | byte1 | byte2;
    }

    public static int readBytes4(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        int byte0 = ((int)bytes[fullOffset] & 0xFF);
        int byte1 = ((int)bytes[fullOffset + 1] & 0xFF) << 8;
        int byte2 = ((int)bytes[fullOffset + 2] & 0xFF) << 16;
        int byte3 = ((int)bytes[fullOffset + 3] & 0xFF) << 24;
        return byte0 | byte1 | byte2 | byte3;
    }

    public static long readBytes5(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        long byte0 = ((long)bytes[fullOffset] & 0xFF);
        long byte1 = ((long)bytes[fullOffset + 1] & 0xFF) << 8;
        long byte2 = ((long)bytes[fullOffset + 2] & 0xFF) << 16;
        long byte3 = ((long)bytes[fullOffset + 3] & 0xFF) << 24;
        long byte4 = ((long)bytes[fullOffset + 4] & 0xFF) << 32;
        return byte0 | byte1 | byte2 | byte3 | byte4;
    }

    public static long readBytes6(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        long byte0 = ((long)bytes[fullOffset] & 0xFF);
        long byte1 = ((long)bytes[fullOffset + 1] & 0xFF) << 8;
        long byte2 = ((long)bytes[fullOffset + 2] & 0xFF) << 16;
        long byte3 = ((long)bytes[fullOffset + 3] & 0xFF) << 24;
        long byte4 = ((long)bytes[fullOffset + 4] & 0xFF) << 32;
        long byte5 = ((long)bytes[fullOffset + 5] & 0xFF) << 40;
        return byte0 | byte1 | byte2 | byte3 | byte4 | byte5;
    }

    public static long readBytes7(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        long byte0 = ((long)bytes[fullOffset] & 0xFF);
        long byte1 = ((long)bytes[fullOffset + 1] & 0xFF) << 8;
        long byte2 = ((long)bytes[fullOffset + 2] & 0xFF) << 16;
        long byte3 = ((long)bytes[fullOffset + 3] & 0xFF) << 24;
        long byte4 = ((long)bytes[fullOffset + 4] & 0xFF) << 32;
        long byte5 = ((long)bytes[fullOffset + 5] & 0xFF) << 40;
        long byte6 = ((long)bytes[fullOffset + 6] & 0xFF) << 48;
        return byte0 | byte1 | byte2 | byte3 | byte4 | byte5 | byte6;
    }

    public static long readBytes8(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        long byte0 = ((long)bytes[fullOffset] & 0xFF);
        long byte1 = ((long)bytes[fullOffset + 1] & 0xFF) << 8;
        long byte2 = ((long)bytes[fullOffset + 2] & 0xFF) << 16;
        long byte3 = ((long)bytes[fullOffset + 3] & 0xFF) << 24;
        long byte4 = ((long)bytes[fullOffset + 4] & 0xFF) << 32;
        long byte5 = ((long)bytes[fullOffset + 5] & 0xFF) << 40;
        long byte6 = ((long)bytes[fullOffset + 6] & 0xFF) << 48;
        long byte7 = ((long)bytes[fullOffset + 7] & 0xFF) << 56;
        return byte0 | byte1 | byte2 | byte3 | byte4 | byte5 | byte6 | byte7;
    }

    public static float readBytesFloat(byte[] bytes, int offset, int staticOffset) {
        int bits = V3S_System.readBytes4(bytes, offset, staticOffset);
        return Float.intBitsToFloat(bits);
    }

    public static double readBytesDouble(byte[] bytes, int offset, int staticOffset) {
        long bits = V3S_System.readBytes8(bytes, offset, staticOffset);
        return Double.longBitsToDouble(bits);
    }

    public static short readBytes2_be(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        int byte0 = ((int)bytes[fullOffset + 1] & 0xFF);
        int byte1 = ((int)bytes[fullOffset + 0] & 0xFF) << 8;
        return ((short)(byte0 | byte1));
    }

    public static int readBytes3_be(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        int byte0 = ((int)bytes[fullOffset + 2] & 0xFF);
        int byte1 = ((int)bytes[fullOffset + 1] & 0xFF) << 8;
        int byte2 = ((int)bytes[fullOffset + 0] & 0xFF) << 16;
        return byte0 | byte1 | byte2;
    }

    public static int readBytes4_be(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        int byte0 = ((int)bytes[fullOffset + 3] & 0xFF);
        int byte1 = ((int)bytes[fullOffset + 2] & 0xFF) << 8;
        int byte2 = ((int)bytes[fullOffset + 1] & 0xFF) << 16;
        int byte3 = ((int)bytes[fullOffset + 0] & 0xFF) << 24;
        return byte0 | byte1 | byte2 | byte3;
    }

    public static long readBytes5_be(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        long byte0 = ((long)bytes[fullOffset + 4] & 0xFF);
        long byte1 = ((long)bytes[fullOffset + 3] & 0xFF) << 8;
        long byte2 = ((long)bytes[fullOffset + 2] & 0xFF) << 16;
        long byte3 = ((long)bytes[fullOffset + 1] & 0xFF) << 24;
        long byte4 = ((long)bytes[fullOffset + 0] & 0xFF) << 32;
        return byte0 | byte1 | byte2 | byte3 | byte4;
    }

    public static long readBytes6_be(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        long byte0 = ((long)bytes[fullOffset + 5] & 0xFF);
        long byte1 = ((long)bytes[fullOffset + 4] & 0xFF) << 8;
        long byte2 = ((long)bytes[fullOffset + 3] & 0xFF) << 16;
        long byte3 = ((long)bytes[fullOffset + 2] & 0xFF) << 24;
        long byte4 = ((long)bytes[fullOffset + 1] & 0xFF) << 32;
        long byte5 = ((long)bytes[fullOffset + 0] & 0xFF) << 40;
        return byte0 | byte1 | byte2 | byte3 | byte4 | byte5;
    }

    public static long readBytes7_be(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        long byte0 = ((long)bytes[fullOffset + 6] & 0xFF);
        long byte1 = ((long)bytes[fullOffset + 5] & 0xFF) << 8;
        long byte2 = ((long)bytes[fullOffset + 4] & 0xFF) << 16;
        long byte3 = ((long)bytes[fullOffset + 3] & 0xFF) << 24;
        long byte4 = ((long)bytes[fullOffset + 2] & 0xFF) << 32;
        long byte5 = ((long)bytes[fullOffset + 1] & 0xFF) << 40;
        long byte6 = ((long)bytes[fullOffset + 0] & 0xFF) << 48;
        return byte0 | byte1 | byte2 | byte3 | byte4 | byte5 | byte6;
    }

    public static long readBytes8_be(byte[] bytes, int offset, int staticOffset) {
        int fullOffset = offset + staticOffset;
        long byte0 = ((long)bytes[fullOffset + 7] & 0xFF);
        long byte1 = ((long)bytes[fullOffset + 6] & 0xFF) << 8;
        long byte2 = ((long)bytes[fullOffset + 5] & 0xFF) << 16;
        long byte3 = ((long)bytes[fullOffset + 4] & 0xFF) << 24;
        long byte4 = ((long)bytes[fullOffset + 3] & 0xFF) << 32;
        long byte5 = ((long)bytes[fullOffset + 2] & 0xFF) << 40;
        long byte6 = ((long)bytes[fullOffset + 1] & 0xFF) << 48;
        long byte7 = ((long)bytes[fullOffset + 0] & 0xFF) << 56;
        return byte0 | byte1 | byte2 | byte3 | byte4 | byte5 | byte6 | byte7;
    }

    public static float readBytesFloat_be(byte[] bytes, int offset, int staticOffset) {
        int bits = V3S_System.readBytes4_be(bytes, offset, staticOffset);
        return Float.intBitsToFloat(bits);
    }

    public static double readBytesDouble_be(byte[] bytes, int offset, int staticOffset) {
        long bits = V3S_System.readBytes8_be(bytes, offset, staticOffset);
        return Double.longBitsToDouble(bits);
    }

    public static void setBytes1(byte[] bytes, int offset, byte value, int staticOffset) {
        bytes[offset + staticOffset] = value;
    }

    public static void setBytes2(byte[] bytes, int offset, short value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset] = (byte)value;
        bytes[fullOffset + 1] = (byte)(value >> 8);
    }

    public static void setBytes3(byte[] bytes, int offset, int value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset] = (byte)value;
        bytes[fullOffset + 1] = (byte)(value >> 8);
        bytes[fullOffset + 2] = (byte)(value >> 16);
    }

    public static void setBytes4(byte[] bytes, int offset, int value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset] = (byte)value;
        bytes[fullOffset + 1] = (byte)(value >> 8);
        bytes[fullOffset + 2] = (byte)(value >> 16);
        bytes[fullOffset + 3] = (byte)(value >> 24);
    }

    public static void setBytes5(byte[] bytes, int offset, long value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset] = (byte)value;
        bytes[fullOffset + 1] = (byte)(value >> 8);
        bytes[fullOffset + 2] = (byte)(value >> 16);
        bytes[fullOffset + 3] = (byte)(value >> 24);
        bytes[fullOffset + 4] = (byte)(value >> 32);
    }

    public static void setBytes6(byte[] bytes, int offset, long value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset] = (byte)value;
        bytes[fullOffset + 1] = (byte)(value >> 8);
        bytes[fullOffset + 2] = (byte)(value >> 16);
        bytes[fullOffset + 3] = (byte)(value >> 24);
        bytes[fullOffset + 4] = (byte)(value >> 32);
        bytes[fullOffset + 5] = (byte)(value >> 40);
    }

    public static void setBytes7(byte[] bytes, int offset, long value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset] = (byte)value;
        bytes[fullOffset + 1] = (byte)(value >> 8);
        bytes[fullOffset + 2] = (byte)(value >> 16);
        bytes[fullOffset + 3] = (byte)(value >> 24);
        bytes[fullOffset + 4] = (byte)(value >> 32);
        bytes[fullOffset + 5] = (byte)(value >> 40);
        bytes[fullOffset + 6] = (byte)(value >> 48);
    }

    public static void setBytes8(byte[] bytes, int offset, long value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset] = (byte)value;
        bytes[fullOffset + 1] = (byte)(value >> 8);
        bytes[fullOffset + 2] = (byte)(value >> 16);
        bytes[fullOffset + 3] = (byte)(value >> 24);
        bytes[fullOffset + 4] = (byte)(value >> 32);
        bytes[fullOffset + 5] = (byte)(value >> 40);
        bytes[fullOffset + 6] = (byte)(value >> 48);
        bytes[fullOffset + 7] = (byte)(value >> 56);
    }

    public static void setBytesFloat(byte[] bytes, int offset, float value, int staticOffset) {
        int bits = Float.floatToRawIntBits(value);
        V3S_System.setBytes4(bytes, offset, bits, staticOffset);
    }

    public static void setBytesDouble(byte[] bytes, int offset, double value, int staticOffset) {
        long bits = Double.doubleToRawLongBits(value);
        V3S_System.setBytes8(bytes, offset, bits, staticOffset);
    }

    public static void setBytes2_be(byte[] bytes, int offset, short value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset + 1] = (byte)value;
        bytes[fullOffset + 0] = (byte)(value >> 8);
    }

    public static void setBytes3_be(byte[] bytes, int offset, int value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset + 2] = (byte)value;
        bytes[fullOffset + 1] = (byte)(value >> 8);
        bytes[fullOffset + 0] = (byte)(value >> 16);
    }

    public static void setBytes4_be(byte[] bytes, int offset, int value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset + 3] = (byte)value;
        bytes[fullOffset + 2] = (byte)(value >> 8);
        bytes[fullOffset + 1] = (byte)(value >> 16);
        bytes[fullOffset + 0] = (byte)(value >> 24);
    }

    public static void setBytes5_be(byte[] bytes, int offset, long value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset + 4] = (byte)value;
        bytes[fullOffset + 3] = (byte)(value >> 8);
        bytes[fullOffset + 2] = (byte)(value >> 16);
        bytes[fullOffset + 1] = (byte)(value >> 24);
        bytes[fullOffset + 0] = (byte)(value >> 32);
    }

    public static void setBytes6_be(byte[] bytes, int offset, long value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset + 5] = (byte)value;
        bytes[fullOffset + 4] = (byte)(value >> 8);
        bytes[fullOffset + 3] = (byte)(value >> 16);
        bytes[fullOffset + 2] = (byte)(value >> 24);
        bytes[fullOffset + 1] = (byte)(value >> 32);
        bytes[fullOffset + 0] = (byte)(value >> 40);
    }

    public static void setBytes7_be(byte[] bytes, int offset, long value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset + 6] = (byte)value;
        bytes[fullOffset + 5] = (byte)(value >> 8);
        bytes[fullOffset + 4] = (byte)(value >> 16);
        bytes[fullOffset + 3] = (byte)(value >> 24);
        bytes[fullOffset + 2] = (byte)(value >> 32);
        bytes[fullOffset + 1] = (byte)(value >> 40);
        bytes[fullOffset + 0] = (byte)(value >> 48);
    }

    public static void setBytes8_be(byte[] bytes, int offset, long value, int staticOffset) {
        int fullOffset = offset + staticOffset;
        bytes[fullOffset + 7] = (byte)value;
        bytes[fullOffset + 6] = (byte)(value >> 8);
        bytes[fullOffset + 5] = (byte)(value >> 16);
        bytes[fullOffset + 4] = (byte)(value >> 24);
        bytes[fullOffset + 3] = (byte)(value >> 32);
        bytes[fullOffset + 2] = (byte)(value >> 40);
        bytes[fullOffset + 1] = (byte)(value >> 48);
        bytes[fullOffset + 0] = (byte)(value >> 56);
    }

    public static void setBytesFloat_be(byte[] bytes, int offset, float value, int staticOffset) {
        int bits = Float.floatToRawIntBits(value);
        V3S_System.setBytes4_be(bytes, offset, bits, staticOffset);
    }

    public static void setBytesDouble_be(byte[] bytes, int offset, double value, int staticOffset) {
        long bits = Double.doubleToRawLongBits(value);
        V3S_System.setBytes8_be(bytes, offset, bits, staticOffset);
    }
}
