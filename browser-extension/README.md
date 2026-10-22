# Dealflow browser add-on (Chrome, Edge, Firefox)

Adds the person you're looking at to your firm's Dealflow (decision 64): a LinkedIn profile, the email open
in Gmail or Outlook on the web, or any text you've selected (a signature). Dealflow's "Check before saving"
screen opens with what was read; nothing is saved until the person checks it there.

## How it keeps to the least

- Permissions: `activeTab`, `scripting`, `storage`. No host permissions, no background script: it can read
  a page only when someone clicks its button on that page, and only that page.
- It never calls Dealflow's API. It opens `https://<your Dealflow>/capture#d=...` in a new tab; the part
  after `#` is never sent to any server, and the page removes it from the address bar at once. Saving
  happens in Dealflow with the person's own session, checks and permissions.
- It remembers only the Dealflow address (https, or http on localhost).

## Building

    python3 browser-extension/build.py

writes `frontend/public/addon/dealflow-capture.zip` (Chrome and Edge) and `dealflow-capture-firefox.zip`
(same code, Firefox add-on id). Every install serves them from Contacts > Add from email or LinkedIn.

## Installing

- Chrome / Edge: unzip, open `chrome://extensions` (`edge://extensions`), turn on Developer mode, "Load unpacked".
- Firefox: `about:debugging` > This Firefox > "Load Temporary Add-on" (until Firefox restarts). For a
  permanent install the add-on has to be signed by Mozilla (addons.mozilla.org, listed or unlisted).
- For firm-wide roll-out, publish it to the Chrome Web Store and Edge Add-ons (or push it with the
  browser's enterprise policy) and to addons.mozilla.org.

LinkedIn's terms forbid automated collection; the add-on reads one profile the person is viewing, when
they click, and never in bulk.
