# Security policy

OpenApply opens untrusted web pages, passes their text to AI tools, and can submit forms with
personal data, so security reports are taken seriously.

## Reporting a vulnerability

Please report privately through GitHub: **Security → Report a vulnerability** on this
repository. Do not open a public issue for something exploitable.

Useful to include: what a hostile page, server or AI reply can make OpenApply do, a minimal
page or fixture that shows it, and the OpenApply version (`openapply --version`).

## What is in scope

Anything that lets a web page, a server or an AI reply cause OpenApply to:

- send a person's details somewhere they did not confirm;
- answer or tick a question it should have left to the user;
- submit without the user's typed confirmation;
- contact internal or private network addresses;
- read credentials or files it was not given.

## Known limitations

These are documented in the README under "Known limitations" and are not treated as new
findings. Notably: hostnames are not resolved before the URL check, application forms inside
iframes and multi-step forms are not supported, and a few CAPTCHA providers remain reachable
while a form is open (requests carrying the user's values are blocked, but data a page encodes
deliberately cannot be recognised).

OpenApply has been tested against local fixture pages, not against real employers' sites.
