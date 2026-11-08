"""Constants of the Voice Router integration."""

DOMAIN = "voice_router"

CONF_ROUTER_URL = "router_url"
CONF_ROUTER_TOKEN = "router_token"
CONF_LITELLM_URL = "litellm_url"
CONF_LITELLM_KEY = "litellm_key"
CONF_CHAT_MODEL = "chat_model"
CONF_COMPLEX_MODEL = "complex_model"
CONF_LLM_HASS_API = "llm_hass_api"
CONF_RISKY = "risky"
CONF_ROUTER_TIMEOUT = "router_timeout"

DEFAULT_CHAT_MODEL = "voice-chat"
DEFAULT_COMPLEX_MODEL = "voice-complex"
DEFAULT_LLM_HASS_API = ["assist"]
# One command may spend this long: the router waits up to 0.8 s on the model,
# and the call crosses the gateway. One second left no room for either.
DEFAULT_ROUTER_TIMEOUT = 5.0

# The sync writes the word file on a volume and has nothing to do with the
# speed of a reply, so it gets its own, generous limit. At one second the house
# never reached the router and the local route had no words.
SYNC_TIMEOUT = 30.0

# The time between two polls of the items that wait for a person.
NOTIFY_MINUTES = 30

# A domain acts on each entity of that domain. An entry with a colon acts only
# on the entities of that device class. Site entities are added in the options
# flow.
DEFAULT_RISKY = [
    "lock",
    "alarm_control_panel",
    "cover:garage",
]
