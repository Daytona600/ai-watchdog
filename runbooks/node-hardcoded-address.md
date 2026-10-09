# Node-RED Hard-coded Address

The "Node-RED Hard-coded Addresses" check found a node in the live flows with a private IP address typed into its code or URL.
Every service address belongs in ONE place, AI_CONFIG.urls (Postgres memory.config_sections, section 'urls'), so that when a
service moves it is fixed once. (2026-10-09: urls.frigate_base_url went stale when Frigate moved and three nodes silently
talked to a dead host for a month; 40 nodes were then cleaned up so none carries an address of its own.)

The check names the node, its tab and the address. Fix it like this:

1. Find the config key that holds that address (curl -s "http://10.0.0.35:3011/config_sections?section_key=eq.urls"). If none
   does, add one (see runbooks/config-url-stale.md for the PATCH + reload recipe).
2. In the node's code use the helper that the cleaned nodes start with (copy it from any of them, e.g. "HA REST tts.speak"):

   const __U = (k) => { const v = ((global.get("AI_CONFIG") || {}).urls || {})[k]; if (!v) throw new Error("AI_CONFIG.urls." + k + " is not set"); return v; };
   const haBase = __U("ha_base_url").replace(/\/+$/, "");

   No `|| "http://10.0.0.x"` fallback: a missing key must fail loudly, not quietly use an old address.
3. An http-request node cannot call the helper and does not resolve {{{global.AI_CONFIG.urls.x}}} in its URL field. Leave its
   URL blank and have the function node in front of it set msg.url = __U("key").
4. Nodes that can run at deploy, before the config has loaded, should first wait for it:
   `await __cfgReady();` (see "Fetch HA entity template").

There are no exemptions. The config loader ("Set AI global config -v2") needs the PostgREST address to load the config at all,
so that one address comes from the Node-RED container's environment (PGRST_URL in /home/davids/node-red/docker-compose.yml),
not from a literal in the flow. If the check ever flags the loader, someone typed the address back in: use env.get("PGRST_URL").
