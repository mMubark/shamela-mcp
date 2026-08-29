package dev.shamela.mcp;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * NDJSON server over stdin/stdout: one JSON request per line, one response per line.
 *
 * <p>Started lazily by the Python side and kept alive between calls, because opening a
 * multi-gigabyte Lucene index costs far more than any single query. The parent process
 * id is passed as the only argument: if Claude Desktop dies, this process would
 * otherwise keep the index files open indefinitely, so a watchdog thread exits when the
 * parent disappears.
 */
public final class Main {

    private static IndexCache cache;
    private static String storeDir;

    public static void main(String[] args) {
        if (args.length > 0) {
            startParentWatchdog(args[0]);
        }

        PrintStream out = new PrintStream(System.out, true, StandardCharsets.UTF_8);
        BufferedReader in = new BufferedReader(
                new InputStreamReader(System.in, StandardCharsets.UTF_8));

        // Announced before the first request so the caller can distinguish a helper
        // that is starting up from one that failed to start at all.
        out.println(Json.write(Map.of("id", "ready", "ok", true)));

        try {
            String line;
            while ((line = in.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty()) {
                    continue;
                }
                Object id = null;
                try {
                    Map<String, Object> request = Json.readObject(line);
                    id = request.get("id");
                    String cmd = Json.str(request, "cmd", "");
                    if ("close".equals(cmd)) {
                        out.println(Json.write(response(id, Map.of("closed", true))));
                        break;
                    }
                    Object result = dispatch(request, cmd);
                    out.println(Json.write(response(id, result)));
                } catch (Throwable t) {
                    out.println(Json.write(failure(id, t)));
                }
            }
        } catch (IOException e) {
            // Parent closed the pipe: nothing left to serve.
        } finally {
            if (cache != null) {
                cache.close();
            }
        }
    }

    private static Object dispatch(Map<String, Object> request, String cmd) throws IOException {
        IndexCache indexes = indexes(request);
        switch (cmd) {
            case "health":
                return Commands.health(indexes);

            case "search": {
                List<String> bookIds = Json.strings(request.get("bookIds"));
                return Commands.search(
                        indexes,
                        Json.str(request, "field", "body"),
                        Json.str(request, "mode", "all_terms"),
                        groups(request),
                        bookIds.isEmpty() ? null : bookIds,
                        Json.integer(request, "limit", 10),
                        Json.optionalInt(request, "afterDoc"),
                        Json.optionalDouble(request, "afterScore"));
            }

            case "count_by_book":
                return Commands.countByBook(
                        indexes,
                        Json.str(request, "field", "body"),
                        Json.str(request, "mode", "all_terms"),
                        groups(request),
                        Json.strings(request.get("bookIds")));

            case "get_pages":
                return Commands.getPages(indexes, bookRequests(request));

            case "get_titles":
                return Commands.getTitles(indexes, bookRequests(request));

            case "probe":
                return Commands.probe(
                        indexes,
                        Json.str(request, "index", "page"),
                        Json.str(request, "field", "body"),
                        Json.strings(request.get("terms")));

            default:
                throw new IllegalArgumentException("unknown command: " + cmd);
        }
    }

    private static synchronized IndexCache indexes(Map<String, Object> request) {
        String requested = Json.str(request, "storeDir", null);
        if (requested == null || requested.isBlank()) {
            if (cache == null) {
                throw new IllegalArgumentException("storeDir is required");
            }
            return cache;
        }
        if (cache == null || !requested.equals(storeDir)) {
            if (cache != null) {
                cache.close();
            }
            cache = new IndexCache(Path.of(requested));
            storeDir = requested;
        }
        return cache;
    }

    private static List<List<String>> groups(Map<String, Object> request) {
        List<List<String>> groups = new ArrayList<>();
        for (Object entry : Json.list(request, "groups")) {
            List<String> terms = Json.strings(entry);
            if (!terms.isEmpty()) {
                groups.add(terms);
            }
        }
        if (groups.isEmpty()) {
            throw new IllegalArgumentException("groups must hold at least one term");
        }
        return groups;
    }

    private static List<Commands.BookRequest> bookRequests(Map<String, Object> request) {
        List<Commands.BookRequest> requests = new ArrayList<>();
        for (Object entry : Json.list(request, "requests")) {
            if (!(entry instanceof Map<?, ?> raw)) {
                continue;
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> item = (Map<String, Object>) raw;
            String bookId = String.valueOf(item.get("bookId"));
            List<Integer> ids = new ArrayList<>();
            for (Object id : Json.list(item, "ids")) {
                if (id instanceof Number n) {
                    ids.add(n.intValue());
                }
            }
            if (!ids.isEmpty()) {
                requests.add(new Commands.BookRequest(bookId, ids));
            }
        }
        return requests;
    }

    private static Map<String, Object> response(Object id, Object result) {
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("id", id);
        response.put("ok", true);
        response.put("result", result);
        return response;
    }

    private static Map<String, Object> failure(Object id, Throwable t) {
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("id", id);
        response.put("ok", false);
        String message = t.getMessage() == null ? t.toString() : t.getMessage();
        response.put("error", t.getClass().getSimpleName() + ": " + message);
        return response;
    }

    private static void startParentWatchdog(String parentPid) {
        final long pid;
        try {
            pid = Long.parseLong(parentPid.trim());
        } catch (NumberFormatException e) {
            return;
        }
        Thread watchdog = new Thread(() -> {
            while (true) {
                try {
                    Thread.sleep(10_000);
                } catch (InterruptedException e) {
                    return;
                }
                if (ProcessHandle.of(pid).map(ProcessHandle::isAlive).orElse(false)) {
                    continue;
                }
                // The parent is gone; release the index files instead of lingering.
                Runtime.getRuntime().halt(0);
            }
        }, "parent-watchdog");
        watchdog.setDaemon(true);
        watchdog.start();
    }
}
