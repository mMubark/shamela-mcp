package dev.shamela.mcp;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.StoredFields;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.store.Directory;
import org.apache.lucene.store.FSDirectory;

/**
 * Read-only Lucene readers, opened once and kept for the life of the process.
 *
 * <p>Opening the page index is the expensive part of a query — it is multiple
 * gigabytes — so the reader, searcher, and stored-fields accessor are cached per index
 * name. Nothing here ever writes: the user's library is not ours to modify.
 */
final class IndexCache implements AutoCloseable {

    static final String PAGE = "page";
    static final String TITLE = "title";

    private final Path storeRoot;
    private final Map<String, Entry> entries = new HashMap<>();

    IndexCache(Path storeRoot) {
        this.storeRoot = storeRoot;
    }

    private record Entry(Directory directory, DirectoryReader reader, IndexSearcher searcher,
                         StoredFields storedFields) {}

    private synchronized Entry entry(String name) throws IOException {
        Entry existing = entries.get(name);
        if (existing != null) {
            return existing;
        }
        Path indexPath = storeRoot.resolve(name);
        if (!Files.isDirectory(indexPath)) {
            throw new IOException("index directory not found: " + indexPath);
        }
        Directory directory = FSDirectory.open(indexPath);
        DirectoryReader reader = DirectoryReader.open(directory);
        Entry created = new Entry(directory, reader, new IndexSearcher(reader), reader.storedFields());
        entries.put(name, created);
        return created;
    }

    DirectoryReader reader(String name) throws IOException {
        return entry(name).reader();
    }

    IndexSearcher searcher(String name) throws IOException {
        return entry(name).searcher();
    }

    StoredFields storedFields(String name) throws IOException {
        return entry(name).storedFields();
    }

    boolean has(String name) {
        return Files.isDirectory(storeRoot.resolve(name));
    }

    Path storeRoot() {
        return storeRoot;
    }

    @Override
    public synchronized void close() {
        for (Entry entry : entries.values()) {
            try {
                entry.reader().close();
            } catch (IOException ignored) {
                // Shutting down; a failed close has nothing left to affect.
            }
            try {
                entry.directory().close();
            } catch (IOException ignored) {
                // As above.
            }
        }
        entries.clear();
    }
}
