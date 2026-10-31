# Sample captures

Real Capsule output for six historical records, for developers who want to read the formats,
build against them, or compare their own results with Capsule's. Each folder is one capture,
named by its capture ID, with the files Capsule wrote when the page was captured:

| File | What it is | Spec |
|---|---|---|
| `manifest.json` | The capture's source, archive and media records and the SHA-256 of every file | [capture-folder/v1](../docs/specs/capture-folder-v1.md) §4 |
| `extraction.json` | Personas, verbatim snippets and the assertions linking them | [capture-folder/v1](../docs/specs/capture-folder-v1.md) §3, §4b, §4c |
| `citation.json` | The Evidence Explained–style citation: reference note, short note, source list entry | [capture-folder/v1](../docs/specs/capture-folder-v1.md) §3 |
| `transcript/<id>/transcript.json` | A machine reading of the scanned certificate, field by field (two samples only) | [capture-folder/v2](../docs/specs/capture-folder-v2.md) §4 |

## The records

All come from public agencies; all are at least a century old.

| Capture | Holder | Record | Shows |
|---|---|---|---|
| `…nycgov-4cfca63f` | NYC Municipal Archives | Birth, Brooklyn, 1887 | One persona from an index panel |
| `…nycgov-40f557bf` | NYC Municipal Archives | Marriage, Brooklyn, 1914 | Two personas sharing record-level facts |
| `…nycgov-c4c3bc1d` | NYC Municipal Archives | Marriage, Brooklyn, 1886 | A 30-field transcript of the certificate image |
| `…nycgov-603f8316` | NYC Municipal Archives | Death, Queens, 1903 | A transcript, and the record whose hand-checked gold is in [`evals/fixtures/`](../evals/fixtures/) |
| `…irishgenealogy-dc58e58b` | IrishGenealogy.ie (Government of Ireland) | Civil marriage, Ballina, 1867 | A different site's layout and roles |
| `…registersnliie-d6c8ab12` | National Library of Ireland | Parish baptism register page | An image-only capture: empty personas and assertions, not a failed read |

## What is left out, and why

Only the derived JSON ships. The page snapshot (`snapshot.html`), the record images in
`media/`, and the transcript crops belong to the institutions that hold them, so they are not
redistributed here. Each `manifest.json` still lists their hashes, exactly as written, so a
folder here will not pass `capsule verify`; that needs the complete capture. Two other
per-capture files are also left out: `agent_log/` (the model call record) and `notes.json` (the
researcher's own notes).

The JSON files are unedited, with one exception: the NYC media links in `manifest.json` were
signed, expiring download URLs, so their query strings are removed and only the file's path
remains. Nothing hashes `manifest.json` itself, so every hash in it still describes the original
capture, and the hashes of `extraction.json` and `citation.json` match the files in this
folder (`tests/test_samples.py` checks this).

## Reading them in code

The collection reader that builds Capsule's own index reads this folder as it is:

```python
from pathlib import Path
from capsule.collection.read import read_all

for record in read_all(Path("samples")):
    print(record.capture_id, [p.label for p in record.personas])
```

The pydantic models in `capsule/contracts/` (`Persona`, `Snippet`, `Assertion`, `Citation`,
`Transcription`, `TranscriptField`) validate each file's contents.

For a whole archive rather than single captures, see the collection-level formats:
[captures-index/v2](../docs/specs/captures-index-v2.md) and
[capsule-db-v1](../docs/specs/capsule-db-v1.md).
