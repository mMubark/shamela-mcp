# Shamela 4 on-disk format

Verified by direct inspection of a real installation (August 2026, 8,598 books,
7.6M indexed pages). Counts here are illustrative — they change as the user
downloads books, so read them at runtime rather than encoding them.

## Layout

```
<root>/
├── shamela.exe
├── app/
│   ├── lucene/2/            Lucene 10.4.0 jars + AlKhalil morphology + shamela-misc
│   └── win/64/
│       ├── jre/2/           bundled JRE 21.0.10  ← we run the helper on this
│       └── bin/             the app itself (Python 3.7 + JPype)
└── database/
    ├── master.db            catalogue (books, authors, categories)
    ├── book/NNN/<id>.db     per-book pagination and heading tree
    ├── store/               Lucene indexes — all book text lives here
    │   ├── page/            ~13 GB: page bodies and footnotes
    │   ├── title/           heading texts
    │   ├── book/ author/    bibliographic search
    │   └── aya/ esnad/ …    Quran verses, chains of narration
    ├── service/
    │   ├── S2.db            morphology cache: roots(token, root), cp1256
    │   ├── S1.db            book metadata blobs
    │   └── tafseer.db …     verse→page service maps
    └── user/data.db         user data — exclusively locked while the app runs
```

Two consequences shape the whole design:

1. **The text is only in Lucene.** The per-book SQLite files have no text column, and
   the index uses the `Lucene104` postings format. There is no pure-Python reader for
   it, so a JVM is required.
2. **Shamela ships the runtime we need.** Its JRE 21 and Lucene 10.4 jars are already on
   disk, so the helper needs neither. That JRE is slim, though — no `java.sql`, no
   `jdk.compiler` — so SQLite is read from Python and the helper is compiled ahead of
   time.

## master.db

```sql
book(book_id PK, book_name, book_category, book_type, book_date, authors,
     main_author, printed, group_id, hidden, major_online, minor_online,
     major_ondisk, minor_ondisk, pdf_links, pdf_ondisk, pdf_online,
     cover_ondisk, cover_online, meta_data, parent, alpha, group_order, book_up)
author(author_id PK, author_name, death_number, death_text, alpha)
author_book(author_id, book_id)      -- co-authors
coauthor_book(author_id, book_id)
category(category_id PK, category_name, category_order)
```

- **40 real categories** (ids 1–40). Id 42 is a `#` placeholder holding no books; filter
  it out.
- `death_number` uses a sentinel far in the future for "unknown", so values outside
  `0 < n < 2000` are not years.
- `parent` links multiple editions of the same work (~250 books).
- **Downloaded** means `major_ondisk > 0` *and* the per-book file exists. Either alone
  is unreliable.

## Per-book databases

Sharded by the last three digits of the book id: book `9944` → `book/944/9944.db`,
book `13000` → `book/000/13000.db`.

```sql
page(id PK, part TEXT, page INTEGER, number INTEGER, services TEXT)
title(id PK, page INTEGER, parent INTEGER)
```

- `part` is the printed volume, `page` the printed page number. Both may be null or 0,
  meaning the library does not record them — never substitute `id`, which is an internal
  identifier and unrelated to any printed page.
- **Page ids are sparse.** Gaps are normal, so neighbouring pages must be queried
  (`WHERE id < ? ORDER BY id DESC`), not computed as `id ± 1`.
- `title.parent` gives the real heading hierarchy. There is no depth column, so depth is
  derived by walking `parent`. Guard against cycles: corrupt rows exist.
- Neither table holds text.

## Lucene: store/page

Fields present on a real index:

```
id  body  foot  m_body  m_foot  n_body  n_foot
book_key  book  page  author  date  group  group_order
```

| Field | Role |
|---|---|
| `id` | `"<book_id>-<page_id>"`, stored — the key for all fetches |
| `body` | page text: **indexed folded, stored original** (diacritics intact) |
| `foot` | the editor's footnote, not the author's words |
| `m_body`, `m_foot` | root-stemmed copies (AlKhalil) — this is what root search queries |
| `n_body`, `n_foot` | number-normalised copies |
| `book_key` | book id, **indexed but not stored** — the scope filter field |
| `book` | present but not a plain-string term field; unusable for scoping |

`book_key` being indexed-without-being-stored is worth knowing: a scope field cannot be
identified by reading it back off a document. `Commands.bookField()` instead takes a book
id from a sample document's stored `id` and checks which candidate field has a matching
term.

## Lucene: store/title

```
id  body  parent  book_key  page  m_body  n_body  author  date  group  group_order
```

`id` is `"<book_id>-<title_id>"`, and `body` is the heading text — stored **wrapped in
brackets** (`[باب سجود السهو]`), which must be unwrapped before it goes into a citation.

## Folding (what the indexer did)

`body` stores folded terms, so query terms must be folded identically or they match
nothing. Verified against the live index by `scripts/probe_index.py`:

| Rule | Indexed | Not indexed |
|---|---|---|
| `ئ → ي` | `بير` (30,515) | `بئر` (0) |
| `ة → ه` | `مكه` (241,635) | `مكة` (0) |
| `أ إ آ ٱ → ا` | `الامر` (487,745) | `الأمر` (0) |
| `ى → ي` | `علي` (5,341,392) | `على` (0) |
| `ؤ → و` | `مومن` (52,473) | `مؤمن` (0) |
| `ابن → بن` (token) | `بن` (4,686,465) | `ابن` (0) |

Also dropped: diacritics (U+064B–065F), superscript alef, Quranic annotation marks,
tatweel, zero-width and bidi controls. Arabic-Indic digits become ASCII. A standalone
`ء` is **not** folded — the morphology roots use it as a letter.

`ئ → ي` is the rule most easily missed, and missing it fails silently: every query
containing that letter returns zero results with no error.

## service/S2.db — morphology cache

```sql
roots(token BLOB, root BLOB)   -- cp1256; root is a comma-separated list
```

~3.25M rows. **Keyed by the word's original spelling**, so lookups must not pre-fold:

| Lookup | Result |
|---|---|
| `بئر` | `بءر` ✓ |
| `بير` (folded) | `يرر,بور,رءي,وري` ✗ unrelated |
| `صلاة` | `صلي,صلاة,صلو` ✓ |
| `صلاه` (folded) | `صلو,صلي,صلل,وصل` ✗ different word |
| `الطلاق` | `طلق` |
| `طلق` | *(row exists, no root)* |

A row with an empty root means the analyser examined the word and found none — different
from a word never analysed, but either way there is nothing to search, so the caller
falls back to a literal search. Roots are index terms in `m_body` verbatim (e.g. `ءبو`
matches 4,024,897 documents), so they must not be folded after retrieval.

## Operational notes

- **`user/data.db` is exclusively locked while the Shamela app runs.** Never depend on
  it.
- **Reading Lucene while Shamela is downloading or reindexing is dramatically slow** —
  sub-second operations can take minutes. Hence a generous timeout whose error names that
  cause.
- **`reader.getVersion()`** changes when Shamela rebuilds an index; cursors are bound to
  it so a rebuild invalidates them loudly.
- Library size is a moving target. During one day of development a sibling project saw
  its page index grow from 1.87M to over 7M documents. Read counts at runtime.
