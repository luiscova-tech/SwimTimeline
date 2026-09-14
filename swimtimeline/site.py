"""Where this app lives, in one place.

The canonical domain is swimtimeline.org, verified with TLS on Render as a custom domain.
Render's own subdomain (swimtimeline.onrender.com) stays enabled alongside it and keeps
resolving to the same service -- it is NOT being retired, because badge cards already printed
with that URL (swimtimeline/badges.py's CARD_CREDIT_URL) can't be corrected after the fact.
This constant only changes which one is canonical going forward.

Everything that prints, emails, or links to the live site reads it from here rather than
repeating the string -- including the URL printed on every new badge card, which is the one
copy that ends up on paper in an official's hand.
"""

from __future__ import annotations

SITE_DOMAIN = "swimtimeline.org"
SITE_URL = f"https://{SITE_DOMAIN}"

OFFICIALS_PATH = "/officials"
OFFICIALS_URL = f"{SITE_URL}{OFFICIALS_PATH}"

# What gets printed on a badge card: no scheme, no query string. A 2"x3" card is already fighting
# for room in every direction (see draw_card's shrink-to-fit loops), and nobody types
# "https://" -- or a query string -- off a piece of paper anyway.
CARD_CREDIT_URL = f"{SITE_DOMAIN}{OFFICIALS_PATH}"
