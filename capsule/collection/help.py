"""`help.html` — a short guide to Genealogy Capsule, linked from the captures index.

Written beside `index.html` (so the link works in the static file, in an export, and on
the page the backend serves) and served at `/help.html`. Static text only: no data from
the collection, so it is the same for everyone and safe in the public export.
"""
from __future__ import annotations

from capsule.collection.assets import INDEX_CSS

HELP_FILENAME = "help.html"

_HELP_CSS = """
.help{max-width:46rem;margin:0 auto;padding:1rem 1.25rem 3rem;line-height:1.55}
.help h2{font-size:1.05rem;margin:1.6rem 0 .4rem}
.help p,.help li{font-size:.9rem}
.help dt{font-weight:600;font-size:.88rem;margin-top:.5rem}
.help dd{margin:0 0 0 1rem;font-size:.88rem;color:var(--muted)}
.help code{font-size:.85em}
.help h3{font-size:.92rem;margin:1rem 0 .3rem}
.help blockquote{margin:.4rem 0;padding:.45rem .7rem;border-left:3px solid var(--accent);
  background:var(--accent-bg);font-size:.86rem}
"""

_BODY = """
<p><a href="index.html">← Back to the index</a></p>

<p>Genealogy Capsule saves the record page you are looking at as evidence you keep: the page
itself, the record image, what the record says, and a citation, each with a fingerprint
(a SHA-256 hash) that shows it has not changed since you saved it. Everything stays on your
computer.</p>

<h2>Saving a page</h2>
<ol>
<li>Open the record page in Chrome, the way you normally would (signed in, if the site
needs it).</li>
<li>Click the Capsule icon, then <b>Capture this page</b>. Add a note if you want to remember
why you kept it.</li>
<li>The popup says what was saved and what was read. Its footer link opens that capture
here, in the index.</li>
</ol>
<p>Every capture stays on your computer. <b>Access</b> in the popup, usually best left on
<b>Auto-detect</b>, says where a page came from: a page from a subscription or sign-in site
is marked <b>gated</b> and flagged as copyright, a reminder not to share it or an export
that holds it. Choose <b>Public</b> or <b>Gated</b> yourself when you know better; you are
the one looking at the page.</p>
<p>Leave <b>Also archive embedded scans / PDFs</b> ticked to keep the record image. If
Chrome asks for permission to read the site's image server, allow it, or the image is not
saved.</p>

<h2>Reading what the record says</h2>
<p>With your own Anthropic API key (added under <b>Anthropic API key</b> in the popup and
billed to you, roughly 8 to 9 US cents for a typical page), Capsule reads each page as you
save it: who is named, and each fact about them. Without a key, pages are saved and marked
<b>not read yet</b>; add a key later and choose <b>Read them now</b>.</p>
<p>Every fact is linked to the words on the page it came from, and is marked as one of:</p>
<dl>
<dt>stated</dt><dd>the record says it.</dd>
<dt>calculated</dt><dd>worked out from what it says, such as a birth year from an age.</dd>
<dt>estimated</dt><dd>a judgment, not something the record says or implies exactly.</dd>
</dl>
<p>Values are copied exactly as written; anything illegible is shown as <code>[?]</code>.</p>

<h2>This index</h2>
<p>Each row is one saved page. Type in the filter box, or tick one or more record types
or sites (and a status), to narrow the list; click a column heading to sort. Click a row
to see the whole capture: its citation, notes, files and facts. Record types are grouped
(Birth &amp; baptism, Marriage, Death &amp; burial, Census &amp; household, …); the exact
type the reader gave is shown in the capture's detail.</p>
<p>Every capture is a folder of ordinary files you can open yourself. On the page Capsule
serves, <b>Open folder</b> shows a capture's folder in Finder or File Explorer, and
<b>Open archive folder</b> at the top shows them all.</p>
<dl>
<dt>captured · extracted · reviewed</dt>
<dd>saved; read, with facts; checked or corrected by you.</dd>
<dt>🔒</dt><dd>gated: from a sign-in or subscription site, and flagged as copyright. Don't
share it.</dd>
<dt>not read yet</dt><dd>saved without an API key.</dd>
<dt>incomplete</dt>
<dd>the capture says it does not hold the record, for example a page whose scan image was
not saved. Hover over it for the reason.</dd>
<dt>superseded</dt><dd>a newer capture replaces this one; the old one is kept.</dd>
<dt>↗ source</dt><dd>the page names the original document it was made from.</dd>
</dl>

<h2>Correcting what was read</h2>
<p>When you open the index from Capsule itself
(<code>http://127.0.0.1:8765/</code>, or the popup's footer link), each fact has:</p>
<dl>
<dt>✎ correct</dt>
<dd>shows the words on the page the fact came from, and lets you change the value. Earlier
corrections of that fact are listed in the same form.</dd>
<dt>✕ retract</dt>
<dd>for a fact that is wrong altogether. It stays listed, struck through.</dd>
<dt>+ Add a fact</dt>
<dd>for something the reading missed about a person it found: choose the person, then
give the value, how the record supports it, and the words on the page that say it. (A
person the reading missed altogether cannot be added yet.)</dd>
</dl>
<p>A corrected fact shows a small <b>edited</b>, <b>added</b> or <b>retracted</b> tag.
Corrections never change the saved page: they are kept beside it, so the original reading
and every change remain visible. Each correction also becomes a test case, so a future
version of the reader can be checked against what you corrected.</p>
<p>The index file itself (<code>captures/index.html</code>) and an exported archive show
the original reading; corrections are made, and shown, on the page Capsule serves.</p>

<h2>Putting a capture into your family tree</h2>
<p>Capsule does not connect to Gramps, RootsMagic, Family Tree Maker or an online tree yet;
a direct link to Gramps is planned. Until then you carry each capture across yourself, with
an AI assistant's help or by hand. Every capture shows its citation, with <b>Copy</b>
buttons, under <b>Citation</b>.</p>
<h3>With an AI assistant (recommended)</h3>
<ol>
<li>Open the capture in the index and click <b>Copy for AI</b>. It copies the capture as
text: the citation, the people, and every fact with its evidence type and the record's own
words.</li>
<li>Paste it into Claude (or another assistant) and ask something like:
<blockquote>Here is a source I saved. Help me enter it in Gramps: list each person, then
the events and facts to add for them, with this citation attached. Keep every value
exactly as written and keep its evidence type. Don't assume anyone is already in my tree;
ask me.</blockquote></li>
<li>Make the entries in your program yourself, checking each one against the record. The
assistant cannot change your tree, and you decide who is who.</li>
</ol>
<p>If the capture is from a subscription site, its text goes to the assistant when you paste
it: that is your call, under the site's terms.</p>
<h3>By hand</h3>
<ol>
<li>In your program, add a <b>source</b> from the capture's <b>Source list entry</b>, and a
<b>citation</b> of it from the <b>Reference note</b>.</li>
<li>Attach the saved page, and the record image when one was saved, as media:
<b>Open folder</b> shows them (a PDF of the page and any image are in <code>media</code>;
the page itself is <code>snapshot.html</code>).</li>
<li>Enter each fact under the right person, with that citation, noting whether the record
states it or it was calculated or estimated.</li>
</ol>

<h2>Notes, deleting, and moving your archive</h2>
<ul>
<li>The <b>Add note</b> button keeps your own words about a capture. Notes are yours, not
evidence, and are never mixed with what the record says.</li>
<li>The trash can moves a capture's folder to <code>_trash</code>; nothing is erased.</li>
<li>To move everything to another computer, open a terminal (Terminal on a Mac,
PowerShell on Windows) and export the archive to one file:
<ul>
<li>Mac: <code>cd ~/Capsule; uv run --project app capsule export ~/Desktop/archive.zip</code></li>
<li>Windows: <code>cd $env:LOCALAPPDATA\\Capsule; uv run --project app capsule export
$HOME\\Desktop\\archive.zip</code></li>
</ul>
Copy the file to the other computer, then run the same command there with
<code>import</code> in place of <code>export</code>. Importing only adds what is missing,
so it is safe to repeat. The file holds pages from subscription sites, so keep it private
and delete it once imported.</li>
</ul>

<h2>The ground rules</h2>
<ul>
<li>You drive. Capsule saves only the page you are viewing, when you click; it never
visits a site by itself and never crawls.</li>
<li>Everything stays on your computer; Capsule never publishes a page anywhere. Pages
from subscription sites are flagged so you know not to share them.</li>
<li>Use it within the terms of the sites you visit.</li>
</ul>

<h2>Help</h2>
<p>Something not working, or unclear? Open an issue at
<a href="https://github.com/mjlawless/capsule-genealogy/issues" target="_blank"
rel="noopener noreferrer">github.com/mjlawless/capsule-genealogy/issues</a> and say what you
clicked and what you saw.</p>
"""


def render_help() -> str:
    """The guide as a self-contained HTML document (pure, no I/O)."""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        "<title>Genealogy Capsule — How to use it</title>\n"
        f"<style>{INDEX_CSS}{_HELP_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "<header><h1>Genealogy Capsule — How to use it</h1></header>\n"
        f'<main class="help">{_BODY}</main>\n'
        "</body>\n"
        "</html>\n"
    )
