package dev.shamela.mcp;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.apache.lucene.document.Document;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.FieldInfo;
import org.apache.lucene.index.FieldInfos;
import org.apache.lucene.index.StoredFields;
import org.apache.lucene.index.Term;
import org.apache.lucene.search.BooleanClause;
import org.apache.lucene.search.BooleanQuery;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.MultiPhraseQuery;
import org.apache.lucene.search.PhraseQuery;
import org.apache.lucene.search.Query;
import org.apache.lucene.search.ScoreDoc;
import org.apache.lucene.search.TermInSetQuery;
import org.apache.lucene.search.TermQuery;
import org.apache.lucene.search.TopDocs;
import org.apache.lucene.search.TotalHitCountCollectorManager;
import org.apache.lucene.util.BytesRef;
import org.apache.lucene.util.Version;

/**
 * Every read against the Lucene indexes.
 *
 * <p>Query terms arrive already folded by the Python side, using the rules Shamela's
 * analyzer applied at index time, and are treated here as exact terms. Folding them
 * again on this side would give the two implementations room to drift apart, and the
 * drift would show up as silent zero-hit queries rather than as an error.
 *
 * <p>Terms are passed as <em>groups</em>: one group per query position, holding the
 * alternatives acceptable at that position. Exact search sends one term per group;
 * root search sends every inflectional root of the word. That single shape lets one
 * search command serve both modes, including phrase queries over roots.
 */
final class Commands {

    private static final String F_ID = "id";
    private static final String F_BODY = "body";
    private static final String F_FOOT = "foot";
    private static final String F_PARENT = "parent";
    private static final String[] BOOK_FIELD_CANDIDATES = {"book_key", "book"};

    private static String resolvedBookField;
    private static boolean bookFieldResolved;

    private Commands() {}

    // ---------- health ----------

    static Map<String, Object> health(IndexCache cache) throws IOException {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("java_version", System.getProperty("java.version"));
        result.put("lucene_version", Version.LATEST.toString());
        result.put("store_dir", cache.storeRoot().toString());
        for (String name : new String[] {IndexCache.PAGE, IndexCache.TITLE}) {
            if (!cache.has(name)) {
                result.put(name + "_docs", null);
                result.put(name + "_generation", null);
                continue;
            }
            DirectoryReader reader = cache.reader(name);
            result.put(name + "_docs", reader.numDocs());
            // The reader's generation changes whenever Shamela rebuilds the index.
            // Cursors are bound to it, so a rebuild invalidates them loudly.
            result.put(name + "_generation", reader.getVersion());
        }
        result.put("book_field", bookField(cache));
        return result;
    }

    // ---------- search ----------

    static Map<String, Object> search(IndexCache cache, String field, String mode,
                                      List<List<String>> groups, List<String> bookIds,
                                      int limit, Integer afterDoc, Double afterScore)
            throws IOException {
        IndexSearcher searcher = cache.searcher(IndexCache.PAGE);
        Query text = buildTextQuery(field, mode, groups);

        String scopeField = bookField(cache);
        boolean pushedDown = bookIds != null && !bookIds.isEmpty() && scopeField != null;
        Query query = pushedDown ? scoped(text, scopeField, bookIds) : text;

        int total = searcher.search(query, new TotalHitCountCollectorManager(searcher.getSlices()));

        int fetch = Math.max(1, limit);
        TopDocs top;
        if (afterDoc != null && afterDoc >= 0) {
            ScoreDoc after = new ScoreDoc(afterDoc, afterScore == null ? 0f : afterScore.floatValue());
            top = searcher.searchAfter(after, query, fetch);
        } else {
            top = searcher.search(query, fetch);
        }

        StoredFields stored = cache.storedFields(IndexCache.PAGE);
        List<Object> hits = new ArrayList<>();
        int skipped = 0;
        for (ScoreDoc sd : top.scoreDocs) {
            Document doc = stored.document(sd.doc);
            String id = doc.get(F_ID);
            if (id == null) {
                continue;
            }
            String[] parts = splitKey(id);
            if (parts == null) {
                continue;
            }
            // Without a usable scope field the filter cannot be pushed into Lucene,
            // so restrict here and report the total as approximate.
            if (!pushedDown && bookIds != null && !bookIds.isEmpty() && !bookIds.contains(parts[0])) {
                skipped++;
                continue;
            }
            Map<String, Object> hit = new LinkedHashMap<>();
            hit.put("book_id", parts[0]);
            hit.put("page_id", Integer.parseInt(parts[1]));
            hit.put("doc", sd.doc);
            hit.put("score", (double) sd.score);
            hits.add(hit);
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("total_hits", total);
        result.put("total_hits_exact", pushedDown || bookIds == null || bookIds.isEmpty());
        result.put("hits", hits);
        result.put("has_more", top.scoreDocs.length >= fetch);
        result.put("scope_pushed_down", pushedDown);
        result.put("post_filtered_out", skipped);
        if (top.scoreDocs.length > 0) {
            ScoreDoc last = top.scoreDocs[top.scoreDocs.length - 1];
            result.put("last_doc", last.doc);
            result.put("last_score", (double) last.score);
        }
        return result;
    }

    static Map<String, Object> countByBook(IndexCache cache, String field, String mode,
                                           List<List<String>> groups, List<String> bookIds)
            throws IOException {
        IndexSearcher searcher = cache.searcher(IndexCache.PAGE);
        Query text = buildTextQuery(field, mode, groups);
        String scopeField = bookField(cache);

        List<Object> counts = new ArrayList<>();
        if (scopeField == null) {
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("counts", counts);
            result.put("supported", false);
            return result;
        }
        for (String bookId : bookIds) {
            Query scoped = new BooleanQuery.Builder()
                    .add(text, BooleanClause.Occur.MUST)
                    .add(new TermQuery(new Term(scopeField, new BytesRef(bookId))),
                            BooleanClause.Occur.FILTER)
                    .build();
            int hits = searcher.search(scoped, new TotalHitCountCollectorManager(searcher.getSlices()));
            if (hits > 0) {
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("book_id", bookId);
                row.put("hits", hits);
                counts.add(row);
            }
        }
        counts.sort((a, b) -> Integer.compare(
                (Integer) ((Map<?, ?>) b).get("hits"), (Integer) ((Map<?, ?>) a).get("hits")));

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("counts", counts);
        result.put("supported", true);
        return result;
    }

    // ---------- stored-field fetches ----------

    record BookRequest(String bookId, List<Integer> ids) {}

    static Map<String, Object> getPages(IndexCache cache, List<BookRequest> requests) throws IOException {
        return fetch(cache, IndexCache.PAGE, requests, new String[] {F_BODY, F_FOOT},
                new String[] {"body", "foot"});
    }

    static Map<String, Object> getTitles(IndexCache cache, List<BookRequest> requests) throws IOException {
        return fetch(cache, IndexCache.TITLE, requests, new String[] {F_BODY, F_PARENT},
                new String[] {"body", "parent"});
    }

    private static Map<String, Object> fetch(IndexCache cache, String index, List<BookRequest> requests,
                                             String[] sourceFields, String[] outputNames)
            throws IOException {
        List<String> keys = new ArrayList<>();
        for (BookRequest request : requests) {
            for (Integer id : request.ids()) {
                keys.add(request.bookId() + "-" + id);
            }
        }
        Map<String, Document> byKey = lookupByKey(cache, index, keys);

        List<Object> groups = new ArrayList<>();
        for (BookRequest request : requests) {
            List<Object> rows = new ArrayList<>();
            for (Integer id : request.ids()) {
                Document doc = byKey.get(request.bookId() + "-" + id);
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("id", id);
                row.put("found", doc != null);
                for (int i = 0; i < sourceFields.length; i++) {
                    row.put(outputNames[i], doc == null ? null : doc.get(sourceFields[i]));
                }
                rows.add(row);
            }
            Map<String, Object> group = new LinkedHashMap<>();
            group.put("book_id", request.bookId());
            group.put("results", rows);
            groups.add(group);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("groups", groups);
        return result;
    }

    /** One query for a whole cross-book batch; misses come back marked, never dropped. */
    private static Map<String, Document> lookupByKey(IndexCache cache, String index, List<String> keys)
            throws IOException {
        Map<String, Document> byKey = new HashMap<>();
        if (keys.isEmpty() || !cache.has(index)) {
            return byKey;
        }
        IndexSearcher searcher = cache.searcher(index);
        StoredFields stored = cache.storedFields(index);

        List<BytesRef> refs = new ArrayList<>(keys.size());
        for (String key : keys) {
            refs.add(new BytesRef(key));
        }
        Query query = refs.size() == 1
                ? new TermQuery(new Term(F_ID, refs.get(0)))
                : new TermInSetQuery(F_ID, refs);

        TopDocs top = searcher.search(query, Math.max(1, keys.size()));
        for (ScoreDoc sd : top.scoreDocs) {
            Document doc = stored.document(sd.doc);
            String id = doc.get(F_ID);
            if (id != null) {
                byKey.put(id, doc);
            }
        }
        return byKey;
    }

    // ---------- diagnostics ----------

    static Map<String, Object> probe(IndexCache cache, String index, String field, List<String> terms)
            throws IOException {
        DirectoryReader reader = cache.reader(index);
        Map<String, Object> frequencies = new LinkedHashMap<>();
        for (String term : terms) {
            frequencies.put(term, reader.docFreq(new Term(field, new BytesRef(term))));
        }
        List<Object> fields = new ArrayList<>();
        for (FieldInfo info : FieldInfos.getMergedFieldInfos(reader)) {
            fields.add(info.name);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("docfreqs", frequencies);
        result.put("fields", fields);
        result.put("num_docs", reader.numDocs());
        return result;
    }

    // ---------- query construction ----------

    private static Query buildTextQuery(String field, String mode, List<List<String>> groups) {
        String target = (field == null || field.isBlank()) ? F_BODY : field;
        if (groups.isEmpty()) {
            throw new IllegalArgumentException("no query terms");
        }
        if ("phrase".equals(mode)) {
            return phraseQuery(target, groups);
        }
        BooleanClause.Occur occur = "any_terms".equals(mode)
                ? BooleanClause.Occur.SHOULD
                : BooleanClause.Occur.MUST;
        BooleanQuery.Builder builder = new BooleanQuery.Builder();
        for (List<String> group : groups) {
            builder.add(groupQuery(target, group), occur);
        }
        if (occur == BooleanClause.Occur.SHOULD) {
            builder.setMinimumNumberShouldMatch(1);
        }
        return builder.build();
    }

    /** A single query position: one term, or any of several alternatives. */
    private static Query groupQuery(String field, List<String> group) {
        if (group.isEmpty()) {
            throw new IllegalArgumentException("empty term group");
        }
        if (group.size() == 1) {
            return new TermQuery(new Term(field, new BytesRef(group.get(0))));
        }
        List<BytesRef> refs = new ArrayList<>(group.size());
        for (String term : group) {
            refs.add(new BytesRef(term));
        }
        return new TermInSetQuery(field, refs);
    }

    private static Query phraseQuery(String field, List<List<String>> groups) {
        boolean hasAlternatives = groups.stream().anyMatch(g -> g.size() > 1);
        if (!hasAlternatives) {
            PhraseQuery.Builder builder = new PhraseQuery.Builder();
            int position = 0;
            for (List<String> group : groups) {
                builder.add(new Term(field, new BytesRef(group.get(0))), position++);
            }
            return builder.build();
        }
        // Root phrases: any root may stand at each position.
        MultiPhraseQuery.Builder builder = new MultiPhraseQuery.Builder();
        int position = 0;
        for (List<String> group : groups) {
            Term[] alternatives = new Term[group.size()];
            for (int i = 0; i < group.size(); i++) {
                alternatives[i] = new Term(field, new BytesRef(group.get(i)));
            }
            builder.add(alternatives, position++);
        }
        return builder.build();
    }

    private static Query scoped(Query base, String scopeField, List<String> bookIds) {
        List<BytesRef> refs = new ArrayList<>(bookIds.size());
        for (String id : bookIds) {
            refs.add(new BytesRef(id));
        }
        Query filter = refs.size() == 1
                ? new TermQuery(new Term(scopeField, refs.get(0)))
                : new TermInSetQuery(scopeField, refs);
        return new BooleanQuery.Builder()
                .add(base, BooleanClause.Occur.MUST)
                .add(filter, BooleanClause.Occur.FILTER)
                .build();
    }

    /**
     * Find the field holding the book id, by evidence rather than by name.
     *
     * <p>Different Shamela builds name it {@code book_key} or {@code book}, and the
     * field is indexed without being stored, so its value cannot simply be read back
     * off a document. Instead each candidate is tested the way it will actually be
     * used: take the book id from a sample document's stored {@code id} ("&lt;book&gt;-&lt;page&gt;")
     * and confirm the candidate field has a matching term. A field that answers for
     * every sample is the one a scope filter can rely on; anything else would silently
     * mis-scope every scoped search.
     */
    private static synchronized String bookField(IndexCache cache) throws IOException {
        if (bookFieldResolved) {
            return resolvedBookField;
        }
        bookFieldResolved = true;
        resolvedBookField = null;
        if (!cache.has(IndexCache.PAGE)) {
            return null;
        }
        DirectoryReader reader = cache.reader(IndexCache.PAGE);
        StoredFields stored = cache.storedFields(IndexCache.PAGE);
        int max = reader.maxDoc();
        if (max <= 0) {
            return null;
        }

        List<String> sampleBooks = new ArrayList<>();
        for (int docId : new int[] {0, max / 3, (2 * max) / 3, max - 1}) {
            if (docId < 0 || docId >= max) {
                continue;
            }
            try {
                String id = stored.document(docId).get(F_ID);
                String[] parts = id == null ? null : splitKey(id);
                if (parts != null) {
                    sampleBooks.add(parts[0]);
                }
            } catch (IOException e) {
                // Deleted or unreadable sample; the remaining ones still decide it.
            }
        }
        if (sampleBooks.isEmpty()) {
            return null;
        }

        for (String candidate : BOOK_FIELD_CANDIDATES) {
            boolean agreesEverywhere = true;
            for (String bookId : sampleBooks) {
                if (reader.docFreq(new Term(candidate, new BytesRef(bookId))) <= 0) {
                    agreesEverywhere = false;
                    break;
                }
            }
            if (agreesEverywhere) {
                resolvedBookField = candidate;
                return resolvedBookField;
            }
        }
        return null;
    }

    private static String[] splitKey(String id) {
        int dash = id.indexOf('-');
        if (dash <= 0 || dash == id.length() - 1) {
            return null;
        }
        String book = id.substring(0, dash);
        String rest = id.substring(dash + 1);
        for (int i = 0; i < rest.length(); i++) {
            if (!Character.isDigit(rest.charAt(i))) {
                return null;
            }
        }
        return new String[] {book, rest};
    }
}
