# Kev

Kev is the local decision model of the router. This document gives the service
contract, the router settings, the measurement and the promotion checklist.

## What Kev is

Kev is a small decision model on the GPU of the `ai` host. It answers the
device question of the router: which of the offered devices fits the
command.

Kev speaks the System One API. Jev speaks the same API through OpenRouter. The
router uses one client for both models, with a different URL and model name.

The router sends the text to Kev on the home network. The text stays in the
house. Jev is a third party and gets the same text.

## The service contract

The router sends one `POST` request to `VOICE_ROUTER_KEV_URL`. The body holds
the state, the model and one question:

```json
{
  "state": {"command": "<the command>", "speaker_room": "<the room>"},
  "model": "kev",
  "questions": {
    "device": {
      "type": "choice",
      "instructions": "<the measured device instruction>",
      "criteria": {
        "<entity_id>": "<the name, the area and the aliases>",
        "none": "None of the listed devices fits the command"
      }
    }
  }
}
```

- `state.command` is the text of the command.
- `state.speaker_room` is the room of the satellite, or `null`.
- `model` is the name of the model. The default is `kev`.
- `questions.device.criteria` maps each offered entity id to its text. The text
  holds the name, the area and the aliases. The key `none` is always present.

The answer holds the chosen option, a probability for each option and a
confidence:

```json
{
  "answers": {
    "device": {
      "choice": "<one option>",
      "probabilities": {"<option>": 0.0},
      "confidence": 0.0
    }
  }
}
```

The p95 target of one call is 0.25 s. The router stops a call after
`VOICE_ROUTER_KEV_LIMIT` seconds.

The server ignores the bearer value. The router sends the value `local`. The
service needs no secret.

## The router settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `VOICE_ROUTER_KEV_URL` | none | The URL of the Kev service. No local route when unset. |
| `VOICE_ROUTER_KEV_MODEL` | `kev` | The model name in the request. |
| `VOICE_ROUTER_KEV_LIMIT` | `0.25` | The most seconds for one Kev call. |
| `VOICE_ROUTER_LOCAL_FIRST` | false | The local-first switch. `1`, `true` or `yes` sets the switch to on. |

## The shadow

The router sends the device question to Kev beside Jev when the filter offers
2 or more options. In the shadow, the Kev call never changes the Jev answer.

The reply waits at most 0.05 s for the Kev answer. On a device command, a real
answer within the grace goes into the reply and into the record. A later
answer goes to the record's `decision.local` alone.

The shadow never acts. A slow or down Kev server changes no reply. On a device
command, a failed call writes an error to `decision.local`.

## The offline gate

When Jev fails or answers too late, the router uses the offline gate. The gate
acts only on the offered devices, with these rules:

- The gate needs at most 3 options.
- The words name exactly one action.
- The words hold no question word.

For 2 or 3 options, the gate asks Kev. Kev acts only when `choose()` accepts a
strong answer and the choice is one of the offered devices. A weak answer or an
answer for a device that the router did not offer does not act.

Without a Kev client, a set of 2 or 3 options does not act. A single option
acts from the words alone.

## The switch

`VOICE_ROUTER_LOCAL_FIRST` moves the device decision from Jev to Kev. The
default is off.

The switch stays off until `voice-router compare` passes its four criteria and
the automatic run exists. When it is on, the router waits for the Kev answer
before the reply. The switch adds up to the Kev limit to the reply.

The switch replaces only the device choice. The action stays the one that Jev
answered.

## The training data

`voice-router export-training` writes the next training data from the log:

```sh
voice-router export-training --db <path> --out <dir> [--house-words <path>]
```

The command writes `train.kev.jsonl` in the output folder. Each line is one
record of the device question, in the shape of the service contract, without
`model`, with one more field: `questions.device.label`.

The label is the entity that the router acted on. When the router did not act
and the device answer is `none`, the label is `none`.

A repeated or corrected command gives no row. A record with another route, with
no options or with an entity that is not in the house also gives no row.

## The training on the `ai` host

The training runs on the `ai` host. The training uses the `kev.train` module.
The user confirms the exact command before the run.

The measured run took about 54 minutes.

Start a new training run after a change to the entity registry. The automatic
run is a later task.

## The promotion checklist

The switch needs all five criteria:

- [ ] At least 100 real device commands with the shadow on.
- [ ] An agreement of 97% or more between Kev and Jev.
- [ ] No wrong device.
- [ ] A local p95 under 0.25 s.
- [ ] An automatic training run when the entity registry changes.

`voice-router compare` checks the first four:

```sh
voice-router compare --db <path>
```

The command prints the count, the agreement, the wrong devices, the local p50
and p95, and the result of each criterion. The fifth criterion needs the
automatic run, which is a later task.

## The deployment

The Kev service runs on the `ai` host.

1. Start the service before the router.
2. Set `VOICE_ROUTER_KEV_URL` to the URL of the service. The path ends in
   `/v1/systemone`.

The service needs no key, because it is on the home network. The command text
and the entity text stay in the house for Kev. Jev still sends the same text to
OpenRouter.

## Parked

The service and the training need access to the `ai` host. The current work has
no SSH key for the `ai` host yet. The user must start the service. The user must
run the training later.

The first 100 real device commands and the switch also wait for the user.
