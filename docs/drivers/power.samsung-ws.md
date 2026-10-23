# power.samsung-ws — Samsung Tizen grand public 2023

## Réglages sur la TV
1. Réseau : TV sur le même VLAN que l'agent, IP réservée, Ethernet de préférence.
2. Général > Réseau > Paramètres experts : « Allumer avec mobile » (Power on with Mobile) activé, pour Wake-on-LAN et l'API en veille.
3. Gestionnaire de périphériques externes > Gestionnaire de connexion d'appareil : « Notification d'accès » activée, accès aux appareils non bloqué.

## Test manuel
1. Config écran : `host` (IP), `mac` (MAC Ethernet de préférence ; la MAC Wi-Fi renvoyée par la détection ne sert que si la TV est en Wi-Fi).
2. « Détecter » : lit `http://<ip>:8001/api/v2/`, puis déclenche l'invite sur la TV : « VDS veut se connecter », accepter dans les ~40 s. Le jeton obtenu et l'empreinte TLS de la TV sont stockés chiffrés dans l'état de l'agent (`token:<ip>`, `pin:<ip>`).
3. `power.probe` : `on` (champ `device.PowerState` du REST).
4. `power.set off` : `KEY_POWER` n'est envoyée que si la TV est `on` (la touche bascule). Veille attendue, état `standby`.
5. `power.set on` : paquet magique (UDP 9) puis, si l'API répond encore en veille, `KEY_POWER`. Compter 15 à 25 s : monter `power.verify_delay_s` si la vérification échoue.
6. Après changement de certificat de la TV (mise à jour, réinitialisation) : `power.samsung.cert_changed`. Effacer l'appairage : `python -m vds_agent state-reset power.samsung-ws` puis redétecter.

## Limites
- `KEY_POWER` est une bascule : le pilote lit l'état d'abord et ne bascule jamais à l'aveugle. TV injoignable : extinction refusée (`power.samsung.unreachable`).
- L'état ne vient que du REST 8001 ; selon le firmware, le champ `PowerState` peut manquer (`unknown`) ou la TV cesser de répondre une fois en veille complète (option `unreachable_means_off`).
- Certificat auto-signé de la TV : épinglage au premier contact (TOFU), vérifié avant l'envoi du jeton.
- Le nom d'appareil (`name`) est celui affiché lors de l'autorisation ; changer le nom crée une nouvelle autorisation côté TV.
- Détails de protocole (événements `ms.channel.*`, format de la touche) à revalider sur le firmware réel : non testé sur matériel dans ce lot.
