# Security

Capsule runs a backend on `127.0.0.1:8765` that only the paired extension can drive (a shared
pairing token, CORS limited to Chrome extension origins). It stores your Anthropic API key in
your operating system's keyring and your captures on your own disk.

If you find a security problem, such as a way for a web page to drive captures, read files,
or obtain the pairing token or API key, please **do not open a public issue**. Report it
privately through GitHub's **Security → Report a vulnerability** on this repository.

