# Configuration and API keys

Media collection roots live in `discover.py` (`GAME_ROOTS`, `MOVIE_ROOTS`,
`MUSIC_ROOTS`) — a root only matches on drives where the path exists, so one
global list scopes naturally. **Adjust these to your own layout.**

API keys go in a **gitignored** `secrets.json` (never committed):

```json
{
  "tmdb_api_key": "…",
  "igdb_client_id": "…",
  "igdb_client_secret": "…"
}
```

#### Getting the keys

- **TMDB** — movies and series. Free.
  1. Create an account at <https://www.themoviedb.org/signup>.
  2. Settings → **API** → *Request an API key* → choose **Developer**.
  3. It asks for an application name and URL; a personal project with any
     placeholder URL is accepted.
  4. Copy **API Key (v3 auth)** — the 32-character hex string — into
     `tmdb_api_key`.

- **IGDB** — games. Free, but it authenticates through Twitch.
  1. Create a Twitch account and enable two-factor auth (required before you
     can register an app) at <https://dev.twitch.tv/console>.
  2. **Applications** → *Register Your Application*. OAuth Redirect URL
     `http://localhost`, Category *Application Integration*.
  3. Copy the **Client ID**, then *New Secret* for the **Client Secret**, into
     `igdb_client_id` / `igdb_client_secret`.
  4. media-catalog exchanges these for a short-lived token itself — there is
     nothing else to renew.

- **MusicBrainz / Deezer / iTunes** — albums. **No key needed**, nothing to
  set up.

Enrichment degrades gracefully: a provider with no key is skipped, and
everything else still runs.

Env vars `MEDIACAT_TMDB_API_KEY` etc. override the file.

