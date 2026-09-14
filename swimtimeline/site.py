"""Where this app lives, in one place.

The deployed host is Render's default domain for the service named "swimtimeline" in render.yaml,
so the two move together: rename the service and this constant has to change with it. Everything
that prints, emails, or links to the live site reads it from here rather than repeating the string
-- including the URL printed on every badge card (swimtimeline/badges.py), which is the one copy
that ends up on paper in an official's hand and cannot be corrected after the fact.
"""

from __future__ import annotations

SITE_DOMAIN = "swimtimeline.onrender.com"
SITE_URL = f"https://{SITE_DOMAIN}"

OFFICIALS_PATH = "/officials"
OFFICIALS_URL = f"{SITE_URL}{OFFICIALS_PATH}"

# What gets printed on a badge card: no scheme, no query string. A 2"x3" card is already fighting
# for room in every direction (see draw_card's shrink-to-fit loops), and nobody types
# "https://" -- or a query string -- off a piece of paper anyway.
CARD_CREDIT_URL = f"{SITE_DOMAIN}{OFFICIALS_PATH}"
