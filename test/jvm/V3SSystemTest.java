// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Tests for the JVM target's runtime class, src/os/jvm/V3S_System.java. Run
// them with `make test-jvm-runtime`; `ARGS='children'` runs only the tests
// whose names contain "children". They call the class directly, through the
// same static methods that compiled Virgil code calls, and start copies of
// this program as children ("V3SSystemTest child <mode>") to test pipes,
// processes, and the child's descriptor layout. Every test must leave no
// descriptor, child, or runtime thread behind; the runner checks that.

import java.io.*;
import java.lang.reflect.Method;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Modifier;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.*;

public class V3SSystemTest {
    interface Body {
        void run() throws Exception;
    }

    static final LinkedHashMap<String, Body> tests = new LinkedHashMap<String, Body>();
    static Path scratch;

    public static void main(String[] args) throws Exception {
        if (args.length > 0 && args[0].equals("child")) {
            Child.run(args);
            return;
        }
        scratch = Files.createTempDirectory("v3s-test");
        register();
        int run = 0, failed = 0;
        try {
            for (Map.Entry<String, Body> t : tests.entrySet()) {
                if (!selected(t.getKey(), args)) continue;
                run++;
                long start = System.nanoTime();
                String failure = runOne(t.getValue());
                long ms = (System.nanoTime() - start) / 1000000;
                if (failure == null) {
                    System.out.println("ok   " + t.getKey() + " (" + ms + " ms)");
                } else {
                    failed++;
                    System.out.println("FAIL " + t.getKey() + ": " + failure);
                }
            }
        } finally {
            removeTree(scratch);
        }
        System.out.println(run + " tests, " + failed + " failed");
        System.exit(failed == 0 && run > 0 ? 0 : 1);
    }

    static boolean selected(String name, String[] patterns) {
        if (patterns.length == 0) return true;
        for (String p : patterns) {
            if (name.contains(p)) return true;
        }
        return false;
    }

    // Runs a test, then checks that it left nothing open. Returns null or why
    // it failed.
    static String runOne(Body body) {
        int descriptors = V3S_System.descriptorCount(), children = V3S_System.childCount();
        int threads = runtimeThreads().size();
        String failure = null;
        try {
            body.run();
        } catch (Throwable t) {
            StringWriter w = new StringWriter();
            t.printStackTrace(new PrintWriter(w));
            failure = w.toString().trim();
        }
        if (failure == null && V3S_System.descriptorCount() != descriptors) {
            failure = "leaked " + (V3S_System.descriptorCount() - descriptors) + " descriptors";
        }
        if (failure == null && V3S_System.childCount() != children) {
            failure = "left " + (V3S_System.childCount() - children) + " children unwaited";
        }
        if (failure == null && !runtimeThreadsEnd(threads, 5000)) {
            failure = "left runtime threads running: " + runtimeThreads();
        }
        if (failure != null) reset(threads);
        return failure;
    }

    // After a failure: ends every child and closes every descriptor that the
    // runtime assigned, so that the next test starts clean.
    static void reset(int threads) {
        List<Integer> pids, fds;
        synchronized (V3S_System.LOCK) {
            pids = new ArrayList<Integer>(V3S_System.children.keySet());
            fds = new ArrayList<Integer>(V3S_System.fds.keySet());
        }
        for (int pid : pids) {
            V3S_System.Spawn.kill(pid, 9);
            V3S_System.Spawn.wait(pid);
        }
        for (int fd : fds) {
            if (fd >= V3S_System.MIN_FD) V3S_System.close(fd);
        }
        runtimeThreadsEnd(threads, 5000);
    }

    static List<String> runtimeThreads() {
        List<String> names = new ArrayList<String>();
        for (Thread t : Thread.getAllStackTraces().keySet()) {
            String n = t.getName();
            if (t.isAlive() && (n.equals("V3S_System-input") || n.equals("V3S_System-output"))) names.add(n);
        }
        return names;
    }

    // Whether at most {count} runtime threads are left within {timeoutMs}.
    static boolean runtimeThreadsEnd(int count, long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (runtimeThreads().size() > count) {
            if (System.currentTimeMillis() > deadline) return false;
            sleep(10);
        }
        return true;
    }

    // ---- Assertions and helpers ----------------------------------------

    static void check(boolean condition, String what) {
        if (!condition) throw new AssertionError(what);
    }

    static void eq(Object expected, Object actual, String what) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(what + ": expected " + expected + ", got " + actual);
        }
    }

    static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            throw new RuntimeException(e);
        }
    }

    static byte[] bytes(String s) {
        return s.getBytes(StandardCharsets.UTF_8);
    }

    static String text(byte[] b) {
        return new String(b, StandardCharsets.UTF_8);
    }

    // A command name: byte 1, the operation, and each argument after a 0.
    static byte[] name(String op, Object... args) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        b.write(1);
        b.write(bytes(op), 0, bytes(op).length);
        for (Object a : args) {
            if (a instanceof Object[]) {
                for (Object x : (Object[]) a) {
                    b.write(0);
                    byte[] s = bytes(String.valueOf(x));
                    b.write(s, 0, s.length);
                }
                continue;
            }
            b.write(0);
            byte[] s = bytes(String.valueOf(a));
            b.write(s, 0, s.length);
        }
        return b.toByteArray();
    }

    static int cmd(String op, Object... args) {
        return V3S_System.fileOpen(name(op, args), false);
    }

    // A byte command's reply: its status byte and payload.
    static byte[] query(String op, Object... args) {
        byte[] r = V3S_System.fileLoad(name(op, args));
        check(r != null && r.length >= 1, op + " replied with no status byte");
        return r;
    }

    // A byte command's payload, which must have succeeded.
    static byte[] payload(String op, Object... args) {
        byte[] r = query(op, args);
        eq(0, (int) r[0], op + " status");
        return Arrays.copyOfRange(r, 1, r.length);
    }

    // A byte command's errno, which must have failed with no payload.
    static int failure(String op, Object... args) {
        byte[] r = query(op, args);
        eq(1, r.length, op + " reply length");
        check(r[0] != 0, op + " should have failed");
        return r[0] & 0xFF;
    }

    static long[] longs(byte[] b) {
        ByteBuffer buf = ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN);
        long[] r = new long[b.length / 8];
        for (int i = 0; i < r.length; i++) r[i] = buf.getLong();
        return r;
    }

    // The revents of each descriptor, after poll with {fdEvents} as pairs.
    static int[] poll(int timeoutMs, int... fdEvents) {
        Object[] args = new Object[fdEvents.length + 1];
        args[0] = timeoutMs;
        for (int i = 0; i < fdEvents.length; i++) args[i + 1] = fdEvents[i];
        byte[] r = payload("poll", args);
        eq(fdEvents.length / 2, r.length, "poll reply length");
        int[] revents = new int[r.length];
        for (int i = 0; i < r.length; i++) revents[i] = r[i] & 0xFF;
        return revents;
    }

    static int write(int fd, String s) {
        byte[] b = bytes(s);
        return V3S_System.write(fd, b, 0, b.length);
    }

    // Reads a nonblocking descriptor until {count} bytes have arrived, or to
    // its end if {count} is negative, polling as the server does.
    static byte[] readFully(int fd, int count, long timeoutMs) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[65536];
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (count < 0 || out.size() < count) {
            long left = deadline - System.currentTimeMillis();
            if (left <= 0) {
                throw new AssertionError("descriptor " + fd + ": " + (count < 0 ? "no end of input" : "only " + out.size() + " of " + count + " bytes") + " within " + timeoutMs + " ms");
            }
            int[] re = poll((int) left, fd, V3S_System.POLLIN);
            if (re[0] == 0) continue;
            int n = V3S_System.read(fd, buf, 0, count < 0 ? buf.length : Math.min(buf.length, count - out.size()));
            if (n == -V3S_System.EAGAIN) continue;
            if (n == 0) {
                if (count >= 0) throw new AssertionError("end of input after " + out.size() + " of " + count + " bytes");
                break;
            }
            check(n > 0, "read failed with errno " + -n);
            out.write(buf, 0, n);
        }
        return out.toByteArray();
    }

    static Path file(Path p, String content) throws IOException {
        Files.write(p, bytes(content));
        return p;
    }

    static void removeTree(Path root) throws IOException {
        if (!Files.exists(root, LinkOption.NOFOLLOW_LINKS)) return;
        Files.walkFileTree(root, new SimpleFileVisitor<Path>() {
            public FileVisitResult preVisitDirectory(Path d, java.nio.file.attribute.BasicFileAttributes a) throws IOException {
                d.toFile().setReadable(true);
                d.toFile().setExecutable(true);
                return FileVisitResult.CONTINUE;
            }
            public FileVisitResult visitFile(Path f, java.nio.file.attribute.BasicFileAttributes a) throws IOException {
                Files.delete(f);
                return FileVisitResult.CONTINUE;
            }
            public FileVisitResult visitFileFailed(Path f, IOException e) throws IOException {
                f.toFile().setReadable(true);
                f.toFile().setExecutable(true);
                removeTree(f);
                return FileVisitResult.CONTINUE;
            }
            public FileVisitResult postVisitDirectory(Path d, IOException e) throws IOException {
                Files.delete(d);
                return FileVisitResult.CONTINUE;
            }
        });
    }

    // Whether this process ignores permissions, as root does.
    static boolean ignoresPermissions(Path unreadable) {
        return Files.isReadable(unreadable);
    }

    // ---- Children ------------------------------------------------------

    // The server's ends of a child's pipes, set up as WorkerProcesses.start
    // does: descriptor 0 null, 1 and 2 standard error, 3 requests, 4 results.
    static final class Worker {
        final int pid, requests, results;
        Worker(int pid, int requests, int results) {
            this.pid = pid;
            this.requests = requests;
            this.results = results;
        }
    }

    static String[] childArgv(String... mode) {
        List<String> argv = new ArrayList<String>();
        argv.add(Paths.get(System.getProperty("java.home"), "bin", "java").toString());
        argv.add("-cp");
        argv.add(System.getProperty("java.class.path"));
        argv.add("V3SSystemTest");
        argv.add("child");
        argv.addAll(Arrays.asList(mode));
        return argv.toArray(new String[0]);
    }

    static Worker start(String... mode) {
        int requests = cmd("pipe"), results = cmd("pipe"), input = cmd("null");
        check(requests >= V3S_System.MIN_FD && results >= V3S_System.MIN_FD && input >= V3S_System.MIN_FD, "pipes");
        int pid = cmd("spawn", 5, input, 2, 2, requests, results + 1, childArgv(mode));
        eq(0, cmd("close", requests), "close the child's request end");
        eq(0, cmd("close", results + 1), "close the child's result end");
        eq(0, cmd("close", input), "close the child's input");
        check(pid > 0, "spawn failed with errno " + -pid);
        eq(0, cmd("nonblock", requests + 1), "nonblock requests");
        eq(0, cmd("nonblock", results), "nonblock results");
        return new Worker(pid, requests + 1, results);
    }

    // As WorkerProcess.terminate: closes the pipes, kills, and waits.
    static int terminate(Worker w) {
        cmd("close", w.requests);
        cmd("close", w.results);
        cmd("kill", w.pid, 9);
        return cmd("wait", w.pid);
    }

    // The child side: runs with the descriptor layout that spawn gave it.
    static final class Child {
        // Keeps an intentionally uncaught exception out of the test's stderr.
        // The JVM still ends the process with status 1.
        static void silenceUncaught() {
            Thread.setDefaultUncaughtExceptionHandler(new Thread.UncaughtExceptionHandler() {
                public void uncaughtException(Thread t, Throwable e) { }
            });
        }

        static void run(String[] args) throws Exception {
            String mode = args[1];
            byte[] buf = new byte[65536];
            if (mode.equals("echo")) {
                while (true) {
                    int n = V3S_System.read(3, buf, 0, buf.length);
                    if (n <= 0) System.exit(n == 0 ? 0 : 2);
                    if (V3S_System.write(4, buf, 0, n) != n) System.exit(3);
                }
            } else if (mode.equals("write")) {
                write(4, args[2]);
                System.exit(Integer.parseInt(args[3]));
            } else if (mode.equals("error")) {
                silenceUncaught();
                V3S_System.error(bytes("HostRuntimeError"), bytes("deliberate"));
            } else if (mode.equals("throw")) {
                silenceUncaught();
                throw new NullPointerException("uncaught");
            } else if (mode.equals("flood")) {
                int size = Integer.parseInt(args[2]);
                for (int at = 0; at < size; at += buf.length) {
                    int n = Math.min(buf.length, size - at);
                    for (int i = 0; i < n; i++) buf[i] = (byte) ((at + i) % 251);
                    if (V3S_System.write(4, buf, 0, n) != n) System.exit(3);
                }
                System.exit(0);
            } else if (mode.equals("layout")) {
                int r0 = V3S_System.read(0, buf, 0, buf.length);
                int w1 = write(1, "to-fd1\n");
                int w2 = write(2, "to-fd2\n");
                int r5 = V3S_System.read(5, buf, 0, buf.length);
                System.out.println("java-out");
                V3S_System.puts(bytes("puts\n"));
                write(4, "fd0=" + r0 + " fd1=" + w1 + " fd2=" + w2 + " fd5=" + r5);
                System.exit(0);
            } else if (mode.equals("environment")) {
                write(4, new java.util.TreeSet<String>(System.getenv().keySet()).toString());
                System.exit(0);
            } else if (mode.equals("sleep")) {
                Thread.sleep(60000);
                System.exit(0);
            } else if (mode.equals("late")) {
                Thread.sleep(300);
                write(4, "late");
                Thread.sleep(60000);
            } else if (mode.equals("alarm")) {
                V3S_System.fileOpen(name("alarm", args[2]), false);
                Thread.sleep(60000);
                System.exit(0);
            } else if (mode.equals("alarm-cancel")) {
                V3S_System.fileOpen(name("alarm", 100), false);
                V3S_System.fileOpen(name("alarm", 0), false);
                Thread.sleep(400);
                System.exit(3);
            } else if (mode.equals("print")) {
                V3S_System.putc('A');
                V3S_System.puts(bytes("bc"));
                V3S_System.puti(-42);
                V3S_System.ln();
                V3S_System.write(1, new byte[] {(byte) 0xE9}, 0, 1);
                V3S_System.putc((char) 0xE9);
                V3S_System.fileWriteK(1, bytes("z"), 0, 1);
                System.exit(0);
            }
            System.exit(99);
        }
    }

    // ---- Tests ---------------------------------------------------------

    static void test(String name, Body body) {
        tests.put(name, body);
    }

    static void register() {
        // Command dispatch and failure reporting.
        test("dispatch: unknown commands report ENOSYS", () -> {
            eq(-V3S_System.ENOSYS, cmd("nope"), "int command");
            eq(V3S_System.ENOSYS, failure("nope"), "byte command");
            eq(-V3S_System.ENOSYS, cmd(""), "empty int command");
        });
        test("dispatch: malformed arguments report EINVAL without throwing", () -> {
            eq(-V3S_System.EINVAL, cmd("close"), "missing argument");
            eq(-V3S_System.EINVAL, cmd("close", "x"), "not a number");
            eq(-V3S_System.EINVAL, cmd("close", 10, 11), "extra argument");
            eq(-V3S_System.EINVAL, cmd("openAt", 10, "a", 2), "directory flag");
            eq(-V3S_System.EINVAL, cmd("openRead", ""), "empty path");
            eq(-V3S_System.EINVAL, cmd("spawn"), "spawn with no count");
            eq(-V3S_System.EINVAL, cmd("spawn", -1, "x"), "negative count");
            eq(-V3S_System.EINVAL, cmd("spawn", 2, 2, 2), "spawn with no program");
            eq(-V3S_System.EINVAL, cmd("alarm", -1), "negative alarm");
            eq(V3S_System.EINVAL, failure("poll"), "poll with no timeout");
            eq(V3S_System.EINVAL, failure("poll", 0, 10), "poll with a descriptor but no events");
            eq(V3S_System.EINVAL, failure("readDirectory", "x", 64), "readDirectory with a bad descriptor");
            eq(V3S_System.EINVAL, failure("stat"), "stat with no descriptor");
        });
        test("dispatch: unknown descriptors report EBADF", () -> {
            byte[] b = new byte[4];
            eq(-V3S_System.EBADF, V3S_System.read(999, b, 0, 4), "read");
            eq(-V3S_System.EBADF, V3S_System.write(999, b, 0, 4), "write");
            eq(-V3S_System.EBADF, cmd("close", 999), "close");
            eq(-V3S_System.EBADF, cmd("nonblock", 999), "nonblock");
            eq(V3S_System.EBADF, failure("stat", 999), "stat");
            eq(V3S_System.EBADF, failure("readDirectory", 999, 64), "readDirectory");
            eq(-1, V3S_System.fileRead(999), "fileRead");
            eq(0, V3S_System.fileReadK(999, b, 0, 4), "fileReadK");
            eq(0, V3S_System.fileLeft(999), "fileLeft");
            V3S_System.fileClose(999);
        });
        test("dispatch: plain file names keep upstream's meaning", () -> {
            Path p = file(scratch.resolve("plain.txt"), "hello");
            eq("hello", text(V3S_System.fileLoad(bytes(p.toString()))), "fileLoad");
            eq(null, V3S_System.fileLoad(bytes(scratch.resolve("missing").toString())), "fileLoad of a missing file");
            eq(null, V3S_System.fileLoad(bytes(scratch.toString())), "fileLoad of a directory");
            eq(-1, V3S_System.fileOpen(bytes(scratch.resolve("missing").toString()), true), "fileOpen of a missing file");
            Path out = scratch.resolve("out.txt");
            int fd = V3S_System.fileOpen(bytes(out.toString()), false);
            check(fd >= V3S_System.MIN_FD, "fileOpen for output");
            V3S_System.fileWriteK(fd, bytes("xabcx"), 1, 3);
            V3S_System.fileClose(fd);
            eq("abc", text(Files.readAllBytes(out)), "written");
            fd = V3S_System.fileOpen(bytes(out.toString()), true);
            eq(3, V3S_System.fileLeft(fd), "fileLeft");
            eq((int) 'a', V3S_System.fileRead(fd), "fileRead");
            byte[] b = new byte[8];
            eq(2, V3S_System.fileReadK(fd, b, 0, 8), "fileReadK");
            eq(0, V3S_System.fileReadK(fd, b, 0, 8), "fileReadK at the end");
            eq(-1, V3S_System.fileRead(fd), "fileRead at the end");
            V3S_System.fileClose(fd);
        });

        test("errors: native errno numbers use Linux values", () -> {
            String host = System.getProperty("os.name");
            int[][] mappings = {
                {1, 1}, {2, 2}, {3, 3}, {4, 4}, {5, 5}, {7, 7}, {8, 8}, {9, 9}, {10, 10}, {12, 12}, {13, 13},
                {17, 17}, {20, 20}, {21, 21}, {22, 22}, {23, 23}, {24, 24}, {28, 28}, {32, 32},
                {35, 11}, {45, 95}, {62, 40}, {63, 36}, {66, 39}, {0, 5}, {999, 5}
            };
            try {
                for (String mac : new String[] {"Mac OS X", "Darwin"}) {
                    System.setProperty("os.name", mac);
                    for (int[] mapping : mappings) {
                        for (String message : new String[] {"error=" + mapping[0] + ", failure",
                                "Exec failed, error: " + mapping[0] + " (failure)"}) {
                            eq(mapping[1], V3S_System.errno(new IOException(message)), mac + " " + message);
                        }
                    }
                }
                System.setProperty("os.name", "Linux");
                for (int linux : new int[] {2, 11, 13, 20, 36, 39, 40, 95}) {
                    eq(linux, V3S_System.errno(new IOException("error=" + linux)), "Linux " + linux);
                }
                eq(5, V3S_System.errno(new IOException("error=999")), "unknown Linux errno");
                System.setProperty("os.name", "Unknown");
                eq(5, V3S_System.errno(new IOException("error=62")), "unknown host");
            } finally {
                System.setProperty("os.name", host);
            }
        });

        test("errors: NIO filesystem reasons use Linux values", () -> {
            Object[][] mappings = {
                {"File name too long", 36}, {"Too many open files", 24}, {"Too many open files in system", 23},
                {"No space left on device", 28}, {"Not a directory", 20}, {"Is a directory", 21},
                {"Permission denied", 13}, {"Read-only file system", 30}, {"No such file or directory", 2},
                {"Too many levels of symbolic links", 40}, {"Unknown filesystem error", 5}, {null, 5}
            };
            for (Object[] mapping : mappings) {
                FileSystemException failure = new FileSystemException("entry", null, (String) mapping[0]);
                eq(mapping[1], V3S_System.errno(failure), "NIO reason " + mapping[0]);
                eq(mapping[1], V3S_System.errno(new DirectoryIteratorException(failure)), "iterator reason " + mapping[0]);
            }
        });
        test("errors: overlong path components report ENAMETOOLONG", () -> {
            char[] chars = new char[256];
            Arrays.fill(chars, 'a');
            String component = new String(chars);
            Path path = scratch.resolve(component);
            eq(-36, cmd("openRead", path), "path metadata before a regular-file open");
            eq(-36, cmd("openDirectory", path), "directory open");
            int dir = cmd("openDirectory", scratch);
            for (int directory : new int[] {0, 1}) {
                eq(-36, cmd("openAt", dir, component, directory), "relative open, directory=" + directory);
            }
            eq(36, failure("statAt", dir, component), "relative metadata byte reply");
            cmd("close", dir);
        });
        test("descriptors: opened channel validation keeps the original file", () -> {
            Path path = file(scratch.resolve("validated-file"), "original");
            try (FileChannel channel = FileChannel.open(path, StandardOpenOption.READ)) {
                Files.move(path, scratch.resolve("validated-file-moved"));
                file(path, "replacement");
                int fd = V3S_System.openRegular(channel);
                check(fd >= V3S_System.MIN_FD, "validated descriptor: " + fd);
                eq("original", text(readFully(fd, -1, 1000)), "opened file content");
                eq(0, cmd("close", fd), "close descriptor");
                check(!channel.isOpen(), "channel closed with descriptor");
            }
        });
        test("descriptors: opened FIFO validation rejects and closes the channel", () -> {
            Path fifo = scratch.resolve("validated-fifo");
            Process mkfifo = new ProcessBuilder("mkfifo", fifo.toString()).inheritIO().start();
            eq(0, mkfifo.waitFor(), "create FIFO");
            try (FileChannel channel = FileChannel.open(fifo, StandardOpenOption.READ, StandardOpenOption.WRITE)) {
                eq(-V3S_System.EINVAL, V3S_System.openRegular(channel), "FIFO rejected after open");
                check(!channel.isOpen(), "rejected channel closed");
            }
        });

        test("directories: openAt rejects a FIFO swapped in after metadata", () -> {
            Path dir = Files.createDirectory(scratch.resolve("fifo-open-race"));
            Path source = file(dir.resolve("source"), "regular");
            Path fifo = dir.resolve("fifo");
            Process mkfifo = new ProcessBuilder("mkfifo", fifo.toString()).inheritIO().start();
            eq(0, mkfifo.waitFor(), "create FIFO");
            try (FileChannel writer = FileChannel.open(fifo, StandardOpenOption.READ, StandardOpenOption.WRITE);
                    DirectoryStream<Path> listing = Files.newDirectoryStream(dir)) {
                check(writer.isOpen(), "FIFO has a writer before the raced open");
                SecureDirectoryStream<Path> stream = (SecureDirectoryStream<Path>) listing;
                @SuppressWarnings("unchecked")
                SecureDirectoryStream<Path> swapping = (SecureDirectoryStream<Path>) java.lang.reflect.Proxy.newProxyInstance(
                    V3SSystemTest.class.getClassLoader(), new Class<?>[] {SecureDirectoryStream.class},
                    (proxy, method, args) -> {
                        if (method.getName().equals("newByteChannel")) {
                            Files.move(source, dir.resolve("original"));
                            Files.move(fifo, source);
                        }
                        try {
                            return method.invoke(stream, args);
                        } catch (InvocationTargetException e) {
                            throw e.getCause();
                        }
                    });
                int fd = V3S_System.add(new V3S_System.DirEnd(swapping));
                eq(-V3S_System.EINVAL, cmd("openAt", fd, "source", 0), "raced FIFO rejected after open");
                cmd("close", fd);
            }
        });

        // Descriptor reads and writes, and the end of input.
        test("descriptors: a file reads to its end, then 0 every time", () -> {
            StringBuilder s = new StringBuilder();
            for (int i = 0; i < 10000; i++) s.append((char) ('a' + i % 26));
            Path p = file(scratch.resolve("long.txt"), s.toString());
            int fd = cmd("openRead", p);
            check(fd >= V3S_System.MIN_FD, "openRead: " + fd);
            eq(V3S_System.S_IFREG + "," + 10000, longs(payload("stat", fd))[0] + "," + longs(payload("stat", fd))[1], "stat");
            ByteArrayOutputStream all = new ByteArrayOutputStream();
            byte[] b = new byte[4096];
            int[] sizes = new int[5];
            for (int i = 0; i < 5; i++) {
                sizes[i] = V3S_System.read(fd, b, 0, b.length);
                if (sizes[i] > 0) all.write(b, 0, sizes[i]);
            }
            eq("[4096, 4096, 1808, 0, 0]", Arrays.toString(sizes), "read sizes");
            eq(s.toString(), text(all.toByteArray()), "content");
            eq(0, cmd("close", fd), "close");
            eq(-V3S_System.EBADF, cmd("close", fd), "second close");
        });
        test("descriptors: openRead failures", () -> {
            eq(-V3S_System.ENOENT, cmd("openRead", scratch.resolve("missing")), "missing");
            eq(-V3S_System.EISDIR, cmd("openRead", scratch), "directory");
            Path target = file(scratch.resolve("target.txt"), "via link");
            Path link = Files.createSymbolicLink(scratch.resolve("link.txt"), target.getFileName());
            int fd = cmd("openRead", link);
            check(fd >= 0, "openRead follows a symlink, as open(2)");
            eq("via link", text(readFully(fd, 8, 1000)), "content through the link");
            cmd("close", fd);
            Path secret = file(scratch.resolve("secret.txt"), "x");
            Files.setPosixFilePermissions(secret, PosixFilePermissions.fromString("---------"));
            if (!ignoresPermissions(secret)) eq(-V3S_System.EACCES, cmd("openRead", secret), "unreadable");
        });
        test("descriptors: closed descriptors are reused, lowest first", () -> {
            int a = cmd("null"), b = cmd("null");
            eq(V3S_System.MIN_FD, a, "first descriptor");
            eq(V3S_System.MIN_FD + 1, b, "second descriptor");
            cmd("close", a);
            eq(a, cmd("null"), "reused descriptor");
            cmd("close", a);
            int p = cmd("pipe");
            eq(V3S_System.MIN_FD + 2, p, "a pipe needs two free descriptors in a row");
            cmd("close", b);
            cmd("close", p);
            cmd("close", p + 1);
        });
        test("descriptors: each descriptor reads or writes only as it should", () -> {
            byte[] b = new byte[4];
            Path p = file(scratch.resolve("ro.txt"), "ro");
            int fd = cmd("openRead", p);
            eq(-V3S_System.EBADF, V3S_System.write(fd, b, 0, 1), "write to a file opened for reading");
            cmd("close", fd);
            eq(-V3S_System.EBADF, V3S_System.read(1, b, 0, 1), "read standard output");
            eq(-V3S_System.EBADF, V3S_System.read(2, b, 0, 1), "read standard error");
            int n = cmd("null");
            eq(0, V3S_System.read(n, b, 0, 4), "null reads as empty");
            eq(-V3S_System.EBADF, V3S_System.write(n, b, 0, 1), "null is read-only");
            eq(V3S_System.POLLIN, poll(0, n, V3S_System.POLLIN | V3S_System.POLLOUT)[0], "null is readable");
            eq(V3S_System.S_IFCHR, longs(payload("stat", n))[0], "null is a character device");
            cmd("close", n);
        });
        test("descriptors: an unconnected pipe refuses I/O", () -> {
            int p = cmd("pipe");
            byte[] b = new byte[4];
            eq(-V3S_System.EINVAL, V3S_System.read(p, b, 0, 4), "read");
            eq(-V3S_System.EINVAL, V3S_System.write(p + 1, b, 0, 4), "write");
            eq("[0, 0]", Arrays.toString(poll(0, p, V3S_System.POLLIN, p + 1, V3S_System.POLLOUT)), "poll");
            eq(V3S_System.S_IFIFO, longs(payload("stat", p))[0], "a pipe is a FIFO");
            cmd("close", p);
            cmd("close", p + 1);
        });

        // Child-process pipes and lifecycle.
        test("children: a worker exchange over nonblocking pipes", () -> {
            Worker w = start("echo");
            eq(V3S_System.POLLOUT, poll(0, w.requests, V3S_System.POLLOUT)[0], "request pipe is writable");
            eq(5, write(w.requests, "hello"), "write");
            eq("hello", text(readFully(w.results, 5, 20000)), "echoed");
            byte[] b = new byte[16];
            eq(-V3S_System.EAGAIN, V3S_System.read(w.results, b, 0, b.length), "nonblocking read with no data");
            eq(9, terminate(w), "killed worker's wait status");
        });
        test("children: closing the request pipe gives the child EOF", () -> {
            // The child exits 0 only once its read returns 0. If closing the
            // server's end leaked the stream, the child would never see the
            // end and the result pipe would never end.
            Worker w = start("echo");
            eq(2, write(w.requests, "hi"), "write");
            eq("hi", text(readFully(w.results, 2, 20000)), "echoed");
            eq(0, cmd("close", w.requests), "close requests");
            eq("", text(readFully(w.results, -1, 20000)), "the result pipe ends");
            eq(V3S_System.POLLHUP, poll(0, w.results, V3S_System.POLLIN)[0], "POLLHUP at the end");
            byte[] b = new byte[4];
            eq(0, V3S_System.read(w.results, b, 0, 4), "read at the end returns 0");
            eq(0, V3S_System.read(w.results, b, 0, 4), "and again");
            eq(0, cmd("close", w.results), "close results");
            eq(0, cmd("wait", w.pid), "exit status 0, after the child saw EOF");
        });
        test("children: large output arrives whole, then EOF", () -> {
            int size = 4 << 20;
            Worker w = start("flood", String.valueOf(size));
            byte[] got = readFully(w.results, -1, 60000);
            eq(size, got.length, "bytes");
            for (int i = 0; i < size; i++) {
                if (got[i] != (byte) (i % 251)) throw new AssertionError("byte " + i + " differs");
            }
            cmd("close", w.requests);
            cmd("close", w.results);
            eq(0, cmd("wait", w.pid), "exit status");
        });
        test("children: wait reports the exit status", () -> {
            Worker w = start("write", "bye", "7");
            eq("bye", text(readFully(w.results, -1, 20000)), "output");
            cmd("close", w.requests);
            cmd("close", w.results);
            eq(7 << 8, cmd("wait", w.pid), "wait status");
        });
        test("children: System.error, an uncaught exception, and exit 1 all report status 1", () -> {
            // Java reports an uncaught exception as exit status 1, like
            // System.exit(1), and V3S_System.error throws as well, so wait
            // can't tell them apart. The server classifies a worker failure
            // from protocol state, not this number (#462).
            for (Worker w : new Worker[] {start("write", "", "1"), start("error"), start("throw")}) {
                eq("", text(readFully(w.results, -1, 20000)), "output");
                cmd("close", w.requests);
                cmd("close", w.results);
                eq(1 << 8, cmd("wait", w.pid), "wait status");
            }
        });
        test("children: JVM exit values 129–159 conventionally mean signals", () -> {
            for (int status : new int[] {128, 129, 137, 142, 159, 160, 255}) {
                Worker w = start("write", "", String.valueOf(status));
                eq("", text(readFully(w.results, -1, 20000)), "output");
                cmd("close", w.requests);
                cmd("close", w.results);
                int expected = status >= 129 && status <= 159 ? status - 128 : status << 8;
                eq(expected, cmd("wait", w.pid), "wait status for exit " + status);
            }
        });
        test("children: the child's environment holds only the descriptor layout", () -> {
            Worker w = start("environment");
            eq("[" + V3S_System.LAYOUT_ENV + "]", text(readFully(w.results, -1, 20000)), "environment");
            cmd("close", w.requests);
            cmd("close", w.results);
            eq(0, cmd("wait", w.pid), "wait status");
        });
        test("children: kill and wait", () -> {
            Worker w = start("sleep");
            eq(-V3S_System.EINVAL, cmd("kill", w.pid, 5), "unsupported signal");
            eq(0, cmd("kill", w.pid, 0), "signal 0 checks the child");
            eq(0, cmd("kill", w.pid, 9), "kill");
            eq(9, cmd("wait", w.pid), "killed by signal 9");
            eq(-V3S_System.ESRCH, cmd("kill", w.pid, 9), "kill after wait");
            eq(-V3S_System.ECHILD, cmd("wait", w.pid), "second wait");
            cmd("close", w.requests);
            cmd("close", w.results);
        });
        test("children: the child's descriptors match the native worker's", () -> {
            // Descriptor 0 reads as empty, 1 and 2 are standard error, and
            // nothing but descriptor 4 reaches the result pipe, not even
            // Java's System.out or Virgil's System.puts.
            Worker w = start("layout");
            eq("fd0=0 fd1=7 fd2=7 fd5=-9", text(readFully(w.results, -1, 20000)), "result pipe");
            cmd("close", w.requests);
            cmd("close", w.results);
            eq(0, cmd("wait", w.pid), "exit status");
        });
        test("children: spawn failures leave nothing behind", () -> {
            int requests = cmd("pipe"), results = cmd("pipe"), input = cmd("null");
            Path notExecutable = file(scratch.resolve("plain.sh"), "#!/bin/sh\n");
            Path text = file(scratch.resolve("data.txt"), "x");
            int textFd = cmd("openRead", text);
            eq(-V3S_System.ENOENT, cmd("spawn", 5, input, 2, 2, requests, results + 1, new Object[] {scratch.resolve("missing")}), "missing program");
            eq(-V3S_System.EACCES, cmd("spawn", 5, input, 2, 2, requests, results + 1, new Object[] {notExecutable}), "not executable");
            eq(-V3S_System.EINVAL, cmd("spawn", 1, textFd, childArgv("echo")), "a file isn't a child descriptor");
            eq(-V3S_System.EBADF, cmd("spawn", 1, 999, childArgv("echo")), "an unknown descriptor");
            eq(-V3S_System.EINVAL, cmd("spawn", 2, requests, results, childArgv("echo")), "two read ends");
            eq(-V3S_System.EINVAL, cmd("spawn", 11, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, childArgv("echo")), "more descriptors than MIN_FD");
            eq(0, V3S_System.childCount(), "no children");
            int pid = cmd("spawn", 5, input, 2, 2, requests, results + 1, childArgv("echo"));
            check(pid > 0, "spawn after failures: " + pid);
            eq(-V3S_System.EINVAL, cmd("spawn", 1, requests, childArgv("echo")), "a connected pipe can't be reused");
            for (int fd : new int[] {requests, requests + 1, results, results + 1, input, textFd}) cmd("close", fd);
            cmd("kill", pid, 9);
            cmd("wait", pid);
        });
        test("children: writing to an exited child fails with EPIPE and POLLERR", () -> {
            Worker w = start("write", "x", "0");
            eq("x", text(readFully(w.results, -1, 20000)), "output");
            eq(0, cmd("wait", w.pid), "exit status");
            byte[] chunk = new byte[65536];
            long deadline = System.currentTimeMillis() + 20000;
            int n;
            while ((n = V3S_System.write(w.requests, chunk, 0, chunk.length)) == chunk.length) {
                check(System.currentTimeMillis() < deadline, "writes kept succeeding");
                sleep(5);
            }
            eq(-V3S_System.EPIPE, n, "write after the child exited");
            eq(V3S_System.POLLOUT | V3S_System.POLLERR, poll(0, w.requests, V3S_System.POLLOUT)[0], "poll");
            cmd("close", w.requests);
            cmd("close", w.results);
        });
        test("children: an alarm ends the child as SIGALRM", () -> {
            long start = System.currentTimeMillis();
            Worker w = start("alarm", "300");
            eq(14, cmd("wait", w.pid), "ended by signal 14");
            check(System.currentTimeMillis() - start >= 300, "not before the alarm");
            cmd("close", w.requests);
            cmd("close", w.results);
        });
        test("children: a cancelled alarm doesn't fire", () -> {
            Worker w = start("alarm-cancel");
            eq(3 << 8, cmd("wait", w.pid), "exit status 3");
            cmd("close", w.requests);
            cmd("close", w.results);
        });

        // Polling and deadlines.
        test("polling: timeouts and deadlines", () -> {
            Worker w = start("sleep");
            long t0 = System.nanoTime();
            eq("[0]", Arrays.toString(poll(0, w.results, V3S_System.POLLIN)), "no data");
            check((System.nanoTime() - t0) / 1000000 < 1000, "a zero timeout returns at once");
            t0 = System.nanoTime();
            eq("[0]", Arrays.toString(poll(200, w.results, V3S_System.POLLIN)), "no data after waiting");
            long ms = (System.nanoTime() - t0) / 1000000;
            check(ms >= 190 && ms < 5000, "waited " + ms + " ms for a 200 ms timeout");
            t0 = System.nanoTime();
            eq(0, poll(100).length, "no descriptors");
            ms = (System.nanoTime() - t0) / 1000000;
            check(ms >= 90, "no descriptors still waits: " + ms + " ms");
            eq("[0, 32]", Arrays.toString(poll(-1, -1, V3S_System.POLLIN, 999, V3S_System.POLLIN)), "a negative descriptor is ignored and an unknown one is POLLNVAL");
            eq(9, terminate(w), "wait status");
        });
        test("polling: data ends a long wait early", () -> {
            Worker w = start("late");
            long t0 = System.nanoTime();
            eq(V3S_System.POLLIN, poll(30000, w.results, V3S_System.POLLIN)[0], "readable");
            long ms = (System.nanoTime() - t0) / 1000000;
            check(ms < 20000, "woke after " + ms + " ms");
            eq("late", text(readFully(w.results, 4, 1000)), "data");
            eq(9, terminate(w), "wait status");
        });

        // Directory entries and file metadata.
        test("directories: entries and their types", () -> {
            Path d = tree("entries");
            int fd = cmd("openDirectory", d);
            check(fd >= V3S_System.MIN_FD, "openDirectory: " + fd);
            TreeMap<String, Integer> entries = new TreeMap<String, Integer>();
            parseDirents(payload("readDirectory", fd, 8192), entries);
            eq(0, payload("readDirectory", fd, 8192).length, "empty at the end");
            eq(expectedEntries(), entries, "entries");
            cmd("close", fd);
        });
        test("directories: a small buffer spreads entries over several reads", () -> {
            Path d = tree("small");
            int fd = cmd("openDirectory", d);
            eq(V3S_System.EINVAL, failure("readDirectory", fd, 16), "a buffer too small for one entry");
            TreeMap<String, Integer> entries = new TreeMap<String, Integer>();
            int reads = 0;
            while (true) {
                byte[] b = payload("readDirectory", fd, 40);
                check(b.length <= 40, "at most the buffer size");
                if (b.length == 0) break;
                parseDirents(b, entries);
                reads++;
            }
            check(reads >= expectedEntries().size(), "one entry per read: " + reads);
            eq(expectedEntries(), entries, "entries");
            cmd("close", fd);
        });
        test("directories: openAt never follows a symlink", () -> {
            Path d = tree("openat");
            int dir = cmd("openDirectory", d);
            int fd = cmd("openAt", dir, "a.v3", 0);
            check(fd >= 0, "a file: " + fd);
            eq("[" + V3S_System.S_IFREG + ", 5]", Arrays.toString(longs(payload("stat", fd))), "its metadata");
            eq("a = 1", text(readFully(fd, 5, 1000)), "its content");
            eq(-V3S_System.ENOTDIR, cmd("openAt", fd, "x", 0), "openAt in a file");
            eq(V3S_System.ENOTDIR, failure("readDirectory", fd, 64), "readDirectory of a file");
            cmd("close", fd);
            int tab = cmd("openAt", dir, "tab\tname", 0);
            eq("tab", text(readFully(tab, 3, 1000)), "a name with a tab");
            cmd("close", tab);
            int sub = cmd("openAt", dir, "sub", 1);
            check(sub >= 0, "a directory: " + sub);
            eq(V3S_System.S_IFDIR, longs(payload("stat", sub))[0], "its metadata");
            eq(-V3S_System.EISDIR, V3S_System.read(sub, new byte[4], 0, 4), "read a directory");
            cmd("close", sub);
            int subAsFile = cmd("openAt", dir, "sub", 0);
            check(subAsFile >= 0, "a directory without O_DIRECTORY opens, as openat");
            cmd("close", subAsFile);
            eq(-V3S_System.ELOOP, cmd("openAt", dir, "link", 0), "a symlink to a file");
            eq(-V3S_System.ENOTDIR, cmd("openAt", dir, "link", 1), "a symlink as a directory");
            eq(-V3S_System.ENOTDIR, cmd("openAt", dir, "dlink", 1), "a symlink to a directory");
            eq(-V3S_System.ENOTDIR, cmd("openAt", dir, "a.v3", 1), "a file as a directory");
            eq(-V3S_System.ENOENT, cmd("openAt", dir, "missing", 0), "a missing entry");
            eq(-V3S_System.EINVAL, cmd("openAt", dir, "sub/b.v3", 0), "a name with a slash");
            eq(-V3S_System.EBADF, cmd("openAt", 999, "a.v3", 0), "an unknown descriptor");
            if (!ignoresPermissions(d.resolve("blocked"))) {
                eq(-V3S_System.EACCES, cmd("openAt", dir, "blocked", 1), "an unreadable directory fails when opened");
            }
            cmd("close", dir);
        });
        test("directories: openAt stays anchored after parent replacement", () -> {
            Path original = tree("anchored-open");
            Path moved = scratch.resolve("anchored-open-moved");
            Path replacement = tree("anchored-open-replacement");
            file(replacement.resolve("a.v3"), "replacement");
            file(replacement.resolve("sub").resolve("b.v3"), "replacement");
            int dir = cmd("openDirectory", original);
            Files.move(original, moved);
            Files.createSymbolicLink(original, replacement);
            int fd = cmd("openAt", dir, "a.v3", 0);
            eq("a = 1", text(readFully(fd, -1, 1000)), "original file");
            cmd("close", fd);
            for (int flag : new int[] {0, 1}) {
                int sub = cmd("openAt", dir, "sub", flag);
                int child = cmd("openAt", sub, "b.v3", 0);
                eq("b", text(readFully(child, -1, 1000)), "original subdirectory");
                cmd("close", child);
                cmd("close", sub);
            }
            eq(-V3S_System.ELOOP, cmd("openAt", dir, "link", 0), "final symlink refused");
            eq(-V3S_System.ENOTDIR, cmd("openAt", dir, "dlink", 1), "intermediate directory symlink refused");
            cmd("close", dir);
        });
        for (int flag : new int[] {0, 1}) {
            test("directories: child directory stays anchored after parent replacement, flag " + flag, () -> {
                Path original = tree("anchored-child-" + flag);
                Path replacement = tree("anchored-child-replacement-" + flag);
                file(replacement.resolve("sub").resolve("b.v3"), "replacement");
                int dir = cmd("openDirectory", original);
                Files.move(original, scratch.resolve("anchored-child-moved-" + flag));
                Files.createSymbolicLink(original, replacement);
                int sub = cmd("openAt", dir, "sub", flag);
                int child = cmd("openAt", sub, "b.v3", 0);
                eq("b", text(readFully(child, -1, 1000)), "original child directory");
                cmd("close", child);
                cmd("close", sub);
                cmd("close", dir);
            });
        }
        test("metadata: statAt stays anchored after parent replacement", () -> {
            Path original = tree("anchored-statat");
            Path replacement = tree("anchored-statat-replacement");
            file(replacement.resolve("a.v3"), "replacement");
            int dir = cmd("openDirectory", original);
            Files.move(original, scratch.resolve("anchored-statat-moved"));
            Files.createSymbolicLink(original, replacement);
            eq("[" + V3S_System.S_IFREG + ", 5]", Arrays.toString(longs(payload("statAt", dir, "a.v3"))), "original metadata");
            eq(V3S_System.S_IFLNK, longs(payload("statAt", dir, "dlink"))[0], "symlink metadata");
            cmd("close", dir);
        });
        test("metadata: stat uses the open directory after path replacement", () -> {
            Path original = tree("anchored-stat");
            int dir = cmd("openDirectory", original);
            long[] before = longs(payload("stat", dir));
            Files.move(original, scratch.resolve("anchored-stat-moved"));
            file(original, "replacement");
            eq(Arrays.toString(before), Arrays.toString(longs(payload("stat", dir))), "original directory metadata");
            cmd("close", dir);
        });
        test("directories: entry types stay anchored after parent replacement", () -> {
            Path original = tree("anchored-list");
            Path replacement = Files.createDirectory(scratch.resolve("anchored-list-replacement"));
            Files.createDirectory(replacement.resolve("a.v3"));
            file(replacement.resolve("sub"), "replacement");
            int dir = cmd("openDirectory", original);
            Files.move(original, scratch.resolve("anchored-list-moved"));
            Files.createSymbolicLink(original, replacement);
            TreeMap<String, Integer> entries = new TreeMap<String, Integer>();
            parseDirents(payload("readDirectory", dir, 8192), entries);
            eq(expectedEntries(), entries, "original entries and types");
            eq(0, payload("readDirectory", dir, 8192).length, "end of directory");
            cmd("close", dir);
        });
        test("directories: an insecure provider fails closed and closes its stream", () -> {
            final boolean[] closed = {false};
            DirectoryStream<Path> plain = new DirectoryStream<Path>() {
                public Iterator<Path> iterator() { throw new AssertionError("insecure directory was read"); }
                public void close() { closed[0] = true; }
            };
            eq(-V3S_System.ENOTSUP, V3S_System.openDirectory(plain), "unsupported directory provider");
            check(closed[0], "unsupported directory stream closed");
        });
        test("metadata: stat and statAt", () -> {
            Path d = tree("stat");
            int dir = cmd("openDirectory", d);
            eq(V3S_System.S_IFDIR, longs(payload("stat", dir))[0], "the directory");
            eq("[" + V3S_System.S_IFREG + ", 5]", Arrays.toString(longs(payload("statAt", dir, "a.v3"))), "a file");
            eq(V3S_System.S_IFDIR, longs(payload("statAt", dir, "sub"))[0], "a subdirectory");
            eq(V3S_System.S_IFLNK, longs(payload("statAt", dir, "link"))[0], "a symlink isn't followed");
            eq(V3S_System.S_IFLNK, longs(payload("statAt", dir, "dlink"))[0], "nor a symlink to a directory");
            eq(V3S_System.ENOENT, failure("statAt", dir, "missing"), "a missing entry");
            eq(V3S_System.EBADF, failure("statAt", 999, "a.v3"), "an unknown descriptor");
            int n = cmd("null");
            eq(V3S_System.ENOTDIR, failure("statAt", n, "a.v3"), "not a directory");
            cmd("close", n);
            cmd("close", dir);
        });
        test("directories: openDirectory follows symlinks, as open(2)", () -> {
            Path d = tree("opendir");
            eq(-V3S_System.ENOENT, cmd("openDirectory", d.resolve("missing")), "missing");
            eq(-V3S_System.ENOTDIR, cmd("openDirectory", d.resolve("a.v3")), "a file");
            int fd = cmd("openDirectory", d.resolve("dlink"));
            check(fd >= 0, "a symlink to a directory: " + fd);
            TreeMap<String, Integer> entries = new TreeMap<String, Integer>();
            parseDirents(payload("readDirectory", fd, 8192), entries);
            eq("{b.v3=8}", entries.toString(), "its entries");
            cmd("close", fd);
        });

        // The other operations that the server and the worker need.
        test("host: access checks that a file is executable", () -> {
            Path script = file(scratch.resolve("run.sh"), "#!/bin/sh\n");
            Files.setPosixFilePermissions(script, PosixFilePermissions.fromString("rwxr-xr-x"));
            Path plain = file(scratch.resolve("plain.txt"), "x");
            eq(0, cmd("access", script), "executable");
            eq(-V3S_System.EACCES, cmd("access", plain), "not executable");
            eq(-V3S_System.ENOENT, cmd("access", scratch.resolve("missing")), "missing");
        });
        test("host: random words", () -> {
            byte[] a = payload("random"), b = payload("random");
            eq(16, a.length, "length");
            check(!Arrays.equals(a, b), "two calls differ");
        });
        test("host: the executable's path", () -> {
            String before = System.getProperty("virgil.lsp.executable");
            try {
                System.setProperty("virgil.lsp.executable", "/opt/virgil-lsp/bin/virgil-lsp");
                eq("/opt/virgil-lsp/bin/virgil-lsp", text(payload("executable")), "the launcher's property");
                System.clearProperty("virgil.lsp.executable");
                String path = text(payload("executable"));
                check(path.startsWith("/") && Files.exists(Paths.get(path)), "the runtime's code source: " + path);
            } finally {
                if (before == null) System.clearProperty("virgil.lsp.executable");
                else System.setProperty("virgil.lsp.executable", before);
            }
        });
        test("host: heap size, heap in use, and gc", () -> {
            long[] m = longs(payload("memory"));
            eq(2, m.length, "values");
            check(m[0] > 0 && m[1] > 0 && m[1] <= m[0], "max " + m[0] + ", used " + m[1]);
            eq(0, cmd("gc"), "gc");
        });
        test("host: the thread's stack depth in frames", () -> {
            long[] d = longs(payload("stack"));
            eq(1, d.length, "values");
            check(d[0] > 4, "depth " + d[0]);
        });
        test("host: standard output keeps raw bytes in order", () -> {
            ProcessBuilder pb = new ProcessBuilder(childArgv("print"));
            pb.environment().remove(V3S_System.LAYOUT_ENV);
            pb.redirectError(ProcessBuilder.Redirect.INHERIT);
            Process p = pb.start();
            p.getOutputStream().close();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            InputStream in = p.getInputStream();
            byte[] b = new byte[256];
            for (int n; (n = in.read(b)) > 0; ) out.write(b, 0, n);
            eq(0, p.waitFor(), "exit status");
            byte[] expected = {'A', 'b', 'c', '-', '4', '2', '\n', (byte) 0xE9, (byte) 0xE9, 'z'};
            eq(Arrays.toString(expected), Arrays.toString(out.toByteArray()), "standard output");
        });

        // The class as a whole: upstream's interface, and what a jar needs.
        test("upstream: every System call keeps upstream's signature", () -> {
            Path classes = Paths.get(property("v3s.upstream.classes"));
            URLClassLoader loader = new URLClassLoader(new URL[] {classes.toUri().toURL()}, null);
            Class<?> upstream = Class.forName("V3S_System", false, loader);
            check(upstream != V3S_System.class, "loaded upstream's class");
            int count = 0;
            for (Method m : upstream.getDeclaredMethods()) {
                if (!Modifier.isPublic(m.getModifiers()) || !Modifier.isStatic(m.getModifiers())) continue;
                Method ours = V3S_System.class.getMethod(m.getName(), m.getParameterTypes());
                check(Modifier.isStatic(ours.getModifiers()), m + " is static");
                eq(m.getReturnType(), ours.getReturnType(), m.getName() + " return type");
                count++;
            }
            check(count > 50, "upstream's methods: " + count);
        });
        test("upstream: unchanged methods match executable results and failures", () -> {
            Path classes = Paths.get(property("v3s.upstream.classes"));
            try (URLClassLoader loader = new URLClassLoader(new URL[] {classes.toUri().toURL()}, null)) {
                Class<?> upstream = Class.forName("V3S_System", true, loader);
                List<String> replaced = Arrays.asList("putc", "puts", "puti", "ln", "fileRead", "fileWriteK", "fileReadK",
                    "write", "read", "fileOpen", "fileLeft", "fileClose", "fileLoad");
                for (Method method : upstream.getDeclaredMethods()) {
                    if (!Modifier.isPublic(method.getModifiers()) || !Modifier.isStatic(method.getModifiers())) continue;
                    String name = method.getName();
                    if (replaced.contains(name)) continue;
                    Method ours = V3S_System.class.getMethod(name, method.getParameterTypes());
                    if (name.startsWith("ticks")) {
                        int before = (Integer) method.invoke(null);
                        int actual = (Integer) ours.invoke(null);
                        int after = (Integer) method.invoke(null);
                        check(Integer.compareUnsigned(actual - before, after - before) <= 0, name + " clock interval");
                    } else if (name.equals("chmod")) {
                        Path reference = file(scratch.resolve("upstream-mode"), "x");
                        Path actual = file(scratch.resolve("runtime-mode"), "x");
                        for (int mode : new int[] {0600, 0755, 0000}) {
                            method.invoke(null, bytes(reference.toString()), mode);
                            ours.invoke(null, bytes(actual.toString()), mode);
                            eq(Files.getPosixFilePermissions(reference), Files.getPosixFilePermissions(actual), "chmod " + mode);
                        }
                    } else if (name.equals("error")) {
                        compareInvocation(method, ours, new Object[] {bytes("type"), bytes("message")});
                        compareInvocation(method, ours, new Object[] {null, bytes("")});
                    } else {
                        compareInputs(method, ours, new Object[method.getParameterCount()], 0);
                    }
                }
            }
        });
        test("packaging: every runtime class is V3S_System or nested in it", () -> {
            // Aeneas copies only V3S_System.class into a jar; the jar build
            // must add the rest, which this name pattern finds.
            File[] files = new File(property("v3s.runtime.classes")).listFiles();
            check(files != null && files.length > 1, "compiled runtime classes");
            for (File f : files) {
                check(f.getName().matches("V3S_System(\\$[A-Za-z0-9_$]+)?\\.class"), "unexpected runtime file " + f.getName());
            }
        });
    }

    static String property(String name) {
        String v = System.getProperty(name);
        check(v != null, "set -D" + name + " (see the Makefile's test-jvm-runtime)");
        return v;
    }

    static void compareInputs(Method reference, Method actual, Object[] args, int at) throws Exception {
        if (at == args.length) {
            compareInvocation(reference, actual, args);
            return;
        }
        Class<?> type = reference.getParameterTypes()[at];
        Object[] values;
        boolean bytes = reference.getName().startsWith("readBytes") || reference.getName().startsWith("setBytes");
        if (type == byte[].class) {
            byte[] pattern = new byte[64];
            for (int i = 0; i < pattern.length; i++) pattern[i] = (byte) (i * 37 + 129);
            values = new Object[] {null, new byte[0], pattern};
        } else if (type == Object.class) {
            values = new Object[] {null, "", "same", "other", 0, 1};
        } else if (type == int.class && bytes && (at == 1 || at == args.length - 1)) {
            values = new Object[] {-1, 0, 1, 8, 63, 64, Integer.MAX_VALUE};
        } else if (type == int.class) {
            values = new Object[] {Integer.MIN_VALUE, -16777217, -65, -33, -1, 0, 1, 2, 7, 31, 32, 33, 63, 64,
                16777216, 16777217, Integer.MAX_VALUE};
        } else if (type == long.class) {
            values = new Object[] {Long.MIN_VALUE, -9007199254740993L, -65L, -1L, 0L, 1L, 2L, 31L, 32L, 63L, 64L,
                16777217L, 9007199254740992L, 9007199254740993L, Long.MAX_VALUE};
        } else if (type == byte.class) {
            values = new Object[] {Byte.MIN_VALUE, (byte) -1, (byte) 0, (byte) 1, (byte) 31, (byte) 63, Byte.MAX_VALUE};
        } else if (type == short.class) {
            values = new Object[] {Short.MIN_VALUE, (short) -1, (short) 0, (short) 1, Short.MAX_VALUE};
        } else if (type == float.class) {
            values = new Object[] {Float.NEGATIVE_INFINITY, -Float.MAX_VALUE, -1.5f, -0.0f, 0.0f, Float.MIN_VALUE,
                1.0f, 1.5f, Float.MAX_VALUE, Float.POSITIVE_INFINITY, Float.NaN, Float.intBitsToFloat(0xFFC00001)};
        } else if (type == double.class) {
            values = new Object[] {Double.NEGATIVE_INFINITY, -Double.MAX_VALUE, -0x1p63, -1.5d, -0.0d, 0.0d,
                Double.MIN_VALUE, 1.0d, 1.5d, 0x1p63, 0x1p64, Double.MAX_VALUE, Double.POSITIVE_INFINITY,
                Double.NaN, Double.longBitsToDouble(0xFFF8000000000001L)};
        } else {
            throw new AssertionError("uncovered parameter: " + reference + " " + type);
        }
        for (Object value : values) {
            args[at] = value;
            compareInputs(reference, actual, args, at + 1);
        }
    }

    static void compareInvocation(Method reference, Method actual, Object[] inputs) throws Exception {
        Object[] expectedArgs = inputs.clone(), actualArgs = inputs.clone();
        for (int i = 0; i < inputs.length; i++) {
            if (inputs[i] instanceof byte[]) {
                expectedArgs[i] = ((byte[]) inputs[i]).clone();
                actualArgs[i] = ((byte[]) inputs[i]).clone();
            }
        }
        Object expected = null, got = null;
        Throwable expectedFailure = null, actualFailure = null;
        try {
            expected = reference.invoke(null, expectedArgs);
        } catch (InvocationTargetException e) {
            expectedFailure = e.getCause();
        }
        try {
            got = actual.invoke(null, actualArgs);
        } catch (InvocationTargetException e) {
            actualFailure = e.getCause();
        }
        String context = reference.getName() + Arrays.deepToString(inputs);
        eq(expectedFailure == null ? null : expectedFailure.getClass(),
            actualFailure == null ? null : actualFailure.getClass(), context + " exception");
        if (expectedFailure != null && reference.getName().equals("error")) {
            eq(expectedFailure.getMessage(), actualFailure.getMessage(), context + " message");
        }
        if (expected instanceof Float) {
            expected = Float.floatToRawIntBits((Float) expected);
            got = Float.floatToRawIntBits((Float) got);
        } else if (expected instanceof Double) {
            expected = Double.doubleToRawLongBits((Double) expected);
            got = Double.doubleToRawLongBits((Double) got);
        }
        eq(expected, got, context + " result");
        check(Arrays.deepEquals(expectedArgs, actualArgs), context + " side effects");
    }

    // A directory for the directory tests: a.v3, sub/b.v3, a symlink to each,
    // a name with a tab, a non-ASCII name, and an unreadable directory.
    static Path tree(String name) throws IOException {
        Path d = Files.createDirectory(scratch.resolve(name));
        file(d.resolve("a.v3"), "a = 1");
        Files.createDirectory(d.resolve("sub"));
        file(d.resolve("sub").resolve("b.v3"), "b");
        Files.createSymbolicLink(d.resolve("link"), Paths.get("a.v3"));
        Files.createSymbolicLink(d.resolve("dlink"), Paths.get("sub"));
        file(d.resolve("tab\tname"), "tab");
        file(d.resolve("é.v3"), "e");
        Path blocked = Files.createDirectory(d.resolve("blocked"));
        Files.setPosixFilePermissions(blocked, PosixFilePermissions.fromString("---------"));
        return d;
    }

    static TreeMap<String, Integer> expectedEntries() {
        TreeMap<String, Integer> m = new TreeMap<String, Integer>();
        m.put("a.v3", V3S_System.DT_REG);
        m.put("sub", V3S_System.DT_DIR);
        m.put("link", V3S_System.DT_LNK);
        m.put("dlink", V3S_System.DT_LNK);
        m.put("tab\tname", V3S_System.DT_REG);
        m.put("é.v3", V3S_System.DT_REG);
        m.put("blocked", V3S_System.DT_DIR);
        return m;
    }

    // Parses linux_dirent64 records as src/workspace/ProjectFiles.v3 does,
    // with HostOs's offsets: the record length at 16, the type at 18, and the
    // name at 19.
    static void parseDirents(byte[] b, Map<String, Integer> into) {
        int at = 0;
        while (at < b.length) {
            int len = (b[at + 16] & 0xFF) | ((b[at + 17] & 0xFF) << 8);
            check(len > 19 && len % 8 == 0 && at + len <= b.length, "record length " + len);
            int end = at + 19;
            while (end < at + len && b[end] != 0) end++;
            check(end < at + len, "a name ends with a 0 byte");
            String name = new String(b, at + 19, end - at - 19, StandardCharsets.UTF_8);
            check(!name.equals(".") && !name.equals(".."), "no . or ..");
            check(into.put(name, (int) b[at + 18]) == null, "listed once: " + name);
            at += len;
        }
    }
}
