# Architecture

```
Claude Desktop
     │  stdio (MCP)
     ▼
shamela_mcp (Python 3.10+, in its own .venv)
     ├── sqlite3, read-only ──► master.db          catalogue
     │                          book/NNN/<id>.db   pagination + heading tree
     │                          service/S2.db      morphology cache
     └── NDJSON over a pipe ──► Java helper (~20 KB)
                                   running on Shamela's bundled JRE 21
                                   with Shamela's Lucene 10.4 jars
                                        │
                                        ▼
                                 store/page   page bodies and footnotes
                                 store/title  heading texts
```

No network access at runtime. Everything Shamela owns is read-only.

## Why a Java helper

The book text exists only inside Lucene indexes written with the `Lucene104` postings
format, and no pure-Python reader for it exists. A JVM is unavoidable.

What is avoidable is asking the user to install one. Shamela already ships a JRE 21 and
the matching Lucene 10.4 jars, so the helper runs on those: the project ships one small
jar of its own code and nothing else. That JRE is deliberately slim — no `java.sql`, no
`jdk.compiler` — which settles two design questions. SQLite is read from Python, and the
helper is compiled ahead of time on a developer machine and committed.

The alternative, JPype in-process, was rejected: it would bind the server's lifetime to a
JVM inside the same process, make a Lucene stall unkillable, and add a heavyweight
dependency to an install that must work by double-clicking.

## The pipe

One JSON object per line, both ways. Requests carry `{id, cmd, storeDir, …}`; responses
carry `{id, ok, result}` or `{id, ok:false, error}`.

The helper starts on first use and stays warm, because opening a multi-gigabyte index
costs seconds while a query costs milliseconds. It shuts down after five idle minutes and
restarts transparently on the next call. Two safety measures matter:

- **A parent-pid watchdog.** The helper receives this process's pid and exits when it
  disappears, so a killed Claude Desktop cannot leave a JVM holding index files open.
- **A stderr tail.** The last 20 lines are kept and attached to any failure. A JVM stack
  trace is the most useful diagnostic there is, and it must reach the error rather than a
  log nobody reads.

A request that exceeds the timeout is reported as `INDEX_BUSY`, naming the usual cause:
Shamela reindexing in the background, during which reads slow by orders of magnitude.

## Query construction

Terms are folded in Python and sent to Lucene as **exact terms**; no analyzer runs at
query time. Folding in both places would let the two implementations drift, and the drift
would appear as silently empty results rather than as an error. `scripts/probe_index.py`
verifies the rules against a live index instead.

Terms travel as **positional groups**: `[[t1a, t1b], [t2]]`, one group per query position
holding the alternatives acceptable there.

| Mode | Groups | Field |
|---|---|---|
| literal | one term each | `body` |
| root | every root of the word | `m_body` |

That single shape serves both modes, including phrase queries over roots (via
`MultiPhraseQuery`). Combining is `MUST` for all-terms, `SHOULD` for any-term, and a
phrase query for consecutive matching.

Root expansion reads Shamela's own morphology cache. If any word in the query has no
recorded root, the *whole* query falls back to a literal search with a note — a partially
rooted query would quietly change what the other words mean.

## Scope filtering

A category search can span thousands of books, so the filter is pushed into Lucene as a
`TermInSetQuery` FILTER clause on the book-id field. Post-filtering would mean walking the
entire match set to return ten results.

That field is `book_key` on current installations and `book` on others, and it is indexed
without being stored — so it cannot be identified by reading it off a document.
`Commands.bookField()` resolves it by evidence: take a book id from a sample document's
stored `id`, then check which candidate field has a matching term. If neither does,
scoping degrades to post-filtering and the total is reported as approximate rather than
quietly wrong.

## Paging

`searchAfter` keyset paging, with the position carried in a cursor alongside a
fingerprint of the index generation and a hash of the query. If Shamela rebuilds an index
mid-session, the cursor is rejected with an explanation instead of resuming at a position
whose meaning has changed. The total rides along too, so later batches need not recount.

## Citations

A citation is assembled from three sources and invents nothing:

| Datum | From |
|---|---|
| book, author, death year, category | `master.db` |
| volume (`part`), printed page | per-book `page` table |
| chapter chain | per-book `title` table + Lucene `title` index |
| page text, footnote | Lucene `page` index |

The chapter chain is the **full** hierarchy, walked through `title.parent`
(`كتاب الصلاة › باب صلاة الجماعة`). Prior projects reported only the nearest heading,
which loses the context that makes a reference locatable in a printed copy.

A volume or page the library does not record is reported as unrecorded. It is never
guessed, and the internal page id is never presented as a printed page number.
`page.foot` is the editor's footnote and is always labelled as such.

## Full pages, not excerpts

Search results carry the complete page text. A fiqh argument routinely places the ruling
on one line and its qualification on the next; an excerpt window cannot know where that
boundary falls, and a truncated quotation is worse than a long one for a scholar who will
cite it. `matchinfo.py` therefore only *explains* the match — which terms were found, and
whether in the body or the footnote — without touching the text.

## Failure behaviour

Every error is Arabic-first and names the next useful action. Two conventions:

- **The catalogue degrades independently of the engine.** If Java or the index is
  unavailable, `shamela_find_books`, `shamela_book_info`, and
  `shamela_list_categories` still work off SQLite, so a user can learn what their library
  holds while diagnosing the problem.
- **`shamela_health` treats a broken library as a successful answer.** When nothing works,
  the useful reply is a precise account of what is missing — including which candidate
  paths were tried and which test each one failed.

Zero hits is a success, not an error, and says so explicitly: an absent textual match in
this wording is not a ruling that the question is absent from those books.

## Module map

| Module | Responsibility |
|---|---|
| `discover.py` | find the library, JRE, and Lucene jars — structurally, never by folder name |
| `normalize.py` | folding, tokenising, and the folded↔original offset map |
| `bridge.py` | helper lifecycle, framing, timeouts, failure reporting |
| `engine.py` | query building, paging, passage assembly |
| `master.py` | catalogue: books, authors, categories, name matching |
| `bookdb.py` | pagination, neighbours, heading chains, TOC trees |
| `roots.py` | morphology cache lookups |
| `citation.py` | HTML→text, heading unwrapping, citation formatting |
| `matchinfo.py` | match evidence (never trims text) |
| `cursor.py` | cursor encoding bound to index generation |
| `render.py` | the Arabic a scholar reads |
| `context.py` | process-wide state and lazy initialisation |
| `tools/` | the eleven MCP tools |
