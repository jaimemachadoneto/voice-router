# voice-router

The voice router turns a spoken command into the devices that it names and the
action. The router never runs an action. Home Assistant runs each action.

The project has two parts:

- The router service: an HTTP service in `src/voice_router/`. Home Assistant
  sends the house in one sync, sends each command to `/route`, and sends the
  result of the action to `/log`.
- The Home Assistant component: a custom component. It syncs the house and
  calls the router service.

## Environment variables

The service reads the process environment at start.

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `VOICE_ROUTER_TOKEN` | Yes | none | The bearer token of the Home Assistant component. |
| `OPENROUTER_API_KEY` | One of the two | none | The OpenRouter key, as the value. |
| `VOICE_ROUTER_OPENROUTER_KEY_FILE` | One of the two | none | The OpenRouter key, as a file path. |
| `VOICE_ROUTER_DB` | No | `/data/voice-router.db` | The SQLite file of the sync and the log. |
| `VOICE_ROUTER_HOUSE_WORDS` | No | none | The private word file of the house. |
| `VOICE_ROUTER_JEV_LIMIT` | No | `0.8` | The Jev time limit in seconds. |
| `VOICE_ROUTER_JEV_MODEL` | No | `~typesafe/jev-latest` | The OpenRouter model of Jev. |
| `VOICE_ROUTER_KEV_URL` | No | none | The URL of the Kev service. No local route when unset. |
| `VOICE_ROUTER_KEV_MODEL` | No | `kev` | The model name of Kev. |
| `VOICE_ROUTER_KEV_LIMIT` | No | `0.25` | The Kev time limit in seconds. |
| `VOICE_ROUTER_LOCAL_FIRST` | No | false | The local-first switch. `1`, `true` or `yes` sets the switch to on. |

**`OPENROUTER_API_KEY` and `VOICE_ROUTER_OPENROUTER_KEY_FILE` are EXCLUSIVE.
Set exactly one of them.** The service stops at start if it finds both, or
neither. For a container, mount the secret as a file and set
`VOICE_ROUTER_OPENROUTER_KEY_FILE` to its path.

## The local route

Kev is the local decision model. It answers the device question beside Jev.
The service contract, the settings, the training data and the promotion
checklist are in [KEV.md](KEV.md).

## The word data in two layers

The router reads the words of a command in two layers:

1. The package file `src/voice_router/general_words.json` holds the general
   words: the domains, the actions, the questions and the connecting words.
   It holds no private data, so it is in git.
2. The file in `VOICE_ROUTER_HOUSE_WORDS` holds the rooms, the entities and
   the aliases of one house. It is private, so it is not in git.

The router joins the two layers. The general words come first, and the house
words follow with no duplicate. The `alias_version` in the answer is a digest
of both files.

### Connecting words

A person says "quarto do casal" for the area "Quarto Casal". The key
`connectors` lists, for each language, the words that may sit between two
words of an area name. A name matches when up to two connecting words sit
between two words of the name. The words of all languages apply to every command.

To add a connecting word for your house, put it in the `reviewed` part of the
house file. The general file and the `reviewed` part are the only sources:

```json
{ "reviewed": { "connectors": { "pt": ["no", "na"] } } }
```

## Home Assistant component

The component is in `custom_components/voice_router/`. Install it as a HACS
custom repository.

The config flow asks for these values:

- the URL and the token of the router;
- the URL and the key of LiteLLM.

The options flow sets these values:

- the model alias of the `chat` route and of the `complex` route;
- the Home Assistant LLM APIs of the `complex` route;
- the risky list;
- the timeout of the router.

Add the risky entities of the site in the options flow. The repository holds
only the generic defaults, so no site entity is in git.

The phase-1 device service map has no `lock` service and no
`alarm_control_panel` service. A confirmed lock command goes to the built-in
agent.

## Run it

Enter the project shell and install the package:

```sh
devenv shell
uv sync --extra dev
```

The `uv sync` command makes `uv.lock`. Do not commit `uv.lock`.

Run the test suite with `check`:

```sh
check
```

Start the service:

```sh
VOICE_ROUTER_TOKEN=dev OPENROUTER_API_KEY=dev VOICE_ROUTER_DB=./voice-router.db \
  uvicorn voice_router.api:app_from_env --factory --host 127.0.0.1 --port 8000
```

The command sets the token, the OpenRouter key and a database file in the
current directory, so it works on a developer machine.

`GET /health` needs no token. It answers `{"status": "ok", "synced": false}`
when no sync exists.

`GET /stats` needs the token. It returns the dashboard summary from the log.
It is read-only.

Build the container image and start it with test settings:

```sh
docker build -t voice-router .
docker run --rm -p 8000:8000 \
  -e VOICE_ROUTER_TOKEN=test -e OPENROUTER_API_KEY=test voice-router
```

Mount a volume at `/data` to keep the sync and the log after a restart.

## Privacy

At runtime, the router sends the text of each spoken command, the room, and the
name, the area (with its aliases) and the aliases of each offered device to
OpenRouter System One. OpenRouter is a third party. The router sends nothing
else.

For a `chat` command, the component sends the conversation text to the LiteLLM
endpoint. For a `complex` command, it also sends the offered device texts and
uses the Home Assistant Assist API tools.

The token, the OpenRouter key and the LiteLLM key are secrets. Never put a
secret in git, in a log line or in an error message.

## Release status

The first release is not ready. These steps are not done yet:

- The pull request in `home-ops` that creates the HelmRelease of the app.
- The `MY_PAT` secret in GitHub.
- The first version tag and the image in `ghcr.io`.

A new app needs the `home-ops` pull request first. That pull request creates
the HelmRelease of the app. Then add the secret and run `deploy patch`. The
workflow publishes the image and opens the image pull request in `home-ops`.
