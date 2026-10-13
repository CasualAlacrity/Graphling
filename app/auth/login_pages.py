"""What the pilot sees after a Discord login: the browser page the local listener serves
(auth/discord_identity.py's _CallbackHandler) and the console line ALICE prints, both
from one table so they always agree.

The page has to be one self-contained response. The listener answers exactly one request
and stops, so anything the page tried to load afterwards (fonts, images, a favicon)
would hit a closed port. Styles are inline, fonts are system fonts, and the colors are
the overlay's own tokens (overlay/theme.py), copied rather than imported: theme.py
pulls in PySide6, and this runs before Qt is set up.
"""
import html
from dataclasses import dataclass

# A permanent (never-expiring) invite to the ALICE Discord, shown as a button on the
# "not in the server" page. None = no button; the text still says where to go. The
# invite handed out on 2026-10-12 expires 2026-11-11, so it isn't used here.
ALICE_DISCORD_INVITE_URL: str | None = None

_WRONG_ACCOUNT_HINT = (
    "Signed in with the wrong Discord account? Log out of Discord in this browser, "
    "then start ALICE again."
)


@dataclass(frozen=True)
class Outcome:
    key: str
    tone: str  # "ok", "wait" or "stop": the status dot's color
    eyebrow: str
    title: str
    # "{account}" becomes the Discord name in bold on the page, "(name)" in the console.
    body: str
    hint: str | None = None
    show_invite: bool = False


OUTCOMES = {outcome.key: outcome for outcome in [
    Outcome(
        "signed_in", "ok", "Signed in", "You're in.",
        "ALICE is signed in as {account}. Head back to the cockpit.",
    ),
    Outcome(
        "missing_role", "stop", "Closed beta", "Not in the beta yet",
        "{account} is in the ALICE Discord, but doesn't have beta access yet. Ask for it "
        "there, then start ALICE again.",
        hint=_WRONG_ACCOUNT_HINT,
    ),
    Outcome(
        "not_in_server", "stop", "Closed beta", "Not in the beta yet",
        "{account} isn't in the ALICE Discord server. Beta access is handed out there: "
        "join it, ask for access, then start ALICE again.",
        hint=_WRONG_ACCOUNT_HINT, show_invite=True,
    ),
    # A server from before `missing` existed only says "not in the beta".
    Outcome(
        "not_in_beta", "stop", "Closed beta", "Not in the beta yet",
        "{account} isn't in the ALICE beta yet. Ask in the ALICE Discord server for "
        "access, then start ALICE again.",
        hint=_WRONG_ACCOUNT_HINT, show_invite=True,
    ),
    Outcome(
        "beta_check_failed", "wait", "Sign-in paused", "Couldn't confirm your access",
        "Discord didn't answer when ALICE checked your beta access. That's on Discord's "
        "side and usually brief: start ALICE again in a minute.",
    ),
    Outcome(
        "cancelled", "wait", "Sign-in cancelled", "Sign-in cancelled",
        "You cancelled the Discord sign-in. ALICE needs it to know who you are, so "
        "start ALICE again whenever you're ready.",
    ),
    Outcome(
        "failed", "stop", "Sign-in failed", "Sign-in didn't complete",
        "Something went wrong between ALICE, Discord and this browser. Close this tab "
        "and start ALICE again.",
    ),
]}


def outcome_for(result: dict | None) -> Outcome:
    """Which page/message a listener result (token/username/state/error/missing) gets.
    State is checked by login() itself, not here."""
    if not result:
        return OUTCOMES["failed"]
    error = result.get("error")
    if error == "not_in_beta":
        missing = result.get("missing")
        if missing == "role":
            return OUTCOMES["missing_role"]
        if missing == "server":
            return OUTCOMES["not_in_server"]
        return OUTCOMES["not_in_beta"]
    if error == "beta_check_failed":
        return OUTCOMES["beta_check_failed"]
    if error == "access_denied":
        return OUTCOMES["cancelled"]
    if error or not result.get("token"):
        return OUTCOMES["failed"]
    return OUTCOMES["signed_in"]


def console_message(outcome: Outcome, username: str | None) -> str:
    account = f"This Discord account ({username})" if username else "This Discord account"
    if outcome.key == "signed_in":
        account = username or "you"
    return outcome.body.format(account=account)


def render_page(outcome: Outcome, username: str | None) -> str:
    if username:
        account = f"<strong>{html.escape(username)}</strong>"
    elif outcome.key == "signed_in":
        account = "you"
    else:
        account = "This Discord account"
    body = html.escape(outcome.body).replace("{account}", account)

    hint = f'<p class="hint">{html.escape(outcome.hint)}</p>' if outcome.hint else ""
    invite = ""
    if outcome.show_invite and ALICE_DISCORD_INVITE_URL:
        invite = f'<a class="button" href="{html.escape(ALICE_DISCORD_INVITE_URL)}">Join the ALICE Discord</a>'

    return _PAGE.format(
        title=html.escape(outcome.title),
        eyebrow=html.escape(outcome.eyebrow),
        tone=outcome.tone,
        body=body,
        invite=invite,
        hint=hint,
    )


# Tokens from overlay/theme.py: BG, SURFACE, BORDER, TEXT_PRIMARY, TEXT_SECONDARY,
# TEXT_DISABLED, ACCENT, ACCENT_HOVER, SUCCESS, WARNING, ERROR. The wordmark and the
# faint grid + orange glow follow docs/assets/alice-hero.svg.
_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<link rel="icon" href="data:,">
<title>ALICE · {title}</title>
<style>
  :root {{
    --bg: #0D0F10; --surface: #17191B; --border: #33373A; --grid: #1B1E20;
    --text: #EDEAE4; --text-2: #9AA0A6; --text-3: #5C6266;
    --accent: #F2650C; --accent-hover: #FF7A1F;
    --ok: #4CAF6D; --wait: #E8B23A; --stop: #E5484D;
    --font: "Helvetica Neue", "Segoe UI", Helvetica, Arial, sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; min-height: 100%; }}
  body {{
    min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    padding: 32px 16px;
    background-color: var(--bg);
    background-image:
      radial-gradient(ellipse at 78% 30%, rgba(242, 101, 12, 0.22), rgba(242, 101, 12, 0) 60%),
      linear-gradient(var(--grid) 1px, transparent 1px),
      linear-gradient(90deg, var(--grid) 1px, transparent 1px);
    background-size: 100% 100%, 120px 105px, 120px 105px;
    color: var(--text);
    font-family: var(--font);
    -webkit-font-smoothing: antialiased;
  }}
  main {{ width: 100%; max-width: 520px; }}
  .brand {{ margin: 0 0 28px; }}
  .wordmark {{
    display: block;
    font-size: 44px; font-weight: 800; letter-spacing: 0.08em; line-height: 1;
  }}
  .bar {{ display: block; width: 52px; height: 4px; margin-top: 12px; background: var(--accent); }}
  .tagline {{
    display: block; margin-top: 14px;
    font-size: 12px; font-weight: 600; letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--text-2);
  }}
  .tagline b {{ font-weight: 600; color: var(--accent); }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-top: 3px solid var(--tone);
    padding: 28px 28px 24px;
  }}
  .card.ok {{ --tone: var(--ok); }}
  .card.wait {{ --tone: var(--wait); }}
  .card.stop {{ --tone: var(--stop); }}
  .eyebrow {{
    display: flex; align-items: center; gap: 10px; margin: 0;
    font-size: 12px; font-weight: 600; letter-spacing: 0.24em; text-transform: uppercase;
    color: var(--text-2);
  }}
  .dot {{
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--tone); box-shadow: 0 0 10px var(--tone);
  }}
  h1 {{ margin: 14px 0 12px; font-size: 30px; font-weight: 700; line-height: 1.15; }}
  .body {{ margin: 0; font-size: 17px; line-height: 1.55; color: var(--text-2); }}
  .body strong {{ color: var(--text); font-weight: 600; }}
  .button {{
    display: inline-block; margin-top: 22px; padding: 12px 20px;
    background: var(--accent); color: #0D0F10;
    font-size: 14px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
    text-decoration: none;
  }}
  .button:hover {{ background: var(--accent-hover); }}
  .button:focus-visible {{ outline: 2px solid #FF9142; outline-offset: 3px; }}
  .hint {{
    margin: 22px 0 0; padding-top: 16px; border-top: 1px solid var(--border);
    font-size: 14px; line-height: 1.5; color: var(--text-3);
  }}
  footer {{ margin-top: 18px; font-size: 12px; letter-spacing: 0.04em; color: var(--text-3); }}
  @media (max-width: 480px) {{
    .wordmark {{ font-size: 36px; }}
    .card {{ padding: 22px 20px 20px; }}
    h1 {{ font-size: 26px; }}
  }}
</style>
</head>
<body>
<main>
  <header class="brand" aria-label="ALICE">
    <span class="wordmark">ALICE</span>
    <span class="bar"></span>
    <span class="tagline"><b>A</b>ccidental <b>L</b>ogistics <b>I</b>ntelligence <b>C</b>oordination <b>E</b>ngine</span>
  </header>
  <section class="card {tone}">
    <p class="eyebrow"><span class="dot"></span>{eyebrow}</p>
    <h1>{title}</h1>
    <p class="body">{body}</p>
    {invite}
    {hint}
  </section>
  <footer>You can close this tab.</footer>
</main>
</body>
</html>
"""
