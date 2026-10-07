# Test fixtures

`tests/fakesite.py` currently stands in for hypedrop.com. Its markup follows the
*guessed* locators in `src/autohypedrop/site_selectors.py`, so today's tests
prove the flow and the safety rails, not that the selectors match the real site.

Phase 0 (`docs/discovery.md`) replaces that guesswork. To add a real page as a
fixture:

1. Capture it with `autohypedrop record` (press Enter on the page you want).
2. Copy the `.html` file from `data/discovery/<timestamp>/` into this directory
   with a descriptive name, e.g. `free-drops-one-claimable.html`.
3. **Scrub it.** Replace your username, user id, email, balance, avatar URLs,
   referral codes and any token-looking strings with placeholders. Remove
   `<script>` tags that embed account state. Never commit a trace `.zip`: it
   holds your session cookies.
4. Write a test that loads it with `page.set_content(...)` and asserts the
   locators in `site_selectors.py` find what they should.
