# Genealogy Capsule privacy policy

*Applies to the Genealogy Capsule Chrome extension and the Genealogy Capsule program it works with ("Capsule" for short).*

**Capsule does not collect any data about you. Nothing is sent to the authors of Capsule.**

## What the extension does with web pages

The extension adds a small Capsule button to pages, but it reads nothing until you click
**Capture** (on that button or in the toolbar popup). Then it reads the page you are viewing (its
HTML, a PDF render of it, and any record image you download through the site's own Download
button) and sends it to the Capsule program running **on your own computer** (`127.0.0.1`).

## Where your data goes

- **Your captures stay on your computer**, in a folder you control. Capsule has no servers and no
  accounts.
- **If you add your own Anthropic API key**, the text of a page you capture is sent to Anthropic
  so that Claude can read the record, using your key and under
  [Anthropic's terms](https://www.anthropic.com/legal). Without a key, nothing is sent anywhere.
- Your key is stored in your operating system's password store (Keychain or Credential Manager)
  and is used only to contact Anthropic.
- Capsule does not use analytics, trackers or advertising, and does not sell or share data.

## Permissions the extension asks for, and why

- **`debugger`**: only to save a PDF of the page you are capturing, at the moment you click
  Capture.
- **`downloads`**: to collect a record image that you download with the site's own Download
  button while a capture is waiting for it.
- **Access to the websites you visit** (Chrome describes this as being able to read and change
  data on all websites): the extension's script runs on pages so the Capsule button is there
  when you want it, but it reads a page only when you click Capture. Permission to download
  files from a particular site (a record PDF, say) is asked for separately, per site, the first
  time you capture there. **`scripting` and `activeTab`**: to read the page you chose to capture.
- **`storage`**: to remember the connection to the Capsule program on your computer and your
  preferences.

## Contact

Questions: open an issue at <https://github.com/mjlawless/capsule-genealogy/issues>.
