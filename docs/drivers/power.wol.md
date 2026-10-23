# power.wol — Wake-on-LAN

## Réglages
1. TV/boîtier : activer l'allumage par le réseau (Samsung : « Allumer avec mobile » ; Android TV : « Réveil par réseau / WoL »). Ethernet de préférence (la plupart des TV ne répondent pas en Wi-Fi).
2. L'agent doit être sur le même domaine de diffusion que la TV ; sinon renseigner `broadcast` avec le broadcast dirigé du sous-réseau (ex. `192.168.10.255`) et autoriser UDP 9 sur le routeur.

## Test manuel
1. Config écran : `mac` de la TV (formats `aa:bb:...` ou `aa-bb-...`).
2. TV en veille : `power.set on`. La TV s'allume sous 5 à 30 s. Vérifier avec `tcpdump -n udp port 9` sur l'agent : 102 octets, envoyé 2 fois.
3. `power.set off` retourne `power.wol.unsupported` : normal, le pilote ne fait que rallumer.

## Limites
- Aucune lecture d'état ni extinction : la vérification est faite par un autre pilote de la cascade (ADB/Samsung). Sans pilote capable, `power.changed` est émis avec `verified=false`.
- Pas de retour d'acquittement : un paquet perdu n'est détecté que par la relecture.
