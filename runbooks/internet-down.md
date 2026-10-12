# Internet Down

The "Internet" check walks the path out of the house and names the FIRST link that is broken. Look at the STEP in the
message before rebooting anything: rebooting everything at once fixes it but leaves no clue what failed.

The path: this server -> router (10.0.0.1) -> Starlink dish (192.168.100.1) -> internet (1.1.1.1, 8.8.8.8)
-> DNS through AdGuard (10.0.0.35) -> a real HTTPS page.

STEP 1/5 ROUTER - the router does not answer. Power-cycle the router. (If the whole LAN is down, the problem may be
the switch the server is on, not the router.)

STEP 2/5 DISH - the router answers, the dish does not. The dish is hung, rebooting or has no power, or the cable between
router and dish is loose. Unplug the Starlink power supply for 30 seconds, plug it back in, wait 2-5 minutes for it to
reconnect, then power-cycle the router if the check is still red.

STEP 3/5 STARLINK - router and dish both answer but nothing on the internet does: the dish has lost its satellite link.
Open the Starlink app on Wi-Fi (it talks to the dish directly) and read the status: "Obstructed", "Searching", "Offline".
If the app cannot reach the dish even over cellular, the dish is offline to Starlink's network as well - power-cycle it
as in step 2. If the whole region is out, check status.starlink.com from a phone on cellular and wait.
"STARLINK OR ROUTER" means the dish could not be checked from the server (see below); treat it as step 2.

STEP 4/5 DNS - the internet works but AdGuard cannot resolve names, so every device looks offline:

docker restart adguardhome

Then see the DNS/AdGuard outage notes (devices also use the replica on the Z4, 10.0.0.60).

STEP 5/5 WEB - IPs and DNS work but an HTTPS page fails. Usually a wider Starlink or provider problem; check
status.starlink.com from a phone on cellular.

Run the check by hand:

python3 ~/ai-watchdog/scripts/watchdog_host_health.py internet --router 10.0.0.1 --dish 192.168.100.1 --dns 10.0.0.35

The row is refreshed every 2 minutes by the user timer ai-watchdog-internet (units in systemd/, installed in
~/.config/systemd/user/); the "AI Watchdog Internet Timer" row goes yellow if that timer stops. If the tile's
"(checked HH:MM)" time is old, check: systemctl --user status ai-watchdog-internet.timer

Dish not checkable from the server: the Docker network parakeet_default took 192.168.96.0/20, which contains the dish's
192.168.100.1, so the server sends dish traffic into Docker. A one-address route through the router fixes it (added
2026-10-11 and saved in NetworkManager; Parakeet keeps working, it only uses 192.168.96.2). If it is ever lost:

sudo ip route add 192.168.100.1/32 via 10.0.0.1 dev enp3s0
sudo nmcli con modify "Wired connection 1" +ipv4.routes "192.168.100.1/32 10.0.0.1"

(The first line takes effect now, the second keeps it after a reboot.)

History: 2026-10-11 all internet stopped while every dashboard row stayed green; rebooting router and Starlink together
fixed it, so which one failed is unknown. The Starlink app could not reach the dish even over cellular.
